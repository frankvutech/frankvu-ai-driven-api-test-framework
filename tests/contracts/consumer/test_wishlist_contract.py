"""Consumer contract: adding to and removing from the wishlist."""

from __future__ import annotations

import allure
import pytest
from pact import match

from api_tests.pact import bearer_from_state, path_from_state
from api_tests.pact.matchers import EXAMPLE_ACCESS_TOKEN
from api_tests.resources import ShopApi
from support.pact_consumer import ConsumerContract
from support.pact_shapes import EXAMPLE_PRODUCT_ID, JSON, uuid

pytestmark = [
    pytest.mark.pact_consumer,
    allure.parent_suite("Pact"),
    allure.suite("Consumer contracts"),
    allure.epic("Pact"),
    allure.feature("Consumer: Wishlist"),
]


@allure.title("Contract: add a product to the wishlist")
def test_add_to_wishlist(contract: ConsumerContract) -> None:
    description = "a request to add product_008 to an empty wishlist"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 is signed in")
        .given("the customer's wishlist is empty")
        .given("product product_008 is available")
        .with_request("POST", path_from_state("/api/v1/wishlist/items/{}", EXAMPLE_PRODUCT_ID, "productId"))
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(201)
        .with_body(
            {
                "items": [
                    {
                        "product": {
                            "id": uuid(EXAMPLE_PRODUCT_ID),
                            "reference": "product_008",
                            "name": match.str("Clean Architecture"),
                        }
                    }
                ],
                "count": 1,
            },
            content_type=JSON,
        )
    )

    def exercise(api: ShopApi) -> None:
        response = api.as_token(EXAMPLE_ACCESS_TOKEN).wishlist.add(EXAMPLE_PRODUCT_ID)
        assert response.json()["count"] == 1

    contract.verify(description, exercise)


@allure.title("Contract: remove a product from the wishlist")
def test_remove_from_wishlist(contract: ConsumerContract) -> None:
    description = "a request to remove product_008 from the wishlist"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 is signed in")
        .given("the customer's wishlist contains product_008")
        .with_request("DELETE", path_from_state("/api/v1/wishlist/items/{}", EXAMPLE_PRODUCT_ID, "productId"))
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(204)
    )

    def exercise(api: ShopApi) -> None:
        assert api.as_token(EXAMPLE_ACCESS_TOKEN).wishlist.remove(EXAMPLE_PRODUCT_ID).status_code == 204

    contract.verify(description, exercise)
