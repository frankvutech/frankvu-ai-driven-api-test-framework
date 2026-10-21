"""Consumer contract: sign-in and the current user.

Credentials never appear in the contract. The request body carries placeholder examples with
`ProviderState` generators (`${email}`, `${password}`); the provider-state handler supplies the real
values from configuration at verification time.
"""

from __future__ import annotations

import allure
import pytest
from pact import match

from api_tests.pact import bearer_from_state, from_state
from api_tests.pact.matchers import EXAMPLE_ACCESS_TOKEN
from api_tests.resources import ShopApi
from support.pact_consumer import ConsumerContract
from support.pact_shapes import JSON, error, user

pytestmark = [
    pytest.mark.pact_consumer,
    allure.parent_suite("Pact"),
    allure.suite("Consumer contracts"),
    allure.epic("Pact"),
    allure.feature("Consumer: Authentication"),
]

EXAMPLE_EMAIL = "customer@shoptest.local"
EXAMPLE_PASSWORD = "example-password"  # placeholder; replaced by provider state at verify time


@allure.title("Contract: sign in with valid customer credentials")
def test_login(contract: ConsumerContract) -> None:
    description = "a sign-in request with valid customer credentials"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 exists")
        .with_request("POST", "/api/v1/auth/login")
        .with_body(
            {
                "email": from_state(EXAMPLE_EMAIL, "${email}"),
                "password": from_state(EXAMPLE_PASSWORD, "${password}"),
            },
            content_type=JSON,
        )
        .will_respond_with(200)
        .with_body(
            {
                "user": user(),
                "tokens": {
                    "access_token": match.str("eyJhbGciOiJIUzI1NiJ9.example.access"),
                    "refresh_token": match.str("eyJhbGciOiJIUzI1NiJ9.example.refresh"),
                    "token_type": "Bearer",
                    "expires_in": match.int(900),
                },
            },
            content_type=JSON,
        )
    )

    def exercise(api: ShopApi) -> None:
        response = api.auth.login(EXAMPLE_EMAIL, EXAMPLE_PASSWORD)
        assert response.status_code == 200
        body = response.json()
        assert body["tokens"]["token_type"] == "Bearer"
        assert body["user"]["status"] == "active"

    contract.verify(description, exercise)


@allure.title("Contract: a wrong password is a 401 with error_invalid_credentials")
def test_login_with_wrong_password(contract: ConsumerContract) -> None:
    description = "a sign-in request with a wrong password"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 exists")
        .with_request("POST", "/api/v1/auth/login")
        .with_body({"email": from_state(EXAMPLE_EMAIL, "${email}"), "password": "not-the-password"}, content_type=JSON)
        .will_respond_with(401)
        .with_header("WWW-Authenticate", "Bearer")
        .with_body(error("error_invalid_credentials"), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        response = api.auth.login(EXAMPLE_EMAIL, "not-the-password")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "error_invalid_credentials"

    contract.verify(description, exercise)


@allure.title("Contract: read the signed-in user")
def test_me(contract: ConsumerContract) -> None:
    description = "a request for the signed-in user"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 is signed in")
        .with_request("GET", "/api/v1/auth/me")
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(200)
        .with_body(user(), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        response = api.as_token(EXAMPLE_ACCESS_TOKEN).auth.me()
        assert response.status_code == 200
        assert response.json()["role"] in {"customer", "premium_customer", "admin"}

    contract.verify(description, exercise)
