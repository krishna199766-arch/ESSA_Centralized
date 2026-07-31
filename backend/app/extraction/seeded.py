"""
Seeded provider — returns the human-verified ground truth for the bundled
sample invoices, matched by file content hash.

Purpose: (1) a deterministic, offline demo path that shows the *target* output
quality regardless of whether a vision model or OCR is configured, and (2) a
regression fixture set. In production this provider simply never matches a real
new upload, so the engine falls through to the configured live provider.
"""
import os
import json
import hashlib
from typing import Optional, Dict, Any
from .base import ExtractionProvider, ProviderResult
from ..config import GROUND_TRUTH_DIR, SAMPLE_DIR


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class SeededProvider(ExtractionProvider):
    name = "seeded"

    def __init__(self):
        self._by_hash: Dict[str, Dict[str, Any]] = {}
        self._by_srcfile: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not os.path.isdir(GROUND_TRUTH_DIR):
            return
        for fn in os.listdir(GROUND_TRUTH_DIR):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(GROUND_TRUTH_DIR, fn)) as f:
                doc = json.load(f)
            src = doc.get("source_file")
            self._by_srcfile[src] = doc
            sample_path = os.path.join(SAMPLE_DIR, src) if src else None
            if sample_path and os.path.exists(sample_path):
                self._by_hash[_sha256(sample_path)] = doc

    def match(self, image_path: str) -> Optional[Dict[str, Any]]:
        try:
            h = _sha256(image_path)
        except OSError:
            return None
        if h in self._by_hash:
            return self._by_hash[h]
        # fallback: match by basename (copied/renamed samples)
        base = os.path.basename(image_path)
        return self._by_srcfile.get(base)

    def available(self) -> bool:
        return bool(self._by_srcfile)

    def extract(self, image_path: str, profile: Optional[Dict[str, Any]] = None,
                ocr_text: Optional[str] = None) -> ProviderResult:
        doc = self.match(image_path)
        if not doc:
            return ProviderResult(data={}, provider=self.name, confidence=0.0,
                                  notes=["no seeded match"])
        data = json.loads(json.dumps(doc))   # deep copy
        data.pop("source_file", None)
        return ProviderResult(data=data, provider=self.name, confidence=0.99,
                              notes=["matched bundled sample by content hash"])
