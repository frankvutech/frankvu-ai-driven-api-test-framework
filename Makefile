# Mobile Test Playground — API, Pact and Allure test framework.
#
# Every target is a thin wrapper; `make help` lists them. The test targets go through
# scripts/run-tests.sh, which writes a run-specific Allure results directory, always generates the HTML
# report, and exits with pytest's status.

.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
RUN := $(UV) run --frozen
PYTEST_ARGS ?=

.PHONY: help
help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------------------------------

.PHONY: install
install: ## Install pinned dependencies (uv.lock) into .venv
	$(UV) sync --frozen

.PHONY: allure-install
allure-install: ## Download the pinned, checksummed Allure CLI into .tools/
	scripts/install-allure.sh

# --- quality -----------------------------------------------------------------------------------------

.PHONY: lint
lint: ## Ruff lint and format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

.PHONY: format
format: ## Ruff autofix and format
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

.PHONY: typecheck
typecheck: ## mypy --strict over src/ and tests/
	$(RUN) mypy

.PHONY: secrets-scan
secrets-scan: ## gitleaks over the working tree (Docker)
	docker run --rm -v "$(CURDIR):/repo:ro" zricethezav/gitleaks:v8.30.1 dir /repo --config /repo/.gitleaks.toml --redact --no-banner

.PHONY: check
check: lint typecheck unit ## Everything that needs no backend

# --- tests -------------------------------------------------------------------------------------------

.PHONY: unit
unit: ## Framework self-tests (no backend)
	$(RUN) pytest -m unit $(PYTEST_ARGS)

.PHONY: smoke
smoke: ## A fast subset proving the backend is wired up
	scripts/run-tests.sh -m smoke $(PYTEST_ARGS)

.PHONY: functional
functional: ## API functional tests against the configured backend
	scripts/run-tests.sh -m functional $(PYTEST_ARGS)

.PHONY: pact-consumer
pact-consumer: ## Generate the consumer contract (Pact mock provider; no backend)
	scripts/run-tests.sh -m pact_consumer $(PYTEST_ARGS)

.PHONY: pact-provider
pact-provider: ## Verify contracts against the real backend
	scripts/run-tests.sh -m pact_provider $(PYTEST_ARGS)

.PHONY: pact
pact: ## Consumer tests, then provider verification, in one run
	scripts/run-tests.sh -m "pact_consumer or pact_provider" $(PYTEST_ARGS)

.PHONY: test
test: ## Everything: unit, functional, consumer, provider — one Allure report
	scripts/run-tests.sh $(PYTEST_ARGS)

.PHONY: test-random
test-random: ## Functional tests in a random order (pytest-randomly), to prove order independence
	$(UV) run --frozen --with pytest-randomly==5.0.0 pytest -m functional $(PYTEST_ARGS)

# --- reports -----------------------------------------------------------------------------------------

.PHONY: report
report: ## Regenerate the HTML report for the latest run
	"$$(scripts/install-allure.sh)" generate artifacts/latest/allure-results --clean -o artifacts/latest/allure-report

.PHONY: report-open
report-open: ## Serve the latest HTML report in a browser
	"$$(scripts/install-allure.sh)" open artifacts/latest/allure-report

# --- backend (the existing ShopTest stack; never built or modified here) ------------------------------

.PHONY: backend-up
backend-up: ## Start the ShopTest backend's own Compose stack (BACKEND_DIR), migrate, wait
	scripts/backend.sh up

.PHONY: backend-reset
backend-reset: ## Reset backend data to the documented baseline
	scripts/backend.sh reset

.PHONY: backend-down
backend-down: ## Stop the backend stack and drop its volume
	scripts/backend.sh down

# --- pact broker -------------------------------------------------------------------------------------

.PHONY: broker-up
broker-up: ## Start a throwaway local Pact Broker on :9292
	docker compose -f docker-compose.broker.yml up -d --wait

.PHONY: broker-down
broker-down: ## Stop the local Pact Broker and forget its data
	docker compose -f docker-compose.broker.yml down -v

.PHONY: pact-publish
pact-publish: ## Publish pacts/ to MTP_PACT_BROKER_URL (VERSION=<sha> BRANCH=<branch>)
	$(RUN) python -m api_tests.pact.broker publish --version "$(VERSION)" $(if $(BRANCH),--branch "$(BRANCH)")

.PHONY: can-i-deploy
can-i-deploy: ## PACTICIPANT=<name> VERSION=<sha> ENVIRONMENT=<env>
	$(RUN) python -m api_tests.pact.broker can-i-deploy --pacticipant "$(PACTICIPANT)" --version "$(VERSION)" --environment "$(ENVIRONMENT)"

# --- housekeeping ------------------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove run artifacts and generated pacts (keeps .tools/)
	rm -rf artifacts pacts/*.json .pytest_cache .mypy_cache .ruff_cache
