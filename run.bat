@echo off
REM Start the app on Windows: seed the DB on first run, then serve API + UI on :8000.
cd /d "%~dp0\backend"
call .venv\Scripts\activate.bat

REM Seed only if the DB doesn't exist yet (pass --reset to force a clean rebuild).
if not exist data\essa.db (
  echo ==^> Seeding database ^(suppliers + sample invoices^)
  python app\seed.py --reset
)
if "%1"=="--reset" (
  echo ==^> Resetting database
  python app\seed.py --reset
)

echo.
echo ==^> Serving on http://localhost:8000/   (Ctrl-C to stop)
echo     Tip: turn on Vision in the app (top-right) for best extraction.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
