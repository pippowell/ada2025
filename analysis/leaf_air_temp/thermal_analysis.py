"""
thermal_analysis.py

Computes leaf temperature from thermal TIF images and compares
against air temperature sensor readings for the same time window.

For each valid plant/date at the scheduled 3 pm run:
  - Reads up to 5 thermal TIFs
  - If the 3 pm run has no valid thermal TIFs, falls back to a re-run within +-1 h
    (14 or 16; the one with more valid TIFs). Re-runs were made after failed
    scheduled runs, e.g. W2/W3 thermal on 2025-09-11 was captured at 16:00.
    The reading keeps hour "15" (the scheduled slot) and records the run it
    came from in capture_hour. --no-recovery disables the fallback.
  - Takes the coolest 50% of pixels per TIF (largely the plants themselves) and takes their median
  - Takes the median of that across all valid TIFs to get one leaf temperature reading
  - Pulls the median air temperature from the 10-minute window matching the bed in the
    run actually used (the robot takes about 10 minutes to scan each bed):
      W1 (bed 1, control) → first 10 min past the hour
      W2 (bed 2, water deficit) → second 10 min past the hour
      W3 (bed 3, water deficit) → third 10 min past the hour
  - Records diff = air_temp - leaf_temp for each sensor (note: the paper and
    analysis/combined/run_stats.py use the opposite sign, leaf - air, which
    run_stats.py recomputes from leaf_temp_c and the air readings)

Output is a JSON file keyed by plant ID, each containing a list of readings.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import tifffile


COOL_FRACTION = 0.50  # use the coolest this fraction of pixels
SCHEDULED_HOUR = 15   # the 3 pm run used in the analyses
RECOVERY_HOURS = (14, 16)  # re-runs within +-1 h that may stand in for a failed scheduled run

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DATA_DIR = str(_REPO_ROOT / "data")


def bed_number(plant_id: str) -> int:
    """Extract bed number from plant ID, e.g. 'W2_R7' -> 2."""
    return int(plant_id[1])


def air_window_minutes(bed: int) -> tuple[int, int]:
    """Return (start_minute, end_minute) past the hour for the given bed."""
    start = (bed - 1) * 10
    return start, start + 10


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


def leaf_temp_from_tif(path: Path) -> float:
    """Return median temperature of the coolest COOL_FRACTION pixels in a TIF."""
    data = tifffile.imread(path).astype(np.float32).ravel()
    threshold = np.percentile(data, COOL_FRACTION * 100)
    return float(np.median(data[data <= threshold]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute leaf-to-air temperature differences from thermal TIFs."
    )
    parser.add_argument(
        "--data-dir", default=_DEFAULT_DATA_DIR,
        help="Dataset folder with thermal/ and air_readings.parquet (default: the repo's data/ folder).",
    )
    parser.add_argument(
        "--plant-ids", default="W1_A2,W1_A8,W1_J5,W1_R1,W1_R7,W2_A2,W2_A8,W2_J5,W2_R1,W2_R7",
        help="Comma-separated <bed>_<plant> ids (default: the paper's five plants "
             "in W1 and W2, A2,A8,J5,R1,R7 each)",
    )
    parser.add_argument(
        "--output", default=str(Path(__file__).resolve().parent / "thermal_analysis.json"),
        help="Output JSON file path (default: next to this script).",
    )
    parser.add_argument(
        "--start-date", default="2025_09_01",
        help="Earliest date folder to include (inclusive), format YYYY_MM_DD.",
    )
    parser.add_argument(
        "--end-date", default="2025_09_14",
        help="Latest date folder to include (inclusive), format YYYY_MM_DD.",
    )
    parser.add_argument(
        "--no-recovery", action="store_true",
        help="Use the scheduled 3 pm run only; do not fall back to re-runs within +-1 h.",
    )
    args = parser.parse_args()

    print("Loading air readings parquet...")
    air_df = pd.read_parquet(Path(args.data_dir) / "air_readings.parquet")

    data_dir = Path(args.data_dir) / "thermal"
    results: dict[str, list[dict]] = {}
    total_readings = 0

    plant_ids = [p.strip() for p in args.plant_ids.split(",")]
    plant_dirs = [data_dir / p for p in plant_ids if (data_dir / p).is_dir()]
    missing = sorted(set(plant_ids) - {p.name for p in plant_dirs})
    if missing:
        print(f"[WARNING] No folder in {data_dir} for: {', '.join(missing)}")
    print(f"Found {len(plant_dirs)} plant directories.\n")

    for plant_dir in plant_dirs:
        plant_id = plant_dir.name

        bed = bed_number(plant_id)
        win_start_min, win_end_min = air_window_minutes(bed)
        plant_results: list[dict] = []

        date_dirs = sorted(d for d in plant_dir.iterdir() if d.is_dir())
        for date_dir in date_dirs:
            date_str = date_dir.name

            if date_str < args.start_date or date_str > args.end_date:
                continue

            # Thermal TIFs of the scheduled run, or of a re-run within +-1 h if
            # the scheduled run has none
            capture_hour, valid_tifs = select_run(date_dir, recovery=not args.no_recovery)
            if not valid_tifs:
                continue
            capture_hour_str = f"{capture_hour:02d}"
            if capture_hour != SCHEDULED_HOUR:
                print(f"  [{plant_id}] {date_str}: no valid 15h thermal, using the {capture_hour_str}h re-run")

            # Per-TIF: median of coolest COOL_FRACTION pixels
            tif_temps = [leaf_temp_from_tif(p) for p in valid_tifs]
            leaf_temp = float(np.median(tif_temps))

            # Locate the matching 10-minute air temperature window in Berlin time
            # (in the run the images actually come from)
            date_iso = date_str.replace("_", "-")
            hour_start = pd.Timestamp(
                f"{date_iso} {capture_hour_str}:00:00", tz="Europe/Berlin"
            )
            win_start = hour_start + pd.Timedelta(minutes=win_start_min)
            win_end   = hour_start + pd.Timedelta(minutes=win_end_min)

            window = air_df[
                (air_df["berlin_timestamp"] >= win_start) &
                (air_df["berlin_timestamp"] <  win_end)
            ]

            def sensor_median(sensor_num: str) -> float | None:
                val = window[window["sensor_number"] == sensor_num]["air_temperature_value"].median()
                return None if pd.isna(val) else round(float(val), 4)

            s1 = sensor_median("1")
            s2 = sensor_median("2")

            reading: dict = {
                "date": date_str,
                "hour": f"{SCHEDULED_HOUR:02d}",
                "capture_hour": capture_hour_str,
                "leaf_temp_c": round(leaf_temp, 4),
                "valid_tif_count": len(valid_tifs),
                "air_temp_sensor1_c": s1,
                "air_temp_sensor2_c": s2,
                "diff_sensor1": round(s1 - leaf_temp, 4) if s1 is not None else None,
                "diff_sensor2": round(s2 - leaf_temp, 4) if s2 is not None else None,
            }
            plant_results.append(reading)

        if plant_results:
            results[plant_id] = plant_results
            total_readings += len(plant_results)
            print(f"  [{plant_id}]  {len(plant_results)} readings")
        else:
            print(f"  [{plant_id}]  no valid readings in date range")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nDone. {total_readings} readings across {len(results)} plants -> {args.output}")


if __name__ == "__main__":
    main()
