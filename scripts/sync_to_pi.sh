#!/usr/bin/env bash
set -euo pipefail

PI_ALIAS="${PI_ALIAS:-pi5}"
REMOTE_DIR="${REMOTE_DIR:-~/FOR_COMP/wd-drone-autonomous-mission}"
DRY_RUN="${DRY_RUN:-0}"

cd "$(dirname "$0")/.."

RSYNC_FLAGS=(-az --progress)
if [[ "$DRY_RUN" == "1" ]]; then
    RSYNC_FLAGS=(-azn --itemize-changes)
fi

EXCLUDES=(
    --exclude=".venv/"
    --exclude=".wheelhouse/"
    --exclude="venv/"
    --exclude="env/"
    --exclude="__pycache__/"
    --exclude="*.pyc"
    --exclude=".pytest_cache/"
    --exclude=".mypy_cache/"
    --exclude=".ruff_cache/"
    --exclude="build/"
    --exclude="dist/"
    --exclude="logs/"
    --exclude="target_mission_v2/logs/"
    --exclude="data/raw/"
    --exclude="data/processed/"
    --exclude="models/*.hef"
    --exclude="models/*.onnx"
    --exclude="models/*.pt"
    --exclude="secrets/"
    --exclude=".env"
)

ssh "$PI_ALIAS" "mkdir -p $REMOTE_DIR"
rsync "${RSYNC_FLAGS[@]}" "${EXCLUDES[@]}" ./ "$PI_ALIAS:$REMOTE_DIR/"

if [[ "$DRY_RUN" == "1" ]]; then
    echo
    echo "Dry run only. Run without DRY_RUN=1 to sync."
else
    echo
    echo "Synced project to $PI_ALIAS:$REMOTE_DIR"
fi
