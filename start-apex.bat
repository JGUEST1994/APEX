@echo off
rem Start the APEX dashboard on Windows: double-click this file.
rem Research and paper modes only; there is no live trading.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo No .venv folder here. Follow the quick start in README.md first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
apex db-upgrade
rem open the dashboard a few seconds later, once the server is up
start "" /min powershell -NoProfile -Command "Start-Sleep -Seconds 5; Start-Process 'http://127.0.0.1:8000/ui/'"
echo APEX is running. Close this window to stop it.
apex serve
pause
