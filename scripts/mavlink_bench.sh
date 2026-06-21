#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export LANG=C.UTF-8
export LC_ALL=C.UTF-8

if [[ ! -d .venv ]]; then
    echo "Missing .venv. Run ./scripts/setup.sh first." >&2
    exit 1
fi

source .venv/bin/activate
python tools/mavlink_bench.py "$@"
