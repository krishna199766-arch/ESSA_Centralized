"""One-click launcher: initializes DB if missing, starts server, opens browser."""
import os
import sys
import time
import threading
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "textile_shop.db"

from app import create_app, db
from app.seed import seed_all

app = create_app()
PORT = int(os.environ.get("PORT", "8000"))

def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Seed if there are no users yet (handles partial/failed prior init)
        from app.models import User
        if User.query.count() == 0:
            print("Empty database — seeding sample data...")
            seed_all()
            print("Done. Default login: admin / admin123")

    print("\n" + "=" * 50)
    print("  The store is running")
    print(f"  Open: http://localhost:{PORT}")
    print("  Login: admin / admin123")
    print("  Press Ctrl+C to stop")
    print("=" * 50 + "\n")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
