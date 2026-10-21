"""Consumer contract: placing an order and reading order history.

The contract pays cash on delivery, so no card data — not even the public test card — is ever
written into a pact file.
"""

from __future__ import annotations

import allure
import pytest
from pact import match

from api_tests.fixtures.data import SAMPLE_ADDRESS, checkout_body
from api_tests.pact import bearer_from_state, path_from_state
from api_tests.pact.matchers import EXAMPLE_ACCESS_TOKEN
from api_tests.resources import ShopApi
from support.pact_consumer import ConsumerContract
from support.pact_shapes import EXAMPLE_ORDER_ID, JSON, order

pytestmark = [
    pytest.mark.pact_consumer,
    allure.parent_suite("Pact"),
    allure.suite("Consumer contracts"),
    allure.epic("Pact"),
    allure.feature("Consumer: Orders"),
]

ORDER_BODY = checkout_body(payment_method="cod", address=SAMPLE_ADDRESS)


@allure.title("Contract: place an order, paying cash on delivery")
def test_place_order(contract: ConsumerContract) -> None:
    description = "a request to place an order for the cart"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 is signed in")
        .given("the customer's cart contains product_003", quantity=1)
        .with_request("POST", "/api/v1/orders")
        .with_header("Authorization", bearer_from_state())
        .with_body(ORDER_BODY, content_type=JSON)
        .will_respond_with(201)
        .with_body(order(), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        body = api.as_token(EXAMPLE_ACCESS_TOKEN).orders.place(ORDER_BODY).json()
        assert body["status"] == "pending"
        assert body["order_number"].startswith("SHP-")

    contract.verify(description, exercise)


@allure.title("Contract: order history")
def test_order_history(contract: ConsumerContract) -> None:
    description = "a request for the customer's order history"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 is signed in")
        .given("the customer has placed an order")
        .with_request("GET", "/api/v1/orders")
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(200)
        .with_body(
            {
                "items": match.each_like(order(), min=1),
                "page": 1,
                "total": match.int(1),
                "total_pages": match.int(1),
            },
            content_type=JSON,
        )
    )

    def exercise(api: ShopApi) -> None:
        assert api.as_token(EXAMPLE_ACCESS_TOKEN).orders.list().json()["total"] >= 1

    contract.verify(description, exercise)


@allure.title("Contract: one order by id")
def test_order_detail(contract: ConsumerContract) -> None:
    description = "a request for one of the customer's orders"
    (
        contract.pact.upon_receiving(description)
        .given("customer user_001 is signed in")
        .given("the customer has placed an order")
        .with_request("GET", path_from_state("/api/v1/orders/{}", EXAMPLE_ORDER_ID, "orderId"))
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(200)
        .with_body(order(), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        assert api.as_token(EXAMPLE_ACCESS_TOKEN).orders.get(EXAMPLE_ORDER_ID).json()["status"] == "pending"

    contract.verify(description, exercise)
