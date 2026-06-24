#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT/real_mission/run_real_mission.sh" "$ROOT/real_mission/parameter_config/mission2_target_payload.json"

