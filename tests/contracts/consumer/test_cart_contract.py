"""Consumer contract: reading and changing the cart, and applying a coupon.

Cart lines are addressed by the product's UUID, which differs per backend instance, so paths and
bodies carry `${productId}` from provider state. The bearer token is injected the same way.
"""

from __future__ import annotations

import allure
import pytest
from pact import match

from api_tests.pact import bearer_from_state, from_state, path_from_state
from api_tests.pact.matchers import EXAMPLE_ACCESS_TOKEN, UUID_REGEX
from api_tests.resources import ShopApi
from support.pact_consumer import ConsumerContract
from support.pact_shapes import EXAMPLE_PRODUCT_ID, JSON, cart_line, error, totals

pytestmark = [
    pytest.mark.pact_consumer,
    allure.parent_suite("Pact"),
    allure.suite("Consumer contracts"),
    allure.epic("Pact"),
    allure.feature("Consumer: Cart"),
]

SIGNED_IN = "customer user_001 is signed in"
CART_HAS_PRODUCT = "the customer's cart contains product_001"


def _signed_in(api: ShopApi) -> ShopApi:
    return api.as_token(EXAMPLE_ACCESS_TOKEN)


@allure.title("Contract: read the cart")
def test_read_cart(contract: ConsumerContract) -> None:
    description = "a request for the cart holding one product"
    (
        contract.pact.upon_receiving(description)
        .given(SIGNED_IN)
        .given(CART_HAS_PRODUCT, quantity=1)
        .with_request("GET", "/api/v1/cart")
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(200)
        .with_body(
            {
                "items": [cart_line()],
                "item_count": 1,
                "coupon": None,
                "shipping_method": "standard",
                "totals": totals(),
            },
            content_type=JSON,
        )
    )

    def exercise(api: ShopApi) -> None:
        body = _signed_in(api).cart.get().json()
        assert body["item_count"] == 1

    contract.verify(description, exercise)


@allure.title("Contract: add a product to an empty cart")
def test_add_to_cart(contract: ConsumerContract) -> None:
    description = "a request to add product_001 to an empty cart"
    (
        contract.pact.upon_receiving(description)
        .given(SIGNED_IN)
        .given("the customer's cart is empty")
        .given("product product_001 is available")
        .with_request("POST", "/api/v1/cart/items")
        .with_header("Authorization", bearer_from_state())
        .with_body(
            {
                "product_id": from_state(EXAMPLE_PRODUCT_ID, "${productId}", regex=f"^{UUID_REGEX}$"),
                "quantity": 1,
            },
            content_type=JSON,
        )
        .will_respond_with(201)
        .with_body({"items": [cart_line()], "item_count": 1, "totals": totals()}, content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        response = _signed_in(api).cart.add_item(EXAMPLE_PRODUCT_ID, 1)
        assert response.status_code == 201

    contract.verify(description, exercise)


@allure.title("Contract: change a line's quantity")
def test_update_quantity(contract: ConsumerContract) -> None:
    description = "a request to set product_001's quantity to 2"
    (
        contract.pact.upon_receiving(description)
        .given(SIGNED_IN)
        .given(CART_HAS_PRODUCT, quantity=1)
        .with_request("PATCH", path_from_state("/api/v1/cart/items/{}", EXAMPLE_PRODUCT_ID, "productId"))
        .with_header("Authorization", bearer_from_state())
        .with_body({"quantity": 2}, content_type=JSON)
        .will_respond_with(200)
        .with_body({"items": [cart_line(quantity=2)], "item_count": 2, "totals": totals()}, content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        assert _signed_in(api).cart.update_item(EXAMPLE_PRODUCT_ID, 2).json()["item_count"] == 2

    contract.verify(description, exercise)


@allure.title("Contract: remove a line")
def test_remove_line(contract: ConsumerContract) -> None:
    description = "a request to remove product_001 from the cart"
    (
        contract.pact.upon_receiving(description)
        .given(SIGNED_IN)
        .given(CART_HAS_PRODUCT, quantity=1)
        .with_request("DELETE", path_from_state("/api/v1/cart/items/{}", EXAMPLE_PRODUCT_ID, "productId"))
        .with_header("Authorization", bearer_from_state())
        .will_respond_with(200)
        .with_body({"items": [], "item_count": 0}, content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        assert _signed_in(api).cart.remove_item(EXAMPLE_PRODUCT_ID).json()["items"] == []

    contract.verify(description, exercise)


@allure.title("Contract: apply the WELCOME10 coupon")
def test_apply_coupon(contract: ConsumerContract) -> None:
    description = "a request to apply coupon WELCOME10"
    (
        contract.pact.upon_receiving(description)
        .given(SIGNED_IN)
        .given(CART_HAS_PRODUCT, quantity=1)
        .given("coupon WELCOME10 is valid")
        .with_request("POST", "/api/v1/cart/coupon")
        .with_header("Authorization", bearer_from_state())
        .with_body({"code": "WELCOME10"}, content_type=JSON)
        .will_respond_with(200)
        .with_body(
            {
                "coupon": {"code": "WELCOME10", "discount_percent": 10},
                "totals": {**totals(), "discount_cents": match.int(690)},
            },
            content_type=JSON,
        )
    )

    def exercise(api: ShopApi) -> None:
        assert _signed_in(api).cart.apply_coupon("WELCOME10").json()["coupon"]["code"] == "WELCOME10"

    contract.verify(description, exercise)


@allure.title("Contract: the cart without a token is a 401")
def test_cart_requires_a_token(contract: ConsumerContract) -> None:
    description = "a request for the cart without an access token"
    (
        contract.pact.upon_receiving(description)
        .with_request("GET", "/api/v1/cart")
        .will_respond_with(401)
        .with_header("WWW-Authenticate", "Bearer")
        .with_body(error("error_invalid_token"), content_type=JSON)
    )

    def exercise(api: ShopApi) -> None:
        assert api.cart.get().json()["error"]["code"] == "error_invalid_token"

    contract.verify(description, exercise)
