#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

exec ./scripts/pi_camera_check.sh --config "$ROOT/real_mission/parameter_config/real_drone.json" "$@"
