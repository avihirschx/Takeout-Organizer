"""Live Photo motion-clip detection and disposition."""

from pathlib import Path

from conftest import minimal_jpeg, minimal_mp4, sidecar_json
from takeout_lib import livephoto, pipeline
from takeout_lib.config import Config


# ── unit: pure detection ────────────────────────────────────────────────────

def test_photo_stems():
    records = [{"name": "IMG.heic"}, {"name": "IMG.mp4"}, {"name": "VID.mov"}]
    assert livephoto.photo_stems(records) == {"IMG"}


def test_is_motion_clip():
    stems = {"IMG"}
    assert livephoto.is_motion_clip("IMG.mp4", 2.0, stems)        # short, paired
    assert not livephoto.is_motion_clip("IMG.mp4", 30.0, stems)   # too long
    assert not livephoto.is_motion_clip("OTHER.mp4", 2.0, stems)  # no matching still
    assert not livephoto.is_motion_clip("IMG.jpg", 2.0, stems)    # not a video
    assert not livephoto.is_motion_clip("IMG.mp4", None, stems)   # unknown duration


# ── integration: dispositions ───────────────────────────────────────────────

def _build(root, taken=None):
    """A Live Photo: still IMG.heic (with sidecar) + short motion IMG.mp4 (no sidecar)."""
    a = root / "Album"
    a.mkdir(parents=True)
    (a / "IMG.heic").write_bytes(minimal_jpeg(1))
    (a / "IMG.heic.json").write_bytes(sidecar_json(1483228800))   # 2017 still
    (a / "IMG.mp4").write_bytes(minimal_mp4(tag=2, duration_s=2, taken=taken))
    return root


def _run(tmp_path, mode, taken=None):
    src = _build(tmp_path / "src", taken=taken)
    out = tmp_path / "out"
    cfg = Config(source=src, output=out, exiftool=Path(_exiftool()),
                 rescan=True, live_motion=mode)
    pipeline.run(cfg)
    return out


def _exiftool():
    import shutil
    import os
    return shutil.which("exiftool") or r"C:\tools\exiftool\exiftool.exe"


def test_motion_archive(tmp_path, need_exiftool):
    out = _run(tmp_path, "archive")
    final = {p.relative_to(out / "final").as_posix()
             for p in (out / "final").rglob("*") if p.is_file()}
    assert "photos/2017/01/IMG.jpg" in final              # still kept, ext fixed
    assert not any(p.endswith(".mp4") for p in final)     # motion not in final
    archived = [p.name for p in (out / "Live Photo motion").rglob("*") if p.is_file()]
    assert "IMG.mp4" in archived


def test_motion_drop(tmp_path, need_exiftool):
    out = _run(tmp_path, "drop")
    assert not any(p.suffix == ".mp4" for p in out.rglob("*") if p.is_file())
    assert not (out / "Live Photo motion").exists()


def test_motion_keep(tmp_path, need_exiftool):
    # With a real capture date and keep mode, the clip is treated as a normal
    # video (exact YYYY/MM depends on local timezone, so just assert it's there).
    out = _run(tmp_path, "keep", taken=1483228800)
    final = {p.relative_to(out / "final").as_posix()
             for p in (out / "final").rglob("*") if p.is_file()}
    assert any(p.startswith("videos/") and p.endswith("/IMG.mp4") for p in final)
    assert not (out / "Live Photo motion").exists()


def test_albums_rebuilt(tmp_path, need_exiftool):
    import json
    base = tmp_path / "src" / "Takeout" / "Google Photos"
    (base / "Vacation 2019").mkdir(parents=True)
    (base / "Photos from 2019").mkdir(parents=True)
    # one photo in an album, one only in the timeline
    (base / "Vacation 2019" / "V.jpg").write_bytes(minimal_jpeg(1))
    (base / "Vacation 2019" / "V.jpg.json").write_bytes(sidecar_json(1546300800))
    (base / "Photos from 2019" / "T.jpg").write_bytes(minimal_jpeg(2))
    (base / "Photos from 2019" / "T.jpg.json").write_bytes(sidecar_json(1546300800))

    out = tmp_path / "out"
    pipeline.run(Config(source=tmp_path / "src", output=out,
                        exiftool=Path(_exiftool()), rescan=True))

    # the album photo is linked under albums/<name>/; the timeline photo is not
    assert next((out / "albums" / "Vacation 2019").rglob("V.jpg"), None) is not None
    assert next((out / "albums").rglob("T.jpg"), None) is None

    manifest = json.loads((out / "albums.json").read_text(encoding="utf-8"))
    assert "Vacation 2019" in manifest and "Photos from 2019" not in manifest

    # the album entry shares content with the embedded final file (hardlink)
    final_v = next((out / "final").rglob("V.jpg"))
    album_v = next((out / "albums" / "Vacation 2019").rglob("V.jpg"))
    assert final_v.stat().st_size == album_v.stat().st_size
    r = subprocess_run_date(album_v)
    assert r  # album copy carries the embedded date


def subprocess_run_date(path):
    import subprocess
    return subprocess.run([_exiftool(), "-s3", "-DateTimeOriginal", str(path)],
                          capture_output=True, text=True).stdout.strip()
