"""
depth_background_sensitivity.py

Depth-channel counterpart to thermal_background_sensitivity.py, answering
the same pot-overgrowth concern for the height pipeline.

analysis/plant_height/step_02_compute_metrics.py computes canopy height
(z_p25) using a single fixed EXG vegetation threshold (5.0, from
analysis/plant_height/config.py) applied to RGB-colored, depth-aligned
point clouds built by step_01_build_pointclouds.py. Because those point
clouds already carry a per-point EXG value from proper RGB<->depth
calibration (see analysis/plant_height/README.md), no re-registration is
needed here — this script just re-reads the same *_cloud.ply files at
several EXG thresholds instead of one.

Two questions:
  (a) Does the W1-vs-W2 canopy-height (z_p25) divergence survive stricter
      or looser vegetation thresholds? -> sweep EXG > {-5, 5 [published],
      20, 40}.
  (b) Does the background surface itself (points clearly not vegetation,
      EXG <= -5 fixed) differ in depth by bed? Physically it shouldn't —
      pot rim / soil surface distance from the sensor has no reason to
      depend on water treatment. A divergence here would flag a real
      confound (e.g. pot settling, soil compaction, or a registration
      problem) rather than a genuine plant-height effect.

Output: a long-format CSV, one row per (bed, plant, date).

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import csv
import re
from pathlib import Path

import numpy as np
from plyfile import PlyData

VEG_EXG_THRESHOLDS = [-5.0, 5.0, 20.0, 40.0]  # 5.0 matches the published pipeline
BACKGROUND_EXG_THRESHOLD = -5.0  # fixed: clearly non-vegetation regardless of sweep

_NAME_RE = re.compile(r"^(?P<plant_id>W[12]_[A-Z0-9]+)_(?P<date>\d{4}_\d{2}_\d{2})_(?P<hour>\d{2})_cloud\.ply$")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_PLY_DIR = _REPO_ROOT / "analysis" / "plant_height" / "processed" / "ply"
_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "processed" / "depth_background_sensitivity.csv"


def _variant_name(threshold: float) -> str:
    sign = "neg" if threshold < 0 else ""
    return f"exg_{sign}{abs(int(threshold))}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test sensitivity of the plant-height pipeline's canopy metric "
        "to EXG vegetation-threshold choice, given pot overgrowth."
    )
    parser.add_argument("--ply-dir", default=str(_DEFAULT_PLY_DIR))
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    args = parser.parse_args()

    ply_dir = Path(args.ply_dir)
    cloud_paths = sorted(ply_dir.glob("*.ply"))
    if not cloud_paths:
        raise RuntimeError(f"No .ply files found in {ply_dir} — run analysis/plant_height/step_01 first.")

    variant_keys = [_variant_name(t) for t in VEG_EXG_THRESHOLDS]
    rows: list[dict] = []

    for cloud_path in cloud_paths:
        match = _NAME_RE.fullmatch(cloud_path.name)
        if not match:
            continue
        plant_id = match.group("plant_id")
        date_str = match.group("date")
        bed = plant_id[:2]

        ply = PlyData.read(str(cloud_path))
        vertex = ply["vertex"].data
        z = np.asarray(vertex["z"], dtype=np.float64)
        exg = np.asarray(vertex["exg"], dtype=np.float64)

        row = {"bed": bed, "plant": plant_id, "date": date_str, "num_points": len(z)}

        for threshold, key in zip(VEG_EXG_THRESHOLDS, variant_keys):
            veg = z[exg > threshold]
            row[key] = round(float(np.percentile(veg, 25)), 5) if veg.size else ""

        bg = z[exg <= BACKGROUND_EXG_THRESHOLD]
        row["background"] = round(float(np.median(bg)), 5) if bg.size else ""

        rows.append(row)

    rows.sort(key=lambda r: (str(r["bed"]), str(r["plant"]), str(r["date"])))
    fieldnames = ["bed", "plant", "date", "num_points"] + variant_keys + ["background"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processed {len(rows)} point clouds -> {args.output}")


if __name__ == "__main__":
    main()
