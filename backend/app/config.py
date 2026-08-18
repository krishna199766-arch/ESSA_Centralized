"""Central configuration. Everything overridable by environment variable so the
same code runs on a laptop (SQLite, Tesseract) or in production (Postgres,
vision model) with no edits."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.environ.get("ESSA_UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))
SAMPLE_DIR = os.path.join(DATA_DIR, "sample_images")
GROUND_TRUTH_DIR = os.path.join(DATA_DIR, "ground_truth")

DATABASE_URL = os.environ.get("ESSA_DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'essa.db')}")

# Which extraction provider to prefer. "auto" = vision model if a key is present,
# else tesseract. The seeded provider is always consulted first for known samples.
EXTRACTION_PROVIDER = os.environ.get("ESSA_EXTRACTION_PROVIDER", "auto")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VISION_MODEL = os.environ.get("ESSA_VISION_MODEL", "claude-3-5-sonnet-20241022")

# The company this deployment belongs to (the "buyer" on every purchase invoice).
COMPANY_GSTIN = os.environ.get("ESSA_COMPANY_GSTIN", "33AADCE6591N1Z7")
COMPANY_NAME = os.environ.get("ESSA_COMPANY_NAME", "Essa Garments Private Limited")

# --- Login ---
# Accounts live in the database (see models.User and services/users), not here.
# Three ranked roles: user (the floor), admin (setup + money), superadmin (also
# accounts and server settings).
#
# What is left here is the signing key and the accounts used to SEED an empty
# database — a fresh install, or an existing one upgrading from the two
# hard-coded accounts these variables used to be the whole of. Seeding only ever
# creates a missing row, so a password changed in the app is not reverted to the
# value below on the next restart.
AUTH_SECRET = os.environ.get("ESSA_AUTH_SECRET", "essa-local-secret-change-me")
SEED_ACCOUNTS = {
    os.environ.get("ESSA_SUPERADMIN_USER", "superadmin"): {
        "password": os.environ.get("ESSA_SUPERADMIN_PASSWORD", "super@123"),
        "role": "superadmin", "full_name": "Super Admin"},
    os.environ.get("ESSA_ADMIN_USER", "admin"): {
        "password": os.environ.get("ESSA_ADMIN_PASSWORD", os.environ.get("ESSA_PASSWORD", "essa@123")),
        "role": "admin", "full_name": "Administrator"},
    os.environ.get("ESSA_USER_USER", "user"): {
        "password": os.environ.get("ESSA_USER_PASSWORD", "user@123"),
        "role": "user", "full_name": "Warehouse User"},
}

os.makedirs(UPLOAD_DIR, exist_ok=True)
