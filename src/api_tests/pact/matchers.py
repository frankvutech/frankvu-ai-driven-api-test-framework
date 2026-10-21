"""Pact matchers this framework needs beyond `pact.match`.

**Provider-state injection.** Product, order and user ids are UUIDs that PostgreSQL generates, so
they differ between backend instances; access tokens differ on every sign-in. A contract that
hard-coded either would verify against exactly one database. Instead the consumer writes an
*example* value plus a `ProviderState` generator expression (`${productId}`); during verification the
state-change handler returns the real values, and the Pact verifier substitutes them into the request
before replaying it.

pact-python's convenience matchers ignore a generator when an example is given, so these build the
underlying `GenericMatcher` directly. The JSON this produces was checked against a real verification
run (see docs/pact-workflow.md).
"""

from __future__ import annotations

from pact import generate
from pact.match.matcher import AbstractMatcher, GenericMatcher

UUID_REGEX = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
EXAMPLE_ACCESS_TOKEN = "example.access.token"  # noqa: S105 - placeholder, never a real credential


def from_state(example: str, expression: str, *, regex: str | None = None) -> AbstractMatcher[str]:
    """`example` in the contract; `expression` evaluated against provider-state values at verify time."""
    generator = generate.provider_state(expression)
    if regex is None:
        return GenericMatcher("type", example, generator=generator)
    return GenericMatcher("regex", example, generator=generator, extra_fields={"regex": regex})


def path_from_state(template: str, example_id: str, state_key: str) -> AbstractMatcher[str]:
    """A path containing one injected UUID: `path_from_state("/api/v1/orders/{}", ex, "orderId")`."""
    prefix, suffix = template.split("{}")
    return from_state(
        f"{prefix}{example_id}{suffix}",
        f"{prefix}${{{state_key}}}{suffix}",
        regex=f"^{_escape(prefix)}{UUID_REGEX}{_escape(suffix)}$",
    )


def bearer_from_state(state_key: str = "accessToken") -> AbstractMatcher[str]:
    """`Authorization: Bearer <token>` — any bearer in the contract, the real one at verify time."""
    return from_state(f"Bearer {EXAMPLE_ACCESS_TOKEN}", f"Bearer ${{{state_key}}}", regex=r"^Bearer \S+$")


def _escape(text: str) -> str:
    return "".join(f"\\{ch}" if ch in ".^$*+?()[]{}|\\" else ch for ch in text)
