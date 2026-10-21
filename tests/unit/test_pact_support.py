"""Pact plumbing without a backend: matchers, provider-state registry, the state server's lifecycle,
verification setup, and contract provenance."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import allure
import httpx
import pytest
from pact.match.matcher import AbstractMatcher

from api_tests.client import HttpClient
from api_tests.config import Settings
from api_tests.pact import (
    ProviderStateError,
    ProviderStateRegistry,
    StateChangeServer,
    StateContext,
    VerificationSetupError,
    bearer_from_state,
    contract_source,
    from_state,
    local_pact_files,
    path_from_state,
    registry,
)
from api_tests.pact.verification import build_verifier
from api_tests.redaction import REDACTED, Redactor
from api_tests.resources import ShopApi

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("Pact support"),
]


def _json(matcher: AbstractMatcher[Any]) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(json.dumps(matcher.to_integration_json()))
    return document


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(pact_dir=tmp_path / "pacts", run_id="UNIT")


# --------------------------------------------------------------------------------- matchers


def test_path_from_state_has_an_example_a_regex_and_a_generator() -> None:
    matcher = _json(path_from_state("/api/v1/orders/{}", "6f1c2a9e-1b2c-4d3e-8f40-0000000000c1", "orderId"))

    assert matcher["value"] == "/api/v1/orders/6f1c2a9e-1b2c-4d3e-8f40-0000000000c1"
    assert re.match(matcher["regex"], matcher["value"])
    assert not re.match(matcher["regex"], "/api/v1/orders/product_001")
    assert matcher["pact:generator:type"] == "ProviderState"
    assert matcher["expression"] == "/api/v1/orders/${orderId}"


def test_bearer_from_state_never_contains_a_real_token() -> None:
    matcher = _json(bearer_from_state())

    assert matcher["value"] == "Bearer example.access.token"
    assert (matcher["pact:generator:type"], matcher["expression"]) == ("ProviderState", "Bearer ${accessToken}")


def test_from_state_without_regex_is_a_type_matcher() -> None:
    assert _json(from_state("example", "${password}"))["pact:matcher:type"] == "type"


# --------------------------------------------------------------------------------- registry


def test_registry_matches_patterns_and_merges_values(settings: Settings) -> None:
    states = ProviderStateRegistry()
    states.state(r"product (?P<reference>product_\d{3}) is available")(lambda ctx, m, p: {"productId": m["reference"]})
    states.state(r"signed in")(lambda ctx, m, p: {"accessToken": "t"})
    context = StateContext(api=None, settings=settings)  # type: ignore[arg-type]

    states.setup(context, "signed in", {})
    values = states.setup(context, "product product_007 is available", {})

    assert values == {"accessToken": "t", "productId": "product_007"}


def test_unknown_states_fail_loudly(settings: Settings) -> None:
    context = StateContext(api=None, settings=settings)  # type: ignore[arg-type]

    with pytest.raises(ProviderStateError, match="Unknown provider state"):
        ProviderStateRegistry().setup(context, "the moon is full", {})


def test_every_state_used_by_the_contracts_is_registered() -> None:
    used = {
        "customer user_001 exists",
        "customer user_001 is signed in",
        "the catalogue contains the seeded products",
        "product product_001 is available",
        "product product_008 is available",
        "product product_999 does not exist",
        "the customer's cart is empty",
        "the customer's cart contains product_001",
        "the customer's cart contains product_003",
        "coupon WELCOME10 is valid",
        "the customer's wishlist is empty",
        "the customer's wishlist contains product_008",
        "the customer has placed an order",
    }
    patterns = [re.compile(f"^{pattern}$") for pattern in registry.patterns]

    assert all(any(p.match(state) for p in patterns) for state in used)


# --------------------------------------------------------------------------------- state server


def _fake_backend(calls: list[str]) -> ShopApi:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/test-data/reset"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"tokens": {"access_token": "eyJ.fake.token-value"}})
        return httpx.Response(200, json={})

    return ShopApi(
        HttpClient(
            "http://backend.test/api/v1", run_id="UNIT", redactor=Redactor(), transport=httpx.MockTransport(handler)
        )
    )


def test_state_server_resets_once_per_interaction_and_redacts_its_log(settings: Settings) -> None:
    calls: list[str] = []
    with StateChangeServer(registry, _fake_backend(calls), settings, Redactor()) as server:
        status_a, values_a = server.handle({"state": "customer user_001 is signed in", "action": "setup"})
        status_b, values_b = server.handle({"state": "customer user_001 exists", "action": "setup"})
        server.handle({"state": "customer user_001 is signed in", "action": "teardown"})
        server.handle({"state": "customer user_001 exists", "action": "setup"})

    assert (status_a, status_b) == (200, 200)
    assert values_a == {"accessToken": "eyJ.fake.token-value"}
    assert values_b["password"] == "Customer123!"
    assert calls.count("POST /api/v1/test-data/reset") == 2
    logged = json.dumps(server.events)
    assert "Customer123!" not in logged
    assert "eyJ.fake.token-value" not in logged
    assert REDACTED in logged


def test_a_failing_state_answers_500(settings: Settings) -> None:
    with StateChangeServer(ProviderStateRegistry(), _fake_backend([]), settings, Redactor()) as server:
        status, body = server.handle({"state": "nonsense", "action": "setup"})

    assert status == 500
    assert "Unknown provider state" in body["error"]


# --------------------------------------------------------------------------------- verification setup


def _write_pact(path: Path, provider: str, metadata: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "consumer": {"name": "C"},
        "provider": {"name": provider},
        "interactions": [],
        "metadata": metadata or {},
    }
    path.write_text(json.dumps(document))
    return path


def test_no_pact_files_is_a_setup_error_not_a_pass(settings: Settings) -> None:
    with pytest.raises(VerificationSetupError, match="Run `pytest -m pact_consumer` first"):
        build_verifier(settings, "http://127.0.0.1:1/state", [])


def test_local_pact_files_include_external_contracts_for_this_provider_only(settings: Settings, tmp_path: Path) -> None:
    ours = _write_pact(settings.pact_dir / "ours.json", settings.pact_provider_name)
    _write_pact(settings.pact_dir / "other.json", "SomeOtherProvider")
    external = _write_pact(tmp_path / "mobile" / "android.json", settings.pact_provider_name)
    with_external = settings.model_copy(update={"pact_external_dir": tmp_path / "mobile"})

    assert local_pact_files(with_external) == [ours, external]


def test_publishing_results_needs_a_broker_and_a_provider_version(settings: Settings) -> None:
    pact = _write_pact(settings.pact_dir / "ours.json", settings.pact_provider_name)

    no_broker = settings.model_copy(update={"pact_publish_results": True})
    with pytest.raises(VerificationSetupError, match="needs MTP_PACT_BROKER_URL"):
        build_verifier(no_broker, "http://127.0.0.1:1/state", [pact])

    no_version = no_broker.model_copy(update={"pact_broker_url": "http://broker.local"})
    with pytest.raises(VerificationSetupError, match="MTP_BACKEND_VERSION"):
        build_verifier(no_version, "http://127.0.0.1:1/state", [pact])


def test_contract_source_distinguishes_our_model_from_external_pacts(tmp_path: Path) -> None:
    ours = _write_pact(tmp_path / "a.json", "P", {"mobileTestPlayground": {"contractSource": "python-model"}})
    theirs = _write_pact(tmp_path / "b.json", "P")

    assert (contract_source(ours), contract_source(theirs)) == ("python-model", "external")
