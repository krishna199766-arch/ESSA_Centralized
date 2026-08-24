"""Entry point. Usage:
    python run.py         # start dev server
    python run.py init    # create DB + seed sample data
"""
import sys
from app import create_app, db
from app.seed import seed_all

app = create_app()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        with app.app_context():
            db.drop_all()
            db.create_all()
            seed_all()
            print("Database initialized with sample data.")
            print("Login: admin / admin123")
    else:
        with app.app_context():
            db.create_all()
            # Pick up anything the warehouse gained since last start.
            from app.master_categories import sync_master_categories
            from app.warehouse_items import sync_warehouse_items
            from app.places import sync_locations
            sync_master_categories()
            sync_warehouse_items()
            sync_locations()
        import os
        port = int(os.environ.get("PORT", "8000"))
        app.run(host="0.0.0.0", port=port, debug=True)
