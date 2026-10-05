"""
Downloads the ADA-2025 dataset from osnaData (Dataverse) into local folders.

The dataset card (doi:10.26249/FK2/DMDLTE) links out to 5 sub-datasets: one per
camera modality (hsi, thermal, rgb, depth), each holding one archive per bed
(<modality>_W1.zip, _W2.zip, _W3.zip), plus the environmental sub-dataset
(sensor parquet files, metadata workbook, leaf scans, plant images). This
script lists each sub-dataset's files via the Dataverse API and downloads them,
verifying MD5 checksums against the published metadata.

What to download:
    --paper         everything the exemplary analyses of the paper need: the
                    W1 and W2 archives of all four camera modalities plus the
                    environmental sub-dataset
    --modalities    which sub-datasets (default: all five)
    --beds          which beds' camera archives (default: all three); the
                    environmental sub-dataset is not split by bed

Everything lands under one --data-dir (default: the repo's data/ folder,
where unzip_data.py and all analysis scripts look by default; pass the same
--data-dir to them if you download elsewhere). Imagery archives
(thermal/hsi/rgb/depth) are written to {data_dir}/{modality}/{filename}, where
unzip_data.py extracts them. Environmental & metadata files are written flat
into {data_dir}/{filename}, where sensor_summary.py and the analysis scripts
read them. Files already present with the right size and checksum are skipped,
so an interrupted download can be resumed by re-running the same command.

Run from the repository root:
    python utils/download_dataset.py --list-only
    python utils/download_dataset.py --paper
    python utils/download_dataset.py --modalities thermal rgb --beds W1
    python utils/download_dataset.py --data-dir path/to/data

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
import json
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm is required: pip install tqdm")
    sys.exit(1)

API_BASE = "https://osnadata.ub.uni-osnabrueck.de"

# Sub-dataset DOIs, and where each one's files land locally.
# "staged" -> {output_dir}/{subdir}/{filename}; "flat" -> {output_dir}/{filename}
SUBDATASETS = {
    "hsi":           {"doi": "10.26249/FK2/PK9YDR", "dest": "staged", "subdir": "hsi"},
    "thermal":       {"doi": "10.26249/FK2/ASL0BV", "dest": "staged", "subdir": "thermal"},
    "rgb":           {"doi": "10.26249/FK2/FGLG94", "dest": "staged", "subdir": "rgb"},
    "depth":         {"doi": "10.26249/FK2/SMHP1Y", "dest": "staged", "subdir": "depth"},
    "environmental": {"doi": "10.26249/FK2/C0HOBN", "dest": "flat"},
}

BEDS = ["W1", "W2", "W3"]

# What the exemplary analyses in the paper use (see README, "Replicating the
# Paper Results"): control bed W1 and treatment bed W2, all four cameras, plus
# the sensor readings.
PAPER_MODALITIES = ["hsi", "thermal", "rgb", "depth", "environmental"]
PAPER_BEDS = ["W1", "W2"]

# Bed in a camera archive name: "thermal_W2.zip" (one archive per bed) or
# "W2_A2.zip" (older per-plant archives).
BED_IN_FILENAME = re.compile(r"(?:^|_)(W\d)(?=[_.])")

CHUNK_SIZE = 1024 * 1024  # 1 MB
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 5  # multiplied by attempt number


def list_files(doi: str) -> tuple[list[dict], str]:
    """Files of the latest published version of a sub-dataset, and that version (e.g. "V2.0")."""
    url = f"{API_BASE}/api/datasets/:persistentId/?persistentId=doi:{doi}"
    with urllib.request.urlopen(url) as r:
        data = json.load(r)
    latest = data["data"]["latestVersion"]
    version = f"V{latest.get('versionNumber', '?')}.{latest.get('versionMinorNumber', 0)}"
    return latest["files"], version


def bed_of(filename: str) -> str | None:
    """Bed ID named in a camera archive's filename, or None if it names none."""
    m = BED_IN_FILENAME.search(filename)
    return m.group(1) if m else None


def md5sum(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _try_download_once(url: str, tmp_path: Path, expected_size: int, expected_md5: str | None,
                        filename: str) -> tuple[bool, str]:
    """One download attempt. Returns (ok, error_message)."""
    h = hashlib.md5()
    written = 0
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, "wb") as out:
            with tqdm(
                total=expected_size, unit="B", unit_scale=True, unit_divisor=1024,
                desc=f"  {filename}", dynamic_ncols=True, leave=False,
            ) as pbar:
                while chunk := resp.read(CHUNK_SIZE):
                    out.write(chunk)
                    h.update(chunk)
                    written += len(chunk)
                    pbar.update(len(chunk))
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return False, f"network error: {exc}"

    if written != expected_size:
        return False, f"size mismatch: expected {expected_size}, got {written} bytes"
    if expected_md5 and h.hexdigest() != expected_md5:
        return False, f"checksum mismatch: expected {expected_md5}, got {h.hexdigest()}"
    return True, ""


def is_intact(path: Path, expected_size: int, expected_md5: str | None) -> bool:
    """Checks a previously-downloaded file against expected size and checksum."""
    if not path.exists() or path.stat().st_size != expected_size:
        return False
    if expected_md5 is None:
        return True
    return md5sum(path) == expected_md5


