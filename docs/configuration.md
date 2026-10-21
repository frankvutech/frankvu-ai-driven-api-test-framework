# Configuration

All configuration is `MTP_*` environment variables (or a gitignored `.env`; see `.env.example`), read
once into `api_tests.config.Settings`. A handful can also be given as pytest options, which win:

| pytest option | Variable |
|---|---|
| `--base-url` | `MTP_BASE_URL` |
| `--target-env local\|ci\|staging` | `MTP_ENVIRONMENT` |
| `--reset-mode session\|none` | `MTP_RESET_MODE` |
| `--run-id` | `MTP_RUN_ID` |

Invalid configuration aborts the run before collection (`pytest.UsageError`). The first line of every
run prints the target: `mtp target: http://localhost:8000/api/v1 (environment=local, reset_mode=session)`.

## Variables

| Variable | Default | Notes |
|---|---|---|
| `MTP_BASE_URL` | `http://localhost:8000` | backend origin |
| `MTP_API_PREFIX` | `/api/v1` | |
| `MTP_ENVIRONMENT` | `local` | `local`, `ci`, `staging`; anything else (e.g. `production`) is rejected |
| `MTP_ALLOWED_HOSTS` | — | comma-separated extra hosts considered non-production |
| `MTP_ALLOW_UNSAFE_TARGET` | `false` | the only way past the target guard |
| `MTP_TIMEOUT_SECONDS` | `10` | per request, 0–120 |
| `MTP_RETRIES` | `2` | transport retries; GET/HEAD/OPTIONS connect failures only |
| `MTP_RETRY_BACKOFF_SECONDS` | `0.25` | linear, no jitter |
| `MTP_RESET_MODE` | `session` (local/ci), `none` (staging) | `session` is refused for staging |
| `MTP_{CUSTOMER,PREMIUM,ADMIN,LOCKED}_EMAIL/_PASSWORD` | documented seed accounts (local/ci only) | required for staging |
| `MTP_REGISTRATION_DOMAIN` | `mtp-api-tests.local` | for accounts registered during a run |
| `MTP_RUN_ID` | `API-<UTC>-<pid>` | sanitised to `[A-Za-z0-9._:-]`, ≤ 64 chars |
| `MTP_BACKEND_VERSION`, `MTP_COMMIT_SHA`, `MTP_BRANCH`, `MTP_BUILD_URL` | — | report and Broker metadata |
| `MTP_ARTIFACTS_DIR` | `artifacts` | |
| `MTP_ATTACHMENT_MAX_BYTES` | `65536` | 1 KiB – 5 MiB |
| `MTP_PACT_DIR` | `pacts` | generated contracts |
| `MTP_PACT_EXTERNAL_DIR` | — | contracts supplied by the mobile repositories |
| `MTP_PACT_CONSUMER_NAME` / `MTP_PACT_PROVIDER_NAME` | `MobileTestPlayground-Mobile-API` / `MobileTestPlayground-Backend-API` | |
| `MTP_PACT_SOURCE` | `local` | `broker` needs `MTP_PACT_BROKER_URL` |
| `MTP_PACT_BROKER_URL`, `_TOKEN`, `_USERNAME`, `_PASSWORD` | — | token **or** basic auth |
| `MTP_PACT_PUBLISH_RESULTS` | `false` | needs a Broker and `MTP_BACKEND_VERSION` |

Blank values are treated as unset, so a CI secret that is not defined (rendered as `""`) does not
become an empty token.

## The target guard

The framework registers accounts, fills carts, places orders and — locally and in CI — resets the
database. So the base URL must be recognisably non-production:

- `localhost`, `host.docker.internal`, loopback and private IP addresses;
- names ending `.local`, `.localhost`, `.test`, `.internal`, `.example`;
- single-label names (Compose/Kubernetes service names such as `backend`);
- anything listed in `MTP_ALLOWED_HOSTS`.

Everything else is refused unless `MTP_ALLOW_UNSAFE_TARGET=true`.

## Environments

| | local | ci | staging |
|---|---|---|---|
| Backend | the ShopTest Compose stack | started per job, or a dedicated CI instance | a shared deployed instance |
| `/test-data` endpoints | yes (`development`/`testing`) | yes (`testing`) | **no** (not mounted) |
| Reset | session + destructive tests | session + destructive tests | none; destructive tests skip |
| Credentials | seed defaults | seed defaults or secrets | must be configured |
| Pact provider verification | yes | yes | **no** — needs provider states |

## Running the backend under test

The backend is the existing ShopTest backend; this repository never builds or edits it. It ships its
own Compose stack, which `scripts/backend.sh` drives:

```bash
BACKEND_DIR=../shoptest/backend scripts/backend.sh up   # PostgreSQL + API, alembic upgrade head, wait
scripts/backend.sh reset                                # POST /test-data/reset
scripts/backend.sh down                                 # stop and drop the volume
```

`BACKEND_ENVIRONMENT` defaults to `testing` (automation endpoints on, interactive docs off); only
`development` and `testing` are accepted. Requirements: Docker with Compose v2; ports 8000 and 5433
free on the host.

## Python and tooling

- **Python 3.12** (the backend's version; CI uses it). 3.13 is allowed by `requires-python` but not
  exercised. pact-python 4.x needs ≥ 3.11.
- Dependencies are pinned exactly in `pyproject.toml` and locked in `uv.lock`; install with
  `uv sync --frozen` (uv 0.11.31 locally and in CI).
- Allure CLI: Java 8+.
