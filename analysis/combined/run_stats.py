"""
Statistical analysis of multimodal stress signals (W1 control vs W2 water deficit).

Analysis 1: OLS — leaf-to-air temperature difference over time (W1 vs W2).
Analysis 2: OLS — hyperspectral index (NDVI, CI_RedEdge, SIPI) over time (W1 vs W2).
Analysis 3: Spearman correlation — daily median turgor vs leaf temperature
            and hyperspectral indices over time, per bed.

All regression models use the fixed-effects structure:
  outcome ~ treatment * days_since_intervention
where treatment contrasts W2 against W1 (reference). Both hyperspectral and thermal data are
aggregated to per-bed daily values (mean and median respectively) before modelling, so each
observation is an independent bed-date point and OLS is appropriate without random effects.

Date windows: the inputs cover Sept 1–14 (the results figure shows the two pre-cutoff days
to show both beds behaving alike before the cutoff), but all models and correlations use
Sept 3–14 only, from the irrigation cutoff (day 0; the treatment beds were last watered at
11:00 that day) to the day before irrigation resumed. A single straight-line slope cannot
treat the pre-cutoff days as a baseline, so including them would only dilute the slope.
The plant-height mixed model uses the same window (analysis/plant_height/config.py).

Sensor assignments for turgor:
  W1 (control):       sensors 19–21
  W2 (water deficit): sensors 10–18

Outputs: processed/ sub-directory (OLS tables + correlation CSV).

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import json
import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.formula.api import ols

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
DATA = ROOT / "data"  # dataset folder; --data-dir overrides it
RESULTS_XLSX = ROOT / "data" / "results_w5_mean_15_sa.xlsx"  # tracked in the repo
ANALYSIS = ROOT / "analysis"
OUT = Path(__file__).resolve().parent
PROC = OUT / "processed"
PROC.mkdir(exist_ok=True)

INTERVENTION = pd.Timestamp("2025-09-03")
ANALYSIS_START = pd.Timestamp("2025-09-01")
ANALYSIS_END = pd.Timestamp("2025-09-14")
MODEL_START = INTERVENTION  # models and correlations start at the cutoff (see docstring)
HYPERSPECTRAL_INDICES = ["ndvi", "ci_rededge", "sipi"]

W1_SENSORS = list(range(19, 22))   # 19, 20, 21
W2_SENSORS = list(range(10, 19))   # 10–18


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_hyperspectral():
    df = pd.read_excel(RESULTS_XLSX)
    df = df[df["index"].isin(HYPERSPECTRAL_INDICES)].copy()
    df["date"] = pd.to_datetime(df["group"], format="%Y_%m_%d")
    df["days_since_intervention"] = (df["date"] - INTERVENTION).dt.days
    return df[df["bed_id"].isin(["W1", "W2"])].copy()


def load_thermal():
    with open(ANALYSIS / "leaf_air_temp" / "thermal_analysis.json") as f:
        raw = json.load(f)

    rows = []
    for key, records in raw.items():
        bed, plant = key.split("_", 1)
        for r in records:
            row = dict(r)
            row["bed"] = bed
            row["plant"] = plant
            row["plant_id"] = key
            rows.append(row)

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="%Y_%m_%d")
    df["hour"] = df["hour"].astype(int)
    df = df[df["hour"] == 15].copy()
    df["days_since_intervention"] = (df["date"] - INTERVENTION).dt.days

    # leaf-to-air diff; W2 averages both air sensors, W1 uses sensor 2
    df["leaf_air_diff"] = df["leaf_temp_c"] - np.where(
        df["bed"] == "W1",
        df["air_temp_sensor2_c"],
        (df["air_temp_sensor1_c"] + df["air_temp_sensor2_c"]) / 2,
    )
    return df[df["bed"].isin(["W1", "W2"])].copy()


def load_thermal_background():
    """Bed-date background (soil/pot) proxy from the background-sensitivity check.

    Produced by analysis/background_sensitivity/compute/thermal_background_sensitivity.py
    (warmest 25% of pixels per thermal TIF, aggregated here to one value per
    bed-date to match the granularity of analyse_thermal_ols's model input).

    Used only for the descriptive correlation in analyse_thermal_background_correlation
    below — NOT as a regression covariate. Background soil/pot temperature is plausibly
    downstream of the treatment itself (less watering -> drier, warmer soil, in parallel
    with less watering -> reduced leaf transpiration), so it sits on or alongside the
    causal path from treatment to leaf temperature rather than outside it. Adding it as
    a covariate in an OLS predicting leaf_air_diff would condition on a likely mediator /
    shared-cause variable, which can attenuate a real treatment effect rather than reveal
    a fake one ("bad control").

    Returns None if the background-sensitivity check (README Step 7, optional) has not
    been run yet.
    """
    path = ANALYSIS / "background_sensitivity" / "processed" / "thermal_background_sensitivity.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], format="%Y_%m_%d")
    df["days_since_intervention"] = (df["date"] - INTERVENTION).dt.days
    return (
        df.groupby(["bed", "days_since_intervention"])["background"]
        .median()
        .reset_index()
    )


def load_turgor():
    df = pd.read_parquet(DATA / "leaf_readings.parquet")
    df["sensor_number"] = df["sensor_number"].astype(int)
    df = df[df["sensor_number"].isin(W1_SENSORS + W2_SENSORS)].copy()
    df["bed"] = np.where(df["sensor_number"].isin(W1_SENSORS), "W1", "W2")
    df["date"] = df["berlin_timestamp"].dt.normalize().dt.tz_localize(None)

    # Each sensor has its own arbitrary range, so raw values are not comparable
    # across sensors. Normalize to [0, 1] per sensor using the full recorded range
    # before restricting to the analysis window.
    stats = df.groupby("sensor_number")["turgic_value"].agg(["min", "max"])
    df = df.join(stats, on="sensor_number")
    rng = (df["max"] - df["min"]).replace(0, np.nan)
    df["turgic_value"] = (df["turgic_value"] - df["min"]) / rng
    df = df.drop(columns=["min", "max"])

    df = df[(df["date"] >= ANALYSIS_START) & (df["date"] <= ANALYSIS_END)].copy()
    return df


# ---------------------------------------------------------------------------
# Analysis 1: OLS — leaf temperature (W1 vs W2)
# ---------------------------------------------------------------------------

def analyse_thermal_ols(df):
    ols_df = (
        df.groupby(["bed", "days_since_intervention"])["leaf_air_diff"]
        .median()
        .reset_index()
        .dropna()
    )
    ols_df["bed"] = ols_df["bed"].astype("category")
    fit = ols(
        "leaf_air_diff ~ C(bed, Treatment('W1')) * days_since_intervention",
        data=ols_df,
    ).fit()
    summary = fit.summary2().tables[1]
    summary.to_csv(PROC / "thermal_ols_summary.csv")
    print(summary)


def analyse_thermal_background_correlation(df, bg_df):
    """Descriptive-only companion to analyse_thermal_ols: reports how background
    (soil/pot) temperature covaries with time and with the leaf-air signal, per bed.

    Deliberately NOT a regression adjustment (see load_thermal_background's
    docstring for why treating background as a covariate to control for is
    methodologically unsound here — it's plausibly downstream of the treatment,
    not an independent confound). Spearman correlation makes no claim about
    which variable is a cause of which; it just documents the co-movement so
    the paper can state it as expected corroboration (soil dries out alongside
    the plant under water deficit) rather than as a threat to the leaf-signal
    finding.
    """
    ols_df = (
        df.groupby(["bed", "days_since_intervention"])["leaf_air_diff"]
        .median()
        .reset_index()
        .dropna()
    )
    merged = ols_df.merge(bg_df, on=["bed", "days_since_intervention"], how="inner")

    results = []
    for bed in ("W1", "W2"):
        sub = merged[merged["bed"] == bed]
        rho_time, p_time = stats.spearmanr(sub["days_since_intervention"], sub["background"])
        rho_leaf, p_leaf = stats.spearmanr(sub["background"], sub["leaf_air_diff"])
        results.append({
            "bed": bed, "predictor": "days_since_intervention", "target": "background",
            "n": len(sub), "spearman_rho": rho_time, "p_value": p_time,
        })
        results.append({
            "bed": bed, "predictor": "background", "target": "leaf_air_diff",
            "n": len(sub), "spearman_rho": rho_leaf, "p_value": p_leaf,
        })

    res_df = pd.DataFrame(results)
    res_df.to_csv(PROC / "thermal_background_correlation.csv", index=False)
    print(res_df.to_string(index=False))
    return res_df


# ---------------------------------------------------------------------------
# Analysis 2: OLS — hyperspectral indices (W1 vs W2)
# ---------------------------------------------------------------------------

def analyse_hyperspectral_ols(df):
    for idx in HYPERSPECTRAL_INDICES:
        sub = df[df["index"] == idx][
            ["mean", "bed_id", "days_since_intervention"]
        ].dropna().copy()
        sub["bed_id"] = sub["bed_id"].astype("category")
        fit = ols(
            "mean ~ C(bed_id, Treatment('W1')) * days_since_intervention",
            data=sub,
        ).fit()
        summary = fit.summary2().tables[1]
        summary.to_csv(PROC / f"hyperspectral_ols_{idx}.csv")
        print(f"\n{idx}:")
        print(summary)


# ---------------------------------------------------------------------------
# Analysis 3: Spearman correlation — turgor vs thermal and hyperspectral
# ---------------------------------------------------------------------------

def analyse_turgor_correlations(hyp_df, th_df, tur_df):
    # Daily median turgor per bed
    daily_tur = (
        tur_df.groupby(["bed", "date"])["turgic_value"]
        .median()
        .reset_index()
    )
    daily_tur.to_csv(PROC / "turgor_daily_bed.csv", index=False)

    # Daily median leaf-air diff per bed
    daily_th = (
        th_df.groupby(["bed", "date"])["leaf_air_diff"]
        .median()
        .reset_index()
    )

    results = []
    for bed in ["W1", "W2"]:
        tur_s = daily_tur[daily_tur["bed"] == bed].set_index("date")["turgic_value"]
        th_s = daily_th[daily_th["bed"] == bed].set_index("date")["leaf_air_diff"]

        merged = pd.concat([tur_s, th_s], axis=1, sort=True).dropna()
        if len(merged) >= 4:
            rho, p = stats.spearmanr(merged["turgic_value"], merged["leaf_air_diff"])
        else:
            rho, p = np.nan, np.nan
        results.append({
            "bed": bed,
            "predictor": "leaf_air_diff",
            "n": len(merged),
            "spearman_rho": rho,
            "p_value": p,
        })

        for idx in HYPERSPECTRAL_INDICES:
            hyp_s = (
                hyp_df[(hyp_df["index"] == idx) & (hyp_df["bed_id"] == bed)]
                .set_index("date")["mean"]
            )
            merged = pd.concat([tur_s, hyp_s], axis=1, sort=True).dropna()
            if len(merged) >= 4:
                rho, p = stats.spearmanr(merged["turgic_value"], merged["mean"])
            else:
                rho, p = np.nan, np.nan
            results.append({
                "bed": bed,
                "predictor": idx,
                "n": len(merged),
                "spearman_rho": rho,
                "p_value": p,
            })

    res_df = pd.DataFrame(results)
    res_df.to_csv(PROC / "turgor_correlations.csv", index=False)
    return res_df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _parser = argparse.ArgumentParser(description="Statistical analyses for the paper.")
    _parser.add_argument("--data-dir", default=str(DATA),
                         help="Dataset folder holding leaf_readings.parquet (default: the repo's data/ folder)")
    DATA = Path(_parser.parse_args().data_dir)

    print("Loading data...")
    hyp = load_hyperspectral()
    th = load_thermal()
    tur = load_turgor()
    hyp = hyp[hyp["date"].between(MODEL_START, ANALYSIS_END)].copy()
    th = th[th["date"].between(MODEL_START, ANALYSIS_END)].copy()
    tur = tur[tur["date"].between(MODEL_START, ANALYSIS_END)].copy()
    print(f"Model window: {MODEL_START.date()} .. {ANALYSIS_END.date()}")

    print("\nAnalysis 1: Thermal OLS (W1 vs W2)...")
    analyse_thermal_ols(th)

    print("\nAnalysis 1b: Thermal-background correlation (descriptive, background-sensitivity check)...")
    th_bg = load_thermal_background()
    if th_bg is None:
        print("  skipped: run the background-sensitivity check first (README Step 7) "
              "to get analysis/background_sensitivity/processed/thermal_background_sensitivity.csv")
    else:
        analyse_thermal_background_correlation(th, th_bg)

    print("\nAnalysis 2: Hyperspectral OLS (W1 vs W2)...")
    analyse_hyperspectral_ols(hyp)

    print("\nAnalysis 3: Turgor correlations...")
    res_cor = analyse_turgor_correlations(hyp, th, tur)
    print(res_cor.to_string(index=False))

    print(f"\nProcessed intermediates: {PROC}")
