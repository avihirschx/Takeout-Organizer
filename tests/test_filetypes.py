"""Unit tests for magic-byte type detection and extension correction."""

import struct

from takeout_lib import filetypes as ft
from conftest import minimal_jpeg, minimal_mp4


def _ftyp(brand):
    payload = brand + struct.pack(">I", 0x200) + b"isom"
    return struct.pack(">I", 8 + len(payload)) + b"ftyp" + payload


def test_sniff_real_jpeg_and_mp4():
    assert ft.sniff_type(minimal_jpeg()) == "jpg"
    assert ft.sniff_type(minimal_mp4()) == "mp4"


def test_sniff_image_signatures():
    assert ft.sniff_type(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "png"
    assert ft.sniff_type(b"GIF89a" + b"\x00" * 8) == "gif"
    assert ft.sniff_type(b"BM" + b"\x00" * 12) == "bmp"
    assert ft.sniff_type(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 4) == "webp"


def test_sniff_video_signatures():
    assert ft.sniff_type(_ftyp(b"qt  ")) == "mov"
    assert ft.sniff_type(_ftyp(b"M4V ")) == "m4v"
    assert ft.sniff_type(_ftyp(b"3gp4")) == "3gp"
    assert ft.sniff_type(_ftyp(b"mp42")) == "mp4"
    assert ft.sniff_type(_ftyp(b"heic")) == "heic"
    assert ft.sniff_type(_ftyp(b"avif")) == "avif"
    assert ft.sniff_type(b"RIFF\x00\x00\x00\x00AVI LIST") == "avi"
    assert ft.sniff_type(b"\x1aE\xdf\xa3" + b"\x00" * 12) == "mkv"


def test_sniff_leaves_tiff_raw_alone():
    # TIFF/RAW signatures must return None so genuine RAW files are never renamed.
    assert ft.sniff_type(b"II*\x00" + b"\x00" * 12) is None
    assert ft.sniff_type(b"MM\x00*" + b"\x00" * 12) is None


def test_sniff_unknown_returns_none():
    assert ft.sniff_type(b"not a real header at all") is None
    assert ft.sniff_type(b"") is None
    assert ft.sniff_type(b"\x00\x01") is None


def test_corrected_name():
    # The real-world case: a JPEG named .heic.
    assert ft.corrected_name("IMG.heic", "jpg") == "IMG.jpg"
    # Equivalent extensions are never churned.
    assert ft.corrected_name("IMG.jpeg", "jpg") == "IMG.jpeg"
    assert ft.corrected_name("IMG.heif", "heic") == "IMG.heif"
    # Unrecognised content (None) is a no-op.
    assert ft.corrected_name("IMG.cr2", None) == "IMG.cr2"
    # Already correct.
    assert ft.corrected_name("IMG.jpg", "jpg") == "IMG.jpg"


def test_media_kind():
    assert ft.media_kind(".jpg") == "photos"
    assert ft.media_kind("heic") == "photos"
    assert ft.media_kind(".mp4") == "videos"
    assert ft.media_kind("mov") == "videos"


def test_canon_ext():
    assert ft.canon_ext(".JPEG") == "jpg"
    assert ft.canon_ext("tiff") == "tif"
    assert ft.canon_ext(".HEIF") == "heic"
    assert ft.canon_ext("png") == "png"
