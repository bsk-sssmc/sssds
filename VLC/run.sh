#!/usr/bin/env bash
# Wrapper that runs vlc_timestamp_tool.py with the right architecture.
#
# On Apple Silicon Macs where the installed VLC.app is the Intel build,
# python-vlc can't load libvlccore. This script detects that case and
# runs Python under Rosetta (x86_64) automatically.

set -e

cd "$(dirname "$0")"

SCRIPT="vlc_timestamp_tool.py"
VLC_LIB="/Applications/VLC.app/Contents/MacOS/lib/libvlccore.dylib"

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
    echo "error: python3 not found in PATH" >&2
    exit 1
fi

# Default: just run python3 normally.
RUNNER=("$PY")

# On macOS, if Python is arm64 but VLC is x86_64, switch to Rosetta.
if [[ "$(uname)" == "Darwin" ]]; then
    machine_arch="$(uname -m)"
    if [[ -f "$VLC_LIB" ]]; then
        vlc_arch="$(file -b "$VLC_LIB")"
        if [[ "$machine_arch" == "arm64" && "$vlc_arch" != *arm64* ]]; then
            echo "note: detected Intel-only VLC on arm64 Mac; running Python under Rosetta (x86_64)."
            echo "      for a permanent fix, install the Apple Silicon build of VLC."
            RUNNER=(arch -x86_64 "$PY")
        fi
    fi
fi

exec "${RUNNER[@]}" "$SCRIPT" "$@"
