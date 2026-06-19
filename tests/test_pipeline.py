"""End-to-end pipeline test against a synthetic Takeout. Needs ExifTool."""

import subprocess
import zipfile
from pathlib import Path

from conftest import minimal_jpeg, minimal_mp4, sidecar_json
from takeout_lib import pipeline, verifier
from takeout_lib.config import Config


def jpeg_with_exif_date(path, exiftool, datestr, tag=0):
    """Write a JPEG and stamp DateTimeOriginal into it (e.g. '2019:05:01 12:00:00')."""
    path.write_bytes(minimal_jpeg(tag))
    subprocess.run([str(exiftool), f"-DateTimeOriginal={datestr}",
                    "-overwrite_original", str(path)],
                   capture_output=True, text=True)


def build_source(root):
    """A small but representative Takeout: dedup, truncated sidecar, bogus date,
    a cross-zip split, and a video."""
    a = root / "AlbumA"
    a.mkdir(parents=True)
    GPS = dict(lat=37.7749, lon=-122.4194, alt=12.0)

    (a / "PIC1.jpg").write_bytes(minimal_jpeg(1))
    (a / "PIC1.jpg.json").write_bytes(sidecar_json(1483228800, **GPS))     # 2017
    (a / "PIC2.jpg").write_bytes(minimal_jpeg(2))
    (a / "PIC2.jpg.supplemental-metad.json").write_bytes(sidecar_json(1717543804))  # 2024, truncated
    (a / "BOGUS.jpg").write_bytes(minimal_jpeg(3))
    (a / "BOGUS.jpg.json").write_bytes(sidecar_json(315525610))            # 1979 -> unknown
    (a / "SPLIT.jpg").write_bytes(minimal_jpeg(4))                         # sidecar is in the zip
    (a / "CLIP.mp4").write_bytes(minimal_mp4(1))
    (a / "CLIP.mp4.json").write_bytes(sidecar_json(1715727953))           # 2024 video

    with zipfile.ZipFile(root / "takeout.zip", "w") as z:
        z.writestr("Takeout/Google Photos/Other/SPLIT.jpg.json", sidecar_json(1546300800))  # 2019 orphan
        z.writestr("Takeout/Google Photos/AlbumA/PIC1.jpg", minimal_jpeg(1))                 # dup of PIC1
        z.writestr("Takeout/Google Photos/AlbumA/PIC1.jpg.json", sidecar_json(1483228800, **GPS))
    return root


def test_full_pipeline(tmp_path, need_exiftool):
    src = build_source(tmp_path / "src")
    out = tmp_path / "out"
    config = Config(source=src, output=out, exiftool=Path(need_exiftool), rescan=True)

    pipeline.run(config)

    final = out / "final"
    present = {p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file()}

    # dedup: PIC1 (dir + zip) collapses to one
    assert "photos/2017/01/PIC1.jpg" in present
    assert sum(1 for p in present if Path(p).name == "PIC1.jpg") == 1
    # truncated sidecar matched -> 2024
    assert "photos/2024/06/PIC2.jpg" in present
    # bogus date -> unknown
    assert "photos/unknown/BOGUS.jpg" in present
    # cross-zip split recovery -> dated from the orphan sidecar (2019)
    assert "photos/2019/01/SPLIT.jpg" in present
    # video split out and dated
    assert "videos/2024/05/CLIP.mp4" in present
    # output is media-only
    assert not any(p.endswith(".json") for p in present)

    # verify says SAFE TO DELETE
    assert verifier.report(out, need_exiftool) is True


def test_hash_bucket():
    from takeout_lib.pipeline import hash_bucket
    assert hash_bucket([{"bucket": None}, {"bucket": "archive"}]) is None      # normal wins
    assert hash_bucket([{"bucket": "archive"}, {"bucket": "trash"}]) == "archive"
    assert hash_bucket([{"bucket": "trash"}]) == "trash"
    assert hash_bucket([{"bucket": None}]) is None


def test_trash_archive_routing(tmp_path, need_exiftool):
    base = tmp_path / "src" / "Takeout" / "Google Photos"
    for sub in ("Album", "Trash", "Archive"):
        (base / sub).mkdir(parents=True)
    (base / "Album" / "N.jpg").write_bytes(minimal_jpeg(1))
    (base / "Album" / "N.jpg.json").write_bytes(sidecar_json(1483228800))
    (base / "Trash" / "T.jpg").write_bytes(minimal_jpeg(2))
    (base / "Trash" / "T.jpg.json").write_bytes(sidecar_json(1483228800))
    (base / "Archive" / "A.jpg").write_bytes(minimal_jpeg(3))
    (base / "Archive" / "A.jpg.json").write_bytes(sidecar_json(1483228800))

    out = tmp_path / "out"
    pipeline.run(Config(source=tmp_path / "src", output=out,
                        exiftool=Path(need_exiftool), rescan=True))

    assert next((out / "final").rglob("N.jpg"), None) is not None
    assert next((out / "Trash").rglob("T.jpg"), None) is not None
    assert next((out / "Archive").rglob("A.jpg"), None) is not None
    # trashed/archived media must NOT be in the main library
    assert next((out / "final").rglob("T.jpg"), None) is None
    assert next((out / "final").rglob("A.jpg"), None) is None


