"""The framework's pytest plugin: options, configuration, fixtures, data reset and run metadata.

Registered through the `pytest11` entry point, so every test directory gets it without conftest
imports. Configuration is validated in `pytest_configure` — an unsafe target aborts the run before a
single test is collected.
"""

from __future__ import annotations

import json
import logging
import platform
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import allure
import pytest
from pydantic import ValidationError

from api_tests import __version__
from api_tests.client import ApiTransportError, HttpClient
from api_tests.config import Account, Settings, TargetEnvironment, load_settings
from api_tests.fixtures.accounts import SignedIn, register, sign_in
from api_tests.redaction import Redactor
from api_tests.reporting import AllureReporter, write_categories, write_environment, write_executor
from api_tests.resources import ShopApi
from api_tests.resources.models import Product, ProductPage, ResetSummary

SETTINGS_KEY = pytest.StashKey[Settings]()
REDACTOR_KEY = pytest.StashKey[Redactor]()
BACKEND_KEY = pytest.StashKey["BackendInfo"]()

_BACKEND_MARKERS = ("functional", "smoke", "pact_provider")


# --------------------------------------------------------------------------------- options


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mtp-api-tests", "Mobile Test Playground API tests")
    group.addoption("--base-url", dest="mtp_base_url", help="Backend base URL (MTP_BASE_URL).")
    group.addoption(
        "--target-env",
        dest="mtp_environment",
        choices=[env.value for env in TargetEnvironment],
        help="local | ci | staging (MTP_ENVIRONMENT).",
    )
    group.addoption(
        "--reset-mode",
        dest="mtp_reset_mode",
        choices=["session", "none"],
        help="Reset backend data at session start (MTP_RESET_MODE).",
    )
    group.addoption("--run-id", dest="mtp_run_id", help="Run id for correlation and artifacts (MTP_RUN_ID).")


class _RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.text(record.getMessage())
        record.args = None
        return True


def pytest_configure(config: pytest.Config) -> None:
    try:
        settings = load_settings(
            base_url=config.getoption("mtp_base_url"),
            environment=config.getoption("mtp_environment"),
            reset_mode=config.getoption("mtp_reset_mode"),
            run_id=config.getoption("mtp_run_id"),
        )
    except ValidationError as error:
        raise pytest.UsageError(f"Invalid MTP_* configuration:\n{error}") from None
    redactor = Redactor(settings.secret_values())
    config.stash[SETTINGS_KEY] = settings
    config.stash[REDACTOR_KEY] = redactor
    logging.getLogger("api_tests").addFilter(_RedactingFilter(redactor))
    for name in ("api_tests.http", "api_tests.pact"):
        logging.getLogger(name).addFilter(_RedactingFilter(redactor))
    # httpx logs every full URL at INFO, unredacted and query string included. Our client logs its
    # own redacted line per exchange instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def pytest_report_header(config: pytest.Config) -> list[str]:
    # Almost every confusing run of an API suite turns out to have been pointed somewhere other than
    # the reader assumed, so the target is the first thing printed.
    settings = config.stash[SETTINGS_KEY]
    return [
        f"mtp target: {settings.api_url} (environment={settings.environment.value}, "
        f"reset_mode={settings.reset_mode.value if settings.reset_mode else 'none'})",
        f"mtp run id: {settings.run_id}",
    ]


# --------------------------------------------------------------------------------- core fixtures


@pytest.fixture(scope="session")
def settings(pytestconfig: pytest.Config) -> Settings:
    return pytestconfig.stash[SETTINGS_KEY]


@pytest.fixture(scope="session")
def redactor(pytestconfig: pytest.Config) -> Redactor:
    return pytestconfig.stash[REDACTOR_KEY]


@pytest.fixture(scope="session")
def reporter(settings: Settings, redactor: Redactor) -> AllureReporter:
    return AllureReporter(redactor, max_bytes=settings.attachment_max_bytes)


@pytest.fixture(scope="session")
def http_client(settings: Settings, redactor: Redactor, reporter: AllureReporter) -> Iterator[HttpClient]:
    client = HttpClient(
        settings.api_url,
        run_id=settings.run_id,
        redactor=redactor,
        reporter=reporter,
        timeout_seconds=settings.timeout_seconds,
        retries=settings.retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
    )
    yield client
    client.close()


