#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"

python_bin="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
    python_bin="python3"
fi

if ! "$python_bin" - <<'PY' >/dev/null 2>&1
import cv2
import numpy
PY
then
    cat >&2 <<'EOF'
[MISSION VIDEO ERROR] OpenCV/NumPy are unavailable on this laptop.
Run:
  ./scripts/setup_ubuntu.sh
Then retry this command.
EOF
    exit 1
fi

exec "$python_bin" tools/mission_stream_view.py "$@"
