#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"
exec "${PYTHON_BIN:-python3}" tools/mission_stream_view.py "$@"
