"""Location-independent sidecar references.

A *ref* describes where a JSON sidecar lives so it can be read no matter where
its media file is — which is what lets a photo in one zip use a sidecar that
Takeout split into another. Two shapes::

    {"kind": "dir", "path": "C:/.../IMG.jpg.json"}
    {"kind": "zip", "zip": "C:/.../takeout.zip", "entry": "Album/IMG.jpg.json"}
"""

import json
import zipfile
from pathlib import Path

from . import dates


def read_sidecar_bytes(ref, open_zip=None, open_zip_path=None):
    """Read a sidecar's raw bytes from its ref, or None on failure. Reuses an
    already-open ZipFile when the ref points into the same zip.
    """
    if not ref:
        return None
    try:
        if ref["kind"] == "dir":
            with open(ref["path"], "rb") as f:
                return f.read()
        if open_zip is not None and ref.get("zip") == open_zip_path:
            return open_zip.read(ref["entry"])
        with zipfile.ZipFile(ref["zip"]) as zf:
            return zf.read(ref["entry"])
    except Exception:
        return None


def ref_basename(ref):
    if ref["kind"] == "dir":
        return Path(ref["path"]).name
    return Path(ref["entry"]).name


def parse_sidecar(raw_bytes):
    """Parse sidecar bytes into a dict, or {} on any failure."""
    if not raw_bytes:
        return {}
    try:
        return json.loads(raw_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return {}


def choose_sidecar(refs, min_year=dates.MIN_PLAUSIBLE_YEAR, max_year=None):
    """Pick the best sidecar among same-named candidates: prefer one whose
    ``photoTakenTime`` is a plausible capture time, else the first (sorted for
    determinism). Duplicate copies share metadata, so any choice is fine; for
    genuinely different photos sharing a name this may pick a slightly off date,
    but no media is ever lost.
    """
    refs = sorted(refs, key=lambda r: (ref_basename(r), str(r.get("path") or r.get("entry"))))
    if len(refs) == 1:
        return refs[0]
    for ref in refs:
        data = parse_sidecar(read_sidecar_bytes(ref))
        ts = dates.ts_from_sidecar(data, "photoTakenTime")
        if dates.plausible_ts(ts, min_year, max_year):
            return ref
    return refs[0]
