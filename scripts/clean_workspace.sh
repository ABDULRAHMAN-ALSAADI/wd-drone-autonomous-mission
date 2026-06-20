#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

REMOVE_LOGS=0
if [[ "${1:-}" == "--logs" ]]; then
    REMOVE_LOGS=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [--logs]"
    echo "Removes ignored Python caches. With --logs, also removes local runtime logs."
    exit 0
fi

find . \
    -path './.git' -prune -o \
    -path './.venv' -prune -o \
    -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' \) \
    -print -exec rm -rf {} +

if [[ "$REMOVE_LOGS" == "1" ]]; then
    for path in logs target_mission_v2/logs; do
        if [[ -e "$path" ]]; then
            echo "$path"
            rm -rf "$path"
        fi
    done
fi
