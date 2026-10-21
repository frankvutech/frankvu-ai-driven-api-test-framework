"""`POST /checkout/validate`: "would this checkout succeed, and what would it cost?"

It answers **200 even when the checkout is invalid** and reports every problem at once. Card expiry
is judged against the backend's fixed reference month (`CARD_EXPIRY_REFERENCE`, default 2026-01),
not the wall clock, so these expectations do not age.
"""

from __future__ import annotations

from typing import Any

import allure
import pytest

from api_tests.fixtures import Catalog, SignedIn
from api_tests.fixtures.data import SAMPLE_ADDRESS, SAMPLE_CARD, WELCOME10, checkout_body, expected_totals
from api_tests.resources import ShopApi
from api_tests.resources.models import Cart, CheckoutValidation
from support.assertions import assert_error, assert_schema, assert_totals

pytestmark = [
    pytest.mark.functional,
    allure.parent_suite("API"),
    allure.suite("Orders"),
    allure.epic("API"),
    allure.feature("Checkout"),
]


@pytest.fixture
def filled_cart(shopper: SignedIn, catalog: Catalog) -> SignedIn:
    assert_schema(shopper.api.cart.add_item(catalog.id("product_001"), 1), Cart, 201)
    return shopper


def _validate(shopper: SignedIn, body: dict[str, Any]) -> CheckoutValidation:
    return assert_schema(shopper.api.checkout.validate(body), CheckoutValidation)


@pytest.mark.smoke
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize("payment_method", ["card", "paypal", "cod"])
def test_a_complete_checkout_is_valid(filled_cart: SignedIn, payment_method: str) -> None:
    result = _validate(filled_cart, checkout_body(payment_method=payment_method))

    assert (result.valid, result.problems) == (True, [])
    assert_totals(result.totals, expected_totals([("product_001", 1)]))


@allure.severity(allure.severity_level.CRITICAL)
def test_an_empty_cart_is_reported_in_a_200(shopper: SignedIn) -> None:
    result = _validate(shopper, checkout_body())

    assert result.valid is False
    assert result.problem_codes == {None: "error_cart_empty"}


def test_every_problem_is_reported_at_once(shopper: SignedIn) -> None:
    blank = dict.fromkeys(("full_name", "street", "city", "postal_code", "country"), "")

    result = _validate(shopper, checkout_body(address=blank, include_card=False))

    assert result.valid is False
    assert result.problem_codes == {
        None: "error_cart_empty",
        "full_name": "error_address_incomplete",
        "street": "error_address_incomplete",
        "city": "error_address_incomplete",
        "postal_code": "error_address_incomplete",
        "country": "error_address_incomplete",
        "card": "error_card_required",
    }


def test_whitespace_only_address_fields_are_incomplete(filled_cart: SignedIn) -> None:
    result = _validate(filled_cart, checkout_body(address={**SAMPLE_ADDRESS, "city": "   "}))

    assert result.problem_codes == {"city": "error_address_incomplete"}


def test_phone_is_optional(filled_cart: SignedIn) -> None:
    address = {key: value for key, value in SAMPLE_ADDRESS.items() if key != "phone"}

    assert _validate(filled_cart, checkout_body(address=address)).valid is True


CARD_CASES = [
    ({"number": "4111111111111112"}, "card.number", "card_invalid_number"),
    ({"number": "4111"}, "card.number", "invalid_format"),
    ({"number": "411111111111111a"}, "card.number", "invalid_format"),
    ({"expiry": "01/20"}, "card.expiry", "card_expired"),
    ({"expiry": "13/99"}, "card.expiry", "invalid_format"),
    ({"expiry": "1230"}, "card.expiry", "invalid_format"),
    ({"cvv": "12"}, "card.cvv", "invalid_format"),
    ({"holder": "   "}, "card.holder", "invalid_format"),
]


@allure.story("Card validation")
@pytest.mark.parametrize(
    ("override", "field", "code"),
    CARD_CASES,
    ids=["luhn", "too-short", "non-digit", "expired", "month-13", "no-slash", "cvv-short", "blank-holder"],
)
def test_card_problems_name_the_field(filled_cart: SignedIn, override: dict[str, str], field: str, code: str) -> None:
    result = _validate(filled_cart, checkout_body(card={**SAMPLE_CARD, **override}))

    assert result.valid is False
    assert result.problem_codes == {field: code}


@allure.story("Card validation")
def test_card_expiring_in_the_reference_month_is_still_valid(filled_cart: SignedIn) -> None:
    # Valid *through* its expiry month: 01/26 against the 2026-01 reference month.
    assert _validate(filled_cart, checkout_body(card={**SAMPLE_CARD, "expiry": "01/26"})).valid is True


@allure.story("Card validation")
def test_card_details_are_ignored_for_non_card_payment(filled_cart: SignedIn) -> None:
    body = checkout_body(payment_method="paypal")
    body["card"] = {**SAMPLE_CARD, "number": "4111111111111112"}

    assert _validate(filled_cart, body).valid is True


@allure.story("Card validation")
@allure.severity(allure.severity_level.CRITICAL)
def test_the_cvv_is_never_echoed(filled_cart: SignedIn) -> None:
    response = filled_cart.api.checkout.validate(checkout_body(card={**SAMPLE_CARD, "cvv": "987"}))

    assert "987" not in response.text
    assert SAMPLE_CARD["number"] not in response.text


def test_totals_follow_the_shipping_method_and_coupon(filled_cart: SignedIn) -> None:
    assert_schema(filled_cart.api.cart.apply_coupon(WELCOME10.code), Cart)

    result = _validate(filled_cart, checkout_body(shipping_method="express", payment_method="cod"))

    assert_totals(result.totals, expected_totals([("product_001", 1)], discount_percent=10, shipping_method="express"))


def test_validation_does_not_persist_anything(filled_cart: SignedIn) -> None:
    before = assert_schema(filled_cart.api.cart.get(), Cart)

    _validate(filled_cart, checkout_body())

    assert assert_schema(filled_cart.api.cart.get(), Cart) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"address": SAMPLE_ADDRESS, "shipping_method": "teleport"},
        {"address": SAMPLE_ADDRESS, "payment_method": "bitcoin"},
        {"address": {**SAMPLE_ADDRESS, "postal_code": "1" * 21}},
    ],
    ids=["no-address", "bad-shipping", "bad-payment", "postal-code-too-long"],
)
def test_malformed_requests_are_422_not_a_report(filled_cart: SignedIn, body: dict[str, Any]) -> None:
    assert_error(filled_cart.api.checkout.validate(body), 422, "error_validation")


def test_checkout_requires_a_token(api: ShopApi) -> None:
    assert_error(api.checkout.validate(checkout_body()), 401, "error_invalid_token")
