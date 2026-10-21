"""Provider verification: replay every consumer contract against the real backend.

Wraps pact-python's `Verifier` (the Pact Rust verifier via FFI, pinned through `pact-python==4.1.0`)
so that:

* the source is explicit — local pact files, or the Pact Broker;
* the verifier's own outcome decides the result — a pact file merely *existing* verifies nothing;
* output and per-interaction results are captured, redacted, for the report;
* results are published to the Broker only when explicitly enabled.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pact import Verifier

from api_tests.config import PactSource, Settings
from api_tests.redaction import Redactor

logger = logging.getLogger("api_tests.pact")


class VerificationSetupError(RuntimeError):
    """Verification cannot start: nothing to verify, or publishing misconfigured."""


@dataclass
class InteractionResult:
    consumer: str
    description: str
    states: list[str]
    passed: bool
    pending: bool
    detail: str | None = None


@dataclass
class VerificationResult:
    passed: bool
    sources: list[str]
    output: str
    interactions: list[InteractionResult] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    published: bool = False

    @property
    def failed(self) -> list[InteractionResult]:
        return [i for i in self.interactions if not i.passed and not i.pending]


def local_pact_files(settings: Settings) -> list[Path]:
    """Every pact file to verify locally: ours, plus any supplied by the mobile repositories."""
    directories = [settings.pact_dir]
    if settings.pact_external_dir is not None:
        directories.append(settings.pact_external_dir)
    files: list[Path] = []
    for directory in directories:
        if directory.is_dir():
            files.extend(sorted(path for path in directory.glob("*.json") if path.is_file()))
    return [path for path in files if _is_for_provider(path, settings.pact_provider_name)]


def _is_for_provider(path: Path, provider: str) -> bool:
    try:
        return bool(json.loads(path.read_text(encoding="utf-8"))["provider"]["name"] == provider)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def build_verifier(settings: Settings, state_url: str, pact_files: list[Path]) -> tuple[Verifier, list[str]]:
    verifier = (
        Verifier(settings.pact_provider_name)
        .add_transport(url=settings.base_url)
        .state_handler(state_url, teardown=True, body=True)
        .set_request_timeout(int(settings.timeout_seconds * 1000))
        .set_coloured_output(enabled=False)
        .set_error_on_empty_pact(enabled=True)
    )
    sources: list[str]
    if settings.pact_source is PactSource.BROKER:
        if settings.pact_broker_url is None:  # also enforced by Settings
            raise VerificationSetupError("MTP_PACT_SOURCE=broker requires MTP_PACT_BROKER_URL")
        # Token and basic auth are mutually exclusive in the verifier, as they are in Settings.
        if settings.pact_broker_token is not None:
            builder = verifier.broker_source(
                settings.pact_broker_url,
                token=settings.pact_broker_token.get_secret_value(),
                selector=True,
                use_env=False,
            )
        else:
            password = settings.pact_broker_password.get_secret_value() if settings.pact_broker_password else None
            builder = verifier.broker_source(
                settings.pact_broker_url,
                username=settings.pact_broker_username,
                password=password,
                selector=True,
                use_env=False,
            )
        # The recommended selectors: what is on the consumer's main branch, what is deployed or
        # released, and whatever the consumer has on a branch named like ours.
        builder = builder.consumer_version(main_branch=True).consumer_version(deployed_or_released=True)
        if settings.branch:
            builder = builder.consumer_version(matching_branch=True).provider_branch(settings.branch)
        verifier = builder.include_pending().build()
        sources = [f"broker:{settings.pact_broker_url}"]
    else:
        if not pact_files:
            raise VerificationSetupError(
                f"No pact files for provider {settings.pact_provider_name!r} in {settings.pact_dir}"
                + (f" or {settings.pact_external_dir}" if settings.pact_external_dir else "")
                + ". Run `pytest -m pact_consumer` first (or supply MTP_PACT_EXTERNAL_DIR)."
            )
        for path in pact_files:
            verifier.add_source(path)
        sources = [str(path) for path in pact_files]

    if settings.pact_publish_results:
        if not settings.broker_enabled:
            raise VerificationSetupError("MTP_PACT_PUBLISH_RESULTS=true needs MTP_PACT_BROKER_URL")
        version = settings.backend_version
        if not version:
            raise VerificationSetupError(
                "Publishing verification results needs the provider version: set MTP_BACKEND_VERSION "
                "(e.g. the backend's commit SHA)."
            )
        verifier.set_publish_options(version=version, url=settings.build_url, branch=settings.branch)
    return verifier, sources


@contextmanager
def _silenced_stdout() -> Iterator[None]:
    """Discard what the Rust verifier prints straight to file descriptor 1.

    The FFI writes its report to the process's stdout, below Python, so pytest would capture it
    unredacted and print it on failure — into a CI log. The same text is read back, redacted, through
    `Verifier.output()`, so nothing is lost.
    """
    sys.stdout.flush()
    saved = os.dup(1)
    with tempfile.TemporaryFile() as sink:
        os.dup2(sink.fileno(), 1)
        try:
            yield
        finally:
            sys.stdout.flush()
            os.dup2(saved, 1)
            os.close(saved)


def run_verification(
    settings: Settings, state_url: str, pact_files: list[Path], redactor: Redactor
) -> VerificationResult:
    verifier, sources = build_verifier(settings, state_url, pact_files)
    passed = True
    try:
        with _silenced_stdout():
            verifier.verify()
    except RuntimeError as error:
        # The FFI raises when any non-pending interaction fails; the details are in the output.
        logger.warning("Pact verifier reported failure: %s", error)
        passed = False
    raw: dict[str, Any] = verifier.results
    return VerificationResult(
        passed=passed and not raw.get("errors"),
        sources=sources,
        output=redactor.text(verifier.output(strip_ansi=True)),
        interactions=[_interaction(item) for item in raw.get("interactionResults", [])],
        errors=redactor.data(raw.get("errors", [])),
        raw=redactor.data(raw),
        published=settings.pact_publish_results,
    )


def _interaction(item: dict[str, Any]) -> InteractionResult:
    result = item.get("result")
    passed = result == "OK"
    detail = None if passed else str(result)
    return InteractionResult(
        consumer=str(item.get("consumer", "")),
        description=str(item.get("description", "")),
        states=[str(state) for state in item.get("providerStates", [])],
        passed=passed,
        pending=bool(item.get("pending", False)),
        detail=detail,
    )
