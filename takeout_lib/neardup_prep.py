"""End-of-run near-duplicate prep: precompute groups (optional) and drop a
launcher so the user can open the browser review later with one click.

Runs on ``final/photos`` only (the deduplicated photos), after the rest of the
pipeline. Kept in the CLI layer rather than ``pipeline.py`` so the library stays
dependency-free and we have the venv-Python / repo paths to build the launcher.
"""

import os
import stat
from pathlib import Path

from . import neardup


def _pillow_ready():
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return True


def _write_launcher(config, repo_dir, python_exe, with_groups):
    # Absolute paths so the launcher works no matter where it's double-clicked from.
    tool = (Path(repo_dir) / "tools" / "find_near_dupes.py").resolve()
    parts = [f'"{python_exe}"', f'"{tool}"', "--dir", f'"{config.photos_dir.resolve()}"']
    if with_groups:
        parts += ["--groups", f'"{config.near_dup_groups.resolve()}"']
    cmd = " ".join(parts)
    path = config.near_dup_launcher
    if os.name == "nt":
        path.write_text(f"@echo off\r\n{cmd}\r\npause\r\n", encoding="utf-8")
    else:
        path.write_text(f"#!/bin/sh\n{cmd}\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return path


def prepare(config, repo_dir, python_exe):
    """Precompute near-dup groups (when mode is 'scan' and Pillow is available)
    and write the review launcher. No-op when mode is 'off' or there are no
    photos."""
    if config.near_dupes == "off":
        return
    if not config.photos_dir.exists():
        return

    print("\nPreparing near-duplicate review...")
    cache_written = False
    if config.near_dupes == "scan":
        if _pillow_ready():
            # Fingerprinting decodes pixels (CPU-bound); use every core unless the
            # user asked for a fully sequential run.
            workers = 1 if getattr(config, "parallel", "auto") == "off" else None
            groups = neardup.scan_folder(config.photos_dir, workers=workers)
            neardup.save_groups(groups, config.photos_dir, config.near_dup_groups)
            cache_written = True
            extras = sum(len(g) - 1 for g in groups)
            print(f"  Fingerprinted photos: {len(groups)} near-dup groups "
                  f"({extras} extra copies) saved to {config.near_dup_groups.name}")
        else:
            print("  Pillow not found — skipping the fingerprint pass. The review "
                  "will compute it on first open. (Run dedup from the venv to "
                  "precompute it here.)")

    launcher = _write_launcher(config, repo_dir, python_exe, cache_written)
    print(f"  Review launcher written: {launcher}")
    print(f"  -> run it any time to pick which near-duplicates to keep.")
