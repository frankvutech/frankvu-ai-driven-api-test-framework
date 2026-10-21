"""Categories: `GET /categories`, `GET /categories/{category_id}`. Public and unpaged."""

from __future__ import annotations

import allure
import pytest
from pydantic import TypeAdapter

from api_tests.fixtures.data import SEED_CATEGORIES, SEED_PRODUCTS, UNKNOWN_UUID
from api_tests.resources import ShopApi
from api_tests.resources.models import Category
from support.assertions import assert_error, assert_schema, assert_status

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Products"),
    allure.epic("API"),
    allure.feature("Products"),
    allure.story("Categories"),
]

_categories = TypeAdapter(list[Category])


def _list(api: ShopApi) -> list[Category]:
    response = api.categories.list()
    assert_status(response, 200)
    return _categories.validate_python(response.json())


@pytest.mark.smoke
@allure.title("The seven seeded categories, ordered by name")
def test_lists_every_category_ordered_by_name(api: ShopApi) -> None:
    categories = _list(api)

    assert [(c.slug, c.name) for c in categories] == list(SEED_CATEGORIES)


@allure.title("product_count includes premium and inactive products")
def test_product_count_counts_every_product(api: ShopApi) -> None:
    counts = {category.slug: category.product_count for category in _list(api)}

    expected = {slug: sum(p.category == slug for p in SEED_PRODUCTS) for slug, _ in SEED_CATEGORIES}
    assert counts == expected


@allure.title("Category detail by id matches the list entry")
def test_get_category_by_id(api: ShopApi) -> None:
    electronics = next(c for c in _list(api) if c.slug == "electronics")

    detail = assert_schema(api.categories.get(electronics.id), Category)

    assert detail == electronics


@allure.title("An unknown category id is 404")
def test_unknown_category_is_404(api: ShopApi) -> None:
    assert_error(api.categories.get(UNKNOWN_UUID), 404, "error_category_not_found")


@allure.title("A category id that is not a UUID is 422")
def test_non_uuid_category_id_is_422(api: ShopApi) -> None:
    # Unlike products, categories are addressed only by UUID; the slug is not accepted here.
    assert_error(api.categories.get("electronics"), 422, "error_validation")
