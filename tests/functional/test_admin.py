"""Admin authorisation and admin catalogue writes.

Endpoints: `POST /admin/categories`, `PATCH/DELETE /admin/categories/{id}`, `POST /admin/products`,
`PATCH/DELETE /admin/products/{id}`. `require_admin` is attached to the router: anonymous is 401,
any signed-in non-admin — **including premium** — is 403.

Everything an admin test creates is registered with `admin_cleanup` and deleted afterwards; `reset`
re-seeds the documented catalogue but never removes extra rows. Names are derived from the run id,
so two runs never collide and one run is reproducible.
"""

from __future__ import annotations

import hashlib
from typing import Any

import allure
import pytest

from api_tests.config import Settings
from api_tests.fixtures import Catalog, SignedIn
from api_tests.fixtures.data import UNKNOWN_UUID, checkout_body
from api_tests.resources import ShopApi
from api_tests.resources.models import Cart, Category, Order, Product
from support.assertions import assert_error, assert_schema, assert_status

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Admin"),
    allure.epic("API"),
    allure.feature("Admin authorization"),
]

ADMIN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/admin/categories", {"name": "Nope", "slug": "nope"}),
    ("PATCH", f"/admin/categories/{UNKNOWN_UUID}", {"name": "Nope"}),
    ("DELETE", f"/admin/categories/{UNKNOWN_UUID}", None),
    ("POST", "/admin/products", {"reference": "nope"}),
    ("PATCH", f"/admin/products/{UNKNOWN_UUID}", {"name": "Nope"}),
    ("DELETE", f"/admin/products/{UNKNOWN_UUID}", None),
]
ROUTE_IDS = [f"{method} {path.replace(UNKNOWN_UUID, '{id}')}" for method, path, _ in ADMIN_ROUTES]


def _slug(settings: Settings, label: str) -> str:
    return f"mtp-{hashlib.sha256(f'{settings.run_id}:{label}'.encode()).hexdigest()[:10]}"


# --------------------------------------------------------------------------------- authorisation matrix


@allure.story("Authorization matrix")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES, ids=ROUTE_IDS)
def test_admin_routes_are_401_without_a_token(
    api: ShopApi, method: str, path: str, body: dict[str, Any] | None
) -> None:
    assert_error(api.client.request(method, path, json=body), 401, "error_invalid_token")


@allure.story("Authorization matrix")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("role", ["customer", "premium_customer"])
@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES, ids=ROUTE_IDS)
def test_admin_routes_are_403_for_non_admins(
    request: pytest.FixtureRequest, role: str, method: str, path: str, body: dict[str, Any] | None
) -> None:
    # The guard runs before body validation, so even a malformed body is refused with 403.
    signed_in: SignedIn = request.getfixturevalue(role)

    assert_error(signed_in.api.client.request(method, path, json=body), 403, "error_forbidden")


@allure.story("Authorization matrix")
def test_a_non_admin_token_is_403_even_for_an_unknown_id(customer: SignedIn) -> None:
    # 403 before 404: the caller learns nothing about which ids exist.
    assert_error(customer.api.admin.delete_product(UNKNOWN_UUID), 403, "error_forbidden")


# --------------------------------------------------------------------------------- categories


@pytest.fixture
def new_category(admin: SignedIn, settings: Settings, admin_cleanup: dict[str, list[str]]) -> Category:
    slug = _slug(settings, "category")
    category = assert_schema(admin.api.admin.create_category({"name": slug.title(), "slug": slug}), Category, 201)
    admin_cleanup["categories"].append(str(category.id))
    return category


@allure.story("Categories")
@allure.feature("Admin catalogue")
def test_create_category(new_category: Category, api: ShopApi) -> None:
    assert new_category.product_count == 0

    assert assert_schema(api.categories.get(new_category.id), Category) == new_category


