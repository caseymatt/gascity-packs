#!/bin/sh
set -eu

if [ -z "${GC_PACK_DIR:-}" ]; then
    echo "thunderdome: GC_PACK_DIR is required" >&2
    exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "thunderdome: python3 is required" >&2
    exit 2
fi

exec python3 "$GC_PACK_DIR/assets/scripts/thunderdome.py" "$@"
