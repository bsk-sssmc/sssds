#!/usr/bin/env bash
# uninstall-node.sh — wipe a SSSDS client install from this machine.
#
# Reverses provision-node.sh: stops + removes services, sudoers, auto-login
# drop-ins, install/state/log dirs, and the legacy 'sssds' user account if
# an older provisioner created one. After this you can re-provision from a
# clean slate.
#
# Run as root. The script you are currently looking at lives inside the
# repo, so just clone the repo on the node, then run:
#     sudo ./agent/uninstall-node.sh

set -e

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (use sudo)" >&2
  exit 1
fi

echo "==> stopping services"
for svc in sssds-vlc sssds-agent sssds-wol; do
  systemctl stop    "${svc}.service" 2>/dev/null || true
  systemctl disable "${svc}.service" 2>/dev/null || true
done

echo "==> removing systemd units"
rm -f /etc/systemd/system/sssds-vlc.service \
      /etc/systemd/system/sssds-agent.service \
      /etc/systemd/system/sssds-wol.service
systemctl daemon-reload || true

echo "==> removing shutdown-time WoL hook"
rm -f /lib/systemd/system-shutdown/sssds-wol-persist

echo "==> removing sudoers rule"
rm -f /etc/sudoers.d/sssds

echo "==> removing auto-login drop-ins"
rm -f /etc/sddm.conf.d/10-sssds-autologin.conf
rm -f /etc/lightdm/lightdm.conf.d/12-sssds-autologin.conf

echo "==> removing install / state / log directories"
rm -rf /etc/sssds /var/lib/sssds /var/log/sssds /opt/sssds

# Legacy: an older provisioner created a separate 'sssds' user. Get rid
# of it so the next provision can run cleanly under the desktop user.
if id sssds >/dev/null 2>&1; then
  echo "==> removing legacy sssds user account (and home dir)"
  pkill -KILL -u sssds 2>/dev/null || true
  sleep 1
  userdel -r sssds 2>/dev/null || userdel sssds 2>/dev/null || true
fi

cat <<EOF

Cleanup complete.

Reboot recommended. After reboot, log in as your normal desktop user
(the one with sudo), then re-run:
    sudo ./agent/provision-node.sh ZONE NODE DASHBOARD_HOST AGENT_TOKEN [VIDEO_PATH]
EOF
