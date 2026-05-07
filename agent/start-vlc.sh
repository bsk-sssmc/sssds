#!/bin/bash
# start-vlc.sh — launcher invoked by sssds-vlc.service.
#
# Solves the kiosk-mode XAUTHORITY problem: a system service running
# as User=sssds doesn't inherit the X cookie from the user's graphical
# session. Different display managers put the cookie in different
# places (LightDM: /var/run/lightdm/<user>/xauthority, SDDM:
# /var/run/sddm/<token>, GDM: /run/user/<uid>/gdm/Xauthority), and
# Xorg's own -auth points at a root-owned file we can't read.
#
# Most robust strategy: read the XAUTHORITY env var that's *already*
# exported in some other process the user owns (the session leader,
# panel, file manager, etc.). That path is guaranteed to work because
# the session is currently using it.

set -e

# Load /etc/sssds/identity.conf so SSSDS_* env vars (notably
# SSSDS_VIDEO_PATH) are set whether we're invoked by systemd — which
# already imports the file via EnvironmentFile= — or by hand from a
# shell. Re-importing under systemd is harmless; same values, same env.
IDENTITY_FILE="/etc/sssds/identity.conf"
if [[ -r "$IDENTITY_FILE" ]]; then
    while IFS='=' read -r key value || [[ -n "${key:-}" ]]; do
        [[ -z "${key:-}" || "${key}" == \#* ]] && continue
        [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        export "${key}=${value}"
    done < "$IDENTITY_FILE"
fi

DISPLAY="${DISPLAY:-:0}"
export DISPLAY

ME_UID="$(id -u)"

# --- helpers ---------------------------------------------------------------

# Pull XAUTHORITY out of /proc/<pid>/environ. Returns 0 + path on success.
_environ_xauth() {
    local pid=$1
    [[ -r /proc/$pid/environ ]] || return 1
    local val
    val=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null \
          | sed -n 's/^XAUTHORITY=//p' | head -n1)
    if [[ -n "$val" && -r "$val" && -s "$val" ]]; then
        printf '%s\n' "$val"
        return 0
    fi
    return 1
}

discover_from_session() {
    local names="lxqt-session lxsession openbox xfce4-session gnome-session-binary plasmashell mutter awesome i3 cinnamon-session lxqt-panel pcmanfm-qt"
    local pid auth
    for name in $names; do
        for pid in $(pgrep -u "$ME_UID" -x "$name" 2>/dev/null); do
            if auth=$(_environ_xauth "$pid"); then
                printf '%s\n' "$auth"
                return 0
            fi
        done
    done
    # Wider net: any process we own.
    for pid in $(pgrep -u "$ME_UID" 2>/dev/null); do
        if auth=$(_environ_xauth "$pid"); then
            printf '%s\n' "$auth"
            return 0
        fi
    done
    return 1
}

discover_from_xorg_cmdline() {
    local pid args auth
    for pid in $(pgrep -x Xorg 2>/dev/null) $(pgrep -x Xwayland 2>/dev/null); do
        [[ -r /proc/$pid/cmdline ]] || continue
        args=$(tr '\0' '\n' < "/proc/$pid/cmdline")
        auth=$(printf '%s\n' "$args" | awk '/^-auth$/{getline; print; exit}')
        if [[ -n "$auth" && -r "$auth" && -s "$auth" ]]; then
            printf '%s\n' "$auth"
            return 0
        fi
    done
    return 1
}

discover_from_known_paths() {
    local f
    for f in \
        "/var/run/lightdm/${USER}/xauthority" \
        "${HOME}/.Xauthority" \
        "/run/user/${ME_UID}/gdm/Xauthority" \
        /run/sddm/*; do
        if [[ -r "$f" && -s "$f" ]]; then
            printf '%s\n' "$f"
            return 0
        fi
    done
    return 1
}

# --- main ------------------------------------------------------------------

# Wait up to 60s for an X server to be running.
for _ in $(seq 1 60); do
    if pgrep -x Xorg >/dev/null 2>&1 || pgrep -x Xwayland >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Wait up to 60s for a working XAUTHORITY. Re-resolves on each tick because
# auto-login may take several seconds to spawn the session leader after X
# itself comes up.
XAUTH=""
for _ in $(seq 1 60); do
    XAUTH=$(discover_from_session 2>/dev/null) \
        || XAUTH=$(discover_from_xorg_cmdline 2>/dev/null) \
        || XAUTH=$(discover_from_known_paths 2>/dev/null) \
        || XAUTH=""
    if [[ -n "$XAUTH" ]]; then
        export XAUTHORITY="$XAUTH"
        if xset -display "$DISPLAY" q >/dev/null 2>&1; then
            break
        fi
    fi
    sleep 1
done

if ! xset -display "$DISPLAY" q >/dev/null 2>&1; then
    echo "start-vlc: could not open ${DISPLAY} (XAUTHORITY=${XAUTHORITY:-unset})" >&2
    exit 1
fi

echo "start-vlc: connected, DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"
exec /opt/sssds/.venv/bin/python /opt/sssds/VLC/vlc_timestamp_tool.py
