#!/usr/bin/env bash
set -euo pipefail

ARDUPILOT_GAZEBO_DIR="${ARDUPILOT_GAZEBO_DIR:-$HOME/ardupilot_gazebo}"
EXPECTED_COMMIT="082a0fe231f6e63bc8d1598f1cba461d9e2ea7f5"
PATCH_FILE="$(cd "$(dirname "$0")" && pwd)/assets/ardupilot_gazebo_wd.patch"
ACTION="${1:---check}"
WORLD_FILE="$ARDUPILOT_GAZEBO_DIR/worlds/iris_runway.sdf"
CAMERA_FILE="$ARDUPILOT_GAZEBO_DIR/models/gimbal_small_3d/model.sdf"

if [[ ! -d "$ARDUPILOT_GAZEBO_DIR/.git" ]]; then
    echo "[ERROR] ardupilot_gazebo is not a Git clone: $ARDUPILOT_GAZEBO_DIR" >&2
    echo "Clone the tested upstream repository first. See simulation/README.md." >&2
    exit 2
fi

if [[ ! -f "$PATCH_FILE" ]]; then
    echo "[ERROR] Project simulation patch is missing: $PATCH_FILE" >&2
    exit 2
fi

current_commit="$(git -C "$ARDUPILOT_GAZEBO_DIR" rev-parse HEAD)"
if [[ "$current_commit" != "$EXPECTED_COMMIT" ]]; then
    echo "[WARNING] Tested commit: $EXPECTED_COMMIT"
    echo "[WARNING] Current commit: $current_commit"
fi

is_applied() {
    grep -Fq '<pose>0 0 0 0 0 -1.5078</pose>' "$CAMERA_FILE" \
        && grep -Fq '<model name="target_hexagon_blue">' "$WORLD_FILE" \
        && grep -Fq '<model name="target_triangle_red">' "$WORLD_FILE"
}

if is_applied; then
    echo "[OK] WD Drone Gazebo field and downward-camera patch already applied."
    exit 0
fi

if ! git -C "$ARDUPILOT_GAZEBO_DIR" apply --check "$PATCH_FILE"; then
    echo "[ERROR] Patch does not apply cleanly." >&2
    echo "Inspect local changes in $ARDUPILOT_GAZEBO_DIR before continuing." >&2
    exit 3
fi

case "$ACTION" in
    --check)
        echo "[READY] Patch is compatible but has not been applied."
        echo "Run: $0 --apply"
        ;;
    --apply)
        git -C "$ARDUPILOT_GAZEBO_DIR" apply "$PATCH_FILE"
        echo "[DONE] Installed the WD Drone Gazebo field and downward camera."
        echo "Changed only:"
        echo "  models/gimbal_small_3d/model.sdf"
        echo "  worlds/iris_runway.sdf"
        ;;
    *)
        echo "Usage: $0 [--check|--apply]" >&2
        exit 2
        ;;
esac
