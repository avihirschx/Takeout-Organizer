"""Run configuration and command-line parsing.

All paths and options live here so nothing is hardcoded; ``dedup.py`` / ``verify.py``
just build a :class:`Config` from argv and hand it to the pipeline.
"""

import argparse
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .dates import MIN_PLAUSIBLE_YEAR

LIVE_MOTION_MODES = ("archive", "drop", "keep", "errors")
ALBUM_MODES = ("link", "copy", "manifest", "none")
NEAR_DUP_MODES = ("scan", "defer", "off")
PARALLEL_MODES = ("auto", "on", "off")


@dataclass
class Config:
    source: Path
    output: Path
    exiftool: Path
    rescan: bool = False
    min_year: int = MIN_PLAUSIBLE_YEAR
    fix_extensions: bool = True
    live_motion: str = "archive"
    include_undated: bool = False
    albums: str = "link"
    near_dupes: str = "scan"
    parallel: str = "auto"
    dry_run: bool = False

    # ── derived output paths ────────────────────────────────────────────────
    @property
    def final(self):
        return self.output / "final"

    @property
    def errors_dir(self):
        return self.output / "Deduplication Errors"

    @property
    def live_motion_dir(self):
        # Sibling of final/, so it isn't uploaded with photos or videos.
        return self.output / "Live Photo motion"

    @property
    def archive_dir(self):
        return self.output / "Archive"

    @property
    def trash_dir(self):
        return self.output / "Trash"

    @property
    def albums_dir(self):
        return self.output / "albums"

    @property
    def albums_manifest(self):
        return self.output / "albums.json"

    @property
    def placements_file(self):
        return self.output / "placements.json"

    @property
    def photos_dir(self):
        return self.final / "photos"

    @property
    def near_dup_groups(self):
        return self.output / "near-dup-groups.json"

    @property
    def near_dup_launcher(self):
        name = "review-near-dupes.cmd" if os.name == "nt" else "review-near-dupes.sh"
        return self.output / name

    def root_for_bucket(self, bucket):
        if bucket == "archive":
            return self.archive_dir
        if bucket == "trash":
            return self.trash_dir
        return self.final

    # Roots that hold dated photos/videos and need metadata embedded.
    def content_roots(self):
        return [self.final, self.archive_dir, self.trash_dir]

    @property
    def staging(self):
        return self.output / "_staging"

    @property
    def log(self):
        return self.output / "errors.log"

    @property
    def inventory(self):
        return self.output / "inventory.json"

    @property
    def done_file(self):
        return self.output / "extracted.json"

    @property
    def run_log(self):
        return self.output / "run.log"


def _default_exiftool():
    found = shutil.which("exiftool")
    if found:
        return found
    win = r"C:\tools\exiftool\exiftool.exe"
    return win if Path(win).exists() else "exiftool"


def build_parser(prog=None, description=None):
    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument("--source", required=True, type=Path,
                   help="Folder containing the Takeout (zips and/or extracted dirs).")
    p.add_argument("--output", required=True, type=Path,
                   help="Where to write the cleaned library (created if missing).")
    p.add_argument("--exiftool", type=Path, default=Path(_default_exiftool()),
                   help="Path to the exiftool executable (default: from PATH).")
    p.add_argument("--rescan", action="store_true",
                   help="Ignore any cached inventory and re-scan from scratch.")
    p.add_argument("--min-year", type=int, default=MIN_PLAUSIBLE_YEAR,
                   help="Capture dates before this year are treated as bogus "
                        f"(default: {MIN_PLAUSIBLE_YEAR}).")
    p.add_argument("--no-extension-fix", dest="fix_extensions", action="store_false",
                   help="Do not rename files whose content doesn't match their extension.")
    p.add_argument("--live-motion", choices=LIVE_MOTION_MODES, default="archive",
                   help="What to do with Live Photo motion clips (default: archive).")
    p.add_argument("--albums", choices=ALBUM_MODES, default="link",
                   help="Rebuild albums as a parallel folder tree: link (hardlinks, "
                        "no extra space) / copy / manifest (albums.json only) / none. "
                        "Default: link.")
    p.add_argument("--near-dupes", choices=NEAR_DUP_MODES, default="scan",
                   help="Near-duplicate review prep (runs on final/photos only, after "
                        "dedup): scan (fingerprint now + write launcher) / defer "
                        "(launcher only, fingerprint when you open it) / off. Default: scan.")
    p.add_argument("--parallel", choices=PARALLEL_MODES, default="auto",
                   help="Speed up the disk-read scan and the near-dup fingerprinting by "
                        "using multiple workers: auto (parallelize only when the source is "
                        "on an SSD — safe default), on (force it), off (fully sequential, "
                        "best for a spinning HDD). Default: auto.")
    p.add_argument("--include-undated", action="store_true",
                   help="Put real media that has no sidecar and no embedded date into "
                        "final/<kind>/unknown instead of the errors folder.")
    p.add_argument("--dry-run", action="store_true",
                   help="Scan and report what would happen; write nothing to the library.")
    return p


def config_from_args(argv=None, prog=None, description=None):
    args = build_parser(prog, description).parse_args(argv)
    return Config(
        source=args.source,
        output=args.output,
        exiftool=args.exiftool,
        rescan=args.rescan,
        min_year=args.min_year,
        fix_extensions=args.fix_extensions,
        live_motion=args.live_motion,
        include_undated=args.include_undated,
        albums=args.albums,
        near_dupes=args.near_dupes,
        parallel=args.parallel,
        dry_run=args.dry_run,
    )


def validate(config, require_exiftool=True):
    """Return a list of human-readable problems with the config (empty = OK)."""
    problems = []
    if not config.source.exists():
        problems.append(f"Source directory not found: {config.source}")
    if require_exiftool and not Path(config.exiftool).exists() \
            and not shutil.which(str(config.exiftool)):
        problems.append(f"exiftool not found: {config.exiftool}")
    return problems
