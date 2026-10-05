"""
plot_rgb_background_sensitivity.py

Visualizes rgb_background_sensitivity.py's output. Left panel: vegetation
(canopy) fraction of frame over time per bed, at each EXG threshold —
the most literal picture of the "plants overgrow their pots"
effect. Right panel: background (soil/pot) brightness per bed, to
check whether the exposed background itself looks different by
treatment.

Output: analysis/figures/rgb_background_sensitivity.png

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from common import COLORS, FIG_DIR, LABELS, PROCESSED_DIR, bed_date_means, load_rows, w2_minus_w1

VARIANTS = ["veg_frac_exg_neg5", "veg_frac_exg_5", "veg_frac_exg_20", "veg_frac_exg_40"]
STYLES = {
    "veg_frac_exg_neg5": {"ls": ":", "lw": 1.2, "alpha": 0.6},
    "veg_frac_exg_5":    {"ls": "-", "lw": 2.2, "alpha": 1.0},   # published pipeline default
    "veg_frac_exg_20":   {"ls": "--", "lw": 1.4, "alpha": 0.8},
    "veg_frac_exg_40":   {"ls": "-.", "lw": 1.4, "alpha": 0.8},
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
                label=f"{LABELS[bed]} — {variant.replace('veg_frac_', '')}" if variant == "veg_frac_exg_5" else None,
                **STYLES[variant],
            )
    ax.set_title("Vegetation fraction of frame across EXG thresholds\n(solid = published exg_5 method)")
    ax.set_ylabel("Fraction of frame classed as canopy")
    ax.set_ylim(0, 1)
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=7)

    ax = axes[1]
    bg_means = bed_date_means(rows, "background_brightness")
    for bed in ("W1", "W2"):
        if bed not in bg_means:
            continue
        dates = sorted(bg_means[bed])
        values = [bg_means[bed][d] for d in dates]
        ax.plot(dates, values, "o-", color=COLORS[bed], label=LABELS[bed])
    ax.set_title("Background brightness (EXG <= -5)\nsoil/pot signal, W1 vs W2")
    ax.set_ylabel("Mean RGB intensity (0-255)")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=8)

    fig.suptitle(
        "Background-sensitivity check — RGB (pot/soil vs. plant background)",
        fontsize=11, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def print_summary(csv_path: Path) -> None:
    rows = load_rows(csv_path)

    print("\nVegetation fraction by bed, first vs. last date in range (exg_5 = published threshold):")
    means = bed_date_means(rows, "veg_frac_exg_5")
    for bed in ("W1", "W2"):
        if bed not in means:
            continue
        dates = sorted(means[bed])
        print(f"  {bed}: {means[bed][dates[0]]:.3f} ({dates[0]}) -> {means[bed][dates[-1]]:.3f} ({dates[-1]})")

    print("\nW2 - W1 divergence by EXG threshold (mean vegetation fraction over range):")
    for variant in VARIANTS:
        dates, diffs = w2_minus_w1(rows, variant)
        if not diffs:
            continue
        avg = sum(diffs) / len(diffs)
        print(f"  {variant:20s}  mean(W2-W1) = {avg:+.4f}  over {len(diffs)} shared dates")

    dates, diffs = w2_minus_w1(rows, "background_brightness")
    if diffs:
        avg = sum(diffs) / len(diffs)
        print(f"\n  {'background':20s}  mean(W2-W1 brightness) = {avg:+.2f}  over {len(diffs)} shared dates")
        print(
            "  (a background brightness difference by bed would suggest visibly "
            "different soil/pot appearance between beds, e.g. from differential soil moisture)"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(PROCESSED_DIR / "rgb_background_sensitivity.csv"))
    parser.add_argument("--output", default=str(FIG_DIR / "rgb_background_sensitivity.png"))
    args = parser.parse_args()

    csv_path = Path(args.input)
    make_plot(csv_path, Path(args.output))
    print_summary(csv_path)


if __name__ == "__main__":
    main()
