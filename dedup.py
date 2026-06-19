#!/usr/bin/env python3
"""Deduplicate a Google Photos Takeout and attach its sidecar metadata.

Reads zips and/or extracted folders under --source, writes a cleaned, dated,
deduplicated library under --output/final (photos and videos split). The source
is only ever read — never modified.

    python dedup.py --source "D:/Takeout" --output "D:/Cleaned"
    python dedup.py --source "D:/Takeout" --output "D:/Cleaned" --rescan

Then check it with verify.py before deleting anything.
"""

import sys
from pathlib import Path

from takeout_lib import config as cfg
from takeout_lib import neardup_prep, pipeline


class _Tee:
    """Mirror stdout into a log file."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, s):
        self._stdout.write(s)
        self._file.write(s)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        sys.stdout = self._stdout
        self._file.close()


def main(argv=None):
    config = cfg.config_from_args(
        argv, prog="dedup.py",
        description="Deduplicate a Google Photos Takeout and attach its metadata.")

    problems = cfg.validate(config)
    if problems:
        for p in problems:
            print(f"ERROR: {p}")
        return 1

    config.output.mkdir(parents=True, exist_ok=True)
    tee = _Tee(config.run_log)
    sys.stdout = tee
    try:
        print("=" * 60)
        print("Google Photos Takeout Organizer")
        print("=" * 60)
        print(f"Source:  {config.source}")
        print(f"Output:  {config.output}")
        print(f"Run log: {config.run_log}")
        if config.rescan:
            print("  (--rescan: ignoring cached inventory)")
        pipeline.run(config)
        if not config.dry_run:
            neardup_prep.prepare(config, Path(__file__).resolve().parent, sys.executable)
    finally:
        tee.close()

    print("\nAll done!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
