# Security

## Secrets

- No real credential is committed. `.env` is gitignored; `.env.example` holds placeholders only.
- The only built-in credentials are the backend's **documented, public seed accounts**
  (`docs/test-credentials.md` of the ShopTest repository), used as defaults for `local` and `ci` only.
  `staging` must supply its own; a missing value fails the test that needs it with the variable name.
- Passwords and the Broker token are `SecretStr` from the moment they are read. `Account.__repr__`
  hides the password; `Settings.describe()` (report metadata) contains no secret.
- CI reads credentials from repository secrets; the Broker URL is a variable, its token a secret.
- `make secrets-scan` runs **gitleaks 8.30.1** over the tree (also a CI step). `.gitleaks.toml`
  allow-lists only the documented seed passwords, the Visa test number and Pact placeholders.

## Redaction

`api_tests.redaction.Redactor` is the single redaction path for logs, Allure attachments, Pact
verifier output, provider-state logs and Broker error messages. It works on copies; the objects tests
assert on are never altered.

1. **Sensitive keys** — `password`, `access_token`, `refresh_token`, `token`, `authorization`,
   `cookie`, `set-cookie`, `api_key`, `secret`, `cvv`, `card_number`, ... in any spelling
   (`Refresh-Token`, `refreshToken`); a card object's `number`.
2. **Sensitive shapes in free text** — JWTs, `Bearer`/`Basic` credentials, 13–19 digit card numbers
   (masked to the last four; UUIDs and order references are not mistaken for them — a unit test caught
   and fixed exactly that).
3. **Known values** — every configured password and token, and every token or password the run itself
   obtains (sign-ins and registrations add theirs), scrubbed verbatim anywhere.
4. URLs lose userinfo and sensitive query parameters.

The `httpx` logger is lowered to WARNING because it logs full URLs unredacted; the framework logs its
own redacted line per exchange instead. A logging filter redacts every record under `api_tests`.

Attachments are size-capped (`MTP_ATTACHMENT_MAX_BYTES`).

Two paths around the redactor are closed explicitly:

- `--allure-no-capture` (in `pytest.ini`): allure-pytest would otherwise attach captured
  stdout/stderr/logging to every result, unredacted.
- The Pact verifier (Rust, via FFI) prints its report straight to file descriptor 1; that descriptor is
  silenced during verification, so pytest never captures and re-prints it into a CI log. The same
  report is read back through `Verifier.output()`, redacted, and attached.

**Verified:** a full run's Allure results contain no seed password, registered-account password, JWT
or card number (`grep` over every result and attachment); 160+ `"password": "[REDACTED]"` and
140+ `"access_token": "[REDACTED]"` occurrences show the redaction working on real traffic.

## Contracts carry no secrets

Login credentials, access tokens and per-instance ids reach the verifier through provider-state
injection (`${password}`, `${accessToken}`), never as literal values in the contract. The order
contract pays cash on delivery, so no card data is in any pact file.

## Not calling production

- Default-deny target guard (see [configuration.md](configuration.md#the-target-guard)).
- `production` is not an accepted `MTP_ENVIRONMENT`.
- Resetting is refused against `staging`; the backend itself does not mount `/test-data` outside
  `development`/`testing`, so a reset against a deployed environment answers 404.
- `scripts/backend.sh` only starts the backend as `development` or `testing`.

## Test data

- Writes use freshly registered accounts on a `.local` domain (never routable), with deterministic,
  run-scoped identities; `reset`/`cleanup` remove them.
- No real user data is created, read or stored. Card data is the public Visa test number, and the
  backend processes no payments.
