"""Detect iPhone Live Photo *motion* clips.

A Live Photo is a still + a separate ~2-3 second video. Google Takeout exports
the motion as its own ``.MP4`` that shares the still's base name but has no
sidecar of its own (the sidecar is named after the still). Left alone, the
EXIF-date fallback would pull every one of these short clips into the video
library. We flag them so the pipeline can divert them (archive / drop / keep).

A no-sidecar video is treated as a motion clip when it shares a base name with a
still and is short. Duration is read from the file; the rest is name-based.
"""

from pathlib import Path

from . import filetypes

# iPhone Live Photo motion is ~3 s; allow a little headroom.
MOTION_MAX_SECONDS = 4.0


def photo_stems(records):
    """Base names (stems) of all still images among ``records``."""
    return {Path(r["name"]).stem for r in records
            if filetypes.media_kind(Path(r["name"]).suffix) == "photos"}


def is_motion_clip(name, duration, stems, max_seconds=MOTION_MAX_SECONDS):
    """True if ``name`` looks like a Live Photo motion clip: a video that shares
    a base name with a still and is at most ``max_seconds`` long."""
    if filetypes.media_kind(Path(name).suffix) != "videos":
        return False
    if Path(name).stem not in stems:
        return False
    return duration is not None and duration <= max_seconds
