#!/usr/bin/env bash
# install-dashboard-dev.sh — local-only setup for testing.
#
# Run this on your dev box (this Mac). It does NOT touch /opt, /etc, or
# systemd. Everything lives in the repo:
#
#   ./.venv/                  Python venv with dashboard deps
#   ./dev/dashboard.env       config (admin hash, secrets, agent token)
#   ./dev/dashboard.db        SQLite, created on first run
#
# After this finishes, start the dashboard with:  ./deploy/run-dashboard-dev.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

VENV="${REPO_DIR}/.venv"
DEV_DIR="${REPO_DIR}/dev"
ENV_FILE="${DEV_DIR}/dashboard.env"

if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install Python 3 first (brew install python or pyenv)." >&2
  exit 1
fi

mkdir -p "${DEV_DIR}"

if [[ ! -d "${VENV}" ]]; then
  echo "==> creating venv in ${VENV}"
  python3 -m venv "${VENV}"
fi

echo "==> installing dashboard deps"
"${VENV}/bin/pip" install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet -r "${REPO_DIR}/dashboard/requirements.txt"

if [[ -f "${ENV_FILE}" ]]; then
  echo "==> reusing existing ${ENV_FILE}"
  # shellcheck disable=SC1090
  AGENT_TOKEN="$(grep '^SSSDS_AGENT_TOKEN=' "${ENV_FILE}" | cut -d= -f2-)"
else
  echo "==> generating ${ENV_FILE}"
  echo
  echo "Set the admin password (used to log into the dashboard)."
  HASH="$("${VENV}/bin/python" -m dashboard.auth)"
  if [[ -z "${HASH}" ]]; then
    echo "password setup failed" >&2
    exit 1
  fi

  if command -v openssl >/dev/null; then
    SESSION_SECRET="$(openssl rand -hex 32)"
    AGENT_TOKEN="$(openssl rand -hex 24)"
  else
    SESSION_SECRET="$("${VENV}/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"
    AGENT_TOKEN="$("${VENV}/bin/python" -c 'import secrets; print(secrets.token_hex(24))')"
  fi

  cat > "${ENV_FILE}" <<EOF
# Local dev config — DO NOT check this file into git.
SSSDS_BIND_HOST=0.0.0.0
SSSDS_BIND_PORT=8080
SSSDS_DB_PATH=${DEV_DIR}/dashboard.db
SSSDS_ADMIN_PASSWORD_HASH=${HASH}
SSSDS_SESSION_SECRET=${SESSION_SECRET}
SSSDS_AGENT_TOKEN=${AGENT_TOKEN}
SSSDS_EVENT_RETENTION_DAYS=180
SSSDS_OFFLINE_AFTER=30
EOF
  chmod 600 "${ENV_FILE}"
fi

# Best-effort LAN IP for printing — uses the UDP-connect trick, no packet sent.
LAN_IP="$("${VENV}/bin/python" - <<'PY'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
except OSError:
    print("127.0.0.1")
PY
)"

cat <<EOF

------------------------------------------------------------
Setup done.

Start the dashboard:
    ./deploy/run-dashboard-dev.sh

Open it in a browser on this Mac:
    http://localhost:8080

Open it from any other device on the LAN (e.g. the Mac mini node):
    http://${LAN_IP}:8080

When you provision the test Mac mini, run on it:
    sudo ./agent/provision-node.sh 1 1 ${LAN_IP}:8080 ${AGENT_TOKEN}

(zone=1 node=1 — pick whatever scheme you want.)
------------------------------------------------------------
EOF
