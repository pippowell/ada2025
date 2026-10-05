"""
Build aligned RGB-colored depth point clouds with EXG channel for W1/W2.

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import cv2
import numpy as np
from plyfile import PlyData, PlyElement
import tifffile

try:
    from . import config
except ImportError:  # pragma: no cover
    import config  # type: ignore

# Shared camera calibration (see docs/camera_calibration.md).
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "combined"))
import camera_calibration as cc  # noqa: E402


@dataclass(frozen=True)
class DatasetRef:
    plant_id: str
    date_str: str
    hour_str: str
    depth_dir: Path
    rgb_dir: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build W1/W2 depth+RGB point clouds (PLY with EXG)")
    parser.add_argument(
        "--data-dir",
        default=str(config.DEFAULT_DATA_ROOT),
        # This step expects modality-first staging produced by utils/unzip_data.py.
        help="Path to data root (contains depth/ and rgb/)",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=config.DEFAULT_FRAME_INDEX,
        help="Frame id to extract per dataset (default: 7)",
    )
    parser.add_argument(
        "--hour",
        type=int,
        default=config.DEFAULT_HOUR,
        help="Hour folder to process (default: 15)",
    )
    parser.add_argument(
        "--start-date",
        default=config.DEFAULT_START_DATE,
        help="Start date inclusive, format YYYY_MM_DD",
    )
    parser.add_argument(
        "--end-date",
        default=config.DEFAULT_END_DATE,
        help="End date inclusive, format YYYY_MM_DD",
    )
    parser.add_argument(
        "--plant-ids",
        default=",".join(config.DEFAULT_PLANT_IDS),
        help="Comma-separated <bed>_<plant> ids (default: the paper's five plants in W1 and W2)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing PLY outputs",
    )
    return parser.parse_args()


R_ROBOT_TO_DEPTH = cc.R_ROBOT_TO_DEPTH
T_DEPTH_TO_RGB = cc.T_DEPTH_TO_RGB


def _read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    return rows


def _find_row_for_frame(rows: List[Dict[str, str]], frame_index: int) -> Dict[str, str]:
    for row in rows:
        raw_frame_id = row.get("frame_id", "")
        try:
            frame_id = int(raw_frame_id)
        except ValueError:
            continue
        if frame_id == frame_index:
            return row
    raise ValueError(f"No CSV row found for frame_id={frame_index:03d}")


def _get_robot_position(row: Dict[str, str]) -> np.ndarray:
    return np.asarray(
        [
            float(row["position_robot_x"]),
            float(row["position_robot_y"]),
            float(row["position_robot_z"]),
        ],
        dtype=np.float64,
    )


def _load_depth_image(depth_dir: Path, frame_index: int) -> np.ndarray:
    depth_path = depth_dir / f"depth_{frame_index:03d}.tif"
    if not depth_path.exists():
        raise FileNotFoundError(f"Depth frame not found: {depth_path}")
    depth = tifffile.imread(depth_path).astype(np.float32)
    # Depth/Thermal exports are stored rotated; mirror legacy extraction behavior.
    return np.rot90(np.rot90(depth))


def _load_rgb_image(rgb_dir: Path, frame_index: int) -> np.ndarray:
    rgb_path = rgb_dir / f"rgb_{frame_index:03d}.jpg"
    if not rgb_path.exists():
        raise FileNotFoundError(f"RGB frame not found: {rgb_path}")
    bgr = cv2.imread(str(rgb_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Failed to decode RGB image: {rgb_path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _fill_depth_holes(depth_img: np.ndarray, max_gap_pixels: int = 5) -> np.ndarray:
    valid = (depth_img > 0) & ~np.isnan(depth_img)
    if np.all(valid):
        return depth_img.copy()

    filled = depth_img.copy()
    filled[~valid] = 0

    kernel_size = 2 * max_gap_pixels + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    closed_mask = cv2.morphologyEx(valid.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)

    for _ in range(max_gap_pixels):
        prev_invalid = filled == 0
        shifted = np.stack(
            [
                np.roll(filled, 1, axis=0),
                np.roll(filled, -1, axis=0),
                np.roll(filled, 1, axis=1),
                np.roll(filled, -1, axis=1),
            ]
        )
        counts = np.stack(
            [
                np.roll(valid, 1, axis=0),
                np.roll(valid, -1, axis=0),
                np.roll(valid, 1, axis=1),
                np.roll(valid, -1, axis=1),
            ]
        ).astype(np.float32)

        neighbor_sum = np.sum(shifted, axis=0)
        neighbor_count = np.sum(counts, axis=0)
        fill_mask = prev_invalid & closed_mask & (neighbor_count > 0)
        filled[fill_mask] = neighbor_sum[fill_mask] / neighbor_count[fill_mask]
        valid = filled > 0

    return filled


def _colorize_depth_with_rgb(
    depth_img: np.ndarray,
    rgb_img: np.ndarray,
    transform_depth_to_rgb: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    # Undistort in each native camera frame before 3D projection.
    depth_undist = cv2.undistort(depth_img, cc.K_DEPTH, cc.DEPTH_DISTORTION)
    rgb_undist = cv2.undistort(rgb_img, cc.K_RGB, cc.RGB_DISTORTION)
    depth_undist = _fill_depth_holes(depth_undist, max_gap_pixels=5)

    height, width = depth_undist.shape
    rgb_h, rgb_w = rgb_undist.shape[:2]

    fx_d, fy_d = cc.K_DEPTH[0, 0], cc.K_DEPTH[1, 1]
    cx_d, cy_d = cc.K_DEPTH[0, 2], cc.K_DEPTH[1, 2]
    fx_rgb, fy_rgb = cc.K_RGB[0, 0], cc.K_RGB[1, 1]
    cx_rgb, cy_rgb = cc.K_RGB[0, 2], cc.K_RGB[1, 2]

    u_coords, v_coords = np.meshgrid(np.arange(width), np.arange(height))
    z_depth = depth_undist
    valid_depth = (z_depth > 0) & ~np.isnan(z_depth)

    x_depth = (u_coords - cx_d) * z_depth / fx_d
    y_depth = (v_coords - cy_d) * z_depth / fy_d

    points_depth_h = np.stack([x_depth, y_depth, z_depth, np.ones_like(z_depth)], axis=0).reshape(4, -1)
    # Project depth points into RGB camera coordinates with shifted extrinsics.
    points_rgb_h = transform_depth_to_rgb @ points_depth_h

    z_rgb = points_rgb_h[2].reshape(height, width)
    safe_z = np.where(z_rgb > 0, z_rgb, 1.0)
    u_rgb = (fx_rgb * points_rgb_h[0].reshape(height, width) / safe_z + cx_rgb).astype(np.float32)
    v_rgb = (fy_rgb * points_rgb_h[1].reshape(height, width) / safe_z + cy_rgb).astype(np.float32)

    invalid_rgb = (
        ~valid_depth
        | (z_rgb <= 0)
        | (u_rgb < 0)
        | (u_rgb >= rgb_w)
        | (v_rgb < 0)
        | (v_rgb >= rgb_h)
    )
    u_rgb[invalid_rgb] = -1.0
    v_rgb[invalid_rgb] = -1.0

    colorized_rgb = cv2.remap(
        rgb_undist,
        u_rgb,
        v_rgb,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    colorized_rgb[invalid_rgb] = 0

    return depth_undist, colorized_rgb


def _depth_to_colorized_point_cloud(depth_img: np.ndarray, colorized_rgb: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    height, width = depth_img.shape
    fx, fy = cc.K_DEPTH[0, 0], cc.K_DEPTH[1, 1]
    cx, cy = cc.K_DEPTH[0, 2], cc.K_DEPTH[1, 2]

    u_coords, v_coords = np.meshgrid(np.arange(width), np.arange(height))
    valid_depth = (depth_img > 0) & ~np.isnan(depth_img)
    has_color = np.any(colorized_rgb != 0, axis=2)
    valid = valid_depth & has_color

    z = depth_img[valid]
    u = u_coords[valid]
    v = v_coords[valid]

    x = (u - cx) * z / fx
    y = (v - cy) * z / fy

    points = np.stack([x, y, z], axis=1).astype(np.float32)
    colors_uint8 = colorized_rgb[valid].astype(np.uint8)
    return points, colors_uint8


def _write_ply_with_exg(output_path: Path, points: np.ndarray, colors_uint8: np.ndarray) -> int:
    if len(points) == 0:
        raise RuntimeError("Cannot write PLY: no points")

    red = colors_uint8[:, 0].astype(np.float32)
    green = colors_uint8[:, 1].astype(np.float32)
    blue = colors_uint8[:, 2].astype(np.float32)
    # EXG is stored as float32 for downstream vegetation masking/threshold sweeps.
    exg = (2.0 * green - red - blue).astype(np.float32)

    vertices = np.empty(
        len(points),
        dtype=[
            ("x", "f4"),
            ("y", "f4"),
            ("z", "f4"),
            ("red", "u1"),
            ("green", "u1"),
            ("blue", "u1"),
            ("exg", "f4"),
        ],
    )
    vertices["x"] = points[:, 0]
    vertices["y"] = points[:, 1]
    vertices["z"] = points[:, 2]
    vertices["red"] = colors_uint8[:, 0]
    vertices["green"] = colors_uint8[:, 1]
    vertices["blue"] = colors_uint8[:, 2]
    vertices["exg"] = exg

    output_path.parent.mkdir(parents=True, exist_ok=True)
    PlyData([PlyElement.describe(vertices, "vertex")], text=False).write(str(output_path))
    return len(points)


def _iter_dataset_dirs(
    data_root: Path,
    hour_filter: int,
    start_date: str,
    end_date: str,
    plant_ids: List[str],
) -> Iterable[DatasetRef]:
    # Expected unzip layout:
    #   data_root/depth/<plant>/<date>/<hour>/...
    #   data_root/rgb/<plant>/<date>/<hour>/...
    depth_root = data_root / "depth"
    rgb_root = data_root / "rgb"
    if not depth_root.is_dir() or not rgb_root.is_dir():
        raise ValueError(
            f"Expected data_root to contain 'depth' and 'rgb' directories: {data_root}"
        )

    for plant_id in plant_ids:
        plant_dir = depth_root / plant_id
        if not plant_dir.is_dir():
            print(f"[WARNING] No depth folder for {plant_id} in {depth_root}")
            continue
        if not any(plant_id.startswith(f"{bed}_") for bed in config.ALLOWED_BEDS):
            continue

        rgb_plant_dir = rgb_root / plant_id
        if not rgb_plant_dir.is_dir():
            continue

        for date_dir in sorted(plant_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            date_str = date_dir.name
            if date_str < start_date or date_str > end_date:
                continue

            rgb_date_dir = rgb_plant_dir / date_str
            if not rgb_date_dir.is_dir():
                continue

            hour_str = f"{hour_filter:02d}"
            depth_hour_dir = date_dir / hour_str
            rgb_hour_dir = rgb_date_dir / hour_str
            if not depth_hour_dir.is_dir() or not rgb_hour_dir.is_dir():
                continue

            if not (depth_hour_dir / "frames_depth.csv").exists():
                continue
            if not (rgb_hour_dir / "frames_rgb.csv").exists():
                continue
            yield DatasetRef(
                plant_id=plant_id,
                date_str=date_str,
                hour_str=hour_str,
                depth_dir=depth_hour_dir,
                rgb_dir=rgb_hour_dir,
            )


def main() -> int:
    args = _parse_args()

    data_root = Path(args.data_dir)
    frame_index = int(args.frame_index)
    hour_filter = int(args.hour)
    start_date = str(args.start_date)
    end_date = str(args.end_date)

    config.PLY_DIR.mkdir(parents=True, exist_ok=True)

    refs = list(_iter_dataset_dirs(data_root, hour_filter, start_date, end_date,
                              [p.strip() for p in args.plant_ids.split(",")]))
    if not refs:
        raise RuntimeError("No W1/W2 datasets found for requested date/hour window")

    print(f"Data root: {data_root}")
    print(f"Processing beds: {', '.join(config.ALLOWED_BEDS)}")
    print(f"Date range: {start_date} .. {end_date}")
    print(f"Hour filter: {hour_filter:02d}")
    print(f"Frame index: {frame_index:03d}")
    print(f"Datasets discovered: {len(refs)}")

    success_count = 0
    skipped_count = 0
    failed_count = 0

    for index, ref in enumerate(refs, start=1):
        output_path = config.PLY_DIR / f"{ref.plant_id}_{ref.date_str}_{ref.hour_str}_cloud.ply"
        if output_path.exists() and not args.overwrite:
            # Keep pointcloud generation resumable by default.
            skipped_count += 1
            print(f"[{index}/{len(refs)}] SKIP {output_path.name} (exists)")
            continue

        print(f"[{index}/{len(refs)}] {ref.plant_id}/{ref.date_str}/{ref.hour_str}")

        try:
            depth_csv_rows = _read_csv_rows(ref.depth_dir / "frames_depth.csv")
            rgb_csv_rows = _read_csv_rows(ref.rgb_dir / "frames_rgb.csv")
            depth_row = _find_row_for_frame(depth_csv_rows, frame_index)
            rgb_row = _find_row_for_frame(rgb_csv_rows, frame_index)

            depth_img = _load_depth_image(ref.depth_dir, frame_index)
            rgb_img = _load_rgb_image(ref.rgb_dir, frame_index)

            depth_img = depth_img.astype(np.float32)
            if float(np.nanmedian(depth_img)) < 0.0:
                depth_img = -depth_img
            depth_img[depth_img <= 0] = 0.0

            depth_pos = cc.robot_to_depth_position(_get_robot_position(depth_row))
            rgb_pos = cc.robot_to_depth_position(_get_robot_position(rgb_row))
            t_shift_depth = cc.shift_matrix(depth_pos)
            t_shift_rgb = cc.shift_matrix(rgb_pos)
            # Dynamic shift aligns frame-indexed robot positions before projection.
            t_depth_to_rgb_shifted = T_DEPTH_TO_RGB @ np.linalg.inv(t_shift_rgb) @ t_shift_depth

            depth_aligned, colorized_rgb = _colorize_depth_with_rgb(depth_img, rgb_img, t_depth_to_rgb_shifted)
            points, colors_uint8 = _depth_to_colorized_point_cloud(depth_aligned, colorized_rgb)
            point_count = _write_ply_with_exg(output_path, points, colors_uint8)

            print(f"  -> wrote {output_path.name} ({point_count} points)")
            success_count += 1
        except Exception as exc:
            failed_count += 1
            print(f"  [WARN] failed: {exc}")

    print("-" * 72)
    print(f"Done. success={success_count}, skipped={skipped_count}, failed={failed_count}")
    print(f"PLY output dir: {config.PLY_DIR}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
