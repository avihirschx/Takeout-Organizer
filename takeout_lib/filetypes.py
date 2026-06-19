"""Photo/video classification and magic-byte type detection.

``sniff_type`` looks at a file's first bytes and returns the canonical extension
for what the bytes actually are — used to fix Takeout's mislabelled files (e.g.
JPEGs named ``.heic``). It is deliberately conservative: anything it can't
positively identify (including RAW formats, which share TIFF's signature) returns
None and is left untouched.
"""

from pathlib import Path

PHOTO_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".avif",
    ".bmp", ".tiff", ".tif", ".webp",
    ".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".3gp", ".wmv"}
MEDIA_EXTS = PHOTO_EXTS | VIDEO_EXTS

# Canonical extension -> which output tree it belongs in.
_VIDEO_CANON = {"mp4", "mov", "avi", "mkv", "m4v", "3gp", "wmv"}

# Interchangeable extensions — never "correct" within a group. The first item of
# each pair is the canonical representative the group collapses to.
_EQUIV = [
    ("jpg", {"jpg", "jpeg", "jpe"}),
    ("tif", {"tif", "tiff"}),
    ("heic", {"heic", "heif"}),
]

HEADER_BYTES = 32  # enough for every signature below

_ASF_GUID = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")


def canon_ext(ext):
    """Lower-case, de-dotted, and collapsed to a representative of its equiv group."""
    ext = ext.lower().lstrip(".")
    for rep, group in _EQUIV:
        if ext in group:
            return rep
    return ext


def media_kind(ext):
    """'videos' or 'photos' for an extension (with or without a leading dot)."""
    return "videos" if canon_ext(ext) in _VIDEO_CANON else "photos"


def sniff_type(head):
    """Return the canonical extension for the content in ``head`` (bytes), or
    None if it isn't confidently recognised.

    Returns one of: jpg png gif bmp webp heic avif mp4 mov m4v 3gp avi mkv wmv.
    Notably returns None for TIFF/RAW signatures, so genuine RAW files are never
    renamed.
    """
    if not head or len(head) < 12:
        return None

    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:2] == b"BM":
        return "bmp"
    if head[:4] == b"\x1aE\xdf\xa3":  # EBML (Matroska / WebM)
        return "mkv"
    if len(head) >= 16 and head[:16] == _ASF_GUID:
        return "wmv"

    if head[:4] == b"RIFF":
        fourcc = head[8:12]
        if fourcc == b"WEBP":
            return "webp"
        if fourcc == b"AVI ":
            return "avi"
        return None

    # ISO base media (MP4/MOV/HEIF family): "ftyp" box at offset 4, brand at 8.
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        b = brand.rstrip(b" ").lower()
        if brand in (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx",
                     b"mif1", b"msf1"):
            return "heic"
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand == b"qt  ":
            return "mov"
        if b.startswith(b"m4v"):
            return "m4v"
        if b.startswith(b"3g"):
            return "3gp"
        if brand in (b"isom", b"iso2", b"iso4", b"iso5", b"iso6",
                     b"mp41", b"mp42", b"mp4 ", b"mp4v", b"avc1", b"dash",
                     b"msnv", b"ndsc", b"f4v ", b"cmff"):
            return "mp4"
        return None  # unknown ISO-BMFF brand: leave alone

    return None  # TIFF/RAW and everything unrecognised -> leave alone


def corrected_name(name, true_ext):
    """Return ``name`` with its extension swapped to ``true_ext`` if they
    genuinely differ; otherwise return ``name`` unchanged.

    Equivalent extensions (jpeg/jpg, tif/tiff, heif/heic) are never churned, and
    ``true_ext = None`` (unrecognised content) is a no-op.
    """
    if not true_ext:
        return name
    cur = Path(name).suffix.lower().lstrip(".")
    if canon_ext(cur) == canon_ext(true_ext):
        return name
    return Path(name).with_suffix("." + true_ext).name
