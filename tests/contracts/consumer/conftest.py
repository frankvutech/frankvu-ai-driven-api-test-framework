"""Consumer-test plumbing: a fresh Pact per test, merged into one contract file per run.

Each test declares one interaction and exercises it through the framework's own resource clients
against the Pact mock provider. The mock provider fails the test if the client's request does not
match what the interaction declares — so the contract records what the client *actually sends*, not
what someone typed into a JSON file.

The contract file is written only after the interaction verified on the mock provider, and the
previous run's file is removed at session start, so a stale or failed interaction can never reach
provider verification or the Broker.
"""

from __future__ import annotations

import pytest

from api_tests.config import Settings
from api_tests.pact import new_pact, pact_file
from api_tests.redaction import Redactor
from api_tests.reporting import AllureReporter
from support.pact_consumer import ConsumerContract


@pytest.fixture(scope="session", autouse=True)
def _fresh_contract_file(settings: Settings) -> None:
    pact_file(settings).unlink(missing_ok=True)


@pytest.fixture
def contract(settings: Settings, redactor: Redactor, reporter: AllureReporter) -> ConsumerContract:
    return ConsumerContract(new_pact(settings), settings, redactor, reporter)
