@echo off
rem Live demo, no API spend: replays the recorded model answers and runs every check live.
rem Usage (PowerShell or cmd, from this folder):   .\demo            .\demo --regrade
setlocal
set FACTORY_OFFLINE=1
set PYTHONIOENCODING=utf-8
"%~dp0factory\.cache\venv\Scripts\python.exe" "%~dp0slides\demo.py" %*
