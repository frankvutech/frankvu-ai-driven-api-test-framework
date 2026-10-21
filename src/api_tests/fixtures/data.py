"""The backend's documented, deterministic baseline — as the tests expect it.

Mirrors `backend/scripts/seed.py` and `docs/test-credentials.md` of the ShopTest repository. Tests
compute their expectations *from this table* (which products a filter returns, in what order, at
what price) rather than hard-coding result lists, so one source of truth drives every assertion and
a change to the seed fails loudly in exactly one place.

`POST /test-data/reset` restores precisely this state; nothing here depends on the clock.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

Viewer = Literal["anonymous", "customer", "premium_customer", "admin"]


@dataclass(frozen=True, slots=True)
class SeedProduct:
    reference: str
    name: str
    price_cents: int
    discount_price_cents: int | None
    category: str
    rating: Decimal
    review_count: int
    stock: int
    is_premium: bool = False
    is_active: bool = True

    @property
    def effective_price_cents(self) -> int:
        return self.discount_price_cents if self.discount_price_cents is not None else self.price_cents

    def visible_to(self, viewer: Viewer) -> bool:
        """`CatalogueViewer`: premium needs premium/admin, inactive needs admin."""
        if not self.is_active and viewer != "admin":
            return False
        return not (self.is_premium and viewer not in ("premium_customer", "admin"))


def _p(  # noqa: PLR0917 - one positional argument per column of the seed table
    index: int,
    name: str,
    price: int,
    discount: int | None,
    category: str,
    rating: str,
    reviews: int,
    stock: int,
    *,
    premium: bool = False,
    active: bool = True,
) -> SeedProduct:
    return SeedProduct(
        f"product_{index:03d}", name, price, discount, category, Decimal(rating), reviews, stock, premium, active
    )


# fmt: off
SEED_PRODUCTS: tuple[SeedProduct, ...] = (
    _p(1, "Wireless Headphones", 8_900, 6_900, "electronics", "4.6", 128, 24),
    _p(2, "Mechanical Keyboard", 12_900, None, "electronics", "4.4", 87, 15),
    _p(3, "Cotton T-Shirt", 1_990, None, "clothing", "3.8", 240, 60),
    _p(4, "Studio Laptop 16", 189_900, 179_900, "electronics", "4.9", 45, 6, premium=True),
    _p(5, "Trail Running Shoes", 13_900, None, "shoes", "4.3", 96, 0),
    _p(6, "Desk Lamp", 3_500, 2_450, "home", "4.4", 62, 40),
    _p(7, "Yoga Mat", 2_900, None, "sports", "4.0", 154, 35),
    _p(8, "Clean Architecture", 4_200, 3_600, "books", "4.7", 310, 50),
    _p(9, "Leather Wallet", 5_900, None, "accessories", "4.1", 73, 28),
    _p(10, "Espresso Machine", 34_900, 29_900, "home", "4.5", 58, 9),
    _p(11, "Merino Base Layer", 7_900, None, "clothing", "4.2", 41, 22),
    _p(12, "Smart Water Bottle", 2_490, None, "sports", "4.3", 96, 44),
    _p(13, "Canvas Sneakers", 4_900, 3_900, "shoes", "3.9", 187, 31),
    _p(14, "Noise-Cancelling Earbuds Pro", 24_900, None, "electronics", "4.8", 64, 12, premium=True),
    _p(15, "Cast Iron Skillet", 4_500, None, "home", "4.6", 205, 26),
    _p(16, "Testing Mobile Apps", 3_900, None, "books", "4.5", 128, 55),
    _p(17, "Down Jacket", 19_900, 15_900, "clothing", "4.4", 52, 0),
    _p(18, "Foam Roller", 1_990, None, "sports", "3.7", 118, 48),
    _p(19, "Sunglasses", 8_500, None, "accessories", "4.0", 66, 19),
    _p(20, "Bluetooth Speaker", 6_900, 5_500, "electronics", "4.2", 143, 33),
    _p(21, "Chef's Knife", 9_900, None, "home", "4.7", 89, 17),
    _p(22, "Wool Socks", 1_490, None, "clothing", "4.1", 276, 90),
    _p(23, "Hiking Boots", 16_900, None, "shoes", "4.5", 71, 14),
    _p(24, "Kotlin in Practice", 4_500, None, "books", "4.6", 198, 42),
    _p(25, "Resistance Band Set", 2_290, 1_790, "sports", "3.9", 132, 61),
    _p(26, "Automatic Watch", 74_900, None, "accessories", "4.9", 38, 5, premium=True),
    _p(27, "Laptop Backpack", 7_500, None, "accessories", "4.3", 154, 37),
    _p(28, "Standing Desk Mat", 5_500, None, "home", "3.6", 47, 0),
    _p(29, "Running Cap", 1_890, None, "sports", "2.9", 58, 72),
    _p(30, "Retro Film Camera", 29_900, None, "electronics", "4.2", 24, 0, active=False),
)
# fmt: on

PRODUCTS: dict[str, SeedProduct] = {product.reference: product for product in SEED_PRODUCTS}

# (slug, name), in the order `GET /categories` returns them: by name.
SEED_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("accessories", "Accessories"),
    ("books", "Books"),
    ("clothing", "Clothing"),
    ("electronics", "Electronics"),
    ("home", "Home"),
    ("shoes", "Shoes"),
    ("sports", "Sports"),
)

# Reference ids the backend reserves or that never exist.
UNKNOWN_PRODUCT_REFERENCE = "product_999"
UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"

PREMIUM_ONLY = ("product_004", "product_014", "product_026")
OUT_OF_STOCK = ("product_005", "product_017", "product_028")
INACTIVE = ("product_030",)


def visible_products(viewer: Viewer) -> list[SeedProduct]:
    return [product for product in SEED_PRODUCTS if product.visible_to(viewer)]


# --------------------------------------------------------------------------------- money

SHIPPING_CENTS = {"standard": 499, "express": 1_499, "pickup": 0}
FREE_SHIPPING_THRESHOLD_CENTS = 7_500


def expected_totals(
    lines: list[tuple[str, int]],
    *,
    discount_percent: int = 0,
    shipping_method: str = "standard",
) -> dict[str, int]:
    """The backend's pricing rules (`app/domain/shopping.py`), restated independently.

    Subtotal at effective prices; discount floored; shipping free for pickup or at/above €75 of
    *discounted* subtotal; total never negative. An empty cart costs nothing, shipping included.
    """
    if not lines:
        return {"subtotal_cents": 0, "discount_cents": 0, "shipping_cents": 0, "total_cents": 0}
    subtotal = sum(PRODUCTS[reference].effective_price_cents * quantity for reference, quantity in lines)
    discount = subtotal * discount_percent // 100
    discounted = max(subtotal - discount, 0)
    if shipping_method == "pickup" or discounted >= FREE_SHIPPING_THRESHOLD_CENTS:
        shipping = 0
    else:
        shipping = SHIPPING_CENTS[shipping_method]
    return {
        "subtotal_cents": subtotal,
        "discount_cents": discount,
        "shipping_cents": shipping,
        "total_cents": discounted + shipping,
    }


# --------------------------------------------------------------------------------- coupons


@dataclass(frozen=True, slots=True)
class SeedCoupon:
    code: str
    discount_percent: int


WELCOME10 = SeedCoupon("WELCOME10", 10)
PREMIUM20 = SeedCoupon("PREMIUM20", 20)
EXPIRED10 = "EXPIRED10"
BIGSPEND25 = SeedCoupon("BIGSPEND25", 25)
BIGSPEND25_MINIMUM_CENTS = 10_000
WITHDRAWN15 = "WITHDRAWN15"
UNKNOWN_COUPON = "NOPE"

# --------------------------------------------------------------------------------- checkout

# `checkout_address_fill_sample_button` in the app; documented in docs/test-credentials.md.
SAMPLE_ADDRESS: dict[str, Any] = {
    "full_name": "Alex Customer",
    "street": "12 Test Street",
    "city": "Berlin",
    "postal_code": "10115",
    "country": "Germany",
    "phone": "+4930123456",
}

# The documented test card: a well-known Visa *test* number that passes Luhn. No payment is ever
# processed by the backend. Redacted from every report all the same.
SAMPLE_CARD: dict[str, str] = {
    "holder": "ALEX CUSTOMER",
    "number": "4111111111111111",
    "expiry": "12/30",
    "cvv": "123",
}


def checkout_body(
    *,
    shipping_method: str = "standard",
    payment_method: str = "card",
    card: dict[str, str] | None = None,
    address: dict[str, Any] | None = None,
    include_card: bool = True,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "address": dict(address if address is not None else SAMPLE_ADDRESS),
        "shipping_method": shipping_method,
        "payment_method": payment_method,
    }
    if payment_method == "card" and include_card:
        body["card"] = dict(card if card is not None else SAMPLE_CARD)
    return body


# --------------------------------------------------------------------------------- registration


def registration_identity(run_id: str, label: str, domain: str) -> tuple[str, str]:
    """A deterministic, run-unique email and password for an account registered by a test.

    Derived, not random: re-running one test inside one run produces the same identity, and two runs
    never collide because the run id differs. `cleanup`/`reset` removes these accounts.
    """
    digest = hashlib.sha256(f"{run_id}:{label}".encode()).hexdigest()
    slug = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-")[:24]
    email = f"{slug}-{digest[:12]}@{domain}"
    password = f"Mtp-{digest[12:28]}!"
    return email, password
