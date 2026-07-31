#!/usr/bin/env bash
# Start the app: seed the DB on first run, then serve API + built UI on :8000.
set -e
cd "$(dirname "$0")/backend"
. .venv/bin/activate

# Seed only if the DB doesn't exist yet (use --reset to force a clean rebuild).
if [ ! -f data/essa.db ] || [ "$1" == "--reset" ]; then
  echo "==> Seeding database (suppliers + sample invoices)"
  python app/seed.py --reset
fi

echo "==> Serving on http://localhost:8000  (Ctrl-C to stop)"
echo "    Set ANTHROPIC_API_KEY to enable the vision extractor (recommended for new invoices)."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
