"""Unit tests for perceptual-hash near-duplicate grouping and review."""

from pathlib import Path

from takeout_lib import neardup, neardup_review


def test_hamming():
    assert neardup.hamming(0, 0) == 0
    assert neardup.hamming(0b1010, 0b1000) == 1
    assert neardup.hamming(0xFF, 0x00) == 8


def test_dhash_from_gray():
    # 3x2 grayscale: row0 increasing (left<right -> 0 bits), row1 decreasing (1 bits).
    px = [10, 20, 30, 30, 20, 10]
    assert neardup.dhash_from_gray(px, size=2) == 0b0011


def test_group_near_duplicates_groups_close():
    hashes = [0b0000, 0b0001, 0xFF]   # first two are distance 1; third is far
    groups = neardup.group_near_duplicates(hashes, max_dist=1)
    assert len(groups) == 1
    assert set(groups[0]) == {0, 1}


def test_group_near_duplicates_chains():
    # 0-1 and 1-2 are each within distance 1 -> all three chain into one group.
    hashes = [0b0000, 0b0001, 0b0011]
    groups = neardup.group_near_duplicates(hashes, max_dist=1)
    assert len(groups) == 1
    assert set(groups[0]) == {0, 1, 2}


def test_group_no_dupes():
    assert neardup.group_near_duplicates([0x0, 0xF, 0xFF], max_dist=1) == []


# ── color signature + the color gate ─────────────────────────────────────────

def test_color_distance():
    assert neardup.color_distance((0, 0, 0), (0, 0, 0)) == 0
    assert neardup.color_distance((10, 20, 30), (10, 20, 35)) == 5
    assert neardup.color_distance((0, 0, 0), (255, 255, 255)) == 765   # 3 channels


def test_color_gate_blocks_structural_chains():
    # Identical structure (hash 0) would single-linkage all three together; the
    # color gate must keep the odd-colored one out.
    structs = [0, 0, 0]
    colors = [(20, 20, 200), (20, 20, 200), (20, 200, 20)]   # blue, blue, green
    # blue<->blue distance 0; blue<->green distance 360.
    groups = neardup.group_near_duplicates(structs, 0, color_sigs=colors, color_max=100)
    assert len(groups) == 1 and set(groups[0]) == {0, 1}
    # Without the gate, single-linkage merges all three.
    assert set(neardup.group_near_duplicates(structs, 0)[0]) == {0, 1, 2}


def test_fingerprint_color_separates_flat_images(tmp_path, need_pillow):
    from PIL import Image
    d = tmp_path / "p"
    d.mkdir()
    # Two flat images: same (zero) structure, very different color.
    Image.new("RGB", (48, 48), (20, 20, 200)).save(d / "blue.jpg", quality=90)
    Image.new("RGB", (48, 48), (20, 200, 20)).save(d / "green.jpg", quality=90)
    assert neardup.scan_folder(d, workers=1) == []          # gate keeps them apart
    # A second blue (different quality) IS a near-dup of the first.
    Image.new("RGB", (48, 48), (20, 20, 200)).save(d / "blue2.jpg", quality=30)
    groups = neardup.scan_folder(d, workers=1)
    assert any({p.name for p in g} == {"blue.jpg", "blue2.jpg"} for g in groups)


# ── review: safety-critical apply + page (no Pillow needed) ──────────────────

