"""Provider states: how each `given(...)` in a contract is made true on the real backend.

Every handler drives the backend through its public API (and `POST /test-data/reset`), never its
database — the same black-box boundary as the functional tests. A handler returns the values the
contract injects into the replayed request (`${productId}`, `${accessToken}`, ...).

**Isolation.** The state-change server resets the backend before the first state of every
interaction (see `state_server.py`), so no interaction can observe another's leftovers and the
verification order does not matter.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from api_tests.config import Settings
from api_tests.fixtures.data import SAMPLE_ADDRESS, checkout_body
from api_tests.resources import ShopApi

StateValues = dict[str, Any]


class ProviderStateError(RuntimeError):
    """A state could not be set up. Fails the interaction loudly instead of verifying garbage."""


@dataclass
class StateContext:
    """Shared by every state of one interaction; discarded at teardown."""

    api: ShopApi
    settings: Settings
    values: StateValues = field(default_factory=dict)

    def customer(self) -> ShopApi:
        """The seeded customer, signed in once per interaction."""
        if "accessToken" not in self.values:
            account = self.settings.account("customer")
            response = self.api.anonymous().auth.login(account.email, account.password)
            _expect(response.status_code, 200, "sign in as the seeded customer")
            self.values["accessToken"] = response.json()["tokens"]["access_token"]
        return self.api.as_token(self.values["accessToken"])

    def product_id(self, reference: str) -> str:
        response = self.api.anonymous().products.get(reference)
        _expect(response.status_code, 200, f"look up {reference}")
        return str(response.json()["id"])


Handler = Callable[[StateContext, re.Match[str], dict[str, Any]], StateValues]


class ProviderStateRegistry:
    def __init__(self) -> None:
        self._states: list[tuple[re.Pattern[str], str, Handler]] = []

    def state(self, pattern: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self._states.append((re.compile(f"^{pattern}$"), pattern, handler))
            return handler

        return register

    @property
    def patterns(self) -> list[str]:
        return [pattern for _, pattern, _ in self._states]

    def setup(self, context: StateContext, name: str, params: dict[str, Any]) -> StateValues:
        for regex, _, handler in self._states:
            match = regex.match(name)
            if match:
                values = handler(context, match, params)
                context.values.update(values)
                return dict(context.values)
        raise ProviderStateError(f"Unknown provider state {name!r}. Known: {self.patterns}")


def _expect(actual: int, expected: int, what: str) -> None:
    if actual != expected:
        raise ProviderStateError(f"Provider state setup failed to {what}: HTTP {actual}, expected {expected}")


registry = ProviderStateRegistry()

_REFERENCE = r"(?P<reference>product_\d{3})"


@registry.state(r"customer user_001 exists")
def _customer_exists(context: StateContext, _: re.Match[str], __: dict[str, Any]) -> StateValues:
    # Seeded by `reset`. The credentials come from configuration, so they never appear in a contract.
    account = context.settings.account("customer")
    return {"email": account.email, "password": account.password.get_secret_value()}


@registry.state(r"customer user_001 is signed in")
def _customer_signed_in(context: StateContext, _: re.Match[str], __: dict[str, Any]) -> StateValues:
    context.customer()
    return {}


@registry.state(r"the catalogue contains the seeded products")
def _seeded_catalogue(context: StateContext, _: re.Match[str], __: dict[str, Any]) -> StateValues:
    response = context.api.anonymous().products.list(page_size=1)
    _expect(response.status_code, 200, "list products")
    if response.json()["total"] == 0:
        raise ProviderStateError("the catalogue is empty after reset")
    return {}


@registry.state(rf"product {_REFERENCE} is available")
def _product_available(context: StateContext, match: re.Match[str], _: dict[str, Any]) -> StateValues:
    reference = match["reference"]
    product = context.api.anonymous().products.get(reference)
    _expect(product.status_code, 200, f"find {reference}")
    if product.json()["stock"] < 1:
        raise ProviderStateError(f"{reference} is out of stock in the baseline")
    return {"productId": str(product.json()["id"])}


@registry.state(r"product (?P<reference>product_\d{3}) does not exist")
def _product_missing(context: StateContext, match: re.Match[str], _: dict[str, Any]) -> StateValues:
    _expect(context.api.anonymous().products.get(match["reference"]).status_code, 404, "confirm it is missing")
    return {}


@registry.state(r"the customer's cart is empty")
def _cart_empty(context: StateContext, _: re.Match[str], __: dict[str, Any]) -> StateValues:
    _expect(context.customer().cart.clear().status_code, 200, "empty the cart")
    return {}


@registry.state(rf"the customer's cart contains {_REFERENCE}")
def _cart_contains(context: StateContext, match: re.Match[str], params: dict[str, Any]) -> StateValues:
    product_id = context.product_id(match["reference"])
    quantity = int(params.get("quantity", 1))
    _expect(context.customer().cart.add_item(product_id, quantity).status_code, 201, "fill the cart")
    return {"productId": product_id}


@registry.state(r"coupon (?P<code>[A-Z0-9]+) is valid")
def _coupon_valid(context: StateContext, match: re.Match[str], _: dict[str, Any]) -> StateValues:
    # Coupons are seed data and the API has no read endpoint for them; `reset` guarantees the
    # documented codes. Only codes the seed defines as generally valid are accepted here.
    if match["code"] not in {"WELCOME10"}:
        raise ProviderStateError(f"coupon {match['code']} is not a generally valid seeded coupon")
    return {}


@registry.state(r"the customer's wishlist is empty")
def _wishlist_empty(context: StateContext, _: re.Match[str], __: dict[str, Any]) -> StateValues:
    response = context.customer().wishlist.get()
    _expect(response.status_code, 200, "read the wishlist")
    if response.json()["count"]:
        raise ProviderStateError("the wishlist is not empty after reset")
    return {}


@registry.state(rf"the customer's wishlist contains {_REFERENCE}")
def _wishlist_contains(context: StateContext, match: re.Match[str], _: dict[str, Any]) -> StateValues:
    product_id = context.product_id(match["reference"])
    _expect(context.customer().wishlist.add(product_id).status_code, 201, "add to the wishlist")
    return {"productId": product_id}


@registry.state(r"the customer has placed an order")
def _order_placed(context: StateContext, _: re.Match[str], __: dict[str, Any]) -> StateValues:
    customer = context.customer()
    _expect(customer.cart.add_item(context.product_id("product_003"), 1).status_code, 201, "fill the cart")
    order = customer.orders.place(checkout_body(payment_method="cod", address=SAMPLE_ADDRESS))
    _expect(order.status_code, 201, "place an order")
    return {"orderId": str(order.json()["id"]), "orderNumber": order.json()["order_number"]}
