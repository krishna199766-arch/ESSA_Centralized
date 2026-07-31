@echo off
REM One-time setup on Windows: backend Python venv + deps, then build the frontend.
cd /d "%~dp0"

echo ==^> Backend: creating venv + installing deps
cd backend
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR installing Python deps. Make sure Python 3.10+ is installed and on PATH.
  pause
  exit /b 1
)
call deactivate
cd ..

echo.
echo ==^> Frontend: installing + building
cd frontend
call npm install
call npm run build
if errorlevel 1 (
  echo.
  echo ERROR building frontend. Make sure Node.js 18+ is installed and on PATH.
  pause
  exit /b 1
)
cd ..

echo.
echo Setup complete. Now run:  run.bat
pause
