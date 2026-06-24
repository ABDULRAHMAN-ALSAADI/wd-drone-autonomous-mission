#!/usr/bin/env bash
set -euo pipefail

WORLD_PATH="${WORLD_PATH:-$HOME/ardupilot_gazebo/worlds/iris_runway.sdf}"
GAZEBO_RESOURCE_PATH="${GAZEBO_RESOURCE_PATH:-$HOME/ardupilot_gazebo/models:$HOME/ardupilot_gazebo/worlds}"
GAZEBO_PLUGIN_PATH="${GAZEBO_PLUGIN_PATH:-$HOME/ardupilot_gazebo/build}"

if [[ "${1:-}" == "--nvidia" ]]; then
    export __NV_PRIME_RENDER_OFFLOAD=1
    export __GLX_VENDOR_LIBRARY_NAME=nvidia
    shift
fi

if [[ ! -f "$WORLD_PATH" ]]; then
    echo "Gazebo world not found: $WORLD_PATH" >&2
    echo "Expected ardupilot_gazebo to be installed at ~/ardupilot_gazebo." >&2
    exit 1
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
export GZ_IP="${GZ_IP:-127.0.0.1}"
export GZ_PARTITION="${GZ_PARTITION:-kambe_project}"
export GZ_TRANSPORT_ADDR_IFACE="${GZ_TRANSPORT_ADDR_IFACE:-lo}"
export GZ_SIM_RESOURCE_PATH="$GAZEBO_RESOURCE_PATH${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
export GZ_SIM_SYSTEM_PLUGIN_PATH="$GAZEBO_PLUGIN_PATH${GZ_SIM_SYSTEM_PLUGIN_PATH:+:$GZ_SIM_SYSTEM_PLUGIN_PATH}"

echo "[GAZEBO] world=$WORLD_PATH"
exec gz sim -v4 -r "$WORLD_PATH" "$@"

