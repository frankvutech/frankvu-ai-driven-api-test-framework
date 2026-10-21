"""Allure attachments and run metadata.

The only module that calls `allure.attach`. Every attachment is redacted and size-capped here, so a
test cannot attach a raw token by accident — there is no other path to the report.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import allure

from api_tests.redaction import Redactor, truncate

_DEFAULT_MAX_BYTES = 64 * 1024


class AllureReporter:
    def __init__(self, redactor: Redactor, *, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        self.redactor = redactor
        self.max_bytes = max_bytes

    def attach_json(self, name: str, payload: Any) -> None:
        text = json.dumps(self.redactor.data(payload), indent=2, sort_keys=False, default=str)
        allure.attach(truncate(text, self.max_bytes), name=name, attachment_type=allure.attachment_type.JSON)

    def attach_text(self, name: str, text: str) -> None:
        allure.attach(
            truncate(self.redactor.text(text), self.max_bytes),
            name=name,
            attachment_type=allure.attachment_type.TEXT,
        )

    def attach_file(self, name: str, path: Path) -> None:
        """Attach a text file (a Pact contract, a verifier log) after redaction."""
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            try:
                self.attach_json(name, json.loads(content))
            except ValueError:
                pass
            else:
                return
        self.attach_text(name, content)


def write_environment(results_dir: Path, properties: Mapping[str, str]) -> None:
    """`environment.properties`: the Environment widget on the report's overview page."""
    results_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={_escape(value)}" for key, value in sorted(properties.items())]
    (results_dir / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_executor(results_dir: Path, *, run_id: str, build_url: str | None, ci: bool) -> None:
    """`executor.json`: who ran this, and a link back to the build when there is one."""
    results_dir.mkdir(parents=True, exist_ok=True)
    executor: dict[str, Any] = {
        "name": "GitHub Actions" if ci else "Local",
        "type": "github" if ci else "local",
        "buildName": run_id,
    }
    if build_url:
        executor["buildUrl"] = build_url
        executor["reportUrl"] = build_url
    (results_dir / "executor.json").write_text(json.dumps(executor, indent=2), encoding="utf-8")


# Allure groups failures into these buckets on the Categories tab. Ordered: the first match wins.
CATEGORIES: list[dict[str, Any]] = [
    {
        "name": "Backend unreachable",
        "matchedStatuses": ["broken", "failed"],
        "messageRegex": ".*(ConnectError|ApiTransportError|Connection refused|not reachable).*",
    },
    {
        "name": "Pact verification failure",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*Pact verification failed.*",
    },
    {
        "name": "Contract / schema drift",
        "matchedStatuses": ["failed"],
        "messageRegex": ".*(validation error|Schema mismatch).*",
    },
    {"name": "Assertion failures", "matchedStatuses": ["failed"]},
    {"name": "Framework or environment errors", "matchedStatuses": ["broken"]},
]


def write_categories(results_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "categories.json").write_text(json.dumps(CATEGORIES, indent=2), encoding="utf-8")


def _escape(value: str) -> str:
    # Java properties: backslashes and line breaks would otherwise corrupt the file.
    return value.replace("\\", "\\\\").replace("\n", "\\n")
