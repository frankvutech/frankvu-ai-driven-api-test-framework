"""Wishlist: `GET /wishlist`, `POST/DELETE /wishlist/items/{product_id}`. Keyed by the token."""

from __future__ import annotations

from collections.abc import Callable

import allure
import pytest

from api_tests.fixtures import Catalog, SignedIn
from api_tests.fixtures.data import UNKNOWN_UUID
from api_tests.resources import ShopApi
from api_tests.resources.models import Wishlist
from support.assertions import assert_error, assert_schema, assert_status

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Wishlist"),
    allure.epic("API"),
    allure.feature("Wishlist"),
]


@pytest.mark.smoke
def test_a_new_customer_has_an_empty_wishlist(shopper: SignedIn) -> None:
    wishlist = assert_schema(shopper.api.wishlist.get(), Wishlist)

    assert (wishlist.items, wishlist.count) == ([], 0)


@allure.severity(allure.severity_level.CRITICAL)
def test_add_returns_the_whole_wishlist(shopper: SignedIn, catalog: Catalog) -> None:
    assert_schema(shopper.api.wishlist.add(catalog.id("product_008")), Wishlist, 201)

    wishlist = assert_schema(shopper.api.wishlist.add(catalog.id("product_016")), Wishlist, 201)

    assert wishlist.count == 2
    assert {item.product.reference for item in wishlist.items} == {"product_008", "product_016"}


def test_adding_a_duplicate_is_409(shopper: SignedIn, catalog: Catalog) -> None:
    assert_schema(shopper.api.wishlist.add(catalog.id("product_008")), Wishlist, 201)

    assert_error(shopper.api.wishlist.add(catalog.id("product_008")), 409, "error_already_in_wishlist")


def test_an_out_of_stock_product_can_be_wished_for(shopper: SignedIn, catalog: Catalog) -> None:
    # Stock is deliberately not checked: wishing for something sold out is rather the point.
    wishlist = assert_schema(shopper.api.wishlist.add(catalog.id("product_017")), Wishlist, 201)

    assert wishlist.items[0].product.in_stock is False


@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("reference", ["product_014", "product_030"], ids=["premium", "inactive"])
def test_a_hidden_product_is_404(shopper: SignedIn, catalog: Catalog, reference: str) -> None:
    assert_error(shopper.api.wishlist.add(catalog.id(reference)), 404, "error_product_not_found")


def test_premium_customer_can_wish_for_a_premium_product(premium_customer: SignedIn, catalog: Catalog) -> None:
    product_id = catalog.id("product_014")
    premium_customer.api.wishlist.remove(product_id)  # shared seeded account: start clean
    try:
        wishlist = assert_schema(premium_customer.api.wishlist.add(product_id), Wishlist, 201)
        assert "product_014" in {item.product.reference for item in wishlist.items}
    finally:
        premium_customer.api.wishlist.remove(product_id)


def test_an_unknown_product_is_404(shopper: SignedIn) -> None:
    assert_error(shopper.api.wishlist.add(UNKNOWN_UUID), 404, "error_product_not_found")


def test_remove_is_204_and_removes_the_item(shopper: SignedIn, catalog: Catalog) -> None:
    assert_schema(shopper.api.wishlist.add(catalog.id("product_008")), Wishlist, 201)

    response = shopper.api.wishlist.remove(catalog.id("product_008"))

    assert_status(response, 204)
    assert response.content == b""
    assert assert_schema(shopper.api.wishlist.get(), Wishlist).count == 0


def test_removing_something_not_on_the_wishlist_is_404(shopper: SignedIn, catalog: Catalog) -> None:
    assert_error(shopper.api.wishlist.remove(catalog.id("product_008")), 404, "error_not_in_wishlist")


def test_a_non_uuid_product_id_is_422(shopper: SignedIn) -> None:
    assert_error(shopper.api.wishlist.add("product_008"), 422, "error_validation")


@allure.severity(allure.severity_level.CRITICAL)
def test_wishlists_are_isolated_per_user(new_shopper: Callable[[str], SignedIn], catalog: Catalog) -> None:
    alice, bob = new_shopper("alice"), new_shopper("bob")
    assert_schema(alice.api.wishlist.add(catalog.id("product_008")), Wishlist, 201)

    assert assert_schema(bob.api.wishlist.get(), Wishlist).count == 0


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/wishlist"), ("POST", f"/wishlist/items/{UNKNOWN_UUID}"), ("DELETE", f"/wishlist/items/{UNKNOWN_UUID}")],
)
def test_every_wishlist_route_requires_a_token(api: ShopApi, method: str, path: str) -> None:
    assert_error(api.client.request(method, path), 401, "error_invalid_token")
