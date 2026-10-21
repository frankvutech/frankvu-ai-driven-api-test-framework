"""Response models: the framework's statement of the backend contract.

Written independently of the backend's own Pydantic schemas. Importing those would prove only that
the server agrees with itself; a rename on the server side must fail these models instead.

`extra="forbid"` throughout, so a field the API starts returning that nobody asked for — an
accidentally exposed `password_hash`, say — fails the schema check rather than going unnoticed.

Field sources: `backend/app/schemas/*.py` and `GET /openapi.json` of the ShopTest backend 0.1.0.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

UserRole = Literal["customer", "premium_customer", "admin"]
UserStatus = Literal["active", "locked", "disabled"]
OrderStatus = Literal["pending", "confirmed", "processing", "shipped", "delivered", "cancelled"]
ShippingMethod = Literal["standard", "express", "pickup"]
PaymentMethod = Literal["card", "paypal", "cod"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------- errors


class ErrorBody(Strict):
    code: str = Field(pattern=r"^error_[a-z0-9_]+$")
    message: str
    details: dict[str, Any]


class ErrorEnvelope(Strict):
    error: ErrorBody


# --------------------------------------------------------------------------------- health


class Health(Strict):
    status: Literal["ok"]


class DatabaseHealth(Strict):
    status: Literal["ok"]
    component: str
    latency_ms: float | None


# --------------------------------------------------------------------------------- auth


class User(Strict):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    full_name: str
    role: UserRole
    status: UserStatus
    created_at: datetime


class Tokens(Strict):
    access_token: str = Field(min_length=20)
    refresh_token: str = Field(min_length=20)
    token_type: Literal["Bearer"]
    expires_in: int = Field(gt=0)


class Authenticated(Strict):
    user: User
    tokens: Tokens


# --------------------------------------------------------------------------------- catalogue


class Category(Strict):
    id: uuid.UUID
    name: str
    slug: str
    product_count: int = Field(ge=0)
    created_at: datetime


class CategorySummary(Strict):
    id: uuid.UUID
    name: str
    slug: str


class Product(Strict):
    id: uuid.UUID
    reference: str
    name: str
    description: str
    price_cents: int = Field(ge=0)
    discount_price_cents: int | None
    effective_price_cents: int = Field(ge=0)
    category: CategorySummary
    rating: Decimal
    review_count: int = Field(ge=0)
    stock: int = Field(ge=0)
    in_stock: bool
    image_url: str | None
    is_premium: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _derived_fields_agree(self) -> Product:
        # The two derived fields are a promise the client relies on instead of recomputing them.
        expected = self.discount_price_cents if self.discount_price_cents is not None else self.price_cents
        if self.effective_price_cents != expected:
            raise ValueError(f"effective_price_cents {self.effective_price_cents} != {expected}")
        if self.in_stock != (self.stock > 0):
            raise ValueError(f"in_stock {self.in_stock} disagrees with stock {self.stock}")
        return self


class ProductPage(Strict):
    items: list[Product]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)

    @model_validator(mode="after")
    def _envelope_is_consistent(self) -> ProductPage:
        _check_page(self.items, self.page, self.page_size, self.total, self.total_pages)
        return self


# --------------------------------------------------------------------------------- cart


class Totals(Strict):
    subtotal_cents: int = Field(ge=0)
    discount_cents: int = Field(ge=0)
    shipping_cents: int = Field(ge=0)
    total_cents: int = Field(ge=0)

    @model_validator(mode="after")
    def _adds_up(self) -> Totals:
        if self.total_cents != self.subtotal_cents - self.discount_cents + self.shipping_cents:
            raise ValueError(f"totals do not add up: {self.model_dump()}")
        return self


class CartItem(Strict):
    product: Product
    quantity: int = Field(ge=1, le=99)
    unit_price_cents: int
    line_total_cents: int


class AppliedCoupon(Strict):
    code: str
    discount_percent: int


class Cart(Strict):
    items: list[CartItem]
    item_count: int = Field(ge=0)
    coupon: AppliedCoupon | None
    shipping_method: ShippingMethod
    totals: Totals

    def line(self, reference: str) -> CartItem:
        for item in self.items:
            if item.product.reference == reference:
                return item
        raise LookupError(f"{reference} is not in the cart: {[i.product.reference for i in self.items]}")


# --------------------------------------------------------------------------------- wishlist


class WishlistItem(Strict):
    product: Product
    added_at: datetime


class Wishlist(Strict):
    items: list[WishlistItem]
    count: int = Field(ge=0)


# --------------------------------------------------------------------------------- checkout


class CheckoutProblem(Strict):
    code: str
    message: str
    field: str | None = None
    product_id: str | None = None


class CheckoutValidation(Strict):
    valid: bool
    totals: Totals
    problems: list[CheckoutProblem]

    @property
    def problem_codes(self) -> dict[str | None, str]:
        """`{field: code}` — the field is `None` for cart-level problems."""
        return {problem.field: problem.code for problem in self.problems}


# --------------------------------------------------------------------------------- orders


class OrderItem(Strict):
    product_id: uuid.UUID
    product_name: str
    quantity: int
    unit_price_cents: int
    subtotal_cents: int


class OrderAddress(Strict):
    full_name: str
    street: str
    city: str
    postal_code: str
    country: str
    phone: str | None


class Order(Strict):
    id: uuid.UUID
    order_number: str = Field(pattern=r"^SHP-\d{4,}$")
    status: OrderStatus
    items: list[OrderItem]
    item_count: int
    totals: Totals
    shipping_method: ShippingMethod
    payment_method: PaymentMethod
    coupon_code: str | None
    shipping_address: OrderAddress
    created_at: datetime
    updated_at: datetime


class OrderPage(Strict):
    items: list[Order]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)

    @model_validator(mode="after")
    def _envelope_is_consistent(self) -> OrderPage:
        _check_page(self.items, self.page, self.page_size, self.total, self.total_pages)
        return self


# --------------------------------------------------------------------------------- test data


class CleanupSummary(Strict):
    orders: int
    order_items: int
    cart_items: int
    carts: int
    wishlist_items: int
    refresh_tokens: int
    users: int
    total_removed: int


class SeededCounts(Strict):
    roles: int
    categories: int
    products: int
    users: int
    coupons: int


class ResetSummary(Strict):
    status: Literal["ok"]
    seeded: SeededCounts
    removed: CleanupSummary


class Delay(Strict):
    delayed_seconds: int
    status: Literal["ok"]


def _check_page(items: list[Any], page: int, page_size: int, total: int, total_pages: int) -> None:
    expected_pages = -(-total // page_size)  # ceiling division; 0 when empty
    if total_pages != expected_pages:
        raise ValueError(f"total_pages {total_pages} != ceil({total}/{page_size}) = {expected_pages}")
    if len(items) > page_size:
        raise ValueError(f"{len(items)} items on a page of {page_size}")
    if page <= total_pages:
        expected_items = min(page_size, total - (page - 1) * page_size)
        if len(items) != expected_items:
            raise ValueError(f"page {page} has {len(items)} items, expected {expected_items}")
    elif items:
        raise ValueError(f"page {page} is past the last page ({total_pages}) but has items")