def download_file(file_id: int, filename: str, expected_size: int, expected_md5: str | None,
                   dest_path: Path) -> str:
    """Returns 'skipped', 'downloaded', or 'failed'. Retries transient failures;
    a corrupt/incomplete file (wrong size or checksum, e.g. from a dropped
    connection) is retried rather than left on disk looking "done"."""
    if is_intact(dest_path, expected_size, expected_md5):
        return "skipped"

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{API_BASE}/api/access/datafile/{file_id}"
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ok, last_error = _try_download_once(url, tmp_path, expected_size, expected_md5, filename)
        if ok:
            tmp_path.replace(dest_path)
            return "downloaded"
        tmp_path.unlink(missing_ok=True)
        if attempt < MAX_ATTEMPTS:
            print(f"    [RETRY {attempt}/{MAX_ATTEMPTS - 1}] {filename}: {last_error}")
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    print(f"    [ERROR] {filename}: {last_error} (gave up after {MAX_ATTEMPTS} attempts)")
    return "failed"


def main():
    parser = argparse.ArgumentParser(description="Download the ADA-2025 dataset from osnaData")
    parser.add_argument(
        "--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"),
        help="Destination for all downloaded files: imagery under {data-dir}/{modality}/, "
             "environmental/metadata flat at {data-dir}/ (default: the repo's data/ folder)",
    )
    parser.add_argument(
        "--paper", action="store_true",
        help="Download what the paper's exemplary analyses need: the W1 and W2 archives of "
             "all four camera modalities plus the environmental sub-dataset "
             "(cannot be combined with --modalities/--beds)",
    )
    parser.add_argument(
        "--modalities", nargs="+", choices=list(SUBDATASETS),
        help="Which sub-datasets to download (default: all)",
    )
    parser.add_argument(
        "--beds", nargs="+", choices=BEDS,
        help="Which beds' camera archives to download (default: all). "
             "Does not apply to the environmental sub-dataset",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip MD5 checksum verification after download",
    )
    parser.add_argument(
        "--list-only", action="store_true",
        help="List the selected files and their sizes, then exit without downloading",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation")
    args = parser.parse_args()

    if args.paper and (args.modalities or args.beds):
        parser.error("--paper selects modalities and beds itself; drop --modalities/--beds")
    if args.paper:
        modalities, beds = PAPER_MODALITIES, PAPER_BEDS
    else:
        modalities = args.modalities or list(SUBDATASETS)
        beds = args.beds or BEDS

    output_dir = Path(args.data_dir)

    # --- Gather file listings -------------------------------------------
    plan = []  # (modality, filename, file_id, size, md5, dest_path)
    grand_total = 0
    print("\nADA-2025 Dataset Downloader")
    print("=" * 62)
    print(f"  Sub-datasets: {' '.join(modalities)}")
    print(f"  Beds (camera archives): {' '.join(beds)}")
    print("-" * 62)
    for mod in modalities:
        cfg = SUBDATASETS[mod]
        files, version = list_files(cfg["doi"])
        if cfg["dest"] == "staged":
            # Camera archives are split by bed; keep the selected beds' archives
            # (and anything that names no bed).
            files = [f for f in files if bed_of(f["dataFile"]["filename"]) in (*beds, None)]
        mod_total = sum(f["dataFile"].get("filesize", 0) or 0 for f in files)
        grand_total += mod_total
        print(f"  {mod:14s} {len(files):2d} file(s)  {mod_total / 1e9:7.2f} GB   (doi:{cfg['doi']}, {version})")
        for f in sorted(files, key=lambda f: f["dataFile"]["filename"]):
            df = f["dataFile"]
            filename = df["filename"]
            size = df.get("filesize", 0) or 0
            if args.list_only:
                size_str = f"{size / 1e9:7.2f} GB" if size >= 1e8 else f"{size / 1e6:7.1f} MB"
                print(f"      {filename:30s} {size_str}")
            if cfg["dest"] == "staged":
                dest_path = output_dir / cfg["subdir"] / filename
            else:
                dest_path = output_dir / filename
            plan.append((mod, filename, df["id"], size,
                         df.get("checksum", {}).get("value"), dest_path))
    print("-" * 62)
    print(f"  {'TOTAL':14s}    {grand_total / 1e9:7.2f} GB")
    print()

    if args.list_only:
        return

    print(f"  Output -> {output_dir}")
    if not args.yes:
        ans = input("\n  Proceed with download? [y/N]: ").strip().lower()
        if ans != "y":
            print("  Aborted.")
            return

    # --- Download ----------------------------------------------------------
    stats = {"skipped": 0, "downloaded": 0, "failed": 0}
    for mod, filename, file_id, size, expected_md5, dest_path in plan:
        md5_to_check = None if args.no_verify else expected_md5
        result = download_file(file_id, filename, size, md5_to_check, dest_path)
        stats[result] += 1

        status_label = {
            "skipped": "SKIP (already present)",
            "downloaded": "OK",
            "failed": "FAIL",
        }[result]
        print(f"  [{status_label}] {mod}/{filename}")

    # --- Summary -------------------------------------------------------
    print("\n" + "=" * 62)
    print(f"  Downloaded: {stats['downloaded']}  Skipped (already present): {stats['skipped']}  "
          f"Failed: {stats['failed']}")
    if stats["failed"]:
        print("  Re-run the same command to retry failed files (completed ones are skipped).")
    elif args.paper:
        data_dir_arg = "" if output_dir == Path(parser.get_default("data_dir")) else f" --data-dir \"{output_dir}\""
        print("  Next, extract the ten analysis plants:")
        print(f"    python utils/unzip_data.py{data_dir_arg} --modalities thermal hsi rgb depth "
              f"--beds W1 W2 --plants A2 A8 J5 R1 R7 -y")


if __name__ == "__main__":
    main()
