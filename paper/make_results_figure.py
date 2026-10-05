"""
Stacked four-panel results figure for the ADA 2025 paper.

Panels (a)-(d) share the Sep 1–14 x-axis:
  (a) Plant height
  (b) NDVI
  (c) SIPI
  (d) Thermal: leaf-to-air temperature difference

Output: paper/results_multimodal.{pdf,png}

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.transforms import blended_transform_factory

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ANALYSIS = ROOT / "analysis"
PAPER = ROOT / "paper"
OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

INTERVENTION = pd.Timestamp("2025-09-03")
ANALYSIS_START = pd.Timestamp("2025-09-01")
ANALYSIS_END = pd.Timestamp("2025-09-14")

COLORS = {"W1": "#2166ac", "W2": "#d6604d"}
LABELS = {"W1": "Bed 1 / W1 (control)", "W2": "Bed 2 / W2 (water deficit)"}

STYLE = {
    "figure.dpi": 150,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 9,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
}


# ---------------------------------------------------------------------------
# Data loaders — return {bed: DataFrame(date, mean[, std])}
# ---------------------------------------------------------------------------

def load_thermal() -> dict[str, pd.DataFrame]:
    with open(ANALYSIS / "leaf_air_temp" / "thermal_analysis.json") as f:
        raw = json.load(f)

    rows = []
    for key, records in raw.items():
        bed = key[:2]
        if bed not in ("W1", "W2"):
            continue
        for r in records:
            if int(r.get("hour", -1)) != 15:
                continue
            leaf = r["leaf_temp_c"]
            air = (r["air_temp_sensor2_c"] if bed == "W1"
                   else (r["air_temp_sensor1_c"] + r["air_temp_sensor2_c"]) / 2)
            rows.append({
                "bed": bed,
                "date": pd.to_datetime(r["date"], format="%Y_%m_%d"),
                "val": leaf - air,
            })

    df = pd.DataFrame(rows)
    df = df[(df["date"] >= ANALYSIS_START) & (df["date"] <= ANALYSIS_END)]

    out: dict[str, pd.DataFrame] = {}
    for bed in ("W1", "W2"):
        out[bed] = (
            df[df["bed"] == bed]
            .groupby("date")["val"]
            .agg(mean="mean", std="std")
            .reset_index()
            .sort_values("date")
        )
    return out


def load_plant_height() -> dict[str, pd.DataFrame]:
    df = pd.read_csv(ANALYSIS / "plant_height" / "processed" / "depth_results_w1_w2_values.csv")
    df["date"] = pd.to_datetime(df["date"], format="%Y_%m_%d")

    out: dict[str, pd.DataFrame] = {}
    for bed in ("W1", "W2"):
        out[bed] = (
            df[df["bed"] == bed][["date", "height_mean", "std_dev"]]
            .rename(columns={"height_mean": "mean", "std_dev": "std"})
            .sort_values("date")
        )
    return out


def load_hyperspectral(index: str) -> dict[str, pd.DataFrame]:
    df = pd.read_excel(DATA / "results_w5_mean_15_sa.xlsx")
    df = df[df["index"] == index].copy()
    df["date"] = pd.to_datetime(df["group"], format="%Y_%m_%d")
    df = df[df["bed_id"].isin(["W1", "W2"])]
    df = df[(df["date"] >= ANALYSIS_START) & (df["date"] <= ANALYSIS_END)]

    out: dict[str, pd.DataFrame] = {}
    for bed in ("W1", "W2"):
        sub = df[df["bed_id"] == bed]
        cols = ["date", "mean"] + (["std"] if "std" in sub.columns else [])
        out[bed] = sub[cols].sort_values("date").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

ALL_DATES = pd.date_range(ANALYSIS_START, ANALYSIS_END, freq="D")


def _missing_dates(sub: pd.DataFrame) -> pd.DatetimeIndex:
    return ALL_DATES.difference(pd.DatetimeIndex(sub["date"]))


def _plot_bed(ax, data: dict[str, pd.DataFrame]) -> None:
    for bed in ("W1", "W2"):
        # Reindex to the full daily range so a day with no reading becomes NaN.
        # matplotlib then breaks the line and the +-1 SD band there instead of
        # drawing a segment across the gap (no interpolation).
        sub = data[bed].set_index("date").reindex(ALL_DATES)
        c = COLORS[bed]
        ax.plot(sub.index, sub["mean"], "o-", color=c,
                ms=3.5, lw=1.6, label=LABELS[bed], zorder=3)
        if "std" in sub.columns:
            ax.fill_between(
                sub.index,
                sub["mean"] - sub["std"],
                sub["mean"] + sub["std"],
                color=c, alpha=0.15, zorder=1,
            )


def _add_vline(ax) -> None:
    ax.axvline(INTERVENTION, color="#555555", lw=1.0, ls="--", alpha=0.75, zorder=2)


def _label_panel(ax, letter: str) -> None:
    ax.text(0.015, 0.97, letter, transform=ax.transAxes,
            fontsize=10, fontweight="bold", va="top")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_figure() -> None:
    thermal = load_thermal()
    height = load_plant_height()
    ndvi = load_hyperspectral("ndvi")
    sipi = load_hyperspectral("sipi")

    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(4, 1, figsize=(6.5, 7.5), sharex=True)

        # (a) Plant height
        _plot_bed(axes[0], height)
        _add_vline(axes[0])
        axes[0].set_ylabel("Plant height (m)")
        _label_panel(axes[0], "(a)")

        # (b) NDVI
        _plot_bed(axes[1], ndvi)
        _add_vline(axes[1])
        axes[1].set_ylabel("NDVI")
        _label_panel(axes[1], "(b)")

        # (c) SIPI
        _plot_bed(axes[2], sipi)
        _add_vline(axes[2])
        axes[2].set_ylabel("SIPI")
        _label_panel(axes[2], "(c)")

        # (d) Thermal
        _plot_bed(axes[3], thermal)
        _add_vline(axes[3])
        axes[3].axhline(0, color="black", lw=0.6, ls=":", alpha=0.4)
        axes[3].set_ylabel("Leaf−air diff. (°C)")
        axes[3].set_xlabel("Date")
        _label_panel(axes[3], "(d)")

        # Flag days with no reading (none in Sep 1-14 since thermal_analysis.py
        # recovers W2's Sep 11 reading from the 16:00 re-run) so a gap reads as
        # missing data rather than a plotting artefact
        for bed in ("W1", "W2"):
            for day in _missing_dates(thermal[bed]):
                print(f"Thermal {bed}: no 3pm reading on {day.date()} (line left broken)")
                neighbours = thermal[bed].set_index("date")["mean"].reindex(
                    [day - pd.Timedelta(days=1), day + pd.Timedelta(days=1)]
                )
                target = neighbours.mean() if neighbours.notna().all() else 0.0
                axes[3].annotate(
                    f"no {bed} reading", xy=(day, target),
                    xytext=(day, target + 5.5), ha="center", va="bottom",
                    fontsize=7, color=COLORS[bed],
                    arrowprops=dict(arrowstyle="-|>", color=COLORS[bed], lw=0.8,
                                    shrinkA=2, shrinkB=4),
                    zorder=4,
                )

        # Annotation on the intervention line, bottom of panel (d)
        trans = blended_transform_factory(axes[3].transData, axes[3].transAxes)
        axes[3].text(
            INTERVENTION + pd.Timedelta(hours=7), 0.75,
            "irrigation\ncutoff",
            transform=trans, fontsize=7, color="#555555", va="bottom",
        )

        # Shared x-axis formatting (only bottom panel shows labels via sharex)
        axes[-1].set_xlim(
            ANALYSIS_START - pd.Timedelta(hours=12),
            ANALYSIS_END + pd.Timedelta(hours=12),
        )
        axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=2))
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        plt.setp(axes[-1].get_xticklabels(), rotation=30, ha="right")

        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False,
                   bbox_to_anchor=(0.5, 0), fontsize=8)

        fig.tight_layout(h_pad=0.5, rect=[0, 0.05, 1, 1])

        out_pdf = OUT_DIR / "results_multimodal.pdf"
        out_png = OUT_DIR / "results_multimodal.png"
        fig.savefig(str(out_pdf), bbox_inches="tight")
        fig.savefig(str(out_png), dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved {out_pdf}")
        print(f"Saved {out_png}")


if __name__ == "__main__":
    make_figure()
