# Troubleshooting

Problems this framework actually hit while being built, and their fixes.

### `Invalid MTP_* configuration: Refusing to target '...'`

The target guard. Use a local/private host, add it to `MTP_ALLOWED_HOSTS` if it is a dedicated test
environment, or — deliberately — `MTP_ALLOW_UNSAFE_TARGET=true`.

### Every backend test fails with `Backend not reachable at http://localhost:8000/api/v1`

Start it: `BACKEND_DIR=../shoptest/backend scripts/backend.sh up`. If Docker is not running, start
Docker Desktop first. Ports 8000 (API) and 5433 (PostgreSQL) must be free.

### `MTP_RESET_MODE=session, but the backend does not expose /test-data`

The backend runs as `staging`/`production`, where the automation router is not mounted. For a local
stack use `BACKEND_ENVIRONMENT=testing` (or `development`); for a shared environment use
`MTP_RESET_MODE=none` (destructive tests and provider verification then cannot run).

### Destructive tests are skipped

`fresh_baseline` needs `MTP_RESET_MODE=session` *and* the `/test-data` endpoints. The skip reason says
which is missing.

### Registration fails with `special-use or reserved name`

The backend's email validation accepts `.local` but rejects other reserved suffixes such as `.test`.
`MTP_REGISTRATION_DOMAIN` defaults to `mtp-api-tests.local` for that reason.

### `Pact verification failed ... No pact files for provider ...`

Run `pytest -m pact_consumer` first (or `make pact`, which runs both), or point
`MTP_PACT_EXTERNAL_DIR` at supplied contracts, or verify from the Broker (`MTP_PACT_SOURCE=broker`).
"Nothing to verify" is a failure on purpose.

### A provider-state request answers 500 / "Unknown provider state"

The contract uses a state name that `api_tests/pact/provider_states.py` does not register, or setup
failed (e.g. the backend's seed changed). The state-change log is attached to the Allure result.

### Provider states cannot inject values with a function handler

pact-python 4.1's in-process function state handlers discard their return value, so `${productId}`
would never be filled in. The framework uses a URL state handler (`StateChangeServer`) whose JSON
response is what the verifier injects.

### mypy or isort treat `pact` as a local package

A test directory named `pact/` on the import path shadows the library; that is why the contract tests
live in `tests/contracts/`. Ruff's isort is also pinned with `known-third-party = ["pact"]`.

### `Illegal header value b'Bearer '`

h11 (under httpx) rejects header values with trailing whitespace; send `Bearer` without a credential
instead when testing a malformed header.

### Unit tests fail only on my machine

They must not read your `MTP_*` variables or `.env`; `tests/unit/conftest.py` clears them and changes
into a temporary directory. If a new unit test reads configuration some other way, route it through
`Settings`.

### Allure report missing, but tests ran

`run-tests.sh` prints `warning: Allure CLI unavailable` or `allure generate failed`; the raw results
are still in `artifacts/runs/<run id>/allure-results`. Check `java -version` (Java 8+), or set
`MTP_ALLURE_BIN` to an existing Allure 2 CLI. The script's exit status is pytest's either way.

### The local Pact Broker container is "unhealthy"

With basic auth enabled the heartbeat needs `PACT_BROKER_PUBLIC_HEARTBEAT=true`, and inside the
container `localhost` resolves to IPv6 first — the healthcheck uses `127.0.0.1`. Both are set in
`docker-compose.broker.yml`.

### `PATCH /admin/products/{id}` answers 200 but nothing changed

A known backend defect (see [api-coverage.md](api-coverage.md#backend-defect-found)): a constraint
violation at commit time is reported as success.
