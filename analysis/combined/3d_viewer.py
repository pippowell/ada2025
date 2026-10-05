#!/usr/bin/env python3
"""Interactive 3D point-cloud viewer for one ADA 2025 plant/date/hour.

Builds a 3D point cloud from the depth camera, colorizes each point with the
synchronized RGB and thermal (FLIR) images, and shows it in an Open3D GUI
window.  The point-cloud construction mirrors ``dataset-checker-v2``'s
``app/pointcloud.py`` — the step-by-step camera math is the part worth copying:

  * every depth pixel is unprojected with the depth intrinsics ``K_depth``
    (``x = (u - cx) * z / fx``, ``y = (v - cy) * z / fy``),
  * the per-frame robot position from ``frames_*.csv`` builds shift matrices
    that correct the fixed ``depth -> RGB`` / ``depth -> FLIR`` extrinsics for
    the exact capture pose,
  * undistorted depth is projected into the RGB and FLIR cameras
    (``p = T @ [x, y, z, 1]`` followed by pinhole projection) so that each
    depth pixel can sample the RGB color and the temperature of the same scene
    point,
  * the depth page (``depth_NNN.tif`` page 0) yields the 3D points and the
    second page the NIR intensity.

A full reference of the camera calibration (intrinsics, extrinsics, frames,
per-frame shift and the projection math) is in ``docs/camera_calibration.md``;
all numbers live in ``analysis/plant_height/config.py``.

The viewer GUI lets you switch how the points are colored (RGB, height,
intensity, thermal), pick the colormap for the scalar modes, toggle the
sensor frustums (RGB / depth / thermal — the HSI camera has no calibrated
extrinsics in this dataset), change the point size, and save a snapshot.

Usage:
    python 3d_viewer.py                                # interactive selection
    python 3d_viewer.py --plant-id W1_A2 --date 2025_09_01 --hour 15
    python 3d_viewer.py --plant-id W1_A2 --date 2025_09_01 --hour 15 --check

Keys in the window:
    1..4     color mode (RGB, height, intensity, thermal)
    R        cycle colormap
    F        toggle sensor frustums
    [  ]     decrease / increase point size
    S        save screenshot (or colored .ply snapshot)

Requires the relevant modality data to be unzipped under ``data/`` (see
``utils/unzip_data.py``). Requires ``open3d`` (see ``environment/*``).

Dataset layout:
    data/<modality>/<plant>/<date>/<hour>/

"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np
import tifffile
from plyfile import PlyData, PlyElement

from camera_calibration import (  # noqa: E402
    K_DEPTH,
    DEPTH_DISTORTION,
    K_RGB,
    RGB_DISTORTION,
    K_FLIR,
    FLIR_DISTORTION,
    T_DEPTH_TO_RGB,
    T_DEPTH_TO_FLIR,
    R_ROBOT_TO_DEPTH,
    robot_to_depth_position,
    shift_matrix,
    DEPTH_W,
    DEPTH_H,
    RGB_W,
    RGB_H,
    FLIR_W,
    FLIR_H,
)

import open3d as o3d  # noqa: E402
from open3d.visualization import gui, rendering  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_DEPTH_FRAME = 7
DEFAULT_RGB_FRAME = 7
DEFAULT_THERMAL_FRAME = 2

# ---------------------------------------------------------------------------
# Camera calibration
# ---------------------------------------------------------------------------
# All intrinsics, distortion coefficients, extrinsics and the transform
# helpers live in ``analysis/combined/camera_calibration.py`` (single source
# of truth; validated by ``align_test.py``).  The coordinate frames, the
# robot-shift composition and the projection math are documented in
# ``docs/camera_calibration.md``.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Image loading (units + orientation are part of the dataset convention)
# ---------------------------------------------------------------------------

def load_rgb(path: Path) -> np.ndarray | None:
    """Load a JPEG as RGB uint8 (OpenCV reads BGR)."""
    if not path.exists():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def load_depth(path: Path) -> tuple[np.ndarray, np.ndarray | None, bool]:
    """Load a depth TIF: page 0 = depth (m), page 1 = NIR intensity.

    Both pages are stored rotated by 180 degrees relative to the other
    cameras, so they are rotated back here.
    """
    if not path.exists():
        return None, None, True
    with tifffile.TiffFile(path) as tif:
        depth = np.rot90(np.rot90(tif.pages[0].asarray())).astype(np.float32)
        if depth.ndim != 2:
            return None, None, True
        # Pico Flexx stores a signed depth (positive = closer); flip if negative.
        if np.nanmedian(depth) < 0:
            depth = -depth
        depth[depth <= 0] = 0
        intensity = None
        if len(tif.pages) > 1:
            intensity = np.rot90(np.rot90(tif.pages[1].asarray())).astype(np.float32)
    return depth, intensity, False


def load_thermal(path: Path) -> tuple[np.ndarray | None, bool]:
    """Load a FLIR TIF as a 2D temperature array (rotated to match depth)."""
    if not path.exists():
        return None, True
    thermal = tifffile.imread(str(path)).astype(np.float32)
    thermal = np.rot90(np.rot90(thermal))
    return thermal, False


def read_frames_csv(path: Path) -> list[dict]:
    """Read per-frame metadata (robot positions) for one hour and sensor."""
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def robot_position(row: dict) -> np.ndarray:
    return np.array(
        [float(row["position_robot_x"]), float(row["position_robot_y"]), float(row["position_robot_z"])]
    )


# ---------------------------------------------------------------------------
# Point cloud construction
# ---------------------------------------------------------------------------

def fill_depth_holes(depth_img: np.ndarray, max_gap_pixels: int = 5) -> np.ndarray:
    """Interpolate small invalid regions (zeros) in the depth image."""
    valid = (depth_img > 0) & ~np.isnan(depth_img)
    if np.all(valid):
        return depth_img.copy()

    filled = depth_img.copy()
    filled[~valid] = 0

    kernel_size = 2 * max_gap_pixels + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    closed_mask = cv2.morphologyEx(valid.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    new_pixels = closed_mask & ~valid
    if not np.any(new_pixels):
        return filled

    valid_or_new = (valid | new_pixels).astype(float)
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
                np.roll(valid_or_new, 1, axis=0),
                np.roll(valid_or_new, -1, axis=0),
                np.roll(valid_or_new, 1, axis=1),
                np.roll(valid_or_new, -1, axis=1),
            ]
        )
        neighbor_sum = np.sum(shifted, axis=0)
        neighbor_count = np.sum(counts, axis=0)
        fill_mask = prev_invalid & closed_mask & (neighbor_count > 0)
        filled[fill_mask] = neighbor_sum[fill_mask] / neighbor_count[fill_mask]
        valid = filled > 0
        valid_or_new = (valid | new_pixels).astype(float)

    return filled


def colorize_depth_with_thermal(
    depth_img: np.ndarray,
    rgb_img: np.ndarray,
    flir_img: np.ndarray,
    K_depth: np.ndarray,
    K_rgb: np.ndarray,
    K_flir: np.ndarray,
    T_depth_to_rgb: np.ndarray,
    T_depth_to_flir: np.ndarray,
    fill_depth_gaps: int = 5,
    interpolation: int = cv2.INTER_LINEAR,
) -> tuple[np.ndarray, np.ndarray]:
    """Project every depth pixel into the RGB and FLIR images.

    For each depth pixel the 3D point is computed in the depth camera frame,
    transformed with the (per-frame) extrinsic ``T_depth_to_<sensor>`` and
    projected with the sensor intrinsics.  The RGB/FLIR pixels under those
    projections are sampled back, so every depth pixel carries the color and
    temperature of the scene point it represents.

    Returns:
        colorized_rgb: (H, W, 3) uint8, RGB color per depth pixel (black where invalid).
        thermal_values: (H, W) float32, temperature per depth pixel (NaN where invalid).
    """
    h, w = depth_img.shape
    fx_d, fy_d = K_depth[0, 0], K_depth[1, 1]
    cx_d, cy_d = K_depth[0, 2], K_depth[1, 2]

    if fill_depth_gaps and fill_depth_gaps > 0:
        depth_img = fill_depth_holes(depth_img, max_gap_pixels=fill_depth_gaps)

    u, v = np.meshgrid(np.arange(w), np.arange(h))
    z = depth_img
    valid = (z > 0) & ~np.isnan(z)

    # Depth camera frame -> 3D points (pinhole unprojection).
    x = (u.astype(np.float32) - cx_d) * z / fx_d
    y = (v.astype(np.float32) - cy_d) * z / fy_d
    p_depth = np.stack([x, y, z, np.ones_like(z)], axis=0).reshape(4, -1)

    # Project into each sensor image plane.  A depth pixel is valid for a
    # sensor only if it lands in front of that camera (z_cam > 0) and inside
    # its image bounds; everything else is marked as an invalid sample.
    u_map: dict[str, np.ndarray] = {}
    v_map: dict[str, np.ndarray] = {}
    invalid_map: dict[str, np.ndarray] = {}
    for name, (K, T) in [("rgb", (K_rgb, T_depth_to_rgb)), ("flir", (K_flir, T_depth_to_flir))]:
        p = T @ p_depth
        z_cam = p[2].reshape(h, w)
        uu = (K[0, 0] * p[0].reshape(h, w) / np.where(z_cam > 0, z_cam, 1) + K[0, 2]).astype(np.float32)
        vv = (K[1, 1] * p[1].reshape(h, w) / np.where(z_cam > 0, z_cam, 1) + K[1, 2]).astype(np.float32)
        imw = rgb_img.shape[1] if name == "rgb" else flir_img.shape[1]
        imh = rgb_img.shape[0] if name == "rgb" else flir_img.shape[0]
        invalid = ~valid | (z_cam <= 0) | (uu < 0) | (uu >= imw) | (vv < 0) | (vv >= imh)
        uu[invalid] = -1.0
        vv[invalid] = -1.0
        u_map[name] = uu
        v_map[name] = vv
        invalid_map[name] = invalid

    colorized_rgb = cv2.remap(
        rgb_img, u_map["rgb"], v_map["rgb"], interpolation=interpolation,
        borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
    # A NaN border for the thermal image: depth pixels whose FLIR projection
    # lands on or just outside the image edge interpolate against NaN instead
    # of a constant 0, so they become invalid instead of dragging the thermal
    # colormap range down to ~0.
    flir_remapped = cv2.remap(
        flir_img, u_map["flir"], v_map["flir"], interpolation=interpolation,
        borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan,
    )
    thermal_values = flir_remapped.astype(np.float32)
    # Pixels that did not sample the FLIR image (background / out of field of
    # view) are NaN so the viewer can render them black instead of mapping
    # them into the temperature colormap.
    thermal_values[invalid_map["flir"]] = np.nan

    return colorized_rgb, thermal_values


def depth_image_to_point_cloud(
    depth_img: np.ndarray,
    colorized_rgb: np.ndarray,
    thermal_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Unproject the (filled) depth image into 3D points.

    Returns:
        (points (N,3) float32, colors (N,3) uint8, temperatures (N,) float32,
         rgb_valid (N,) bool, thermal_valid (N,) bool)
    """
    h, w = depth_img.shape
    fx, fy = K_DEPTH[0, 0], K_DEPTH[1, 1]
    cx, cy = K_DEPTH[0, 2], K_DEPTH[1, 2]

    u, v = np.meshgrid(np.arange(w), np.arange(h))
    z = depth_img
    valid = (z > 0) & ~np.isnan(z)
    has_rgb = np.any(colorized_rgb != 0, axis=2)
    has_temp = ~np.isnan(thermal_values)
    keep = valid & (has_rgb | has_temp)

    z_keep = z[keep]
    x = (u[keep].astype(np.float32) - cx) * z_keep / fx
    y = (v[keep].astype(np.float32) - cy) * z_keep / fy

    points = np.stack([x, y, z_keep], axis=1)
    return (
        points.astype(np.float32),
        colorized_rgb[keep],
        thermal_values[keep],
        has_rgb[keep],
        has_temp[keep],
    )


