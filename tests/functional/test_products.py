"""Product listing defaults, detail lookup and visibility rules.

Endpoints: `GET /products`, `GET /products/{product_id}` (UUID or stable reference).

Visibility (`CatalogueViewer`): premium products only for premium customers and admins; inactive
products only for admins; a hidden product answers 404 — never 403, which would confirm it exists.
"""

from __future__ import annotations

from decimal import Decimal

import allure
import pytest

from api_tests.fixtures import Catalog, SignedIn
from api_tests.fixtures.data import (
    INACTIVE,
    PREMIUM_ONLY,
    PRODUCTS,
    UNKNOWN_PRODUCT_REFERENCE,
    UNKNOWN_UUID,
    Viewer,
    visible_products,
)
from api_tests.resources import ShopApi
from api_tests.resources.models import Product, ProductPage
from support.assertions import assert_error, assert_schema

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Products"),
    allure.epic("API"),
    allure.feature("Products"),
]


def _viewer_api(viewer: Viewer, api: ShopApi, request: pytest.FixtureRequest) -> ShopApi:
    if viewer == "anonymous":
        return api
    fixture = {"customer": "customer", "premium_customer": "premium_customer", "admin": "admin"}[viewer]
    signed_in: SignedIn = request.getfixturevalue(fixture)
    return signed_in.api


VIEWERS: list[Viewer] = ["anonymous", "customer", "premium_customer", "admin"]


# --------------------------------------------------------------------------------- listing


@pytest.mark.smoke
@allure.story("Listing")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("The default listing is the first 20 products the caller may see")
def test_default_listing_envelope(api: ShopApi) -> None:
    page = assert_schema(api.products.list(), ProductPage)

    visible = len(visible_products("anonymous"))
    assert (page.page, page.page_size, page.total) == (1, 20, visible)
    assert page.total_pages == -(-visible // 20)
    assert len(page.items) == 20


@allure.story("Visibility")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("viewer", VIEWERS)
def test_total_counts_only_what_the_caller_may_see(
    api: ShopApi, request: pytest.FixtureRequest, viewer: Viewer
) -> None:
    client = _viewer_api(viewer, api, request)

    page = assert_schema(client.products.list(page_size=100), ProductPage)

    assert {p.reference for p in page.items} == {p.reference for p in visible_products(viewer)}
    assert page.total == len(visible_products(viewer))


# --------------------------------------------------------------------------------- detail


@pytest.mark.smoke
@allure.story("Detail")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("Product detail by reference returns the documented seed values")
def test_detail_by_reference_matches_the_seed(api: ShopApi) -> None:
    product = assert_schema(api.products.get("product_012"), Product)
    seed = PRODUCTS["product_012"]

    assert product.reference == "product_012"
    assert product.name == seed.name == "Smart Water Bottle"
    assert (product.price_cents, product.discount_price_cents) == (2_490, None)
    assert product.effective_price_cents == 2_490
    assert product.category.slug == "sports"
    assert product.rating == Decimal("4.3")
    assert (product.review_count, product.stock, product.in_stock) == (96, seed.stock, True)
    assert (product.is_premium, product.is_active) == (False, True)


@allure.story("Detail")
@allure.title("A discounted product's effective price is the discount")
def test_discounted_product_effective_price(api: ShopApi) -> None:
    product = assert_schema(api.products.get("product_001"), Product)

    assert (product.price_cents, product.discount_price_cents, product.effective_price_cents) == (
        8_900,
        6_900,
        6_900,
    )


@allure.story("Detail")
@allure.title("Lookup by UUID and by reference return the same product")
def test_detail_by_uuid_equals_detail_by_reference(api: ShopApi, catalog: Catalog) -> None:
    by_reference = assert_schema(api.products.get("product_007"), Product)

    by_uuid = assert_schema(api.products.get(catalog.id("product_007")), Product)

    assert by_uuid == by_reference


@allure.story("Detail")
@allure.title("An out-of-stock product is visible, with in_stock false")
def test_out_of_stock_product_is_still_listed(api: ShopApi) -> None:
    product = assert_schema(api.products.get("product_005"), Product)

    assert (product.stock, product.in_stock) == (0, False)


@allure.story("Detail")
@pytest.mark.parametrize(
    "identifier", [UNKNOWN_PRODUCT_REFERENCE, UNKNOWN_UUID, "nonsense"], ids=["reference", "uuid", "garbage"]
)
def test_unknown_product_is_404(api: ShopApi, identifier: str) -> None:
    assert_error(api.products.get(identifier), 404, "error_product_not_found")


# --------------------------------------------------------------------------------- visibility


@allure.story("Visibility")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("viewer", ["anonymous", "customer"])
@pytest.mark.parametrize("reference", PREMIUM_ONLY)
def test_premium_products_are_404_to_ordinary_callers(
    api: ShopApi, request: pytest.FixtureRequest, viewer: Viewer, reference: str
) -> None:
    client = _viewer_api(viewer, api, request)

    assert_error(client.products.get(reference), 404, "error_product_not_found")


@allure.story("Visibility")
@pytest.mark.parametrize("viewer", ["premium_customer", "admin"])
def test_premium_products_are_visible_to_premium_and_admin(
    api: ShopApi, request: pytest.FixtureRequest, viewer: Viewer
) -> None:
    client = _viewer_api(viewer, api, request)

    product = assert_schema(client.products.get("product_004"), Product)

    assert product.is_premium is True


@allure.story("Visibility")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("viewer", ["anonymous", "customer", "premium_customer"])
def test_inactive_products_are_404_to_everyone_but_admin(
    api: ShopApi, request: pytest.FixtureRequest, viewer: Viewer
) -> None:
    client = _viewer_api(viewer, api, request)

    assert_error(client.products.get(INACTIVE[0]), 404, "error_product_not_found")


@allure.story("Visibility")
def test_admin_sees_inactive_products(admin: SignedIn) -> None:
    product = assert_schema(admin.api.products.get(INACTIVE[0]), Product)

    assert product.is_active is False


@allure.story("Visibility")
@allure.severity(allure.severity_level.CRITICAL)
@allure.title("A bad token on a public endpoint is 401, not silently anonymous")
def test_bad_token_on_public_listing_is_401(api: ShopApi) -> None:
    # Treating an expired token as "not signed in" would quietly hide premium products from a
    # premium customer instead of telling them their session ended.
    assert_error(api.as_token("not.a.jwt").products.list(), 401, "error_invalid_token")
