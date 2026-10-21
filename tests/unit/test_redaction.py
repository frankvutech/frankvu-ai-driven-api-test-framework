"""Redaction: every attachment and log line passes through `Redactor`."""

from __future__ import annotations

import json
from typing import Any

import allure
import pytest

from api_tests.redaction import REDACTED, Redactor, truncate

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("Redaction"),
]

# The public jwt.io example token: a known, inert JWT.
JWT = ".".join(  # gitleaks:allow
    [
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ]
)


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Password",
        "access_token",
        "refreshToken",
        "Refresh-Token",
        "authorization",
        "cookie",
        "cvv",
        "api_key",
    ],
)
def test_sensitive_keys_are_redacted_whatever_their_spelling(key: str) -> None:
    assert Redactor().data({key: "s3cret-value"}) == {key: REDACTED}


def test_nested_structures_are_redacted_and_the_original_is_untouched() -> None:
    original: dict[str, Any] = {"user": {"email": "a@b.local"}, "tokens": [{"access_token": "x" * 30}]}

    redacted = Redactor().data(original)

    assert redacted == {"user": {"email": "a@b.local"}, "tokens": [{"access_token": REDACTED}]}
    assert original["tokens"][0]["access_token"] == "x" * 30


def test_card_number_is_redacted_only_inside_card_details() -> None:
    body = {"card": {"number": "4111111111111111", "holder": "ALEX"}, "order_number": "SHP-1001"}

    assert Redactor().data(body) == {"card": {"number": REDACTED, "holder": "ALEX"}, "order_number": "SHP-1001"}


def test_jwts_and_bearer_credentials_are_scrubbed_from_free_text() -> None:
    text = Redactor().text(f"token={JWT} and Authorization: Bearer abcdef123456")

    assert JWT not in text
    assert "abcdef123456" not in text
    assert "Bearer [REDACTED]" in text


def test_card_numbers_are_masked_in_free_text_but_uuids_are_not() -> None:
    uuid = "6f1c2a9e-1b2c-4d3e-8f40-000000000001"
    text = Redactor().text(f"card 4111 1111 1111 1111 for {uuid} at 2026-01-20")

    assert "4111 1111 1111 1111" not in text
    assert "PAN-1111" in text
    assert uuid in text
    assert "2026-01-20" in text


def test_known_secrets_are_scrubbed_anywhere() -> None:
    redactor = Redactor(["Customer123!"])

    assert redactor.text("login failed for password=Customer123! (retry)") == (
        f"login failed for password={REDACTED} (retry)"
    )


def test_secrets_added_later_are_scrubbed() -> None:
    redactor = Redactor()
    redactor.add_secret("late-secret-value")

    assert "late-secret-value" not in redactor.text("x late-secret-value y")


def test_short_values_are_not_treated_as_secrets() -> None:
    # A one-character "secret" would shred every attachment.
    assert Redactor(["a"]).text("banana") == "banana"


def test_headers_keep_the_scheme_but_not_the_credential() -> None:
    headers = Redactor().headers({"Authorization": "Bearer abc.def.ghi", "Content-Type": "application/json"})

    assert headers == {"Authorization": f"Bearer {REDACTED}", "Content-Type": "application/json"}


def test_urls_lose_userinfo_and_sensitive_query_parameters() -> None:
    url = Redactor().url("https://user:pw@broker.local/pacts?token=abc&page=2")

    assert url == f"https://{REDACTED}@broker.local/pacts?token={REDACTED}&page=2"


def test_json_bodies_are_parsed_then_redacted() -> None:
    body = json.dumps({"email": "a@b.local", "password": "hunter22"}).encode()

    assert Redactor().body(body, "application/json") == {"email": "a@b.local", "password": REDACTED}


def test_non_json_bodies_are_text_redacted() -> None:
    assert Redactor().body(f"Bearer {JWT}", "text/plain") == f"Bearer {REDACTED}"


def test_empty_bodies_are_none() -> None:
    assert Redactor().body(b"", "application/json") is None


def test_truncate_caps_utf8_bytes_and_says_so() -> None:
    text = "é" * 100  # 200 bytes

    capped = truncate(text, 51)

    assert capped.startswith("é" * 25)
    assert "[truncated 150 bytes]" in capped
    assert truncate("short", 100) == "short"