def modality_scan_root(data_dir: Path, modality: str, plant: str) -> Path:
    """Directory that contains the date folders for a plant + modality:
    data/<modality>/<plant>/<date>/<hour>/
    """
    return data_dir / modality / plant


def build_point_cloud(
    plant: str,
    date: str,
    hour: str,
    data_dir: Path,
    depth_frame: int = DEFAULT_DEPTH_FRAME,
    rgb_frame: int = DEFAULT_RGB_FRAME,
    thermal_frame: int = DEFAULT_THERMAL_FRAME,
) -> dict | None:
    """Build the 3D cloud + per-point attributes for one plant/date/hour.

    Returns None if the depth frame is unavailable.

    The returned dict contains:
        points      (N,3) float32  — XYZ in the depth-camera frame
        colors      (N,3) uint8    — RGB color of each point
        temperature (N,) float32   — thermal value of each point (NaN if absent)
        intensity   (N,) float32   — NIR intensity of each point (or None)
        T_depth_to_rgb, T_depth_to_flir  — per-frame corrected extrinsics
    """
    rgb_dir = modality_scan_root(data_dir, "rgb", plant) / date / hour
    depth_dir = modality_scan_root(data_dir, "depth", plant) / date / hour
    thermal_dir = modality_scan_root(data_dir, "thermal", plant) / date / hour

    depth_path = depth_dir / f"depth_{depth_frame:03d}.tif"
    rgb_path = rgb_dir / f"rgb_{rgb_frame:03d}.jpg"
    thermal_path = thermal_dir / f"thermal_{thermal_frame:03d}.tif"

    depth_img, _, depth_missing = load_depth(depth_path)
    if depth_missing or depth_img is None:
        return None

    depth_rows = read_frames_csv(depth_dir / "frames_depth.csv")
    rgb_rows = read_frames_csv(rgb_dir / "frames_rgb.csv")
    thermal_rows = read_frames_csv(thermal_dir / "frames_thermal.csv")

    # The robot moves while capturing, so every frame has its own pose. The
    # per-frame pose difference is applied as an offset to the fixed extrinsic
    # so that depth, RGB and thermal of the *same* scene point align.
    if depth_frame < len(depth_rows) and rgb_frame < len(rgb_rows):
        pos_depth = robot_to_depth_position(robot_position(depth_rows[depth_frame]))
        pos_rgb = robot_to_depth_position(robot_position(rgb_rows[rgb_frame]))
        T_shift_depth = shift_matrix(pos_depth)
        T_shift_rgb = shift_matrix(pos_rgb)
        T_depth_to_rgb_shifted = T_DEPTH_TO_RGB @ np.linalg.inv(T_shift_rgb) @ T_shift_depth
    else:
        T_depth_to_rgb_shifted = T_DEPTH_TO_RGB

    if thermal_frame < len(thermal_rows):
        pos_flir = robot_to_depth_position(robot_position(thermal_rows[thermal_frame]))
        T_shift_flir = shift_matrix(pos_flir)
        T_depth_to_flir_shifted = T_DEPTH_TO_FLIR @ np.linalg.inv(T_shift_flir) @ T_shift_depth
    else:
        T_depth_to_flir_shifted = T_DEPTH_TO_FLIR

    depth_undist = cv2.undistort(depth_img, K_DEPTH, DEPTH_DISTORTION)

    rgb_np = load_rgb(rgb_path)
    if rgb_np is None:
        rgb_np = np.zeros((RGB_H, RGB_W, 3), dtype=np.uint8)

    thermal_raw, t_missing = load_thermal(thermal_path)
    thermal_for_color = thermal_raw if (thermal_raw is not None and not t_missing) else np.zeros(
        (FLIR_H, FLIR_W), dtype=np.float32
    )

    rgb_undist = cv2.undistort(rgb_np, K_RGB, RGB_DISTORTION)
    # Rectify the FLIR image with a NaN border: pixels that fall outside the
    # original (distorted) image become NaN instead of 0, and the interpolated
    # pixels next to them propagate NaN.  The raw FLIR never contains ~0 values
    # (temperatures are >> 0), so a 0-filled rectified border would otherwise
    # drag the thermal colormap range down to ~0.
    flir_map1, flir_map2 = cv2.initUndistortRectifyMap(
        K_FLIR, FLIR_DISTORTION, None, K_FLIR, (FLIR_W, FLIR_H), cv2.CV_32FC1
    )
    flir_undist = cv2.remap(thermal_for_color, flir_map1, flir_map2, cv2.INTER_LINEAR, borderValue=np.nan)

    colorized_rgb, thermal_values = colorize_depth_with_thermal(
        depth_undist,
        rgb_undist,
        flir_undist,
        K_DEPTH, K_RGB, K_FLIR,
        T_depth_to_rgb_shifted,
        T_depth_to_flir_shifted,
    )
    if t_missing or thermal_raw is None:
        thermal_values = np.full(depth_undist.shape, np.nan)

    points, colors, temperatures, rgb_valid, thermal_valid = depth_image_to_point_cloud(
        depth_undist, colorized_rgb, thermal_values
    )

    intensity_values = None
    _, intensity_raw, _ = load_depth(depth_path)
    if intensity_raw is not None:
        # Same keep mask as depth_image_to_point_cloud so the intensity array
        # is aligned with the points.
        keep = (depth_undist > 0) & (
            np.any(colorized_rgb != 0, axis=2) | ~np.isnan(thermal_values)
        )
        intensity_values = intensity_raw[keep]

    return {
        "points": points.astype(np.float32),
        "colors": colors.astype(np.uint8),
        "rgb_valid": rgb_valid.astype(bool),
        "temperature": temperatures.astype(np.float32),  # NaN where no thermal sample
        "intensity": intensity_values.astype(np.float32) if intensity_values is not None else None,
        "T_depth_to_rgb": T_depth_to_rgb_shifted,
        "T_depth_to_flir": T_depth_to_flir_shifted,
    }


