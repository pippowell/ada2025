#!/usr/bin/env python3
"""Generate plant-segmentation mask images for every HSI cube and store them
next to the source images in the dataset folders.

Runs the bundled TorchExport plant-segmentation model (the same one
``calc_spectral_index.py`` uses by default) on every hyperspectral JP2 image
matching the requested beds, plants, hours and date window, and writes a soft
probability mask as an 8-bit grayscale PNG alongside the source file::

    data/hsi/<bed>_<plant>/<YYYY_MM_DD>/<HH>/hsi_000.jp2
    data/hsi/<bed>_<plant>/<YYYY_MM_DD>/<HH>/hsi_000_mask.png

``calc_spectral_index.py`` can then consume these precomputed masks with
``--use-masks`` instead of re-running the model.  This decouples mask
computation (once per dataset) from the index computation (many times across
indices/windows), which is convenient when re-using masks or running on a
machine without torch.

Only new masks are written by default; pass ``--overwrite`` to regenerate
existing ones.

Usage:
    python generate_plant_masks.py
    python generate_plant_masks.py --overwrite
    python generate_plant_masks.py --start-date 2025_08_12 --end-date 2025_09_22

The defaults mirror ``calc_spectral_index.py``: the paper's five plants in W1
and W2 (full ``--plant-ids`` of the form ``<bed>_<plant>``), the 3 pm run and
the Sep 1-14 analysis window.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from tqdm import tqdm

from hyperio import HSI

# Shared defaults and dataset-walking/loading helpers come from the index
# script so both tools always agree on layout, paths and the model loader.
from calc_spectral_index import (
    _DEFAULT_DARK,
    _DEFAULT_DATA_DIR,
    _DEFAULT_MODEL,
    load_segmentation_model,
    scan_all_jp2,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        description="Generate plant-segmentation mask PNGs for HSI cubes and store them "
        "next to the source images.",
    )
    p.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR),
                   help="Dataset folder holding hsi/ (default: the repo's data/ folder)")
    p.add_argument("--plant-ids", default="W1_A2,W1_A8,W1_J5,W1_R1,W1_R7,W2_A2,W2_A8,W2_J5,W2_R1,W2_R7",
                   help="Comma-separated full plant IDs of the form <bed>_<plant>, one per plant "
                        "(different plants per bed allowed); default: the paper's five plants "
                        "in W1 and W2 (A2,A8,J5,R1,R7 each)")
    p.add_argument("--time-ids", default="15",
                   help="Comma-separated hour-of-day IDs, zero-padded (e.g. 06,09,12,15,18); "
                        "default: 15 (the 3 pm run used in the paper)")
    p.add_argument("--start-date", default="2025_09_01", help="Start date filter (YYYY_MM_DD)")
    p.add_argument("--end-date", default="2025_09_14", help="End date filter (YYYY_MM_DD)")
    p.add_argument("--model", default=str(_DEFAULT_MODEL),
                   help=f"Path to .pt2 model (default: {_DEFAULT_MODEL})")
    p.add_argument("--device", default=None,
                   help="Torch device (default: auto-detect)")
    p.add_argument("--dark-reference", nargs="?", const=str(_DEFAULT_DARK), default=None,
                   help="Path to a dark (black) reference .npy for flat-field correction "
                        f"(default when bare --dark-reference: {_DEFAULT_DARK}); off unless given")
    p.add_argument("--overwrite", action="store_true",
                   help="Regenerate masks that already exist (default: skip them)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    dataset_dir = Path(args.data_dir) / "hsi"
    if not dataset_dir.is_dir():
        sys.exit(f"Dataset directory not found: {dataset_dir}")

    # Full plant IDs of the form <bed>_<plant>, e.g. "W1_A2" — different
    # plants per bed are allowed by simply listing them.
    plant_ids = [p.strip() for p in args.plant_ids.split(",")]
    time_ids = [t.strip() for t in args.time_ids.split(",")] if args.time_ids else None

    start_date = None
    end_date = None
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y_%m_%d").date()
    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y_%m_%d").date()

    device = torch.device(args.device) if args.device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    model_path = Path(args.model)
    if not model_path.exists():
        sys.exit(f"Model file not found: {model_path}")
    print(f"Loading model from {model_path} ...")
    model = load_segmentation_model(model_path, device)

    dark = None
    if args.dark_reference:
        dark_path = Path(args.dark_reference)
        if not dark_path.exists():
            sys.exit(f"Dark reference file not found: {dark_path}")
        dark = np.load(dark_path)
        print(f"Using dark reference from {dark_path} (flat-field correction)")

    print("Scanning JP2 files ...")
    records = scan_all_jp2(dataset_dir, plant_ids, time_ids, start_date, end_date)
    print(f"Found {len(records)} JP2 files")

    if not records:
        sys.exit("No JP2 files found. Check --data-dir, --plant-ids, --time-ids, and date filters.")

    n_written = 0
    n_skipped_existing = 0
    n_failed = 0
    for r in tqdm(records, desc="Generating masks"):
        mask_path = r["mask_path"]
        if mask_path.exists() and not args.overwrite:
            n_skipped_existing += 1
            continue
        try:
            hsi = HSI.read(r["jp2_path"], dark_reference=dark)
            cube = np.asarray(hsi, dtype=np.float32)
            if cube.shape[2] != 300:
                print(f"  Skip {r['jp2_path'].name}: expected 300 bands, got {cube.shape[2]}")
                n_failed += 1
                continue

            with torch.no_grad():
                pred = model(torch.from_numpy(cube).to(device))
                pred_np = pred.float().cpu().squeeze().numpy()

            # Soft probability mask scaled to [0, 255]; calc_spectral_index.py
            # --use-masks converts back to [0, 1] and applies --mask-threshold.
            mask_uint8 = (np.clip(pred_np, 0.0, 1.0) * 255).astype(np.uint8)
            Image.fromarray(mask_uint8, mode="L").save(str(mask_path))
            n_written += 1
        except Exception as e:
            print(f"  Skip {r['jp2_path'].name}: {e}")
            n_failed += 1
            continue

    print(f"\nMasks written: {n_written}, skipped (existing): {n_skipped_existing}, failed: {n_failed}")


if __name__ == "__main__":
    main()