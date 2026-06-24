#!/usr/bin/env bash
set -euo pipefail

TOPIC="${GAZEBO_CAMERA_TOPIC:-/world/iris_runway/model/iris_with_gimbal/model/gimbal/link/pitch_link/sensor/camera/image/enable_streaming}"

echo "[GAZEBO CAMERA] enabling stream on $TOPIC"
exec gz topic \
    -t "$TOPIC" \
    -m gz.msgs.Boolean \
    -p "data: true"

