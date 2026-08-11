"""Emergency reset: (re)creates the admin/admin123 login without touching data.

Usage:
    python reset_admin.py             # ensures admin exists with password admin123
    python reset_admin.py fullseed    # wipes DB and reseeds all sample data
"""
import sys
from app import create_app, db
from app.models import User
from app.seed import seed_all

app = create_app()

with app.app_context():
    db.create_all()
    if len(sys.argv) > 1 and sys.argv[1] == "fullseed":
        print("Wiping database and reseeding...")
        db.drop_all()
        db.create_all()
        seed_all()
        print("Done. Login: admin / admin123 (also: manager/manager123, ravi/cashier123)")
    else:
        u = User.query.filter_by(username="admin").first()
        if u:
            u.set_password("admin123")
            u.active = True
            u.role = "admin"
            print("Reset password for existing 'admin' user -> admin123")
        else:
            u = User(username="admin", full_name="Shop Owner", role="admin")
            u.set_password("admin123")
            db.session.add(u)
            print("Created new 'admin' user -> admin123")
        db.session.commit()
        print("You can now log in with: admin / admin123")
