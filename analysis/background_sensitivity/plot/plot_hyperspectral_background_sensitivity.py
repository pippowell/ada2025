"""
plot_hyperspectral_background_sensitivity.py

Visualizes hyperspectral_background_sensitivity.py's output. Left panel:
NDVI at each in-cube EXG vegetation-fraction threshold, per bed. Right
panel: NDVI of the background proxy (bottom 25% EXG = soil/pot pixels)
per bed — tests for the classic "soil line" confound (bare-soil
reflectance differing by treatment) independent of any plant signal.

Output: analysis/figures/hyperspectral_background_sensitivity.png

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from common import COLORS, FIG_DIR, LABELS, PROCESSED_DIR, bed_date_means, load_rows, w2_minus_w1

VARIANTS = ["ndvi_veg_10", "ndvi_veg_25", "ndvi_veg_50", "ndvi_veg_75"]
STYLES = {
    "ndvi_veg_10": {"ls": ":", "lw": 1.2, "alpha": 0.6},
    "ndvi_veg_25": {"ls": "--", "lw": 1.4, "alpha": 0.8},
    "ndvi_veg_50": {"ls": "-", "lw": 2.2, "alpha": 1.0},   # matches paper's NDVI aggregation spirit
    "ndvi_veg_75": {"ls": "-.", "lw": 1.4, "alpha": 0.8},
}


def make_plot(csv_path: Path, out_path: Path) -> None:
    rows = load_rows(csv_path)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    ax = axes[0]
    for variant in VARIANTS:
        means = bed_date_means(rows, variant)
        for bed in ("W1", "W2"):
            if bed not in means:
                continue
            dates = sorted(means[bed])
            values = [means[bed][d] for d in dates]
            ax.plot(
                dates, values, color=COLORS[bed],
                label=f"{LABELS[bed]} — {variant}" if variant == "ndvi_veg_50" else None,
                **STYLES[variant],
            )
    ax.set_title("NDVI across in-cube EXG vegetation-fraction thresholds")
    ax.set_ylabel("NDVI")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=7)

    ax = axes[1]
    bg_means = bed_date_means(rows, "ndvi_background")
    for bed in ("W1", "W2"):
        if bed not in bg_means:
            continue
        dates = sorted(bg_means[bed])
        values = [bg_means[bed][d] for d in dates]
        ax.plot(dates, values, "o-", color=COLORS[bed], label=LABELS[bed])
    ax.set_title("Background NDVI (bottom 25% EXG)\nsoil/pot signal, W1 vs W2")
    ax.set_ylabel("NDVI")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=8)

    fig.suptitle(
        "Background-sensitivity check — hyperspectral (pot/soil vs. plant background)",
        fontsize=11, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def print_summary(csv_path: Path) -> None:
    rows = load_rows(csv_path)

    print("\nW2 - W1 divergence by vegetation-fraction threshold (mean NDVI over range):")
    for variant in VARIANTS:
        dates, diffs = w2_minus_w1(rows, variant)
        if not diffs:
            continue
        avg = sum(diffs) / len(diffs)
        print(f"  {variant:14s}  mean(W2-W1 NDVI) = {avg:+.4f}  over {len(diffs)} shared dates")

    dates, diffs = w2_minus_w1(rows, "ndvi_background")
    if diffs:
        avg = sum(diffs) / len(diffs)
        print(f"\n  {'background':14s}  mean(W2-W1 NDVI) = {avg:+.4f}  over {len(diffs)} shared dates")
        print(
            "  (a background NDVI difference by bed would indicate a soil-line "
            "confound — bare soil reflectance differing by treatment, e.g. via soil moisture)"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(PROCESSED_DIR / "hyperspectral_background_sensitivity.csv"))
    parser.add_argument("--output", default=str(FIG_DIR / "hyperspectral_background_sensitivity.png"))
    args = parser.parse_args()

    csv_path = Path(args.input)
    make_plot(csv_path, Path(args.output))
    print_summary(csv_path)


if __name__ == "__main__":
    main()
