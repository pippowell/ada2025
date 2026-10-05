"""
plot_thermal.py

Plots leaf-to-air temperature difference over time, grouped by bed.

Air sensor reference per bed (physical placement):
  W1 -> sensor 2 (located in bed 1)
  W2 -> average of sensor 1 and sensor 2 (no in-bed sensor)
  W3 -> sensor 1 (located in bed 3)

Note that the output JSON from thermal_analysis.py contains both sensor 1 and sensor 2 diffs for each plant, so this script selects the appropriate one (or average) per bed based on the above mapping.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


INTERVENTION_DATE = datetime(2025, 9, 3)

BED_SENSOR = {
    "1": "sensor2",   # sensor 2 is in bed 1
    "2": "avg",       # no in-bed sensor
    "3": "sensor1",   # sensor 1 is in bed 3
}

BED_COLORS = {
    "1": "#2196F3",   # blue  — watered control
    "2": "#FF9800",   # orange — drying
    "3": "#F44336",   # red    — drying, farthest from water
}

BED_LABELS = {
    "1": "Bed 1 / W1 (control, watered) — sensor 2",
    "2": "Bed 2 / W2 (drying) — avg sensors 1 & 2",
    "3": "Bed 3 / W3 (drying, far from water) — sensor 1",
}


def local_diff(reading: dict, bed: str) -> float | None:
    s1 = reading["diff_sensor1"]
    s2 = reading["diff_sensor2"]
    if BED_SENSOR[bed] == "sensor1":
        return s1
    if BED_SENSOR[bed] == "sensor2":
        return s2
    # avg
    if s1 is not None and s2 is not None:
        return (s1 + s2) / 2
    return s1 if s1 is not None else s2


def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y_%m_%d")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(Path(__file__).resolve().parent / "thermal_analysis.json"))
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "thermal_plot.png"))
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    # Collect per-plant daily means and per-bed-date all plant readings
    # Structure: bed -> date -> list of diff values (one per plant-hour reading)
    bed_date_vals: dict[str, dict[datetime, list[float]]] = {
        "1": defaultdict(list),
        "2": defaultdict(list),
        "3": defaultdict(list),
    }
    # Also per-plant daily mean for individual scatter
    # plant -> date -> mean diff across hours
    plant_daily: dict[str, dict[datetime, float]] = {}

    for plant_id, readings in data.items():
        bed = plant_id[1]
        by_date: dict[datetime, list[float]] = defaultdict(list)
        for r in readings:
            if int(r.get("hour", -1)) != 15:
                continue
            diff = local_diff(r, bed)
            if diff is None:
                continue
            dt = parse_date(r["date"])
            by_date[dt].append(diff)
            bed_date_vals[bed][dt].append(diff)

        plant_daily[plant_id] = {
            dt: float(np.mean(vals)) for dt, vals in by_date.items()
        }

    fig, ax = plt.subplots(figsize=(12, 6))

    for bed in ["1", "2", "3"]:
        color = BED_COLORS[bed]
        label = BED_LABELS[bed]
        date_map = bed_date_vals[bed]

        dates_sorted = sorted(date_map.keys())
        means = [float(np.mean(date_map[d])) for d in dates_sorted]
        stds  = [float(np.std(date_map[d]))  for d in dates_sorted]

        # Individual plant daily means as faint dots
        for plant_id, daily in plant_daily.items():
            if plant_id[1] != bed:
                continue
            xs = sorted(daily.keys())
            ys = [daily[x] for x in xs]
            ax.scatter(xs, ys, color=color, alpha=0.2, s=18, zorder=2)

        # Shaded std band
        arr_means = np.array(means)
        arr_stds  = np.array(stds)
        ax.fill_between(
            dates_sorted,
            arr_means - arr_stds,
            arr_means + arr_stds,
            color=color, alpha=0.12,
        )

        # Mean line
        ax.plot(dates_sorted, means, color=color, linewidth=2.2,
                marker="o", markersize=5, label=label, zorder=3)

    # Zero reference line
    ax.axhline(0, color="black", linewidth=0.9, linestyle="--", alpha=0.6,
               label="Air temp = Leaf temp")

    # Intervention line
    ax.axvline(INTERVENTION_DATE, color="purple", linewidth=1.4,
               linestyle=":", alpha=0.8, label="Intervention (Sept 3)")

    ax.set_xlabel("Date", fontsize=12)
    ax.set_ylabel("Air temp − Leaf temp (°C)", fontsize=12)
    ax.set_title(
        "Leaf-to-Air Temperature Difference by Bed\n"
        "Positive = leaf cooler than air (transpiring); "
        "Negative = leaf warmer than air (stressed)",
        fontsize=13,
    )

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    fig.autofmt_xdate(rotation=30)

    ax.legend(fontsize=10, loc="upper right")
    ax.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(args.output, dpi=150)
    print(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()
