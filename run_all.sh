#!/usr/bin/env bash
# V3 only. Historical sources are documented in archive/README.md.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; fi
if [ -f .venv/Scripts/python.exe ]; then PYTHON=.venv/Scripts/python.exe; fi
exec "$PYTHON" sim/experiments.py "$@"
