"""Match a Google Photos Takeout media file to its JSON sidecar.

Takeout names sidecars after the media file, but mangles them in several ways
that this module untangles:

  * the standard ``<file>.json`` and ``<file>.supplemental-metadata.json``
  * truncation of the ``.supplemental-metadata`` suffix when the whole name is
    long (``.supplemental-metad.json``, ``.suppl.json``, ``.s.json``, ...)
  * the duplicate counter, which moves from before the extension on the media
    file (``IMG(1).jpg``) to after it on the sidecar (``IMG.jpg(1).json``)
  * edited copies (``IMG-edited.jpg``) that reuse the original's sidecar
  * truncation of a *long media name* itself (``A_VERY_LONG.jpg.json`` for
    ``A_VERY_LONG_NAME.jpg``)

Everything here is pure: it operates on names and in-memory sets/dicts only.
"""

import re
from pathlib import Path

SUPP_SUFFIX = ".supplemental-metadata"

# Minimum stem length for the fuzzy long-name-truncation fallback. Short names
# share prefixes too easily, so we only trust truncation matching on long stems.
MIN_FUZZY_STEM = 30


def supp_prefixes():
    """Every prefix of ``.supplemental-metadata``, longest first, including ''.

    Trying the media name followed by each prefix + ``.json`` matches whatever
    length Takeout truncated the suffix to.
    """
    return [SUPP_SUFFIX[:k] for k in range(len(SUPP_SUFFIX), -1, -1)]


def candidate_sidecars(fname):
    """Yield candidate sidecar names for ``fname``, best match first."""
    prefixes = supp_prefixes()
    for p in prefixes:
        yield fname + p + ".json"
    # Duplicate counter: media "stem(n).ext" -> sidecar built off "stem.ext"
    # with the "(n)" moved to the end, before or after the supplemental suffix.
    m = re.match(r"^(.*)(\(\d+\))(\.[^.]+)$", fname)
    if m:
        stem, counter, ext = m.groups()
        full = stem + ext
        for p in prefixes:
            yield full + counter + p + ".json"
            yield full + p + counter + ".json"


def sidecar_inner(json_name):
    """The media name a sidecar refers to: strip ``.json`` and any
    ``.supplemental-metadata`` prefix. Returns None if not a ``.json`` name.
    """
    if not json_name.endswith(".json"):
        return None
    inner = json_name[: -len(".json")]
    for p in supp_prefixes():
        if p and inner.endswith(p):
            return inner[: -len(p)]
    return inner


def find_sidecar(fname, name_set):
    """Return the sidecar filename for ``fname`` from ``name_set`` (the other
    filenames in the same folder), or None.
    """
    # Exact and known-variant matches first.
    for cand in candidate_sidecars(fname):
        if cand in name_set:
            return cand

    # Fuzzy fallback: a long media name may have a sidecar whose base was
    # truncated (e.g. "A_VERY_LONG.jpg.json" for "A_VERY_LONG_NAME.jpg").
    fname_stem = Path(fname).stem
    fname_ext = Path(fname).suffix.lower()
    for json_name in name_set:
        inner = sidecar_inner(json_name)
        if inner is None:
            continue
        inner_stem = Path(inner).stem
        inner_ext = Path(inner).suffix.lower()
        if (
            inner_ext == fname_ext
            and len(inner_stem) >= MIN_FUZZY_STEM
            and fname_stem.startswith(inner_stem)
        ):
            return json_name

    # Edited copies reuse the original's sidecar.
    if fname_stem.endswith("-edited"):
        base = fname_stem[: -len("-edited")] + fname_ext
        for cand in candidate_sidecars(base):
            if cand in name_set:
                return cand

    return None


def global_lookup(name, index):
    """Find orphan-sidecar refs for a media filename in ``index`` (a mapping of
    media-name -> list of sidecar refs), using the same matching semantics as
    :func:`find_sidecar`. Returns a list of refs, or None.

    Used only as a cross-folder fallback for photos split from their sidecar
    across zip parts; ``index`` should contain *orphan* sidecars only.
    """
    refs = index.get(name)
    if refs:
        return refs

    m = re.match(r"^(.*)(\(\d+\))(\.[^.]+)$", name)
    if m:
        stem, counter, ext = m.groups()
        refs = index.get(stem + ext + counter)
        if refs:
            return refs

    stem_p = Path(name).stem
    ext_p = Path(name).suffix

    if stem_p.endswith("-edited"):
        refs = global_lookup(stem_p[: -len("-edited")] + ext_p, index)
        if refs:
            return refs

    ext_l = ext_p.lower()
    for key, krefs in index.items():
        k_stem = Path(key).stem
        if (
            Path(key).suffix.lower() == ext_l
            and len(k_stem) >= MIN_FUZZY_STEM
            and stem_p.startswith(k_stem)
        ):
            return krefs
    return None


def sidecar_dest_name(media_name, original_sidecar_name):
    """Normalised sidecar name to write next to ``media_name`` in the output, so
    exiftool's two standard passes (``.json`` / ``.supplemental-metadata.json``)
    can find it regardless of how Takeout originally named it.
    """
    if ".supplemental-metadata.json" in original_sidecar_name:
        return media_name + ".supplemental-metadata.json"
    return media_name + ".json"
