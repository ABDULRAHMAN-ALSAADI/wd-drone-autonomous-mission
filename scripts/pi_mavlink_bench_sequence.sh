#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

exec ./scripts/mavlink_bench.sh bench-sequence \
    --connection "${MAVLINK_CONNECTION:-/dev/ttyAMA0}" \
    --baud "${MAVLINK_BAUD:-921600}" \
    "$@"
