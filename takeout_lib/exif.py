"""ExifTool wrapper: embed sidecar metadata, and read dates/probe media.

Photos get EXIF ``DateTimeOriginal``/``CreateDate`` + GPS; videos get the
QuickTime capture-date atoms. A bogus date (implausible / future) or a zero GPS
coordinate is skipped via exiftool advanced-formatting guards rather than being
stamped in.
"""

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

from . import dates, filetypes

# Output lines that are expected on every run and would otherwise flood the log.
_NOISE = ("Error opening file", "No writable tags set", "returned undef", "not defined")

# Description source, guarded so an empty caption (the norm in Takeout) is not
# written and can't blank an existing one.
_DESC = "${Description;$_=undef if $_ eq ''}"

# exiftool -progress emits "======== <file> [n/m]" as it works.
_PROGRESS_RE = re.compile(r"\[(\d+)/(\d+)\]\s*$")


def _warn_category(line):
    """Bucket an exiftool warning/error line into a short, countable category —
    or None to drop it entirely (the minor auto-fix noise)."""
    if "[minor]" in line:
        return None                       # auto-fixed structural quirks: ignore
    low = line.lower()
    if "gps" in low:
        return "GPS unreadable"
    if "maker notes" in low:
        return "maker notes unparsed"
    if "not yet supported" in low:
        return "unsupported file type (left as-is)"
    if "ifd" in low or "stripoffsets" in low:
        return "corrupt EXIF structure"
    if line.startswith("Error"):
        return "other errors"
    return "other warnings"


def _run(exiftool, args, label, log=None):
    """Run one exiftool pass, streaming a live N/M counter and collapsing the
    flood of warnings into a compact per-category tally (full text -> ``log``)."""
    print(f"  {label}...")
    proc = subprocess.Popen(
        [str(exiftool), "-progress"] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1)
    cats = Counter()
    summary = []
    shown_progress = False
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        m = _PROGRESS_RE.search(line)
        if m:
            print(f"    {m.group(1)}/{m.group(2)} files...", end="\r", flush=True)
            shown_progress = True
            continue
        line = line.strip()
        if not line or any(n in line for n in _NOISE):
            continue
        if any(kw in line for kw in ("updated", "unchanged", "created", "weren't")):
            summary.append(line)
        elif line.startswith(("Warning", "Error")):
            cat = _warn_category(line)
            if cat:
                cats[cat] += 1
            if log:
                log.write(line + "\n")
    proc.wait()
    if shown_progress:
        print()                           # end the \r progress line
    for s in summary:
        print(f"    {s}")
    if cats:
        tally = ", ".join(f"{n} {c}" for c, n in cats.most_common())
        suffix = " (see embed.log)" if log else ""
        print(f"    quietly handled: {tally}{suffix}")


def _ext_flags(exts):
    flags = []
    for ext in sorted(exts):
        flags += ["-ext", ext.lstrip(".")]
    return flags


def _date_guard(min_year):
    min_ts = int(datetime(min_year, 1, 1, tzinfo=timezone.utc).timestamp())
    max_ts = int(datetime.now(tz=timezone.utc).timestamp()) + 2 * 86400
    return f"$_=undef if not $_ or $_ < {min_ts} or $_ > {max_ts}"


def _photo_args(pt, photos_dir):
    return ["-r"] + _ext_flags(filetypes.PHOTO_EXTS) + [
        "-d", "%s",
        "-GPSAltitude<${GeoDataAltitude;$_=undef if $_==0}",
        "-GPSLatitude<${GeoDataLatitude;$_=undef if $_==0}",
        "-GPSLongitude<${GeoDataLongitude;$_=undef if $_==0}",
        "-GPSLatitudeRef<${GeoDataLatitude;$_=undef if $_==0}",
        "-GPSLongitudeRef<${GeoDataLongitude;$_=undef if $_==0}",
        "-DateTimeOriginal<" + pt,
        "-CreateDate<" + pt,
        # Descriptive metadata, only when the sidecar actually has it. The
        # description is present-but-empty on almost every sidecar, so the guard
        # avoids blanking a caption the camera already embedded.
        "-IPTC:Caption-Abstract<" + _DESC,
        "-XMP-dc:Description<" + _DESC,
        "-EXIF:ImageDescription<" + _DESC,
        "-XMP:Rating<${Favorited;$_=($_ and $_ ne 'false')?5:undef}",
        "-XMP-iptcExt:PersonInImage<PeopleName",
        "-overwrite_original",
        str(photos_dir),
    ]


