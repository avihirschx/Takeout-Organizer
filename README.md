# Takeout Organizer

Turn a messy **Google Photos Takeout** into a clean, deduplicated, correctly-dated
library you can upload anywhere (Amazon Photos, iCloud, a NAS, …).

Point it at your Takeout (zips and/or extracted folders); it reads everything,
drops exact duplicates, attaches each photo's Google metadata, and writes a tidy
`final/photos/YYYY/MM` and `final/videos/YYYY/MM` tree. **Your source is only ever
read — never modified.**

```bash
python dedup.py  --source "D:/Takeout"  --output "D:/Cleaned"
python verify.py --output "D:/Cleaned"      # then confirm before deleting anything
```

## Why this exists

Google Takeout is full of traps that naive copy/merge tools get wrong. This handles
the ones encountered cleaning a real ~120k-file library:

- **Exact-duplicate removal** by SHA-256 — the same photo repeats across albums and
  across overlapping Takeout zips; only one copy is kept.
- **Sidecar matching** through Google's name mangling: the standard
  `<file>.json` / `<file>.supplemental-metadata.json`, **truncated** suffixes
  (`.supplemental-metad.json`, `.suppl.json`, `.s.json`), the **duplicate counter**
  that hops from `IMG(1).jpg` to `IMG.jpg(1).json`, **`-edited`** copies, and
  **long-name truncation**.
- **Cross-zip recovery** — when Takeout splits a photo from its sidecar into a
  different zip part, the orphaned sidecar is matched back by name.
- **Bogus dates** — a camera with an unset clock writes 1980 (shown as Dec 1979);
  those are rejected instead of being trusted.
- **EXIF fallback** — a photo with no usable sidecar date but a real embedded
  capture date is dated by that, not dumped aside.
- **Mistaken extensions** — Google often stores JPEGs named `.heic` (and `.mov`
  that are really `.mp4`, etc.); files are renamed to match their true content.
- **Photo / video split** — written to separate trees so you can send photos and
  videos to different places.
- **Live Photos** — the short iPhone motion `.mp4` clips (which have no sidecar of
  their own) are detected and handled separately instead of cluttering your videos.
- **Trash & Archive preserved** — media Google had trashed or archived goes to its
  own `Trash/` and `Archive/` folders, so you can delete the source without losing
  anything.
- **Albums rebuilt** — album membership (lost when flattening to date folders) is
  recreated as a parallel `albums/` tree of hardlinks (no extra disk space) plus an
  `albums.json` manifest.
- **Rich metadata** — date and GPS, plus the caption, tagged people, and favorite
  star from the sidecar, embedded into each file.
- **Near-duplicate cleanup** (optional tool) — find visually-similar copies (e.g.
  Storage-saver vs original) and pick the keepers in a quick browser review.
- **GPS sanity** — a `0,0` "no location" coordinate is never stamped in.
- **Resume-safe** — interrupt it and re-run; it picks up where it left off without
  creating duplicate copies.

## Requirements

