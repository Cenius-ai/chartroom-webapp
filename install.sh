#!/usr/bin/env bash
#
# Chartroom setup: installs the pinned test runner and self-checks the program.
# Project-level dependencies only - no sudo, no system packages - and it exits;
# the tool is run with `python3 chartroom.py ...`.

set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON:-python3}"

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else "Chartroom needs Python 3.8 or newer")'

"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
"$PYTHON_BIN" -m pip install -r requirements.txt

# Cheap self-checks: the module must import, and the CLI must answer --help.
"$PYTHON_BIN" -c 'import importlib.util as u, pathlib as p, sys; s = u.spec_from_file_location("chartroom", p.Path("chartroom.py")); m = u.module_from_spec(s); s.loader.exec_module(m); sys.exit(0 if callable(m.main) else "chartroom.py has no main() entry point")'
"$PYTHON_BIN" chartroom.py --help > /dev/null

echo "Chartroom is ready (python3 chartroom.py --help)."
echo "Try: python3 chartroom.py charts add --number 1234 --title 'Solent Approach' --region Solent --scale 25000"
echo "Test suite: python3 -m pytest"
