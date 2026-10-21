# CI

`.github/workflows/api-tests.yml` — GitHub Actions, `ubuntu-24.04`, on push to `main`, pull requests
and manual dispatch. Actions are pinned to commit SHAs; uv 0.11.31, Python 3.12, Temurin 21 for the
Allure CLI.

> **Status.** The `quality` job has **passed on GitHub Actions** (PR #1, run 39643703524). The `api`
> job has **not yet run its tests on GitHub**: it stops at its first step, by design, until
> `vars.BACKEND_REPOSITORY` (or `vars.MTP_EXTERNAL_BASE_URL`) is set. Every command it runs was
> executed locally in the same order against a backend started the same way (`scripts/backend.sh up`
> with `ENVIRONMENT=testing`), and the workflow passes `actionlint 1.7.12` (including shellcheck of
> every `run:` block).

No mobile build, emulator, simulator or UI-test step exists.

## Jobs

### `quality`

`uv sync --frozen` → `uv lock --check` → Ruff lint + format check → `mypy --strict` → unit tests →
gitleaks.

### `api` (needs `quality`)

1. Check out this repository.
2. **Backend:** if `vars.BACKEND_REPOSITORY` is set, check it out (`vars.BACKEND_REF`, default `main`;
   `secrets.BACKEND_REPO_TOKEN` if private) and run `scripts/backend.sh up` — the backend's own
   `docker-compose.yml`, `ENVIRONMENT=testing`, `alembic upgrade head`, wait for
   `/health/database`. `MTP_BACKEND_VERSION` becomes the backend commit SHA. Otherwise, if
   `vars.MTP_EXTERNAL_BASE_URL` is set, connect to that **dedicated** CI backend and wait for it
   (its host must be in `vars.MTP_ALLOWED_HOSTS`). With neither, the job fails immediately.
3. Python 3.12 + pinned dependencies.
4. `pytest -m functional` (session reset seeds the deterministic baseline).
5. `pytest -m pact_consumer`.
6. If a Broker is configured (and secrets are available — not on fork PRs): publish the contract with
   the commit SHA, branch and build URL.
7. `pytest -m pact_provider` — from the Broker with results published when step 6 succeeded,
   otherwise from the contract generated in step 5.
8. **Always:** Java, restore Allure history (cache), `allure generate`, save history, write a summary
   table to the job summary, collect backend logs.
9. **Always:** upload artifacts — `allure-results-*` (raw), `allure-report-*` (HTML),
   `test-artifacts-*` (pact files, run manifests, backend log).
10. Stop the backend; then fail the job if any test step failed.

Test steps use `continue-on-error` so the report is always produced; the final step restores the
failure. Results share one run-specific directory (`artifacts/runs/CI-<run>-<attempt>/allure-results`)
and only the first pytest step cleans it.

### `can-i-deploy` (needs `api`)

On `main` only, when `vars.PACT_BROKER_URL` and `vars.PACT_DEPLOY_ENVIRONMENT` are set: asks the
Broker whether this consumer version may be deployed, retrying while verification is pending.

## Configuration

| Name | Kind | Needed for |
|---|---|---|
| `BACKEND_REPOSITORY` | variable | starting the backend in the job (`owner/repo` containing `backend/`) |
| `BACKEND_REF` | variable | backend ref (default `main`) |
| `BACKEND_REPO_TOKEN` | secret | private backend repository |
| `MTP_EXTERNAL_BASE_URL`, `MTP_ALLOWED_HOSTS` | variables | connecting to a dedicated CI backend instead |
| `MTP_{CUSTOMER,PREMIUM,ADMIN,LOCKED}_PASSWORD` | secrets | non-default test credentials |
| `PACT_BROKER_URL`, `PACT_BROKER_USERNAME` | variables | Broker |
| `PACT_BROKER_TOKEN` or `PACT_BROKER_PASSWORD` | secrets | Broker auth |
| `PACT_DEPLOY_ENVIRONMENT`, `PACT_CONSUMER_NAME` | variables | can-i-deploy gate |

Never configure production credentials or a production URL; the target guard refuses unknown hosts.

## Reproducing the CI job locally

```bash
BACKEND_DIR=../shoptest/backend BACKEND_ENVIRONMENT=testing scripts/backend.sh up
export MTP_ENVIRONMENT=ci MTP_RUN_ID=CI-LOCAL-1 MTP_BACKEND_VERSION=$(BACKEND_DIR=../shoptest/backend scripts/backend.sh version)
RESULTS=artifacts/runs/$MTP_RUN_ID/allure-results
uv run --frozen pytest -m functional    --alluredir $RESULTS --clean-alluredir
uv run --frozen pytest -m pact_consumer --alluredir $RESULTS
uv run --frozen pytest -m pact_provider --alluredir $RESULTS
"$(scripts/install-allure.sh)" generate $RESULTS --clean -o artifacts/runs/$MTP_RUN_ID/allure-report
```
