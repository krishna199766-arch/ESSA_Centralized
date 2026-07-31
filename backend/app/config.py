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

# --- Login (UI gate, credentials validated server-side) ---
# Two roles: admin (controls masters + master-entry-by-image) and user (processing).
AUTH_SECRET = os.environ.get("ESSA_AUTH_SECRET", "essa-local-secret-change-me")
AUTH_USERS = {
    os.environ.get("ESSA_ADMIN_USER", "admin"): {
        "password": os.environ.get("ESSA_ADMIN_PASSWORD", os.environ.get("ESSA_PASSWORD", "essa@123")),
        "role": "admin"},
    os.environ.get("ESSA_USER_USER", "user"): {
        "password": os.environ.get("ESSA_USER_PASSWORD", "user@123"),
        "role": "user"},
}
# backwards-compat single-user vars
AUTH_USER = os.environ.get("ESSA_USER", "admin")
AUTH_PASSWORD = os.environ.get("ESSA_PASSWORD", "essa@123")

os.makedirs(UPLOAD_DIR, exist_ok=True)
