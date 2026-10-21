#!/usr/bin/env bash
#
# Drive the *existing* ShopTest backend's own Docker Compose stack. Nothing here builds, edits or
# replaces the backend: it runs the stack the backend repository ships (`backend/docker-compose.yml`).
#
#   scripts/backend.sh up        # start PostgreSQL + API, migrate, wait until ready
#   scripts/backend.sh reset     # POST /test-data/reset (development/testing only)
#   scripts/backend.sh wait      # block until /health/database answers 200
#   scripts/backend.sh logs      # backend container logs
#   scripts/backend.sh down      # stop and remove the stack and its volume
#   scripts/backend.sh version   # the backend checkout's commit SHA, for MTP_BACKEND_VERSION
#
# BACKEND_DIR        path to the backend directory (default ../shoptest/backend)
# BACKEND_ENVIRONMENT  ENVIRONMENT for the API container (default testing — enables /test-data,
#                    disables /docs; never staging or production)
# MTP_BASE_URL       where the API answers (default http://localhost:8000)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${BACKEND_DIR:-${ROOT}/../shoptest/backend}"
BACKEND_ENVIRONMENT="${BACKEND_ENVIRONMENT:-testing}"
BASE_URL="${MTP_BASE_URL:-http://localhost:8000}"
WAIT_SECONDS="${BACKEND_WAIT_SECONDS:-120}"

case "${BACKEND_ENVIRONMENT}" in
  development|testing) ;;
  *) echo "error: BACKEND_ENVIRONMENT must be development or testing, not ${BACKEND_ENVIRONMENT}" >&2; exit 2 ;;
esac

compose() {
  [[ -f "${BACKEND_DIR}/docker-compose.yml" ]] || {
    echo "error: no docker-compose.yml in BACKEND_DIR=${BACKEND_DIR}" >&2
    exit 2
  }
  ENVIRONMENT="${BACKEND_ENVIRONMENT}" docker compose --project-directory "${BACKEND_DIR}" \
    -f "${BACKEND_DIR}/docker-compose.yml" "$@"
}

wait_ready() {
  local deadline=$((SECONDS + WAIT_SECONDS))
  until curl --silent --fail --max-time 3 "${BASE_URL}/api/v1/health/database" >/dev/null; do
    if ((SECONDS >= deadline)); then
      echo "error: backend not ready at ${BASE_URL} after ${WAIT_SECONDS}s" >&2
      return 1
    fi
    sleep 2
  done
  echo "Backend ready at ${BASE_URL}"
}

case "${1:-}" in
  up)
    compose up -d --build --wait
    compose run --rm backend alembic upgrade head
    wait_ready
    ;;
  wait) wait_ready ;;
  reset)
    curl --silent --show-error --fail -X POST "${BASE_URL}/api/v1/test-data/reset"
    echo
    ;;
  logs) compose logs --no-color backend ;;
  down) compose down -v ;;
  version) git -C "${BACKEND_DIR}" rev-parse HEAD ;;
  *)
    sed -n '3,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
