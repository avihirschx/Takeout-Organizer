"""Capture-date logic: plausibility and the sidecar -> EXIF -> unknown waterfall.

Pure functions. The only impurity is the *upper* plausibility bound, which
defaults to "now"; callers (and tests) can pass an explicit ``max_year`` to keep
things deterministic.
"""

from datetime import datetime, timezone

# A capture time before this year is treated as bogus: cameras with an unset
# clock default to 1980-01-01 (often shown as Dec 31 1979). Configurable by the
# caller; this is the default floor.
MIN_PLAUSIBLE_YEAR = 1995


def default_max_year():
    """Current UTC year + 1 (a little slack for timezone/clock skew)."""
    return datetime.now(tz=timezone.utc).year + 1


def is_plausible_year(year, min_year=MIN_PLAUSIBLE_YEAR, max_year=None):
    if year is None:
        return False
    if max_year is None:
        max_year = default_max_year()
    return min_year <= year <= max_year


def plausible_ts(ts, min_year=MIN_PLAUSIBLE_YEAR, max_year=None):
    """True if a unix timestamp is a believable capture time."""
    if ts is None or ts <= 0:
        return False
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return False
    return is_plausible_year(dt.year, min_year, max_year)


def year_month_from_ts(ts):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.year, dt.month


def ts_from_sidecar(data, key):
    """Read an integer unix timestamp from ``data[key]['timestamp']``, or None."""
    raw = data.get(key, {}).get("timestamp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def date_from_sidecar(data, min_year=MIN_PLAUSIBLE_YEAR, max_year=None):
    """Return (year, month) from a parsed sidecar, or (None, None).

    Uses ``photoTakenTime`` when plausible. If ``photoTakenTime`` is present but
    implausible (e.g. a 1980 camera default), returns (None, None) rather than
    guessing — so the caller can fall back to the file's own EXIF date. Only when
    ``photoTakenTime`` is entirely absent do we consider ``creationTime``.
    """
    pt = ts_from_sidecar(data, "photoTakenTime")
    if pt is not None:
        if plausible_ts(pt, min_year, max_year):
            return year_month_from_ts(pt)
        return None, None
    ct = ts_from_sidecar(data, "creationTime")
    if plausible_ts(ct, min_year, max_year):
        return year_month_from_ts(ct)
    return None, None


def parse_exif_datetime(s, min_year=MIN_PLAUSIBLE_YEAR, max_year=None):
    """Parse an EXIF/QuickTime date string ("YYYY:MM:DD HH:MM:SS", possibly with
    a timezone or sub-seconds) into a plausible (year, month), or None.

    Year/month are read straight from the string — no timezone math — which is
    what we want for foldering (the embedded value is already local capture time).
    """
    if not s or len(s) < 7:
        return None
    head = s.strip()
    if head[:4].isdigit():
        try:
            year = int(head[0:4])
            month = int(head[5:7])
        except ValueError:
            return None
    else:
        return None
    if not (1 <= month <= 12):
        return None
    if not is_plausible_year(year, min_year, max_year):
        return None
    return year, month
