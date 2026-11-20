@echo off
rem Keep APEX data (and practice accounts, if listed) up to date on a timer.
rem Double-click this file and leave the window open. Close it to stop.
rem Jobs are read from jobs.json in this folder; see "Keep prices up to date" in docs\USER_GUIDE.md.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo No .venv folder here. Follow the quick start in README.md first.
  pause
  exit /b 1
)
if not exist "jobs.json" (
  copy /y "jobs.example.json" "jobs.json" >nul
  echo Created jobs.json from jobs.example.json. Edit it to choose your markets, then run this again.
  pause
  exit /b 0
)
call ".venv\Scripts\activate.bat"
apex db-upgrade
echo APEX scheduler is running. Close this window to stop it.
apex schedule --config jobs.json
pause
