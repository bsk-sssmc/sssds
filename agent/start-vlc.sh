#!/bin/bash
# start-vlc.sh — launcher invoked by sssds-vlc.service.
#
# Solves a common kiosk gotcha: SDDM/LightDM don't write a cookie to
# /home/sssds/.Xauthority, so a *system* service running as the auto-
# logged-in user can't find the X cookie. We discover it at runtime by
# reading Xorg's -auth argument out of /proc, which is the path the
# display manager actually used.

set -e

DISPLAY="${DISPLAY:-:0}"
export DISPLAY

# Wait up to 60s for an X server to be running at all.
for _ in $(seq 1 60); do
    if pgrep -x Xorg >/dev/null 2>&1 || pgrep -x Xwayland >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Read the -auth argument out of an X server's cmdline. /proc/<pid>/cmdline
# is NUL-separated, one arg per record.
discover_xauth() {
    local pid args auth
    for pid in $(pgrep -x Xorg 2>/dev/null) $(pgrep -x Xwayland 2>/dev/null); do
        [[ -r /proc/$pid/cmdline ]] || continue
        args=$(tr '\0' '\n' < /proc/$pid/cmdline)
        auth=$(printf '%s\n' "$args" | awk '/^-auth$/{getline; print; exit}')
        if [[ -n "$auth" && -r "$auth" && -s "$auth" ]]; then
            printf '%s\n' "$auth"
            return 0
        fi
    done
    # Fallbacks for non-SDDM display managers / Wayland sessions.
    local me; me=$(id -u)
    for f in \
        "${HOME}/.Xauthority" \
        "/run/user/${me}/gdm/Xauthority" \
        /run/sddm/*; do
        if [[ -r "$f" && -s "$f" ]]; then
            printf '%s\n' "$f"
            return 0
        fi
    done
    return 1
}

# Discover + verify the cookie actually opens the display. Loop because
# the auto-login finishes a few seconds after Xorg comes up.
for _ in $(seq 1 60); do
    if XAUTH=$(discover_xauth); then
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
