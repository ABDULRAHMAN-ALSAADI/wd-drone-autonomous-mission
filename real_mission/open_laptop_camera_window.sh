#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${REAL_MISSION_CONFIG:-$ROOT/real_mission/parameter_config/mission2_target_payload.json}"

cd "$ROOT"
exec ./scripts/pi_camera_live.sh --config "$CONFIG_PATH" "$@"
