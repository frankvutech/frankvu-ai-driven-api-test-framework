"""Response shapes the mobile clients depend on, as Pact matchers.

Deliberately *partial*: a contract constrains only what the consumer reads. The product card shows a
name, a price, a rating and a stock badge; it does not read `created_at`, so the contract does not
mention it and the provider remains free to change it.
"""

from __future__ import annotations

from typing import Any

from pact import match

from api_tests.pact.matchers import UUID_REGEX

EXAMPLE_PRODUCT_ID = "6f1c2a9e-1b2c-4d3e-8f40-0000000000a1"
EXAMPLE_USER_ID = "6f1c2a9e-1b2c-4d3e-8f40-0000000000b1"
EXAMPLE_ORDER_ID = "6f1c2a9e-1b2c-4d3e-8f40-0000000000c1"

JSON = "application/json"
ISO_DATETIME = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"


def uuid(example: str) -> Any:
    return match.regex(example, regex=f"^{UUID_REGEX}$")


def error(code: str) -> dict[str, Any]:
    # The consumer branches on the stable code; the message is shown to people and may change.
    return {"error": {"code": code, "message": match.str("A human-readable explanation.")}}


def product(reference: str = "product_001", *, name: str = "Wireless Headphones") -> dict[str, Any]:
    return {
        "id": uuid(EXAMPLE_PRODUCT_ID),
        "reference": match.regex(reference, regex=r"^product_\d{3}$"),
        "name": match.str(name),
        "price_cents": match.int(8_900),
        "effective_price_cents": match.int(6_900),
        "category": {"slug": match.str("electronics"), "name": match.str("Electronics")},
        # A decimal string ("4.6"), never a float: the backend serialises NUMERIC(2,1) exactly.
        "rating": match.regex("4.6", regex=r"^\d\.\d$"),
        "review_count": match.int(128),
        "in_stock": match.bool(True),
        "is_premium": match.bool(False),
    }


def totals() -> dict[str, Any]:
    return {
        "subtotal_cents": match.int(6_900),
        "discount_cents": match.int(0),
        "shipping_cents": match.int(0),
        "total_cents": match.int(6_900),
    }


def page(item: dict[str, Any], *, page_number: int = 1, page_size: int = 20) -> dict[str, Any]:
    return {
        "items": match.each_like(item, min=1),
        "page": page_number,
        "page_size": page_size,
        "total": match.int(26),
        "total_pages": match.int(2),
    }


def cart_line(reference: str = "product_001", quantity: int = 1) -> dict[str, Any]:
    return {
        "product": {"id": uuid(EXAMPLE_PRODUCT_ID), "reference": reference, "name": match.str("Wireless Headphones")},
        "quantity": quantity,
        "unit_price_cents": match.int(6_900),
        "line_total_cents": match.int(6_900 * quantity),
    }


def user() -> dict[str, Any]:
    return {
        "id": uuid(EXAMPLE_USER_ID),
        "email": match.str("customer@shoptest.local"),
        "full_name": match.str("Alex Customer"),
        "role": match.regex("customer", regex=r"^(customer|premium_customer|admin)$"),
        "status": "active",
    }


def order(status: str = "pending") -> dict[str, Any]:
    return {
        "id": uuid(EXAMPLE_ORDER_ID),
        "order_number": match.regex("SHP-1001", regex=r"^SHP-\d{4,}$"),
        "status": status,
        "items": match.each_like(
            {
                "product_name": match.str("Cotton T-Shirt"),
                "quantity": match.int(1),
                "unit_price_cents": match.int(1_990),
                "subtotal_cents": match.int(1_990),
            },
            min=1,
        ),
        "item_count": match.int(1),
        "totals": totals(),
        "payment_method": match.regex("cod", regex=r"^(card|paypal|cod)$"),
        "created_at": match.regex("2026-01-20T10:00:00.000000Z", regex=ISO_DATETIME),
    }
