"""
Run the full W1/W2 plant-height pipeline (pointcloud -> metrics -> plot -> LMM).

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import List

try:
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full plant-height analysis pipeline")
    parser.add_argument(
        "--data-dir",
        default=str(config.DEFAULT_DATA_ROOT),
        help="Dataset folder holding depth/ and rgb/ (default: the repo's data/ folder)",
    )
    parser.add_argument("--frame-index", type=int, default=config.DEFAULT_FRAME_INDEX, help="Frame id")
    parser.add_argument("--hour", type=int, default=config.DEFAULT_HOUR, help="Hour filter")
    parser.add_argument(
        "--pipeline-start-date",
        default=config.DEFAULT_START_DATE,
        help="Start date for pointcloud/metrics/plot pipeline (YYYY_MM_DD)",
    )
    parser.add_argument(
        "--pipeline-end-date",
        default=config.DEFAULT_END_DATE,
        help="End date for pointcloud/metrics/plot pipeline (YYYY_MM_DD)",
    )
    parser.add_argument(
        "--model-start-date",
        default=config.DEFAULT_MODEL_START_DATE,
        help="Start date for mixed model stage (YYYY_MM_DD)",
    )
    parser.add_argument(
        "--model-end-date",
        default=config.DEFAULT_END_DATE,
        help="End date for mixed model stage (YYYY_MM_DD)",
    )
    parser.add_argument("--exg-threshold", type=float, default=config.DEFAULT_EXG_THRESHOLD, help="EXG threshold")
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
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing PLYs")
    parser.add_argument("--skip-pointclouds", action="store_true", help="Skip point cloud stage")
    parser.add_argument("--skip-metrics", action="store_true", help="Skip metrics stage")
    parser.add_argument("--skip-plot", action="store_true", help="Skip plotting stage")
    parser.add_argument("--skip-model", action="store_true", help="Skip model fitting stage")
    return parser.parse_args()


def _run_command(cmd: List[str], cwd: Path) -> None:
    print(f"[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd), check=True)


def main() -> int:
    args = _parse_args()
    script_dir = Path(__file__).resolve().parent

    if not args.skip_pointclouds:
        # Step 01: build EXG-augmented point clouds from staged depth/rgb folders.
        _run_command(
            [
                sys.executable,
                str(script_dir / "step_01_build_pointclouds.py"),
                "--data-dir",
                str(args.data_dir),
                "--frame-index",
                str(args.frame_index),
                "--hour",
                str(args.hour),
                "--start-date",
                str(args.pipeline_start_date),
                "--end-date",
                str(args.pipeline_end_date),
                *( ["--overwrite"] if args.overwrite else [] ),
            ],
            cwd=script_dir,
        )

    if not args.skip_metrics:
        # Step 02: compute vegetation-filtered z metrics from generated PLYs.
        _run_command(
            [
                sys.executable,
                str(script_dir / "step_02_compute_metrics.py"),
                "--ply-dir",
                str(config.PLY_DIR),
                "--output-csv",
                str(config.METRICS_CSV),
                "--exg-threshold",
                str(args.exg_threshold),
            ],
            cwd=script_dir,
        )

    if not args.skip_plot:
        # Step 03: create W1/W2 trajectory figure and export plotted daily values.
        _run_command(
            [
                sys.executable,
                str(script_dir / "step_03_plot_depth_results.py"),
                "--metrics-csv",
                str(config.METRICS_CSV),
                "--output-png",
                str(config.PLOT_FIGURE_PNG),
                "--output-values-csv",
                str(config.PLOT_VALUES_CSV),
                "--sensor-height",
                str(args.sensor_height),
                "--vegetation-threshold",
                str(args.vegetation_threshold),
            ],
            cwd=script_dir,
        )

    if not args.skip_model:
        # Step 04: fit random-intercept mixed model on a potentially later date window.
        _run_command(
            [
                sys.executable,
                str(script_dir / "step_04_fit_lmm.py"),
                "--metrics-csv",
                str(config.METRICS_CSV),
                "--model-input-csv",
                str(config.MODEL_INPUT_CSV),
                "--summary-txt",
                str(config.MODEL_SUMMARY_TXT),
                "--coefficients-csv",
                str(config.MODEL_COEFFICIENTS_CSV),
                "--trajectory-png",
                str(config.MODEL_TRAJECTORY_PNG),
                "--sensor-height",
                str(args.sensor_height),
                "--vegetation-threshold",
                str(args.vegetation_threshold),
                "--start-date",
                str(args.model_start_date),
                "--end-date",
                str(args.model_end_date),
            ],
            cwd=script_dir,
        )

    print("-" * 72)
    print("Plant-height pipeline complete")
    print(f"Processed outputs: {config.PROCESSED_DIR}")
    print(f"Figure outputs: {config.FIGURES_DIR}")
    print(f"Model outputs: {config.MODELS_DIR}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
