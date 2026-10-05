"""
hyperspectral_background_sensitivity.py

Hyperspectral counterpart to the thermal/depth/RGB background-sensitivity
checks (pot-overgrowth / background).

Thermal and HSI are not geometrically co-registered to RGB/depth, so an RGB-derived vegetation
mask cannot be reprojected onto HSI pixels. Instead, this script computes
excess-green (EXG = 2*Green - Red - Blue) directly from three visible-range
bands *within the same HSI cube* used for the paper's spectral indices —
so the vegetation mask and the index values it's applied to always share
the exact same pixel grid, with no registration step at all.

EXG is thresholded by percentile of each image's own distribution (as in
thermal_background_sensitivity.py) rather than by a fixed absolute value.

Two questions, mirroring the other modalities:
  (a) Does the W1-vs-W2 NDVI divergence survive stricter/looser
      vegetation-fraction thresholds (top 10/25/50/75% of EXG per image)?
  (b) Does the background proxy (bottom 25% of EXG = soil/pot pixels)
      carry its own W1-vs-W2 NDVI signal? Bare soil reflectance is a
      well-known confound for vegetation indices over partial canopies
      (the "soil line" effect in remote sensing) and could differ by bed
      if soil moisture differs between the control and water-deficit beds.

Output: a long-format CSV, one row per (bed, plant, date).

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import csv
import json
import os
import pathlib
import sys
from pathlib import Path

import numpy as np

# On Windows, openjp2.dll may not be on PATH unless the active conda env's
# Library\bin directory is included (see paper/make_example_figure.py).
if os.name == "nt":
    _openjpeg_bin = str(pathlib.Path(sys.prefix) / "Library" / "bin")
    if pathlib.Path(_openjpeg_bin).is_dir():
        os.environ["PATH"] = _openjpeg_bin + ";" + os.environ.get("PATH", "")
        _glymur_home = pathlib.Path.home() / ".glymur"
        _glymur_home.mkdir(exist_ok=True)
        (_glymur_home / "glymurrc").write_text(
            f"[library]\nopenjp2 = {_openjpeg_bin}\\openjp2.dll\n", encoding="utf-8"
        )

import glymur

VEG_FRACTIONS = [0.10, 0.25, 0.50, 0.75]  # top-EXG fraction treated as vegetation
BACKGROUND_FRACTION = 0.25  # bottom 25% of EXG = soil/pot proxy

RED_NM, GREEN_NM, BLUE_NM, NIR_NM = 670.0, 550.0, 450.0, 800.0

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_DATA_DIR = _REPO_ROOT / "data"
_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "processed" / "hyperspectral_background_sensitivity.csv"


def _band_indices(wavelengths: np.ndarray) -> dict[str, int]:
    return {
        "red": int(np.argmin(np.abs(wavelengths - RED_NM))),
        "green": int(np.argmin(np.abs(wavelengths - GREEN_NM))),
        "blue": int(np.argmin(np.abs(wavelengths - BLUE_NM))),
        "nir": int(np.argmin(np.abs(wavelengths - NIR_NM))),
    }


def _load_cube(jp2_path: Path, wavelengths: np.ndarray) -> dict[str, np.ndarray]:
    jp2 = glymur.Jp2k(str(jp2_path))
    data = jp2[:].astype(np.float32)
    idx = _band_indices(wavelengths)

    if data.ndim == 3 and data.shape[2] == len(wavelengths):
        bands = {name: data[:, :, i] for name, i in idx.items()}
    elif data.ndim == 3 and data.shape[0] == len(wavelengths):
        bands = {name: data[i] for name, i in idx.items()}
    else:
        raise ValueError(f"Unexpected HSI shape: {data.shape}")
    return bands


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test sensitivity of NDVI to in-cube EXG vegetation-threshold choice, "
        "given pot overgrowth."
    )
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR), help="dataset folder (default: the repo's data/ folder)")
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    parser.add_argument(
        "--plant-ids", default="W1_A2,W1_A8,W1_J5,W1_R1,W1_R7,W2_A2,W2_A8,W2_J5,W2_R1,W2_R7",
        help="Comma-separated <bed>_<plant> ids (default: the paper's five plants "
             "in W1 and W2, A2,A8,J5,R1,R7 each)",
    )
    parser.add_argument("--hour", default="15")
    parser.add_argument("--start-date", default="2025_09_01")
    parser.add_argument("--end-date", default="2025_09_14")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) / "hsi"
    variant_keys = [f"ndvi_veg_{int(f * 100)}" for f in VEG_FRACTIONS]
    rows: list[dict] = []

    plant_ids = [p.strip() for p in args.plant_ids.split(",")]
    plant_dirs = [data_dir / p for p in plant_ids if (data_dir / p).is_dir()]
    missing = sorted(set(plant_ids) - {p.name for p in plant_dirs})
    if missing:
        print(f"[WARNING] No folder in {data_dir} for: {', '.join(missing)}")
    for plant_dir in plant_dirs:
        plant_id = plant_dir.name
        bed = plant_id[:2]
        if bed not in ("W1", "W2"):
            continue

        for date_dir in sorted(d for d in plant_dir.iterdir() if d.is_dir()):
            date_str = date_dir.name
            if date_str < args.start_date or date_str > args.end_date:
                continue

            hour_dir = date_dir / args.hour
            jp2_path = hour_dir / "hsi_000.jp2"
            json_path = hour_dir / "hsi_000.json"
            if not jp2_path.exists() or not json_path.exists():
                continue

            with open(json_path) as f:
                meta = json.load(f)
            wavelengths = np.array(meta["wavelengths"])

            try:
                bands = _load_cube(jp2_path, wavelengths)
            except Exception as exc:
                print(f"  [WARN] {plant_id}/{date_str}: {exc}")
                continue

            exg = (2.0 * bands["green"] - bands["red"] - bands["blue"]).ravel()
            ndvi = (
                (bands["nir"] - bands["red"]) / (bands["nir"] + bands["red"] + 1e-6)
            ).ravel()

            row = {"bed": bed, "plant": plant_id, "date": date_str}
            for frac, key in zip(VEG_FRACTIONS, variant_keys):
                threshold = np.percentile(exg, (1 - frac) * 100)
                veg = ndvi[exg >= threshold]
                row[key] = round(float(np.median(veg)), 5) if veg.size else ""

            bg_threshold = np.percentile(exg, BACKGROUND_FRACTION * 100)
            bg = ndvi[exg <= bg_threshold]
            row["ndvi_background"] = round(float(np.median(bg)), 5) if bg.size else ""

            rows.append(row)

    rows.sort(key=lambda r: (str(r["bed"]), str(r["plant"]), str(r["date"])))
    fieldnames = ["bed", "plant", "date"] + variant_keys + ["ndvi_background"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processed {len(rows)} cubes -> {args.output}")


if __name__ == "__main__":
    main()
