#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
    echo "Missing .venv. Run ./scripts/setup.sh first." >&2
    exit 1
fi

source .venv/bin/activate
python tools/mavlink_bench.py "$@"