def test_metadata_embed(tmp_path, need_exiftool):
    import json
    import subprocess
    src = tmp_path / "src" / "Album"
    src.mkdir(parents=True)
    (src / "CAP.jpg").write_bytes(minimal_jpeg(1))
    (src / "CAP.jpg.json").write_bytes(json.dumps({
        "photoTakenTime": {"timestamp": "1483228800"},
        "description": "Sunset",
        "people": [{"name": "Alice"}, {"name": "Bob"}],
        "favorited": True,
        "geoData": {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0},
    }).encode())
    (src / "EMPTY.jpg").write_bytes(minimal_jpeg(2))
    (src / "EMPTY.jpg.json").write_bytes(json.dumps({
        "photoTakenTime": {"timestamp": "1483228800"},
        "description": "",
        "geoData": {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0},
    }).encode())

    out = tmp_path / "out"
    pipeline.run(Config(source=tmp_path / "src", output=out,
                        exiftool=Path(need_exiftool), rescan=True))

    cap = next((out / "final").rglob("CAP.jpg"))
    empty = next((out / "final").rglob("EMPTY.jpg"))
    r = subprocess.run([str(need_exiftool), "-s", "-ImageDescription", "-Rating",
                        "-PersonInImage", str(cap)], capture_output=True, text=True)
    assert "Sunset" in r.stdout
    assert "5" in r.stdout          # favorited -> rating 5
    assert "Alice" in r.stdout
    # empty description must not have been written
    r2 = subprocess.run([str(need_exiftool), "-s", "-ImageDescription", str(empty)],
                        capture_output=True, text=True)
    assert "Sunset" not in r2.stdout
    assert not r2.stdout.strip() or "ImageDescription" not in r2.stdout


def test_dry_run_writes_nothing(tmp_path):
    """--dry-run scans and caches inventory but writes no media library."""
    src = tmp_path / "src" / "Album"
    src.mkdir(parents=True)
    (src / "P.jpg").write_bytes(minimal_jpeg(1))
    (src / "P.jpg.json").write_bytes(sidecar_json(1483228800))
    out = tmp_path / "out"
    cfg = Config(source=tmp_path / "src", output=out, exiftool=Path("exiftool"),
                 rescan=True, dry_run=True)
    pipeline.run(cfg)
    assert not (out / "final").exists()          # nothing written to the library
    assert (out / "inventory.json").exists()     # inventory cached for the real run


def test_read_json_chunks(tmp_path, need_exiftool, monkeypatch):
    """read_json must return every file's data even when chunked into batches."""
    from takeout_lib import exif
    monkeypatch.setattr(exif, "BATCH_SIZE", 1)
    paths = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(minimal_jpeg(i + 1))
        paths.append(p)
    rows = exif.read_json(need_exiftool, paths, ["FileType"])
    assert len(rows) == 3


def test_extension_fix(tmp_path, need_exiftool):
    """A JPEG named .heic comes out as .jpg; a .mov that's really mp4 routes to
    videos as .mp4. With --no-extension-fix the original names are kept."""
    src = tmp_path / "src" / "Album"
    src.mkdir(parents=True)
    (src / "IMG.heic").write_bytes(minimal_jpeg(1))                 # JPEG misnamed .heic
    (src / "IMG.heic.json").write_bytes(sidecar_json(1483228800))   # 2017
    (src / "VID.mov").write_bytes(minimal_mp4(1))                   # mp4 misnamed .mov
    (src / "VID.mov.json").write_bytes(sidecar_json(1483228800))

    out = tmp_path / "out"
    cfg = Config(source=tmp_path / "src", output=out,
                 exiftool=Path(need_exiftool), rescan=True)
    pipeline.run(cfg)
    present = {p.relative_to(out / "final").as_posix()
               for p in (out / "final").rglob("*") if p.is_file()}
    assert "photos/2017/01/IMG.jpg" in present
    assert "videos/2017/01/VID.mp4" in present
    assert not any(p.endswith(".heic") or p.endswith(".mov") for p in present)

    # With the fix disabled, names are preserved.
    out2 = tmp_path / "out2"
    cfg2 = Config(source=tmp_path / "src", output=out2,
                  exiftool=Path(need_exiftool), rescan=True, fix_extensions=False)
    pipeline.run(cfg2)
    present2 = {p.relative_to(out2 / "final").as_posix()
                for p in (out2 / "final").rglob("*") if p.is_file()}
    assert "photos/2017/01/IMG.heic" in present2


