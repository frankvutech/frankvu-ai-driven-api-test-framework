#!/usr/bin/env bash
#
# Run pytest into a run-specific Allure results directory, then always generate the HTML report.
#
#   scripts/run-tests.sh [pytest args...]          e.g. scripts/run-tests.sh -m functional
#
# Layout (MTP_ARTIFACTS_DIR, default `artifacts/`):
#
#   artifacts/runs/<run id>/allure-results/   raw results of this run — never shared between runs
#   artifacts/runs/<run id>/allure-report/    the generated HTML report for this run
#   artifacts/runs/<run id>/run.json          run manifest (target, versions, exit status)
#   artifacts/allure-history/                 trend data carried from one run's report to the next
#   artifacts/latest                          symlink to the most recent run directory
#
# **The exit status is pytest's.** A report that fails to generate is reported loudly but never turns
# a failed run green, or a green run red.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

ARTIFACTS="${MTP_ARTIFACTS_DIR:-artifacts}"
RUN_ID="${MTP_RUN_ID:-API-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
export MTP_RUN_ID="${RUN_ID}"
RUN_DIR="${ARTIFACTS}/runs/${RUN_ID}"
RESULTS="${RUN_DIR}/allure-results"
REPORT="${RUN_DIR}/allure-report"
HISTORY="${ARTIFACTS}/allure-history"

PYTEST="${PYTEST:-.venv/bin/pytest}"
[[ -x "${PYTEST}" ]] || PYTEST="pytest"

echo "Run ${RUN_ID}: results -> ${RESULTS}"
"${PYTEST}" --alluredir "${RESULTS}" --clean-alluredir "$@"
pytest_status=$?

# Trend graphs: Allure reads the previous report's history from the results directory.
if [[ -d "${HISTORY}" ]]; then
  mkdir -p "${RESULTS}/history"
  cp -R "${HISTORY}/." "${RESULTS}/history/"
fi

# MTP_ALLURE_BIN points at an existing Allure 2 CLI instead of the pinned download.
if allure_bin="${MTP_ALLURE_BIN:-$(scripts/install-allure.sh)}"; then
  if "${allure_bin}" generate "${RESULTS}" --clean -o "${REPORT}" >/dev/null; then
    rm -rf "${HISTORY}" && mkdir -p "${HISTORY}"
    cp -R "${REPORT}/history/." "${HISTORY}/" 2>/dev/null || true
    ln -sfn "runs/${RUN_ID}" "${ARTIFACTS}/latest"
    echo "Allure report: ${REPORT}/index.html  (open with: make report-open)"
  else
    echo "warning: allure generate failed; raw results are still in ${RESULTS}" >&2
  fi
else
  echo "warning: Allure CLI unavailable; raw results are still in ${RESULTS}" >&2
fi

exit "${pytest_status}"
