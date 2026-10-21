"""The one place the framework talks HTTP.

`HttpClient` wraps `httpx.Client` and centralises what every request needs: base URL, timeout,
transport retries, default headers, the bearer token, correlation ids, redacted logging and the Allure
evidence for each exchange.

Two rules shape it:

* **It never raises for an HTTP status.** Half of an API suite is about error responses; a client that
  threw on 4xx would make every negative test a `pytest.raises` around a response it could no longer
  inspect. Only *transport* failures raise (`ApiTransportError`).
* **Authentication is a property of the client, not of a request.** `with_token()` returns a *new*
  client sharing the connection pool, so an anonymous and an authenticated client coexist and no test
  can leak a token into another by mutating shared state.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

import allure
import httpx

from api_tests.redaction import Redactor
from api_tests.reporting import AllureReporter

logger = logging.getLogger("api_tests.http")

TEST_RUN_ID_HEADER = "X-Test-Run-ID"
REQUEST_ID_HEADER = "X-Request-ID"
# The backend sanitises both ids to 64 characters; staying under that keeps the echo exact.
_MAX_ID_LENGTH = 64

# Only methods whose repetition cannot change server state, and only failures where the request
# provably never reached the server. A retried read timeout could double-apply nothing here, but it
# would turn a deliberate client-timeout test into a slow flaky one.
_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_RETRYABLE_ERRORS: tuple[type[httpx.TransportError], ...] = (httpx.ConnectError, httpx.ConnectTimeout)

_EVIDENCE_REQUEST_HEADERS = ("accept", "authorization", "content-type", "x-request-id", "x-test-run-id")
_EVIDENCE_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "www-authenticate",
    "x-request-id",
    "x-test-run-id",
)


class ApiTransportError(Exception):
    """The request did not complete: connection refused, DNS, TLS, or a timeout."""

    def __init__(self, method: str, url: str, cause: Exception) -> None:
        self.method = method
        self.url = url
        self.cause = cause
        super().__init__(f"ApiTransportError: {method} {url} failed: {type(cause).__name__}: {cause}")


class ApiTimeoutError(ApiTransportError):
    """The server accepted the connection but did not answer within the timeout."""


class MalformedResponseError(Exception):
    """The response claimed JSON and was not."""


@dataclass
class _Session:
    """State shared by a client and every client derived from it."""

    http: httpx.Client
    run_id: str
    redactor: Redactor
    reporter: AllureReporter | None
    retries: int
    backoff_seconds: float
    counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def next_request_id(self) -> str:
        suffix = f"-{next(self.counter):06d}"
        return f"{self.run_id[: _MAX_ID_LENGTH - len(suffix)]}{suffix}"


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        run_id: str,
        redactor: Redactor,
        reporter: AllureReporter | None = None,
        timeout_seconds: float = 10.0,
        retries: int = 0,
        retry_backoff_seconds: float = 0.25,
        default_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json", TEST_RUN_ID_HEADER: run_id[:_MAX_ID_LENGTH]}
        headers.update(default_headers or {})
        self._session = _Session(
            http=httpx.Client(
                base_url=base_url.rstrip("/"),
                timeout=timeout_seconds,
                headers=headers,
                transport=transport,
                follow_redirects=False,
            ),
            run_id=run_id,
            redactor=redactor,
            reporter=reporter,
            retries=retries,
            backoff_seconds=retry_backoff_seconds,
        )
        self._token: str | None = None

    # --- identity -------------------------------------------------------------------------------

    @classmethod
    def _derived(cls, session: _Session, token: str | None) -> HttpClient:
        client = cls.__new__(cls)
        client._session = session
        client._token = token
        return client

    def with_token(self, token: str | None) -> HttpClient:
        """A client sending `Authorization: Bearer <token>` (or nothing, for `None`)."""
        if token is not None:
            self._session.redactor.add_secret(token)
        return self._derived(self._session, token)

    def anonymous(self) -> HttpClient:
        return self._derived(self._session, None)

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def base_url(self) -> str:
        return str(self._session.http.base_url)

    @property
    def redactor(self) -> Redactor:
        return self._session.redactor

    @property
    def run_id(self) -> str:
        return self._session.run_id

    def close(self) -> None:
        self._session.http.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- requests -------------------------------------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        session = self._session
        method = method.upper()
        request_headers = {REQUEST_ID_HEADER: session.next_request_id()}
        if self._token is not None:
            request_headers["Authorization"] = f"Bearer {self._token}"
        request_headers.update(headers or {})

        request = session.http.build_request(
            method,
            path,
            params=_clean_params(params),
            json=json,
            headers=request_headers,
            timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
        )
        title = f"{method} {request.url.path}"
        step = allure.step(title) if session.reporter is not None else nullcontext()
        with step:
            started = time.perf_counter()
            try:
                response = self._send(request)
            except ApiTransportError as error:
                self._record(request, None, started, error)
                raise
            self._record(request, response, started, None)
            return response

    def _send(self, request: httpx.Request) -> httpx.Response:
        session = self._session
        attempts = 1 + (session.retries if request.method in _RETRYABLE_METHODS else 0)
        safe_url = session.redactor.url(str(request.url))
        for attempt in range(1, attempts + 1):
            try:
                response = session.http.send(request)
                response.read()
            except httpx.TimeoutException as error:
                if isinstance(error, _RETRYABLE_ERRORS) and attempt < attempts:
                    self._backoff(attempt, request.method, safe_url, error)
                    continue
                raise ApiTimeoutError(request.method, safe_url, error) from error
            except httpx.TransportError as error:
                if isinstance(error, _RETRYABLE_ERRORS) and attempt < attempts:
                    self._backoff(attempt, request.method, safe_url, error)
                    continue
                raise ApiTransportError(request.method, safe_url, error) from error
            else:
                return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _backoff(self, attempt: int, method: str, url: str, error: Exception) -> None:
        logger.warning("%s %s attempt %d failed (%s); retrying", method, url, attempt, type(error).__name__)
        # Linear and fixed — deterministic, unlike jitter, and the retry budget is tiny anyway.
        time.sleep(self._session.backoff_seconds * attempt)

    # --- evidence -------------------------------------------------------------------------------

    def _record(
        self,
        request: httpx.Request,
        response: httpx.Response | None,
        started: float,
        error: Exception | None,
    ) -> None:
        session = self._session
        redactor = session.redactor
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        safe_url = redactor.url(str(request.url))
        request_id = request.headers.get(REQUEST_ID_HEADER, "")

        if response is not None:
            logger.info(
                "%s %s -> %s in %.1f ms [request_id=%s]",
                request.method,
                safe_url,
                response.status_code,
                elapsed_ms,
                request_id,
            )
        else:
            logger.warning("%s %s -> %s [request_id=%s]", request.method, safe_url, error, request_id)

        if session.reporter is None:
            return
        exchange: dict[str, Any] = {
            "request": {
                "method": request.method,
                "url": safe_url,
                "headers": _select(request.headers, _EVIDENCE_REQUEST_HEADERS, redactor),
                "body": redactor.body(request.content, request.headers.get("content-type")),
            },
            "correlation": {"run_id": session.run_id, "request_id": request_id},
            "elapsed_ms": elapsed_ms,
        }
        if response is not None:
            exchange["response"] = {
                "status": response.status_code,
                "headers": _select(response.headers, _EVIDENCE_RESPONSE_HEADERS, redactor),
                "body": redactor.body(response.content, response.headers.get("content-type")),
            }
        else:
            exchange["error"] = redactor.text(str(error))
        status = response.status_code if response is not None else "error"
        session.reporter.attach_json(f"{request.method} {request.url.path} -> {status}", exchange)


def response_json(response: httpx.Response) -> Any:
    """`response.json()`, failing with a readable, redacted message when the body is not JSON."""
    try:
        return response.json()
    except ValueError as error:
        snippet = response.text[:200]
        raise MalformedResponseError(
            f"{response.request.method} {response.request.url.path} answered {response.status_code} "
            f"with a body that is not valid JSON: {snippet!r}"
        ) from error


def _select(headers: httpx.Headers, names: tuple[str, ...], redactor: Redactor) -> dict[str, str]:
    return redactor.headers([(name, headers[name]) for name in names if name in headers])


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop `None`s and render booleans as the backend expects (`true`/`false`)."""
    if params is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        cleaned[key] = str(value).lower() if isinstance(value, bool) else value
    return cleaned
