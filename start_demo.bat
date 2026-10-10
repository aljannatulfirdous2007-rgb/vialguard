@echo off
rem VialGuard demo: double-click this file. No internet or hardware needed.
rem Runs the same in-browser engine as the public site (ML forecaster, assistant, map need internet only for map tiles and live weather).
rem For a real ESP32 or Firebase, use: python simulator\simulate.py --local --panel
cd /d "%~dp0"
start "" http://localhost:8090/
echo VialGuard is running at http://localhost:8090/  (close this window to stop)
python -m http.server 8090 --bind 127.0.0.1
pause
