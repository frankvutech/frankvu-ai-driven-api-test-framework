"""Authentication: register, login, refresh, logout and `/me`.

Endpoints: `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`,
`GET /auth/me`. Refresh/logout tests use a freshly registered `shopper`, never a seeded account:
refresh-token reuse revokes *every* session the user has.
"""

from __future__ import annotations

from collections.abc import Callable

import allure
import pytest

from api_tests.config import Account, Settings
from api_tests.fixtures import SignedIn
from api_tests.fixtures.data import registration_identity
from api_tests.resources import ShopApi
from api_tests.resources.models import Authenticated, Tokens, User
from support.assertions import assert_error, assert_schema, assert_status

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Authentication"),
    allure.epic("API"),
    allure.feature("Authentication"),
]

ROLES = [("customer", "customer"), ("premium", "premium_customer"), ("admin", "admin")]


# --------------------------------------------------------------------------------- login


@pytest.mark.smoke
@allure.story("Login")
@allure.severity(allure.severity_level.BLOCKER)
@pytest.mark.parametrize(("account_key", "role"), ROLES, ids=[r[0] for r in ROLES])
def test_login_succeeds_for_each_seeded_role(api: ShopApi, settings: Settings, account_key: str, role: str) -> None:
    account = settings.account(account_key)

    body = assert_schema(api.auth.login(account.email, account.password), Authenticated)

    assert body.user.email == account.email
    assert body.user.role == role
    assert body.user.status == "active"
    assert body.tokens.token_type == "Bearer"
    assert body.tokens.access_token != body.tokens.refresh_token


@allure.story("Login")
def test_login_email_ignores_case_and_surrounding_whitespace(api: ShopApi, settings: Settings) -> None:
    account = settings.account("customer")

    body = assert_schema(api.auth.login(f"  {account.email.upper()}  ", account.password), Authenticated)

    assert body.user.email == account.email


@allure.story("Login")
@allure.severity(allure.severity_level.CRITICAL)
def test_wrong_password_is_401_invalid_credentials(api: ShopApi, settings: Settings) -> None:
    response = api.auth.login(settings.account("customer").email, "Definitely-Wrong-1")

    assert_error(response, 401, "error_invalid_credentials")
    assert response.headers["WWW-Authenticate"] == "Bearer"


@allure.story("Login")
@allure.severity(allure.severity_level.CRITICAL)
def test_unknown_email_is_indistinguishable_from_wrong_password(api: ShopApi, settings: Settings) -> None:
    # Distinguishing the two would turn the login form into an oracle for which addresses exist.
    wrong_password = api.auth.login(settings.account("customer").email, "Definitely-Wrong-1")
    unknown_email = api.auth.login("nobody@shoptest.local", "Definitely-Wrong-1")

    assert_error(unknown_email, 401, "error_invalid_credentials")
    assert unknown_email.json() == wrong_password.json()


@allure.story("Login")
@allure.severity(allure.severity_level.CRITICAL)
def test_locked_account_with_correct_password_is_refused(api: ShopApi, locked_account: Account) -> None:
    assert_error(api.auth.login(locked_account.email, locked_account.password), 401, "error_account_locked")


@allure.story("Login")
def test_locked_account_with_wrong_password_does_not_reveal_the_lock(api: ShopApi, locked_account: Account) -> None:
    assert_error(api.auth.login(locked_account.email, "Definitely-Wrong-1"), 401, "error_invalid_credentials")


@allure.story("Login")
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"email": "customer@shoptest.local"}, "password"),
        ({"password": "x"}, "email"),
        ({"email": "not-an-email", "password": "x"}, "email"),
        ({"email": "customer@shoptest.local", "password": ""}, "password"),
    ],
    ids=["missing-password", "missing-email", "malformed-email", "empty-password"],
)
def test_login_validation_errors_name_the_field(api: ShopApi, body: dict[str, str], field: str) -> None:
    envelope = assert_error(api.client.post("/auth/login", json=body), 422, "error_validation")

    locations = [error["loc"] for error in envelope.error.details["errors"]]
    assert ["body", field] in locations