# ---------------------------------------------------------------------------
# Point coloring (color mode + colormap) and sensor frustums
# ---------------------------------------------------------------------------

COLORMAPS = ["viridis", "plasma", "inferno", "gray", "coolwarm"]
COLOR_MODES = ["rgb", "height", "intensity", "thermal"]
MODE_LABELS = {"rgb": "RGB", "height": "height", "intensity": "intensity", "thermal": "thermal"}


def _scalar_colors(values: np.ndarray, cmap_name: str, vmin: float, vmax: float) -> np.ndarray:
    """Map scalar values to (N,3) float colors in [0,1]."""
    import matplotlib

    cmap = matplotlib.colormaps.get_cmap(cmap_name)
    norm = np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)
    return cmap(norm)[:, :3].astype(np.float32)


def point_colors(cloud: dict, mode: str, cmap_name: str) -> tuple[np.ndarray, tuple[float, float]]:
    """Compute (N,3) float32 vertex colors and the (vmin, vmax) range shown."""
    if mode == "rgb":
        return cloud["colors"].astype(np.float32) / 255.0, (0.0, 1.0)

    if mode == "height":
        values = cloud["points"][:, 2].astype(np.float32)
        lo, hi = np.nanpercentile(values, [1, 99])
    elif mode == "intensity":
        values = cloud["intensity"]
        if values is None:
            values = cloud["points"][:, 2].astype(np.float32)
        lo, hi = np.nanpercentile(values, [1, 99])
    else:  # thermal — full min/max range, invalid (background) pixels black
        values = cloud["temperature"]
        valid = np.isfinite(values)
        if np.any(valid):
            lo = float(np.min(values[valid]))
            hi = float(np.max(values[valid]))
        else:
            lo, hi = 0.0, 1.0
        if hi - lo < 1e-6:
            hi = lo + 1e-3
        colors = _scalar_colors(values, cmap_name, lo, hi)
        colors[~np.isfinite(values)] = (0.0, 0.0, 0.0)
        return colors, (lo, hi)

    if hi - lo < 1e-6:
        hi = lo + 1e-3
    return _scalar_colors(values, cmap_name, lo, hi), (float(lo), float(hi))


