"""
Shared plotting/aggregation helpers for the background-sensitivity checks
(pot/soil background vs. plant signal), one per modality:
  - thermal_background_sensitivity.py  (coolest/warmest pixel fraction)
  - depth_background_sensitivity.py    (EXG-threshold sweep on aligned point clouds)
  - rgb_background_sensitivity.py      (EXG-threshold sweep, native RGB)
  - hyperspectral_background_sensitivity.py (in-cube EXG from visible bands)

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROCESSED_DIR = HERE.parent / "processed"
FIG_DIR = HERE.parent.parent / "figures"
FIG_DIR.mkdir(exist_ok=True)

COLORS = {"W1": "#2166ac", "W2": "#d6604d"}
LABELS = {"W1": "W1 (control)", "W2": "W2 (water deficit)"}


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def bed_date_means(rows: list[dict], column: str) -> dict[str, dict[str, float]]:
    """Return {bed: {date: mean_value}} for one column, averaged across plants, skipping blanks/NaN."""
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        raw = r.get(column, "")
        if raw in ("", "nan", "NaN"):
            continue
        buckets[r["bed"]][r["date"]].append(float(raw))
    return {
        bed: {date: sum(vals) / len(vals) for date, vals in dates.items() if vals}
        for bed, dates in buckets.items()
    }


def w2_minus_w1(rows: list[dict], column: str) -> tuple[list[str], list[float]]:
    """Return (shared_dates, diffs) of mean(W2) - mean(W1) per date for one column."""
    means = bed_date_means(rows, column)
    if "W1" not in means or "W2" not in means:
        return [], []
    common_dates = sorted(set(means["W1"]) & set(means["W2"]))
    diffs = [means["W2"][d] - means["W1"][d] for d in common_dates]
    return common_dates, diffs
