"""Phases 2-5 and the top-level run().

Phase 2  pick one copy per unique file (sidecar in own folder wins; else an
         orphan sidecar from elsewhere; else the errors folder)
Phase 3  copy/extract to final/<kind>/YYYY/MM, dated from the sidecar
Phase 4  embed (in exif.embed)
Phase 5  verify counts
"""

import hashlib
import json
import os
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path

from . import exif, filetypes, inventory, livephoto
from .dates import date_from_sidecar
from .matching import global_lookup, sidecar_dest_name
from .sidecars import choose_sidecar, parse_sidecar, read_sidecar_bytes, ref_basename

_SKIP_DIRS = {"Takeout", "Google Photos"}

# Folders that are not user albums: structural, system, and the date timeline.
_NON_ALBUM = {"takeout", "google photos", "trash", "archive"}
_TIMELINE_RE = re.compile(r"^photos from \d{4}$")
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _album_for(r):
    """The album a record's source folder represents, or None for the timeline
    / structural / system folders."""
    parent = (Path(r["path"]).parent.name if r["src"] == "dir"
              else Path(r["entry"]).parent.name)
    low = parent.lower()
    if not parent or low in _NON_ALBUM or _TIMELINE_RE.match(low):
        return None
    return parent


def albums_for(records):
    return sorted({a for a in (_album_for(r) for r in records) if a})


def _safe_album(name):
    return _BAD_NAME.sub("_", name).strip().rstrip(". ") or "_album"


def new_errors():
    return {"read_error": [], "missing_sidecar": [], "extract_error": []}


# ── small path helpers ──────────────────────────────────────────────────────

def record_id(r):
    if r["src"] == "dir":
        return r["path"]
    return f"{r['zip']}::{r['entry']}"


def unique_path(folder, filename):
    p = folder / filename
    if not p.exists():
        return p
    stem, ext = Path(filename).stem, Path(filename).suffix
    n = 1
    while True:
        p = folder / f"{stem}_{n}{ext}"
        if not p.exists():
            return p
        n += 1


def _file_hash(path):
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256()
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
            return h.hexdigest()
    except OSError:
        return None


def resolve_dest(folder, filename, want_hash):
    """unique_path, but resume-safe: if a target name already holds the winner's
    exact content (from a prior, possibly crashed run), reuse it instead of
    making a duplicate. Returns (path, already_present)."""
    p = folder / filename
    if not p.exists():
        return p, False
    if _file_hash(p) == want_hash:
        return p, True
    stem, ext = Path(filename).stem, Path(filename).suffix
    n = 1
    while True:
        p = folder / f"{stem}_{n}{ext}"
        if not p.exists():
            return p, False
        if _file_hash(p) == want_hash:
            return p, True
        n += 1


def errors_dest(r, config):
    if r["src"] == "dir":
        parts = [p for p in Path(r["path"]).relative_to(config.source).parts
                 if p not in _SKIP_DIRS]
    else:
        zip_rel = Path(r["zip"]).relative_to(config.source)
        top = zip_rel.stem if len(zip_rel.parts) == 1 else zip_rel.parts[0]
        parts = [top] + [p for p in Path(r["entry"]).parts if p not in _SKIP_DIRS]
    dest_dir = config.errors_dir / Path(*parts[:-1]) if len(parts) > 1 else config.errors_dir
    return unique_path(dest_dir, parts[-1])


def copy_to_errors(r, config):
    try:
        dest = errors_dest(r, config)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if r["src"] == "dir":
            shutil.copy2(r["path"], dest)
        else:
            with zipfile.ZipFile(r["zip"]) as zf, zf.open(r["entry"]) as src, \
                    open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception as e:
        print(f"\n  WARNING: could not copy {r['name']} to errors dir: {e}")


# ── Phase 2 ─────────────────────────────────────────────────────────────────

