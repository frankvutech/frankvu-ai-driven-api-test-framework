"""Domain clients, one per backend resource.

**No test builds a URL or calls httpx directly.** When the API renames a path, one method changes and
every test keeps working. Each method maps to exactly one operation in the backend's OpenAPI document
(ShopTest API 0.1.0); nothing here exists that the backend does not implement.

Methods return `httpx.Response` and never raise for a status — tests decide what a status means.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from pydantic import SecretStr

from api_tests.client import HttpClient

ProductId = uuid.UUID | str


def _secret(value: str | SecretStr) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


class Resource:
    def __init__(self, client: HttpClient) -> None:
        self.client = client


class HealthApi(Resource):
    def liveness(self) -> httpx.Response:
        return self.client.get("/health")

    def database(self) -> httpx.Response:
        return self.client.get("/health/database")


class AuthApi(Resource):
    def login(self, email: str, password: str | SecretStr) -> httpx.Response:
        return self.client.post("/auth/login", json={"email": email, "password": _secret(password)})

    def register(
        self,
        email: str,
        password: str | SecretStr,
        first_name: str,
        last_name: str,
        **extra: Any,
    ) -> httpx.Response:
        body = {
            "email": email,
            "password": _secret(password),
            "first_name": first_name,
            "last_name": last_name,
            **extra,
        }
        return self.client.post("/auth/register", json=body)

    def refresh(self, refresh_token: str) -> httpx.Response:
        return self.client.post("/auth/refresh", json={"refresh_token": refresh_token})

    def logout(self, refresh_token: str) -> httpx.Response:
        return self.client.post("/auth/logout", json={"refresh_token": refresh_token})

    def me(self) -> httpx.Response:
        return self.client.get("/auth/me")


class CategoriesApi(Resource):
    def list(self) -> httpx.Response:
        return self.client.get("/categories")

    def get(self, category_id: uuid.UUID | str) -> httpx.Response:
        return self.client.get(f"/categories/{category_id}")


class ProductsApi(Resource):
    def list(
        self,
        *,
        q: str | None = None,
        category: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        rating: float | None = None,
        in_stock: bool | None = None,
        is_premium: bool | None = None,
        sort: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        **raw: Any,
    ) -> httpx.Response:
        params = {
            "q": q,
            "category": category,
            "min_price": min_price,
            "max_price": max_price,
            "rating": rating,
            "in_stock": in_stock,
            "is_premium": is_premium,
            "sort": sort,
            "page": page,
            "page_size": page_size,
            **raw,
        }
        return self.client.get("/products", params=params)

    def get(self, product_id: ProductId) -> httpx.Response:
        """By UUID or by stable reference (`product_007`) — the backend accepts both."""
        return self.client.get(f"/products/{product_id}")


class CartApi(Resource):
    def get(self, shipping_method: str | None = None) -> httpx.Response:
        return self.client.get("/cart", params={"shipping_method": shipping_method})

    def add_item(self, product_id: ProductId, quantity: int | None = 1, **extra: Any) -> httpx.Response:
        body: dict[str, Any] = {"product_id": str(product_id), **extra}
        if quantity is not None:
            body["quantity"] = quantity
        return self.client.post("/cart/items", json=body)

    def update_item(self, product_id: ProductId, quantity: Any) -> httpx.Response:
        return self.client.patch(f"/cart/items/{product_id}", json={"quantity": quantity})

    def remove_item(self, product_id: ProductId) -> httpx.Response:
        return self.client.delete(f"/cart/items/{product_id}")

    def clear(self) -> httpx.Response:
        return self.client.delete("/cart")

    def apply_coupon(self, code: str, **extra: Any) -> httpx.Response:
        return self.client.post("/cart/coupon", json={"code": code, **extra})

    def remove_coupon(self) -> httpx.Response:
        return self.client.delete("/cart/coupon")


class WishlistApi(Resource):
    def get(self) -> httpx.Response:
        return self.client.get("/wishlist")

    def add(self, product_id: ProductId) -> httpx.Response:
        return self.client.post(f"/wishlist/items/{product_id}")

    def remove(self, product_id: ProductId) -> httpx.Response:
        return self.client.delete(f"/wishlist/items/{product_id}")


class CheckoutApi(Resource):
    def validate(self, body: dict[str, Any]) -> httpx.Response:
        return self.client.post("/checkout/validate", json=body)


class OrdersApi(Resource):
    def place(self, body: dict[str, Any]) -> httpx.Response:
        return self.client.post("/orders", json=body)

    def list(
        self,
        *,
        status: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> httpx.Response:
        return self.client.get("/orders", params={"status": status, "page": page, "page_size": page_size})

    def get(self, order_id: uuid.UUID | str) -> httpx.Response:
        return self.client.get(f"/orders/{order_id}")

    def cancel(self, order_id: uuid.UUID | str) -> httpx.Response:
        return self.client.post(f"/orders/{order_id}/cancel")


class AdminCatalogApi(Resource):
    def create_category(self, body: dict[str, Any]) -> httpx.Response:
        return self.client.post("/admin/categories", json=body)

    def update_category(self, category_id: uuid.UUID | str, body: dict[str, Any]) -> httpx.Response:
        return self.client.patch(f"/admin/categories/{category_id}", json=body)

    def delete_category(self, category_id: uuid.UUID | str) -> httpx.Response:
        return self.client.delete(f"/admin/categories/{category_id}")

    def create_product(self, body: dict[str, Any]) -> httpx.Response:
        return self.client.post("/admin/products", json=body)

    def update_product(self, product_id: uuid.UUID | str, body: dict[str, Any]) -> httpx.Response:
        return self.client.patch(f"/admin/products/{product_id}", json=body)

    def delete_product(self, product_id: uuid.UUID | str) -> httpx.Response:
        return self.client.delete(f"/admin/products/{product_id}")


class TestDataApi(Resource):
    """The backend's automation endpoints. Mounted only when it runs as `development`/`testing`."""

    __test__ = False  # not a pytest test class, despite the name

    def error(self, status: int) -> httpx.Response:
        return self.client.get(f"/test-data/error/{status}")

    def delay(self, seconds: int, *, timeout: float | None = None) -> httpx.Response:
        return self.client.request("GET", f"/test-data/delay/{seconds}", timeout=timeout)

    def empty_products(self) -> httpx.Response:
        return self.client.get("/test-data/empty-products")

    def malformed(self) -> httpx.Response:
        return self.client.get("/test-data/malformed")

    def reset(self) -> httpx.Response:
        return self.client.post("/test-data/reset")

    def seed(self) -> httpx.Response:
        return self.client.post("/test-data/seed")

    def cleanup(self) -> httpx.Response:
        return self.client.post("/test-data/cleanup")


class ShopApi:
    """Every resource client, bound to one `HttpClient` (and so to one identity)."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self.health = HealthApi(client)
        self.auth = AuthApi(client)
        self.categories = CategoriesApi(client)
        self.products = ProductsApi(client)
        self.cart = CartApi(client)
        self.wishlist = WishlistApi(client)
        self.checkout = CheckoutApi(client)
        self.orders = OrdersApi(client)
        self.admin = AdminCatalogApi(client)
        self.test_data = TestDataApi(client)

    def as_token(self, token: str | None) -> ShopApi:
        return ShopApi(self.client.with_token(token))

    def anonymous(self) -> ShopApi:
        return ShopApi(self.client.anonymous())
