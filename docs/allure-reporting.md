# Allure reporting

Versions: **allure-pytest 2.16.2** (pinned in `pyproject.toml`) and the **Allure 2 CLI 2.39.0**
(`io.qameta.allure:allure-commandline`, downloaded from Maven Central by `scripts/install-allure.sh`
and checked against a pinned SHA-256; needs Java 8+ — verified with JDK 18 locally, Temurin 21 in CI).

## Commands

The commands from the brief, verified as-is:

```bash
pytest --alluredir artifacts/allure-results --clean-alluredir
"$(scripts/install-allure.sh)" generate artifacts/allure-results --clean -o artifacts/allure-report
```

The recommended wrapper — run-specific directories, history, report always generated, pytest's exit
status preserved:

```bash
scripts/run-tests.sh                 # everything
scripts/run-tests.sh -m functional   # any pytest arguments
make test | make functional | make pact
make report-open                     # serve the latest report
```

## Where things go

```
artifacts/runs/<run id>/allure-results/   raw results — one directory per run, never shared
artifacts/runs/<run id>/allure-report/    HTML report (index.html)
artifacts/runs/<run id>/run.json          manifest: target, versions, run id, exit status
artifacts/allure-history/                 trend data carried into the next report
artifacts/latest -> runs/<run id>
```

The run id is `MTP_RUN_ID` (CI: `CI-<run id>-<attempt>`), else `API-<UTC timestamp>-<pid>`, so two
concurrent runs never write into the same directory.

**Exit status.** `run-tests.sh` exits with pytest's status. If report generation fails it prints a
warning and still exits with pytest's status — verified for passing tests + failing generator (exit 0)
and failing tests + failing generator (exit 1), and for failing tests + working generator (exit 1,
report generated).

## Organisation

| Parent suite | Suites |
|---|---|
| API | Health, Authentication, Products, Cart, Wishlist, Orders, Admin, Errors |
| Pact | Consumer contracts, Provider verification |
| Framework | Unit |

Each module also sets `epic`/`feature`; tests set `story`, `severity` (smoke paths are `blocker`,
security-relevant ones `critical`) and titles where the function name is not enough. pytest markers
become Allure tags.

## Steps and evidence

- **Every HTTP exchange** is an Allure step (`POST /api/v1/orders`) with one JSON attachment: method,
  redacted URL, selected request headers (`Accept`, `Authorization` → redacted, `Content-Type`,
  `X-Request-ID`, `X-Test-Run-ID`), redacted request body, status, selected response headers
  (`Content-Type`, `Content-Length`, `WWW-Authenticate`, ids), redacted response body, elapsed time,
  and the run/request correlation ids — the request id is echoed by the backend and appears in its
  logs.
- Setup steps: sign-ins, registrations, resets, admin cleanup.
- Assertions: `Assert status is 201`, `Assert the body matches Cart`, `Assert error code is ...`.
- **Consumer tests**: mock-provider exercise step, then the merged interaction JSON.
- **Provider verification**: contract sources (python-model vs external), each contract file, every
  provider-state change (values redacted), verifier output, results JSON, and one step per interaction.
- **Environment widget** (`environment.properties`): base URL, environment, backend version, automation
  endpoints, run id, commit, branch, reset mode, Pact consumer/provider/source/Broker status, framework
  and Python versions. `executor.json` links back to the CI run; `categories.json` buckets failures
  (backend unreachable, Pact verification failure, schema drift, assertion failures, environment
  errors).

## Redaction and size limits

Every attachment goes through `AllureReporter` → `Redactor` (see [security.md](security.md)) and is
truncated to `MTP_ATTACHMENT_MAX_BYTES` (default 64 KiB) with a `[truncated N bytes]` marker. A scan
of a full run's results directory for the seed passwords, registered-account passwords, JWTs and the
test card number found **no matches**.

Captured stdout/stderr/logging is **not** attached (`--allure-no-capture`): it would bypass redaction.
Everything worth keeping is attached explicitly.

## History and trends

`run-tests.sh` copies `artifacts/allure-history/` into the results before generating and refreshes it
afterwards. CI does the same through `actions/cache` keyed per branch, falling back to `main`.

## Known reporting caveats

- Allure counts an `xfail` as **skipped**; the one strict xfail is a documented backend defect.
- The backend exposes no version endpoint outside `development`; set `MTP_BACKEND_VERSION` (CI passes
  the backend commit SHA) or the report says `unknown`.
