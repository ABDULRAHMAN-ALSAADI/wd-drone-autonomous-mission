#!/usr/bin/env bash
set -euo pipefail

PI_ALIAS="${PI_ALIAS:-pi5}"

ssh "$PI_ALIAS" '
set -e
echo "== Pi =="
hostname
hostname -I
uname -m
vcgencmd measure_temp
vcgencmd get_throttled

echo
echo "== Camera Boot Config =="
grep -n -E "camera|cam|imx|dtoverlay" /boot/firmware/config.txt 2>/dev/null || true

echo
echo "== rpicam Cameras =="
if command -v rpicam-hello >/dev/null 2>&1; then
    rpicam-hello --list-cameras || true
else
    echo "rpicam-hello not installed"
fi

echo
echo "== Video Devices =="
ls -l /dev/video* /dev/media* 2>/dev/null || true

echo
echo "== Hailo PCIe =="
if command -v lspci >/dev/null 2>&1; then
    lspci | grep -i -E "hailo|co-processor|pcie" || true
else
    echo "lspci not installed"
fi

echo
echo "== Hailo Runtime =="
dpkg -l 2>/dev/null | grep -i hailo || true
if command -v hailortcli >/dev/null 2>&1; then
    hailortcli fw-control identify || true
else
    echo "hailortcli not installed"
fi
'
