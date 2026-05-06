#!/usr/bin/env bash
# run-dashboard-dev.sh — start the dashboard locally.
#
# Sources ./dev/dashboard.env, activates the local venv, runs uvicorn
# with --reload so edits to dashboard/ pick up automatically.
#
# Stop with Ctrl+C.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

VENV="${REPO_DIR}/.venv"
ENV_FILE="${REPO_DIR}/dev/dashboard.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing ${ENV_FILE} — run ./deploy/install-dashboard-dev.sh first" >&2
  exit 1
fi

if [[ ! -x "${VENV}/bin/uvicorn" ]]; then
  echo "missing venv — run ./deploy/install-dashboard-dev.sh first" >&2
  exit 1
fi

# Read the env file as data (not bash code). Values like bcrypt hashes
# contain '$' which would otherwise be interpreted as variable refs and
# blow up under set -u.
while IFS='=' read -r key value || [[ -n "${key:-}" ]]; do
  [[ -z "${key:-}" || "${key}" == \#* ]] && continue
  [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  # Strip optional surrounding quotes so quoted env files also work.
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  export "${key}=${value}"
done < "${ENV_FILE}"

echo "==> dashboard listening on ${SSSDS_BIND_HOST}:${SSSDS_BIND_PORT}"
echo "    open: http://localhost:${SSSDS_BIND_PORT}"
echo

exec "${VENV}/bin/uvicorn" dashboard.main:app \
  --host "${SSSDS_BIND_HOST}" \
  --port "${SSSDS_BIND_PORT}" \
  --reload \
  --reload-dir "${REPO_DIR}/dashboard" \
  --reload-dir "${REPO_DIR}/shared"
