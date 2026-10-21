"""Provider verification: every consumer contract replayed against the real backend.

Needs a controlled backend instance (local Compose or a CI service container) running with
`ENVIRONMENT=development` or `testing`: provider states are set through `POST /test-data/reset` and
the public API. Never point this at a shared environment.

The verdict is the Pact verifier's own result. A missing contract is a failure, not a skip — "nothing
to verify" must never read as "verified".
"""

from __future__ import annotations

import allure
import pytest

from api_tests.config import PactSource, Settings
from api_tests.fixtures import BackendInfo
from api_tests.pact import (
    StateChangeServer,
    VerificationSetupError,
    contract_source,
    local_pact_files,
    registry,
    run_verification,
)
from api_tests.redaction import Redactor
from api_tests.reporting import AllureReporter
from api_tests.resources import ShopApi

pytestmark = [
    pytest.mark.pact_provider,
    allure.parent_suite("Pact"),
    allure.suite("Provider verification"),
    allure.epic("Pact"),
    allure.feature("Provider verification"),
]


@allure.title("The backend honours every consumer contract")
@allure.severity(allure.severity_level.BLOCKER)
def test_provider_honours_consumer_contracts(
    settings: Settings,
    backend: BackendInfo,
    api: ShopApi,
    redactor: Redactor,
    reporter: AllureReporter,
) -> None:
    if not backend.automation_endpoints:
        pytest.fail(
            "Provider verification needs the backend's /test-data endpoints to set provider states; "
            "run the backend with ENVIRONMENT=development or testing.",
            pytrace=False,
        )

    pact_files = local_pact_files(settings) if settings.pact_source is PactSource.LOCAL else []
    with allure.step("Collect the contracts to verify"):
        sources = {str(path): contract_source(path) for path in pact_files}
        reporter.attach_json("Contract sources", sources or {"broker": settings.pact_broker_url})
        for path in pact_files:
            reporter.attach_file(f"Contract: {path.name}", path)

    with StateChangeServer(registry, api, settings, redactor, host=settings.pact_state_server_host) as states:
        with allure.step(f"Run the Pact verifier against {settings.base_url}"):
            try:
                result = run_verification(settings, states.url, pact_files, redactor)
            except VerificationSetupError as error:
                pytest.fail(str(error), pytrace=False)
        reporter.attach_json("Provider state changes", states.events)

    reporter.attach_text("Pact verifier output", result.output)
    reporter.attach_json("Pact verifier results", result.raw)
    for interaction in result.interactions:
        status = "pending" if interaction.pending else ("passed" if interaction.passed else "FAILED")
        with allure.step(f"[{status}] {interaction.consumer}: {interaction.description}"):
            reporter.attach_json(
                "Interaction result",
                {"provider_states": interaction.states, "result": interaction.detail or "OK"},
            )

    if not result.interactions and not result.passed:
        pytest.fail(f"Pact verification failed before any interaction ran:\n{result.output}", pytrace=False)
    if not result.passed:
        failed = "\n".join(f"  - {i.consumer}: {i.description}" for i in result.failed) or "  (see output)"
        pytest.fail(
            f"Pact verification failed: {len(result.failed)} of {len(result.interactions)} interactions.\n"
            f"{failed}\n\n{result.output}",
            pytrace=False,
        )
    assert result.interactions, "the verifier reported success without verifying a single interaction"
