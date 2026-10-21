"""Cart lines, server-side pricing and coupons.

Endpoints: `GET/DELETE /cart`, `POST /cart/items`, `PATCH/DELETE /cart/items/{product_id}`,
`POST/DELETE /cart/coupon`. Every test acts as a freshly registered `shopper`, so no two tests share
a cart. The cart is keyed by the token — there is no cart id to address someone else's.
"""

from __future__ import annotations

from collections.abc import Callable

import allure
import pytest

from api_tests.fixtures import Catalog, SignedIn
from api_tests.fixtures.data import (
    BIGSPEND25,
    EXPIRED10,
    PREMIUM20,
    UNKNOWN_COUPON,
    UNKNOWN_UUID,
    WELCOME10,
    WITHDRAWN15,
    expected_totals,
)
from api_tests.resources import ShopApi
from api_tests.resources.models import Cart
from support.assertions import assert_error, assert_schema, assert_totals

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Cart"),
    allure.epic("API"),
    allure.feature("Cart"),
]


def _add(shopper: SignedIn, catalog: Catalog, reference: str, quantity: int = 1) -> Cart:
    return assert_schema(shopper.api.cart.add_item(catalog.id(reference), quantity), Cart, 201)


# --------------------------------------------------------------------------------- lines


@pytest.mark.smoke
@allure.story("Cart lines")
def test_a_new_customer_has_an_empty_cart(shopper: SignedIn) -> None:
    cart = assert_schema(shopper.api.cart.get(), Cart)

    assert (cart.items, cart.item_count, cart.coupon, cart.shipping_method) == ([], 0, None, "standard")
    assert_totals(cart.totals, expected_totals([]))


@pytest.mark.smoke
@allure.story("Cart lines")
@allure.severity(allure.severity_level.CRITICAL)
def test_add_item_prices_the_line_at_the_effective_price(shopper: SignedIn, catalog: Catalog) -> None:
    cart = _add(shopper, catalog, "product_001", 2)

    line = cart.line("product_001")
    assert (line.quantity, line.unit_price_cents, line.line_total_cents) == (2, 6_900, 13_800)
    assert cart.item_count == 2
    assert_totals(cart.totals, expected_totals([("product_001", 2)]))