@dataclass
class BackendInfo:
    api_url: str
    version: str
    automation_endpoints: bool
    details: dict[str, Any] = field(default_factory=dict)


@pytest.fixture(scope="session")
def backend(pytestconfig: pytest.Config, settings: Settings, http_client: HttpClient) -> BackendInfo:
    """The backend is up, and what it is. Fails every dependent test with one clear reason if not."""
    api = ShopApi(http_client)
    try:
        liveness = api.health.liveness()
    except ApiTransportError as error:
        pytest.fail(
            f"Backend not reachable at {settings.api_url}: {error.cause!r}. Start it (see "
            "docs/configuration.md) or point MTP_BASE_URL at a running test instance.",
            pytrace=False,
        )
    if liveness.status_code != 200:
        pytest.fail(f"Backend liveness answered {liveness.status_code} at {settings.api_url}", pytrace=False)

    # The backend has no version endpoint. `/openapi.json` carries `info.version` but is served only in
    # `development`; anywhere else the version must come from configuration (CI passes the commit).
    version = settings.backend_version or "unknown"
    openapi = http_client.anonymous().request("GET", f"{settings.base_url}/openapi.json")
    if settings.backend_version is None and openapi.status_code == 200:
        version = str(openapi.json().get("info", {}).get("version", "unknown"))
    automation = api.test_data.empty_products().status_code == 200
    info = BackendInfo(settings.api_url, version, automation)
    pytestconfig.stash[BACKEND_KEY] = info
    return info


@pytest.fixture(scope="session")
def api(backend: BackendInfo, http_client: HttpClient) -> ShopApi:
    """Every resource client, anonymous."""
    return ShopApi(http_client)


# --------------------------------------------------------------------------------- test data


@pytest.fixture(scope="session")
def session_baseline(settings: Settings, backend: BackendInfo, api: ShopApi, reporter: AllureReporter) -> None:
    """`POST /test-data/reset` once, before the first backend test. A no-op with reset mode `none`."""
    if not settings.resets_enabled:
        return
    if not backend.automation_endpoints:
        pytest.fail(
            "MTP_RESET_MODE=session, but the backend does not expose /test-data (it only does in "
            "ENVIRONMENT=development or testing). Use MTP_RESET_MODE=none for a shared environment.",
            pytrace=False,
        )
    _reset(api, reporter)


@pytest.fixture
def fresh_baseline(settings: Settings, backend: BackendInfo, api: ShopApi, reporter: AllureReporter) -> Iterator[None]:
    """Reset before *and after* this test. For tests that consume stock or assert order numbers.

    The reset afterwards is what keeps every other test order-independent: without it, a test that
    reads `stock` would pass or fail depending on whether an order test happened to run first.
    """
    if not settings.resets_enabled or not backend.automation_endpoints:
        pytest.skip("destructive test: needs MTP_RESET_MODE=session and the backend's /test-data endpoints")
    _reset(api, reporter)
    yield
    _reset(api, reporter)


def _reset(api: ShopApi, reporter: AllureReporter) -> None:
    with allure.step("Reset backend data to the documented baseline"):
        response = api.test_data.reset()
        if response.status_code != 200:
            pytest.fail(f"POST /test-data/reset answered {response.status_code}", pytrace=False)
        summary = ResetSummary.model_validate(response.json())
        if (summary.seeded.products, summary.seeded.users) != (30, 4):
            pytest.fail(f"Reset produced an unexpected baseline: {summary.seeded}", pytrace=False)


@pytest.fixture(autouse=True)
def _backend_state(request: pytest.FixtureRequest) -> None:
    """Route each backend test to the right baseline, by marker."""
    if request.node.get_closest_marker("destructive"):
        request.getfixturevalue("fresh_baseline")
    elif any(request.node.get_closest_marker(name) for name in _BACKEND_MARKERS):
        request.getfixturevalue("session_baseline")


# --------------------------------------------------------------------------------- identities


