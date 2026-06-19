#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_dir="${1:-"$repo_root/dist/pi_runtime"}"

rm -rf "$out_dir"
mkdir -p "$out_dir/target_mission_v2/configs"

install -m 0644 "$repo_root/requirements.txt" "$out_dir/requirements.txt"
install -m 0644 "$repo_root/target_mission_v2/control.py" "$out_dir/target_mission_v2/control.py"
install -m 0644 "$repo_root/target_mission_v2/vision.py" "$out_dir/target_mission_v2/vision.py"
install -m 0644 "$repo_root/target_mission_v2/mission_controller.py" "$out_dir/target_mission_v2/mission_controller.py"
install -m 0644 "$repo_root/target_mission_v2/mission_config.json" "$out_dir/target_mission_v2/mission_config.json"
install -m 0755 "$repo_root/target_mission_v2/run.sh" "$out_dir/target_mission_v2/run.sh"
install -m 0755 "$repo_root/target_mission_v2/setup.sh" "$out_dir/target_mission_v2/setup.sh"
install -m 0644 "$repo_root/target_mission_v2/configs/sim_gazebo.json" "$out_dir/target_mission_v2/configs/sim_gazebo.json"
install -m 0644 "$repo_root/target_mission_v2/configs/real_pi_camera_module_3.json" "$out_dir/target_mission_v2/configs/real_pi_camera_module_3.json"

cat > "$out_dir/README_PI_RUNTIME.txt" <<'EOF'
WD DRONE Pi Runtime Package

This folder intentionally contains only the flight mission runtime.
It excludes vision_lab, tests, docs, recordings, reports, and development tools.

Run on the Pi from target_mission_v2:

  ./setup.sh
  ./run.sh configs/real_pi_camera_module_3.json
EOF

echo "Pi runtime package written to: $out_dir"
