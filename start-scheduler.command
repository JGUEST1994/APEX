#!/bin/bash
# Keep APEX data (and practice accounts, if listed) up to date on a timer, on a Mac.
# Double-click this file (or run: bash start-scheduler.command) and leave the window open.
# Close it to stop. Jobs are read from jobs.json in this folder; see "Keep prices up to date"
# in docs/USER_GUIDE.md. Run start-apex.command once first: it sets APEX up.
cd "$(dirname "$0")" || exit 1
if [ ! -f ".venv/bin/activate" ]; then
  echo "APEX is not set up here yet. Double-click start-apex.command first."
  read -r -p "Press Return to close." _; exit 1
fi
if [ ! -f "jobs.json" ]; then
  cp jobs.example.json jobs.json
  echo "Created jobs.json from jobs.example.json. Edit it to choose your markets, then run this again."
  read -r -p "Press Return to close." _; exit 0
fi
source .venv/bin/activate
apex db-upgrade
echo "APEX scheduler is running. Close this window to stop it."
# caffeinate keeps the Mac awake (no idle sleep) while the scheduler runs
caffeinate -i apex schedule --config jobs.json