def _video_args(pt, videos_dir):
    return ["-api", "QuickTimeUTC=1", "-r"] + _ext_flags(filetypes.VIDEO_EXTS) + [
        "-d", "%s",
        "-QuickTime:CreateDate<" + pt,
        "-QuickTime:ModifyDate<" + pt,
        "-TrackCreateDate<" + pt,
        "-TrackModifyDate<" + pt,
        "-MediaCreateDate<" + pt,
        "-MediaModifyDate<" + pt,
        "-QuickTime:Description<" + _DESC,
        "-overwrite_original",
        str(videos_dir),
    ]


def embed(config):
    """Phase 4: write sidecar metadata into the media files, then delete the
    sidecar JSONs so the output is media-only. Covers final and the Archive /
    Trash roots."""
    print("\n[4/5] Embedding sidecar metadata into media files...")
    exiftool = config.exiftool
    pt = "${PhotoTakenTimeTimestamp;" + _date_guard(config.min_year) + "}"

    roots = [r for r in config.content_roots() if r.exists()]
    log_path = config.output / "embed.log"
    with open(log_path, "w", encoding="utf-8") as log:
        for root in roots:
            photos_dir = root / "photos"
            videos_dir = root / "videos"
            if photos_dir.exists():
                shared = _photo_args(pt, photos_dir)
                _run(exiftool, ["-tagsfromfile", "%d%F.json"] + shared,
                     f"{root.name} photos: .json metadata", log)
                _run(exiftool, ["-tagsfromfile", "%d%F.supplemental-metadata.json"] + shared,
                     f"{root.name} photos: .supplemental-metadata.json metadata", log)
            if videos_dir.exists():
                shared = _video_args(pt, videos_dir)
                _run(exiftool, ["-tagsfromfile", "%d%F.json"] + shared,
                     f"{root.name} videos: .json capture date", log)
                _run(exiftool, ["-tagsfromfile", "%d%F.supplemental-metadata.json"] + shared,
                     f"{root.name} videos: .supplemental-metadata.json capture date", log)
    print(f"  Full exiftool warnings/errors logged to {log_path.name}")

    print("  Removing sidecar JSON files from output...")
    removed = 0
    for root in roots:
        for p in root.rglob("*.json"):
            try:
                p.unlink()
                removed += 1
            except OSError as e:
                print(f"    WARNING: could not delete {p.name}: {e}")
    print(f"    {removed} JSON sidecars removed — output is now media-only")


# Files per exiftool invocation. Each path is passed on the command line, so we
# cap the batch well under the OS command-line length limit (~32k chars on
# Windows) — a large library can have thousands of files to probe at once.
BATCH_SIZE = 100


def read_json(exiftool, paths, tags):
    """Batch-read ``tags`` from ``paths``, chunking so the command line never
    exceeds the OS limit. Returns a list of dicts (exiftool -j output)."""
    paths = [str(p) for p in paths]
    if not paths:
        return []
    base = [str(exiftool), "-q", "-m", "-api", "QuickTimeUTC=1", "-j"]
    base += [f"-{t}" for t in tags]
    out = []
    for i in range(0, len(paths), BATCH_SIZE):
        result = subprocess.run(base + paths[i:i + BATCH_SIZE],
                                capture_output=True, text=True, errors="replace")
        try:
            if result.stdout.strip():
                out.extend(json.loads(result.stdout))
        except json.JSONDecodeError:
            pass
    return out


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def probe_media(exiftool, paths, min_year=dates.MIN_PLAUSIBLE_YEAR, max_year=None):
    """Batch-read each file's embedded capture date and (for videos) duration.

    Returns a dict keyed by normalised path -> {"date": (year, month) | None,
    "duration": float seconds | None}. Photos use DateTimeOriginal then
    CreateDate; videos use the QuickTime CreateDate. ``Duration#`` forces numeric
    seconds without disturbing the date formatting.
    """
    result = {_norm(p): {"date": None, "duration": None} for p in paths}
    for e in read_json(exiftool, paths, ["DateTimeOriginal", "CreateDate", "Duration#"]):
        sf = e.get("SourceFile")
        if not sf:
            continue
        s = e.get("DateTimeOriginal") or e.get("CreateDate")
        dur = e.get("Duration")
        result[_norm(sf)] = {
            "date": dates.parse_exif_datetime(s, min_year, max_year) if s else None,
            "duration": float(dur) if isinstance(dur, (int, float)) else None,
        }
    return result