@allure.story("Login")
def test_login_with_a_non_json_body_is_422(api: ShopApi) -> None:
    response = api.client.post("/auth/login", headers={"Content-Type": "application/json"}, json=None)

    assert_error(response, 422, "error_validation")


# --------------------------------------------------------------------------------- registration


@allure.story("Registration")
@allure.severity(allure.severity_level.CRITICAL)
def test_register_creates_an_active_customer_and_signs_it_in(api: ShopApi, settings: Settings) -> None:
    email, password = registration_identity(settings.run_id, "register-happy-path", settings.registration_domain)

    body = assert_schema(api.auth.register(email, password, "Ada", "Lovelace"), Authenticated, 201)

    assert body.user.email == email
    assert (body.user.first_name, body.user.last_name, body.user.full_name) == ("Ada", "Lovelace", "Ada Lovelace")
    assert (body.user.role, body.user.status) == ("customer", "active")
    me = assert_schema(api.as_token(body.tokens.access_token).auth.me(), User)
    assert me.id == body.user.id


@allure.story("Registration")
@allure.severity(allure.severity_level.CRITICAL)
def test_register_cannot_choose_its_own_role(api: ShopApi, settings: Settings) -> None:
    email, password = registration_identity(settings.run_id, "register-role-escalation", settings.registration_domain)

    body = assert_schema(
        api.auth.register(email, password, "Mallory", "Admin", role="admin", status="active"), Authenticated, 201
    )

    assert body.user.role == "customer"


@allure.story("Registration")
@pytest.mark.parametrize("email_case", [str.lower, str.upper], ids=["same-case", "different-case"])
def test_register_an_existing_email_is_409(api: ShopApi, settings: Settings, email_case: Callable[[str], str]) -> None:
    existing = settings.account("customer").email

    response = api.auth.register(email_case(existing), "Another-Password-1", "Dup", "Licate")

    assert_error(response, 409, "error_email_already_registered")


@allure.story("Registration")
@allure.severity(allure.severity_level.CRITICAL)
def test_register_with_a_short_password_is_422_and_never_echoes_it(api: ShopApi, settings: Settings) -> None:
    email, _ = registration_identity(settings.run_id, "register-short-password", settings.registration_domain)

    response = api.auth.register(email, "Sh0rt!", "Short", "Password")

    envelope = assert_error(response, 422, "error_validation")
    assert ["body", "password"] in [error["loc"] for error in envelope.error.details["errors"]]
    assert "Sh0rt!" not in response.text


@allure.story("Registration")
@pytest.mark.parametrize("field", ["first_name", "last_name"])
def test_register_with_a_blank_name_is_422(api: ShopApi, settings: Settings, field: str) -> None:
    email, password = registration_identity(settings.run_id, f"register-blank-{field}", settings.registration_domain)
    names = {"first_name": "Blank", "last_name": "Name", field: "   "}

    response = api.auth.register(email, password, names["first_name"], names["last_name"])

    assert_error(response, 422, "error_validation")


# --------------------------------------------------------------------------------- refresh and logout


@allure.story("Token refresh")
@allure.severity(allure.severity_level.CRITICAL)
def test_refresh_rotates_the_token_pair(api: ShopApi, shopper: SignedIn) -> None:
    rotated = assert_schema(api.auth.refresh(shopper.tokens.refresh_token), Tokens)

    assert rotated.refresh_token != shopper.tokens.refresh_token
    assert_schema(api.as_token(rotated.access_token).auth.me(), User)


