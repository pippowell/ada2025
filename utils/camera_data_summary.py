"""
Count the camera images in the ADA 2025 dataset and the share of planned
camera data that is missing (the figures in the paper's dataset table).

Expects the extracted dataset layout (utils/unzip_data.py):
    <data-dir>/<modality>/<bed>_<plant>/<YYYY_MM_DD>/<HH>/<modality>_NNN.<ext>

The paper's figures are for all four modalities and all 216 plants extracted;
the planned total is computed from the plants found, so a partial extraction
reports the share missing for those plants only.

Image totals include every run in the date range, including the off-schedule
re-runs. The missing-data rate is measured against the planned schedule: five
runs per day (06, 09, 12, 15, 18), each with 15 RGB, 15 depth, 5 thermal, and
1 hyperspectral image per plant. Extra runs were mostly re-runs after a failed
scheduled run, so a scheduled slot counts as recovered when a run at most
--max-shift hours away (default 1: 12 -> 11 or 13, not 14) has the data. Per
plant/date/modality/slot, the run with the most images is used, capped at the
planned images per run.

Depth .tif files hold both the depth map and the intensity image (two pages),
so the depth count also covers intensity.

Usage (from the repository root):
    python utils/camera_data_summary.py

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from tqdm import tqdm

SCHEDULED_HOURS = [6, 9, 12, 15, 18]
IMAGES_PER_RUN = {"rgb": 15, "depth": 15, "thermal": 5, "hsi": 1}
IMAGE_RE = re.compile(r"^([a-z]+)_\d{3}\.(tif|jpg|jp2)$")


def scan(data_dir: str, start: str, end: str) -> tuple[dict, set]:
    """Return {(plant, date, hour, modality): image count} for runs within [start, end]."""
    counts: dict[tuple[str, str, int, str], int] = defaultdict(int)
    plants: set[str] = set()
    for modality in IMAGES_PER_RUN:
        mod_root = os.path.join(data_dir, modality)
        if not os.path.isdir(mod_root):
            print(f"[WARNING] {mod_root} not found; {modality} counts will be 0")
            continue
        mod_plants = sorted(p for p in os.listdir(mod_root) if os.path.isdir(os.path.join(mod_root, p)))
        plants.update(mod_plants)
        for plant in tqdm(mod_plants, desc=f"Scanning {modality}"):
            plant_dir = os.path.join(mod_root, plant)
            for date in os.listdir(plant_dir):
                if not (start <= date <= end):
                    continue
                date_dir = os.path.join(plant_dir, date)
                for hour in os.listdir(date_dir):
                    hour_dir = os.path.join(date_dir, hour)
                    if not hour.isdigit() or not os.path.isdir(hour_dir):
                        continue
                    n = sum(1 for f in os.listdir(hour_dir) if IMAGE_RE.match(f))
                    counts[(plant, date, int(hour), modality)] += n
    return counts, plants


def main() -> None:
    parser = argparse.ArgumentParser(description="Camera image totals and missing-data rate for the ADA 2025 dataset.")
    parser.add_argument("--data-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"),
                        help="Dataset folder holding the extracted <modality>/<bed>_<plant>/ folders (default: the repo's data/ folder).")
    parser.add_argument("--start-date", default="2025_08_12", help="First collection day (YYYY_MM_DD, inclusive).")
    parser.add_argument("--end-date", default="2025_09_22", help="Last collection day (YYYY_MM_DD, inclusive).")
    parser.add_argument("--max-shift", type=int, default=1, choices=[0, 1],
                        help="Max hours a re-run may lie from its scheduled slot to count as recovering it "
                             "(0 = scheduled runs only). Scheduled runs are 3 h apart, so larger values "
                             "would let one re-run recover two slots.")
    args = parser.parse_args()

    counts, plants = scan(args.data_dir, args.start_date, args.end_date)

    d0 = datetime.strptime(args.start_date, "%Y_%m_%d")
    d1 = datetime.strptime(args.end_date, "%Y_%m_%d")
    days = [(d0 + timedelta(days=i)).strftime("%Y_%m_%d") for i in range((d1 - d0).days + 1)]
    n_slots = len(plants) * len(days) * len(SCHEDULED_HOURS)

    print(f"\n{len(plants)} plants, {len(days)} days ({args.start_date} to {args.end_date}), "
          f"{len(SCHEDULED_HOURS)} scheduled runs/day -> {n_slots:,} planned runs per modality")
    print(f"Slot recovery: re-runs within +-{args.max_shift} h of a scheduled run\n")

    header = f"{'Modality':<10}{'Images (all runs)':>18}{'Planned':>12}{'Missing (sched. only)':>23}{'Missing (recovered)':>21}{'Empty slots':>13}"
    print(header)
    print("-" * len(header))

    total_got = total_planned = 0
    for modality, per_run in IMAGES_PER_RUN.items():
        images_all = sum(n for (_, _, _, m), n in counts.items() if m == modality)
        planned = n_slots * per_run
        got_sched = got_recovered = empty = 0
        for plant in plants:
            for date in days:
                for slot in SCHEDULED_HOURS:
                    n_sched = counts.get((plant, date, slot, modality), 0)
                    n_best = max(
                        counts.get((plant, date, slot + shift, modality), 0)
                        for shift in range(-args.max_shift, args.max_shift + 1)
                    )
                    got_sched += min(n_sched, per_run)
                    got_recovered += min(n_best, per_run)
                    empty += n_best == 0
        total_got += got_recovered
        total_planned += planned
        print(f"{modality:<10}{images_all:>18,}{planned:>12,}"
              f"{100 * (1 - got_sched / planned):>22.2f}%{100 * (1 - got_recovered / planned):>20.2f}%{empty:>13,}")

    print("-" * len(header))
    print(f"All camera data missing (with slot recovery): {100 * (1 - total_got / total_planned):.2f}%")


if __name__ == "__main__":
    main()
