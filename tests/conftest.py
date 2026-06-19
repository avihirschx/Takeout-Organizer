"""Shared pytest fixtures and tiny synthetic-media generators.

The byte blobs here are the smallest things ExifTool/our sniffer will accept as
real media, so integration tests can build a fake Takeout tree without any
binary fixtures checked into the repo.
"""

import base64
import json
import os
import struct
import sys
import shutil

import pytest

# Make the package importable when tests are run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A genuinely-decodable 1x1 JPEG.
_MINIMAL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAB//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)


def minimal_jpeg(tag=0):
    """A valid tiny JPEG; ``tag`` perturbs one byte so callers can make distinct
    (different-hash) images."""
    if not tag:
        return _MINIMAL_JPEG
    return _MINIMAL_JPEG[:-2] + bytes([tag & 0xFF]) + _MINIMAL_JPEG[-1:]


def _box(typ, payload):
    return struct.pack(">I", 8 + len(payload)) + typ + payload


# Seconds between the QuickTime epoch (1904-01-01) and the Unix epoch.
_QT_EPOCH_OFFSET = 2082844800


def minimal_mp4(tag=0, duration_s=2, taken=None):
    """A minimal but structurally valid MP4 (ftyp + moov/mvhd).

    ``duration_s`` sets the movie duration exiftool will report (used to make
    short "Live Photo" clips). ``taken`` (a Unix timestamp) sets the QuickTime
    creation time so exiftool reports a CreateDate. ``tag`` appends a unique
    'free' box so callers can make distinct files.
    """
    qt_time = (taken + _QT_EPOCH_OFFSET) if taken else 0
    ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 0x200) + b"isom" + b"mp41")
    matrix = struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    mvhd = _box(
        b"mvhd",
        struct.pack(">I", 0)                       # version+flags
        + struct.pack(">I", qt_time)               # creation_time
        + struct.pack(">I", qt_time)               # modification_time
        + struct.pack(">I", 1000)                  # timescale
        + struct.pack(">I", int(duration_s * 1000))  # duration
        + struct.pack(">I", 0x10000)
        + struct.pack(">H", 0x100)
        + b"\x00\x00" + b"\x00" * 8
        + matrix + b"\x00" * 24
        + struct.pack(">I", 0xFFFFFFFF),
    )
    out = ftyp + _box(b"moov", mvhd)
    if tag:
        out += _box(b"free", bytes([tag & 0xFF]) * 4)
    return out


def sidecar_json(ts=None, lat=0.0, lon=0.0, alt=0.0, title=None):
    """Bytes of a Google-Takeout-style sidecar."""
    obj = {}
    if title:
        obj["title"] = title
    if ts is not None:
        obj["photoTakenTime"] = {"timestamp": str(ts)}
    obj["geoData"] = {"latitude": lat, "longitude": lon, "altitude": alt}
    return json.dumps(obj).encode()


@pytest.fixture(scope="session")
def exiftool():
    """Path to exiftool, or None if it isn't installed."""
    return shutil.which("exiftool") or _windows_exiftool()


def _windows_exiftool():
    cand = r"C:\tools\exiftool\exiftool.exe"
    return cand if os.path.exists(cand) else None


@pytest.fixture
def need_exiftool(exiftool):
    """Skip a test when ExifTool isn't available."""
    if not exiftool:
        pytest.skip("ExifTool not installed")
    return exiftool


@pytest.fixture
def need_pillow():
    """Skip a test when Pillow isn't installed (e.g. running outside the venv)."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        pytest.skip("Pillow not installed")