def test_exif_date_waterfall(tmp_path, need_exiftool):
    """No-sidecar files with a real embedded date are kept and dated by it; a
    bogus sidecar date is overridden by the file's own EXIF date; truly undated
    no-sidecar files go to errors (or unknown with --include-undated)."""
    src = tmp_path / "src" / "Album"
    src.mkdir(parents=True)

    # No sidecar, but a real embedded date -> kept, dated by EXIF.
    jpeg_with_exif_date(src / "NOSIDE_DATED.jpg", need_exiftool, "2019:05:01 12:00:00", tag=1)
    # Bogus sidecar date (1979), but a good embedded date -> re-foldered by EXIF.
    jpeg_with_exif_date(src / "BOGUS_GOOD.jpg", need_exiftool, "2018:03:10 09:00:00", tag=2)
    (src / "BOGUS_GOOD.jpg.json").write_bytes(sidecar_json(315525610))
    # Bogus sidecar date and no embedded date -> unknown (it has a sidecar).
    (src / "BOGUS_NONE.jpg").write_bytes(minimal_jpeg(3))
    (src / "BOGUS_NONE.jpg.json").write_bytes(sidecar_json(315525610))
    # No sidecar and no embedded date -> errors by default.
    (src / "ORPHAN_NONE.jpg").write_bytes(minimal_jpeg(4))

    out = tmp_path / "out"
    cfg = Config(source=tmp_path / "src", output=out,
                 exiftool=Path(need_exiftool), rescan=True)
    pipeline.run(cfg)

    present = {p.relative_to(out / "final").as_posix()
               for p in (out / "final").rglob("*") if p.is_file()}
    assert "photos/2019/05/NOSIDE_DATED.jpg" in present
    assert "photos/2018/03/BOGUS_GOOD.jpg" in present
    assert "photos/unknown/BOGUS_NONE.jpg" in present
    # The truly-undated no-sidecar file went to errors, not final.
    assert not any(Path(p).name == "ORPHAN_NONE.jpg" for p in present)
    assert (out / "Deduplication Errors").exists()
    err_names = [p.name for p in (out / "Deduplication Errors").rglob("*") if p.is_file()]
    assert "ORPHAN_NONE.jpg" in err_names


def test_include_undated(tmp_path, need_exiftool):
    """--include-undated keeps no-sidecar/no-date files in unknown instead of errors."""
    src = tmp_path / "src" / "Album"
    src.mkdir(parents=True)
    (src / "ORPHAN_NONE.jpg").write_bytes(minimal_jpeg(4))

    out = tmp_path / "out"
    cfg = Config(source=tmp_path / "src", output=out, exiftool=Path(need_exiftool),
                 rescan=True, include_undated=True)
    pipeline.run(cfg)
    present = {p.relative_to(out / "final").as_posix()
               for p in (out / "final").rglob("*") if p.is_file()}
    assert "photos/unknown/ORPHAN_NONE.jpg" in present


def test_resume_does_not_duplicate(tmp_path, need_exiftool):
    """A crash mid-extract (checkpoint behind the files on disk) must not cause
    already-extracted files to be re-copied as _1 duplicates on resume.

    This exercises extraction only (the real crash window): embed runs after the
    checkpoint is fully saved, so the files on disk are still un-embedded and the
    content-hash resume check matches.
    """
    import json
    from takeout_lib import inventory

    src = build_source(tmp_path / "src")
    out = tmp_path / "out"
    config = Config(source=src, output=out, exiftool=Path(need_exiftool), rescan=True)

    errors = pipeline.new_errors()
    table, index = inventory.scan(config, errors)
    winners, candidates = pipeline.select_winners(table, index, config)
    pipeline.extract(winners, candidates, config, errors)

    # Simulate a crash: checkpoint records fewer files than are actually on disk.
    done = json.loads((out / "extracted.json").read_text())
    (out / "extracted.json").write_text(json.dumps(done[:1]))

    pipeline.extract(winners, candidates, config, errors)  # resume, no embed in between

    names = [p.name for p in (out / "final").rglob("*") if p.is_file()]
    assert not any("_1" in n for n in names), "resume created duplicate files"
