"""Framework configuration, read from `MTP_*` environment variables, `.env`, and pytest options.

Everything the framework can be pointed at lives in `Settings`. Nothing else reads `os.environ`, so
the full set of knobs is one file long and a malformed value fails before a single request is sent.

**Default-deny targeting.** The framework writes data: it registers accounts, fills carts, places
orders and — in `local`/`ci` — resets the database. So a base URL is refused unless its host is
recognisably non-production (loopback, a private address, a `.local`/`.test` name, a Compose service
name) or explicitly listed in `MTP_ALLOWED_HOSTS`. `MTP_ALLOW_UNSAFE_TARGET=true` is the only way past
that, and it is deliberately loud.
"""

from __future__ import annotations

import ipaddress
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class TargetEnvironment(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"


class ResetMode(StrEnum):
    """How backend state is returned to the documented baseline.

    `session` calls `POST /test-data/reset` once at session start (and per test for `destructive`
    tests). `none` never resets — the only safe choice against a shared environment, where the
    `/test-data` router is not mounted anyway.
    """

    SESSION = "session"
    NONE = "none"


class PactSource(StrEnum):
    LOCAL = "local"
    BROKER = "broker"


# Hosts that cannot be production by construction: loopback, Docker's host alias, and the reserved
# or conventional private suffixes. A single-label name (`backend`, `api`) only resolves inside a
# Compose or Kubernetes network, so it is a test-network service, not a public endpoint.
_SAFE_SUFFIXES = (".local", ".localhost", ".test", ".internal", ".example")
_SAFE_NAMES = frozenset({"localhost", "host.docker.internal"})
_RUN_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]")
# The backend caps X-Test-Run-ID at 64 characters; we stay under it so the echoed id is ours verbatim.
MAX_RUN_ID_LENGTH = 64

# The documented seed accounts (backend `scripts/seed.py`, `docs/test-credentials.md`). Public by
# design and only ever used as defaults for `local` and `ci`; `staging` must supply its own.
_SEED_ACCOUNTS: dict[str, tuple[str, str]] = {
    "customer": ("customer@shoptest.local", "Customer123!"),
    "premium": ("premium@shoptest.local", "Premium123!"),
    "admin": ("admin@shoptest.local", "Admin123!"),
    "locked": ("locked@shoptest.local", "Locked123!"),
}


