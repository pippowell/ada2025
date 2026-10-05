"""
plot_depth_background_sensitivity.py

Visualizes depth_background_sensitivity.py's output. Left panel: canopy
height proxy (z_p25; smaller = closer to sensor = taller) at each EXG
vegetation threshold, per bed (solid = published exg_5 method). Right
panel: background surface distance (EXG <= -5, fixed) per bed — a
negative control that should show no treatment-linked signal if the
depth pipeline's vegetation masking is doing its job.

Output: analysis/figures/depth_background_sensitivity.png

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from common import COLORS, FIG_DIR, LABELS, PROCESSED_DIR, bed_date_means, load_rows, w2_minus_w1

VARIANTS = ["exg_neg5", "exg_5", "exg_20", "exg_40"]
STYLES = {
    "exg_neg5": {"ls": ":", "lw": 1.2, "alpha": 0.6},
    "exg_5":    {"ls": "-", "lw": 2.2, "alpha": 1.0},   # published pipeline default
    "exg_20":   {"ls": "--", "lw": 1.4, "alpha": 0.8},
    "exg_40":   {"ls": "-.", "lw": 1.4, "alpha": 0.8},
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
                label=f"{LABELS[bed]} — {variant}" if variant == "exg_5" else None,
                **STYLES[variant],
            )
    ax.invert_yaxis()  # smaller z = taller plant; invert so "up" reads as growth
    ax.set_title("Canopy height (z_p25) across EXG thresholds\n(solid = published exg_5 method)")
    ax.set_ylabel("z (m); inverted — up = taller")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=7)

    ax = axes[1]
    bg_means = bed_date_means(rows, "background")
    for bed in ("W1", "W2"):
        if bed not in bg_means:
            continue
        dates = sorted(bg_means[bed])
        values = [bg_means[bed][d] for d in dates]
        ax.plot(dates, values, "o-", color=COLORS[bed], label=LABELS[bed])
    ax.set_title("Background surface distance (EXG <= -5)\nsoil/pot signal, W1 vs W2")
    ax.set_ylabel("z (m)")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=8)

    fig.suptitle(
        "Background-sensitivity check — depth (pot/soil vs. plant background)",
        fontsize=11, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def print_summary(csv_path: Path) -> None:
    rows = load_rows(csv_path)

    print("\nW2 - W1 divergence by EXG vegetation threshold (mean over Sept 1-14):")
    for variant in VARIANTS:
        dates, diffs = w2_minus_w1(rows, variant)
        if not diffs:
            continue
        avg = sum(diffs) / len(diffs)
        print(f"  {variant:10s}  mean(W2-W1 z) = {avg:+.4f} m  over {len(diffs)} shared dates")

    dates, diffs = w2_minus_w1(rows, "background")
    if diffs:
        avg = sum(diffs) / len(diffs)
        print(f"\n  {'background':10s}  mean(W2-W1 z) = {avg:+.4f} m  over {len(diffs)} shared dates")
        print(
            "  (background surface distance differing by bed would flag pot settling, "
            "soil compaction, or a registration issue, not a plant-height effect)"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(PROCESSED_DIR / "depth_background_sensitivity.csv"))
    parser.add_argument("--output", default=str(FIG_DIR / "depth_background_sensitivity.png"))
    args = parser.parse_args()

    csv_path = Path(args.input)
    make_plot(csv_path, Path(args.output))
    print_summary(csv_path)


if __name__ == "__main__":
    main()