- **Python 3.9+** (no third-party runtime dependencies)
- **[ExifTool](https://exiftool.org/)** on your `PATH` (or pass `--exiftool`), used
  to embed and read metadata.

## Install

Set up a virtual environment and **run everything from it** — `dedup.py`,
`verify.py`, and the tools. The core pipeline itself has no third-party
dependencies, but the venv is the standard runtime: it holds Pillow (for the
near-duplicate fingerprinting/review) and is where any future dependencies live.

```bash
git clone https://github.com/avihirschx/Takeout-Organizer.git
cd Takeout-Organizer
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -e ".[dev,neardup]"     # dev = pytest, neardup = Pillow + pillow-heif
```

Then run with the venv's Python, e.g. on Windows:

```bash
.venv\Scripts\python dedup.py --source "D:/Takeout" --output "D:/Cleaned"
```

(Running `dedup.py` outside the venv still works — it just skips the near-dup
fingerprinting, which the review then does on first open.)

## Usage

```bash
python dedup.py --source SRC --output OUT [options]
```

| Option | Meaning |
| --- | --- |
| `--source` | Folder with your Takeout (zips and/or extracted dirs). Read-only. |
| `--output` | Where the cleaned library is written (created if missing). |
| `--exiftool` | Path to exiftool (default: found on `PATH`). |
| `--rescan` | Ignore the cached inventory and scan from scratch. |
| `--min-year` | Dates before this year are treated as bogus (default 1995). |
| `--no-extension-fix` | Keep original (possibly wrong) extensions. |
| `--live-motion` | `archive` (default) · `drop` · `keep` · `errors` — see below. |
| `--albums` | `link` (default, hardlinks) · `copy` · `manifest` · `none` — rebuild album folders. |
| `--near-dupes` | `scan` (default) · `defer` · `off` — near-dup review prep (runs on `final/photos` after dedup). |
| `--parallel` | `auto` (default) · `on` · `off` — multi-worker scan + fingerprinting. `auto` parallelizes only when the source is on an SSD; see below. |
| `--include-undated` | Put no-sidecar/no-date real media in `final/<kind>/unknown` instead of the errors folder. |
| `--dry-run` | Scan and report what would happen; write nothing to the library. |

Then **always** verify before deleting your source:

```bash
python verify.py --output OUT
```
It cross-checks counts, lists anything set aside, spot-checks embedded dates, and
prints **SAFE TO DELETE** or **DO NOT DELETE**.

## Speed (`--parallel`)

The slowest parts are reading every byte to hash it (phase 1) and decoding photos
to fingerprint near-duplicates. Both can use multiple workers:

- **`auto`** *(default)* — detects the drive holding `--source` (Windows, Linux,
  and macOS, best-effort). On an **SSD/NVMe** it runs several reader threads (they
  overlap and approach the disk's max throughput) and fingerprints across all
  cores. On a **spinning HDD**, or whenever detection is unsure, it stays
  **sequential** — parallel reads make a single head seek back and forth and
  actually run *slower*. If `auto` won't recognize your SSD, use `--parallel on`.
- **`on`** — force parallel (use if `auto` can't detect your SSD).
- **`off`** — fully sequential (best for an external/USB HDD).

Parallelism only changes the speed, never the result — a wrong guess is at worst
"not as fast as it could be", never a corrupt or different library.

## Output layout

```
OUT/
  final/
    photos/YYYY/MM/...      # → upload to your photo service
    photos/unknown/...      # real photos with no trustworthy date
    videos/YYYY/MM/...      # → upload videos wherever you like
  albums/<Album Name>/...   # hardlinks into final/ (no extra space); see albums.json
  Archive/                  # media Google had archived
  Trash/                    # media Google had trashed
  Live Photo motion/        # short iPhone motion clips (with --live-motion archive)
  Deduplication Errors/     # files with no sidecar and no embedded date
  review-near-dupes.cmd     # double-click to review near-duplicates (see below)
  albums.json  near-dup-groups.json  inventory.json  extracted.json  ...
```

## Live Photo handling

An iPhone Live Photo is a still **plus** a separate ~3-second `.mp4`. Takeout gives
the motion clip no sidecar of its own, so `--live-motion` decides what to do with it:

- `archive` *(default)* — move the clips to `OUT/Live Photo motion/`; nothing lost,
  nothing cluttering your library.
- `drop` — discard them.
- `keep` — treat them as ordinary videos (they land in `final/videos`).
- `errors` — leave them in the errors folder.

## How it works

1. **Inventory** — hash every file (exact-dup detection) and index sidecars; cached.
2. **Select** — one copy per unique file; recover orphaned sidecars across zips.
3. **Extract** — copy to `final/<kind>/YYYY/MM`, dated by sidecar → else embedded EXIF
   → else `unknown`; fix extensions; divert Live Photo motion.
4. **Embed** — write the metadata into the files (EXIF for photos, QuickTime for
   videos), then delete the now-redundant sidecars.
5. **Verify** — confirm counts and spot-check dates.

## Limitations

- **Videos**: the capture date is written to the QuickTime atoms; date-folders use
  local time, so a clip near midnight UTC may sit in the adjacent month.
- **Live Photo re-bundling** (recombining still + motion into one playable file) is
  not implemented — clips are archived instead.
- Files with an extension outside the known media set are ignored.

## Extra tools (`tools/`)

Standalone post-processing utilities. **Convention:** `--dry-run` reports what
would change and changes nothing; otherwise the tool runs for real but **asks for
confirmation first** (pass `--yes` to skip the prompt in scripts).

- **`find_near_dupes.py`** — finds visually-similar photos and opens a **local
  browser review**: each group shown side by side with thumbnails/sizes and a
  keep/delete toggle. Click *Apply* and the rejects move to a recoverable
  `near-dup-removed/` folder; keepers stay put. Needs the `neardup` extra (Pillow).

  You normally don't run this by hand: a `dedup.py` run (with the default
  `--near-dupes scan`) fingerprints `final/photos` and drops a
  **`review-near-dupes.cmd`** launcher in the output — double-click it whenever
  you want to review, and it opens instantly using the precomputed groups. To run
  it standalone on any folder:
  `.venv\Scripts\python tools\find_near_dupes.py --dir OUT/final/photos`.
- **`fix_extensions.py`** — rename mistaken extensions in any folder (the main
  pipeline already does this inline).
- **`refolder_unknown.py`** — move dated files out of `unknown/` (also inline now).

## Development

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev,neardup]"
pytest            # unit tests always run; integration tests skip without ExifTool
```

CI (GitHub Actions) installs ExifTool and runs the full suite on every push.

## License

MIT — see [LICENSE](LICENSE).

## Colophon

Authored by **Avi Hirsch**, pair-programmed with **Claude** (Anthropic).
