"""Unit tests for sidecar-name matching (no I/O)."""

from takeout_lib.matching import (
    find_sidecar,
    global_lookup,
    sidecar_inner,
    sidecar_dest_name,
)


def test_plain_json():
    assert find_sidecar("IMG_0001.jpg",
                        {"IMG_0001.jpg", "IMG_0001.jpg.json"}) == "IMG_0001.jpg.json"


def test_supplemental_metadata():
    names = {"IMG.jpg", "IMG.jpg.supplemental-metadata.json"}
    assert find_sidecar("IMG.jpg", names) == "IMG.jpg.supplemental-metadata.json"


def test_truncated_supplemental_variants():
    assert find_sidecar("PXL.jpg",
                        {"PXL.jpg", "PXL.jpg.supplemental-metad.json"}) \
        == "PXL.jpg.supplemental-metad.json"
    assert find_sidecar("VID.mp4",
                        {"VID.mp4", "VID.mp4.suppl.json"}) == "VID.mp4.suppl.json"
    assert find_sidecar("a.jpg", {"a.jpg", "a.jpg.s.json"}) == "a.jpg.s.json"


def test_duplicate_counter_forms():
    assert find_sidecar("IMG_1234(1).jpg",
                        {"IMG_1234(1).jpg",
                         "IMG_1234.jpg(1).supplemental-metadata.json"}) \
        == "IMG_1234.jpg(1).supplemental-metadata.json"
    assert find_sidecar("IMG_1234(2).jpg",
                        {"IMG_1234(2).jpg", "IMG_1234.jpg(2).json"}) \
        == "IMG_1234.jpg(2).json"
    assert find_sidecar("DSC(3).jpg",
                        {"DSC(3).jpg", "DSC.jpg.supplemental-metadata(3).json"}) \
        == "DSC.jpg.supplemental-metadata(3).json"


def test_edited_reuses_original():
    names = {"IMG_1487-EFFECTS-edited.jpg",
             "IMG_1487-EFFECTS.jpg.supplemental-metadata.json"}
    assert find_sidecar("IMG_1487-EFFECTS-edited.jpg", names) \
        == "IMG_1487-EFFECTS.jpg.supplemental-metadata.json"


def test_long_name_truncation_fuzzy():
    stem = "A_VERY_LONG_FILENAME_THAT_EXCEEDS_LIMIT_2024"
    assert find_sidecar(stem + "_extra.jpg",
                        {stem + "_extra.jpg", stem + ".jpg.json"}) \
        == stem + ".jpg.json"


def test_no_sidecar_returns_none():
    assert find_sidecar("IMG_9999.jpg", {"IMG_9999.jpg"}) is None


def test_no_false_match_on_different_name():
    assert find_sidecar("cat.jpg", {"cat.jpg", "dog.jpg.json"}) is None


def test_sidecar_inner():
    assert sidecar_inner("IMG.jpg.json") == "IMG.jpg"
    assert sidecar_inner("IMG.jpg.supplemental-metadata.json") == "IMG.jpg"
    assert sidecar_inner("IMG.jpg.supplemental-metad.json") == "IMG.jpg"
    assert sidecar_inner("not-a-sidecar.jpg") is None


def test_sidecar_dest_name_normalises():
    assert sidecar_dest_name("x.jpg", "x.jpg.su.json") == "x.jpg.json"
    assert sidecar_dest_name("x.jpg", "x.jpg.supplemental-metadata.json") \
        == "x.jpg.supplemental-metadata.json"


# ── global_lookup (cross-folder / cross-zip orphan fallback) ────────────────

def test_global_exact():
    index = {"ORPH.jpg": ["ref"]}
    assert global_lookup("ORPH.jpg", index) == ["ref"]


def test_global_counter():
    index = {"IMG.jpg(1)": ["ref"]}
    assert global_lookup("IMG(1).jpg", index) == ["ref"]


def test_global_truncated_long_name():
    key = "A_VERY_LONG_FILENAME_THAT_EXCEEDS_THE.jpg"
    index = {key: ["ref"]}
    assert global_lookup(
        "A_VERY_LONG_FILENAME_THAT_EXCEEDS_THE_LIMIT_2024.jpg", index) == ["ref"]


def test_global_no_false_match():
    index = {"dog.jpg": ["ref"]}
    assert global_lookup("cat.jpg", index) is None