@allure.story("Token refresh")
@allure.severity(allure.severity_level.CRITICAL)
def test_reusing_a_refresh_token_revokes_every_session(api: ShopApi, shopper: SignedIn) -> None:
    rotated = assert_schema(api.auth.refresh(shopper.tokens.refresh_token), Tokens)

    with allure.step("Replay the already-used refresh token"):
        assert_error(api.auth.refresh(shopper.tokens.refresh_token), 401, "error_invalid_token")
    with allure.step("The token issued by the legitimate refresh is now revoked too"):
        assert_error(api.auth.refresh(rotated.refresh_token), 401, "error_invalid_token")


@allure.story("Token refresh")
@pytest.mark.parametrize("kind", ["access-token", "garbage"])
def test_refresh_rejects_anything_but_a_refresh_token(api: ShopApi, shopper: SignedIn, kind: str) -> None:
    token = shopper.tokens.access_token if kind == "access-token" else "not.a.jwt"

    assert_error(api.auth.refresh(token), 401, "error_invalid_token")


@allure.story("Logout")
def test_logout_revokes_the_refresh_token(api: ShopApi, shopper: SignedIn) -> None:
    assert_status(shopper.api.auth.logout(shopper.tokens.refresh_token), 204)

    assert_error(api.auth.refresh(shopper.tokens.refresh_token), 401, "error_invalid_token")


@allure.story("Logout")
def test_access_token_keeps_working_after_logout_until_it_expires(shopper: SignedIn) -> None:
    # Documented trade-off: access tokens are not revocable; their 15-minute lifetime is the window.
    assert_status(shopper.api.auth.logout(shopper.tokens.refresh_token), 204)

    assert_schema(shopper.api.auth.me(), User)


@allure.story("Logout")
def test_logout_requires_an_access_token(api: ShopApi, shopper: SignedIn) -> None:
    assert_error(api.auth.logout(shopper.tokens.refresh_token), 401, "error_invalid_token")


@allure.story("Logout")
def test_logout_of_an_unknown_token_is_still_204(shopper: SignedIn) -> None:
    # Reporting "unknown token" would let a caller probe which tokens exist.
    assert_status(shopper.api.auth.logout("never-issued-token"), 204)


# --------------------------------------------------------------------------------- /me


@pytest.mark.smoke
@allure.story("Current user")
def test_me_returns_the_signed_in_user(customer: SignedIn) -> None:
    me = assert_schema(customer.api.auth.me(), User)

    assert me == customer.user


@allure.story("Current user")
def test_me_never_exposes_the_password_hash(customer: SignedIn) -> None:
    response = customer.api.auth.me()

    assert "password" not in response.text.lower()


@allure.story("Current user")
@allure.severity(allure.severity_level.CRITICAL)
def test_me_without_a_token_is_401_not_403(api: ShopApi) -> None:
    # FastAPI's HTTPBearer answers a missing header with 403 by default; the backend fixes that.
    response = api.auth.me()

    assert_error(response, 401, "error_invalid_token")
    assert response.headers["WWW-Authenticate"] == "Bearer"


@allure.story("Current user")
@pytest.mark.parametrize(
    "authorization",
    ["Bearer not-a-jwt", "Basic Y3VzdG9tZXI6cGFzc3dvcmQ=", "Bearer", "Token abc"],
    ids=["malformed-jwt", "basic-scheme", "scheme-without-credentials", "unknown-scheme"],
)
def test_me_with_malformed_authorization_is_401(api: ShopApi, authorization: str) -> None:
    response = api.client.get("/auth/me", headers={"Authorization": authorization})

    assert_error(response, 401, "error_invalid_token")


@allure.story("Current user")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_refresh_token_is_not_an_access_token(api: ShopApi, shopper: SignedIn) -> None:
    assert_error(api.as_token(shopper.tokens.refresh_token).auth.me(), 401, "error_invalid_token")


@allure.story("Current user")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_token_with_a_tampered_signature_is_401(api: ShopApi, customer: SignedIn) -> None:
    header, payload, signature = customer.tokens.access_token.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}{'AA' if not signature.endswith('AA') else 'BB'}"

    assert_error(api.as_token(tampered).auth.me(), 401, "error_invalid_token")
