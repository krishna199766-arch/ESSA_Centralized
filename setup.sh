#!/usr/bin/env bash
# One-time setup: backend Python deps + build the frontend.
set -e
cd "$(dirname "$0")"

echo "==> Backend: creating venv + installing deps"
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt
deactivate
cd ..

echo "==> Frontend: installing + building"
cd frontend
npm install
npm run build
cd ..

echo
echo "Setup complete. Now run:  ./run.sh"
