#!/usr/bin/env bash
set -euo pipefail

ARDUPILOT_AUTOTEST="${ARDUPILOT_AUTOTEST:-$HOME/ardupilot/Tools/autotest}"

if [[ ! -d "$ARDUPILOT_AUTOTEST" ]]; then
    echo "ArduPilot autotest folder not found: $ARDUPILOT_AUTOTEST" >&2
    echo "Expected ArduPilot to be installed at ~/ardupilot." >&2
    exit 1
fi

export GZ_IP="${GZ_IP:-127.0.0.1}"
export GZ_PARTITION="${GZ_PARTITION:-kambe_project}"
export GZ_TRANSPORT_ADDR_IFACE="${GZ_TRANSPORT_ADDR_IFACE:-lo}"

cd "$ARDUPILOT_AUTOTEST"
echo "[SITL] ArduCopter gazebo-iris JSON backend"
exec python3 sim_vehicle.py \
    -v ArduCopter \
    -f gazebo-iris \
    --model JSON \
    --map \
    --console \
    --out=udp:127.0.0.1:14550 \
    --out=udp:127.0.0.1:14551 \
    "$@"

