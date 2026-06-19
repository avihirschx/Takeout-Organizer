"""Post-run verification report.

Cross-checks the inventory, the extraction checkpoint, and what's actually on
disk; spot-checks embedded dates with exiftool; and prints a SAFE TO DELETE /
DO NOT DELETE verdict so you know whether it's safe to remove the source.
"""

import json
import random
from pathlib import Path

from . import exif, filetypes

MEDIA_EXTS = filetypes.MEDIA_EXTS
PHOTO_EXTS = filetypes.PHOTO_EXTS
VIDEO_EXTS = filetypes.VIDEO_EXTS
SAMPLE_SIZE = 50


class Report:
    def __init__(self):
        self.lines = []
        self.failures = []

    def log(self, s=""):
        print(s)
        self.lines.append(s)

    def fail(self, s):
        self.failures.append(s)
        self.log(f"  FAIL: {s}")

    def section(self, title):
        self.log()
        self.log("-" * 60)
        self.log(title)
        self.log("-" * 60)


def _media(root):
    return [p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in MEDIA_EXTS]


def check_counts(rep, inventory_path, done_path, final):
    rep.section("1. Count check")
    missing = [p for p in (inventory_path, done_path) if not p.exists()]
    if missing:
        for p in missing:
            rep.fail(f"{p.name} not found — dedup may not have finished")
        return None

    with open(inventory_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    table = raw["table"] if isinstance(raw, dict) and "table" in raw else raw
    with open(done_path, "r", encoding="utf-8") as f:
        done_set = set(json.load(f))

    rep.log(f"  Unique files in inventory:   {len(table)}")
    rep.log(f"  Hashes in extracted.json:    {len(done_set)}")
    rep.log("  Counting media files in final...")
    final_media = _media(final)
    rep.log(f"  Actual media files in final: {len(final_media)}")

    if len(done_set) == len(final_media):
        rep.log("  OK — extracted count matches actual file count")
    else:
        diff = len(done_set) - len(final_media)
        if diff > 0:
            rep.fail(f"{diff} hash(es) in extracted.json have no file in final")
        else:
            rep.fail(f"{abs(diff)} extra file(s) in final not tracked by extracted.json")

    orphaned = done_set - set(table.keys())
    if orphaned:
        rep.log(f"  WARNING: {len(orphaned)} extracted hash(es) not in inventory (stale)")
    return final_media


def check_unknown(rep, final_media, final):
    rep.section("2. Files with no date (unknown/)")
    unknown = [p for p in final_media if "unknown" in p.parts]
    if not unknown:
        rep.log("  OK — no files in unknown/")
        return
    rep.log(f"  {len(unknown)} file(s) have no usable timestamp (missing, or an")
    rep.log("  implausible camera-default/future date that was rejected):")
    for p in unknown[:30]:
        rep.log(f"    {p.relative_to(final)}")
    if len(unknown) > 30:
        rep.log(f"    ... and {len(unknown) - 30} more")
    rep.log()
    rep.log("  These are safely in final but undated. Review before deleting source.")


def check_errors_dir(rep, errors_dir):
    rep.section("3. Errors directory")
    if not errors_dir.exists() or not any(errors_dir.rglob("*")):
        rep.log("  OK — errors directory is empty (nothing skipped)")
        return
    error_files = [p for p in errors_dir.rglob("*") if p.is_file()]
    rep.log(f"  {len(error_files)} file(s) were skipped and copied here:")
    for p in error_files[:30]:
        rep.log(f"    {p.relative_to(errors_dir)}")
    if len(error_files) > 30:
        rep.log(f"    ... and {len(error_files) - 30} more")
    rep.log()
    rep.log(f"  Review: {errors_dir}")
    rep.log("  Do NOT delete source until you've decided what to do with these.")


def check_metadata(rep, exiftool, final_media, final):
    rep.section("4. Metadata spot-check (capture date on random sample)")
    if not exiftool or not Path(exiftool).exists():
        rep.log(f"  SKIP: exiftool not found at {exiftool}")
        return
    eligible = [p for p in final_media
                if p.suffix.lower() in MEDIA_EXTS and "unknown" not in p.parts]
    if not eligible:
        rep.log("  No eligible media to sample.")
        return

    sample = random.sample(eligible, min(SAMPLE_SIZE, len(eligible)))
    rep.log(f"  Checking {len(sample)} random files (from {len(eligible)} eligible)...")
    # One batched read (photos use DateTimeOriginal, videos QuickTime CreateDate).
    rows = exif.read_json(exiftool, sample, ["DateTimeOriginal", "CreateDate"])
    dated = {exif._norm(e["SourceFile"]) for e in rows
             if e.get("DateTimeOriginal") or e.get("CreateDate")}
    missing = [p for p in sample if exif._norm(p) not in dated]

    if not missing:
        rep.log(f"  OK — all {len(sample)} sampled files have a capture date")
    else:
        rep.fail(f"{len(missing)}/{len(sample)} sampled files are missing a capture date")
        rep.log("  This suggests the embed phase had issues; re-run to retry.")


def report(output, exiftool, report_path=None):
    """Run all checks for an output dir. Returns True if SAFE TO DELETE."""
    final = output / "final"
    errors_dir = output / "Deduplication Errors"
    rep = Report()
    rep.log("=" * 60)
    rep.log("Takeout Organizer — Verification Report")
    rep.log("=" * 60)

    final_media = check_counts(rep, output / "inventory.json",
                               output / "extracted.json", final)
    if final_media is None:
        rep.log("\nCannot continue — run dedup.py first.")
    else:
        check_unknown(rep, final_media, final)
        check_errors_dir(rep, errors_dir)
        check_metadata(rep, exiftool, final_media, final)

    rep.section("Verdict")
    safe = not rep.failures
    if safe:
        rep.log()
        rep.log("  All checks passed.")
        rep.log("  SAFE TO DELETE source — but move to Recycle Bin first and confirm")
        rep.log("  your upload looks complete before emptying it.")
    else:
        rep.log()
        rep.log(f"  {len(rep.failures)} check(s) failed:")
        for f in rep.failures:
            rep.log(f"    - {f}")
        rep.log()
        rep.log("  DO NOT DELETE source yet. Resolve the issues above first.")

    if report_path:
        rep.log()
        rep.log(f"Full report written to: {report_path}")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(rep.lines), encoding="utf-8")
    return safe
