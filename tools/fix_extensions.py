#!/usr/bin/env python3
"""Rename files whose extension doesn't match their real content.

Google Takeout sometimes stores a JPEG named .heic, a .mov that's really an mp4,
and so on. This detects each file's true type by its content (magic bytes) and
renames it to match. RAW and anything it can't positively identify are left
untouched, so nothing is ever mis-renamed. (dedup.py already does this inline;
use this only on folders produced before that, or any stray media folder.)

  python tools/fix_extensions.py --dir "D:/Cleaned/final"            # do it (asks first)
  python tools/fix_extensions.py --dir "D:/Cleaned/final" --dry-run  # just report
  python tools/fix_extensions.py --dir "D:/Cleaned/final" --yes      # no prompt
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from takeout_lib import cliutil, filetypes  # noqa: E402


def _unique(path):
    if not path.exists():
        return path
    stem, ext, n = path.stem, path.suffix, 1
    while True:
        p = path.with_name(f"{stem}_{n}{ext}")
        if not p.exists():
            return p
        n += 1


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Rename files whose extension doesn't match their actual content.")
    p.add_argument("--dir", required=True, type=Path, help="Folder of media to check.")
    cliutil.add_run_flags(p)
    args = p.parse_args(argv)
    if not args.dir.exists():
        print(f"ERROR: folder not found: {args.dir}")
        return 1

    print(f"Checking file extensions under {args.dir} ...")
    renames = []                      # (path, corrected_name)
    mismatches = Counter()
    checked = 0
    for path in args.dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in filetypes.MEDIA_EXTS:
            continue
        checked += 1
        with open(path, "rb") as f:
            true_ext = filetypes.sniff_type(f.read(filetypes.HEADER_BYTES))
        new_name = filetypes.corrected_name(path.name, true_ext)
        if new_name != path.name:
            renames.append((path, new_name))
            mismatches[f".{path.suffix.lower().lstrip('.') or '(none)'} -> .{true_ext}"] += 1

    print(f"\nFiles checked:             {checked}")
    print(f"Mistaken extensions found: {len(renames)}")
    for k, v in sorted(mismatches.items(), key=lambda x: -x[1]):
        print(f"   {v:6}  {k}")
    if not renames:
        print("\nNothing to rename.")
        return 0

    if not cliutil.gate(args, f"About to rename {len(renames)} files in {args.dir}."):
        return 0

    renamed = 0
    for path, new_name in renames:
        try:
            path.rename(_unique(path.with_name(new_name)))
            renamed += 1
        except OSError as e:
            print(f"  WARNING: could not rename a file ({e.__class__.__name__})")
    print(f"\nRenamed {renamed} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
