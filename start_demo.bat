@echo off
rem VialGuard demo: double-click this file. No internet or hardware needed.
cd /d "%~dp0"
start "" http://localhost:8787/dashboard/
echo VialGuard is running. Use the SIMULATION CONTROL buttons in the browser.
echo Close this window to stop.
python simulator\simulate.py --local --panel --escalate 15 30
pause
