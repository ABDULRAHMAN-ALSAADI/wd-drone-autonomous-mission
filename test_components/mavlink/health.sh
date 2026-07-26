#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

exec ./scripts/mavlink_bench.sh health \
    --connection "${MAVLINK_CONNECTION:-/dev/ttyAMA0}" \
    --baud "${MAVLINK_BAUD:-921600}" \
    --seconds "${SECONDS_TO_RUN:-10}"
