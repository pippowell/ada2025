"""
Plot W1/W2 plant height trajectories and export plotted values CSV.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

try:
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot W1/W2 height trajectories from metrics CSV")
    parser.add_argument("--metrics-csv", default=str(config.METRICS_CSV), help="Input metrics CSV")
    parser.add_argument("--output-png", default=str(config.PLOT_FIGURE_PNG), help="Output plot image")
    parser.add_argument(
        "--output-values-csv",
        default=str(config.PLOT_VALUES_CSV),
        help="Output CSV of plotted values",
    )
    parser.add_argument(
        "--sensor-height",
        type=float,
        default=config.DEFAULT_SENSOR_HEIGHT_M,
        help="Sensor height in meters",
    )
    parser.add_argument(
        "--vegetation-threshold",
        type=float,
        default=config.DEFAULT_VEGETATION_PT_THRESHOLD,
        help="Minimum num_points_vegetation to include row",
    )
    parser.add_argument("--show", action="store_true", help="Show plot interactively")
    return parser.parse_args()


def _parse_date(date_text: str) -> datetime:
    return datetime.strptime(date_text, "%Y_%m_%d")


def _parse_float(value: str) -> float:
    parsed = float(value)
    if math.isnan(parsed):
        raise ValueError("value is NaN")
    return parsed


def _compute_daily_stats(
    values_by_date: Dict[datetime, List[float]],
) -> Tuple[List[datetime], List[float], List[float], List[float], List[int]]:
    sorted_dates = sorted(values_by_date.keys())
    means: List[float] = []
    band_lowers: List[float] = []
    band_uppers: List[float] = []
    counts: List[int] = []

    for date in sorted_dates:
        values = np.asarray(values_by_date[date], dtype=np.float64)
        n_values = int(values.size)
        mean_value = float(np.mean(values))
        # SD band represents spread across plants on a given bed/date (not CI).
        std_value = float(np.std(values, ddof=1)) if n_values >= 2 else 0.0
        means.append(mean_value)
        band_lowers.append(mean_value - std_value)
        band_uppers.append(mean_value + std_value)
        counts.append(n_values)

    return sorted_dates, means, band_lowers, band_uppers, counts


def _build_output_rows(
    dates: List[datetime],
    means: List[float],
    band_lowers: List[float],
    band_uppers: List[float],
    bed: str,
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for date, mean, lower, upper in zip(dates, means, band_lowers, band_uppers):
        std_dev = (float(upper) - float(lower)) / 2.0
        rows.append(
            {
                "date": date.strftime("%Y_%m_%d"),
                "bed": bed,
                "height_mean": f"{float(mean):.10f}",
                "std_dev": f"{std_dev:.10f}",
            }
        )
    return rows


def _format_avg_count(counts: List[int]) -> str:
    if not counts:
        return "n/a"
    return f"{float(np.mean(counts)):.2f}"


def main() -> int:
    args = _parse_args()
    metrics_csv = Path(args.metrics_csv)
    output_png = Path(args.output_png)
    output_values_csv = Path(args.output_values_csv)
    sensor_height = float(args.sensor_height)
    vegetation_threshold = float(args.vegetation_threshold)

    if not metrics_csv.exists() or not metrics_csv.is_file():
        raise ValueError(f"Metrics CSV does not exist: {metrics_csv}")

    values_by_bed_and_date: Dict[str, Dict[datetime, List[float]]] = {"W1": {}, "W2": {}}
    rows_read = 0
    rows_used = 0
    rows_skipped = 0
    rows_skipped_low_veg = 0

    with metrics_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("Metrics CSV appears empty")
        required = {"plant_id", "date", "z_p25", "num_points_vegetation"}
        missing = sorted(required - set(reader.fieldnames))
        if missing:
            raise ValueError(f"Metrics CSV missing required columns: {missing}")

        for row in reader:
            rows_read += 1
            plant_id = str(row.get("plant_id", "")).strip()
            if plant_id.startswith("W1_"):
                bed = "W1"
            elif plant_id.startswith("W2_"):
                bed = "W2"
            else:
                rows_skipped += 1
                continue

            try:
                date = _parse_date(str(row.get("date", "")).strip())
                z_p25 = _parse_float(str(row.get("z_p25", "")).strip())
                n_veg = _parse_float(str(row.get("num_points_vegetation", "")).strip())
            except (ValueError, TypeError):
                rows_skipped += 1
                continue

            if n_veg < vegetation_threshold:
                # Filter unstable rows where vegetation segmentation is too sparse.
                rows_skipped += 1
                rows_skipped_low_veg += 1
                continue

            # Depth is measured downward, so invert to upward plant-height estimate.
            adjusted_height = float(sensor_height - z_p25)
            values_by_bed_and_date[bed].setdefault(date, []).append(adjusted_height)
            rows_used += 1

    w1_dates, w1_means, w1_lowers, w1_uppers, w1_counts = _compute_daily_stats(values_by_bed_and_date["W1"])
    w2_dates, w2_means, w2_lowers, w2_uppers, w2_counts = _compute_daily_stats(values_by_bed_and_date["W2"])
    if not w1_dates and not w2_dates:
        raise RuntimeError("No valid W1/W2 rows found for plotting")

    fig, ax = plt.subplots(figsize=(12, 6))

    if w1_dates:
        ax.plot(w1_dates, w1_means, marker="o", linewidth=2.0, color="#2E8B57", label="W1 (Control)")
        ax.fill_between(w1_dates, w1_lowers, w1_uppers, color="#2E8B57", alpha=0.20, label="_nolegend_")
    if w2_dates:
        ax.plot(w2_dates, w2_means, marker="o", linewidth=2.0, color="#1F77B4", label="W2 (Drought)")
        ax.fill_between(w2_dates, w2_lowers, w2_uppers, color="#1F77B4", alpha=0.20, label="_nolegend_")

    ax.set_title("Plant Height Trajectories Under Control and Drought Conditions")
    ax.set_xlabel("Date")
    ax.set_ylabel("Estimated plant height (m)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate(rotation=45, ha="right")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_png, dpi=300)

    output_rows = _build_output_rows(w1_dates, w1_means, w1_lowers, w1_uppers, bed="W1")
    output_rows.extend(_build_output_rows(w2_dates, w2_means, w2_lowers, w2_uppers, bed="W2"))
    output_rows.sort(key=lambda row: (row["date"], row["bed"]))

    output_values_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_values_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "bed", "height_mean", "std_dev"])
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Metrics CSV: {metrics_csv}")
    print(f"Sensor height: {sensor_height} m")
    print(f"Vegetation point threshold: {vegetation_threshold}")
    print(f"Rows read: {rows_read}")
    print(f"Rows used: {rows_used}")
    print(f"Rows skipped: {rows_skipped}")
    print(f"Rows skipped (low vegetation points): {rows_skipped_low_veg}")
    print(f"W1 dates plotted: {len(w1_dates)} (plants/day avg: {_format_avg_count(w1_counts)})")
    print(f"W2 dates plotted: {len(w2_dates)} (plants/day avg: {_format_avg_count(w2_counts)})")
    print(f"Output plot: {output_png}")
    print(f"Output values CSV: {output_values_csv} ({len(output_rows)} rows)")

    if args.show:
        plt.show()
    else:
        plt.close(fig)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
