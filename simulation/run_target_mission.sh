#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$ROOT/target_mission_v2/configs/sim_gazebo.json}"

cd "$ROOT/target_mission_v2"
echo "[SIM MISSION] config=$CONFIG_PATH"
exec ./run.sh "$CONFIG_PATH"

