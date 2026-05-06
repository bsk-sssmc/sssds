#!/usr/bin/env bash
# install-dashboard.sh — set up the central dashboard server.
#
# Run this on the dedicated dashboard machine, once. It creates the
# sssds user, installs the code into /opt/sssds, generates an admin
# password hash, writes /etc/sssds/dashboard.env, and starts the systemd
# unit on :8080.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (use sudo)" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/opt/sssds"
CONFIG_DIR="/etc/sssds"
STATE_DIR="/var/lib/sssds"
LOG_DIR="/var/log/sssds"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3 python3-venv python3-pip openssl >/dev/null

if ! id sssds >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/sssds --shell /bin/bash sssds
fi

mkdir -p "${INSTALL_DIR}" "${STATE_DIR}" "${LOG_DIR}" "${CONFIG_DIR}"
if command -v rsync >/dev/null; then
  rsync -a --delete \
    --exclude '__pycache__' --exclude '.venv' --exclude '.git' \
    "${REPO_DIR}/" "${INSTALL_DIR}/"
else
  cp -a "${REPO_DIR}/." "${INSTALL_DIR}/"
fi
chown -R sssds:sssds "${INSTALL_DIR}" "${STATE_DIR}" "${LOG_DIR}"

sudo -u sssds python3 -m venv "${INSTALL_DIR}/.venv"
sudo -u sssds "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
sudo -u sssds "${INSTALL_DIR}/.venv/bin/pip" install --quiet \
  -r "${INSTALL_DIR}/dashboard/requirements.txt"

ENV_FILE="${CONFIG_DIR}/dashboard.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "==> generating ${ENV_FILE}"
  echo -n "Set the admin password: "
  read -rs ADMIN_PW; echo
  HASH="$(sudo -u sssds "${INSTALL_DIR}/.venv/bin/python" -c \
    "from dashboard.auth import hash_password; print(hash_password('${ADMIN_PW//\'/\'\\\'\'}'))")"
  SESSION_SECRET="$(openssl rand -hex 32)"
  AGENT_TOKEN="$(openssl rand -hex 24)"
  cat > "${ENV_FILE}" <<EOF
SSSDS_BIND_HOST=0.0.0.0
SSSDS_BIND_PORT=8080
SSSDS_DB_PATH=${STATE_DIR}/dashboard.db
SSSDS_ADMIN_PASSWORD_HASH=${HASH}
SSSDS_SESSION_SECRET=${SESSION_SECRET}
SSSDS_AGENT_TOKEN=${AGENT_TOKEN}
SSSDS_EVENT_RETENTION_DAYS=180
SSSDS_OFFLINE_AFTER=30
EOF
  chown root:sssds "${ENV_FILE}"
  chmod 640 "${ENV_FILE}"
  echo
  echo "Agent token (each node needs this when you run provision-node.sh):"
  echo "    ${AGENT_TOKEN}"
  echo
else
  echo "==> reusing existing ${ENV_FILE}"
fi

install -m 0644 "${INSTALL_DIR}/deploy/dashboard.service" /etc/systemd/system/sssds-dashboard.service
systemctl daemon-reload
systemctl enable --now sssds-dashboard.service

echo
echo "Dashboard up. Try it at: http://$(hostname -I | awk '{print $1}'):8080"
