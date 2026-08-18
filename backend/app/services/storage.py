"""
Where an uploaded invoice actually lives.

On a laptop it is a file under `data/uploads`, which is what this has always
been. On Vercel there is no such place — the filesystem is read-only apart from
`/tmp`, and `/tmp` is per-invocation, so a file written while handling the upload
is gone before anyone asks to see it. There the bytes go to Vercel Blob and the
database keeps the URL.

Two things this deliberately does NOT do:

*Serve the blob URL to the browser.* Vercel Blob URLs are public — anyone
holding one can read the invoice without signing in, and they do not expire.
Every read here is proxied back through `/api/documents/{id}/image`, which is
policed like every other route, so an invoice scan stays behind the login it was
behind before. The cost is that the bytes travel twice on a cache miss, which is
the right way round for a supplier invoice.

*Change how extraction works.* The engine takes a filesystem path and opens it
(`engine.run_extraction`, `lr_svc.extract_lr`). Rather than rewrite that to take
bytes, `materialise` puts the bytes somewhere real for the length of one request
— which is exactly what `/tmp` is for, and is the only thing it is reliable for.
"""
import os
import tempfile

import httpx

from ..config import UPLOAD_DIR

# Set by Vercel when a Blob store is attached to the project. Its presence is
# what switches this module over — there is no separate mode flag to forget.
BLOB_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
BLOB_API = "https://blob.vercel-storage.com"
# Pinned: the header is required, and a floating version would let a change at
# the other end alter what `put` returns without anything here being edited.
BLOB_API_VERSION = "7"

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def using_blob() -> bool:
    return bool(BLOB_TOKEN)


def backend_name() -> str:
    return "vercel-blob" if using_blob() else "local-disk"


# ---------------------------------------------------------------------------
#  writing
# ---------------------------------------------------------------------------

def save(raw: bytes, name: str) -> str:
    """Store these bytes durably and return the reference to keep in the DB.

    The reference is a filesystem path on a laptop and an https URL on Vercel.
    Callers treat it as opaque and hand it back to the functions below — which
    is why `Document.stored_path` did not need a new column or a migration.
    """
    if not using_blob():
        stored = os.path.join(UPLOAD_DIR, name)
        # The name is content-addressed by every caller, so a file that is
        # already there has identical bytes. Not rewriting it also avoids
        # clobbering the read-only sample invoices the seeder puts here.
        if not os.path.exists(stored):
            with open(stored, "wb") as f:
                f.write(raw)
            os.chmod(stored, 0o644)
        return stored

    # `addRandomSuffix=0` keeps the content hash the caller chose as the whole
    # name, so re-uploading the same invoice overwrites one object instead of
    # growing the store a copy at a time.
    r = httpx.put(
        f"{BLOB_API}/{name}",
        content=raw,
        headers={
            "authorization": f"Bearer {BLOB_TOKEN}",
            "x-api-version": BLOB_API_VERSION,
            "x-content-type": "application/octet-stream",
            "x-add-random-suffix": "0",
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["url"]


# ---------------------------------------------------------------------------
#  reading
# ---------------------------------------------------------------------------

def read(ref: str) -> bytes | None:
    """The stored bytes, or None if the reference no longer resolves."""
    if not ref:
        return None
    if not _is_remote(ref):
        if not os.path.exists(ref):
            return None
        with open(ref, "rb") as f:
            return f.read()
    try:
        r = httpx.get(ref, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.content
    except httpx.HTTPError:
        return None


def materialise(ref: str) -> str | None:
    """A real filesystem path for these bytes, for code that opens files.

    A local reference is already one. A remote one is fetched into the
    per-request scratch directory — which on Vercel is `/tmp`, the one writable
    place, and which nothing may assume still exists on the next request.
    """
    if not ref:
        return None
    if not _is_remote(ref):
        return ref if os.path.exists(ref) else None
    raw = read(ref)
    if raw is None:
        return None
    ext = os.path.splitext(ref.split("?")[0])[1] or ".bin"
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return path


def exists(ref: str) -> bool:
    if not ref:
        return False
    if not _is_remote(ref):
        return os.path.exists(ref)
    try:
        return httpx.head(ref, timeout=_TIMEOUT).status_code < 400
    except httpx.HTTPError:
        return False


def delete(ref: str) -> None:
    """Best effort. A blob that outlives its row costs a fraction of a cent and
    is not worth failing a delete over — the row going is what the user asked
    for, and an orphaned object is invisible to them."""
    if not ref:
        return
    if not _is_remote(ref):
        try:
            os.remove(ref)
        except OSError:
            pass
        return
    try:
        httpx.request(
            "DELETE", f"{BLOB_API}/delete",
            json={"urls": [ref]},
            headers={"authorization": f"Bearer {BLOB_TOKEN}",
                     "x-api-version": BLOB_API_VERSION},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError:
        pass


def _is_remote(ref: str) -> bool:
    return ref.startswith("http://") or ref.startswith("https://")
