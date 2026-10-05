"""
Shared configuration for plant-height analysis scripts.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple


ROOT = Path(__file__).resolve().parents[2]

# Data root produced by utils/unzip_data.py (defaults to repo's data/ folder)
DEFAULT_DATA_ROOT = ROOT / "data"

# Output layout under analysis/plant_height
# All step scripts write into these folders by default.
PLANT_HEIGHT_DIR = Path(__file__).resolve().parent
PROCESSED_DIR = PLANT_HEIGHT_DIR / "processed"
FIGURES_DIR = PLANT_HEIGHT_DIR / "figures"
MODELS_DIR = PLANT_HEIGHT_DIR / "models"

PLY_DIR = PROCESSED_DIR / "ply"
METRICS_CSV = PROCESSED_DIR / "depth_analysis_metrics.csv"
PLOT_VALUES_CSV = PROCESSED_DIR / "depth_results_w1_w2_values.csv"
PLOT_FIGURE_PNG = FIGURES_DIR / "depth_results_w1_w2.png"
MODEL_INPUT_CSV = MODELS_DIR / "mixedlm_model_input.csv"
MODEL_SUMMARY_TXT = MODELS_DIR / "mixedlm_height_estimate_summary.txt"
MODEL_COEFFICIENTS_CSV = MODELS_DIR / "mixedlm_height_estimate_coefficients.csv"
MODEL_TRAJECTORY_PNG = MODELS_DIR / "mixedlm_height_estimate_trajectories.png"

# Scope: W1/W2 only (control vs drought)
ALLOWED_BEDS: Tuple[str, str] = ("W1", "W2")
# The paper's five plants per bed (four grid corners and the centre)
DEFAULT_PLANT_IDS: Tuple[str, ...] = tuple(
    f"{bed}_{pos}" for bed in ALLOWED_BEDS for pos in ("A2", "A8", "J5", "R1", "R7")
)

# Default processing parameters
# These are defaults only; step/orchestrator CLI flags can override them per run.
DEFAULT_FRAME_INDEX = 7
DEFAULT_HOUR = 15
DEFAULT_EXG_THRESHOLD = 5.0
DEFAULT_SENSOR_HEIGHT_M = 0.60
DEFAULT_VEGETATION_PT_THRESHOLD = 1000
DEFAULT_START_DATE = "2025_09_01"
DEFAULT_END_DATE = "2025_09_14"
# Mixed-model stage often starts at intervention onset, later than extraction window.
DEFAULT_MODEL_START_DATE = "2025_09_03"

# Camera intrinsics/extrinsics are no longer defined here: they live in
# ``analysis/combined/camera_calibration.py`` (see ``docs/camera_calibration.md``)
# and are imported by the step scripts directly.
