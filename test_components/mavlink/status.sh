#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

exec ./scripts/mavlink_bench.sh status --connection /dev/serial0 --baud 921600 --seconds "${SECONDS_TO_RUN:-10}"
