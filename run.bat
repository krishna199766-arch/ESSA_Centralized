@echo off
REM Start the app on Windows: seed the DB on first run, then serve API + UI on :8000.
cd /d "%~dp0backend"

REM The venv interpreter is called DIRECTLY rather than activated.
REM
REM activate.bat only edits PATH, using the absolute path the venv was CREATED
REM at. This one was made under ...\OneDrive\Desktop\essa-intake\essa-intake\
REM backend\.venv and the project has since moved to "Centralized ESSA", so
REM activation put a directory that no longer exists on PATH, "python" fell
REM through to the machine-wide install, and the server ran against whatever was
REM there. Nothing errored; a package installed into the venv was simply
REM invisible, and the feature needing it failed at runtime.
REM
REM Naming the executable cannot drift that way: a venv python.exe finds its own
REM prefix from where it stands, so it keeps working after the folder is moved.
set "PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Seed only if the DB does not exist yet (pass --reset to force a rebuild).
if not exist data\essa.db (
  echo ==^> Seeding database
  "%PY%" app\seed.py --reset
)
if "%1"=="--reset" (
  echo ==^> Resetting database
  "%PY%" app\seed.py --reset
)

echo.
echo ==^> Serving on http://localhost:8000/   (Ctrl-C to stop)
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
