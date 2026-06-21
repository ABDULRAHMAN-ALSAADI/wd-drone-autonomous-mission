#!/usr/bin/env bash
set -euo pipefail

PI_ALIAS="${PI_ALIAS:-pi5}"
REMOTE_DIR="${REMOTE_DIR:-~/FOR_COMP/wd-drone-autonomous-mission}"

ssh "$PI_ALIAS" "REMOTE_DIR='$REMOTE_DIR' bash -s" <<'REMOTE_SCRIPT'
set -e
REMOTE_DIR="${REMOTE_DIR/#\~/$HOME}"
cd "$REMOTE_DIR"

fail() {
    echo "[FAIL] $1" >&2
    exit 1
}

ok() {
    echo "[OK] $1"
}

echo "[INFO] host=$(hostname) project=$(pwd)"
echo "[INFO] commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "[INFO] disk=$(df -h / | tail -1)"
echo "[INFO] temp=$(vcgencmd measure_temp)"
echo "[INFO] throttled=$(vcgencmd get_throttled)"

[ -d .venv ] || fail "missing .venv; run ./scripts/pi_validate.sh from the laptop"
. .venv/bin/activate
python - <<'PY'
import json
from pathlib import Path

import pymavlink  # noqa: F401
import serial  # noqa: F401

cfg = json.loads(Path("target_mission_v2/configs/real_pi_camera_module_3.json").read_text())
assert cfg["mavlink"]["connection"] == "/dev/serial0", cfg["mavlink"]
assert int(cfg["mavlink"]["baud"]) == 921600, cfg["mavlink"]
assert cfg["navigation"]["search_speed_source"] == "qgc_mission", cfg["navigation"]
assert cfg["control"]["altitude_control"] == "off", cfg["control"]
assert cfg["payload"]["simulate_only"] is True, cfg["payload"]
print("[OK] Python imports and real mission config are bench-safe")
PY

[ -e /dev/serial0 ] || fail "/dev/serial0 does not exist"
SERIAL_TARGET=$(readlink -f /dev/serial0)
echo "[INFO] /dev/serial0 -> $SERIAL_TARGET"
[ -r /dev/serial0 ] && [ -w /dev/serial0 ] || fail "current user cannot read/write /dev/serial0"
ok "serial device exists and is accessible"

if grep -Eq 'console=tty(AMA|S)[0-9]+' /proc/cmdline; then
    fail "serial console is still enabled on a UART"
fi
ok "serial console is not using the MAVLink UART"

SERIAL_UNIT="serial-getty@$(basename "$SERIAL_TARGET").service"
if systemctl is-active --quiet "$SERIAL_UNIT"; then
    fail "$SERIAL_UNIT is active and may steal MAVLink bytes"
fi
ok "serial getty is not active on $(basename "$SERIAL_TARGET")"

PINS=$(pinctrl get 14; pinctrl get 15)
echo "$PINS"
echo "$PINS" | grep -q 'GPIO14 = TXD0' || fail "GPIO14 is not TXD0"
echo "$PINS" | grep -q 'GPIO15 = RXD0' || fail "GPIO15 is not RXD0"
ok "GPIO14/15 are mapped to UART0 TX/RX"

./scripts/mavlink_bench.sh --help >/tmp/wd_drone_mavlink_bench_help.txt
./scripts/mavlink_bench.sh motor-test --help >/tmp/wd_drone_motor_test_help.txt
ok "MAVLink bench and guarded motor-test commands are available"

echo
echo "[READY] Tomorrow bench order:"
echo "1. PROPS OFF. Connect Cube TELEM TX/RX/GND to Pi GPIO15/GPIO14/GND."
echo "2. ./scripts/mavlink_bench.sh status --connection /dev/serial0 --baud 921600 --seconds 10"
echo "3. Test STABILIZE, GUIDED, AUTO, then back to STABILIZE."
echo "4. Servo/output tests only after channel is verified and payload is safe."
echo "5. Motor-test only with props removed and explicit safety flags."
REMOTE_SCRIPT
