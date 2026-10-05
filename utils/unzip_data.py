"""
Unzipper for the ADA-2025 camera data archives.

The camera data is published as one archive per modality and bed, e.g.
hsi/hsi_W1.zip, as written by download_dataset.py:

    {data_dir}/{modality}/{modality}_{bed}.zip

Each archive holds the plant folders of that bed:

    W1_A2/2025_09_01/15/hsi_000.jp2

This script extracts the selected modalities, beds and plants next to the
archives, giving the layout the analysis scripts read:

    {data_dir}/{modality}/{bed}_{plant}/{YYYY_MM_DD}/{HH}/...

--data-dir defaults to the repo's data/ folder; pass the same --data-dir as
to download_dataset.py if you downloaded elsewhere.

Interactive by default; pass --modalities/--beds/--plants (and -y) to run
without prompts. Files already extracted with the right size are skipped, so
an interrupted run can simply be repeated.

    python utils/unzip_data.py
    python utils/unzip_data.py --modalities thermal hsi --beds W1 W2 --plants A2 A8 J5 R1 R7 -y

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm is required: pip install tqdm")
    sys.exit(1)

MODALITIES = ["thermal", "hsi", "rgb", "depth"]
BEDS = ["W1", "W2", "W3"]

# Top-level folder of every archive entry: <bed>_<plant>, e.g. W1_A2
PLANT_DIR_RE = re.compile(r"^(W\d)_([A-Z]\d+)$")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def index_archives(data_dir: Path) -> dict[tuple[str, str, str], list[tuple[Path, list[zipfile.ZipInfo]]]]:
    """
    Read the entry lists of all archives under {data_dir}/{modality}/.

    Returns {(modality, bed, plant): [(zip_path, [entries]), ...]}. Archives are
    grouped by their content, not their file name.
    """
    index: dict[tuple[str, str, str], list] = defaultdict(list)
    for mod in MODALITIES:
        mod_dir = data_dir / mod
        if not mod_dir.is_dir():
            continue
        for zip_path in sorted(mod_dir.glob("*.zip")):
            per_plant: dict[tuple[str, str], list[zipfile.ZipInfo]] = defaultdict(list)
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    top = info.filename.split("/", 1)[0]
                    m = PLANT_DIR_RE.match(top)
                    if not m or ".." in info.filename.split("/"):
                        print(f"  [WARNING] {zip_path.name}: skipping unexpected entry {info.filename}")
                        continue
                    per_plant[(m.group(1), m.group(2))].append(info)
            for (bed, plant), entries in per_plant.items():
                index[(mod, bed, plant)].append((zip_path, entries))
    return index


# ---------------------------------------------------------------------------
# Interactive multi-select UI
# ---------------------------------------------------------------------------

def _clear():
    os.system("cls" if os.name == "nt" else "clear")


def multiselect(title: str, options: list[str]) -> list[str]:
    """
    Numpad multi-select: press numbers to toggle, 0 = select all, Enter = confirm.
    """
    selected: set[int] = set()
    n = len(options)
    col_w = max(len(o) for o in options) + 10
    cols = max(1, min(3, 80 // col_w))
    message = ""

    while True:
        _clear()
        print(f"\n  {title}")
        print("  " + "=" * 62)

        for i, opt in enumerate(options):
            num = i + 1
            mark = "x" if num in selected else " "
            cell = f"[{mark}] {num}. {opt}"
            end = "\n" if (i + 1) % cols == 0 or (i + 1) == n else ""
            print(f"  {cell:<{col_w}}", end=end)

        if n % cols != 0:
            print()

        print()
        if message:
            print(f"  {message}")
        print(f"  Selected: {len(selected)}/{n}  |  0 = all, 1-{n} = toggle, Enter = confirm")
        inp = input("  > ").strip()
        message = ""

        if inp == "":
            if not selected:
                message = "Select at least one option."
            else:
                break
        else:
            for token in inp.replace(",", " ").split():
                try:
                    num = int(token)
                    if num == 0:
                        selected = set(range(1, n + 1))
                        break
                    elif 1 <= num <= n:
                        selected.discard(num) if num in selected else selected.add(num)
                    else:
                        message = f"  {num} is out of range (1-{n})."
                except ValueError:
                    message = f"  '{token}' is not a valid number."

    return [options[i - 1] for i in sorted(selected)]


def select_plants(plants: list[str]) -> list[str]:
    """
    Ask whether to unzip all plants or specific ones by ID (e.g. A2, R7).
    """
    while True:
        _clear()
        print(f"\n  Step 3 of 3 — Select plants")
        print("  " + "=" * 62)
        print(f"  {len(plants)} plant ID(s) found: {', '.join(plants)}")
        print()
        print("  Enter to unzip all, or type plant IDs separated by commas:")
        inp = input("  > ").strip().upper()

        if inp == "":
            return list(plants)

        tokens = [t.strip() for t in inp.replace(",", " ").split() if t.strip()]
        valid = [t for t in tokens if t in plants]
        invalid = [t for t in tokens if t not in plants]

        if invalid:
            print(f"  Unknown ID(s): {', '.join(invalid)}  —  valid: {', '.join(plants)}")
            input("  Press Enter to try again...")
            continue

        if not valid:
            input("  No valid plants entered. Press Enter to try again...")
            continue

        return valid


def _plant_sort_key(plant: str) -> tuple[str, int]:
    return plant[0], int(plant[1:])


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_entries(zip_path: Path, entries: list[zipfile.ZipInfo], extract_dir: Path, label: str) -> int:
    """Extract entries into extract_dir, skipping files already there with the right size."""
    todo = [e for e in entries
            if not ((extract_dir / e.filename).is_file()
                    and (extract_dir / e.filename).stat().st_size == e.file_size)]
    if not todo:
        print(f"    {label}: already extracted ({len(entries)} files)")
        return 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in tqdm(todo, desc=f"  {label}", unit="file", dynamic_ncols=True, leave=True):
            zf.extract(info, extract_dir)
    return len(todo)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ADA-2025 dataset unzipper")
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"),
                        help="Folder the dataset was downloaded to (the --data-dir of download_dataset.py); "
                             "archives are extracted next to themselves (default: the repo's data/ folder)")
    parser.add_argument("--modalities", nargs="+", choices=MODALITIES, help="Skip the modality prompt")
    parser.add_argument("--beds", nargs="+", choices=BEDS, help="Skip the bed prompt")
    parser.add_argument("--plants", nargs="+", help="Plant IDs without bed, e.g. A2 R7, or 'all' (skips the prompt)")
    parser.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    interactive = not (args.modalities and args.beds and args.plants)
    if interactive:
        _clear()
    print("\n  ADA-2025 Dataset Unzipper")
    print("  " + "=" * 62)
    print(f"\n  Data folder: {data_dir}")

    if not data_dir.is_dir():
        print(f"\n  [ERROR] Directory not found: {data_dir}")
        sys.exit(1)

    # --- Discover --------------------------------------------------------
    print("  Reading archive contents ...")
    index = index_archives(data_dir)
    if not index:
        print(f"\n  [ERROR] No archives found in {data_dir}{os.sep}<modality>{os.sep}.")
        sys.exit(1)

    plants = sorted({p for _, _, p in index}, key=_plant_sort_key)
    print("\n  Found (modality: beds):")
    for mod in MODALITIES:
        beds = sorted({b for m, b, _ in index if m == mod})
        print(f"    {mod:8s}: {', '.join(beds) if beds else '-'}")
    if interactive:
        input("\n  Press Enter to continue to selection ...")

    # --- Select ----------------------------------------------------------
    sel_modalities = args.modalities or multiselect("Step 1 of 3 — Select modalities to unzip:", MODALITIES)
    sel_beds = args.beds or multiselect("Step 2 of 3 — Select beds to unzip:", BEDS)
    if args.plants:
        sel_plants = plants if [p.lower() for p in args.plants] == ["all"] else [p.upper() for p in args.plants]
    else:
        sel_plants = select_plants(plants)

    jobs = []
    missing = []
    for mod in sel_modalities:
        for bed in sel_beds:
            for plant in sel_plants:
                sources = index.get((mod, bed, plant))
                if sources:
                    jobs.append((mod, bed, plant, sources))
                else:
                    missing.append(f"{mod}/{bed}_{plant}")

    # --- Confirmation ----------------------------------------------------
    if interactive:
        _clear()
    n_files = sum(len(e) for *_, sources in jobs for _, e in sources)
    n_bytes = sum(i.file_size for *_, sources in jobs for _, e in sources for i in e)
    print(f"\n  Ready to extract {len(jobs)} modality/plant combination(s), "
          f"{n_files:,} files, {n_bytes / 1e9:.1f} GB.")
    print(f"  Modalities : {', '.join(sel_modalities)}")
    print(f"  Beds       : {', '.join(sel_beds)}")
    print(f"  Plants     : {', '.join(sel_plants)}")
    print(f"  Output     : {data_dir}{os.sep}<modality>{os.sep}<bed>_<plant>{os.sep}")
    if missing:
        print(f"\n  [WARNING] No archive holds these {len(missing)} selection(s); they are skipped:")
        for m in missing:
            print(f"    - {m}")
    if not jobs:
        sys.exit(1)
    print()
    if not args.yes:
        ans = input("  Proceed? [y/N]: ").strip().lower()
        if ans != "y":
            print("  Aborted.")
            sys.exit(0)

    # --- Extract ---------------------------------------------------------
    errors: list[tuple[str, str]] = []
    for mod, bed, plant, sources in jobs:
        label = f"{mod}/{bed}_{plant}"
        for zip_path, entries in sources:
            try:
                extract_entries(zip_path, entries, data_dir / mod, label)
            except Exception as exc:
                errors.append((f"{zip_path} ({label})", str(exc)))
                print(f"    [ERROR] {zip_path.name} ({label}): {exc}")

    # --- Summary ---------------------------------------------------------
    print("\n  " + "=" * 62)
    if errors:
        print(f"  Finished with {len(errors)} error(s):")
        for path, err in errors:
            print(f"    {path}: {err}")
        sys.exit(1)
    print(f"  Done — {len(jobs)} modality/plant combination(s) extracted.")
    print()


if __name__ == "__main__":
    main()
