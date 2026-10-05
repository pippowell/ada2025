#!/usr/bin/env python3
"""Compute spectral vegetation indices per date (or per timestamp) by bed, with optional plant masking.

Adapted from the hsi-plant-segmentation repository for the ADA 2025 dataset
(https://doi.org/10.26249/FK2/DMDLTE).  This is the pipeline that generates
``data/results_w5_mean_15_sa.xlsx`` from the raw HSI cubes (see the repository
README and ``analysis/hyperspectral/README.md``).

Scans the hyperspectral subset of a dataset directory for JP2 images, runs a
plant-segmentation model to create weighted masks, and computes one or more
spectral indices (NDVI, EVI, MCARI, etc.) for each image.  Aggregates
per-group statistics (mean, median, std, min, max, quartiles) by bed, writes a
CSV file (and optionally an Excel workbook with identical columns), and
produces a time-series plot with one subplot per index (line plot + grouped
box plot).

With ``--group-by day`` (default), statistics are aggregated per calendar date.
With ``--group-by hour``, statistics are aggregated per date+hour timestamp,
preserving intra-day variation.

All indices for all requested bands are computed in a single pass over the
data — each image is loaded and inferred only once regardless of how many
indices are requested.

How it works:
  1. ``scan_all_jp2`` walks the dataset directory and collects every JP2
     image matching the requested plants, hours and date window.
  2. Per image, hyperio reads the cube (wavelengths and reference-spectrum
     normalisation are embedded in the JP2 metadata), the segmentation model
     predicts a per-pixel plant probability once, and each requested index is
     computed on a single pass.
  3. The plant-masked (or whole-image) index mean is recorded per image.
  4. Per-image means are aggregated into per-bed group statistics
     (mean/median/std/min/max/quartiles) and written to CSV and Excel; a
     line + boxplot figure is produced per index.

Plants are selected with ``--plant-ids`` as full ``<bed>_<plant>`` strings
(e.g. ``W1_A2``), so different plants can be used per bed.  The default is the
paper's five plants in W1 and W2.

Dataset layout (ADA 2025).  Hour directories are zero-padded (``06``, ``09``,
``12``, ``15``, ``18``).  The paper's exemplary analyses use only the 3 pm run
(``--time-ids 15``)::

    data/hsi/<bed>_<plant>/<YYYY_MM_DD>/<HH>/hsi_000.jp2

Usage:
    python calc_spectral_index.py --index ndvi
    python calc_spectral_index.py --index ndvi,evi,mcari
    python calc_spectral_index.py --group-by hour --index ndvi
    python calc_spectral_index.py --no-mask --index ndvi
    python calc_spectral_index.py --use-masks --index ndvi
    python calc_spectral_index.py --output plot.png --csv stats.csv --xlsx stats.xlsx

By default the plant mask is produced by running the segmentation model on
each image.  With ``--use-masks`` the mask is instead read from precomputed
``hsi_000_mask.png`` images (written by ``generate_plant_masks.py``) and the
model is not loaded — identical results up to mask-quantization noise.

The default ``--data-dir`` is this repository's ``data`` folder (cubes in ``data/hsi``).  The
defaults plus ``--dark-reference`` reproduce the shipped
``data/results_w5_mean_15_sa.xlsx`` (W1/W2, 3 pm run, Sep 1-14 window,
plant-masked means, spectral window=5, all 21 indices, Savitzky-Golay
smoothing, flat-field correction with ``black_reference.npy``).  Pass
``--no-savgol`` or a subset of ``--index``/``--time-ids``/dates to deviate
from that recipe.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
import csv as csv_mod
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
from tqdm import tqdm

from hyperio import HSI

# Paths are resolved relative to this file so the script works no matter where
# it is invoked from (the vendored hyperio package in this folder is found via
# the script's own directory on sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DATA_DIR = _REPO_ROOT / "data"
_DEFAULT_MODEL = _REPO_ROOT / "analysis" / "hyperspectral" / "models" / "model.pt2"
_DEFAULT_PROCESSED = Path(__file__).resolve().parent / "processed"
_DEFAULT_DARK = _REPO_ROOT / "analysis" / "hyperspectral" / "black_reference.npy"

# ---------------------------------------------------------------------------
# Supported spectral indices
# ---------------------------------------------------------------------------

AVAILABLE_INDICES = [
    "ndvi", "evi", "savi", "msavi", "mcari", "pri", "ndwi",
    "red_edge_ndvi", "green_edge_ndvi", "ndre", "msi", "psri",
    "ari", "ci_rededge", "ccci", "sipi", "tcari_osavi", "gndvi", "datt1",
    "cri2", "mtci",
]

INDEX_LABELS = {
    "ndvi": "NDVI",
    "evi": "EVI",
    "savi": "SAVI",
    "msavi": "MSAVI",
    "mcari": "MCARI",
    "pri": "PRI",
    "ndwi": "NDWI",
    "red_edge_ndvi": "Red Edge NDVI",
    "green_edge_ndvi": "Green Edge NDVI",
    "ndre": "NDRE",
    "msi": "MSI",
    "psri": "PSRI",
    "ari": "ARI",
    "ci_rededge": "CI Red Edge",
    "ccci": "CCCI",
    "sipi": "SIPI",
    "tcari_osavi": "TCARI/OSAVI",
    "gndvi": "GNDVI",
    "datt1": "Datt1",
    "cri2": "CRI2",
    "mtci": "MTCI",
}


def compute_index(hsi: HSI, index_name: str, window: int = 3, window_agg: str = "mean") -> np.ndarray:
    """Compute a spectral index from an HSI object.

    Built-in indices (ndvi, evi, savi, etc.) delegate to the corresponding
    hyperio methods.  Custom indices use HSI.compute_index() with explicit
    formulas.

    Args:
        hsi: Hyperspectral image object.
        index_name: Name of the index to compute.
        window: Spectral averaging window size passed to hyperio.
        window_agg: Aggregation method for the spectral window ("mean", "median", or "sum").

    Returns:
        2-D float32 array of the computed index values.
    """
    kw = {"window": window, "window_agg": window_agg}

    # Built-in hyperio methods
    builtin = {
        "ndvi": lambda: hsi.ndvi(**kw),
        "evi": lambda: hsi.evi(**kw),
        "savi": lambda: hsi.savi(**kw),
        "msavi": lambda: hsi.msavi(**kw),
        "mcari": lambda: hsi.mcari(**kw),
        "pri": lambda: hsi.pri(**kw),
        "ndwi": lambda: hsi.ndwi(**kw),
    }

    if index_name in builtin:
        return builtin[index_name]()

    # Red Edge NDVI: (NIR - RedEdge) / (NIR + RedEdge), nir=800 nm, rededge=720 nm
    # Using 720 nm instead of 730 nm because 730 nm is already on the NIR plateau
    # for this sensor (R730~0.60 vs R800~0.65), yielding near-zero dynamic range.
    # At 720 nm the red-edge transition is still active (R720~0.55), giving ~0.08.
    if index_name == "red_edge_ndvi":
        return hsi.compute_index(
            lambda nir, rededge: (nir - rededge) / (nir + rededge + 1e-6),
            nir=800.0, rededge=720.0, **kw,
        )

    # Green Edge NDVI: (NIR - Green) / (NIR + Green), nir=800 nm, green=550 nm
    if index_name == "green_edge_ndvi":
        return hsi.compute_index(
            lambda nir, green: (nir - green) / (nir + green + 1e-6),
            nir=800.0, green=550.0, **kw,
        )

    # Normalized Difference Red Edge: same formula as red_edge_ndvi, rededge=720 nm
    if index_name == "ndre":
        return hsi.compute_index(
            lambda nir, rededge: (nir - rededge) / (nir + rededge + 1e-6),
            nir=800.0, rededge=720.0, **kw,
        )

    # Moisture Stress Index: NIR(860) / NIR(970)
    # Uses the 970nm water absorption feature (within VNIR sensor range up to ~1000nm)
    # instead of the traditional SWIR-based MSI. Higher values indicate water stress.
    #
    # The 940-980nm region is susceptible to sensor artifacts (low SNR, stray light,
    # dead pixels).  A spectral quality check masks pixels where the 940-980nm
    # standard deviation exceeds a threshold (0.1).  If fewer than 30% of pixels
    # pass the quality check, the entire image is flagged as NaN (unreliable).
    if index_name == "msi":
        wl_arr = hsi.wavelengths
        cube = np.asarray(hsi, dtype=np.float32)
        noise_region = (wl_arr >= 940.0) & (wl_arr <= 980.0)
        region_std = np.std(cube[:, :, noise_region], axis=2)
        clean = region_std <= 0.1
        if clean.mean() < 0.3:
            return np.full(cube.shape[:2], np.nan, dtype=np.float32)
        nir860 = hsi.nearest_band(860.0, **kw)
        nir970 = hsi.nearest_band(970.0, **kw)
        msi = nir860 / (nir970 + 1e-6)
        msi[~clean] = np.nan
        return msi.astype(np.float32)

    # Plant Senescence Reflectance Index: (Red - Green) / RedEdge
    # Tracks carotenoid/chlorophyll ratio; increases with stress-induced senescence.
    if index_name == "psri":
        return hsi.compute_index(
            lambda red, green, rededge: (red - green) / (rededge + 1e-6),
            red=680.0, green=500.0, rededge=750.0, **kw,
        )

    # Anthocyanin Reflectance Index: (1/550) - (1/700)
    # Detects anthocyanin accumulation, a common stress pigment response.
    # Reflectance floor of 0.01 prevents 1/R explosion on non-plant pixels.
    if index_name == "ari":
        return hsi.compute_index(
            lambda g550, g700: (1.0 / np.maximum(g550, 0.01)) - (1.0 / np.maximum(g700, 0.01)),
            g550=550.0, g700=700.0, **kw,
        )

    # Chlorophyll Index Red Edge: NIR/RedEdge - 1
    # Canopy chlorophyll content; highly sensitive to chlorophyll changes.
    if index_name == "ci_rededge":
        return hsi.compute_index(
            lambda nir, rededge: nir / (rededge + 1e-6) - 1.0,
            nir=790.0, rededge=720.0, **kw,
        )

    # Canopy Chlorophyll Content Index: NDRE / NDVI
    # Nitrogen status and canopy chlorophyll; ratio decouples chlorophyll from LAI.
    # NDVI is floored at 0.01 to prevent division-by-near-zero, and the result
    # is clamped to [-5, 5] (physically meaningful range for vegetation).
    if index_name == "ccci":
        ndre = hsi.compute_index(
            lambda nir, rededge: (nir - rededge) / (nir + rededge + 1e-6),
            nir=800.0, rededge=720.0, **kw,
        )
        ndvi = hsi.compute_index(
            lambda nir, red: (nir - red) / (nir + red + 1e-6),
            nir=800.0, red=670.0, **kw,
        )
        ccci = ndre / np.maximum(ndvi, 0.01)
        return np.clip(ccci, -5.0, 5.0).astype(np.float32)

    # Structure Intensive Pigment Index: (800 - 445) / (800 - 680)
    # Carotenoid:chlorophyll ratio; increases under stress as carotenoids rise relative to chlorophyll.
    if index_name == "sipi":
        return hsi.compute_index(
            lambda nir, b445, red: (nir - b445) / (nir - red + 1e-6),
            nir=800.0, b445=445.0, red=680.0, **kw,
        )

    # TCARI/OSAVI: gold standard for chlorophyll estimation, resistant to soil/LAI effects.
    # TCARI = 3 * ((700-670) - 0.2*(700-550)*(700/670))
    # OSAVI = (1+0.16)*(800-670)/(800+670+0.16)
    # OSAVI is floored at 0.01 to prevent division-by-near-zero on boundary
    # pixels, and the final ratio is clamped to [-5, 5].
    if index_name == "tcari_osavi":
        tcari = hsi.compute_index(
            lambda r700, r670, r550: 3.0 * ((r700 - r670) - 0.2 * (r700 - r550) * (r700 / (r670 + 1e-6))),
            r700=700.0, r670=670.0, r550=550.0, **kw,
        )
        osavi = hsi.compute_index(
            lambda nir, red: 1.16 * (nir - red) / (nir + red + 0.16),
            nir=800.0, red=670.0, **kw,
        )
        tco = tcari / np.maximum(osavi, 0.01)
        return np.clip(tco, -5.0, 5.0).astype(np.float32)

    # Green NDVI: (NIR - Green) / (NIR + Green)
    # General vigor using the green band instead of red; more chlorophyll-sensitive.
    if index_name == "gndvi":
        return hsi.compute_index(
            lambda nir, green: (nir - green) / (nir + green + 1e-6),
            nir=800.0, green=550.0, **kw,
        )

    # Datt1: (850 - 710) / (850 - 680)
    # Chlorophyll content; uses the red-edge shoulder for improved sensitivity.
    if index_name == "datt1":
        return hsi.compute_index(
            lambda n850, r710, r680: (n850 - r710) / (n850 - r680 + 1e-6),
            n850=850.0, r710=710.0, r680=680.0, **kw,
        )

    # Carotenoid Reflectance Index 2: (1/R510) - (1/R700)
    # Carotenoid content; more sensitive than SIPI for early pigment shifts and
    # complementary to PRI (pool size vs activation state).  Reflectance floor
    # of 0.01 prevents 1/R explosion on non-plant pixels.
    if index_name == "cri2":
        return hsi.compute_index(
            lambda g510, g700: (1.0 / np.maximum(g510, 0.01)) - (1.0 / np.maximum(g700, 0.01)),
            g510=510.0, g700=700.0, **kw,
        )

    # MERIS Terrestrial Chlorophyll Index: (R753 - R708) / (R708 - R681)
    # More sensitive than Datt1 at low-moderate stress.  All bands >680nm so
    # lighting-robust.  Denominator floored at 0.01, result clamped to [-10, 10].
    if index_name == "mtci":
        return hsi.compute_index(
            lambda r753, r708, r681: (r753 - r708) / np.maximum(r708 - r681, 0.01),
            r753=753.0, r708=708.0, r681=681.0, **kw,
        ).clip(-10, 10).astype(np.float32)

    raise ValueError(f"Unknown index: {index_name}. Choose from {AVAILABLE_INDICES}")


# ---------------------------------------------------------------------------
# Dataset scanning
# ---------------------------------------------------------------------------

def scan_all_jp2(
    dataset_dir: Path,
    plant_ids: list[str],
    time_ids: list[str] | None = None,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> list[dict]:
    """Walk the dataset directory tree and collect JP2 file records.

    ``plant_ids`` are full plant identifiers of the form ``<bed>_<plant>``,
    e.g. ``"W1_A2"``, so different plants can be selected per bed.

    Layout:
        <dataset_dir>/<bed>_<plant>/<YYYY_MM_DD>/<hour>/hsi_*.jp2

    Args:
        dataset_dir: Root dataset directory.
        plant_ids: Full plant identifiers, e.g. ["W1_A2", "W2_R1"].
        time_ids: Optional hour-of-day filter (zero-padded strings, e.g. "09", "15").
        start_date: Optional inclusive lower date bound.
        end_date: Optional inclusive upper date bound.

    Returns:
        List of dicts, each with keys: bed_id, plant_id, date, hour,
        frame_id, jp2_path, json_path.
    """
    time_set = set(time_ids) if time_ids else None
    records = []

    for plant_id in plant_ids:
        plant_dir = dataset_dir / plant_id
        if not plant_dir.is_dir():
            continue

        # Bed is the part of the plant id before the first underscore (e.g.
        # "W1_A2" -> bed "W1"); the two are aggregated per bed downstream.
        bed_id = plant_id.split("_", 1)[0]

        for date_dir in sorted(plant_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            try:
                date_parsed = datetime.strptime(date_dir.name, "%Y_%m_%d").date()
            except ValueError:
                continue
            if start_date and date_parsed < start_date:
                continue
            if end_date and date_parsed > end_date:
                continue
            for hour_dir in sorted(date_dir.iterdir()):
                if not hour_dir.is_dir():
                    continue
                if time_set is not None and hour_dir.name not in time_set:
                    continue
                for jp2 in sorted(hour_dir.glob("hsi_*.jp2")):
                    frame_id = jp2.stem.replace("hsi_", "")
                    json_path = jp2.with_suffix(".json")
                    records.append({
                        "bed_id": bed_id,
                        "plant_id": plant_id,
                        "date": date_dir.name,
                        "hour": hour_dir.name,
                        "frame_id": frame_id,
                        "jp2_path": jp2,
                        "json_path": json_path if json_path.exists() else None,
                        # Soft mask produced by generate_plant_masks.py;
                        # used with --use-masks instead of running the model.
                        "mask_path": jp2.with_name(jp2.stem + "_mask.png"),
                    })

    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        description="Compute spectral vegetation indices per date by bed, with optional plant masking.",
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
                   help=f"Path to .pt2 model for plant masking (default: {_DEFAULT_MODEL})")
    p.add_argument("--index", default=",".join(AVAILABLE_INDICES),
                   help="Comma-separated spectral indices (default: all "
                        f"{len(AVAILABLE_INDICES)} available indices, matching results_w5_mean_15_sa). "
                        "Available: " + ", ".join(AVAILABLE_INDICES))
    p.add_argument("--group-by", default="day", choices=["day", "hour"],
                   help="Grouping level for statistics: day (aggregate over hours) or hour (per timestamp); default: day")
    p.add_argument("--window", type=int, default=5,
                   help="Spectral averaging window; default: 5 (matches results_w5_mean_15_sa)")
    p.add_argument("--window-agg", default="mean", choices=["mean", "median", "sum"],
                   help="Aggregation method for the spectral window; default: mean")
    p.add_argument("--device", default=None,
                   help="Torch device (default: auto-detect)")
    p.add_argument("--output", default=None,
                   help=f"Save plot to file, e.g. plot.png/plot.pdf "
                        f"(default: {_DEFAULT_PROCESSED / 'spectral_indices_by_bed.png'})")
    p.add_argument("--csv", default=None,
                   help=f"Export per-date statistics CSV (default: {_DEFAULT_PROCESSED / 'spectral_indices_by_bed.csv'})")
    p.add_argument("--xlsx", default=None,
                   help=f"Export per-date statistics Excel workbook (default: {_DEFAULT_PROCESSED / 'spectral_indices_by_bed.xlsx'})")
    p.add_argument("--no-mask", action="store_true",
                   help="Skip plant masking; compute raw index mean (no model needed)")
    p.add_argument("--use-masks", action="store_true",
                   help="Use precomputed mask images (hsi_000_mask.png, written by "
                        "generate_plant_masks.py) instead of running the segmentation model")
    p.add_argument("--mask-threshold", type=float, default=0.5,
                   help="Minimum model prediction for a pixel to be included in plant-masked mean; default: 0.5")
    p.add_argument("--dark-reference", nargs="?", const=str(_DEFAULT_DARK), default=None,
                   help="Path to a dark (black) reference .npy for flat-field correction "
                        f"(default when bare --dark-reference: {_DEFAULT_DARK}); off unless given")
    p.add_argument("--savgol", action="store_true", default=True,
                   help="Apply Savitzky-Golay spectral smoothing before computing indices "
                        "(not applied to model inference); default: on (matches results_w5_mean_15_sa)")
    p.add_argument("--no-savgol", dest="savgol", action="store_false",
                   help="Disable Savitzky-Golay spectral smoothing")
    p.add_argument("--savgol-window", type=int, default=31,
                   help="Savitzky-Golay window length (must be odd, >0); default: 31")
    p.add_argument("--savgol-polyorder", type=int, default=3,
                   help="Savitzky-Golay polynomial order; default: 3")
    p.add_argument("--savgol-deriv", type=int, default=0,
                   help="Savitzky-Golay derivative order (0=smoothing, 1=first derivative, 2=second derivative); default: 0")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_segmentation_model(model_path: Path, device: torch.device) -> torch.nn.Module:
    """Load the TorchExport segmentation model, transparently supporting CPU-only machines.

    The shipped ``models/model.pt2`` was exported on a CUDA machine, so its
    weights and constants are serialized with device ``cuda:0``.  On a build
    without CUDA, torch.export.load() fails because it tries to move tensors
    to that device.  When ``torch.cuda`` is unavailable we therefore patch the
    (internal) archive loader to force every payload onto the CPU during load.

    Returns:
        The model module, already moved to ``device``.
    """
    if torch.cuda.is_available():
        return torch.export.load(str(model_path)).module().to(device)

    # CPU-only fallback: the PT2 archive stores every tensor on cuda:0, so
    # two load paths must be redirected to the CPU:
    #   1. flat weight/constant tensors -> deserialize_device() decides the
    #      device for tensors built from the archive's storage records;
    #   2. pickled constants -> torch.load() must receive map_location="cpu".
    original_torch_load = torch.load

    def _cpu_forced_load(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("map_location", torch.device("cpu"))
        return original_torch_load(*args, **kwargs)

    torch.load = _cpu_forced_load
    try:
        import torch.export.pt2_archive._package as _pt2_package

        _pt2_package.deserialize_device = lambda _dev: torch.device("cpu")
        return torch.export.load(str(model_path)).module().to(device)
    finally:
        # Restore torch.load so the patch never leaks into the rest of the run.
        torch.load = original_torch_load


# ---------------------------------------------------------------------------
# Mask loading
# ---------------------------------------------------------------------------

def load_mask_image(mask_path: Path) -> np.ndarray | None:
    """Load a soft plant mask written by ``generate_plant_masks.py`` as float32 in [0, 1].

    Both 8-bit (``L``) and 16-bit (``I;16``) grayscale PNGs are supported.  The
    caller applies ``--mask-threshold`` to obtain the binary plant mask.

    Returns:
        (H, W) float32 probability map, or None if the file does not exist.
    """
    if not mask_path.is_file():
        return None
    from PIL import Image

    arr = np.asarray(Image.open(mask_path))
    if arr.dtype == np.uint16:
        mask = arr.astype(np.float32) / 65535.0
    else:
        mask = arr.astype(np.float32) / 255.0
    return np.clip(mask, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Pipeline phases:
    #   1. parse & validate CLI arguments and filters
    #   2. load the segmentation model (unless --no-mask)
    #   3. scan the dataset for matching JP2 images
    #   4. compute plant-masked index means, aggregated per bed and group
    #   5. write CSV + Excel and produce the per-index figure
    args = parse_args()

    # --- Validate dataset directory ---
    dataset_dir = Path(args.data_dir) / "hsi"
    if not dataset_dir.is_dir():
        sys.exit(f"Dataset directory not found: {dataset_dir}")

    # Full plant IDs of the form <bed>_<plant>, e.g. "W1_A2" — different plants
    # per bed are allowed by simply listing them.
    plant_ids = [p.strip() for p in args.plant_ids.split(",")]
    time_ids = [t.strip() for t in args.time_ids.split(",")] if args.time_ids else None

    # Validate requested indices
    index_names = [i.strip() for i in args.index.split(",")]
    for idx in index_names:
        if idx not in AVAILABLE_INDICES:
            sys.exit(f"Unknown index '{idx}'. Choose from {AVAILABLE_INDICES}")

    group_by = args.group_by

    if args.use_masks and args.no_mask:
        sys.exit("--use-masks and --no-mask are mutually exclusive: pick one masking mode.")

    # --- Parse date filters ---
    start_date = None
    end_date = None
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y_%m_%d").date()
    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y_%m_%d").date()

    # --- Device & model ---
    device = torch.device(args.device) if args.device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    model = None

    if not args.no_mask and not args.use_masks:
        model_path = Path(args.model)
        if not model_path.exists():
            sys.exit(f"Model file not found: {model_path}")
        print(f"Loading model from {model_path} ...")
        model = load_segmentation_model(model_path, device)
    elif args.use_masks:
        print("Using precomputed mask images (generate_plant_masks.py output); skipping the model.")

    # --- Dark reference (optional flat-field correction) ---
    dark = None
    if args.dark_reference:
        dark_path = Path(args.dark_reference)
        if not dark_path.exists():
            sys.exit(f"Dark reference file not found: {dark_path}")
        dark = np.load(dark_path)
        print(f"Using dark reference from {dark_path} (flat-field correction)")

    # --- Scan for JP2 files ---
    print("Scanning JP2 files ...")
    records = scan_all_jp2(dataset_dir, plant_ids, time_ids, start_date, end_date)
    print(f"Found {len(records)} JP2 files")

    if not records:
        sys.exit("No JP2 files found. Check --data-dir, --plant-ids, --time-ids, and date filters.")

    # Build a grouping key from record fields.
    # group_by="day"  -> key = date string (e.g. "2025_09_01")
    # group_by="hour" -> key = "date_hour" (e.g. "2025_09_01_15")
    def group_key(rec: dict) -> str:
        if group_by == "hour":
            return f"{rec['date']}_{rec['hour']}"
        return rec["date"]

    # Index records by bed → group_key → list of records
    bed_index: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        bed_index[rec["bed_id"]][group_key(rec)].append(rec)

    window = args.window
    window_agg = args.window_agg
    mask_label = "" if args.no_mask else " (plant-masked)"

    # --- Compute all indices in a single pass ---
    # all_stats[index_name][bed_id][group_key] = {mean, median, std, min, max, q25, q75, iqr, n_images, values}
    # The "values" key holds the raw per-image means used for the box plots.
    all_stats: dict[str, dict[str, dict[str, dict[str, Any]]]] = {n: {} for n in index_names}
    # Track per-image outcomes so a run where every image fails (e.g. a missing
    # JPEG2000 driver) errors out instead of silently writing empty outputs.
    n_processed = 0
    n_skipped = 0

    # Iterate bed -> group -> image.  The per-image model inference is shared
    # across all requested indices, so the total work is O(#images) no matter
    # how many indices are requested.
    for bed_id in sorted(bed_index):
        # Initialise per-bed containers for each index
        for index_name in index_names:
            all_stats[index_name][bed_id] = {}

        for group_key_val in tqdm(list(sorted(bed_index[bed_id])), desc=f"Bed {bed_id}"):
            hsi_records = bed_index[bed_id][group_key_val]
            # Accumulate per-image means for each index
            per_image: dict[str, list[float]] = {n: [] for n in index_names}

            for r in hsi_records:
                try:
                    hsi = HSI.read(r["jp2_path"], dark_reference=dark)
                    cube = np.asarray(hsi, dtype=np.float32)
                    if cube.shape[2] != 300:
                        print(f"  Skip {r['jp2_path'].name}: expected 300 bands, got {cube.shape[2]}")
                        n_skipped += 1
                        continue

                    # Obtain the plant probability map once per image; shared across all
                    # indices.  Either run the segmentation model (default) or
                    # load the precomputed mask image (--use-masks).
                    pred_np = None
                    if args.use_masks:
                        pred_np = load_mask_image(r["mask_path"])
                        if pred_np is None:
                            print(f"  Skip {r['jp2_path'].name}: no mask image at {r['mask_path']}")
                            n_skipped += 1
                            continue
                        if pred_np.shape != cube.shape[:2]:
                            print(f"  Skip {r['jp2_path'].name}: mask shape {pred_np.shape} "
                                  f"does not match image shape {cube.shape[:2]}")
                            n_skipped += 1
                            continue
                    elif not args.no_mask:
                        with torch.no_grad():
                            pred = model(torch.from_numpy(cube).to(device))
                            pred_np = pred.float().cpu().squeeze().numpy()

                    # Optionally smooth spectra before index computation
                    # (NOT before model inference).
                    if args.savgol:
                        hsi = hsi.filter_savgol(
                            window_length=args.savgol_window,
                            polyorder=args.savgol_polyorder,
                            deriv=args.savgol_deriv,
                        )

                    # Compute each index on the (possibly filtered) HSI
                    for index_name in index_names:
                        idx_val = compute_index(hsi, index_name, window=window, window_agg=window_agg)

                        if args.no_mask:
                            mean_val = float(np.nanmean(idx_val))
                        else:
                            # Mean over plant pixels only (pred > mask_threshold).
                            # Using a threshold prevents boundary/soil pixels with
                            # near-zero NDVI or low reflectance from contaminating
                            # ratio indices (CCCI, TCARI/OSAVI) and reciprocal
                            # indices (ARI).
                            plant_mask = pred_np > args.mask_threshold
                            valid = idx_val[plant_mask]
                            valid = valid[np.isfinite(valid)]
                            if len(valid) > 0:
                                mean_val = float(np.nanmean(valid))
                            else:
                                mean_val = np.nan

                        if not np.isnan(mean_val):
                            per_image[index_name].append(mean_val)
                    n_processed += 1
                except Exception as e:
                    print(f"  Skip {r['jp2_path'].name}: {e}")
                    n_skipped += 1
                    continue

            # Aggregate per-image means into group-level statistics
            for index_name in index_names:
                vals = per_image[index_name]
                if vals:
                    arr = np.array(vals)
                    all_stats[index_name][bed_id][group_key_val] = {
                        "mean": float(np.mean(arr)),
                        "median": float(np.median(arr)),
                        "std": float(np.std(arr)),
                        "min": float(np.min(arr)),
                        "max": float(np.max(arr)),
                        "q25": float(np.percentile(arr, 25)),
                        "q75": float(np.percentile(arr, 75)),
                        "iqr": float(np.percentile(arr, 75) - np.percentile(arr, 25)),
                        "n_images": len(vals),
                        "values": vals,
                    }

    print(f"Processed {n_processed}/{len(records)} images ({n_skipped} skipped)")
    if n_processed == 0:
        sys.exit("No images could be processed (see the 'Skip' messages above); no outputs written. "
                 "If every image failed to open, check that the GDAL JPEG2000 driver "
                 "(conda: libgdal-jp2openjpeg) is installed.")

    # --- Write CSV ---
    csv_path = Path(args.csv) if args.csv else _DEFAULT_PROCESSED / "spectral_indices_by_bed.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["index", "bed_id", "group", "mean", "median", "std", "min", "max", "q25", "q75", "iqr", "n_images"]
    rows = []
    with open(csv_path, "w", newline="") as f:
        writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for index_name in index_names:
            stats_per_bed = all_stats[index_name]
            for bed_id in sorted(stats_per_bed):
                for group_key_val in sorted(stats_per_bed[bed_id]):
                    s = stats_per_bed[bed_id][group_key_val]
                    row = {
                        "index": index_name,
                        "bed_id": bed_id,
                        "group": group_key_val,
                        "mean": s["mean"],
                        "median": s["median"],
                        "std": s["std"],
                        "min": s["min"],
                        "max": s["max"],
                        "q25": s["q25"],
                        "q75": s["q75"],
                        "iqr": s["iqr"],
                        "n_images": s["n_images"],
                    }
                    writer.writerow(row)
                    rows.append(row)
    print(f"\nCSV saved to {csv_path}")

    # --- Write Excel workbook with identical columns ---
    import pandas as pd

    xlsx_path = Path(args.xlsx) if args.xlsx else _DEFAULT_PROCESSED / "spectral_indices_by_bed.xlsx"
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=fieldnames).to_excel(xlsx_path, index=False)
    print(f"Excel saved to {xlsx_path}")

    # --- Plot ---
    import matplotlib
    matplotlib.use("Agg")  # the figure is always saved to a file
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    # One figure per index with two panels side by side:
    #   left  — time series of the group-level mean per bed
    #   right — grouped box plot of the per-image means (one box cluster per group)
    n_indices = len(index_names)
    fig, axes = plt.subplots(n_indices, 2, figsize=(18, 5 * n_indices), squeeze=False,
                             gridspec_kw={"width_ratios": [2, 1]})

    default_colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]
    default_markers = ["o", "s", "^", "D", "v", "P", "X", "*"]

    for row_idx, index_name in enumerate(index_names):
        index_label = INDEX_LABELS.get(index_name, index_name)
        stats_per_bed = all_stats[index_name]

        # Extract mean values for the line plot
        index_per_bed: dict[str, dict[str, float]] = {
            bed: {gk: s["mean"] for gk, s in groups.items()}
            for bed, groups in stats_per_bed.items()
        }

        sorted_beds = sorted(index_per_bed)
        all_groups = sorted({gk for bed in index_per_bed.values() for gk in bed})

        # --- Left column: time-series line plot ---
        ax_line = axes[row_idx, 0]
        for i, bed_id in enumerate(sorted_beds):
            data = index_per_bed[bed_id]
            groups_sorted = sorted(data.keys())
            x = range(len(groups_sorted))
            y = [data[g] for g in groups_sorted]
            color = default_colors[i % len(default_colors)]
            marker = default_markers[i % len(default_markers)]
            ax_line.plot(x, y, marker=marker, label=bed_id, color=color)

        ax_line.set_xticks(range(len(all_groups)))
        ax_line.set_xticklabels(all_groups, rotation=45, ha="right", fontsize=8)
        group_ordinal = "Date" if group_by == "day" else "Timestamp"
        ax_line.set_ylabel(f"Mean {index_label}{mask_label}")
        ax_line.set_title(f"Mean {index_label} per {group_ordinal} by Bed{mask_label}")
        ax_line.legend()
        ax_line.grid(True, alpha=0.3)

        # --- Right column: grouped box plot per group, one box per bed ---
        ax_box = axes[row_idx, 1]
        all_groups_sorted = sorted(all_groups)
        n_groups = len(all_groups_sorted)
        n_beds = len(sorted_beds)

        box_data_grouped: list[list[float]] = []
        box_colors: list[str] = []
        box_positions: list[float] = []

        # Group spacing: each group gets a cluster of n_beds boxes
        group_width = n_beds + 0.5
        for g_idx, group_key_val in enumerate(all_groups_sorted):
            base = g_idx * group_width
            for b_idx, bed_id in enumerate(sorted_beds):
                s = stats_per_bed.get(bed_id, {}).get(group_key_val)
                if s and s.get("values"):
                    box_data_grouped.append(s["values"])
                    box_positions.append(base + b_idx)
                    box_colors.append(default_colors[b_idx % len(default_colors)])

        if box_data_grouped:
            bp = ax_box.boxplot(box_data_grouped, positions=box_positions, widths=0.8, patch_artist=True)
            for patch, color in zip(bp["boxes"], box_colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.5)

        # Label x-axis with group key at the center of each cluster
        tick_positions = [g_idx * group_width + (n_beds - 1) / 2.0 for g_idx in range(n_groups)]
        ax_box.set_xticks(tick_positions)
        ax_box.set_xticklabels(all_groups_sorted, rotation=45, ha="right", fontsize=8)

        # Legend via proxy artists
        legend_patches = [
            mpatches.Patch(facecolor=default_colors[i % len(default_colors)], alpha=0.5, label=bed_id)
            for i, bed_id in enumerate(sorted_beds)
        ]
        ax_box.legend(handles=legend_patches, loc="upper right")
        ax_box.set_ylabel(f"{index_label}{mask_label}")
        ax_box.set_title(f"{index_label} Distribution per {group_ordinal} by Bed")
        ax_box.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()

    # The plot is always saved; it lands in processed/ unless --output is given.
    out_path = Path(args.output) if args.output else _DEFAULT_PROCESSED / "spectral_indices_by_bed.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out_path), dpi=150)
    print(f"Plot saved to {out_path}")


if __name__ == "__main__":
    main()