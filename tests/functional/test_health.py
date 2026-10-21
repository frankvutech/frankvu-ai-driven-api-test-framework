"""Liveness, readiness, correlation headers and the routing-level error envelope.

Endpoints: `GET /health`, `GET /health/database`.
"""

from __future__ import annotations

import allure
import pytest

from api_tests.client import REQUEST_ID_HEADER, TEST_RUN_ID_HEADER
from api_tests.resources import ShopApi
from api_tests.resources.models import DatabaseHealth, ErrorEnvelope, Health
from support.assertions import assert_error, assert_schema

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Health"),
    allure.epic("API"),
    allure.feature("Health"),
]


@pytest.mark.smoke
@allure.severity(allure.severity_level.BLOCKER)
@allure.title("Liveness answers exactly status=ok and nothing else")
def test_liveness(api: ShopApi) -> None:
    response = api.health.liveness()

    assert_schema(response, Health)
    assert response.json() == {"status": "ok"}


@pytest.mark.smoke
@allure.severity(allure.severity_level.BLOCKER)
@allure.title("Readiness round-trips PostgreSQL")
def test_database_readiness(api: ShopApi) -> None:
    health = assert_schema(api.health.database(), DatabaseHealth)

    assert health.component == "postgresql"
    assert health.latency_ms is not None
    assert health.latency_ms >= 0


@allure.title("Every response echoes the request id and the test-run id")
def test_correlation_headers_are_echoed(api: ShopApi) -> None:
    response = api.health.liveness()

    assert response.headers[REQUEST_ID_HEADER] == response.request.headers[REQUEST_ID_HEADER]
    assert response.headers[TEST_RUN_ID_HEADER] == response.request.headers[TEST_RUN_ID_HEADER]


@allure.title("A caller-supplied test-run id is sanitised before it is echoed")
def test_test_run_id_is_sanitised(api: ShopApi) -> None:
    # A newline in a log line lets a caller forge entries; the backend strips everything outside
    # [A-Za-z0-9._:-] and caps the length at 64.
    response = api.client.get("/health", headers={TEST_RUN_ID_HEADER: "E2E 001;<script>" + "x" * 80})

    echoed = response.headers[TEST_RUN_ID_HEADER]
    assert echoed.startswith("E2E001script")
    assert len(echoed) == 64


@allure.title("An unknown path answers 404 in the standard error envelope")
def test_unknown_path_uses_error_envelope(api: ShopApi) -> None:
    envelope = assert_error(api.client.get("/no-such-resource"), 404, "error_http_404")

    assert envelope.error.details == {}


@allure.title("A wrong method answers 405 in the standard error envelope")
def test_wrong_method_uses_error_envelope(api: ShopApi) -> None:
    assert_error(api.client.delete("/health"), 405, "error_http_405")


@allure.title("Error envelopes never leak a stack trace")
def test_error_envelope_has_no_internals(api: ShopApi) -> None:
    body = ErrorEnvelope.model_validate(api.client.get("/no-such-resource").json())

    assert "Traceback" not in body.error.message