@allure.story("Categories")
@allure.feature("Admin catalogue")
@pytest.mark.parametrize("clash", ["slug", "name"])
def test_duplicate_category_is_409(admin: SignedIn, clash: str) -> None:
    body = (
        {"name": "Electronics", "slug": "electronics-clash"}
        if clash == "name"
        else {"name": "Elec 2", "slug": "electronics"}
    )

    assert_error(admin.api.admin.create_category(body), 409, "error_category_name_taken")


@allure.story("Categories")
@allure.feature("Admin catalogue")
@pytest.mark.parametrize(
    "body",
    [{"name": "Bad", "slug": "Bad Slug"}, {"name": "   ", "slug": "blank-name"}, {"name": "No slug"}],
    ids=["slug-pattern", "blank-name", "missing-slug"],
)
def test_invalid_category_is_422(admin: SignedIn, body: dict[str, str]) -> None:
    assert_error(admin.api.admin.create_category(body), 422, "error_validation")


@allure.story("Categories")
@allure.feature("Admin catalogue")
def test_rename_category(admin: SignedIn, new_category: Category) -> None:
    renamed = assert_schema(admin.api.admin.update_category(new_category.id, {"name": "Renamed"}), Category)

    assert (renamed.name, renamed.slug) == ("Renamed", new_category.slug)


@allure.story("Categories")
@allure.feature("Admin catalogue")
def test_empty_category_patch_is_422(admin: SignedIn, new_category: Category) -> None:
    assert_error(admin.api.admin.update_category(new_category.id, {}), 422, "error_validation")


@allure.story("Categories")
@allure.feature("Admin catalogue")
def test_delete_an_empty_category(admin: SignedIn, settings: Settings, api: ShopApi) -> None:
    slug = _slug(settings, "category-to-delete")
    category = assert_schema(admin.api.admin.create_category({"name": slug, "slug": slug}), Category, 201)

    assert_status(admin.api.admin.delete_category(category.id), 204)
    assert_error(api.categories.get(category.id), 404, "error_category_not_found")


@allure.story("Categories")
@allure.feature("Admin catalogue")
def test_a_category_with_products_cannot_be_deleted(admin: SignedIn, api: ShopApi) -> None:
    electronics = next(c for c in api.categories.list().json() if c["slug"] == "electronics")

    assert_error(admin.api.admin.delete_category(electronics["id"]), 409, "error_category_not_empty")


@allure.story("Categories")
@allure.feature("Admin catalogue")
def test_unknown_category_is_404_for_admin_writes(admin: SignedIn) -> None:
    assert_error(admin.api.admin.delete_category(UNKNOWN_UUID), 404, "error_category_not_found")


# --------------------------------------------------------------------------------- products


@pytest.fixture
def product_body(settings: Settings, new_category: Category) -> dict[str, Any]:
    return {
        "reference": _slug(settings, "product").replace("-", "_"),
        "name": "Framework Test Product",
        "description": "Created by the API test framework; deleted after the test.",
        "price_cents": 5_000,
        "discount_price_cents": 4_000,
        "category_id": str(new_category.id),
        "stock": 3,
    }


@pytest.fixture
def new_product(admin: SignedIn, product_body: dict[str, Any], admin_cleanup: dict[str, list[str]]) -> Product:
    product = assert_schema(admin.api.admin.create_product(product_body), Product, 201)
    admin_cleanup["products"].insert(0, str(product.id))  # products go before their category
    return product


@allure.story("Products")
@allure.feature("Admin catalogue")
@allure.severity(allure.severity_level.CRITICAL)
def test_create_product_and_read_it_publicly(new_product: Product, product_body: dict[str, Any], api: ShopApi) -> None:
    assert (new_product.reference, new_product.effective_price_cents, new_product.stock) == (
        product_body["reference"],
        4_000,
        3,
    )

    assert assert_schema(api.products.get(new_product.reference), Product) == new_product