@pytest.fixture(scope="session")
def customer(settings: Settings, api: ShopApi, session_baseline: None) -> SignedIn:
    """The seeded `customer` account, signed in once per session. Read-only use only."""
    return sign_in(api, settings.account("customer"))


@pytest.fixture(scope="session")
def premium_customer(settings: Settings, api: ShopApi, session_baseline: None) -> SignedIn:
    return sign_in(api, settings.account("premium"))


@pytest.fixture(scope="session")
def admin(settings: Settings, api: ShopApi, session_baseline: None) -> SignedIn:
    return sign_in(api, settings.account("admin"))


@pytest.fixture(scope="session")
def locked_account(settings: Settings) -> Account:
    """Credentials only — this account cannot sign in, which is the point."""
    return settings.account("locked")


@pytest.fixture
def new_shopper(settings: Settings, api: ShopApi, request: pytest.FixtureRequest) -> Callable[[str], SignedIn]:
    """A factory for freshly registered customers, for tests that need more than one stranger."""
    counter = iter(range(1, 1000))

    def make(label: str = "shopper") -> SignedIn:
        unique = f"{request.node.nodeid}:{label}:{next(counter)}"
        return register(api, run_id=settings.run_id, label=unique, domain=settings.registration_domain)

    return make


@pytest.fixture
def shopper(new_shopper: Callable[[str], SignedIn]) -> SignedIn:
    """A brand-new customer with an empty cart, wishlist and order history.

    **Anything that writes uses this**, never a seeded account: two tests sharing one cart is a suite
    whose failures depend on execution order.
    """
    return new_shopper("shopper")


# --------------------------------------------------------------------------------- catalogue


class Catalog:
    """Every product as the admin sees it, keyed by stable reference.

    Product UUIDs are generated by PostgreSQL on first seed and then preserved by the seed's upserts,
    so they are stable for one backend instance but not across instances. Tests address products by
    `reference` and look the UUID up here.
    """

    def __init__(self, products: list[Product]) -> None:
        self._by_reference = {product.reference: product for product in products}

    def __getitem__(self, reference: str) -> Product:
        return self._by_reference[reference]

    def id(self, reference: str) -> str:
        return str(self._by_reference[reference].id)

    def __len__(self) -> int:
        return len(self._by_reference)


@pytest.fixture(scope="session")
def catalog(admin: SignedIn) -> Catalog:
    products: list[Product] = []
    page = 1
    while True:
        response = admin.api.products.list(page=page, page_size=100)
        body = ProductPage.model_validate(response.json())
        products.extend(body.items)
        if page >= body.total_pages:
            return Catalog(products)
        page += 1


@pytest.fixture
def admin_cleanup(admin: SignedIn) -> Iterator[dict[str, list[str]]]:
    """Records products/categories an admin test creates, and deletes them afterwards.

    `reset` re-seeds the documented catalogue but never deletes extra rows, so anything a test adds
    through the admin API has to be removed by the test that added it.
    """
    created: dict[str, list[str]] = {"products": [], "categories": []}
    yield created
    with allure.step("Clean up admin-created catalogue entries"):
        for product_id in created["products"]:
            admin.api.admin.delete_product(product_id)
        for category_id in created["categories"]:
            admin.api.admin.delete_category(category_id)


# --------------------------------------------------------------------------------- run metadata


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if SETTINGS_KEY not in config.stash:
        return
    settings = config.stash[SETTINGS_KEY]
    backend = config.stash.get(BACKEND_KEY, None)
    properties = settings.describe()
    properties.update(
        {
            "target.backend_version": backend.version if backend else properties["target.backend_version"],
            "target.automation_endpoints": str(backend.automation_endpoints) if backend else "not checked",
            "framework.version": __version__,
            "framework.python": platform.python_version(),
        }
    )
    allure_dir = getattr(config.option, "allure_report_dir", None)
    if allure_dir:
        results = Path(allure_dir)
        write_environment(results, properties)
        write_executor(
            results,
            run_id=settings.run_id,
            build_url=settings.build_url,
            ci=settings.environment is TargetEnvironment.CI,
        )
        write_categories(results)

    manifest = {"exit_status": int(exitstatus), "allure_results": allure_dir, **properties}
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    (settings.run_dir / "run.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
