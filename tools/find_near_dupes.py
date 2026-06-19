#!/usr/bin/env python3
"""Find visually-similar photos and review them in your browser to pick keepers.

Uses a perceptual hash to group near-duplicates — e.g. the Storage-saver and
original-quality versions of one shot, or re-saves at different compression. It
then opens a local review page showing each group side by side (thumbnails,
sizes, dimensions) with a keep/delete toggle per photo. When you click Apply, the
photos you marked are moved to a near-dup-removed/ folder (recoverable — not a
permanent delete); the keepers stay exactly where they are. Nothing changes until
you click Apply.

If dedup already fingerprinted the library, it writes a launcher that passes
--groups so this opens instantly. Otherwise the fingerprinting happens here.

Needs Pillow (and pillow-heif for .heic):  pip install Pillow pillow-heif

  python tools/find_near_dupes.py --dir "D:/Cleaned/final/photos"            # review in browser
  python tools/find_near_dupes.py --dir "D:/Cleaned/final/photos" --dry-run  # just report counts
  python tools/find_near_dupes.py --dir "D:/Cleaned/final/photos" --groups groups.json

Run before building albums, or rebuild albums afterward — moving a file out of
final/ doesn't update existing album hardlinks.
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from takeout_lib import neardup  # noqa: E402


def _check_pillow():
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("ERROR: this tool needs Pillow.   pip install Pillow")
        print("       (and 'pip install pillow-heif' to read .heic files)")
        sys.exit(1)
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Group visually-similar photos and review them in a browser to keep/delete.")
    p.add_argument("--dir", required=True, type=Path, help="Folder of photos to scan.")
    p.add_argument("--distance", type=int, default=neardup.DEFAULT_DISTANCE,
                   help="Max structural distance to treat as a near-dup (0-256, "
                        "default %(default)s). Lower = stricter.")
    p.add_argument("--color-distance", type=int, default=neardup.DEFAULT_COLOR,
                   help="Max color-signature distance (default %(default)s). A pair "
                        "with a bigger color gap is rejected even if its shape matches; "
                        "lower = stricter.")
    p.add_argument("--groups", type=Path,
                   help="Use a precomputed groups file (from dedup) instead of hashing.")
    p.add_argument("--removed", type=Path,
                   help="Where rejected copies are moved (default: <dir>/../near-dup-removed).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report group counts and exit; open nothing, change nothing.")
    p.add_argument("--no-browser", action="store_true",
                   help="Start the review server but don't auto-open the browser.")
    p.add_argument("--sequential", action="store_true",
                   help="Fingerprint on a single core (default: use all cores).")
    args = p.parse_args(argv)

    if not args.dir.exists():
        print(f"ERROR: folder not found: {args.dir}")
        return 1
    _check_pillow()
    removed = args.removed or (args.dir.parent / "near-dup-removed")

    if args.groups and args.groups.exists():
        print(f"Loading precomputed near-dup groups from {args.groups} ...")
        groups = neardup.load_groups(args.groups, args.dir)
    else:
        print(f"Fingerprinting photos under {args.dir} (this can take a while)...")
        groups = neardup.scan_folder(args.dir, args.distance, args.color_distance,
                                     workers=1 if args.sequential else None)

    extras = sum(len(g) - 1 for g in groups)
    by_size = defaultdict(int)
    for g in groups:
        by_size[len(g)] += 1
    print(f"\nNear-duplicate groups: {len(groups)}   (extra copies: {extras})")
    for sz in sorted(by_size):
        print(f"   {by_size[sz]:6}  groups of {sz}")
    if not groups:
        print("\nNo near-duplicates found.")
        return 0
    if args.dry_run:
        print("\n(dry run — nothing opened or changed)")
        return 0

    from takeout_lib import neardup_review
    moved = neardup_review.serve_review(groups, removed, open_browser=not args.no_browser)
    if moved:
        print(f"\nMoved {moved} copies to {removed} (recoverable — delete that folder when you're sure).")
    else:
        print("\nNothing moved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
