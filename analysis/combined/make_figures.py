"""
Figure generation for multimodal stress analysis.
Depends on processed CSVs produced by run_stats.py:
  - thermal_ols_summary.csv
  - hyperspectral_ols_{ndvi,ci_rededge,sipi}.csv
  - turgor_daily_bed.csv
  - turgor_correlations.csv

Outputs: analysis/figures/*.png

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

PROC = Path(__file__).resolve().parent / "processed"
FIG = Path(__file__).resolve().parent.parent / "figures"
FIG.mkdir(exist_ok=True)

INTERVENTION = pd.Timestamp("2025-09-03")
ANALYSIS_END = pd.Timestamp("2025-09-14")

COLORS = {"W1": "#2166ac", "W2": "#d6604d"}
INDEX_LABELS = {
    "ndvi": "NDVI",
    "ci_rededge": "CI Red Edge",
    "sipi": "SIPI",
}

STYLE = {
    "figure.dpi": 150,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
}

PARAM_LABELS = {
    "Intercept": "Intercept",
    "C(bed, Treatment('W1'))[T.W2]": "W2 vs W1 (baseline)",
    "days_since_intervention": "Days since intervention (W1 slope)",
    "C(bed, Treatment('W1'))[T.W2]:days_since_intervention": "Interaction: W2 × time",
    "C(bed_id, Treatment('W1'))[T.W2]": "W2 vs W1 (baseline)",
    "C(bed_id, Treatment('W1'))[T.W2]:days_since_intervention": "Interaction: W2 × time",
}


def shade_intervention(ax, start=INTERVENTION):
    ax.axvline(start, color="#d6604d", lw=1, ls="--", alpha=0.7, label="Water withdrawal")


def format_date_axis(ax):
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")


def _sig_stars(p: float) -> str:
    if np.isnan(p):
        return ""
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return ""


def _read_ols_csv(path: Path) -> pd.DataFrame:
    """Read an OLS summary CSV produced by statsmodels SimpleTable.to_csv()."""
    df = pd.read_csv(path, index_col=0)
    df.index = df.index.str.strip()
    df.columns = df.columns.str.strip()
    return df


def _coef_plot(ax, df: pd.DataFrame, title: str) -> None:
    coef_col = next((c for c in df.columns if "Coef" in c), None)
    lo_col   = next((c for c in df.columns if "0.025" in c), None)
    hi_col   = next((c for c in df.columns if "0.975" in c), None)
    p_col    = next((c for c in df.columns if c.startswith("P>")), None)

    if coef_col is None:
        ax.set_title(f"{title} — unreadable CSV")
        return

    params = [p for p in df.index if "Group Var" not in p]

    coefs = df.loc[params, coef_col].astype(float).values
    los   = df.loc[params, lo_col].astype(float).values if lo_col else coefs
    his   = df.loc[params, hi_col].astype(float).values if hi_col else coefs
    ps    = df.loc[params, p_col].astype(float).values  if p_col  else np.full(len(params), np.nan)
    ys    = np.arange(len(params))

    colors = ["#d6604d" if p < 0.05 else "#888888" for p in ps]

    ax.axvline(0, color="black", lw=0.8, ls="--", alpha=0.5)
    ax.hlines(ys, los, his, colors=colors, lw=2.5, alpha=0.7)
    ax.scatter(coefs, ys, c=colors, zorder=3, s=50)

    labels = [PARAM_LABELS.get(p.strip(), p.strip()) for p in params]
    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Coefficient (95% CI)")
    ax.set_title(title, fontsize=10)


# ---------------------------------------------------------------------------
# Figure 1: Turgor time series — daily bed median
# ---------------------------------------------------------------------------

def fig_turgor():
    daily = pd.read_csv(PROC / "turgor_daily_bed.csv", parse_dates=["date"])

    fig, ax = plt.subplots(figsize=(8, 4))
    for bed in ["W1", "W2"]:
        sub = daily[daily["bed"] == bed].sort_values("date")
        ax.plot(sub["date"], sub["turgic_value"], "o-", color=COLORS[bed],
                ms=4, lw=1.5, label=bed)

    shade_intervention(ax)
    ax.set_xlim(pd.Timestamp("2025-09-01"), ANALYSIS_END)
    ax.set_ylabel("Normalized turgor (per-sensor min–max)")
    ax.set_title("Daily bed-median leaf turgor pressure")
    ax.legend(title="Bed", fontsize=8)
    format_date_axis(ax)
    ax.set_xlabel("Date")

    fig.suptitle("Leaf turgor pressure over time (W1 control vs W2 water deficit)",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    out = FIG / "turgor_divergence.png"
    fig.savefig(str(out), bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ---------------------------------------------------------------------------
# Figure 2: Spearman ρ heatmap — turgor vs all predictors by bed
# ---------------------------------------------------------------------------

def fig_crossmodal():
    df = pd.read_csv(PROC / "turgor_correlations.csv")

    beds       = sorted(df["bed"].unique())
    predictors = df["predictor"].unique()

    pred_labels = {
        "leaf_air_diff": "Leaf−air diff",
        "ndvi": "NDVI",
        "ci_rededge": "CI Red Edge",
        "sipi": "SIPI",
    }

    mat_rho = np.full((len(predictors), len(beds)), np.nan)
    mat_p   = np.full((len(predictors), len(beds)), np.nan)
    for i, pred in enumerate(predictors):
        for j, bed in enumerate(beds):
            row = df[(df["predictor"] == pred) & (df["bed"] == bed)]
            if not row.empty:
                mat_rho[i, j] = row["spearman_rho"].values[0]
                mat_p[i, j]   = row["p_value"].values[0]

    fig, ax = plt.subplots(figsize=(5, 4))
    im = ax.imshow(mat_rho, vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")

    ax.set_xticks(range(len(beds)))
    ax.set_yticks(range(len(predictors)))
    ax.set_xticklabels(beds, fontsize=10)
    ax.set_yticklabels([pred_labels.get(p, p) for p in predictors], fontsize=9)

    for i in range(len(predictors)):
        for j in range(len(beds)):
            v = mat_rho[i, j]
            s = _sig_stars(mat_p[i, j])
            text = f"{v:.2f}{s}" if not np.isnan(v) else ""
            color = "white" if not np.isnan(v) and abs(v) > 0.65 else "black"
            ax.text(j, i, text, ha="center", va="center", fontsize=9, color=color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Spearman ρ", fontsize=9)
    ax.set_title(
        "Spearman ρ: daily turgor vs stress indicators by bed\n"
        "(* p<0.05, ** p<0.01, *** p<0.001)",
        fontsize=10,
    )
    fig.tight_layout()
    out = FIG / "crossmodal_heatmap.png"
    fig.savefig(str(out), bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ---------------------------------------------------------------------------
# Figure 3: OLS coefficient plots — thermal + hyperspectral indices
# ---------------------------------------------------------------------------

def fig_ols():
    indices = list(INDEX_LABELS.keys())
    n_panels = 1 + len(indices)

    fig, axes = plt.subplots(1, n_panels, figsize=(5 * n_panels, 4))

    th_path = PROC / "thermal_ols_summary.csv"
    if th_path.exists():
        _coef_plot(axes[0], _read_ols_csv(th_path), "Thermal: leaf−air diff")
    else:
        axes[0].set_title("Thermal OLS — not found")

    for ax, idx in zip(axes[1:], indices):
        p = PROC / f"hyperspectral_ols_{idx}.csv"
        if p.exists():
            _coef_plot(ax, _read_ols_csv(p), INDEX_LABELS[idx])
        else:
            ax.set_title(f"{INDEX_LABELS[idx]} — not found")

    fig.suptitle(
        "OLS coefficients (W1 reference, ±95% CI)\n"
        "Red = p < 0.05",
        fontsize=11,
        fontweight="bold",
    )
    fig.tight_layout()
    out = FIG / "ols_coefficients.png"
    fig.savefig(str(out), bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    with plt.rc_context(STYLE):
        print("Figure 1: Turgor time series...")
        fig_turgor()

        print("Figure 2: Cross-modal Spearman heatmap...")
        fig_crossmodal()

        print("Figure 3: OLS coefficient plots...")
        fig_ols()

    print(f"\nAll figures saved to {FIG}")
