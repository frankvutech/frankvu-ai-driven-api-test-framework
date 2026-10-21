"""Search, filtering, sorting and pagination on `GET /products`.

Expected results are computed from the documented seed table (`api_tests.fixtures.data`), with the
backend's rules restated independently: prices filter and sort on the *effective* price, `rating` is a
floor, every sort is tie-broken by `reference` ascending, and paging is 1-based with
`1 <= page_size <= 100`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import allure
import pytest

from api_tests.fixtures import SignedIn
from api_tests.fixtures.data import SEED_PRODUCTS, SeedProduct, visible_products
from api_tests.resources import ShopApi
from api_tests.resources.models import ProductPage
from support.assertions import assert_error, assert_schema

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Products"),
    allure.epic("API"),
    allure.feature("Products"),
]

ANONYMOUS = visible_products("anonymous")


def _references(page: ProductPage) -> list[str]:
    return [product.reference for product in page.items]


def _all_pages(api: ShopApi, **params: Any) -> list[str]:
    """Walk every page of a query and return the references in order."""
    references: list[str] = []
    page_number = 1
    while True:
        page = assert_schema(api.products.list(page=page_number, **params), ProductPage)
        references.extend(_references(page))
        if page_number >= page.total_pages:
            return references
        page_number += 1


# --------------------------------------------------------------------------------- search


@allure.story("Search")
@pytest.mark.parametrize("query", ["laptop", "LAPTOP", "LaPtOp"])
def test_search_is_case_insensitive(api: ShopApi, query: str) -> None:
    page = assert_schema(api.products.list(q=query), ProductPage)

    # product_004 "Studio Laptop 16" also matches, but is premium-only and hidden here.
    assert _references(page) == ["product_027"]


@allure.story("Search")
def test_search_matches_description_as_well_as_name(api: ShopApi) -> None:
    # "odour" appears only in product_011's description ("...resists odour.").
    page = assert_schema(api.products.list(q="odour"), ProductPage)

    assert _references(page) == ["product_011"]


@allure.story("Search")
def test_search_respects_visibility(premium_customer: SignedIn) -> None:
    page = assert_schema(premium_customer.api.products.list(q="laptop", sort="price_asc"), ProductPage)

    assert _references(page) == ["product_027", "product_004"]


@allure.story("Search")
@pytest.mark.parametrize("query", ["%", "_", "a_b", "zzz-no-such-product"])
def test_search_with_no_match_is_an_empty_page(api: ShopApi, query: str) -> None:
    # LIKE metacharacters are escaped: `%` must not return the whole catalogue.
    page = assert_schema(api.products.list(q=query), ProductPage)

    assert (page.items, page.total, page.total_pages) == ([], 0, 0)


@allure.story("Search")
def test_search_query_longer_than_200_characters_is_422(api: ShopApi) -> None:
    assert_error(api.products.list(q="x" * 201), 422, "error_validation")


# --------------------------------------------------------------------------------- filters

FILTERS: list[tuple[str, dict[str, Any], Callable[[SeedProduct], bool]]] = [
    ("category-slug", {"category": "electronics"}, lambda p: p.category == "electronics"),
    ("min-price", {"min_price": 10_000}, lambda p: p.effective_price_cents >= 10_000),
    ("max-price", {"max_price": 2_000}, lambda p: p.effective_price_cents <= 2_000),
    (
        "price-range-on-effective-price",
        {"min_price": 6_000, "max_price": 7_000},
        lambda p: 6_000 <= p.effective_price_cents <= 7_000,
    ),
    ("rating-floor", {"rating": 4.5}, lambda p: p.rating >= 4.5),
    ("rating-3-excludes-only-the-2.9", {"rating": 3}, lambda p: p.rating >= 3),
    ("in-stock", {"in_stock": True}, lambda p: p.stock > 0),
    ("out-of-stock", {"in_stock": False}, lambda p: p.stock == 0),
    (
        "combined",
        {"category": "home", "in_stock": True, "max_price": 5_000},
        lambda p: p.category == "home" and p.stock > 0 and p.effective_price_cents <= 5_000,
    ),
]


@allure.story("Filters")
@pytest.mark.parametrize(("params", "predicate"), [f[1:] for f in FILTERS], ids=[f[0] for f in FILTERS])
def test_filter_returns_exactly_the_matching_products(
    api: ShopApi, params: dict[str, Any], predicate: Callable[[SeedProduct], bool]
) -> None:
    expected = sorted(p.reference for p in ANONYMOUS if predicate(p))
    assert expected, "a filter that matches nothing proves nothing"

    actual = _all_pages(api, page_size=100, sort="price_asc", **params)

    assert sorted(actual) == expected


@allure.story("Filters")
def test_category_filter_accepts_the_category_uuid(api: ShopApi) -> None:
    categories = api.categories.list().json()
    books = next(c for c in categories if c["slug"] == "books")

    by_uuid = assert_schema(api.products.list(category=books["id"], page_size=100), ProductPage)
    by_slug = assert_schema(api.products.list(category="books", page_size=100), ProductPage)

    assert _references(by_uuid) == _references(by_slug)
    assert by_slug.total == sum(p.category == "books" for p in ANONYMOUS)


@allure.story("Filters")
def test_unknown_category_matches_nothing(api: ShopApi) -> None:
    page = assert_schema(api.products.list(category="no-such-category"), ProductPage)

    assert (page.total, page.total_pages) == (0, 0)


@allure.story("Filters")
def test_is_premium_filter_for_a_premium_customer(premium_customer: SignedIn) -> None:
    page = assert_schema(premium_customer.api.products.list(is_premium=True, page_size=100), ProductPage)

    assert sorted(_references(page)) == ["product_004", "product_014", "product_026"]


@allure.story("Filters")
def test_is_premium_filter_is_empty_for_anonymous(api: ShopApi) -> None:
    page = assert_schema(api.products.list(is_premium=True), ProductPage)

    assert page.total == 0


@allure.story("Filters")
@pytest.mark.parametrize(
    "params",
    [
        {"min_price": 5_000, "max_price": 1_000},
        {"min_price": -1},
        {"max_price": -1},
        {"rating": 5.1},
        {"rating": -0.5},
        {"in_stock": "sometimes"},
        {"sort": "cheapest"},
    ],
    ids=["min-above-max", "negative-min", "negative-max", "rating-above-5", "negative-rating", "bad-bool", "bad-sort"],
)
def test_invalid_filter_values_are_422(api: ShopApi, params: dict[str, Any]) -> None:
    # min > max is a 422 rather than an empty page: an empty page would read as "no such products"
    # when the truth is "that range is impossible".
    assert_error(api.products.list(**params), 422, "error_validation")


# --------------------------------------------------------------------------------- sorting

SORTS: dict[str, Callable[[SeedProduct], Any]] = {
    "price_asc": lambda p: (p.effective_price_cents, p.reference),
    "price_desc": lambda p: (-p.effective_price_cents, p.reference),
    "rating": lambda p: (-p.rating, p.reference),
    "popularity": lambda p: (-p.review_count, p.reference),
}


@allure.story("Sorting")
@pytest.mark.parametrize("sort", list(SORTS))
def test_sort_order_with_reference_tiebreak(api: ShopApi, sort: str) -> None:
    expected = [p.reference for p in sorted(ANONYMOUS, key=SORTS[sort])]

    assert _all_pages(api, sort=sort, page_size=100) == expected


@allure.story("Sorting")
def test_newest_is_the_default_sort(api: ShopApi) -> None:
    default = assert_schema(api.products.list(page_size=100), ProductPage)
    newest = assert_schema(api.products.list(sort="newest", page_size=100), ProductPage)

    assert _references(default) == _references(newest)
    created = [product.created_at for product in newest.items]
    assert created == sorted(created, reverse=True)


# --------------------------------------------------------------------------------- pagination


@allure.story("Pagination")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("sort", [*SORTS, "newest"])
def test_paging_visits_every_product_exactly_once(api: ShopApi, sort: str) -> None:
    # Nine seeded products share a rating; without the reference tiebreak, OFFSET paging could repeat
    # one product and skip another.
    references = _all_pages(api, sort=sort, page_size=5)

    assert len(references) == len(set(references)) == len(ANONYMOUS)


@allure.story("Pagination")
def test_last_page_is_partial(api: ShopApi) -> None:
    total = len(ANONYMOUS)
    last = -(-total // 5)

    page = assert_schema(api.products.list(page=last, page_size=5), ProductPage)

    assert len(page.items) == total - (last - 1) * 5
    assert page.total_pages == last


@allure.story("Pagination")
def test_page_past_the_end_is_empty_but_keeps_the_total(api: ShopApi) -> None:
    page = assert_schema(api.products.list(page=99, page_size=5), ProductPage)

    assert page.items == []
    assert page.total == len(ANONYMOUS)


@allure.story("Pagination")
@pytest.mark.parametrize("page_size", [1, 100])
def test_page_size_bounds_are_inclusive(api: ShopApi, page_size: int) -> None:
    page = assert_schema(api.products.list(page_size=page_size), ProductPage)

    assert len(page.items) == min(page_size, len(ANONYMOUS))


@allure.story("Pagination")
@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page_size": 0}, {"page_size": 101}, {"page": "two"}],
    ids=["page-0", "page-negative", "page-size-0", "page-size-101", "page-not-a-number"],
)
def test_out_of_bounds_paging_is_422(api: ShopApi, params: dict[str, Any]) -> None:
    assert_error(api.products.list(**params), 422, "error_validation")


@allure.story("Pagination")
def test_seed_table_is_what_the_admin_sees(admin: SignedIn) -> None:
    # Guards every expectation in this module: if the seed drifts, this fails first and says why.
    page = assert_schema(admin.api.products.list(page_size=100, sort="price_asc"), ProductPage)

    actual = {p.reference: (p.name, p.effective_price_cents, p.category.slug, p.rating) for p in page.items}
    expected = {p.reference: (p.name, p.effective_price_cents, p.category, p.rating) for p in SEED_PRODUCTS}
    assert actual == expected
