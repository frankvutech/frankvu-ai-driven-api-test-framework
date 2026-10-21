"""Consumer contract: browsing the catalogue (home feed, search with filters, detail, categories)."""

from __future__ import annotations

import allure
import pytest
from pact import match

from api_tests.resources import ShopApi
from support.pact_consumer import ConsumerContract
from support.pact_shapes import JSON, error, page, product, uuid

pytestmark = [
    pytest.mark.pact_consumer,
    allure.parent_suite("Pact"),
    allure.suite("Consumer contracts"),
    allure.epic("Pact"),
    allure.feature("Consumer: Products"),
]


@allure.title("Contract: the first page of the catalogue")
def test_first_page_of_products(contract: ConsumerContract) -> None:
    description = "a request for the first page of products"
    (
        contract.pact.upon_receiving(description)
        .given("the catalogue contains the seeded products")
        .with_request("GET", "/api/v1/products")
        .with_query_parameters({"page": "1", "page_size": "20"})
        .will_respond_with(200)
        .with_body(page(product()), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        body = api.products.list(page=1, page_size=20).json()
        assert body["page"] == 1
        assert body["items"][0]["reference"].startswith("product_")

    contract.verify(description, exercise)


@allure.title("Contract: search with a category filter, sorted by price")
def test_search_with_filters(contract: ConsumerContract) -> None:
    description = "a product search filtered by category and sorted by price"
    (
        contract.pact.upon_receiving(description)
        .given("the catalogue contains the seeded products")
        .with_request("GET", "/api/v1/products")
        .with_query_parameters({"q": "lamp", "category": "home", "sort": "price_asc", "page": "1", "page_size": "20"})
        .will_respond_with(200)
        .with_body(page(product("product_006", name="Desk Lamp")), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        body = api.products.list(q="lamp", category="home", sort="price_asc", page=1, page_size=20).json()
        assert len(body["items"]) >= 1

    contract.verify(description, exercise)


@allure.title("Contract: product detail by stable reference")
def test_product_detail(contract: ConsumerContract) -> None:
    description = "a request for product product_001 by reference"
    detail = {
        **product("product_001"),
        "reference": "product_001",
        "description": match.str("Over-ear headphones with active noise cancelling."),
        "discount_price_cents": match.int(6_900),
        "stock": match.int(24),
    }
    (
        contract.pact.upon_receiving(description)
        .given("product product_001 is available")
        .with_request("GET", "/api/v1/products/product_001")
        .will_respond_with(200)
        .with_body(detail, content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        body = api.products.get("product_001").json()
        assert body["reference"] == "product_001"
        assert body["effective_price_cents"] <= body["price_cents"]

    contract.verify(description, exercise)


@allure.title("Contract: an unknown product is a 404 with error_product_not_found")
def test_unknown_product(contract: ConsumerContract) -> None:
    description = "a request for a product that does not exist"
    (
        contract.pact.upon_receiving(description)
        .given("product product_999 does not exist")
        .with_request("GET", "/api/v1/products/product_999")
        .will_respond_with(404)
        .with_body(error("error_product_not_found"), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        assert api.products.get("product_999").json()["error"]["code"] == "error_product_not_found"

    contract.verify(description, exercise)


@allure.title("Contract: the category list for the filter chips")
def test_categories(contract: ConsumerContract) -> None:
    description = "a request for every category"
    (
        contract.pact.upon_receiving(description)
        .given("the catalogue contains the seeded products")
        .with_request("GET", "/api/v1/categories")
        .will_respond_with(200)
        .with_body(
            match.each_like(
                {
                    "id": uuid("6f1c2a9e-1b2c-4d3e-8f40-0000000000d1"),
                    "slug": match.regex("electronics", regex=r"^[a-z0-9]+(-[a-z0-9]+)*$"),
                    "name": match.str("Electronics"),
                    "product_count": match.int(6),
                },
                min=1,
            ),
            content_type=JSON,
        )
    )

    def exercise(api: ShopApi) -> None:
        assert api.categories.list().json()[0]["slug"]

    contract.verify(description, exercise)
