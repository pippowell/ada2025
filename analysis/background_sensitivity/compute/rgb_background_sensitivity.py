"""
rgb_background_sensitivity.py

RGB counterpart to the thermal/depth background-sensitivity checks
(pot-overgrowth / background). Uses the same EXG
(excess-green) vegetation definition as the plant-height pipeline
(analysis/plant_height/config.py, threshold=5), computed natively at full
RGB resolution on frame 007 of each plant/date/15h scan (no cross-sensor
registration needed here, unlike depth/thermal/HSI).

Two things this script quantifies directly, at several EXG thresholds:
  (a) Vegetation fraction of frame per bed/date — a direct, visual
      measure of how much of each image is canopy vs. pot/soil
      background over time. This is the most literal measure of the "plants overgrow their pots"
      effect: it shows exactly how that overgrowth timeline looks in the
      actual imagery, per bed.
  (b) Background brightness (mean RGB intensity of non-vegetation
      pixels, EXG <= -5 fixed) per bed/date — tests whether the exposed
      soil/pot itself looks different by bed (e.g. drier soil in the
      water-deficit bed reading visually lighter/brighter), independent
      of any plant signal.

Output: a long-format CSV, one row per (bed, plant, date).

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image

VEG_EXG_THRESHOLDS = [-5.0, 5.0, 20.0, 40.0]  # 5.0 matches the published pipeline
BACKGROUND_EXG_THRESHOLD = -5.0  # fixed: clearly non-vegetation regardless of sweep

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_DATA_DIR = _REPO_ROOT / "data"
_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "processed" / "rgb_background_sensitivity.csv"


def _variant_name(threshold: float) -> str:
    sign = "neg" if threshold < 0 else ""
    return f"exg_{sign}{abs(int(threshold))}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Track vegetation-fraction and background-brightness trajectories "
        "from native RGB frames, given pot overgrowth."
    )
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR), help="dataset folder (default: the repo's data/ folder)")
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    parser.add_argument(
        "--plant-ids", default="W1_A2,W1_A8,W1_J5,W1_R1,W1_R7,W2_A2,W2_A8,W2_J5,W2_R1,W2_R7",
        help="Comma-separated <bed>_<plant> ids (default: the paper's five plants "
             "in W1 and W2, A2,A8,J5,R1,R7 each)",
    )
    parser.add_argument("--frame-index", type=int, default=7)
    parser.add_argument("--hour", default="15")
    parser.add_argument("--start-date", default="2025_09_01")
    parser.add_argument("--end-date", default="2025_09_14")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) / "rgb"
    variant_keys = [f"veg_frac_{_variant_name(t)}" for t in VEG_EXG_THRESHOLDS]
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

            img_path = date_dir / args.hour / f"rgb_{args.frame_index:03d}.jpg"
            if not img_path.exists():
                continue

            rgb = np.asarray(Image.open(img_path), dtype=np.float32)
            r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
            exg = (2.0 * g - r - b).ravel()
            brightness = ((r + g + b) / 3.0).ravel()

            row = {"bed": bed, "plant": plant_id, "date": date_str}
            for threshold, key in zip(VEG_EXG_THRESHOLDS, variant_keys):
                row[key] = round(float(np.mean(exg > threshold)), 5)

            bg = brightness[exg <= BACKGROUND_EXG_THRESHOLD]
            row["background_brightness"] = round(float(np.mean(bg)), 3) if bg.size else ""

            rows.append(row)

    rows.sort(key=lambda r: (str(r["bed"]), str(r["plant"]), str(r["date"])))
    fieldnames = ["bed", "plant", "date"] + variant_keys + ["background_brightness"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Processed {len(rows)} images -> {args.output}")


if __name__ == "__main__":
    main()
