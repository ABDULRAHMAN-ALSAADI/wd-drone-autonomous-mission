#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_SYSTEM_PACKAGES=0

usage() {
    cat <<'EOF'
Usage: ./scripts/setup_ubuntu.sh [--skip-system-packages]

Prepare an Ubuntu laptop for WD Drone development and run all software tests.

  --skip-system-packages  Do not run apt-get. Useful when the listed Ubuntu
                          packages are already installed.
EOF
}

case "${1:-}" in
    "")
        ;;
    --skip-system-packages)
        SKIP_SYSTEM_PACKAGES=1
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *ubuntu* ]]; then
        echo "[WARNING] This installer is tested on Ubuntu; detected ${PRETTY_NAME:-unknown Linux}."
    fi
fi

if (( SKIP_SYSTEM_PACKAGES == 0 )); then
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "[ERROR] apt-get is unavailable. Install the dependencies in UBUNTU_SETUP.txt manually." >&2
        exit 1
    fi

    if (( EUID == 0 )); then
        APT=(apt-get)
    elif command -v sudo >/dev/null 2>&1; then
        APT=(sudo apt-get)
    else
        echo "[ERROR] sudo is required to install Ubuntu packages." >&2
        exit 1
    fi

    echo "[1/3] Installing Ubuntu packages"
    "${APT[@]}" update
    "${APT[@]}" install -y \
        ca-certificates \
        git \
        openssh-client \
        rsync \
        python3 \
        python3-venv \
        python3-pip \
        python3-opencv \
        python3-numpy \
        gstreamer1.0-tools \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-plugins-ugly \
        gstreamer1.0-libav
else
    echo "[1/3] Skipping Ubuntu package installation"
fi

echo "[2/3] Creating the project Python environment"
"$ROOT/scripts/setup.sh"

echo "[CHECK] Verifying laptop OpenCV and NumPy"
"$ROOT/.venv/bin/python" - <<'PY'
import cv2
import numpy

print(f"OpenCV: {cv2.__version__}")
print(f"NumPy: {numpy.__version__}")
PY

echo "[3/3] Running the complete software test suite"
"$ROOT/scripts/check_project.sh"

cat <<EOF

[READY] Ubuntu development setup passed.

Project: $ROOT
Activate Python: source "$ROOT/.venv/bin/activate"
Beginner guide: $ROOT/UBUNTU_SETUP.txt

Hardware and flight commands are not part of this laptop setup.
EOF
