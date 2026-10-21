"""`ConsumerContract`: run one interaction against the Pact mock provider, then record it.

Each consumer test declares one interaction and exercises it through the framework's own resource
clients. The mock provider fails the test if the client's request does not match the interaction, so
the contract records what the client *actually sends*. The interaction is merged into the contract
file only after it verified.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar

import allure
import pytest
from pact import Pact

from api_tests.client import HttpClient
from api_tests.config import Settings
from api_tests.pact import pact_file
from api_tests.redaction import Redactor
from api_tests.reporting import AllureReporter
from api_tests.resources import ShopApi

T = TypeVar("T")


class ConsumerContract:
    def __init__(self, pact: Pact, settings: Settings, redactor: Redactor, reporter: AllureReporter) -> None:
        self.pact = pact
        self._settings = settings
        self._redactor = redactor
        self._reporter = reporter

    def verify(self, description: str, exercise: Callable[[ShopApi], T]) -> T:
        """Run `exercise` against the mock provider, then merge the interaction into the contract."""
        step = allure.step(f"Exercise the consumer against the Pact mock provider: {description}")
        with step, self.pact.serve(raises=True) as server:
            client = HttpClient(
                f"{server.url}{self._settings.api_prefix}",
                run_id=self._settings.run_id,
                redactor=self._redactor,
                reporter=self._reporter,
                timeout_seconds=self._settings.timeout_seconds,
            )
            with client:
                result = exercise(ShopApi(client))
        with allure.step("Merge the verified interaction into the contract file"):
            self.pact.write_file(self._settings.pact_dir, overwrite=False)
            self._attach_interaction(description)
        return result

    def _attach_interaction(self, description: str) -> None:
        document = json.loads(pact_file(self._settings).read_text(encoding="utf-8"))
        for interaction in document.get("interactions", []):
            if interaction.get("description") == description:
                self._reporter.attach_json(f"Pact interaction: {description}", interaction)
                return
        pytest.fail(f"interaction {description!r} was not written to the contract file")
