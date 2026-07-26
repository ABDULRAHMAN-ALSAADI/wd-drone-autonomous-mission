#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

exec ./scripts/mavlink_bench.sh guided-velocity-test \
    --connection "${MAVLINK_CONNECTION:-/dev/ttyAMA0}" \
    --baud "${MAVLINK_BAUD:-921600}" \
    --speed "${SPEED:-0.2}" \
    --duration "${DURATION:-1.0}" \
    --observe "${OBSERVE:-5.0}" \
    "$@"