def hash_bucket(records):
    """Effective bucket for a unique file across all its copies. A copy in the
    normal library wins over archive, which wins over trash — so an archived
    photo that also sits in an album stays in the main library."""
    buckets = {r.get("bucket") for r in records}
    if None in buckets:
        return None
    if "archive" in buckets:
        return "archive"
    return "trash"


def select_winners(table, sidecar_index, config):
    """Pick one copy per unique file. Returns (winners, candidates):

      * winners    — have a sidecar (in their own folder, or an orphan recovered
                     from elsewhere). Always kept.
      * candidates — no sidecar anywhere. Kept only if they carry a plausible
                     embedded date (decided in phase 3); otherwise -> errors.
    """
    print("\n[2/5] Selecting one copy per unique file...")
    winners = []
    candidates = []
    recovered = 0

    for h, records in table.items():
        bucket = hash_bucket(records)
        albums = albums_for(records)
        with_sidecar = [r for r in records if r.get("sidecar")]
        if with_sidecar:
            winner = dict(with_sidecar[0])
            winner.update(hash=h, bucket=bucket, albums=albums)
            winners.append(winner)
            continue

        winner = None
        for r in records:
            refs = global_lookup(r["name"], sidecar_index)
            if refs:
                winner = dict(r)
                winner["sidecar"] = choose_sidecar(refs, config.min_year)
                winner["sidecar_recovered"] = True
                winner.update(hash=h, bucket=bucket, albums=albums)
                break
        if winner is not None:
            winners.append(winner)
            recovered += 1
            continue

        rep = dict(records[0])
        rep.update(hash=h, bucket=bucket, albums=albums)
        candidates.append(rep)

    print(f"  {len(winners)} files with a sidecar")
    if recovered:
        print(f"  {recovered} matched to a sidecar from another folder/zip (cross-folder fallback)")
    print(f"  {len(candidates)} files with no sidecar (kept only if they carry an embedded date)")
    return winners, candidates


# ── Phase 3 ─────────────────────────────────────────────────────────────────