def is_non_production_host(host: str, extra_allowed: list[str] | tuple[str, ...] = ()) -> bool:
    """Whether `host` is recognisably a test target. Unknown means *no*."""
    host = host.lower().strip("[]")
    if not host:
        return False
    if host in _SAFE_NAMES or host in {h.lower() for h in extra_allowed}:
        return True
    if host.endswith(_SAFE_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host
    return address.is_loopback or address.is_private


def default_run_id() -> str:
    """`API-<UTC timestamp>-<pid>`: unique per run, so results directories never collide.

    The run id is metadata — it names directories and tags requests — and is never asserted on, so
    using the clock here does not make any test nondeterministic.
    """
    return f"API-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"


def sanitise_run_id(raw: str) -> str:
    cleaned = _RUN_ID_UNSAFE.sub("-", raw.strip())[:MAX_RUN_ID_LENGTH]
    if not cleaned:
        raise ValueError("run id must contain at least one of [A-Za-z0-9._:-]")
    return cleaned


class Account:
    """One set of credentials. The password stays a `SecretStr` until the moment it is sent."""

    __slots__ = ("email", "password", "role")

    def __init__(self, role: str, email: str, password: SecretStr) -> None:
        self.role = role
        self.email = email
        self.password = password

    def __repr__(self) -> str:
        return f"Account(role={self.role!r}, email={self.email!r}, password=**********)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MTP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- target -----------------------------------------------------------------------------
    base_url: str = "http://localhost:8000"
    api_prefix: str = "/api/v1"
    environment: TargetEnvironment = TargetEnvironment.LOCAL
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ()
    allow_unsafe_target: bool = False

    # --- transport --------------------------------------------------------------------------
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    # Retries apply to *transport* failures on idempotent methods only, never to an HTTP status:
    # retrying a 500 would turn a deterministic server error into a flaky pass.
    retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.25, ge=0, le=5)

    # --- run identity -----------------------------------------------------------------------
    run_id: str = Field(default_factory=default_run_id)
    backend_version: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    build_url: str | None = None

    # --- test data --------------------------------------------------------------------------
    reset_mode: ResetMode | None = None

    customer_email: str | None = None
    customer_password: SecretStr | None = None
    premium_email: str | None = None
    premium_password: SecretStr | None = None
    admin_email: str | None = None
    admin_password: SecretStr | None = None
    locked_email: str | None = None
    locked_password: SecretStr | None = None
    # Domain for accounts registered during a run. `.local` never routes publicly, and it is the one
    # special-use suffix the backend's email validation accepts (`.test` is answered with 422).
    registration_domain: str = "mtp-api-tests.local"

    # --- reporting --------------------------------------------------------------------------
    artifacts_dir: Path = Path("artifacts")
    attachment_max_bytes: int = Field(default=64 * 1024, ge=1024, le=5 * 1024 * 1024)

    # --- pact -------------------------------------------------------------------------------
    pact_dir: Path = Path("pacts")
    pact_external_dir: Path | None = None
    pact_consumer_name: str = "MobileTestPlayground-Mobile-API"
    pact_provider_name: str = "MobileTestPlayground-Backend-API"
    pact_source: PactSource = PactSource.LOCAL
    pact_broker_url: str | None = None
    pact_broker_token: SecretStr | None = None
    pact_broker_username: str | None = None
    pact_broker_password: SecretStr | None = None
    pact_publish_results: bool = False
    pact_state_server_host: str = "127.0.0.1"

    @field_validator(
        "backend_version",
        "commit_sha",
        "branch",
        "build_url",
        "reset_mode",
        "pact_external_dir",
        "pact_broker_url",
        "pact_broker_token",
        "pact_broker_username",
        "pact_broker_password",
        *(f"{role}_{part}" for role in _SEED_ACCOUNTS for part in ("email", "password")),
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        # CI systems pass an unset secret as an empty string; treating "" as a value would, e.g., send
        # an empty Broker token instead of no token at all.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("base_url")
    @classmethod
    def _normalise_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        return value.rstrip("/")

    @field_validator("run_id")
    @classmethod
    def _clean_run_id(cls, value: str) -> str:
        return sanitise_run_id(value)

    @model_validator(mode="after")
    def _guard_target(self) -> Self:
        host = urlsplit(self.base_url).hostname or ""
        if not self.allow_unsafe_target and not is_non_production_host(host, self.allowed_hosts):
            raise ValueError(
                f"Refusing to target {host!r}: it is not a recognised non-production host. Add it to "
                "MTP_ALLOWED_HOSTS if it is a dedicated test environment, or set "
                "MTP_ALLOW_UNSAFE_TARGET=true to override deliberately."
            )
        if self.reset_mode is None:
            self.reset_mode = ResetMode.NONE if self.environment is TargetEnvironment.STAGING else ResetMode.SESSION
        if self.environment is TargetEnvironment.STAGING and self.reset_mode is ResetMode.SESSION:
            raise ValueError("MTP_RESET_MODE=session is not allowed against staging: it wipes shared data.")
        if self.pact_source is PactSource.BROKER and not self.pact_broker_url:
            raise ValueError("MTP_PACT_SOURCE=broker requires MTP_PACT_BROKER_URL.")
        if self.pact_broker_token and (self.pact_broker_username or self.pact_broker_password):
            raise ValueError("Configure either a Pact Broker token or a username/password, not both.")
        return self

    # --- derived ----------------------------------------------------------------------------

    @property
    def api_url(self) -> str:
        return f"{self.base_url}{self.api_prefix}"

    @property
    def resets_enabled(self) -> bool:
        return self.reset_mode is ResetMode.SESSION

    @property
    def broker_enabled(self) -> bool:
        return bool(self.pact_broker_url)

    @cached_property
    def run_dir(self) -> Path:
        return self.artifacts_dir / "runs" / self.run_id

    def account(self, role: str) -> Account:
        """Credentials for a seeded role, from configuration or — locally and in CI — the seed."""
        email = getattr(self, f"{role}_email")
        password = getattr(self, f"{role}_password")
        if email is None or password is None:
            if self.environment is TargetEnvironment.STAGING:
                raise LookupError(f"MTP_{role.upper()}_EMAIL and MTP_{role.upper()}_PASSWORD must be set for staging.")
            seed_email, seed_password = _SEED_ACCOUNTS[role]
            email = email or seed_email
            password = password or SecretStr(seed_password)
        return Account(role, email, password)

    def secret_values(self) -> list[str]:
        """Every configured secret, so the redactor can scrub them from free text too."""
        secrets: list[str] = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr) and value.get_secret_value():
                secrets.append(value.get_secret_value())
        for role in _SEED_ACCOUNTS:
            try:
                secrets.append(self.account(role).password.get_secret_value())
            except LookupError:
                continue
        return sorted(set(secrets), key=len, reverse=True)

    def describe(self) -> dict[str, str]:
        """Non-secret run metadata, for the session header and Allure's environment widget."""
        return {
            "target.base_url": self.base_url,
            "target.environment": self.environment.value,
            "target.backend_version": self.backend_version or "unknown",
            "run.id": self.run_id,
            "run.commit": self.commit_sha or "unknown",
            "run.branch": self.branch or "unknown",
            "data.reset_mode": self.reset_mode.value if self.reset_mode else "none",
            "pact.consumer": self.pact_consumer_name,
            "pact.provider": self.pact_provider_name,
            "pact.source": self.pact_source.value,
            "pact.broker": "configured" if self.broker_enabled else "disabled",
        }


def load_settings(**overrides: Any) -> Settings:
    """Build settings, letting explicit (pytest option) overrides win over the environment."""
    return Settings(**{key: value for key, value in overrides.items() if value is not None})
