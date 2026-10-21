"""`HttpClient` against a controlled `httpx.MockTransport` — no network, no backend.

These are framework tests: they check *our* client's behaviour (headers, retries, evidence,
redaction). They never stand in for the functional tests, which always hit the real backend.
"""

from __future__ import annotations

import json
from typing import Any

import allure
import httpx
import pytest

from api_tests.client import ApiTimeoutError, ApiTransportError, HttpClient, MalformedResponseError, response_json
from api_tests.redaction import REDACTED, Redactor
from api_tests.reporting import AllureReporter
from api_tests.resources import ShopApi

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("HTTP client"),
]


class RecordingReporter(AllureReporter):
    def __init__(self) -> None:
        super().__init__(Redactor(["Customer123!"]), max_bytes=4_096)
        self.attachments: list[tuple[str, Any]] = []

    def attach_json(self, name: str, payload: Any) -> None:
        self.attachments.append((name, self.redactor.data(payload)))


def _recording(seen: list[httpx.Request], **response: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, **response)

    return handler


def _client(handler: Any, *, retries: int = 0, reporter: AllureReporter | None = None) -> HttpClient:
    return HttpClient(
        "http://backend.test/api/v1",
        run_id="UNIT-RUN",
        redactor=Redactor(["Customer123!"]),
        reporter=reporter,
        retries=retries,
        retry_backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )


def test_requests_carry_correlation_headers_and_the_api_prefix() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"})

    client = _client(handler)
    client.get("/health")
    client.get("/health")

    assert [str(r.url) for r in seen] == ["http://backend.test/api/v1/health"] * 2
    assert seen[0].headers["X-Test-Run-ID"] == "UNIT-RUN"
    assert [r.headers["X-Request-ID"] for r in seen] == ["UNIT-RUN-000001", "UNIT-RUN-000002"]
    assert "Authorization" not in seen[0].headers


def test_with_token_returns_a_new_client_and_leaves_the_original_anonymous() -> None:
    seen: list[httpx.Request] = []
    client = _client(_recording(seen))

    authed = client.with_token("tok-123456")
    authed.get("/cart")
    client.get("/cart")

    assert seen[0].headers["Authorization"] == "Bearer tok-123456"
    assert "Authorization" not in seen[1].headers
    assert authed.anonymous().token is None


def test_http_errors_are_returned_not_raised() -> None:
    client = _client(lambda _: httpx.Response(409, json={"error": {"code": "error_conflict"}}))

    assert client.post("/cart/items", json={}).status_code == 409


def test_boolean_params_are_lowercase_and_none_is_dropped() -> None:
    seen: list[httpx.Request] = []
    api = ShopApi(_client(_recording(seen, json={})))

    api.products.list(in_stock=True, q=None, page=2)

    assert seen[0].url.params == httpx.QueryParams({"in_stock": "true", "page": "2"})


def test_connect_errors_on_get_are_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200)

    assert _client(handler, retries=2).get("/health").status_code == 200
    assert attempts["n"] == 3


def test_writes_are_never_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiTransportError):
        _client(handler, retries=3).post("/orders", json={})
    assert attempts["n"] == 1


def test_read_timeouts_raise_without_retrying() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ApiTimeoutError):
        _client(handler, retries=3).get("/test-data/delay/5")
    assert attempts["n"] == 1


def test_transport_errors_never_show_query_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiTransportError) as raised:
        _client(handler).get("/products", params={"token": "abcdef"})

    assert "abcdef" not in str(raised.value)
    assert REDACTED in raised.value.url


def test_every_exchange_is_attached_redacted() -> None:
    reporter = RecordingReporter()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"user": {"email": "c@shoptest.local"}, "tokens": {"access_token": "eyJ.secret.token"}},
            headers={"X-Request-ID": request.headers["X-Request-ID"]},
        )

    client = _client(handler, reporter=reporter).with_token("bearer-token-value")
    client.post("/auth/login", json={"email": "c@shoptest.local", "password": "Customer123!"})

    name, exchange = reporter.attachments[0]
    assert name == "POST /api/v1/auth/login -> 200"
    assert exchange["request"]["body"] == {"email": "c@shoptest.local", "password": REDACTED}
    assert exchange["request"]["headers"]["authorization"] == REDACTED
    assert exchange["response"]["body"]["tokens"]["access_token"] == REDACTED
    assert exchange["correlation"] == {"run_id": "UNIT-RUN", "request_id": "UNIT-RUN-000001"}
    assert "Customer123!" not in json.dumps(exchange)
    assert "bearer-token-value" not in json.dumps(exchange)


def test_malformed_json_is_a_readable_error() -> None:
    client = _client(
        lambda _: httpx.Response(200, content=b'{"items": [', headers={"content-type": "application/json"})
    )

    with pytest.raises(MalformedResponseError, match="not valid JSON"):
        response_json(client.get("/test-data/malformed"))
