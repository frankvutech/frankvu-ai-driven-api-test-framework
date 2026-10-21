"""Configuration safety: default-deny targeting, reset rules, credentials."""

from __future__ import annotations

import re

import allure
import pytest
from pydantic import SecretStr, ValidationError

from api_tests.config import ResetMode, Settings, TargetEnvironment, is_non_production_host

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("Configuration"),
]


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "::1",
        "10.1.2.3",
        "192.168.0.10",
        "backend",
        "api.shoptest.local",
        "x.test",
        "host.docker.internal",
    ],
)
def test_recognised_test_hosts(host: str) -> None:
    assert is_non_production_host(host)


@pytest.mark.parametrize("host", ["api.shoptest.com", "8.8.8.8", "shop.example.com", ""])
def test_unknown_hosts_are_not_assumed_safe(host: str) -> None:
    assert not is_non_production_host(host)


def test_defaults_are_a_safe_local_target() -> None:
    settings = Settings()

    assert settings.base_url == "http://localhost:8000"
    assert settings.environment is TargetEnvironment.LOCAL
    assert settings.reset_mode is ResetMode.SESSION
    assert settings.api_url == "http://localhost:8000/api/v1"


def test_a_production_looking_url_is_refused() -> None:
    with pytest.raises(ValidationError, match=re.escape("Refusing to target 'api.shoptest.com'")):
        Settings(base_url="https://api.shoptest.com")


def test_an_allow_listed_host_is_accepted() -> None:
    settings = Settings(
        base_url="https://staging-api.shoptest.com",
        environment="staging",
        allowed_hosts="staging-api.shoptest.com, other.host",
    )

    assert settings.allowed_hosts == ("staging-api.shoptest.com", "other.host")


def test_the_unsafe_override_is_explicit() -> None:
    assert Settings(base_url="https://api.shoptest.com", allow_unsafe_target=True).base_url


def test_staging_defaults_to_no_reset_and_refuses_one() -> None:
    staging = Settings(base_url="http://staging.local", environment="staging")
    assert staging.reset_mode is ResetMode.NONE

    with pytest.raises(ValidationError, match="not allowed against staging"):
        Settings(base_url="http://staging.local", environment="staging", reset_mode="session")


def test_production_is_not_an_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_base_url_must_be_absolute_http() -> None:
    with pytest.raises(ValidationError, match="absolute http"):
        Settings(base_url="localhost:8000")


def test_seed_credentials_are_defaults_only_locally() -> None:
    assert Settings().account("customer").email == "customer@shoptest.local"

    with pytest.raises(LookupError, match="MTP_CUSTOMER_EMAIL"):
        Settings(base_url="http://staging.local", environment="staging").account("customer")


def test_configured_credentials_win() -> None:
    settings = Settings(customer_email="qa@shoptest.local", customer_password=SecretStr("Configured-1"))

    account = settings.account("customer")
    assert (account.email, account.password.get_secret_value()) == ("qa@shoptest.local", "Configured-1")


def test_account_repr_never_shows_the_password() -> None:
    assert "Customer123!" not in repr(Settings().account("customer"))


def test_secret_values_feed_the_redactor() -> None:
    settings = Settings(pact_broker_token=SecretStr("broker-token-value"))

    assert {"broker-token-value", "Customer123!", "Admin123!"} <= set(settings.secret_values())


def test_broker_source_needs_a_broker_url() -> None:
    with pytest.raises(ValidationError, match="requires MTP_PACT_BROKER_URL"):
        Settings(pact_source="broker")


def test_broker_auth_is_token_or_basic_not_both() -> None:
    with pytest.raises(ValidationError, match="either a Pact Broker token"):
        Settings(
            pact_broker_url="http://broker.local",
            pact_broker_token=SecretStr("t"),
            pact_broker_username="u",
        )


def test_run_id_is_sanitised_and_capped() -> None:
    settings = Settings(run_id="CI run #42\n" + "x" * 100)

    assert settings.run_id.startswith("CI-run--42-")
    assert len(settings.run_id) == 64
    assert settings.run_dir.name == settings.run_id


def test_environment_variables_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MTP_BASE_URL", "http://backend:8000")
    monkeypatch.setenv("MTP_ENVIRONMENT", "ci")

    settings = Settings()

    assert (settings.base_url, settings.environment) == ("http://backend:8000", TargetEnvironment.CI)


def test_describe_contains_no_secrets() -> None:
    described = " ".join(Settings(pact_broker_token=SecretStr("broker-token-value")).describe().values())

    assert "broker-token-value" not in described
    assert "Customer123!" not in described


def test_blank_values_from_ci_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # GitHub Actions renders a missing secret as "".
    for name in ("MTP_PACT_BROKER_URL", "MTP_PACT_BROKER_TOKEN", "MTP_BACKEND_VERSION", "MTP_CUSTOMER_PASSWORD"):
        monkeypatch.setenv(name, "")

    settings = Settings()

    assert (settings.pact_broker_url, settings.pact_broker_token, settings.backend_version) == (None, None, None)
    assert not settings.broker_enabled
    assert settings.account("customer").password.get_secret_value() == "Customer123!"
