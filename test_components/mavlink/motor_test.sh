#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

exec ./scripts/mavlink_bench.sh motor-test \
    --connection /dev/serial0 \
    --baud 921600 \
    --i-understand-props-off \
    --i-accept-motor-spin \
    "$@"
