"""
Compute sampling frequency and record counts for the sensor parquet files.

Run from the repository root:
    python utils/sensor_summary.py

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse

import pandas as pd
from pathlib import Path

PARQUETS = {
    "air":  "air_readings.parquet",
    "leaf": "leaf_readings.parquet",
    "soil": "soil_readings.parquet",
}


def summarise_parquet(name: str, path: Path) -> dict:
    df = pd.read_parquet(path)
    ts = pd.to_datetime(df["berlin_timestamp"])
    n_sensors = df["sensor_number"].nunique()

    intervals = (
        ts.groupby(df["sensor_number"])
        .apply(lambda s: s.sort_values().diff().dropna().mode().iloc[0])
    )
    modal_interval = intervals.mode().iloc[0]

    return {
        "modality":           name,
        "total_rows":         len(df),
        "n_sensors":          n_sensors,
        "start":              ts.min(),
        "end":                ts.max(),
        "modal_interval":     modal_interval,
        "modal_interval_min": int(modal_interval.total_seconds() / 60),
        "duration_days":      round((ts.max() - ts.min()).total_seconds() / 86400, 1),
    }


def summarise_par(path: Path) -> dict | None:
    """Parse the PAR sheet from metadata_rwc_par.xlsx.

    The sheet is a raw logger export: 12 metadata rows, then a header row at
    index 12 ('No.', 'Date/Time:', 'Measuring Value:'), then data from row 13.
    """
    PAR_SHEET   = "PAR"
    HEADER_ROW  = 12   # 0-indexed row that contains column labels
    DATA_OFFSET = HEADER_ROW + 1

    xf = pd.ExcelFile(path)
    if PAR_SHEET not in xf.sheet_names:
        print(f"[PAR] Sheet '{PAR_SHEET}' not found in {path.name}")
        return None

    df = xf.parse(PAR_SHEET, header=HEADER_ROW)
    # Column names after parsing: 'No.', 'Date/Time:', 'Measuring Value:'
    date_col  = "Date/Time:"
    value_col = "Measuring Value:"

    ts     = pd.to_datetime(df[date_col], dayfirst=True, errors="coerce").dropna()
    values = df[value_col].dropna()
    n_rows = min(len(ts), len(values))

    diffs    = ts.sort_values().diff().dropna()
    modal_td = diffs.mode().iloc[0] if len(diffs) else pd.NaT
    modal_min = int(modal_td.total_seconds() / 60) if pd.notna(modal_td) else None

    return {
        "modality":           "par",
        "total_rows":         n_rows,
        "n_sensors":          1,
        "start":              ts.min(),
        "end":                ts.max(),
        "modal_interval_min": modal_min,
        "duration_days":      round((ts.max() - ts.min()).total_seconds() / 86400, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Record counts and sampling frequency of the sensor files.")
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"),
                        help="Folder holding the parquet files and metadata_rwc_par.xlsx "
                             "(default: the repo's data/ folder)")
    data_dir = Path(parser.parse_args().data_dir)

    rows = [summarise_parquet(name, data_dir / fname) for name, fname in PARQUETS.items()]

    par = summarise_par(data_dir / "metadata_rwc_par.xlsx")
    if par:
        rows.append(par)

    summary = pd.DataFrame(rows).set_index("modality")
    display_cols = [c for c in ["total_rows", "n_sensors", "modal_interval_min",
                                "duration_days", "start", "end"] if c in summary.columns]
    print("=== Sensor Data Summary ===")
    print(summary[display_cols].to_string())


if __name__ == "__main__":
    main()
