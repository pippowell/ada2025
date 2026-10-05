"""
plot_thermal_background_sensitivity.py

Visualizes the output of compute/thermal_background_sensitivity.py to
answer two questions raised by pot overgrowth:

  (a) Does the W1-vs-W2 leaf-temperature divergence reported in the paper
      (Fig. 4d, using the coolest 50% of pixels) survive if the pixel
      selection is made stricter (canopy-only) or looser (more background
      included)? -> left panel: cool_10 / cool_25 / cool_50 / cool_75
      trajectories per bed.

  (b) Does the background proxy (warmest 25% of pixels, i.e. sunlit
      soil/pot plastic) itself carry a W1-vs-W2 signal? If it does, that
      background-only channel is moving with treatment despite containing
      no leaf material, which would indicate a genuine confound rather
      than a measurement artifact of the leaf-temperature method.
      -> right panel: background trajectory per bed.

Output: analysis/figures/thermal_background_sensitivity.png

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from common import COLORS, FIG_DIR, LABELS, PROCESSED_DIR, bed_date_means, load_rows, w2_minus_w1

COOL_VARIANTS = ["cool_10", "cool_25", "cool_50", "cool_75"]
COOL_STYLES = {
    "cool_10": {"ls": ":", "lw": 1.2, "alpha": 0.6},
    "cool_25": {"ls": "--", "lw": 1.4, "alpha": 0.8},
    "cool_50": {"ls": "-", "lw": 2.2, "alpha": 1.0},   # matches published pipeline
    "cool_75": {"ls": "-.", "lw": 1.4, "alpha": 0.8},
}


def make_plot(csv_path: Path, out_path: Path) -> None:
    rows = load_rows(csv_path)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))

    # Left panel: leaf-temperature trajectory at each cool-fraction threshold
    ax = axes[0]
    for variant in COOL_VARIANTS:
        means = bed_date_means(rows, variant)
        for bed in ("W1", "W2"):
            dates = sorted(means[bed])
            values = [means[bed][d] for d in dates]
            style = COOL_STYLES[variant]
            ax.plot(
                dates, values,
                color=COLORS[bed],
                label=f"{LABELS[bed]} — {variant}" if variant == "cool_50" else None,
                **style,
            )
    ax.set_title("Leaf-temp proxy across cool-fraction thresholds\n(solid = published cool_50 method)")
    ax.set_ylabel("Temperature (°C)")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=7)

    # Right panel: background (warmest-25%) proxy trajectory
    ax = axes[1]
    bg_means = bed_date_means(rows, "background")
    for bed in ("W1", "W2"):
        dates = sorted(bg_means[bed])
        values = [bg_means[bed][d] for d in dates]
        ax.plot(dates, values, "o-", color=COLORS[bed], label=LABELS[bed])
    ax.set_title("Background proxy (warmest 25% of pixels)\nsoil/pot signal, W1 vs W2")
    ax.set_ylabel("Temperature (°C)")
    ax.tick_params(axis="x", rotation=45)
    ax.legend(fontsize=8)

    fig.suptitle(
        "Background-sensitivity check — thermal (pot/soil vs. plant background)",
        fontsize=11, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def print_summary(csv_path: Path) -> None:
    rows = load_rows(csv_path)

    print("\nW2 - W1 divergence by cool-fraction threshold (mean over Sept 1-14):")
    for variant in COOL_VARIANTS:
        dates, diffs = w2_minus_w1(rows, variant)
        if not diffs:
            continue
        avg_diff = sum(diffs) / len(diffs)
        print(f"  {variant:10s}  mean(W2-W1) = {avg_diff:+.3f} C  over {len(diffs)} shared dates")

    dates, diffs = w2_minus_w1(rows, "background")
    if diffs:
        avg_diff = sum(diffs) / len(diffs)
        print(f"\n  {'background':10s}  mean(W2-W1) = {avg_diff:+.3f} C  over {len(diffs)} shared dates")
        print(
            "  (a background difference in the same direction as the leaf-temp "
            "divergence above would suggest a soil/pot confound, not just a "
            "plant-water-status effect)"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(PROCESSED_DIR / "thermal_background_sensitivity.csv"))
    parser.add_argument("--output", default=str(FIG_DIR / "thermal_background_sensitivity.png"))
    args = parser.parse_args()

    csv_path = Path(args.input)
    make_plot(csv_path, Path(args.output))
    print_summary(csv_path)


if __name__ == "__main__":
    main()
