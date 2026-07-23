#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMMAND="${1:-help}"
if [[ "$#" -gt 0 ]]; then
    shift
fi

pick_python() {
    local candidate
    for candidate in "${PYTHON_BIN:-}" "$ROOT/.venv/bin/python" python3; do
        [[ -n "$candidate" ]] || continue
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY' >/dev/null 2>&1
import cv2
import numpy
import picamera2
PY
        then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if [[ "$COMMAND" == "help" || "$COMMAND" == "--help" || "$COMMAND" == "-h" ]]; then
    cat <<'EOF'
Camera Module 3 OpenCV tests. These commands do not connect to MAVLink.

Usage:
  ./test_components/camera/opencv_test.sh list
  ./test_components/camera/opencv_test.sh diagnostic [options]
  ./test_components/camera/opencv_test.sh live [options]
  ./test_components/camera/opencv_test.sh focus [options]
  ./test_components/camera/opencv_test.sh benchmark [options]
  ./test_components/camera/opencv_test.sh calibrate [options]

Examples:
  ./test_components/camera/opencv_test.sh diagnostic --seconds 20
  ./test_components/camera/opencv_test.sh live --mode full --headless --stream
  ./test_components/camera/opencv_test.sh focus --distance-label search
  ./test_components/camera/opencv_test.sh benchmark --mode full --seconds 30
EOF
    exit 0
fi

PYTHON_BIN="$(pick_python)" || {
    cat >&2 <<'EOF'
No Python environment can import cv2, numpy, and picamera2.
On Raspberry Pi OS, install the system camera/OpenCV packages, then run:
  ./scripts/setup.sh
See docs/OPENCV_PI5_TESTING.md for the exact setup sequence.
EOF
    exit 1
}

cd "$ROOT"
export PYTHONUNBUFFERED=1

case "$COMMAND" in
    list)
        exec "$PYTHON_BIN" tools/pi_camera_diagnostics.py --list-cameras "$@"
        ;;
    diagnostic)
        exec "$PYTHON_BIN" tools/pi_camera_diagnostics.py "$@"
        ;;
    live)
        exec "$PYTHON_BIN" tools/opencv_live_test.py "$@"
        ;;
    focus)
        exec "$PYTHON_BIN" tools/pi_camera_focus_sweep.py "$@"
        ;;
    benchmark)
        exec "$PYTHON_BIN" tools/benchmark_opencv_pipeline.py "$@"
        ;;
    calibrate)
        exec "$PYTHON_BIN" tools/calibrate_payload_drop_pixel.py "$@"
        ;;
    *)
        echo "Unknown OpenCV camera command: $COMMAND" >&2
        echo "Run: ./test_components/camera/opencv_test.sh help" >&2
        exit 2
        ;;
esac
