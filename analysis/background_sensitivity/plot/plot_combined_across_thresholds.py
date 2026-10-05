"""
plot_combined_across_thresholds.py

Combines the "across thresholds" panel from all four per-modality
background-sensitivity plots (thermal, depth, RGB, hyperspectral) into a
single 2x2 figure. This is the panel that most directly answers, per modality,
the core question: does the published W1-vs-W2 divergence survive
stricter/looser vegetation-masking thresholds? Putting all four side by
side makes it easy to see at a glance that direction holds everywhere,
which the four separate two-panel figures make a reader hunt for.

Requires processed/*.csv from all four compute/*_background_sensitivity.py
scripts to already exist.

Output: analysis/figures/background_sensitivity_across_thresholds.png

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from common import COLORS, FIG_DIR, LABELS, PROCESSED_DIR, bed_date_means, load_rows

# (csv filename, column list, published/solid variant, panel title, y-label, invert-y)
PANELS = [
    (
        "thermal_background_sensitivity.csv",
        ["cool_10", "cool_25", "cool_50", "cool_75"],
        "cool_50",
        "Thermal: leaf-temp proxy\nacross cool-fraction thresholds",
        "Temperature (°C)",
        False,
    ),
    (
        "depth_background_sensitivity.csv",
        ["exg_neg5", "exg_5", "exg_20", "exg_40"],
        "exg_5",
        "Depth: canopy height (z_p25)\nacross EXG thresholds",
        "z (m); inverted — up = taller",
        True,
    ),
    (
        "rgb_background_sensitivity.csv",
        ["veg_frac_exg_neg5", "veg_frac_exg_5", "veg_frac_exg_20", "veg_frac_exg_40"],
        "veg_frac_exg_5",
        "RGB: vegetation fraction of frame\nacross EXG thresholds",
        "Fraction of frame classed as canopy",
        False,
    ),
    (
        "hyperspectral_background_sensitivity.csv",
        ["ndvi_veg_10", "ndvi_veg_25", "ndvi_veg_50", "ndvi_veg_75"],
        "ndvi_veg_50",
        "Hyperspectral: NDVI\nacross in-cube EXG vegetation-fraction thresholds",
        "NDVI",
        False,
    ),
]

# threshold sweep position -> line style, shared across all four panels so
# "strictest" and "loosest" read the same way in every subplot
STYLE_BY_RANK = {
    0: {"ls": ":", "lw": 1.2, "alpha": 0.6},    # loosest / most background included
    1: {"ls": "--", "lw": 1.4, "alpha": 0.8},
    2: {"ls": "-", "lw": 2.2, "alpha": 1.0},    # typically the published choice
    3: {"ls": "-.", "lw": 1.4, "alpha": 0.8},   # strictest / vegetation-only
}


def make_plot(processed_dir: Path, out_path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))

    for ax, (csv_name, variants, published, title, ylabel, invert) in zip(axes.flat, PANELS):
        csv_path = processed_dir / csv_name
        if not csv_path.exists():
            ax.set_title(f"{title}\n(missing {csv_name})")
            continue
        rows = load_rows(csv_path)
        for rank, variant in enumerate(variants):
            means = bed_date_means(rows, variant)
            for bed in ("W1", "W2"):
                if bed not in means:
                    continue
                dates = sorted(means[bed])
                values = [means[bed][d] for d in dates]
                ax.plot(
                    dates, values,
                    color=COLORS[bed],
                    label=LABELS[bed] if variant == published else None,
                    **STYLE_BY_RANK[rank],
                )
        if invert:
            ax.invert_yaxis()
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=9)
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        ax.legend(fontsize=7, title="solid = published threshold", title_fontsize=6)

    fig.suptitle(
        "Background-sensitivity check — across-threshold trajectories, all modalities\n"
        "(does the W1-vs-W2 divergence survive stricter/looser vegetation masking?)",
        fontsize=12, fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default=str(PROCESSED_DIR))
    parser.add_argument("--output", default=str(FIG_DIR / "background_sensitivity_across_thresholds.png"))
    args = parser.parse_args()

    make_plot(Path(args.input_dir), Path(args.output))


if __name__ == "__main__":
    main()
