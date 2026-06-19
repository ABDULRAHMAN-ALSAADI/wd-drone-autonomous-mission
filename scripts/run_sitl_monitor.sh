#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
    echo "Missing .venv. Run ./scripts/setup.sh first." >&2
    exit 1
fi

source .venv/bin/activate
export PYTHONPATH="$PWD/src"
python -m wd_drone.main --profile sitl
