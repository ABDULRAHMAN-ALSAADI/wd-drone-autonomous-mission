#!/usr/bin/env bash
set -euo pipefail

PI_ALIAS="${PI_ALIAS:-pi5}"
REMOTE_DIR="${REMOTE_DIR:-~/FOR_COMP/wd-drone-autonomous-mission}"

ssh "$PI_ALIAS" "
set -e
cd $REMOTE_DIR
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m py_compile tools/mavlink_bench.py src/wd_drone/*.py target_mission_v2/control.py target_mission_v2/mission_controller.py target_mission_v2/vision.py
python tools/mavlink_bench.py --help >/tmp/wd_drone_mavlink_bench_help.txt
PYTHONPATH=src python -m unittest discover -s tests -v
python -c 'import sys; import pymavlink; print(sys.version); print(\"pymavlink ok\")'
vcgencmd measure_temp
vcgencmd get_throttled
"
