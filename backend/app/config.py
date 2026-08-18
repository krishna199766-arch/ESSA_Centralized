"""Central configuration. Everything overridable by environment variable so the
same code runs on a laptop (SQLite, Tesseract) or in production (Postgres,
vision model) with no edits."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/

# Two directories, because `backend/data` holds two different kinds of thing and
# a deployment has to treat them oppositely.
#
# DATA_DIR is SHIPPED data, read-only at runtime and versioned in git: the
# category master, the LR sample, the ground-truth fixtures and sample images.
# It travels with the code and belongs wherever the code is unpacked.
#
# STATE_DIR is what the warehouse PRODUCES and must not lose across a restart:
# the database, the uploaded invoice images, and the vision key saved from the
# settings screen. On a host that rebuilds the code directory on every deploy,
# this is the path a persistent disk gets mounted at.
#
# They default to the same folder, so a laptop sees exactly the behaviour it saw
# before this split existed. Only a deployment sets them apart — and if the two
# were left as one, pointing it at a mounted disk would take the 686-row
# category master away with it and seed an empty warehouse.
DATA_DIR = os.path.join(BASE_DIR, "data")
STATE_DIR = os.environ.get("ESSA_STATE_DIR", DATA_DIR)

UPLOAD_DIR = os.environ.get("ESSA_UPLOAD_DIR", os.path.join(STATE_DIR, "uploads"))
SAMPLE_DIR = os.path.join(DATA_DIR, "sample_images")
GROUND_TRUTH_DIR = os.path.join(DATA_DIR, "ground_truth")

DATABASE_URL = os.environ.get("ESSA_DATABASE_URL", f"sqlite:///{os.path.join(STATE_DIR, 'essa.db')}")

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


def _env(name: str, default: str) -> str:
    """An environment variable, treating blank as absent.

    `os.environ.get` does not: a variable set to "" is present and returns "",
    which is how a hosting dashboard that prompts for a value and is given none
    ends up seeding an account with an EMPTY password. That fails open — anyone
    signs in with no password at all, while the documented default is refused,
    so it reads as a broken deployment rather than an unlocked one. Blank here
    means "not configured", which is what whoever left the box empty meant.
    """
    return (os.environ.get(name) or "").strip() or default


SEED_ACCOUNTS = {
    _env("ESSA_SUPERADMIN_USER", "superadmin"): {
        "password": _env("ESSA_SUPERADMIN_PASSWORD", "super@123"),
        "role": "superadmin", "full_name": "Super Admin"},
    _env("ESSA_ADMIN_USER", "admin"): {
        "password": _env("ESSA_ADMIN_PASSWORD", _env("ESSA_PASSWORD", "essa@123")),
        "role": "admin", "full_name": "Administrator"},
    _env("ESSA_USER_USER", "user"): {
        "password": _env("ESSA_USER_PASSWORD", "user@123"),
        "role": "user", "full_name": "Warehouse User"},
}

os.makedirs(UPLOAD_DIR, exist_ok=True)
# On a fresh disk this is the directory the database is about to be created in,
# so it has to exist before SQLAlchemy opens the file rather than after.
os.makedirs(STATE_DIR, exist_ok=True)
