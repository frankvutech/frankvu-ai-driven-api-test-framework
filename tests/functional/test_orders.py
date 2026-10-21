"""Placing, reading and cancelling orders.

Endpoints: `POST /orders`, `GET /orders`, `GET /orders/{order_id}`, `POST /orders/{order_id}/cancel`.

**Destructive.** Placing an order permanently reserves stock and advances the `SHP-` sequence, so
each test runs between two `POST /test-data/reset` calls: it starts from the documented baseline
(the first order is always `SHP-1001`) and leaves that baseline behind for whatever runs next.
"""

from __future__ import annotations

from collections.abc import Callable

import allure
import pytest

from api_tests.fixtures import Catalog, SignedIn
from api_tests.fixtures.data import (
    PRODUCTS,
    SAMPLE_ADDRESS,
    SAMPLE_CARD,
    UNKNOWN_UUID,
    WELCOME10,
    checkout_body,
    expected_totals,
)
from api_tests.resources import ShopApi
from api_tests.resources.models import Cart, Order, OrderPage, Product
from support.assertions import assert_error, assert_schema, assert_totals

pytestmark = [
    pytest.mark.functional,
    pytest.mark.destructive,
    allure.parent_suite("API"),
    allure.suite("Orders"),
    allure.epic("API"),
    allure.feature("Orders"),
]


def _fill(shopper: SignedIn, catalog: Catalog, *lines: tuple[str, int]) -> None:
    for reference, quantity in lines:
        assert_schema(shopper.api.cart.add_item(catalog.id(reference), quantity), Cart, 201)


def _place(shopper: SignedIn, catalog: Catalog, *lines: tuple[str, int], payment_method: str = "card") -> Order:
    _fill(shopper, catalog, *lines)
    return assert_schema(shopper.api.orders.place(checkout_body(payment_method=payment_method)), Order, 201)


def _stock(api: ShopApi, reference: str) -> int:
    return assert_schema(api.products.get(reference), Product).stock


# --------------------------------------------------------------------------------- placing


@pytest.mark.smoke
@allure.story("Place order")
@allure.severity(allure.severity_level.BLOCKER)
def test_placing_an_order_snapshots_the_cart(shopper: SignedIn, catalog: Catalog) -> None:
    order = _place(shopper, catalog, ("product_001", 2), ("product_012", 1))

    assert order.order_number == "SHP-1001"
    assert order.status == "pending"
    assert (order.payment_method, order.shipping_method, order.coupon_code) == ("card", "standard", None)
    assert order.item_count == 3
    lines = {item.product_name: (item.quantity, item.unit_price_cents, item.subtotal_cents) for item in order.items}
    assert lines == {
        PRODUCTS["product_001"].name: (2, 6_900, 13_800),
        PRODUCTS["product_012"].name: (1, 2_490, 2_490),
    }
    assert_totals(order.totals, expected_totals([("product_001", 2), ("product_012", 1)]))
    assert order.shipping_address.model_dump() == SAMPLE_ADDRESS


@allure.story("Place order")
def test_order_numbers_are_sequential_from_shp_1001(shopper: SignedIn, catalog: Catalog) -> None:
    first = _place(shopper, catalog, ("product_003", 1))
    second = _place(shopper, catalog, ("product_003", 1))

    assert (first.order_number, second.order_number) == ("SHP-1001", "SHP-1002")


@allure.story("Place order")
@allure.severity(allure.severity_level.CRITICAL)
def test_placing_an_order_empties_the_cart_and_its_coupon(shopper: SignedIn, catalog: Catalog) -> None:
    _fill(shopper, catalog, ("product_003", 1))
    assert_schema(shopper.api.cart.apply_coupon(WELCOME10.code), Cart)

    order = assert_schema(shopper.api.orders.place(checkout_body()), Order, 201)

    assert order.coupon_code == "WELCOME10"
    assert_totals(order.totals, expected_totals([("product_003", 1)], discount_percent=10))
    cart = assert_schema(shopper.api.cart.get(), Cart)
    assert (cart.items, cart.coupon) == ([], None)


@allure.story("Place order")
@allure.severity(allure.severity_level.CRITICAL)
def test_placing_an_order_reserves_stock(api: ShopApi, shopper: SignedIn, catalog: Catalog) -> None:
    before = _stock(api, "product_010")

    _place(shopper, catalog, ("product_010", 2))

    assert _stock(api, "product_010") == before - 2


