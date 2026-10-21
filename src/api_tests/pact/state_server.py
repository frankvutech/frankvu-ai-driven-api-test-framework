"""The provider-state change endpoint the Pact verifier calls.

A URL handler rather than pact-python's in-process function handler, for one reason: a URL handler's
JSON response body becomes the provider-state values the verifier injects into the request
(`${productId}`). pact-python 4.1's function handlers discard their return value, so they cannot
supply per-instance UUIDs or tokens.

Lifecycle per interaction, which is what makes verification order-independent:

    setup(state 1)   -> reset the backend, then set state 1
    setup(state 2)   -> set state 2 (same context: one sign-in, merged values)
    <request replayed against the real backend>
    teardown(...)    -> discard the context; the next setup resets again
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from api_tests.config import Settings
from api_tests.pact.provider_states import ProviderStateError, ProviderStateRegistry, StateContext
from api_tests.redaction import Redactor
from api_tests.resources import ShopApi

logger = logging.getLogger("api_tests.pact")


class StateChangeServer:
    def __init__(
        self,
        registry: ProviderStateRegistry,
        api: ShopApi,
        settings: Settings,
        redactor: Redactor,
        *,
        host: str = "127.0.0.1",
    ) -> None:
        self._registry = registry
        self._api = api
        self._settings = settings
        self._redactor = redactor
        self._lock = threading.Lock()
        self._context: StateContext | None = None
        self.events: list[dict[str, Any]] = []
        self._server = ThreadingHTTPServer((host, 0), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever, name="pact-state-server", daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}/_pact/provider-states"

    def __enter__(self) -> StateChangeServer:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    # --- state handling -------------------------------------------------------------------------

    def handle(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        state = str(payload.get("state") or "")
        action = str(payload.get("action") or "setup")
        params = payload.get("params") or {}
        with self._lock:
            if action == "teardown":
                self._context = None
                self._record(state, action, params, "ok", {})
                return HTTPStatus.OK, {}
            if not state:
                return HTTPStatus.OK, {}
            try:
                if self._context is None:
                    self._reset()
                    self._context = StateContext(self._api, self._settings)
                values = self._registry.setup(self._context, state, params)
            except (ProviderStateError, KeyError, ValueError) as error:
                self._record(state, action, params, f"failed: {error}", {})
                logger.error("Provider state %r failed: %s", state, error)
                return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)}
            self._record(state, action, params, "ok", values)
            return HTTPStatus.OK, values

    def _reset(self) -> None:
        response = self._api.test_data.reset()
        if response.status_code != HTTPStatus.OK:
            raise ProviderStateError(
                f"POST /test-data/reset answered {response.status_code}; provider verification needs a "
                "backend running with ENVIRONMENT=development or testing"
            )

    def _record(self, state: str, action: str, params: Any, outcome: str, values: dict[str, Any]) -> None:
        self.events.append(
            {
                "state": state,
                "action": action,
                "params": params,
                "outcome": outcome,
                # Values are attached redacted: they include an access token and the seeded password.
                "values": self._redactor.data(values),
            }
        )

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    payload = {}
                status, body = server.handle(payload)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                logger.debug("state server: " + format, *args)

        return Handler
