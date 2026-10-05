"""
Compute EXG-filtered plant height metrics from point clouds.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from plyfile import PlyData

try:
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore


_NAME_RE = re.compile(r"^(?P<plant_id>W[12]_[A-Z0-9]+)_(?P<date>\d{4}_\d{2}_\d{2})_(?P<hour>\d{2})_cloud\.ply$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute per-cloud metrics from W1/W2 PLY files")
    parser.add_argument(
        "--ply-dir",
        default=str(config.PLY_DIR),
        help="Directory containing *_cloud.ply files",
    )
    parser.add_argument(
        "--output-csv",
        default=str(config.METRICS_CSV),
        help="Output metrics CSV",
    )
    parser.add_argument(
        "--exg-threshold",
        type=float,
        default=config.DEFAULT_EXG_THRESHOLD,
        help="Vegetation threshold for EXG channel",
    )
    return parser.parse_args()


def _parse_name(file_name: str) -> Optional[Tuple[str, str, str]]:
    match = _NAME_RE.fullmatch(file_name)
    if not match:
        return None
    return str(match.group("plant_id")), str(match.group("date")), str(match.group("hour"))


def _load_channels(cloud_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    ply = PlyData.read(str(cloud_path))
    if "vertex" not in ply:
        raise ValueError("PLY missing 'vertex' element")
    vertex = ply["vertex"].data
    names = set(vertex.dtype.names or [])
    if "z" not in names or "exg" not in names:
        raise ValueError("PLY missing required channels: z, exg")
    z = np.asarray(vertex["z"], dtype=np.float64)
    exg = np.asarray(vertex["exg"], dtype=np.float64)
    if z.shape != exg.shape:
        raise ValueError("z/exg channel length mismatch")
    return z, exg


def _compute_metrics(z: np.ndarray, exg: np.ndarray, threshold: float) -> Dict[str, float]:
    n_total = int(z.size)
    if n_total == 0:
        return {
            "num_points_total": 0.0,
            "num_points_vegetation": 0.0,
            "vegetation_fraction": float("nan"),
            "z_mean": float("nan"),
            "z_std": float("nan"),
            "z_min": float("nan"),
            "z_max": float("nan"),
            "z_median": float("nan"),
            "z_p25": float("nan"),
            "z_p75": float("nan"),
            "z_iqr": float("nan"),
        }

    # Vegetation definition used consistently across metrics/plot/model stages.
    mask = exg > threshold
    n_veg = int(np.count_nonzero(mask))
    veg_frac = float(n_veg) / float(n_total)
    if n_veg == 0:
        return {
            "num_points_total": float(n_total),
            "num_points_vegetation": 0.0,
            "vegetation_fraction": veg_frac,
            "z_mean": float("nan"),
            "z_std": float("nan"),
            "z_min": float("nan"),
            "z_max": float("nan"),
            "z_median": float("nan"),
            "z_p25": float("nan"),
            "z_p75": float("nan"),
            "z_iqr": float("nan"),
        }

    z_veg = z[mask]
    # z_p25 is used later as robust depth-domain canopy proxy.
    z_p25, z_p75 = np.percentile(z_veg, [25.0, 75.0])
    z_std = float(np.std(z_veg, ddof=1)) if z_veg.size > 1 else float("nan")

    return {
        "num_points_total": float(n_total),
        "num_points_vegetation": float(n_veg),
        "vegetation_fraction": veg_frac,
        "z_mean": float(np.mean(z_veg)),
        "z_std": z_std,
        "z_min": float(np.min(z_veg)),
        "z_max": float(np.max(z_veg)),
        "z_median": float(np.median(z_veg)),
        "z_p25": float(z_p25),
        "z_p75": float(z_p75),
        "z_iqr": float(z_p75 - z_p25),
    }


def main() -> int:
    args = _parse_args()
    ply_dir = Path(args.ply_dir)
    output_csv = Path(args.output_csv)
    threshold = float(args.exg_threshold)

    if not ply_dir.exists() or not ply_dir.is_dir():
        raise ValueError(f"PLY directory does not exist: {ply_dir}")

    cloud_paths = sorted(ply_dir.glob("*.ply"))
    if not cloud_paths:
        raise RuntimeError(f"No .ply files found in {ply_dir}")

    rows: List[Dict[str, object]] = []
    skipped = 0

    for cloud_path in cloud_paths:
        parsed = _parse_name(cloud_path.name)
        if parsed is None:
            skipped += 1
            continue
        plant_id, date, hour = parsed

        try:
            z, exg = _load_channels(cloud_path)
            metrics = _compute_metrics(z, exg, threshold)
        except Exception as exc:
            skipped += 1
            print(f"[WARN] Skip {cloud_path.name}: {exc}")
            continue

        row: Dict[str, object] = {
            "file_name": cloud_path.name,
            "plant_id": plant_id,
            "date": date,
            "hour": hour,
            "exg_threshold": threshold,
        }
        row.update(metrics)
        rows.append(row)

    if not rows:
        raise RuntimeError("No point clouds were successfully processed")

    rows.sort(key=lambda r: (str(r["plant_id"]), str(r["date"]), int(str(r["hour"]))))
    fieldnames = [
        "file_name",
        "plant_id",
        "date",
        "hour",
        "exg_threshold",
        "num_points_total",
        "num_points_vegetation",
        "vegetation_fraction",
        "z_mean",
        "z_std",
        "z_min",
        "z_max",
        "z_median",
        "z_p25",
        "z_p75",
        "z_iqr",
    ]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"PLY directory: {ply_dir}")
    print(f"EXG threshold: {threshold}")
    print(f"Files found: {len(cloud_paths)}")
    print(f"Processed: {len(rows)}")
    print(f"Skipped: {skipped}")
    print(f"Output metrics CSV: {output_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