@allure.story("Place order")
@pytest.mark.parametrize("payment_method", ["paypal", "cod"])
def test_non_card_payment_needs_no_card(shopper: SignedIn, catalog: Catalog, payment_method: str) -> None:
    order = _place(shopper, catalog, ("product_003", 1), payment_method=payment_method)

    assert order.payment_method == payment_method


@allure.story("Place order")
def test_an_empty_cart_cannot_be_ordered(shopper: SignedIn) -> None:
    envelope = assert_error(shopper.api.orders.place(checkout_body()), 409, "error_cart_empty")

    assert envelope.error.details["problems"][0]["code"] == "error_cart_empty"


@allure.story("Place order")
@allure.severity(allure.severity_level.CRITICAL)
def test_an_invalid_card_is_422_and_nothing_is_ordered(shopper: SignedIn, catalog: Catalog) -> None:
    _fill(shopper, catalog, ("product_003", 1))

    response = shopper.api.orders.place(checkout_body(card={**SAMPLE_CARD, "number": "4111111111111112"}))

    envelope = assert_error(response, 422, "error_payment_invalid")
    assert envelope.error.details["problems"] == [
        {"code": "card_invalid_number", "message": "The card number is not valid.", "field": "card.number"}
    ]
    assert len(assert_schema(shopper.api.cart.get(), Cart).items) == 1
    assert assert_schema(shopper.api.orders.list(), OrderPage).total == 0


@allure.story("Place order")
def test_an_incomplete_address_is_refused_with_the_problems_listed(shopper: SignedIn, catalog: Catalog) -> None:
    _fill(shopper, catalog, ("product_003", 1))

    response = shopper.api.orders.place(checkout_body(address={**SAMPLE_ADDRESS, "street": ""}))

    # Backend behaviour as implemented: any problem that is not cart/stock/availability is reported
    # under the top-level code `error_payment_invalid`, even when it is the *address* that is wrong.
    # The specific reason is in `details.problems`. Recorded as a finding in docs/api-coverage.md.
    envelope = assert_error(response, 422, "error_payment_invalid")
    assert [p["code"] for p in envelope.error.details["problems"]] == ["error_address_incomplete"]


@allure.story("Place order")
def test_stock_is_rechecked_when_the_order_is_placed(shopper: SignedIn, admin: SignedIn, catalog: Catalog) -> None:
    _fill(shopper, catalog, ("product_010", 2))
    admin.api.admin.update_product(catalog.id("product_010"), {"stock": 1})

    envelope = assert_error(shopper.api.orders.place(checkout_body()), 409, "error_insufficient_stock")

    assert envelope.error.details["problems"][0]["product_id"] == catalog.id("product_010")


@allure.story("Place order")
def test_a_product_withdrawn_after_adding_cannot_be_ordered(
    shopper: SignedIn, admin: SignedIn, catalog: Catalog
) -> None:
    _fill(shopper, catalog, ("product_003", 1))
    admin.api.admin.update_product(catalog.id("product_003"), {"is_active": False})

    assert_error(shopper.api.orders.place(checkout_body()), 409, "error_product_unavailable")


# --------------------------------------------------------------------------------- reading


@allure.story("Order history")
def test_order_history_is_newest_first(shopper: SignedIn, catalog: Catalog) -> None:
    first = _place(shopper, catalog, ("product_003", 1))
    second = _place(shopper, catalog, ("product_007", 1))

    page = assert_schema(shopper.api.orders.list(), OrderPage)

    assert [order.id for order in page.items] == [second.id, first.id]
    assert page.total == 2


@allure.story("Order history")
def test_order_history_pages(shopper: SignedIn, catalog: Catalog) -> None:
    for _ in range(3):
        _place(shopper, catalog, ("product_003", 1))

    page = assert_schema(shopper.api.orders.list(page=2, page_size=2), OrderPage)

    assert (page.total, page.total_pages, len(page.items)) == (3, 2, 1)
    assert page.items[0].order_number == "SHP-1001"


