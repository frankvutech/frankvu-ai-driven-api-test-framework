"""Centralised redaction. Everything written to a log, an Allure attachment or a Pact report goes
through `Redactor` first.

Three layers, because each catches what the others miss:

1. **Keys.** A JSON field or header whose *name* is sensitive (`password`, `authorization`,
   `refresh_token`, `cvv`, ...) has its value replaced, whatever the value looks like.
2. **Shapes.** Free text is scanned for things that are secrets by form: JWTs, `Bearer` credentials,
   and card-number-length digit runs.
3. **Known values.** The configured passwords and broker token are scrubbed verbatim wherever they
   appear — inside a query string, an error message, or a verifier log line.

Redaction is applied to *copies*; the objects tests assert on are never altered.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"

# Compared after lower-casing and stripping `-`/`_`, so `refresh_token`, `Refresh-Token` and
# `refreshToken` are one entry.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "newpassword",
        "currentpassword",
        "passwordhash",
        "accesstoken",
        "refreshtoken",
        "token",
        "idtoken",
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "apikey",
        "xapikey",
        "secret",
        "clientsecret",
        "cvv",
        "cvc",
        "cardnumber",
        "pactbrokertoken",
        "pactbrokerpassword",
    }
)
# A card object's `number` is a PAN; elsewhere `number` (e.g. `order_number`) is harmless, so it is
# only redacted inside a mapping that is recognisably card details.
_CARD_CONTAINER_KEYS = frozenset({"card", "carddetails", "payment"})

_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
# Bounded by non-word characters, so the digit tail of a UUID or an order reference is never
# mistaken for a card number.
_PAN = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")


def _canonical(key: str) -> str:
    return key.lower().replace("-", "").replace("_", "")


def is_sensitive_key(key: str) -> bool:
    return _canonical(key) in _SENSITIVE_KEYS


class Redactor:
    def __init__(self, known_secrets: Iterable[str] = (), *, min_secret_length: int = 4) -> None:
        # Longest first, so a secret that contains another is replaced whole.
        self._secrets = sorted(
            {secret for secret in known_secrets if secret and len(secret) >= min_secret_length},
            key=len,
            reverse=True,
        )

    def add_secret(self, secret: str | None) -> None:
        if secret and len(secret) >= 4 and secret not in self._secrets:
            self._secrets.append(secret)
            self._secrets.sort(key=len, reverse=True)

    # --- text ---------------------------------------------------------------------------------

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, REDACTED)
        value = _JWT.sub(REDACTED, value)
        value = _BEARER.sub(lambda m: f"{m.group(1)} {REDACTED}", value)
        return _PAN.sub(self._mask_pan, value)

    @staticmethod
    def _mask_pan(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if not 13 <= len(digits) <= 19:
            return match.group(0)
        return f"{REDACTED}-PAN-{digits[-4:]}"

    # --- structures ---------------------------------------------------------------------------

    def data(self, value: Any, *, _in_card: bool = False) -> Any:
        """Deep-copy `value` with sensitive fields replaced."""
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                key_str = str(key)
                canonical = _canonical(key_str)
                if canonical in _SENSITIVE_KEYS or (_in_card and canonical == "number"):
                    result[key_str] = REDACTED if item not in (None, "") else item
                else:
                    result[key_str] = self.data(item, _in_card=canonical in _CARD_CONTAINER_KEYS)
            return result
        if isinstance(value, list | tuple):
            return [self.data(item, _in_card=_in_card) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value

    def headers(self, headers: Mapping[str, str] | Iterable[tuple[str, str]]) -> dict[str, str]:
        pairs = headers.items() if isinstance(headers, Mapping) else headers
        redacted: dict[str, str] = {}
        for name, value in pairs:
            if is_sensitive_key(name):
                scheme = value.split(" ", 1)[0] if " " in value else ""
                redacted[name] = f"{scheme} {REDACTED}".strip()
            else:
                redacted[name] = self.text(value)
        return redacted

    def url(self, url: str) -> str:
        """Redact credentials in the userinfo part and sensitive query parameters."""
        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = f"{REDACTED}@{netloc.rsplit('@', 1)[1]}"
        query = urlencode(
            [
                (key, REDACTED if is_sensitive_key(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ],
            safe="[]",
        )
        return self.text(urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment)))

    def body(self, content: bytes | str | None, content_type: str | None) -> Any:
        """A request or response body as something safe to show: parsed JSON when possible."""
        if content is None or content in (b"", ""):
            return None
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        if content_type and "json" in content_type.lower():
            try:
                return self.data(json.loads(text))
            except ValueError:
                return self.text(text)
        return self.text(text)


def truncate(text: str, max_bytes: int) -> str:
    """Cap `text` at `max_bytes` of UTF-8, saying how much was dropped."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    kept = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{kept}\n... [truncated {len(encoded) - len(kept.encode('utf-8'))} bytes]"


__all__ = ["REDACTED", "Redactor", "is_sensitive_key", "truncate"]
