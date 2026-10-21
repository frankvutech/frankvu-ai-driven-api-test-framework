"""Assertions that fail with the evidence attached.

Each helper fails with the method, path, status and a redacted body excerpt, which is usually the
whole diagnosis. Error assertions check the stable `code`, never the human `message`: the code is part
of the API, the message is prose that may be reworded at any time.
"""

from __future__ import annotations

import json
from typing import Any

import allure
import httpx
import pytest
from pydantic import BaseModel, ValidationError

from api_tests.redaction import Redactor, truncate
from api_tests.resources.models import ErrorEnvelope

_redactor = Redactor()


def describe(response: httpx.Response) -> str:
    request = response.request
    try:
        body: Any = _redactor.data(response.json())
        text = json.dumps(body, indent=2, default=str)
    except ValueError:
        text = _redactor.text(response.text)
    return f"{request.method} {request.url.path} -> {response.status_code}\n{truncate(text, 2_000)}"


def assert_status(response: httpx.Response, expected: int) -> None:
    with allure.step(f"Assert status is {expected}"):
        if response.status_code != expected:
            pytest.fail(f"Expected HTTP {expected}, got:\n{describe(response)}", pytrace=False)


def assert_schema[M: BaseModel](response: httpx.Response, model: type[M], status: int = 200) -> M:
    """Status, then schema. Returns the parsed model for further assertions."""
    assert_status(response, status)
    with allure.step(f"Assert the body matches {model.__name__}"):
        try:
            return model.model_validate(response.json())
        except ValidationError as error:
            pytest.fail(f"Schema mismatch for {model.__name__}:\n{error}\n\n{describe(response)}", pytrace=False)


def assert_error(response: httpx.Response, status: int, code: str) -> ErrorEnvelope:
    """The error envelope `{"error": {"code", "message", "details"}}`, with this status and code."""
    envelope = assert_schema(response, ErrorEnvelope, status)
    with allure.step(f"Assert error code is {code}"):
        if envelope.error.code != code:
            pytest.fail(f"Expected error code {code!r}, got {envelope.error.code!r}:\n{describe(response)}")
    return envelope


def assert_totals(totals: BaseModel, expected: dict[str, int]) -> None:
    with allure.step("Assert the server-calculated totals"):
        actual = totals.model_dump()
        assert actual == expected, f"totals {actual} != expected {expected}"
