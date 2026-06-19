#!/usr/bin/env python3
"""Move dated files out of unknown/ into the right YYYY/MM folder.

For each file under final/<kind>/unknown, this reads the date embedded in the
file itself and, if it's a plausible capture date, moves it into the matching
YYYY/MM folder. Files with no real embedded date stay put. (dedup.py already
does this inline; use this only on output produced before that.)

  python tools/refolder_unknown.py --final "D:/Cleaned/final"            # do it (asks first)
  python tools/refolder_unknown.py --final "D:/Cleaned/final" --dry-run  # just report
  python tools/refolder_unknown.py --final "D:/Cleaned/final" --yes      # no prompt
"""

import argparse
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from takeout_lib import cliutil, config as cfg, exif, filetypes  # noqa: E402
from takeout_lib.dates import MIN_PLAUSIBLE_YEAR                  # noqa: E402


def _unique(folder, name):
    p = folder / name
    if not p.exists():
        return p
    stem, ext, n = p.stem, p.suffix, 1
    while (folder / f"{stem}_{n}{ext}").exists():
        n += 1
    return folder / f"{stem}_{n}{ext}"


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Move files out of unknown/ into YYYY/MM using their embedded date.")
    p.add_argument("--final", required=True, type=Path, help="The final/ folder.")
    p.add_argument("--exiftool", type=Path, default=Path(cfg._default_exiftool()))
    p.add_argument("--min-year", type=int, default=MIN_PLAUSIBLE_YEAR)
    cliutil.add_run_flags(p)
    args = p.parse_args(argv)

    print(f"Reading embedded dates in {args.final}/*/unknown ...")
    moves = []                        # (src, dest_dir)
    by_year = Counter()
    left = 0
    for kind in ("photos", "videos"):
        udir = args.final / kind / "unknown"
        if not udir.exists():
            continue
        files = [q for q in udir.rglob("*")
                 if q.is_file() and q.suffix.lower() in filetypes.MEDIA_EXTS]
        probe = exif.probe_media(args.exiftool, files, args.min_year)
        for fp in files:
            ym = probe.get(exif._norm(fp), {}).get("date")
            if not ym:
                left += 1
                continue
            year, month = ym
            by_year[year] += 1
            moves.append((fp, args.final / kind / str(year) / f"{month:02d}"))

    print(f"\nWould move out of unknown/: {len(moves)}")
    for y in sorted(by_year):
        print(f"   {by_year[y]:6}  -> {y}")
    print(f"Stay in unknown/ (no usable date): {left}")
    if not moves:
        print("\nNothing to move.")
        return 0

    if not cliutil.gate(args, f"About to move {len(moves)} files within {args.final}."):
        return 0

    for src, dest_dir in moves:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(_unique(dest_dir, src.name)))
    print(f"\nMoved {len(moves)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
