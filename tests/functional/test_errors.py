"""The error envelope, and deterministic failure modes via the backend's `/test-data` endpoints.

Endpoints: `GET /test-data/error/{400,401,403,404,409,500}`, `GET /test-data/delay/{seconds}`,
`GET /test-data/empty-products`, `GET /test-data/malformed`. They exist only when the backend runs
as `development`/`testing`; elsewhere these tests skip (and say so).
"""

from __future__ import annotations

import allure
import pytest

from api_tests.client import ApiTimeoutError, MalformedResponseError, response_json
from api_tests.fixtures import BackendInfo
from api_tests.resources import ShopApi
from api_tests.resources.models import Delay, ErrorEnvelope, ProductPage
from support.assertions import assert_error, assert_schema

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Errors"),
    allure.epic("API"),
    allure.feature("Error handling"),
]


@pytest.fixture(autouse=True)
def _automation_endpoints(backend: BackendInfo) -> None:
    if not backend.automation_endpoints:
        pytest.skip("the backend does not expose /test-data (it is not running as development/testing)")


ERRORS = [
    (400, "error_bad_request"),
    (401, "error_invalid_token"),
    (403, "error_forbidden"),
    (404, "error_not_found"),
    (409, "error_conflict"),
    (500, "error_internal"),
]


@allure.story("Error envelope")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(("status", "code"), ERRORS, ids=[str(s) for s, _ in ERRORS])
def test_deterministic_errors_use_the_standard_envelope(api: ShopApi, status: int, code: str) -> None:
    envelope = assert_error(api.test_data.error(status), status, code)

    assert envelope.error.details == {"deterministic": True, "requested_status": status}


@allure.story("Error envelope")
@pytest.mark.parametrize(("status", "code"), ERRORS, ids=[str(s) for s, _ in ERRORS])
def test_deterministic_errors_are_byte_for_byte_repeatable(api: ShopApi, status: int, code: str) -> None:
    assert api.test_data.error(status).content == api.test_data.error(status).content


@allure.story("Error envelope")
def test_a_401_carries_www_authenticate(api: ShopApi) -> None:
    assert api.test_data.error(401).headers["WWW-Authenticate"] == "Bearer"


@allure.story("Error envelope")
def test_an_unsupported_error_status_is_404(api: ShopApi) -> None:
    assert_error(api.test_data.error(418), 404, "error_http_404")


@allure.story("Error envelope")
def test_validation_errors_never_echo_the_rejected_input(api: ShopApi) -> None:
    response = api.client.post("/auth/register", json={"email": "x@shoptest.local", "password": "tiny-pw"})

    envelope = assert_schema(response, ErrorEnvelope, 422)
    for error in envelope.error.details["errors"]:
        assert set(error) <= {"type", "loc", "msg"}
    assert "tiny-pw" not in response.text


@allure.story("Empty data")
def test_empty_products_has_zero_pages(api: ShopApi) -> None:
    page = assert_schema(api.test_data.empty_products(), ProductPage)

    assert (page.items, page.total, page.total_pages) == ([], 0, 0)


@allure.story("Malformed response")
def test_malformed_json_is_reported_by_the_client(api: ShopApi) -> None:
    response = api.test_data.malformed()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    with pytest.raises(MalformedResponseError, match="not valid JSON"):
        response_json(response)


@allure.story("Timeouts")
def test_delay_within_the_timeout_succeeds(api: ShopApi) -> None:
    delay = assert_schema(api.test_data.delay(1, timeout=5), Delay)

    assert delay.delayed_seconds == 1


@allure.story("Timeouts")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_slow_response_raises_a_client_timeout(api: ShopApi) -> None:
    # Read timeouts are never retried, so this takes ~1 s rather than 1 s x attempts.
    with pytest.raises(ApiTimeoutError):
        api.test_data.delay(3, timeout=1)


@allure.story("Timeouts")
def test_delay_is_capped_at_ten_seconds(api: ShopApi) -> None:
    assert_error(api.test_data.delay(11), 422, "error_validation")