def save_point_cloud_ply(cloud: dict, out_path: Path) -> Path:
    """Write the point cloud as a PLY with per-point RGB, intensity, thermal.

    RGB is stored as standard uint8 0-255 ``red/green/blue`` (0/black where a
    point has no colour sample, since uchar cannot store NaN).  Intensity and
    thermal are float and use NaN where no value exists.  The PLY is binary.
    """
    n = len(cloud["points"])
    rgb = cloud["colors"].astype(np.float64)
    rgb_valid = cloud.get("rgb_valid")
    if rgb_valid is None:
        rgb_valid = np.any(rgb != 0, axis=1)
    else:
        rgb_valid = rgb_valid.astype(bool)

    intensity = cloud.get("intensity")
    if intensity is None:
        intensity = np.full(n, np.nan, dtype=np.float64)
    intensity = np.where(np.isfinite(intensity), intensity, np.nan).astype(np.float64)

    thermal = cloud.get("temperature", np.full(n, np.nan))
    thermal = np.where(np.isfinite(thermal), thermal, np.nan).astype(np.float64)

    rec = np.empty(n, dtype=[
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("red", "u1"), ("green", "u1"), ("blue", "u1"),
        ("intensity", "f8"), ("thermal", "f8"),
    ])
    rec["x"], rec["y"], rec["z"] = cloud["points"][:, 0], cloud["points"][:, 1], cloud["points"][:, 2]
    # uint8 0-255 RGB (the standard PLY colour convention); points with no
    # colour sample are written as 0 (black) since uchar cannot store NaN.
    rec["red"] = np.where(rgb_valid, rgb[:, 0], 0).astype(np.uint8)
    rec["green"] = np.where(rgb_valid, rgb[:, 1], 0).astype(np.uint8)
    rec["blue"] = np.where(rgb_valid, rgb[:, 2], 0).astype(np.uint8)
    rec["intensity"] = intensity
    rec["thermal"] = thermal

    el = PlyElement.describe(rec, "vertex")
    PlyData([el], text=False).write(str(out_path))
    return out_path


