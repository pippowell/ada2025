"""
thermal_background_sensitivity.py

Tests a core concern for the ADA 2025 dataset: sugar beet plants
overgrow their ~10cm pots by BBCH 12-14, so a thermal (or spectral) reading
that isn't a strict vegetation mask risks mixing in soil/pot background —
which could bias the reported leaf-temperature and index trajectories.

thermal_analysis.py (the pipeline behind the paper's Fig. 4d) uses a single
fixed heuristic: the coolest 50% of pixels per thermal TIF, as a soft proxy
for canopy (leaves stay cooler than sunlit soil/pot plastic via
transpiration). This script tests how sensitive the resulting W1-vs-W2
leaf-temperature trajectory is to that choice, by recomputing it at several
"cool fraction" thresholds (stricter = closer to canopy-only, looser = more
background included), and separately tracks a "background" proxy signal
(the warmest fraction of pixels, i.e. sunlit soil/pot plastic) to check
whether it carries its own W1-vs-W2 signal that could indicate the pot/soil
itself is confounding the result (e.g. via differential soil moisture
between beds) rather than the plants themselves.

If the W2-minus-W1 divergence is stable in sign and rough magnitude across
cool-fraction thresholds, the paper's fixed 50% choice is not
background-driven. If the background-proxy trajectory diverges by bed on
its own, that is worth reporting as a limitation regardless of the
threshold-sensitivity result.

Like thermal_analysis.py, it uses the scheduled 3 pm run and falls back to a
re-run within +-1 h (14 or 16) when the 3 pm run has no valid thermal TIFs
(e.g. W2 on 2025-09-11, captured at 16:00); capture_hour records the run used.

Output: a long-format CSV, one row per (bed, plant, date, variant).

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import csv
from pathlib import Path

import numpy as np
import tifffile

COOL_FRACTIONS = [0.10, 0.25, 0.50, 0.75]  # 0.50 matches thermal_analysis.py
BACKGROUND_FRACTION = 0.25  # warmest 25% of pixels = sunlit soil/pot proxy
SCHEDULED_HOUR = 15   # the 3 pm run used in the analyses
RECOVERY_HOURS = (14, 16)  # re-runs within +-1 h that may stand in for a failed scheduled run

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_DATA_DIR = str(_REPO_ROOT / "data")
_DEFAULT_OUTPUT = str(Path(__file__).resolve().parent.parent / "processed" / "thermal_background_sensitivity.csv")


def bed_number(plant_id: str) -> int:
    """Extract bed number from plant ID, e.g. 'W2_R7' -> 2."""
    return int(plant_id[1])


def valid_thermal_tifs(date_dir: Path, hour: int) -> list[Path]:
    """Thermal TIFs of one run that exist on disk."""
    hour_dir = date_dir / f"{hour:02d}"
    if not hour_dir.is_dir():
        return []
    return [p for i in range(5) if (p := hour_dir / f"thermal_{i:03d}.tif").exists()]


def select_run(date_dir: Path, recovery: bool) -> tuple[int, list[Path]]:
    """Return (capture hour, valid TIFs): the scheduled run, or the best re-run within +-1 h if it has none."""
    tifs = valid_thermal_tifs(date_dir, SCHEDULED_HOUR)
    if tifs or not recovery:
        return SCHEDULED_HOUR, tifs
    candidates = [(h, valid_thermal_tifs(date_dir, h)) for h in RECOVERY_HOURS]
    return max(candidates, key=lambda c: len(c[1]))


def pixel_stats(path: Path) -> dict[str, float]:
    """
    Return the coolest-fraction and warmest-fraction medians for one TIF.

    Coolest fractions approximate canopy at increasing strictness; the
    warmest fraction approximates exposed soil/pot background.
    """
    data = tifffile.imread(path).astype(np.float32).ravel()
    out: dict[str, float] = {}

    for frac in COOL_FRACTIONS:
        threshold = np.percentile(data, frac * 100)
        out[f"cool_{int(frac * 100)}"] = float(np.median(data[data <= threshold]))

    bg_threshold = np.percentile(data, (1 - BACKGROUND_FRACTION) * 100)
    out["background"] = float(np.median(data[data >= bg_threshold]))

    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test sensitivity of thermal leaf-temperature readings "
        "to background (pot/soil) inclusion, given pot overgrowth."
    )
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR), help="dataset folder (default: the repo's data/ folder)")
    parser.add_argument("--output", default=_DEFAULT_OUTPUT)
    parser.add_argument(
        "--plant-ids", default="W1_A2,W1_A8,W1_J5,W1_R1,W1_R7,W2_A2,W2_A8,W2_J5,W2_R1,W2_R7",
        help="Comma-separated <bed>_<plant> ids (default: the paper's five plants "
             "in W1 and W2, A2,A8,J5,R1,R7 each)",
    )
    parser.add_argument("--start-date", default="2025_09_01")
    parser.add_argument("--end-date", default="2025_09_14")
    parser.add_argument(
        "--no-recovery", action="store_true",
        help="Use the scheduled 3 pm run only; do not fall back to re-runs within +-1 h.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir) / "thermal"
    plant_ids = [p.strip() for p in args.plant_ids.split(",")]
    plant_dirs = [data_dir / p for p in plant_ids if (data_dir / p).is_dir()]
    missing = sorted(set(plant_ids) - {p.name for p in plant_dirs})
    if missing:
        print(f"[WARNING] No folder in {data_dir} for: {', '.join(missing)}")
    print(f"Found {len(plant_dirs)} plant directories.\n")

    variant_keys = [f"cool_{int(f * 100)}" for f in COOL_FRACTIONS] + ["background"]
    rows: list[dict] = []

    for plant_dir in plant_dirs:
        plant_id = plant_dir.name
        bed = f"W{bed_number(plant_id)}"

        if bed not in ("W1", "W2"):
            continue  # W3 not used in the paper's Sept 1-14 comparison

        plant_reading_count = 0
        date_dirs = sorted(d for d in plant_dir.iterdir() if d.is_dir())
        for date_dir in date_dirs:
            date_str = date_dir.name
            if date_str < args.start_date or date_str > args.end_date:
                continue

            capture_hour, valid_tifs = select_run(date_dir, recovery=not args.no_recovery)
            if not valid_tifs:
                continue
            if capture_hour != SCHEDULED_HOUR:
                print(f"  [{plant_id}] {date_str}: no valid 15h thermal, using the {capture_hour:02d}h re-run")

            per_tif = [pixel_stats(p) for p in valid_tifs]
            aggregated = {
                key: float(np.median([s[key] for s in per_tif]))
                for key in variant_keys
            }

            row = {
                "bed": bed,
                "plant": plant_id,
                "date": date_str,
                "capture_hour": f"{capture_hour:02d}",
                "valid_tif_count": len(valid_tifs),
                **{k: round(v, 4) for k, v in aggregated.items()},
            }
            rows.append(row)
            plant_reading_count += 1

        print(f"  [{plant_id}]  {plant_reading_count} readings")

    fieldnames = ["bed", "plant", "date", "capture_hour", "valid_tif_count"] + variant_keys
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {len(rows)} readings -> {args.output}")


if __name__ == "__main__":
    main()
