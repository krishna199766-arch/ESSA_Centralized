@echo off
REM Windows launcher
cd /d "%~dp0"

echo Checking dependencies...
pip install -q -r requirements.txt

echo Starting Taqua Silks...
python launch.py
pause
