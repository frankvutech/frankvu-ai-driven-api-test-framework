"""Signing in, and the identities tests act as."""

from __future__ import annotations

from dataclasses import dataclass

import allure
import pytest
from pydantic import SecretStr, ValidationError

from api_tests.config import Account
from api_tests.fixtures.data import registration_identity
from api_tests.resources import ShopApi
from api_tests.resources.models import Authenticated, Tokens, User


@dataclass(frozen=True, slots=True)
class SignedIn:
    """An account, its tokens, and every resource client bound to its access token."""

    account: Account
    user: User
    tokens: Tokens
    api: ShopApi

    @property
    def role(self) -> str:
        return self.account.role


def sign_in(api: ShopApi, account: Account) -> SignedIn:
    with allure.step(f"Sign in as the {account.role} account"):
        response = api.anonymous().auth.login(account.email, account.password)
        if response.status_code != 200:
            pytest.fail(
                f"Could not sign in as {account.role} ({account.email}): HTTP {response.status_code}. "
                "Check MTP_*_EMAIL / MTP_*_PASSWORD and that the backend is seeded.",
                pytrace=False,
            )
        try:
            body = Authenticated.model_validate(response.json())
        except ValidationError as error:
            pytest.fail(f"Login response does not match the contract: {error}", pytrace=False)
        return SignedIn(account, body.user, body.tokens, api.as_token(body.tokens.access_token))


def register(api: ShopApi, *, run_id: str, label: str, domain: str) -> SignedIn:
    """Register a brand-new customer. Its cart, wishlist and orders start empty."""
    email, password = registration_identity(run_id, label, domain)
    account = Account("registered", email, SecretStr(password))
    api.client.redactor.add_secret(password)
    with allure.step(f"Register a fresh customer ({label})"):
        response = api.anonymous().auth.register(email, account.password, "Api", "Shopper")
        if response.status_code != 201:
            pytest.fail(
                f"Could not register {email}: HTTP {response.status_code} {response.text[:300]}",
                pytrace=False,
            )
        body = Authenticated.model_validate(response.json())
    return SignedIn(account, body.user, body.tokens, api.as_token(body.tokens.access_token))
