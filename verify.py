#!/usr/bin/env python3
"""Verify a dedup.py run before you delete the source.

    python verify.py --output "D:/Cleaned"

Prints a report and a SAFE TO DELETE / DO NOT DELETE verdict, and writes it to
<output>/verify_report.txt.
"""

import argparse
from pathlib import Path

from takeout_lib import config as cfg
from takeout_lib import verifier


def main(argv=None):
    p = argparse.ArgumentParser(prog="verify.py", description=__doc__)
    p.add_argument("--output", required=True, type=Path,
                   help="The --output folder a previous dedup.py run wrote to.")
    p.add_argument("--exiftool", type=Path, default=Path(cfg._default_exiftool()),
                   help="Path to exiftool (default: from PATH).")
    args = p.parse_args(argv)

    if not args.output.exists():
        print(f"ERROR: output folder not found: {args.output}")
        return 1

    safe = verifier.report(args.output, args.exiftool,
                           report_path=args.output / "verify_report.txt")
    return 0 if safe else 2


if __name__ == "__main__":
    raise SystemExit(main())
