#!/usr/bin/env bash
set -euo pipefail

PI_ALIAS="${PI_ALIAS:-pi5}"
REMOTE_DIR="${REMOTE_DIR:-~/FOR_COMP/wd-drone-autonomous-mission}"
MAVLINK_CONNECTION="${MAVLINK_CONNECTION:-/dev/ttyAMA0}"

ssh "$PI_ALIAS" "
set -e
cd $REMOTE_DIR
MAVLINK_CONNECTION='$MAVLINK_CONNECTION'

fail() {
    echo \"[FAIL] \$1\" >&2
    exit 1
}

ok() {
    echo \"[OK] \$1\"
}

echo \"[INFO] host=\$(hostname) project=\$(pwd)\"
echo \"[INFO] commit=\$(git rev-parse --short HEAD 2>/dev/null || echo unknown)\"
echo \"[INFO] temp=\$(vcgencmd measure_temp)\"
echo \"[INFO] throttled=\$(vcgencmd get_throttled)\"

[ -e \"\$MAVLINK_CONNECTION\" ] || fail \"\$MAVLINK_CONNECTION does not exist\"
SERIAL_TARGET=\$(readlink -f \"\$MAVLINK_CONNECTION\")
echo \"[INFO] \$MAVLINK_CONNECTION -> \$SERIAL_TARGET\"
[ -r \"\$MAVLINK_CONNECTION\" ] && [ -w \"\$MAVLINK_CONNECTION\" ] || \
    fail \"current user cannot read/write \$MAVLINK_CONNECTION\"
ok \"serial device exists and is accessible\"

if grep -Eq 'console=tty(AMA|S)[0-9]+' /proc/cmdline; then
    echo \"[INFO] /proc/cmdline: \$(cat /proc/cmdline)\"
    fail \"serial console is still enabled on a UART\"
fi
ok \"serial console is not using the MAVLink UART\"

SERIAL_UNIT=\"serial-getty@\$(basename \"\$SERIAL_TARGET\").service\"
if systemctl is-active --quiet \"\$SERIAL_UNIT\"; then
    fail \"\$SERIAL_UNIT is active and may steal MAVLink bytes\"
fi
ok \"serial getty is not active on \$(basename \"\$SERIAL_TARGET\")\"

PINS=\$(pinctrl get 14; pinctrl get 15)
echo \"\$PINS\"
echo \"\$PINS\" | grep -q 'GPIO14 = TXD0' || fail \"GPIO14 is not TXD0\"
echo \"\$PINS\" | grep -q 'GPIO15 = RXD0' || fail \"GPIO15 is not RXD0\"
ok \"GPIO14/15 are mapped to UART0 TX/RX\"

[ -d .venv ] || fail \"missing .venv; run ./scripts/pi_validate.sh first\"
. .venv/bin/activate
python -c 'import serial; print(\"[OK] pyserial import works\")'

echo \"[READY] Pi UART is ready for Pixhawk TELEM MAVLink. Do not connect props for bench tests.\"
"
