"""takeout_lib — reusable pieces of the Google Photos Takeout organizer.

The package is split so the pure logic (no filesystem or subprocess I/O) can be
unit-tested directly:

    matching   — sidecar-name matching (truncation, counters, -edited, fuzzy)
    dates      — capture-date plausibility and the sidecar/EXIF date waterfall
    filetypes  — photo/video classification and magic-byte type detection
    sidecars   — reading a sidecar from disk or a zip, choosing among duplicates

The I/O-heavy stages (inventory, exif, livephoto, pipeline) build on these.
"""

__version__ = "0.1.0"