@allure.story("Products")
@allure.feature("Admin catalogue")
def test_duplicate_reference_is_409(admin: SignedIn, product_body: dict[str, Any]) -> None:
    assert_error(
        admin.api.admin.create_product({**product_body, "reference": "product_001"}),
        409,
        "error_product_reference_taken",
    )


@allure.story("Products")
@allure.feature("Admin catalogue")
@pytest.mark.parametrize(
    "override",
    [{"discount_price_cents": 5_000}, {"price_cents": -1}, {"rating": 5.5}, {"name": ""}],
    ids=["discount-not-below-price", "negative-price", "rating-above-5", "empty-name"],
)
def test_invalid_product_is_422(admin: SignedIn, product_body: dict[str, Any], override: dict[str, Any]) -> None:
    assert_error(admin.api.admin.create_product({**product_body, **override}), 422, "error_validation")


@allure.story("Products")
@allure.feature("Admin catalogue")
def test_product_in_an_unknown_category_is_404(admin: SignedIn, product_body: dict[str, Any]) -> None:
    response = admin.api.admin.create_product({**product_body, "category_id": UNKNOWN_UUID})

    assert_error(response, 404, "error_category_not_found")


@allure.story("Products")
@allure.feature("Admin catalogue")
def test_patch_is_partial_and_keeps_the_discount(admin: SignedIn, new_product: Product) -> None:
    patched = assert_schema(admin.api.admin.update_product(new_product.id, {"name": "Renamed"}), Product)

    assert (patched.name, patched.discount_price_cents) == ("Renamed", 4_000)


@allure.story("Products")
@allure.feature("Admin catalogue")
def test_patch_with_explicit_null_clears_the_discount(admin: SignedIn, new_product: Product) -> None:
    patched = assert_schema(admin.api.admin.update_product(new_product.id, {"discount_price_cents": None}), Product)

    assert (patched.discount_price_cents, patched.effective_price_cents) == (None, 5_000)


@allure.story("Products")
@allure.feature("Admin catalogue")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Backend defect: PATCH /admin/products/{id} that makes the discount >= the price answers 200 "
        "and echoes the change, but the update is rolled back (ck_products_discount_below_price fails "
        "at commit, after the response). The schema docstring promises a 422."
    ),
)
def test_patch_that_breaks_the_price_constraint_is_422(admin: SignedIn, new_product: Product, api: ShopApi) -> None:
    response = admin.api.admin.update_product(new_product.id, {"price_cents": 3_000})

    persisted = assert_schema(api.products.get(new_product.reference), Product)
    assert persisted.price_cents == 5_000, "the invalid update must not be persisted"
    assert_error(response, 422, "error_validation")


@allure.story("Products")
@allure.feature("Admin catalogue")
def test_admin_can_reach_inactive_products(admin: SignedIn, new_product: Product, api: ShopApi) -> None:
    assert_schema(admin.api.admin.update_product(new_product.id, {"is_active": False}), Product)

    assert_error(api.products.get(new_product.reference), 404, "error_product_not_found")
    assert assert_schema(admin.api.products.get(new_product.reference), Product).is_active is False


@allure.story("Products")
@allure.feature("Admin catalogue")
def test_delete_a_product_that_was_never_ordered(admin: SignedIn, product_body: dict[str, Any], api: ShopApi) -> None:
    product = assert_schema(admin.api.admin.create_product(product_body), Product, 201)

    assert_status(admin.api.admin.delete_product(product.id), 204)
    assert_error(api.products.get(product.reference), 404, "error_product_not_found")


@allure.story("Products")
@allure.feature("Admin catalogue")
@pytest.mark.destructive
def test_a_product_on_an_order_cannot_be_deleted(admin: SignedIn, shopper: SignedIn, catalog: Catalog) -> None:
    assert_schema(shopper.api.cart.add_item(catalog.id("product_003"), 1), Cart, 201)
    assert_schema(shopper.api.orders.place(checkout_body(payment_method="cod")), Order, 201)

    assert_error(admin.api.admin.delete_product(catalog.id("product_003")), 409, "error_product_in_use")
