"""Phase 1: scan the source, hash every media file, and index sidecars.

Produces:
  * ``table``         — sha256 -> list of records (one per copy found)
  * ``sidecar_index`` — orphan-sidecar refs keyed by the media name they describe
                        (only sidecars with NO matching media in their own folder,
                        so they can rescue a photo split into another zip)

While hashing we also sniff each file's true type from its header (free — we're
already reading the bytes), stored as ``true_ext`` for the extension-fix step.

The result is cached to ``inventory.json`` so re-runs skip the expensive scan.
"""

import concurrent.futures
import hashlib
import json
import os
import zipfile
from collections import defaultdict
from pathlib import Path

from . import filetypes, parallelism
from .matching import find_sidecar, sidecar_inner


def hash_and_head(fileobj):
    """Stream a file object, returning (sha256_hex, first_header_bytes)."""
    h = hashlib.sha256()
    head = b""
    for chunk in iter(lambda: fileobj.read(65536), b""):
        if not head:
            head = chunk[: filetypes.HEADER_BYTES]
        h.update(chunk)
    return h.hexdigest(), head


def path_bucket(parts):
    """'trash' / 'archive' for files under Google's Trash or Archive folders,
    else None (the normal library)."""
    lower = {p.lower() for p in parts}
    if "trash" in lower:
        return "trash"
    if "archive" in lower:
        return "archive"
    return None


# ── per-source workers (pure: no shared state, safe to run in threads) ───────
#
# Each returns ``(records, orphans, errs)`` where:
#   records — list of (sha256_hex, record_dict), one per media file
#   orphans — list of (inner_media_name, sidecar_ref) for unclaimed .json files
#   errs    — list of (identifier, message) read failures
# The main thread merges these into the shared table/index, so the workers never
# touch shared state and need no locks.

def _process_dir(dirpath, filenames, source_root):
    """Hash the media files directly inside one directory (non-recursive) and
    note its unclaimed sidecars."""
    dp = Path(dirpath)
    name_set = set(filenames)
    claimed = set()
    records, orphans, errs = [], [], []
    for fname in filenames:
        if Path(fname).suffix.lower() not in filetypes.MEDIA_EXTS:
            continue
        fpath = dp / fname
        sidecar = find_sidecar(fname, name_set)
        if sidecar:
            claimed.add(sidecar)
        try:
            with open(fpath, "rb") as f:
                h, head = hash_and_head(f)
        except OSError as e:
            errs.append((str(fpath), str(e)))
            continue
        try:
            rel_parts = fpath.relative_to(source_root).parts
        except ValueError:
            rel_parts = fpath.parts
        records.append((h, {
            "src": "dir",
            "path": str(fpath),
            "name": fname,
            "sidecar": {"kind": "dir", "path": str(dp / sidecar)} if sidecar else None,
            "true_ext": filetypes.sniff_type(head),
            "bucket": path_bucket(rel_parts),
        }))
    for fname in filenames:
        if fname.endswith(".json") and fname not in claimed:
            inner = sidecar_inner(fname)
            if inner:
                orphans.append((inner, {"kind": "dir", "path": str(dp / fname)}))
    return records, orphans, errs


def _process_zip(zpath):
    """Hash every media entry in one zip (a single open) and note its unclaimed
    sidecars."""
    zpath = Path(zpath)
    records, orphans, errs = [], [], []
    try:
        with zipfile.ZipFile(zpath) as zf:
            dir_contents = defaultdict(set)
            for name in zf.namelist():
                dir_contents[Path(name).parent.as_posix()].add(Path(name).name)

            claimed_by_dir = defaultdict(set)
            for info in zf.infolist():
                if info.is_dir():
                    continue
                fname = Path(info.filename).name
                if Path(fname).suffix.lower() not in filetypes.MEDIA_EXTS:
                    continue
                parent = Path(info.filename).parent.as_posix()
                sidecar_fname = find_sidecar(fname, dir_contents[parent])
                if sidecar_fname:
                    claimed_by_dir[parent].add(sidecar_fname)
                ident = f"{zpath}::{info.filename}"
                try:
                    with zf.open(info) as f:
                        h, head = hash_and_head(f)
                except Exception as e:
                    errs.append((ident, str(e)))
                    continue
                sidecar_ref = (
                    {"kind": "zip", "zip": str(zpath),
                     "entry": (Path(info.filename).parent / sidecar_fname).as_posix()}
                    if sidecar_fname else None
                )
                records.append((h, {
                    "src": "zip",
                    "zip": str(zpath),
                    "entry": info.filename,
                    "name": fname,
                    "sidecar": sidecar_ref,
                    "true_ext": filetypes.sniff_type(head),
                    "bucket": path_bucket(Path(info.filename).parts),
                }))

            for info in zf.infolist():
                if info.is_dir() or not info.filename.endswith(".json"):
                    continue
                parent = Path(info.filename).parent.as_posix()
                jname = Path(info.filename).name
                if jname not in claimed_by_dir[parent]:
                    inner = sidecar_inner(jname)
                    if inner:
                        orphans.append((inner, {"kind": "zip", "zip": str(zpath),
                                                "entry": info.filename}))
    except zipfile.BadZipFile:
        errs.append((str(zpath), "bad zip file"))
    return records, orphans, errs