# 180° rotation about the Y axis, applied to the displayed cloud and frustums
# so the plant matches the photo/RGB orientation (the checker app applies the
# same geo.rotateY(Math.PI)).  Only the viewer geometry is rotated; the raw
# cloud coordinates in ``cloud["points"]`` (and the saved PLY) are unchanged.
VIEW_YAW_ROTATION = np.array(
    [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]], dtype=np.float64
)


SENSOR_FRUSTS = [
    {"id": "depth", "K": K_DEPTH, "w": DEPTH_W, "h": DEPTH_H, "color": [0.17, 0.63, 0.17]},
    {"id": "rgb", "K": K_RGB, "w": RGB_W, "h": RGB_H, "color": [0.12, 0.47, 0.71]},
    {"id": "thermal", "K": K_FLIR, "w": FLIR_W, "h": FLIR_H, "color": [1.0, 0.5, 0.0]},
]


def frustum_lineset(T_cam_to_depth: np.ndarray, K: np.ndarray, w: int, h: int, color, z_near: float = 0.15, z_far: float = 1.0) -> o3d.geometry.LineSet:
    """Build the viewing frustum of a sensor as an Open3D LineSet.

    The frustum is the pyramid from the camera origin through the four image
    corners at near/far distance, transformed into the depth-camera frame with
    ``T_cam_to_depth`` (the inverse of the depth->sensor extrinsic).
    """
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    corners = np.array([(0, 0), (w, 0), (w, h), (0, h)], dtype=np.float32)
    dirs = np.stack([(corners[:, 0] - cx) / fx, (corners[:, 1] - cy) / fy, np.ones(4)], axis=1)
    near = dirs * z_near
    far = dirs * z_far

    def to_depth(points3d):
        hom = np.concatenate([points3d, np.ones((points3d.shape[0], 1))], axis=1)
        return (T_cam_to_depth @ hom.T).T[:, :3]

    pts = np.concatenate([to_depth(near), to_depth(far)], axis=0)
    lines = np.array(
        [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)],
        dtype=np.int32,
    )
    ls = o3d.geometry.LineSet()
    ls.points = o3d.utility.Vector3dVector(pts)
    ls.lines = o3d.utility.Vector2iVector(lines)
    ls.paint_uniform_color(color)
    return ls


def build_frustums(cloud: dict) -> list[tuple[str, o3d.geometry.LineSet]]:
    """All sensor frustums (RGB / depth / thermal) in the depth-camera frame.

    The frustums are rotated with the same 180° Y-rotation as the displayed
    cloud so they stay aligned with it.
    """
    out = []
    for spec in SENSOR_FRUSTS:
        if spec["id"] == "depth":
            T = np.eye(4)
        elif spec["id"] == "rgb":
            T = np.linalg.inv(cloud["T_depth_to_rgb"])
        else:
            T = np.linalg.inv(cloud["T_depth_to_flir"])
        ls = frustum_lineset(T, spec["K"], spec["w"], spec["h"], spec["color"])
        pts = np.asarray(ls.points) @ VIEW_YAW_ROTATION.T
        ls.points = o3d.utility.Vector3dVector(pts)
        out.append((f"frustum_{spec['id']}", ls))
    return out


# ---------------------------------------------------------------------------
# Colormap/colorbar rendering (matplotlib -> Open3D image)
# ---------------------------------------------------------------------------

