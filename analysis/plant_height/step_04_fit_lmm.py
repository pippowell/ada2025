"""
Fit plant-height mixed model with random intercept per plant location.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

try:
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit random-intercept mixed model for plant height")
    parser.add_argument("--metrics-csv", default=str(config.METRICS_CSV), help="Input metrics CSV")
    parser.add_argument("--model-input-csv", default=str(config.MODEL_INPUT_CSV), help="Saved model input CSV")
    parser.add_argument("--summary-txt", default=str(config.MODEL_SUMMARY_TXT), help="Model summary text path")
    parser.add_argument(
        "--coefficients-csv",
        default=str(config.MODEL_COEFFICIENTS_CSV),
        help="Model coefficients CSV path",
    )
    parser.add_argument(
        "--trajectory-png",
        default=str(config.MODEL_TRAJECTORY_PNG),
        help="Trajectory plot output path",
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
        help="Minimum num_points_vegetation",
    )
    parser.add_argument("--start-date", default=config.DEFAULT_START_DATE, help="Start date YYYY_MM_DD")
    parser.add_argument("--end-date", default=config.DEFAULT_END_DATE, help="End date YYYY_MM_DD")
    parser.add_argument("--show", action="store_true", help="Show trajectory plot")
    return parser.parse_args()


def _load_model_input(
    metrics_csv: Path,
    sensor_height: float,
    vegetation_threshold: float,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if not metrics_csv.exists() or not metrics_csv.is_file():
        raise ValueError(f"Metrics CSV does not exist: {metrics_csv}")

    df = pd.read_csv(metrics_csv)
    required = {"plant_id", "date", "z_p25", "num_points_vegetation"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Metrics CSV missing required columns: {missing}")

    data = df.copy()
    data["date"] = pd.to_datetime(data["date"], format="%Y_%m_%d", errors="coerce")
    data["z_p25"] = pd.to_numeric(data["z_p25"], errors="coerce")
    data["num_points_vegetation"] = pd.to_numeric(data["num_points_vegetation"], errors="coerce")
    data = data[data["plant_id"].astype(str).str.startswith(("W1_", "W2_"))].copy()

    start = pd.to_datetime(start_date, format="%Y_%m_%d", errors="raise")
    end = pd.to_datetime(end_date, format="%Y_%m_%d", errors="raise")

    valid_mask = (
        data["date"].notna()
        & data["z_p25"].notna()
        & data["num_points_vegetation"].notna()
        & (data["num_points_vegetation"] >= float(vegetation_threshold))
        & (data["date"] >= start)
        & (data["date"] <= end)
    )
    filtered = data.loc[valid_mask].copy()
    if filtered.empty:
        raise RuntimeError("No rows left after date/quality filters")

    # Transform depth-domain statistic to upward height estimate.
    filtered["height_estimate"] = float(sensor_height) - filtered["z_p25"]
    filtered["treatment"] = filtered["plant_id"].astype(str).str.split("_").str[0]
    filtered["treatment"] = pd.Categorical(filtered["treatment"], categories=["W1", "W2"], ordered=True)
    # Rebase time to day 0 at the first retained date for this model run.
    filtered["day"] = (filtered["date"] - filtered["date"].min()).dt.days.astype(float)

    observed = set(filtered["treatment"].astype(str).unique())
    if observed != {"W1", "W2"}:
        raise RuntimeError(f"Filtered data must include both W1 and W2, found: {sorted(observed)}")

    return filtered[["plant_id", "date", "day", "treatment", "height_estimate", "num_points_vegetation"]].copy()


def _fit_model(model_input: pd.DataFrame):
    # Random-intercept model: repeated measures per plant_id.
    model = smf.mixedlm(
        "height_estimate ~ day * treatment",
        data=model_input,
        groups=model_input["plant_id"],
    )
    try:
        return model.fit(reml=False, method="lbfgs", maxiter=300)
    except Exception:
        return model.fit(reml=False)


def _save_summary(result, out_txt: Path, cfg: argparse.Namespace, n_rows: int, n_plants: int) -> None:
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    with out_txt.open("w", encoding="utf-8") as handle:
        handle.write("Plant Height Mixed Model (Random Intercept)\n")
        handle.write("==========================================\n")
        handle.write("Formula: height_estimate ~ day * treatment + (1 | plant_id)\n")
        handle.write(f"Sensor height: {cfg.sensor_height}\n")
        handle.write("Height transform: height_estimate = sensor_height - z_p25\n")
        handle.write(f"Vegetation threshold: {cfg.vegetation_threshold}\n")
        handle.write(f"Date range: {cfg.start_date} .. {cfg.end_date}\n")
        handle.write(f"Rows used: {n_rows}\n")
        handle.write(f"Plants used: {n_plants}\n\n")
        handle.write(str(result.summary()))
        handle.write("\n")


def _save_coefficients(result, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    conf_int = result.conf_int()
    coef_df = pd.DataFrame(
        {
            "term": result.params.index,
            "estimate": result.params.values,
            "std_error": result.bse.values,
            "z_value": result.tvalues.values,
            "p_value": result.pvalues.values,
            "ci_lower": conf_int.iloc[:, 0].values,
            "ci_upper": conf_int.iloc[:, 1].values,
        }
    )
    coef_df.to_csv(out_csv, index=False)


def _plot_trajectories(model_input: pd.DataFrame, out_png: Path, show: bool) -> None:
    fig, ax = plt.subplots(figsize=(11, 6))

    for plant_id, group in model_input.groupby("plant_id"):
        ordered = group.sort_values("day")
        treatment = str(ordered["treatment"].iloc[0])
        color = "#2E8B57" if treatment == "W1" else "#1F77B4"
        ax.plot(ordered["day"], ordered["height_estimate"], color=color, linewidth=1.0, alpha=0.35)

    # Overlay bed-level means to show treatment trajectory clearly.
    means = (
        model_input.groupby(["treatment", "day"], as_index=False, observed=False)["height_estimate"]
        .mean()
        .sort_values(["treatment", "day"])
    )
    for treatment, color, label in (
        ("W1", "#2E8B57", "W1 (Control)"),
        ("W2", "#1F77B4", "W2 (Drought)"),
    ):
        sub = means[means["treatment"] == treatment]
        if sub.empty:
            continue
        ax.plot(sub["day"], sub["height_estimate"], color=color, linewidth=3.0, marker="o", label=label)

    ax.set_title("Plant Height Trajectories Under Control and Drought Conditions")
    ax.set_xlabel("Day since first measurement")
    ax.set_ylabel("Estimated plant height (m)")
    ax.grid(True, alpha=0.3)
    legend_handles = [
        Line2D([0], [0], color="#2E8B57", linewidth=1.0, alpha=0.35, label="W1 plants"),
        Line2D([0], [0], color="#1F77B4", linewidth=1.0, alpha=0.35, label="W2 plants"),
        Line2D([0], [0], color="#2E8B57", linewidth=3.0, marker="o", label="W1 (Control)"),
        Line2D([0], [0], color="#1F77B4", linewidth=3.0, marker="o", label="W2 (Drought)"),
    ]
    ax.legend(handles=legend_handles)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png, dpi=300)
    if show:
        plt.show()
    else:
        plt.close(fig)


def main() -> int:
    args = _parse_args()
    metrics_csv = Path(args.metrics_csv)
    model_input_csv = Path(args.model_input_csv)
    summary_txt = Path(args.summary_txt)
    coeffs_csv = Path(args.coefficients_csv)
    trajectory_png = Path(args.trajectory_png)

    model_input = _load_model_input(
        metrics_csv=metrics_csv,
        sensor_height=float(args.sensor_height),
        vegetation_threshold=float(args.vegetation_threshold),
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )
    model_input_csv.parent.mkdir(parents=True, exist_ok=True)
    model_input.to_csv(model_input_csv, index=False)

    result = _fit_model(model_input)

    _save_summary(result, summary_txt, args, n_rows=len(model_input), n_plants=int(model_input["plant_id"].nunique()))
    _save_coefficients(result, coeffs_csv)
    _plot_trajectories(model_input, trajectory_png, show=bool(args.show))

    interaction = "day:treatment[T.W2]"
    estimate = float(result.params[interaction]) if interaction in result.params.index else float("nan")
    p_value = float(result.pvalues[interaction]) if interaction in result.pvalues.index else float("nan")

    print(f"Metrics CSV: {metrics_csv}")
    print(f"Rows in model input: {len(model_input)}")
    print(f"Plants used: {model_input['plant_id'].nunique()}")
    print(f"Model input CSV: {model_input_csv}")
    print(f"Summary TXT: {summary_txt}")
    print(f"Coefficients CSV: {coeffs_csv}")
    print(f"Trajectory plot: {trajectory_png}")
    print(f"Interaction day:treatment[T.W2] estimate={estimate:.6g}, p={p_value:.6g}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