def _load_set(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def extract(winners, candidates, config, errors):
    """Phase 3: place each unique file under final/<kind>/YYYY/MM.

    Date waterfall: a plausible sidecar date is used directly; otherwise the
    file's own embedded date is read (one batched exiftool call over a staging
    area) and used if plausible; otherwise the file lands in unknown/ (if it has
    a sidecar) or, for no-sidecar files, in the errors folder unless
    --include-undated. Returns the number of files placed in final.
    """
    final = config.final
    diverted_path = config.output / "diverted.json"
    print(f"\n[3/5] Extracting to {final}...")
    final.mkdir(parents=True, exist_ok=True)
    if config.staging.exists():
        shutil.rmtree(config.staging, ignore_errors=True)

    items = winners + candidates
    stems = livephoto.photo_stems(items)         # still names, for motion pairing
    done_set = _load_set(config.done_file)       # placed in final
    diverted_set = _load_set(diverted_path)      # processed but NOT in final
    placements = {}                              # hash -> output-relative final path
    if config.placements_file.exists():
        try:
            placements = json.loads(config.placements_file.read_text(encoding="utf-8"))
        except Exception:
            placements = {}
    processed = done_set | diverted_set
    pending = [w for w in items if w["hash"] not in processed]
    already = len(items) - len(pending)
    if already:
        print(f"  Resuming: {already}/{len(items)} already processed, skipping...")

    total = len(items)
    placed = [len(done_set & {w["hash"] for w in items})]
    motion = [0]
    archived = [0]
    trashed = [0]
    progress = [already]

    def save():
        with open(config.done_file, "w", encoding="utf-8") as f:
            json.dump(list(done_set), f)
        with open(diverted_path, "w", encoding="utf-8") as f:
            json.dump(list(diverted_set), f)
        with open(config.placements_file, "w", encoding="utf-8") as f:
            json.dump(placements, f)

    def tick():
        progress[0] += 1
        if progress[0] % 1000 == 0:
            save()
        if progress[0] % 500 == 0:
            print(f"  {progress[0]}/{total} done...", end="\r", flush=True)

    def mark_placed(r):
        done_set.add(r["hash"])
        placed[0] += 1
        tick()

    def mark_diverted(r):
        diverted_set.add(r["hash"])
        tick()

    def mark_errored(r, reason):
        diverted_set.add(r["hash"])
        errors["missing_sidecar"].append((record_id(r), reason))
        tick()

    def mark_failed(r, e):
        diverted_set.add(r["hash"])
        errors["extract_error"].append((record_id(r), str(e)))
        print(f"\n  ERROR {r['name']}: {e}")
        copy_to_errors(r, config)
        tick()

    def out_name(r):
        if config.fix_extensions:
            return filetypes.corrected_name(r["name"], r.get("true_ext"))
        return r["name"]

    def dest_for(r, year, month):
        name = out_name(r)
        # Trash/Archive media go to their own root; everything else to final.
        base = config.root_for_bucket(r.get("bucket")) / filetypes.media_kind(Path(name).suffix)
        dest_dir = base / str(year) / f"{month:02d}" if year else base / "unknown"
        dest_dir.mkdir(parents=True, exist_ok=True)
        return resolve_dest(dest_dir, name, r["hash"])

    def mark_result(r, dest):
        """Count a successful placement by where it landed, and remember the
        final path of normal-library files so albums can be rebuilt later."""
        b = r.get("bucket")
        if b == "archive":
            archived[0] += 1
            mark_diverted(r)
        elif b == "trash":
            trashed[0] += 1
            mark_diverted(r)
        else:
            placements[r["hash"]] = str(dest.relative_to(config.output)).replace("\\", "/")
            mark_placed(r)

    def write_sidecar(dest, sidecar_bytes, ref):
        if ref is None or sidecar_bytes is None:
            return
        sd = dest.parent / sidecar_dest_name(dest.name, ref_basename(ref))
        if not sd.exists():
            sd.write_bytes(sidecar_bytes)

    # Items whose sidecar date is missing/bogus need their embedded date read.
    # Each entry: (record, sidecar_bytes, read_path, is_staged).
    needs_exif = []

    def stage_path(r):
        config.staging.mkdir(parents=True, exist_ok=True)
        ext = Path(out_name(r)).suffix
        return config.staging / f"{r['hash']}{ext}"

    # ── pass 1: dir items (fast path placed; slow path noted, read from source) ─
    for r in (r for r in pending if r["src"] == "dir"):
        ref = r.get("sidecar")
        sb = read_sidecar_bytes(ref)
        year, month = date_from_sidecar(parse_sidecar(sb), config.min_year)
        if year:
            try:
                dest, already_there = dest_for(r, year, month)
                if not already_there:
                    shutil.copy2(r["path"], dest)
                write_sidecar(dest, sb, ref)
                mark_result(r, dest)
            except Exception as e:
                mark_failed(r, e)
        else:
            needs_exif.append((r, sb, r["path"], False))

    # ── pass 1: zip items (one open per zip) ───────────────────────────────────
    by_zip = defaultdict(list)
    for r in pending:
        if r["src"] == "zip":
            by_zip[r["zip"]].append(r)

    for zip_path, group in by_zip.items():
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for r in group:
                    ref = r.get("sidecar")
                    sb = read_sidecar_bytes(ref, open_zip=zf, open_zip_path=zip_path)
                    year, month = date_from_sidecar(parse_sidecar(sb), config.min_year)
                    try:
                        if year:
                            dest, already_there = dest_for(r, year, month)
                            if not already_there:
                                with zf.open(r["entry"]) as s, open(dest, "wb") as d:
                                    shutil.copyfileobj(s, d)
                            write_sidecar(dest, sb, ref)
                            mark_result(r, dest)
                        else:
                            sp = stage_path(r)
                            with zf.open(r["entry"]) as s, open(sp, "wb") as d:
                                shutil.copyfileobj(s, d)
                            needs_exif.append((r, sb, str(sp), True))
                    except Exception as e:
                        mark_failed(r, e)
        except zipfile.BadZipFile as e:
            for r in group:
                mark_failed(r, Exception(f"zip unreadable: {e}"))

    # ── pass 2: probe everything that lacked a sidecar date (embedded date +
    # duration), handle Live Photo motion clips, then place by the waterfall ───
    if needs_exif:
        probe = exif.probe_media(
            config.exiftool, [rp for _, _, rp, _ in needs_exif], config.min_year)
        for r, sb, read_path, staged in needs_exif:
            info = probe.get(exif._norm(read_path), {})
            ym = info.get("date")
            has_sidecar = r.get("sidecar") is not None
            try:
                # Live Photo motion clip: a no-sidecar short video paired with a still.
                if (not has_sidecar and config.live_motion != "keep"
                        and livephoto.is_motion_clip(r["name"], info.get("duration"), stems)):
                    motion[0] += 1
                    mode = config.live_motion
                    if mode == "errors":
                        copy_to_errors(r, config)
                        if staged:
                            os.remove(read_path)
                        mark_errored(r, "live photo motion clip")
                    elif mode == "drop":
                        if staged:
                            os.remove(read_path)
                        mark_diverted(r)
                    else:  # archive
                        dest = unique_path(config.live_motion_dir, out_name(r))
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if staged:
                            shutil.move(read_path, dest)
                        else:
                            shutil.copy2(r["path"], dest)
                        mark_diverted(r)
                    continue

                if ym:
                    year, month = ym
                elif has_sidecar or config.include_undated:
                    year, month = None, None  # unknown/
                else:
                    copy_to_errors(r, config)
                    if staged:
                        os.remove(read_path)
                    mark_errored(r, "no sidecar and no embedded date")
                    continue
                dest, already_there = dest_for(r, year, month)
                if not already_there:
                    if staged:
                        shutil.move(read_path, dest)
                    else:
                        shutil.copy2(r["path"], dest)
                elif staged:
                    os.remove(read_path)
                write_sidecar(dest, sb, r.get("sidecar"))
                mark_result(r, dest)
            except Exception as e:
                mark_failed(r, e)

    if config.staging.exists():
        shutil.rmtree(config.staging, ignore_errors=True)
    save()
    print(f"\n  {placed[0]} placed in final, {len(diverted_set)} diverted")
    if motion[0]:
        print(f"  {motion[0]} Live Photo motion clips ({config.live_motion})")
    if archived[0]:
        print(f"  {archived[0]} archived -> {config.archive_dir.name}/")
    if trashed[0]:
        print(f"  {trashed[0]} trashed -> {config.trash_dir.name}/")
    return placed[0], placements


# ── Phase 5 ─────────────────────────────────────────────────────────────────

def verify_counts(expected, config):
    print("\n[5/5] Verifying output...")
    actual = sum(1 for p in config.final.rglob("*")
                 if p.is_file() and p.suffix.lower() in filetypes.MEDIA_EXTS)
    print(f"  Expected : {expected}")
    print(f"  Found    : {actual}  (in {config.final})")
    if actual == expected:
        print("  OK — counts match")
    else:
        diff = expected - actual
        if diff > 0:
            print(f"  WARNING: {diff} files missing from final output — check errors.log")
        else:
            print(f"  WARNING: {abs(diff)} unexpected extra files in final output")
    return actual


# ── error log ───────────────────────────────────────────────────────────────

_LABELS = {
    "read_error": "Read errors (phase 1) — could not open or hash; nothing copied",
    "missing_sidecar": "Missing JSON sidecar (phase 2) — file skipped; copied to errors folder",
    "extract_error": "Extract errors (phase 3) — copy failed; copied to errors folder",
}


def write_error_log(config, errors):
    config.log.parent.mkdir(parents=True, exist_ok=True)
    total = sum(len(v) for v in errors.values())
    with open(config.log, "w", encoding="utf-8") as f:
        f.write(f"Total errors: {total}\n" + "=" * 60 + "\n\n")
        for key, entries in errors.items():
            f.write(f"{_LABELS[key]}: {len(entries)}\n")
            for ident, detail in entries:
                f.write(f"  {ident}\n    reason: {detail}\n")
            f.write("\n")
    if total == 0:
        print("\n  No errors.")
    else:
        print(f"\n  {total} total errors — see: {config.log}")
        for key, entries in errors.items():
            if entries:
                print(f"    {_LABELS[key]}: {len(entries)}")
        print(f"  Inspect files at: {config.errors_dir}")


# ── orchestration ───────────────────────────────────────────────────────────

def dry_run_summary(table, winners, candidates):
    total = sum(len(v) for v in table.values())
    unique = len(table)
    recovered = sum(1 for w in winners if w.get("sidecar_recovered"))
    print("\n[dry run] No media written. What a real run would do:")
    print(f"  Total media files scanned : {total}")
    print(f"  Unique (after dedup)      : {unique}")
    print(f"  Redundant copies dropped  : {total - unique}")
    print(f"  With a sidecar            : {len(winners)}  ({recovered} recovered cross-folder)")
    print(f"  Without a sidecar         : {len(candidates)}  (kept only if they carry an embedded date)")
    print("\n  (the inventory was cached, so a real run skips the scan)")
    print("  Re-run without --dry-run to write the cleaned library.")


def build_albums(winners, candidates, placements, config):
    """Rebuild album membership as a parallel folder tree (hardlinks by default —
    no extra disk space) plus an albums.json manifest. Runs after embed so the
    linked/copied files carry the final metadata (exiftool replaces the inode)."""
    if config.albums == "none":
        return
    album_map = {it["hash"]: it["albums"]
                 for it in (winners + candidates) if it.get("albums")}
    if not album_map:
        return

    print("\nRebuilding albums...")
    manifest = defaultdict(list)
    linked = 0
    link_failed = False
    for h, rel in placements.items():
        albums = album_map.get(h)
        if not albums:
            continue
        src = config.output / rel
        if not src.exists():
            continue
        for album in albums:
            manifest[album].append(rel)
            if config.albums in ("link", "copy"):
                adir = config.albums_dir / _safe_album(album)
                adir.mkdir(parents=True, exist_ok=True)
                target = unique_path(adir, src.name)
                try:
                    if config.albums == "link":
                        os.link(src, target)
                    else:
                        shutil.copy2(src, target)
                    linked += 1
                except OSError:
                    link_failed = True

    with open(config.albums_manifest, "w", encoding="utf-8") as f:
        json.dump({a: sorted(v) for a, v in sorted(manifest.items())},
                  f, indent=1, ensure_ascii=False)
    print(f"  {len(manifest)} albums recorded in {config.albums_manifest.name}")
    if config.albums in ("link", "copy"):
        verb = "linked" if config.albums == "link" else "copied"
        print(f"  {linked} files {verb} into {config.albums_dir.name}/")
    if link_failed:
        print("  NOTE: some hardlinks failed (filesystem may not support them); "
              "albums.json still lists every file.")


def run(config):
    errors = new_errors()
    table, sidecar_index = inventory.scan(config, errors)
    winners, candidates = select_winners(table, sidecar_index, config)
    if config.dry_run:
        dry_run_summary(table, winners, candidates)
        return errors
    placed, placements = extract(winners, candidates, config, errors)
    exif.embed(config)
    verify_counts(placed, config)
    build_albums(winners, candidates, placements, config)
    write_error_log(config, errors)
    return errors
