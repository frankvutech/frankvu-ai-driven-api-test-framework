# Architecture

An **API-only** test framework for the existing ShopTest backend — the REST API behind the Mobile Test
Playground app. It builds no app, runs no UI test, needs no emulator or simulator, and never modifies
the backend: it talks to a running backend over HTTP and nothing else.

```
pytest CLI  (one plugin: api_tests.fixtures.plugin)
   ├── tests/functional/        ──────────>  existing backend API (real HTTP, httpx)
   ├── tests/contracts/consumer/ ─────────>  Pact mock provider (pact-python FFI)
   │        └── writes pacts/<consumer>-<provider>.json
   └── tests/contracts/provider/ ─────────>  Pact verifier ──> existing backend (controlled instance)
                     │                             ▲
                     └── state-change server ──────┘  (provider states via /test-data/reset + API)
                                   ↓
              artifacts/runs/<run id>/allure-results  ──>  allure generate  ──>  HTML report
```

## Layout

```
src/api_tests/
├── config/       Settings: MTP_* env vars, .env, pytest options; default-deny target guard
├── client/       HttpClient around httpx: base URL, timeout, transport retries, auth, correlation ids,
│                 redacted logging and one Allure attachment per exchange
├── resources/    one client per backend resource (AuthApi, ProductsApi, CartApi, ...) + strict
│                 Pydantic response models
├── fixtures/     the pytest plugin (fixtures, reset strategy, run metadata), sign-in helpers, and the
│                 documented seed data tests compute expectations from
├── redaction/    the Redactor every log line, attachment and verifier log passes through
├── reporting/    the only module that calls allure.attach; environment/executor/categories files
└── pact/         consumer setup + provenance, provider-state matchers, provider-state registry,
                  state-change server, verifier wrapper, Pact Broker client + CLI
tests/
├── functional/   one module per resource; real backend
├── contracts/    consumer/ (mock provider) and provider/ (real backend)
├── support/      assertion helpers and Pact shapes shared by tests
└── unit/         the framework's own tests; httpx.MockTransport only, no backend
scripts/          run-tests.sh (results dir + report + exit code), install-allure.sh, backend.sh
```

`tests/contracts/` rather than `tests/pact/`: with `tests/` on the import path, a directory named
`pact` shadows the `pact` library for mypy and for isort's first/third-party classification.

## Decisions

**No generic framework.** One HTTP client, one small client class per resource, plain pytest fixtures.
Abstractions exist only where they remove duplication or make something testable without a backend
(`HttpClient` accepts an `httpx` transport; the state server takes a `ShopApi`).

**The client never raises for a status.** Negative tests are half the suite; they need the response.
Only transport failures raise (`ApiTransportError`, `ApiTimeoutError`).

**Authentication is a property of a client.** `with_token()` returns a new client sharing the pool;
no test can leak a token into another by mutation.

**Response models are written independently of the backend's.** Importing the backend's Pydantic
schemas would prove only that the server agrees with itself. Models are `extra="forbid"` (an
accidentally exposed column fails the check) and carry the invariants clients rely on
(`effective_price_cents`, `in_stock`, totals add up, paging envelope consistent).

**Expectations come from the seed table.** `fixtures/data.py` restates the backend's documented,
deterministic dataset and pricing rules; filter, sort and total assertions are computed from it, so
one table drives every expectation and `test_seed_table_is_what_the_admin_sees` fails first if the
seed drifts.

**Consumer tests exercise the real client code.** Each consumer test runs the framework's resource
clients against the Pact mock provider, which fails if the emitted request does not match the
interaction. The contract records what the client sends, not a hand-written JSON file.

**Provider states inject per-instance values.** UUIDs and tokens differ per backend instance, so
contracts carry examples plus `ProviderState` generators, and a URL state-change server returns the
real values. pact-python 4.1's function handlers discard return values, which is why a URL handler is
used (see [pact-workflow.md](pact-workflow.md)).

**The verifier's verdict is the verdict.** A missing contract fails provider verification; a pact
file existing proves nothing.

**One redaction path.** Logs, attachments, verifier output and Broker errors all go through
`Redactor`; `AllureReporter` is the only caller of `allure.attach`.

## Test-data strategy

| Mechanism | Used for |
|---|---|
| `POST /test-data/reset` once per session (`session_baseline`) | the documented baseline before the first backend test |
| reset before **and after** each `destructive` test (`fresh_baseline`) | orders (consume stock, advance `SHP-` numbers) |
| a freshly registered `shopper` per test | every cart/wishlist/checkout write — no shared carts |
| seeded accounts, session-scoped sign-in | read-only tests and authorisation checks |
| `admin_cleanup` | deleting what admin tests create (`reset` re-seeds but never deletes extra rows) |
| reset before the first state of each Pact interaction | provider verification isolation |

`reset` is cheap (~0.3 s), deterministic, restores stock, re-seeds the catalogue, removes registered
accounts and restarts the order sequence. It exists only when the backend runs as
`development`/`testing`; with `MTP_RESET_MODE=none` destructive tests skip and say why.

Order independence is checked, not assumed: `make test-random` runs the functional suite under
pytest-randomly (verified with seeds 12345 and 987654).

## Execution order dependency

Provider verification consumes the contract written by the consumer tests. In one `pytest` run the
consumer tests are collected first (`tests/contracts/consumer` before `.../provider`); run separately,
`pytest -m pact_consumer` must precede `pytest -m pact_provider` — or verify from the Broker.