def _collect_sources(root):
    """Walk the tree once and list the units of work: one task per directory that
    holds media/sidecars, and one task per zip. Cheap — it reads no file bytes."""
    dir_sources, zip_sources = [], []
    for dp_str, _, filenames in os.walk(root):
        has_payload = False
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            if ext == ".zip":
                zip_sources.append(str(Path(dp_str) / fname))
            elif ext in filetypes.MEDIA_EXTS or fname.endswith(".json"):
                has_payload = True
        if has_payload:
            dir_sources.append((dp_str, filenames))
    return dir_sources, zip_sources


def _run_source(task, source_root):
    kind, payload = task
    if kind == "dir":
        dirpath, filenames = payload
        return _process_dir(dirpath, filenames, source_root)
    return _process_zip(payload)


def scan(config, errors):
    """Return (table, sidecar_index), loading from cache unless --rescan."""
    inv_path = config.inventory
    if inv_path.exists() and not config.rescan:
        print("\n[1/5] Loading cached inventory (pass --rescan to re-scan)...")
        with open(inv_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if "table" not in raw:
            print("  Cached inventory is from an older version — re-scan with --rescan.")
            raise SystemExit(1)
        table = defaultdict(list, raw["table"])
        sidecar_index = defaultdict(list, raw.get("sidecars", {}))
        count = sum(len(v) for v in table.values())
        dupes = sum(len(v) - 1 for v in table.values() if len(v) > 1)
        print(f"  {count} files ({len(table)} unique, {dupes} redundant copies)")
        return table, sidecar_index

    workers, reason = parallelism.plan_io(config)
    print("\n[1/5] Scanning for media files...")
    print(f"  Speed: {reason}")

    dir_sources, zip_sources = _collect_sources(config.source)
    tasks = ([("dir", d) for d in dir_sources]
             + [("zip", z) for z in zip_sources])

    table = defaultdict(list)
    sidecar_index = defaultdict(list)
    count = [0]
    done = [0]
    total_sources = len(tasks)

    def merge(result):
        records, orphans, errs = result
        for h, record in records:
            table[h].append(record)
            count[0] += 1
        for inner, ref in orphans:
            sidecar_index[inner].append(ref)
        for ident, msg in errs:
            errors["read_error"].append((ident, msg))
            print(f"\n  SKIP (read error): {ident}: {msg}")
        # One folder/zip finished. Report by source so progress still moves even
        # while a big zip is mid-hash (the file counter alone can sit still for
        # minutes on a multi-GB zip).
        done[0] += 1
        print(f"  scanned {done[0]}/{total_sources} folders+zips, "
              f"{count[0]} files hashed...", end="\r", flush=True)

    src_root = config.source
    if workers <= 1:
        for task in tasks:
            merge(_run_source(task, src_root))
    else:
        # Threads (not processes): the work is dominated by blocking disk reads,
        # during which the GIL is released, so threads overlap I/O without the
        # cost of pickling records back from subprocesses. ``map`` keeps results
        # in task order, so the cached inventory is deterministic.
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for result in ex.map(lambda t: _run_source(t, src_root), tasks):
                merge(result)

    inv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump({"table": dict(table), "sidecars": dict(sidecar_index)}, f)

    dupes = sum(len(v) - 1 for v in table.values() if len(v) > 1)
    print(f"\n  {count[0]} total media files scanned")
    print(f"  {len(table)} unique files (by SHA-256 hash)")
    print(f"  {dupes} redundant copies will be dropped")
    print(f"  {len(sidecar_index)} orphan sidecar names indexed for cross-folder fallback")
    print(f"  Inventory saved to {inv_path}")
    return table, sidecar_index
