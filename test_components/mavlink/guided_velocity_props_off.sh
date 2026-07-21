#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

exec ./scripts/mavlink_bench.sh guided-velocity-test \
    --connection /dev/serial0 \
    --baud 921600 \
    --speed "${SPEED:-0.2}" \
    --duration "${DURATION:-1.0}" \
    --observe "${OBSERVE:-5.0}" \
    --i-understand-props-off \
    --payload-disabled \
    "$@"