def test_apply_deletions_moves_only_known_ids(tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    registry = {0: {"path": a}, 1: {"path": b}}
    removed = tmp_path / "removed"

    moved = neardup_review.apply_deletions([1], registry, removed)
    assert moved == 1
    assert not b.exists()                 # rejected copy moved out
    assert a.exists()                     # keeper untouched
    assert (removed / "b.jpg").exists()   # recoverable, not destroyed
    # an unknown id can never touch anything
    assert neardup_review.apply_deletions([99], registry, removed) == 0


def test_review_page_renders_groups():
    reg = {
        0: {"path": Path("x"), "name": "big.jpg", "size": 2000, "w": 100, "h": 80,
            "group": 0, "suggest_keep": True},
        1: {"path": Path("y"), "name": "small.jpg", "size": 500, "w": 50, "h": 40,
            "group": 0, "suggest_keep": False},
    }
    page = neardup_review._page(reg)
    assert "big.jpg" in page and "small.jpg" in page
    assert 'data-id="0"' in page and 'data-id="1"' in page
    assert "/apply" in page


def test_review_server_round_trip(tmp_path, need_pillow):
    """Start the server, POST a delete selection, confirm the file moved."""
    import json
    import threading
    import time
    import urllib.request
    from PIL import Image

    d = tmp_path / "photos"
    d.mkdir()
    big, small = d / "big.jpg", d / "small.jpg"
    im = Image.new("RGB", (200, 150), (40, 80, 160))
    im.save(big, quality=95)
    im.save(small, quality=30)
    groups = [[big, small]]                # largest first
    removed = tmp_path / "removed"

    out = {}
    th = threading.Thread(
        target=lambda: out.__setitem__(
            "moved", neardup_review.serve_review(groups, removed, port=8811, open_browser=False)),
        daemon=True)
    th.start()

    base = "http://127.0.0.1:8811"
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    req = urllib.request.Request(
        base + "/apply", data=json.dumps({"delete": [1]}).encode(),
        headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
    th.join(timeout=5)

    assert resp["moved"] == 1 and out.get("moved") == 1
    assert not small.exists() and big.exists()
    assert (removed / "small.jpg").exists()


# ── precomputed-group cache + dedup prep step ────────────────────────────────

def test_save_load_groups_round_trip(tmp_path):
    base = tmp_path / "photos"
    (base / "sub").mkdir(parents=True)
    a, b, c = base / "a.jpg", base / "b.jpg", base / "sub" / "c.jpg"
    for f in (a, b, c):
        f.write_bytes(b"x")
    path = tmp_path / "g.json"
    neardup.save_groups([[a, b, c]], base, path)

    loaded = neardup.load_groups(path, base)
    assert len(loaded) == 1 and {p.name for p in loaded[0]} == {"a.jpg", "b.jpg", "c.jpg"}
    # a missing file is dropped; a group still needs >= 2 survivors
    a.unlink()
    assert len(neardup.load_groups(path, base)[0]) == 2
    b.unlink()
    assert neardup.load_groups(path, base) == []     # only one left -> dropped


def _cfg(tmp_path, mode):
    from takeout_lib.config import Config
    out = tmp_path / "out"
    (out / "final" / "photos").mkdir(parents=True)
    return Config(source=tmp_path, output=out, exiftool=Path("exiftool"), near_dupes=mode)


def test_prepare_defer_writes_launcher_only(tmp_path):
    from takeout_lib import neardup_prep
    cfg = _cfg(tmp_path, "defer")
    (cfg.photos_dir / "a.jpg").write_bytes(b"x")
    neardup_prep.prepare(cfg, repo_dir=tmp_path / "repo", python_exe="py")
    txt = cfg.near_dup_launcher.read_text()
    assert "find_near_dupes.py" in txt and "photos" in txt
    assert "--groups" not in txt                     # defer = no precomputed cache
    assert not cfg.near_dup_groups.exists()


def test_prepare_off_does_nothing(tmp_path):
    from takeout_lib import neardup_prep
    cfg = _cfg(tmp_path, "off")
    neardup_prep.prepare(cfg, repo_dir=tmp_path, python_exe="py")
    assert not cfg.near_dup_launcher.exists()


def test_prepare_scan_precomputes(tmp_path, need_pillow):
    from PIL import Image, ImageDraw
    from takeout_lib import neardup_prep
    cfg = _cfg(tmp_path, "scan")
    # A detailed image saved at two qualities = a near-dup pair; a different image is not.
    base = Image.new("RGB", (120, 90), (40, 80, 160))
    ImageDraw.Draw(base).ellipse((20, 20, 90, 70), fill=(230, 200, 40))
    base.save(cfg.photos_dir / "orig.jpg", quality=95)
    base.save(cfg.photos_dir / "saver.jpg", quality=30)
    other = Image.new("RGB", (120, 90), (10, 10, 10))
    ImageDraw.Draw(other).rectangle((30, 20, 100, 70), fill=(200, 40, 40))
    other.save(cfg.photos_dir / "other.jpg", quality=90)

    neardup_prep.prepare(cfg, repo_dir=tmp_path / "repo", python_exe="py")
    assert cfg.near_dup_groups.exists()
    assert "--groups" in cfg.near_dup_launcher.read_text()
    groups = neardup.load_groups(cfg.near_dup_groups, cfg.photos_dir)
    assert len(groups) == 1 and len(groups[0]) == 2
