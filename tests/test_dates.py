"""Unit tests for date plausibility and the sidecar/EXIF date logic."""

from takeout_lib import dates

# Fixed bounds so the tests don't depend on the wall clock.
BOUNDS = dict(min_year=1995, max_year=2027)

TS_2017 = 1483228800   # 2017-01-01 UTC
TS_2024 = 1717543804   # 2024-06-04 UTC
TS_1979 = 315525610    # 1979-12-31 UTC (camera default)
TS_2060 = 2840140800   # ~2060 (future)


def test_plausible_ts():
    assert dates.plausible_ts(TS_2017, **BOUNDS)
    assert dates.plausible_ts(TS_2024, **BOUNDS)
    assert not dates.plausible_ts(TS_1979, **BOUNDS)
    assert not dates.plausible_ts(TS_2060, **BOUNDS)
    assert not dates.plausible_ts(0, **BOUNDS)
    assert not dates.plausible_ts(None, **BOUNDS)


def test_year_month_from_ts():
    assert dates.year_month_from_ts(TS_2017) == (2017, 1)


def test_date_from_sidecar_uses_photo_taken_time():
    data = {"photoTakenTime": {"timestamp": str(TS_2017)}}
    assert dates.date_from_sidecar(data, **BOUNDS) == (2017, 1)


def test_date_from_sidecar_bogus_photo_taken_time_is_unknown():
    # Bogus photoTakenTime must NOT silently fall back to creationTime.
    data = {
        "photoTakenTime": {"timestamp": str(TS_1979)},
        "creationTime": {"timestamp": str(TS_2024)},
    }
    assert dates.date_from_sidecar(data, **BOUNDS) == (None, None)


def test_date_from_sidecar_creation_time_only():
    data = {"creationTime": {"timestamp": str(TS_2024)}}
    assert dates.date_from_sidecar(data, **BOUNDS) == (2024, 6)


def test_date_from_sidecar_empty():
    assert dates.date_from_sidecar({}, **BOUNDS) == (None, None)


def test_parse_exif_datetime():
    assert dates.parse_exif_datetime("2017:01:15 12:30:00", **BOUNDS) == (2017, 1)
    assert dates.parse_exif_datetime("2024:06:04", **BOUNDS) == (2024, 6)
    assert dates.parse_exif_datetime("1980:01:01 00:00:00", **BOUNDS) is None
    assert dates.parse_exif_datetime("0000:00:00 00:00:00", **BOUNDS) is None
    assert dates.parse_exif_datetime("", **BOUNDS) is None
    assert dates.parse_exif_datetime("garbage", **BOUNDS) is None
