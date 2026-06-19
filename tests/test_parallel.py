"""Parallelism: the worker-count decision, and proof that running the scan with
multiple workers produces byte-identical results to the sequential path."""

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from conftest import minimal_jpeg, sidecar_json
from takeout_lib import inventory, neardup, parallelism, pipeline
from takeout_lib.config import Config


# ── the worker-count decision ────────────────────────────────────────────────

def test_plan_io_respects_forced_modes():
    cap = parallelism.io_worker_cap()
    on, _ = parallelism.plan_io(SimpleNamespace(source=".", parallel="on"))
    off, _ = parallelism.plan_io(SimpleNamespace(source=".", parallel="off"))
    assert on == cap and off == 1


def test_plan_io_auto_follows_disk_type(monkeypatch):
    def fake(kind):
        monkeypatch.setattr(parallelism, "detect_disk_type", lambda _p: kind)
        return parallelism.plan_io(SimpleNamespace(source=".", parallel="auto"))[0]

    assert fake("ssd") == parallelism.io_worker_cap()   # SSD -> parallel
    assert fake("hdd") == 1                              # HDD -> sequential (safe)
    assert fake("unknown") == 1                          # can't tell -> sequential (safe)


def test_detect_disk_type_returns_known_label():
    assert parallelism.detect_disk_type(".") in ("ssd", "hdd", "unknown")


# ── parallel scan == sequential scan ─────────────────────────────────────────

def _build_src(root):
    """A tree with loose dir media, a sidecar, and a zip (with a dup + an orphan
    sidecar) — enough to exercise both worker types and the merge."""
    a = root / "AlbumA"
    a.mkdir(parents=True)
    (a / "PIC1.jpg").write_bytes(minimal_jpeg(1))
    (a / "PIC1.jpg.json").write_bytes(sidecar_json(1483228800))
    (a / "PIC2.jpg").write_bytes(minimal_jpeg(2))
    (root / "More").mkdir()
    (root / "More" / "PIC3.jpg").write_bytes(minimal_jpeg(3))
    with zipfile.ZipFile(root / "takeout.zip", "w") as z:
        z.writestr("Takeout/Google Photos/AlbumA/PIC1.jpg", minimal_jpeg(1))  # dup of PIC1
        z.writestr("Takeout/Google Photos/Other/PIC2.jpg.json", sidecar_json(1546300800))  # orphan
    return root


def _norm_table(table):
    return {h: sorted(json.dumps(r, sort_keys=True) for r in recs)
            for h, recs in table.items()}


def _norm_index(index):
    return {k: sorted(json.dumps(r, sort_keys=True) for r in refs)
            for k, refs in index.items()}


def test_parallel_scan_matches_sequential(tmp_path):
    src = _build_src(tmp_path / "src")

    def run(mode, out):
        cfg = Config(source=src, output=tmp_path / out, exiftool=Path("exiftool"),
                     rescan=True, parallel=mode)
        return inventory.scan(cfg, pipeline.new_errors())

    seq_table, seq_index = run("off", "out_seq")
    par_table, par_index = run("on", "out_par")

    assert _norm_table(seq_table) == _norm_table(par_table)
    assert _norm_index(seq_index) == _norm_index(par_index)
    # sanity: PIC1 really did collapse to two copies under one hash
    assert any(len(v) == 2 for v in seq_table.values())


# ── parallel fingerprinting == sequential fingerprinting ─────────────────────

def test_scan_folder_parallel_matches_sequential(tmp_path, need_pillow):
    from PIL import Image, ImageDraw
    d = tmp_path / "photos"
    d.mkdir()
    # Enough images to cross the pool threshold, including a near-dup pair.
    base = Image.new("RGB", (120, 90), (40, 80, 160))
    ImageDraw.Draw(base).ellipse((20, 20, 90, 70), fill=(230, 200, 40))
    base.save(d / "orig.jpg", quality=95)
    base.save(d / "saver.jpg", quality=30)
    for i in range(8):
        im = Image.new("RGB", (120, 90), (i * 20 % 255, 30, 60))
        ImageDraw.Draw(im).rectangle((10 + i, 10, 80, 70), fill=(200, 40 + i, 40))
        im.save(d / f"img{i}.jpg", quality=90)

    seq = neardup.scan_folder(d, workers=1)
    par = neardup.scan_folder(d, workers=4)

    def shape(groups):
        return sorted(sorted(p.name for p in g) for g in groups)

    assert shape(seq) == shape(par)
    assert ["orig.jpg", "saver.jpg"] in shape(seq)
