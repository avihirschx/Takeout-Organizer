"""Perceptual-hash near-duplicate grouping (pure, testable).

Each image gets two fingerprints: a 256-bit **structural** dHash (16x16) that
captures its light/dark *shape* — so a Storage-saver and the original, or
re-saves at different compression, match even though their bytes differ — and a
small **color** signature (average RGB over a grid). A candidate pair found by
the structural hash is only grouped if its colors agree too (the "color gate"),
which rejects same-shape/different-color false matches and stops marginal chains
from snowballing into one giant blob. Grouping uses a BK-tree + union-find so it
scales to large libraries without an O(n^2) comparison.

Decoding images needs Pillow, imported lazily in ``fingerprint`` so the rest of
the package keeps its zero-dependency promise.
"""

import json
from collections import defaultdict
from pathlib import Path

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
              ".webp", ".heic", ".heif", ".avif"}

# Below this many images, the per-process startup cost isn't worth it.
_MIN_FOR_POOL = 8

# Structural hash: a 16x16 dHash = 256 bits. Finer than the original 8x8/64-bit,
# so different photos that merely share a light/dark layout no longer collide.
STRUCT_SIZE = 16
DEFAULT_DISTANCE = 20        # max structural Hamming distance (out of 256)

# Color signature: average RGB over a COLOR_GRID x COLOR_GRID grid → COLOR_GRID^2
# * 3 values (each 0-255). ``color_distance`` is their raw L1 sum (max
# COLOR_GRID^2 * 3 * 255 = 12240 for a 4x4 grid). The color gate rejects a
# structurally-similar pair whose colors differ by more than DEFAULT_COLOR.
# 500 ≈ 10 L1 per cell-channel: the same scene at a different JPEG quality or a
# slight brightness shift passes, but a visibly different palette is rejected.
COLOR_GRID = 4
DEFAULT_COLOR = 500


def hamming(a, b):
    return bin(a ^ b).count("1")


def dhash_from_gray(pixels, size=8):
    """dHash from a row-major grayscale (size+1) x size pixel list."""
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if pixels[base + col] > pixels[base + col + 1] else 0)
    return bits


def fingerprint(path):
    """Return ``(structural_hash, color_signature)`` for an image, or None if it
    can't be read. The structural hash is a 256-bit dHash (light/dark shape); the
    color signature is the average RGB over a small grid (the color the grayscale
    hash discards). Requires Pillow (and pillow-heif for HEIC, if registered by
    the caller)."""
    from PIL import Image  # lazy: keeps Pillow optional
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    try:
        # 'L' mode: tobytes() is one byte per pixel, row-major (stable across Pillow).
        gray = img.convert("L").resize((STRUCT_SIZE + 1, STRUCT_SIZE))
        struct = dhash_from_gray(list(gray.tobytes()), STRUCT_SIZE)
        rgb = img.convert("RGB").resize((COLOR_GRID, COLOR_GRID))
        return struct, tuple(rgb.tobytes())
    except Exception:
        return None


def color_distance(a, b):
    """L1 distance between two color signatures (sum of absolute RGB diffs)."""
    return sum(abs(x - y) for x, y in zip(a, b))


class _BKTree:
    """Burkhard-Keller tree for Hamming-distance neighbour queries."""

    def __init__(self):
        self.root = None

    def add(self, key, idx):
        if self.root is None:
            self.root = [key, idx, {}]
            return
        node = self.root
        while True:
            d = hamming(key, node[0])
            children = node[2]
            if d in children:
                node = children[d]
            else:
                children[d] = [key, idx, {}]
                return

    def near(self, key, max_dist):
        if self.root is None:
            return []
        found, stack = [], [self.root]
        while stack:
            node = stack.pop()
            d = hamming(key, node[0])
            if d <= max_dist:
                found.append(node[1])
            lo, hi = d - max_dist, d + max_dist
            for dist, child in node[2].items():
                if lo <= dist <= hi:
                    stack.append(child)
        return found


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def group_near_duplicates(hashes, max_dist, color_sigs=None, color_max=0):
    """Given structural hashes, return groups (lists of indices) of near-dups.

    The BK-tree finds structurally-close candidate pairs (within ``max_dist``).
    When ``color_sigs`` is given, a candidate is only joined if its color
    signatures are also within ``color_max`` (the color gate) — this drops
    same-shape/different-color false matches and keeps marginal chains from
    merging into one blob. Only groups with more than one member are returned."""
    tree = _BKTree()
    uf = _UnionFind(len(hashes))
    for i, h in enumerate(hashes):
        for j in tree.near(h, max_dist):
            if color_sigs is None or color_distance(color_sigs[i], color_sigs[j]) <= color_max:
                uf.union(i, j)
        tree.add(h, i)
    groups = defaultdict(list)
    for i in range(len(hashes)):
        groups[uf.find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def _init_worker():
    """Register the HEIC opener inside each pool process (the parent's
    registration doesn't carry across a spawn)."""
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass


def _hash_paths(paths, workers):
    """Return (struct_hashes, color_sigs, valid_paths) — fingerprint every path,
    dropping any that fail to decode. Uses a process pool when worth it (decoding
    is CPU-bound), falling back to sequential if the pool can't start."""
    if workers is None:
        from . import parallelism
        workers = parallelism.cpu_workers()

    results = None
    if workers > 1 and len(paths) >= _MIN_FOR_POOL:
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=workers,
                                     initializer=_init_worker) as ex:
                results = list(ex.map(fingerprint, paths, chunksize=8))
        except Exception:
            results = None  # spawn/pickle trouble -> sequential below
    if results is None:
        results = [fingerprint(p) for p in paths]

    structs, colors, valid = [], [], []
    for p, fp in zip(paths, results):
        if fp is not None:
            structs.append(fp[0])
            colors.append(fp[1])
            valid.append(p)
    return structs, colors, valid


def scan_folder(directory, distance=DEFAULT_DISTANCE, color_max=DEFAULT_COLOR, workers=None):
    """Fingerprint every photo under ``directory`` and return near-duplicate
    groups as lists of Paths, each sorted largest-file-first. Needs Pillow.
    ``distance``: max structural Hamming distance (0-256). ``color_max``: max
    color-gate distance. ``workers``: None = all cores, 1 = sequential."""
    directory = Path(directory)
    paths = [q for q in directory.rglob("*")
             if q.is_file() and q.suffix.lower() in PHOTO_EXTS]
    structs, colors, valid = _hash_paths(paths, workers)
    groups = group_near_duplicates(structs, distance, color_sigs=colors, color_max=color_max)
    return [sorted((valid[i] for i in g), key=lambda p: p.stat().st_size, reverse=True)
            for g in groups]


def save_groups(groups, base_dir, path):
    """Persist groups as JSON of base-relative posix paths."""
    base_dir = Path(base_dir)
    data = {"groups": [[p.relative_to(base_dir).as_posix() for p in g] for g in groups]}
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def load_groups(path, base_dir):
    """Load groups saved by :func:`save_groups`, resolving against ``base_dir``.
    Files that no longer exist are dropped, and groups that fall below two
    surviving members are discarded (so a stale cache degrades safely)."""
    base_dir = Path(base_dir)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for rels in data.get("groups", []):
        members = [base_dir / r for r in rels]
        members = [p for p in members if p.exists()]
        if len(members) > 1:
            out.append(sorted(members, key=lambda p: p.stat().st_size, reverse=True))
    return out