def render_colorbar(cmap_name: str, vmin: float, vmax: float, label: str, width: int = 264, height: int = 60) -> np.ndarray:
    """Render a horizontal colorbar (with min/max labels) as an RGB image."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable

    fig = plt.figure(figsize=(width / 80.0, height / 80.0), dpi=80)
    ax = fig.add_axes([0.04, 0.5, 0.92, 0.28])
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    fig.colorbar(ScalarMappable(norm=norm, cmap=matplotlib.colormaps.get_cmap(cmap_name)), cax=ax, orientation="horizontal")
    ax.set_title(f"{label}: {vmin:.2f} .. {vmax:.2f}", fontsize=7, pad=3)
    fig.canvas.draw()
    img = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
    plt.close(fig)
    return np.ascontiguousarray(img)


# ---------------------------------------------------------------------------
# Open3D GUI
# ---------------------------------------------------------------------------

class ViewerWindow:
    """Open3D window with a 3D point cloud and a color control panel."""

    def __init__(self, cloud: dict, plant: str, date: str, hour: str):
        self.cloud = cloud
        self.plant = plant
        self.date = date
        self.hour = hour
        self.mode = "rgb"
        self.cmap = "viridis"
        self.point_size = 4
        self.show_frustums = False

        app = gui.Application.instance
        win_w, win_h = 1280, 800
        x, y = centered_xy(win_w, win_h)
        self.window = app.create_window(
            f"3D viewer — {plant} {date}/{hour}", win_w, win_h, x, y
        )
        self.window.set_on_key(self.on_key)

        self.scene_widget = gui.SceneWidget()
        self.scene_widget.scene = rendering.Open3DScene(self.window.renderer)
        self.window.add_child(self.scene_widget)

        self.material = rendering.MaterialRecord()
        self.material.shader = "defaultUnlit"
        self.material.point_size = self.point_size

        self.pcd = o3d.geometry.PointCloud()
        # Rotate the view geometry 180° about Y so it matches the photo/RGB
        # orientation (see VIEW_YAW_ROTATION).  The raw cloud coordinates are
        # unchanged, so the height coloring and the saved PLY are unaffected.
        view_pts = np.asarray(self.cloud["points"]) @ VIEW_YAW_ROTATION.T
        self.pcd.points = o3d.utility.Vector3dVector(view_pts)

        self.frustum_sets: list[tuple[str, o3d.geometry.LineSet]] = []

        self._build_panel()
        self._apply_colors()  # adds "cloud" to the scene

        bounds = self.scene_widget.scene.bounding_box
        self.scene_widget.setup_camera(60, bounds, bounds.get_center())
        self.window.set_on_layout(self._on_layout)

    # ---- GUI layout -----------------------------------------------------

    def _build_panel(self):
        em = 10
        panel = gui.Vert(0, gui.Margins(8, 8, 8, 8))
        panel.add_child(gui.Label("Color mode"))
        self.mode_combo = gui.Combobox()
        for m in COLOR_MODES:
            self.mode_combo.add_item(m)
        self.mode_combo.selected_index = COLOR_MODES.index("rgb")
        self.mode_combo.set_on_selection_changed(self._on_mode)
        panel.add_child(self.mode_combo)

        panel.add_child(gui.Label("Colormap (scalar modes)"))
        self.cmap_combo = gui.Combobox()
        for c in COLORMAPS:
            self.cmap_combo.add_item(c)
        self.cmap_combo.selected_index = COLORMAPS.index("viridis")
        self.cmap_combo.set_on_selection_changed(self._on_cmap)
        panel.add_child(self.cmap_combo)

        self.frustum_cb = gui.Checkbox("show sensor frustums")
        self.frustum_cb.checked = False
        self.frustum_cb.set_on_checked(self._on_frustum)
        panel.add_child(self.frustum_cb)

        panel.add_child(gui.Label("point size"))
        self.size_slider = gui.Slider(gui.Slider.INT)
        self.size_slider.set_limits(1, 30)
        self.size_slider.int_value = self.point_size
        self.size_slider.set_on_value_changed(self._on_point_size)
        panel.add_child(self.size_slider)

        self.info = gui.Label("")
        panel.add_child(self.info)

        self.colorbar = gui.ImageWidget()
        panel.add_child(self.colorbar)

        self.save_btn = gui.Button("Save PLY")
        self.save_btn.set_on_clicked(self._save_ply)
        panel.add_child(self.save_btn)

        hints = gui.Label(
            "Keys: 1-4 mode | R colormap | F frustum\n"
            "      [ ] size | S screenshot"
        )
        hints.text_color = gui.Color(0.6, 0.6, 0.6)
        panel.add_child(hints)

        self.panel = panel
        self.window.add_child(panel)

    def _on_layout(self, ctx):
        r = self.window.content_rect
        self.scene_widget.frame = r
        pw = 320
        self.panel.frame = gui.Rect(r.width - pw - 8, r.y + 8, pw, r.height - 16)

    # ---- color updates --------------------------------------------------

    def _apply_colors(self):
        colors, (lo, hi) = point_colors(self.cloud, self.mode, self.cmap)
        self.pcd.colors = o3d.utility.Vector3dVector(colors)
        # Remove+re-add the geometry so the renderer picks up the new colors.
        if self.scene_widget.scene.has_geometry("cloud"):
            self.scene_widget.scene.remove_geometry("cloud")
        self.scene_widget.scene.add_geometry("cloud", self.pcd, self.material)

        if self.mode == "rgb":
            self.colorbar.visible = False
        else:
            self.colorbar.visible = True
            img = o3d.geometry.Image(render_colorbar(self.cmap, lo, hi, MODE_LABELS[self.mode]))
            self.colorbar.update_image(img)

        self.info.text = (
            f"{len(self.cloud['points'])} points | mode {MODE_LABELS[self.mode]} | "
            f"cmap {self.cmap}"
        )

    def _on_mode(self, text, index):
        self.mode = text
        self._apply_colors()

    def _on_cmap(self, text, index):
        self.cmap = text
        self._apply_colors()

    def _on_frustum(self, checked):
        self.show_frustums = bool(checked)
        if self.show_frustums:
            if not self.frustum_sets:
                self.frustum_sets = build_frustums(self.cloud)
            line_material = rendering.MaterialRecord()
            line_material.shader = "defaultUnlit"
            for name, ls in self.frustum_sets:
                self.scene_widget.scene.add_geometry(name, ls, line_material)
        else:
            for name, _ in self.frustum_sets:
                self.scene_widget.scene.remove_geometry(name)

    def _on_point_size(self, value):
        self.point_size = int(value)
        self.material.point_size = self.point_size
        self._apply_colors()

    def _cycle_cmap(self):
        idx = COLORMAPS.index(self.cmap)
        nxt = COLORMAPS[(idx + 1) % len(COLORMAPS)]
        self.cmap_combo.selected_text = nxt
        self.cmap = nxt
        self._apply_colors()

    def _toggle_frustum(self):
        self.frustum_cb.checked = not self.frustum_cb.checked
        self._on_frustum(self.frustum_cb.checked)

    def _save_ply(self):
        """Open a native save dialog and export the point cloud as PLY.

        Uses the OS-native tkinter dialog (much more usable than Open3D's),
        with the Open3D dialog as a fallback when tkinter is unavailable.
        """
        default_dir = Path(__file__).resolve().parent / "processed"
        default_name = f"3d_viewer_{self.plant}_{self.date}_{self.hour}.ply"
        path = None
        tk_ok = True
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.update()
            path = filedialog.asksaveasfilename(
                title="Save PLY",
                defaultextension=".ply",
                initialdir=str(default_dir),
                initialfile=default_name,
                filetypes=[("Point cloud", "*.ply"), ("All files", "*.*")],
                parent=root,
            )
            root.destroy()
        except Exception:
            tk_ok = False

        if path:
            self._save_to(Path(path))
        elif not tk_ok:
            dlg = gui.FileDialog(gui.FileDialog.SAVE, "Save PLY", self.window.theme)
            dlg.add_filter(".ply", "Point cloud (.ply)")
            dlg.set_path(str(default_dir / default_name))
            dlg.set_on_cancel(lambda: self.window.close_dialog())
            dlg.set_on_done(self._on_save_path)
            self.window.show_dialog(dlg)

    def _on_save_path(self, path: str):
        """Open3D-dialog callback: save, then close the dialog."""
        try:
            self._save_to(Path(path))
        finally:
            self.window.close_dialog()

    def _save_to(self, out_path: Path):
        """Write the point cloud and update the status label."""
        try:
            saved = save_point_cloud_ply(self.cloud, out_path)
            print(f"PLY saved to {saved}")
            self.info.text = f"PLY saved: {saved.name}"
        except Exception as exc:  # pragma: no cover
            print(f"PLY export failed: {exc}")
            self.info.text = f"PLY export failed: {exc}"

    def _screenshot(self):
        out_dir = Path(__file__).resolve().parent / "processed"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Try to render an offscreen PNG (needs a working GL context).
        try:
            ren = rendering.OffscreenRenderer(1600, 1000)
            mat = rendering.MaterialRecord()
            mat.shader = "defaultUnlit"
            mat.point_size = self.point_size
            ren.scene.add_geometry("cloud", self.pcd, mat)
            ren.scene.set_background([0.1, 0.1, 0.1, 1.0])
            bounds = self.pcd.get_axis_aligned_bounding_box()
            center = bounds.get_center()
            extent = bounds.get_extent()
            eye = center + np.array([0.0, 0.0, max(extent) * 1.6])
            ren.setup_camera(60.0, center, eye, np.array([0.0, 1.0, 0.0]))
            img = ren.render_to_image()
            path = out_dir / f"3d_viewer_{self.mode}_{self.cmap}.png"
            o3d.io.write_image(str(path), img)
            print(f"screenshot saved to {path}")
        except Exception as exc:  # pragma: no cover - fallback
            print(f"offscreen render failed ({exc}); saving colored .ply instead")
            o3d.io.write_point_cloud(str(out_dir / f"3d_viewer_{self.mode}_{self.cmap}.ply"), self.pcd)

    # ---- keys ---------------------------------------------------------------

    def on_key(self, event) -> bool:
        if event.type != gui.KeyEvent.Type.DOWN:
            return False
        key = event.key
        mode_keys = {
            gui.KeyName.ONE: "rgb",
            gui.KeyName.TWO: "height",
            gui.KeyName.THREE: "intensity",
            gui.KeyName.FOUR: "thermal",
        }
        if key in mode_keys:
            self.mode_combo.selected_text = mode_keys[key]
            self.mode = mode_keys[key]
            self._apply_colors()
            return True
        if key == gui.KeyName.R:
            self._cycle_cmap()
            return True
        if key == gui.KeyName.F:
            self._toggle_frustum()
            return True
        if key == gui.KeyName.LEFT_BRACKET:
            self.size_slider.int_value = max(1, self.point_size - 1)
            return True
        if key == gui.KeyName.RIGHT_BRACKET:
            self.size_slider.int_value = min(30, self.point_size + 1)
            return True
        if key == gui.KeyName.S:
            self._screenshot()
            return True
        return False


# ---------------------------------------------------------------------------
# CLI / interactive selection
# ---------------------------------------------------------------------------

def pick_option(prompt: str, options: list[str]) -> str:
    """Interactively pick one option from a numbered list (or abort)."""
    if not options:
        sys.exit(f"no {prompt.lower()} available under the data dir")
    print(f"\nSelect {prompt}:")
    for i, opt in enumerate(options, 1):
        print(f"  [{i:>2}] {opt}")
    while True:
        raw = input(f"choose {prompt} (1-{len(options)}): ").strip()
        if raw.lower() in ("q", "quit"):
            sys.exit("aborted")
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print(f"  invalid '{raw}' — enter a number 1-{len(options)}")


def screen_size() -> tuple[int, int] | None:
    """Primary screen size in pixels, or None if it cannot be queried.

    Uses tkinter when available (bundled with most Python installs).
    """
    try:
        import tkinter

        root = tkinter.Tk()
        try:
            return int(root.winfo_screenwidth()), int(root.winfo_screenheight())
        finally:
            root.destroy()
    except Exception:
        return None


def centered_xy(width: int, height: int) -> tuple[int, int]:
    """Screen-centre coordinates for a window of the given size.

    Returns (-1, -1) (the window manager's default position) when the screen
    size cannot be queried.  Open3D places its windows at an OS default that
    can end up partially off-screen; passing an explicit centre keeps the
    viewer visible.
    """
    scr = screen_size()
    if scr is None:
        return -1, -1
    return max(0, (scr[0] - width) // 2), max(0, (scr[1] - height) // 2)


def _subdirs(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(p.name for p in path.iterdir() if p.is_dir())


def available_dates(data_dir: Path, plant: str) -> list[str]:
    rgb = _subdirs(modality_scan_root(data_dir, "rgb", plant))
    depth = _subdirs(modality_scan_root(data_dir, "depth", plant))
    return sorted(set(rgb) & set(depth))


def available_hours(data_dir: Path, plant: str, date: str) -> list[str]:
    rgb = _subdirs(modality_scan_root(data_dir, "rgb", plant) / date)
    depth = _subdirs(modality_scan_root(data_dir, "depth", plant) / date)
    return sorted(set(rgb) & set(depth))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Interactive 3D point-cloud viewer for one ADA 2025 plant/date/hour."
    )
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="dataset root (default: repo data/)")
    p.add_argument("--plant-id", default=None, help="full plant id, e.g. W1_A2")
    p.add_argument("--date", default=None, help="date YYYY_MM_DD")
    p.add_argument("--hour", default=None, help="hour HH (zero-padded)")
    p.add_argument("--depth-frame", type=int, default=DEFAULT_DEPTH_FRAME)
    p.add_argument("--rgb-frame", type=int, default=DEFAULT_RGB_FRAME)
    p.add_argument("--thermal-frame", type=int, default=DEFAULT_THERMAL_FRAME)
    p.add_argument("--check", action="store_true",
                   help="build the cloud and print statistics, then exit (no GUI)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        sys.exit(f"data dir not found: {data_dir}")

    plants = _subdirs(data_dir / "rgb")
    plant = args.plant_id or pick_option("plant", plants)
    if plant not in plants:
        sys.exit(f"plant '{plant}' not found under {data_dir / 'rgb'}")

    dates = available_dates(data_dir, plant)
    date = args.date or pick_option("date", dates)
    if date not in dates:
        sys.exit(f"date '{date}' not available for {plant}")

    hours = available_hours(data_dir, plant, date)
    hour = args.hour or pick_option("hour", hours)
    if hour not in hours:
        sys.exit(f"hour '{hour}' not available for {plant} {date}")

    cloud = build_point_cloud(
        plant, date, hour, data_dir,
        depth_frame=args.depth_frame,
        rgb_frame=args.rgb_frame,
        thermal_frame=args.thermal_frame,
    )
    if cloud is None:
        sys.exit(f"could not build the point cloud (missing depth?) for {plant} {date} {hour}")

    n = len(cloud["points"])
    print(f"cloud: {n} points | xyz range "
          f"x[{cloud['points'][:,0].min():.3f},{cloud['points'][:,0].max():.3f}] "
          f"y[{cloud['points'][:,1].min():.3f},{cloud['points'][:,1].max():.3f}] "
          f"z[{cloud['points'][:,2].min():.3f},{cloud['points'][:,2].max():.3f}]")
    if cloud["intensity"] is not None:
        print(f"intensity range [{cloud['intensity'].min():.1f}, {cloud['intensity'].max():.1f}]")
    temp = cloud["temperature"]
    finite = temp[np.isfinite(temp)]
    if finite.size:
        print(f"thermal range [{finite.min():.2f}, {finite.max():.2f}] °C")

    if args.check:
        return

    app = gui.Application.instance
    app.initialize()
    viewer = ViewerWindow(cloud, plant, date, hour)
    app.run()


if __name__ == "__main__":
    main()