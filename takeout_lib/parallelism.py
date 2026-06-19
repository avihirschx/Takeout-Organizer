"""Decide *how much* to parallelize, safely.

Two phases benefit from more than one worker, for different reasons:

* **Phase 1 hashing** reads every byte in the Takeout. It is limited by disk
  bandwidth. On an **SSD/NVMe**, several reader threads overlap and approach the
  drive's maximum throughput (and the hashing overlaps with the reads). On a
  **spinning HDD**, those same threads make the head seek back and forth and the
  scan gets *slower*. So we only parallelize disk reads when we can confirm an
  SSD — or the user forces it.
* **Near-duplicate fingerprinting** decodes images: it is CPU-bound, parallelizes
  cleanly across cores regardless of the disk, and is handled with processes.

Parallelism never changes *what* is produced — only how fast. The worst case of
a wrong guess is "not as fast as it could be", never a corrupt or different
result. The auto mode therefore errs toward sequential whenever it can't *prove*
the source is on an SSD.
"""

import os
import subprocess
import sys


def io_worker_cap():
    """Upper bound on reader threads for the disk-read phase."""
    return min(8, os.cpu_count() or 4)


def cpu_workers():
    """Worker count for CPU-bound work (near-dup fingerprinting)."""
    return min(16, os.cpu_count() or 4)


def _windows_disk_type(path):
    """'ssd' / 'hdd' / 'unknown' for the volume holding ``path`` on Windows.

    Maps the path's drive letter -> partition -> physical disk and reads its
    MediaType via PowerShell. Anything unexpected (UNC path, virtual disk,
    PowerShell missing, timeout) degrades to 'unknown'.
    """
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0].rstrip(":\\")
    if not drive or len(drive) != 1:
        return "unknown"
    ps = ("$ErrorActionPreference='Stop';"
          f"(Get-Partition -DriveLetter '{drive}' | Get-Disk | "
          "Get-PhysicalDisk).MediaType")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20)
    except Exception:
        return "unknown"
    val = (out.stdout or "").strip().lower()
    if "ssd" in val:
        return "ssd"
    if "hdd" in val:
        return "hdd"
    return "unknown"


def _linux_disk_type(path):
    """Best-effort SSD/HDD detection on Linux via the block device's
    ``rotational`` flag. Returns 'unknown' when the mapping is non-trivial
    (LVM, btrfs subvolumes, containers), which keeps auto mode on the safe
    sequential path."""
    try:
        dev = os.stat(path).st_dev
        major, minor = os.major(dev), os.minor(dev)
        for name in os.listdir("/sys/block"):
            devf = f"/sys/block/{name}/dev"
            try:
                with open(devf) as f:
                    if f.read().strip() == f"{major}:{minor}":
                        with open(f"/sys/block/{name}/queue/rotational") as r:
                            return "hdd" if r.read().strip() == "1" else "ssd"
            except OSError:
                continue
    except Exception:
        pass
    return "unknown"


def _macos_disk_type(path):
    """Best-effort SSD/HDD detection on macOS: resolve the path to its backing
    device with ``df`` and ask ``diskutil`` whether it's solid-state. Any hiccup
    degrades to 'unknown' (so auto mode stays on the safe sequential path)."""
    try:
        df = subprocess.run(["df", str(path)], capture_output=True,
                            text=True, timeout=10)
        lines = (df.stdout or "").strip().splitlines()
        if len(lines) < 2:
            return "unknown"
        device = lines[-1].split()[0]            # e.g. /dev/disk3s1
        info = subprocess.run(["diskutil", "info", device], capture_output=True,
                             text=True, timeout=15)
        for line in (info.stdout or "").splitlines():
            if "Solid State" in line:
                return "ssd" if "Yes" in line else "hdd"
    except Exception:
        pass
    return "unknown"


def detect_disk_type(path):
    """'ssd' / 'hdd' / 'unknown' for the drive holding ``path`` (best effort)."""
    try:
        if os.name == "nt":
            return _windows_disk_type(path)
        if sys.platform == "darwin":
            return _macos_disk_type(path)
        return _linux_disk_type(path)
    except Exception:
        return "unknown"


def plan_io(config):
    """Return ``(workers, reason)`` for the disk-read (hashing) phase, honoring
    ``config.parallel`` and — in auto mode — what we can detect about the disk."""
    cap = io_worker_cap()
    mode = getattr(config, "parallel", "auto")
    if mode == "off":
        return 1, "sequential (--parallel off)"
    if mode == "on":
        return cap, f"parallel with {cap} workers (--parallel on)"
    # auto: parallelize only when we can confirm an SSD.
    dt = detect_disk_type(config.source)
    if dt == "ssd":
        return cap, f"parallel with {cap} workers (auto: source is on an SSD)"
    if dt == "hdd":
        return 1, ("sequential (auto: source is on a spinning HDD — parallel reads "
                   "would thrash the disk; pass --parallel on to override)")
    return 1, ("sequential (auto: couldn't confirm the disk is an SSD; "
               "pass --parallel on if it is)")
