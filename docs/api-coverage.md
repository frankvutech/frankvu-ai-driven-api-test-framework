# API coverage

Every endpoint below was confirmed from the backend source (`backend/app/api/v1/endpoints/*.py`) and
the live `GET /openapi.json` of ShopTest API 0.1.0 (commit `2815908`). No test calls an endpoint the
backend does not implement.

Counts are pytest test items (parametrised cases counted individually): **310 functional**,
19 consumer interactions, 1 provider verification, 109 framework unit tests.

## Endpoints and tests

| Area | Endpoints | Module | Tests |
|---|---|---|---|
| Health | `GET /health`, `GET /health/database` | `test_health.py` | 7 |
| Authentication | `POST /auth/register`, `/login`, `/refresh`, `/logout`, `GET /auth/me` | `test_auth.py` | 37 |
| Categories | `GET /categories`, `GET /categories/{id}` | `test_categories.py` | 5 |
| Products | `GET /products`, `GET /products/{id or reference}` | `test_products.py` | 25 |
| Search / filter / sort / paging | `GET /products?q,category,min_price,max_price,rating,in_stock,is_premium,sort,page,page_size` | `test_product_search.py` | 50 |
| Cart and coupons | `GET/DELETE /cart`, `POST /cart/items`, `PATCH/DELETE /cart/items/{id}`, `POST/DELETE /cart/coupon` | `test_cart.py` | 56 |
| Wishlist | `GET /wishlist`, `POST/DELETE /wishlist/items/{id}` | `test_wishlist.py` | 15 |
| Checkout | `POST /checkout/validate` | `test_checkout.py` | 25 |
| Orders | `POST /orders`, `GET /orders`, `GET /orders/{id}`, `POST /orders/{id}/cancel` | `test_orders.py` | 27 |
| Admin authorisation and catalogue | `POST /admin/categories`, `PATCH/DELETE /admin/categories/{id}`, `POST /admin/products`, `PATCH/DELETE /admin/products/{id}` | `test_admin.py` | 43 |
| Error envelope, deterministic failures | `GET /test-data/error/{400..500}`, `/delay/{s}`, `/empty-products`, `/malformed` | `test_errors.py` | 20 |

`POST /test-data/reset|seed|cleanup` are used by fixtures and provider states rather than tested as
features.

### What is covered, per behaviour

- **Success and schema**: every success response is validated against a strict model.
- **Required/optional fields**: missing, empty, blank, over-long and wrongly typed fields → `422 error_validation` naming the field; optional fields (`quantity`, `phone`) default correctly.
- **Authentication**: missing, malformed, wrong-scheme, tampered, refresh-as-access tokens → 401 `error_invalid_token` with `WWW-Authenticate: Bearer` on every protected route; wrong password and unknown email indistinguishable; locked account only revealed with the right password; refresh rotation and reuse detection; logout semantics.
- **Authorisation**: full matrix over the six admin routes — anonymous 401, customer 403, **premium 403**; another user's order is 404 (not 403); premium is not a privilege level.
- **Visibility**: premium and inactive products hidden (404) per viewer, counts per viewer.
- **Not found**: products, categories, orders, cart lines, wishlist items, coupons.
- **Empty results**: empty search, unknown category, empty cart/wishlist/history, `total_pages: 0`.
- **Pagination boundaries**: `page_size` 1 and 100 accepted, 0/101 refused, page 0/negative refused, partial last page, past-the-end page, every product exactly once under every sort.
- **Conflicts**: duplicate registration (case-insensitive), duplicate wishlist item, coupon already applied, stock, unavailable product, empty cart, invalid order transition, duplicate category/reference, non-empty category, product on an order.
- **Pricing**: effective prices, floored percentage discounts, shipping per method and the €75 threshold on the discounted subtotal, coupon re-validation when the basket shrinks, client-sent totals ignored.
- **Stable error schema**: every error validates against `{"error": {"code", "message", "details"}}`; deterministic errors byte-for-byte repeatable; validation errors never echo input; no stack traces.
- **Timeouts and malformed bodies**: client timeout raised (and not retried), malformed JSON surfaced as `MalformedResponseError`.

## Findings and gaps

### Backend defect found

**`PATCH /admin/products/{id}` silently drops constraint-violating updates.** Setting a price at or
below the existing discount answers **200** with the change echoed, but nothing is persisted: the
`ck_products_discount_below_price` constraint fails at commit, which happens after the response is
sent. The schema docstring promises a 422. Pinned as a strict `xfail`
(`test_patch_that_breaks_the_price_constraint_is_422`), so the test turns into a failure — prompting
removal of the marker — the day the backend is fixed.

### Backend behaviour worth knowing

- `POST /orders` with an **incomplete address** answers `422 error_payment_invalid`: every problem that
  is not cart/stock/availability is mapped to that code. The precise reason is in
  `details.problems`. Tests pin the observed behaviour.
- Coupon **rejection precedence** (expired → premium-only → minimum) cannot be exercised end to end: no
  seeded coupon violates two rules at once. Each rule is tested on its own.
- Access tokens remain valid after logout until they expire (documented design; pinned by a test).

### Not implemented by the backend (no tests, by design)

| Expected by the brief / app docs | Status in the backend |
|---|---|
| User profile update, change password | no endpoint — only `GET /auth/me` |
| Forgot / reset password (`123456` code in the app docs) | no endpoint |
| Product reviews (`GET /products/{id}/reviews`, used by the Android client) | no endpoint |
| Save-for-later cart lines | no endpoint |
| Admin order status changes, user role/lock management | no endpoint (only cancel) |
| Version / build-info endpoint | none; `info.version` only via `/openapi.json` in `development` |

### Not testable without backend changes

- **Expired access token** (`error_token_expired`): needs either a 15-minute wait or the signing key.
  Deliberately not done by minting tokens with the development secret.
- **Database outage** (`503 error_database_unavailable`): needs the database stopped mid-run; out of
  scope for a black-box suite that shares one database.
- **Shipped/delivered order transitions**: no endpoint moves an order past `pending`, so "cannot
  cancel a shipped order" is unreachable.
