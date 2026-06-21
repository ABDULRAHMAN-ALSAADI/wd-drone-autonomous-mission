#!/usr/bin/env bash
set -euo pipefail

PI_ALIAS="${PI_ALIAS:-pi5}"
REMOTE_DIR="${REMOTE_DIR:-~/FOR_COMP/wd-drone-autonomous-mission}"

ssh "$PI_ALIAS" "REMOTE_DIR='$REMOTE_DIR' bash -s" <<'REMOTE_SCRIPT'
set -e
REMOTE_DIR="${REMOTE_DIR/#\~/$HOME}"
cd "$REMOTE_DIR"

[ -d .venv ] || {
    echo "[FAIL] missing .venv; run ./scripts/pi_validate.sh first" >&2
    exit 1
}

. .venv/bin/activate
mkdir -p .wheelhouse
python -m pip download -r requirements.txt -d .wheelhouse
echo "[READY] Cached Python wheels in $(pwd)/.wheelhouse"
ls -1 .wheelhouse
REMOTE_SCRIPT
