"""The seed table, the pricing restatement, and the response models' self-consistency checks."""

from __future__ import annotations

from typing import Any

import allure
import pytest
from pydantic import ValidationError

from api_tests.fixtures.data import (
    PRODUCTS,
    SEED_PRODUCTS,
    checkout_body,
    expected_totals,
    registration_identity,
    visible_products,
)
from api_tests.resources.models import ErrorEnvelope, ProductPage, Totals

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("Test data and models"),
]


def test_seed_distribution_matches_the_documentation() -> None:
    assert len(SEED_PRODUCTS) == 30
    assert sum(p.discount_price_cents is not None for p in SEED_PRODUCTS) == 9
    assert [p.reference for p in SEED_PRODUCTS if p.is_premium] == ["product_004", "product_014", "product_026"]
    assert [p.reference for p in SEED_PRODUCTS if p.stock == 0] == [
        "product_005",
        "product_017",
        "product_028",
        "product_030",
    ]


@pytest.mark.parametrize(
    ("viewer", "count"), [("anonymous", 26), ("customer", 26), ("premium_customer", 29), ("admin", 30)]
)
def test_visibility_rules(viewer: Any, count: int) -> None:
    assert len(visible_products(viewer)) == count


@pytest.mark.parametrize(
    ("lines", "kwargs", "expected"),
    [
        ([], {}, (0, 0, 0, 0)),
        ([("product_012", 1)], {}, (2_490, 0, 499, 2_989)),
        ([("product_012", 1)], {"discount_percent": 10}, (2_490, 249, 499, 2_740)),
        ([("product_012", 1)], {"shipping_method": "pickup"}, (2_490, 0, 0, 2_490)),
        ([("product_002", 1)], {"shipping_method": "express"}, (12_900, 0, 0, 12_900)),
        # 7 900 qualifies for free shipping before the coupon and not after it.
        ([("product_011", 1)], {"discount_percent": 10}, (7_900, 790, 499, 7_609)),
    ],
)
def test_expected_totals(lines: list[tuple[str, int]], kwargs: dict[str, Any], expected: tuple[int, ...]) -> None:
    totals = expected_totals(lines, **kwargs)

    assert tuple(totals.values()) == expected


def test_registration_identity_is_deterministic_per_run_and_unique_across_runs() -> None:
    first = registration_identity("RUN-A", "label", "mtp.local")

    assert registration_identity("RUN-A", "label", "mtp.local") == first
    assert registration_identity("RUN-B", "label", "mtp.local") != first
    assert first[0].endswith("@mtp.local")
    assert len(first[1]) >= 8


def test_checkout_body_never_includes_a_card_for_non_card_payment() -> None:
    assert "card" not in checkout_body(payment_method="cod")
    assert checkout_body()["card"]["number"] == "4111111111111111"


def _product(reference: str) -> dict[str, Any]:
    seed = PRODUCTS[reference]
    return {
        "id": "6f1c2a9e-1b2c-4d3e-8f40-000000000001",
        "reference": reference,
        "name": seed.name,
        "description": "d",
        "price_cents": seed.price_cents,
        "discount_price_cents": seed.discount_price_cents,
        "effective_price_cents": seed.effective_price_cents,
        "category": {"id": "6f1c2a9e-1b2c-4d3e-8f40-000000000002", "name": "c", "slug": seed.category},
        "rating": str(seed.rating),
        "review_count": seed.review_count,
        "stock": seed.stock,
        "in_stock": seed.stock > 0,
        "image_url": None,
        "is_premium": seed.is_premium,
        "is_active": seed.is_active,
        "created_at": "2026-01-20T00:00:00Z",
        "updated_at": "2026-01-20T00:00:00Z",
    }


def test_page_envelope_must_be_self_consistent() -> None:
    good = {"items": [_product("product_001")], "page": 1, "page_size": 20, "total": 1, "total_pages": 1}
    assert ProductPage.model_validate(good).total == 1

    with pytest.raises(ValidationError, match="total_pages"):
        ProductPage.model_validate({**good, "total_pages": 2})
    with pytest.raises(ValidationError, match="past the last page"):
        ProductPage.model_validate({**good, "page": 2})


def test_models_reject_unexpected_fields() -> None:
    leaked = {**_product("product_001"), "password_hash": "argon2..."}

    with pytest.raises(ValidationError, match="password_hash"):
        ProductPage.model_validate({"items": [leaked], "page": 1, "page_size": 20, "total": 1, "total_pages": 1})


def test_product_derived_fields_must_agree() -> None:
    wrong = {**_product("product_001"), "effective_price_cents": 8_900}

    with pytest.raises(ValidationError, match="effective_price_cents"):
        ProductPage.model_validate({"items": [wrong], "page": 1, "page_size": 20, "total": 1, "total_pages": 1})


def test_totals_must_add_up() -> None:
    with pytest.raises(ValidationError, match="do not add up"):
        Totals(subtotal_cents=100, discount_cents=10, shipping_cents=0, total_cents=100)


def test_error_codes_must_be_stable_tokens() -> None:
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate({"error": {"code": "Not Found", "message": "m", "details": {}}})