@allure.story("Cart lines")
def test_adding_the_same_product_again_raises_the_quantity(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_003", 1)

    cart = _add(shopper, catalog, "product_003", 2)

    assert len(cart.items) == 1
    assert cart.line("product_003").quantity == 3


@allure.story("Cart lines")
def test_quantity_defaults_to_one(shopper: SignedIn, catalog: Catalog) -> None:
    cart = assert_schema(shopper.api.cart.add_item(catalog.id("product_007"), None), Cart, 201)

    assert cart.line("product_007").quantity == 1


@allure.story("Cart lines")
def test_item_count_is_units_not_lines(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_003", 2)

    cart = _add(shopper, catalog, "product_007", 3)

    assert (len(cart.items), cart.item_count) == (2, 5)


@allure.story("Cart lines")
def test_update_sets_the_quantity(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_006", 1)

    cart = assert_schema(shopper.api.cart.update_item(catalog.id("product_006"), 4), Cart)

    assert cart.line("product_006").quantity == 4
    assert_totals(cart.totals, expected_totals([("product_006", 4)]))


@allure.story("Cart lines")
def test_remove_deletes_the_line(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_006", 1)
    _add(shopper, catalog, "product_007", 1)

    cart = assert_schema(shopper.api.cart.remove_item(catalog.id("product_006")), Cart)

    assert [item.product.reference for item in cart.items] == ["product_007"]


@allure.story("Cart lines")
def test_clear_empties_lines_and_coupon(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_002", 1)
    assert_schema(shopper.api.cart.apply_coupon(WELCOME10.code), Cart)

    cart = assert_schema(shopper.api.cart.clear(), Cart)

    assert (cart.items, cart.coupon) == ([], None)


@allure.story("Cart lines")
@pytest.mark.parametrize("operation", ["update", "remove"])
def test_changing_a_line_that_is_not_in_the_cart_is_404(shopper: SignedIn, catalog: Catalog, operation: str) -> None:
    product_id = catalog.id("product_007")
    response = (
        shopper.api.cart.update_item(product_id, 1)
        if operation == "update"
        else shopper.api.cart.remove_item(product_id)
    )

    assert_error(response, 404, "error_not_in_cart")


@allure.story("Cart lines")
@allure.severity(allure.severity_level.CRITICAL)
def test_adding_beyond_stock_is_409_with_details(shopper: SignedIn, catalog: Catalog) -> None:
    # product_010 (Espresso Machine) has 9 in stock.
    envelope = assert_error(shopper.api.cart.add_item(catalog.id("product_010"), 10), 409, "error_insufficient_stock")

    assert envelope.error.details == {"available": 9, "requested": 10, "product_id": catalog.id("product_010")}


@allure.story("Cart lines")
def test_stock_is_checked_against_the_resulting_quantity(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_010", 9)

    assert_error(shopper.api.cart.add_item(catalog.id("product_010"), 1), 409, "error_insufficient_stock")


@allure.story("Cart lines")
def test_updating_beyond_stock_is_409(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_010", 1)

    assert_error(shopper.api.cart.update_item(catalog.id("product_010"), 10), 409, "error_insufficient_stock")


@allure.story("Cart lines")
def test_an_out_of_stock_product_cannot_be_added(shopper: SignedIn, catalog: Catalog) -> None:
    assert_error(shopper.api.cart.add_item(catalog.id("product_005"), 1), 409, "error_insufficient_stock")


@allure.story("Cart lines")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("reference", ["product_004", "product_030"], ids=["premium", "inactive"])
def test_a_hidden_product_cannot_be_added_by_guessing_its_id(
    shopper: SignedIn, catalog: Catalog, reference: str
) -> None:
    assert_error(shopper.api.cart.add_item(catalog.id(reference), 1), 404, "error_product_not_found")


@allure.story("Cart lines")
def test_an_unknown_product_is_404(shopper: SignedIn) -> None:
    assert_error(shopper.api.cart.add_item(UNKNOWN_UUID, 1), 404, "error_product_not_found")


@allure.story("Cart lines")
@pytest.mark.parametrize("quantity", [0, -1, 100, "two"], ids=["zero", "negative", "above-99", "not-a-number"])
def test_invalid_quantity_on_add_is_422(shopper: SignedIn, catalog: Catalog, quantity: object) -> None:
    response = shopper.api.client.post(
        "/cart/items", json={"product_id": catalog.id("product_001"), "quantity": quantity}
    )

    assert_error(response, 422, "error_validation")


@allure.story("Cart lines")
@pytest.mark.parametrize("quantity", [0, 100], ids=["zero-is-not-remove", "above-99"])
def test_invalid_quantity_on_update_is_422(shopper: SignedIn, catalog: Catalog, quantity: int) -> None:
    _add(shopper, catalog, "product_001", 1)

    assert_error(shopper.api.cart.update_item(catalog.id("product_001"), quantity), 422, "error_validation")


@allure.story("Cart lines")
@pytest.mark.parametrize("product_id", ["product_001", "not-a-uuid"], ids=["reference", "garbage"])
def test_cart_lines_are_addressed_by_uuid_only(shopper: SignedIn, product_id: str) -> None:
    assert_error(shopper.api.cart.add_item(product_id, 1), 422, "error_validation")


# --------------------------------------------------------------------------------- pricing


@allure.story("Pricing")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(
    ("lines", "shipping_method"),
    [
        ([("product_003", 1)], "standard"),
        ([("product_003", 1)], "express"),
        ([("product_003", 1)], "pickup"),
        ([("product_006", 3)], "standard"),
        ([("product_002", 1)], "express"),
    ],
    ids=["standard-under-threshold", "express-under-threshold", "pickup-free", "just-under-75", "free-over-75"],
)
def test_shipping_quote(
    shopper: SignedIn, catalog: Catalog, lines: list[tuple[str, int]], shipping_method: str
) -> None:
    for reference, quantity in lines:
        _add(shopper, catalog, reference, quantity)

    cart = assert_schema(shopper.api.cart.get(shipping_method=shipping_method), Cart)

    assert cart.shipping_method == shipping_method
    assert_totals(cart.totals, expected_totals(lines, shipping_method=shipping_method))


@allure.story("Pricing")
def test_an_invalid_shipping_method_is_422(shopper: SignedIn) -> None:
    assert_error(shopper.api.cart.get(shipping_method="drone"), 422, "error_validation")


@allure.story("Pricing")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_client_supplied_discount_is_ignored(shopper: SignedIn, catalog: Catalog) -> None:
    # The server owns the money: unknown fields are dropped, never believed.
    response = shopper.api.cart.add_item(catalog.id("product_001"), 1, discount_cents=6_900, total_cents=0)

    cart = assert_schema(response, Cart, 201)
    assert_totals(cart.totals, expected_totals([("product_001", 1)]))


# --------------------------------------------------------------------------------- coupons


@pytest.mark.smoke
@allure.story("Coupons")
@allure.severity(allure.severity_level.CRITICAL)
def test_welcome10_takes_ten_percent_floored(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_012", 1)  # 2 490 -> 10% is 249

    cart = assert_schema(shopper.api.cart.apply_coupon(WELCOME10.code), Cart)

    assert cart.coupon is not None
    assert (cart.coupon.code, cart.coupon.discount_percent) == ("WELCOME10", 10)
    assert_totals(cart.totals, expected_totals([("product_012", 1)], discount_percent=10))


@allure.story("Coupons")
def test_coupon_codes_are_canonicalised(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_012", 1)

    cart = assert_schema(shopper.api.cart.apply_coupon("  welcome10 "), Cart)

    assert cart.coupon is not None
    assert cart.coupon.code == "WELCOME10"


@allure.story("Coupons")
def test_applying_the_same_coupon_twice_is_409(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_012", 1)
    assert_schema(shopper.api.cart.apply_coupon(WELCOME10.code), Cart)

    assert_error(shopper.api.cart.apply_coupon(WELCOME10.code), 409, "error_coupon_already_applied")


@allure.story("Coupons")
@pytest.mark.parametrize(
    ("code", "error_code"),
    [
        (EXPIRED10, "error_coupon_expired"),
        (PREMIUM20.code, "error_coupon_premium_only"),
        (BIGSPEND25.code, "error_coupon_minimum_not_met"),
        (WITHDRAWN15, "error_coupon_unknown"),
        (UNKNOWN_COUPON, "error_coupon_unknown"),
    ],
    ids=["expired", "premium-only", "minimum-not-met", "withdrawn-reads-as-unknown", "unknown"],
)
def test_coupon_rejections(shopper: SignedIn, catalog: Catalog, code: str, error_code: str) -> None:
    _add(shopper, catalog, "product_012", 1)

    assert_error(shopper.api.cart.apply_coupon(code), 409, error_code)


@allure.story("Coupons")
def test_premium20_is_accepted_for_a_premium_customer(premium_customer: SignedIn, catalog: Catalog) -> None:
    # The seeded premium account's cart is shared state, so this test empties it before and after.
    cart_api = premium_customer.api.cart
    cart_api.clear()
    try:
        assert_schema(cart_api.add_item(catalog.id("product_012"), 1), Cart, 201)
        cart = assert_schema(cart_api.apply_coupon(PREMIUM20.code), Cart)
        assert_totals(cart.totals, expected_totals([("product_012", 1)], discount_percent=20))
    finally:
        cart_api.clear()


@allure.story("Coupons")
def test_bigspend25_applies_at_the_minimum(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_002", 1)  # 12 900 >= 10 000

    cart = assert_schema(shopper.api.cart.apply_coupon(BIGSPEND25.code), Cart)

    assert_totals(cart.totals, expected_totals([("product_002", 1)], discount_percent=25))


@allure.story("Coupons")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_coupon_drops_off_when_the_basket_falls_below_its_minimum(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_002", 1)
    _add(shopper, catalog, "product_003", 1)
    assert_schema(shopper.api.cart.apply_coupon(BIGSPEND25.code), Cart)

    cart = assert_schema(shopper.api.cart.remove_item(catalog.id("product_002")), Cart)

    assert cart.coupon is None
    assert_totals(cart.totals, expected_totals([("product_003", 1)]))


@allure.story("Coupons")
def test_every_documented_rejection_is_reachable_on_an_empty_basket(shopper: SignedIn) -> None:
    # True precedence (e.g. expired beating premium-only) needs a coupon that breaks two rules at
    # once; the seed has none, so this pins each rule on its own. Recorded in docs/api-coverage.md.
    assert_error(shopper.api.cart.apply_coupon(EXPIRED10), 409, "error_coupon_expired")
    assert_error(shopper.api.cart.apply_coupon(PREMIUM20.code), 409, "error_coupon_premium_only")
    assert_error(shopper.api.cart.apply_coupon(BIGSPEND25.code), 409, "error_coupon_minimum_not_met")


@allure.story("Coupons")
def test_remove_coupon(shopper: SignedIn, catalog: Catalog) -> None:
    _add(shopper, catalog, "product_012", 1)
    assert_schema(shopper.api.cart.apply_coupon(WELCOME10.code), Cart)

    cart = assert_schema(shopper.api.cart.remove_coupon(), Cart)

    assert cart.coupon is None
    assert_totals(cart.totals, expected_totals([("product_012", 1)]))


@allure.story("Coupons")
def test_removing_a_coupon_that_is_not_applied_is_404(shopper: SignedIn) -> None:
    assert_error(shopper.api.cart.remove_coupon(), 404, "error_no_coupon_applied")


@allure.story("Coupons")
@pytest.mark.parametrize("code", ["", "X" * 65], ids=["empty", "too-long"])
def test_invalid_coupon_code_is_422(shopper: SignedIn, code: str) -> None:
    assert_error(shopper.api.cart.apply_coupon(code), 422, "error_validation")


# --------------------------------------------------------------------------------- isolation and auth


@allure.story("Isolation")
@allure.severity(allure.severity_level.CRITICAL)
def test_carts_are_isolated_per_user(new_shopper: Callable[[str], SignedIn], catalog: Catalog) -> None:
    alice, bob = new_shopper("alice"), new_shopper("bob")
    assert_schema(alice.api.cart.add_item(catalog.id("product_001"), 1), Cart, 201)

    bobs_cart = assert_schema(bob.api.cart.get(), Cart)

    assert bobs_cart.items == []


CART_ROUTES: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/cart", None),
    ("DELETE", "/cart", None),
    ("POST", "/cart/items", {"product_id": UNKNOWN_UUID, "quantity": 1}),
    ("PATCH", f"/cart/items/{UNKNOWN_UUID}", {"quantity": 1}),
    ("DELETE", f"/cart/items/{UNKNOWN_UUID}", None),
    ("POST", "/cart/coupon", {"code": "WELCOME10"}),
    ("DELETE", "/cart/coupon", None),
]


@allure.story("Authorization")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(("method", "path", "body"), CART_ROUTES, ids=[f"{m} {p}" for m, p, _ in CART_ROUTES])
def test_every_cart_route_requires_a_token(
    api: ShopApi, method: str, path: str, body: dict[str, object] | None
) -> None:
    assert_error(api.client.request(method, path, json=body), 401, "error_invalid_token")