@allure.story("Order history")
def test_order_history_filters_by_status(shopper: SignedIn, catalog: Catalog) -> None:
    kept = _place(shopper, catalog, ("product_003", 1))
    cancelled = _place(shopper, catalog, ("product_007", 1))
    assert_schema(shopper.api.orders.cancel(cancelled.id), Order)

    pending = assert_schema(shopper.api.orders.list(status="pending"), OrderPage)
    gone = assert_schema(shopper.api.orders.list(status="cancelled"), OrderPage)

    assert [o.id for o in pending.items] == [kept.id]
    assert [o.id for o in gone.items] == [cancelled.id]


@allure.story("Order history")
def test_an_unknown_status_filter_is_422(shopper: SignedIn) -> None:
    assert_error(shopper.api.orders.list(status="lost"), 422, "error_validation")


@allure.story("Order history")
def test_get_order_by_id(shopper: SignedIn, catalog: Catalog) -> None:
    placed = _place(shopper, catalog, ("product_003", 1))

    assert assert_schema(shopper.api.orders.get(placed.id), Order) == placed


@allure.story("Order access")
@allure.severity(allure.severity_level.CRITICAL)
def test_another_customers_order_is_404_not_403(new_shopper: Callable[[str], SignedIn], catalog: Catalog) -> None:
    owner, stranger = new_shopper("owner"), new_shopper("stranger")
    order = _place(owner, catalog, ("product_003", 1))

    assert_error(stranger.api.orders.get(order.id), 404, "error_order_not_found")
    assert_error(stranger.api.orders.cancel(order.id), 404, "error_order_not_found")
    assert assert_schema(stranger.api.orders.list(), OrderPage).total == 0


@allure.story("Order access")
@allure.severity(allure.severity_level.CRITICAL)
def test_premium_is_not_a_privilege_level(shopper: SignedIn, premium_customer: SignedIn, catalog: Catalog) -> None:
    order = _place(shopper, catalog, ("product_003", 1))

    assert_error(premium_customer.api.orders.get(order.id), 404, "error_order_not_found")


@allure.story("Order access")
def test_admin_sees_and_reads_every_order(
    new_shopper: Callable[[str], SignedIn], admin: SignedIn, catalog: Catalog
) -> None:
    alice, bob = new_shopper("alice"), new_shopper("bob")
    a = _place(alice, catalog, ("product_003", 1))
    b = _place(bob, catalog, ("product_007", 1))

    page = assert_schema(admin.api.orders.list(), OrderPage)

    assert {o.id for o in page.items} == {a.id, b.id}
    assert assert_schema(admin.api.orders.get(a.id), Order) == a


@allure.story("Order access")
def test_unknown_order_is_404(shopper: SignedIn) -> None:
    assert_error(shopper.api.orders.get(UNKNOWN_UUID), 404, "error_order_not_found")


@allure.story("Order access")
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/orders"),
        ("POST", "/orders"),
        ("GET", f"/orders/{UNKNOWN_UUID}"),
        ("POST", f"/orders/{UNKNOWN_UUID}/cancel"),
    ],
)
def test_every_order_route_requires_a_token(api: ShopApi, method: str, path: str) -> None:
    body = checkout_body() if (method, path) == ("POST", "/orders") else None
    assert_error(api.client.request(method, path, json=body), 401, "error_invalid_token")


# --------------------------------------------------------------------------------- cancelling


@allure.story("Cancel order")
@allure.severity(allure.severity_level.CRITICAL)
def test_cancelling_a_pending_order_returns_the_stock(api: ShopApi, shopper: SignedIn, catalog: Catalog) -> None:
    before = _stock(api, "product_010")
    order = _place(shopper, catalog, ("product_010", 3))

    cancelled = assert_schema(shopper.api.orders.cancel(order.id), Order)

    assert cancelled.status == "cancelled"
    assert _stock(api, "product_010") == before


@allure.story("Cancel order")
def test_a_cancelled_order_cannot_be_cancelled_again(shopper: SignedIn, catalog: Catalog) -> None:
    order = _place(shopper, catalog, ("product_003", 1))
    assert_schema(shopper.api.orders.cancel(order.id), Order)

    envelope = assert_error(shopper.api.orders.cancel(order.id), 409, "error_invalid_order_status")

    assert envelope.error.details == {"current_status": "cancelled", "target_status": "cancelled"}


@allure.story("Cancel order")
def test_admin_may_cancel_any_order(shopper: SignedIn, admin: SignedIn, catalog: Catalog) -> None:
    order = _place(shopper, catalog, ("product_003", 1))

    assert assert_schema(admin.api.orders.cancel(order.id), Order).status == "cancelled"
