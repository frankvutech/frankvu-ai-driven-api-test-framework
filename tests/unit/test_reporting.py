"""Allure metadata files and the attachment path (redaction + size limit)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import allure
import pytest

from api_tests.redaction import REDACTED, Redactor
from api_tests.reporting import (
    CATEGORIES,
    AllureReporter,
    write_categories,
    write_environment,
    write_executor,
)

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("Allure reporting"),
]


@pytest.fixture
def attached(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_attach(body: str, name: str, attachment_type: object) -> None:
        captured.append({"body": body, "name": name, "type": attachment_type})

    monkeypatch.setattr(allure, "attach", fake_attach)
    return captured


def test_json_attachments_are_redacted(attached: list[dict[str, Any]]) -> None:
    AllureReporter(Redactor()).attach_json("exchange", {"password": "hunter22", "ok": True})

    assert json.loads(attached[0]["body"]) == {"password": REDACTED, "ok": True}


def test_attachments_are_size_limited(attached: list[dict[str, Any]]) -> None:
    AllureReporter(Redactor(), max_bytes=1_024).attach_text("log", "x" * 10_000)

    assert len(attached[0]["body"].encode()) < 1_100
    assert "[truncated" in attached[0]["body"]


def test_file_attachments_are_redacted(attached: list[dict[str, Any]], tmp_path: Path) -> None:
    pact = tmp_path / "pact.json"
    pact.write_text(json.dumps({"request": {"headers": {"Authorization": "Bearer abcdefgh"}}}))

    AllureReporter(Redactor()).attach_file("contract", pact)

    assert "abcdefgh" not in attached[0]["body"]


def test_environment_properties_are_sorted_and_escaped(tmp_path: Path) -> None:
    write_environment(tmp_path, {"b.key": "line1\nline2", "a.key": "C:\\path"})

    assert (tmp_path / "environment.properties").read_text() == "a.key=C:\\\\path\nb.key=line1\\nline2\n"


def test_executor_links_back_to_the_build(tmp_path: Path) -> None:
    write_executor(tmp_path, run_id="RUN-1", build_url="https://ci.local/run/1", ci=True)

    executor = json.loads((tmp_path / "executor.json").read_text())
    assert executor == {
        "name": "GitHub Actions",
        "type": "github",
        "buildName": "RUN-1",
        "buildUrl": "https://ci.local/run/1",
        "reportUrl": "https://ci.local/run/1",
    }


def test_categories_file_matches_the_definitions(tmp_path: Path) -> None:
    write_categories(tmp_path)

    assert json.loads((tmp_path / "categories.json").read_text()) == CATEGORIES
    assert CATEGORIES[-1]["matchedStatuses"] == ["broken"]
