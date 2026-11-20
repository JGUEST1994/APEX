#!/bin/bash
# Start the APEX dashboard on a Mac: double-click this file (or run: bash start-apex.command).
# Research and paper modes only; there is no live trading.
# The first run creates .venv and installs APEX (a few minutes). Close the window to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -f ".venv/bin/activate" ]; then
  PY=""
  for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      PY="$p"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "APEX needs Python 3.11 or newer. Install it from https://www.python.org/downloads/macos/"
    echo "then double-click this file again."
    read -r -p "Press Return to close." _; exit 1
  fi
  echo "First run: setting up APEX with $PY (a few minutes)..."
  "$PY" -m venv .venv && .venv/bin/pip install -q -e ".[server]" || {
    echo "Setup failed (see above)."; read -r -p "Press Return to close." _; exit 1; }
fi
source .venv/bin/activate
apex db-upgrade
# open the dashboard a few seconds later, once the server is up
(sleep 5; open "http://127.0.0.1:8000/ui/") &
echo "APEX is running at http://127.0.0.1:8000/ui/ . Close this window to stop it."
# caffeinate keeps the Mac awake (no idle sleep) for as long as APEX runs
caffeinate -i apex serve
