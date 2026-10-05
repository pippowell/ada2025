#!/usr/bin/env python3
"""Minimal example: build the 3D point cloud yourself from the camera
intrinsics/extrinsics, then show it coloured by NIR intensity, RGB and thermal.

Loads W1_J5 2025_08_21 15 (depth/RGB frame 7, thermal frame 2 — the same
frames 3d_viewer.py shows by default) and opens three Open3D windows, one
after the other (close a window to open the next; each is centred on screen):
    1. points coloured by NIR intensity
    2. points coloured by the RGB camera image
    3. points coloured by the thermal (FLIR) camera image

The geometry (all frames documented in ``docs/camera_calibration.md``):

    depth pixel -> 3D point (depth camera frame), using the depth intrinsics:
        x = (u - cx) * z / fx ,   y = (v - cy) * z / fy

    depth point -> sensor frame, using the extrinsic:
        p_sensor = T_depth_to_<sensor> @ [x, y, z, 1]

    sensor point -> pixel, using the sensor intrinsics:
        u = fx * X/Z + cx ,   v = fy * Y/Z + cy

    RGB and thermal colours are then read at those pixels.

The images are undistorted first with ``cv2.undistort`` and the per-sensor
distortion coefficients, and the fixed extrinsics are corrected for the
per-frame robot position (``frames_*.csv``), exactly as in 3d_viewer.py.

"""

import argparse
import csv
from pathlib import Path

import cv2
import matplotlib  # only for the colormaps (Open3D 0.20 ships no colormap module)
import numpy as np
import open3d as o3d
import tifffile
from open3d.visualization import gui, rendering
from scipy.spatial import cKDTree

from camera_calibration import (
    K_DEPTH, K_RGB, K_FLIR,           # intrinsics (pinhole camera matrices)
    DEPTH_DISTORTION, RGB_DISTORTION, FLIR_DISTORTION,  # lens distortion (OpenCV 5-param)
    T_DEPTH_TO_RGB, T_DEPTH_TO_FLIR,  # extrinsics (depth -> sensor)
    robot_to_depth_position,          # robot-frame position -> depth camera frame
    shift_matrix,                     # position -> 4x4 translation
)

# Frames match 3d_viewer.py's defaults so both tools show the same capture.
PLANT, DATE, HOUR = "W1_J5", "2025_08_21", "15"
DEPTH_FRAME, RGB_FRAME, THERMAL_FRAME = 7, 7, 2
_parser = argparse.ArgumentParser(description="Minimal depth+RGB+thermal fusion example.")
_parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[2] / "data"),
                     help="Dataset folder holding depth/ rgb/ thermal/ (default: the repo's data/ folder)")
DATA_DIR = Path(_parser.parse_args().data_dir)
BASE = f"{DATA_DIR.as_posix()}/{{mod}}/{PLANT}/{DATE}/{HOUR}"
depth_path = BASE.format(mod="depth") + f"/depth_{DEPTH_FRAME:03d}.tif"
rgb_path = BASE.format(mod="rgb") + f"/rgb_{RGB_FRAME:03d}.jpg"
thermal_path = BASE.format(mod="thermal") + f"/thermal_{THERMAL_FRAME:03d}.tif"

# ---------------------------------------------------------------------------
# Load the three images (depth/intensity/thermal are stored rotated 180 deg)
# and remove their lens distortion with the per-sensor coefficients.
# ---------------------------------------------------------------------------
with tifffile.TiffFile(depth_path) as tif:
    depth = np.rot90(np.rot90(tif.pages[0].asarray())).astype(np.float32)
    intensity = np.rot90(np.rot90(tif.pages[1].asarray())).astype(np.float32)
rgb = cv2.cvtColor(cv2.imread(rgb_path), cv2.COLOR_BGR2RGB)
thermal = np.rot90(np.rot90(tifffile.imread(thermal_path))).astype(np.float32)

depth = cv2.undistort(depth, K_DEPTH, DEPTH_DISTORTION)
rgb = cv2.undistort(rgb, K_RGB, RGB_DISTORTION)
thermal = cv2.undistort(thermal, K_FLIR, FLIR_DISTORTION)
# The intensity page shares the depth camera's optics but is not rectified;
# it is sampled at the same (u, v) pixels as the undistorted depth.

# ---------------------------------------------------------------------------
# Per-frame extrinsics.  The robot moves along the row while capturing, so the
# depth/RGB/thermal frames of the same run were taken at slightly different
# positions.  The robot position of each frame (frames_*.csv) is used to shift
# the fixed extrinsics so that all sensors agree on the same scene points.
# ---------------------------------------------------------------------------
def read_frames(modality):
    with open(BASE.format(mod=modality) + f"/frames_{modality}.csv") as f:
        return list(csv.DictReader(f))


def robot_position(row):
    return np.array([float(row["position_robot_x"]), float(row["position_robot_y"]), float(row["position_robot_z"])])


depth_rows, rgb_rows, thermal_rows = read_frames("depth"), read_frames("rgb"), read_frames("thermal")
pos_d = robot_to_depth_position(robot_position(depth_rows[DEPTH_FRAME]))
pos_r = robot_to_depth_position(robot_position(rgb_rows[RGB_FRAME]))
pos_t = robot_to_depth_position(robot_position(thermal_rows[THERMAL_FRAME]))

T_depth_to_rgb = T_DEPTH_TO_RGB @ np.linalg.inv(shift_matrix(pos_r)) @ shift_matrix(pos_d)
T_depth_to_flir = T_DEPTH_TO_FLIR @ np.linalg.inv(shift_matrix(pos_t)) @ shift_matrix(pos_d)

h, w = depth.shape
u, v = np.meshgrid(np.arange(w), np.arange(h))
# The camera is ~0.3-0.7 m above the plant, so depth readings beyond a couple
# of metres are sensor noise (isolated pixels) and must be discarded.
MAX_DEPTH_M = 2.0
valid = (depth > 0) & (depth < MAX_DEPTH_M) & np.isfinite(depth)

# ---------------------------------------------------------------------------
# 1) depth pixel -> 3D point, using the depth intrinsics
# ---------------------------------------------------------------------------
fx, fy = K_DEPTH[0, 0], K_DEPTH[1, 1]
cx, cy = K_DEPTH[0, 2], K_DEPTH[1, 2]
x = (u - cx) * depth / fx
y = (v - cy) * depth / fy
pts = np.stack([x, y, depth], axis=-1)  # (H, W, 3) in the depth camera frame


def project(points3, T_ext, K):
    """Depth-frame (H,W,3) points -> sensor pixel coordinates.

    p = T_ext @ [x, y, z, 1]   (extrinsic into the sensor frame)
    u = fx*X/Z + cx, v = fy*Y/Z + cy   (intrinsic pinhole projection)
    Returns (u, v, Z) arrays of shape (H, W).
    """
    hom = np.concatenate([points3, np.ones(points3.shape[:2] + (1,))], axis=-1)
    p = hom @ T_ext.T  # p = T_ext @ [x,y,z,1]
    zc = np.where(p[..., 2] > 0, p[..., 2], 1.0)  # avoid division by zero
    uu = K[0, 0] * p[..., 0] / zc + K[0, 2]
    vv = K[1, 1] * p[..., 1] / zc + K[1, 2]
    return uu, vv, p[..., 2]


def sample_image(img, uu, vv, ok):
    """Nearest-neighbour colour/temperature sampling at the projected pixels."""
    H, W = img.shape[:2]
    ui = np.clip(np.round(np.nan_to_num(uu)).astype(int), 0, W - 1)
    vi = np.clip(np.round(np.nan_to_num(vv)).astype(int), 0, H - 1)
    out = img[vi, ui].copy()
    out[~ok] = 0
    return out


# ---------------------------------------------------------------------------
# 2) project the points into the RGB and thermal cameras and sample colours
# ---------------------------------------------------------------------------
u_rgb, v_rgb, z_rgb = project(pts, T_depth_to_rgb, K_RGB)
u_th, v_th, z_th = project(pts, T_depth_to_flir, K_FLIR)

rgb_ok = valid & (z_rgb > 0) & (u_rgb >= 0) & (u_rgb < rgb.shape[1]) & (v_rgb >= 0) & (v_rgb < rgb.shape[0])
th_ok = valid & (z_th > 0) & (u_th >= 0) & (u_th < thermal.shape[1]) & (v_th >= 0) & (v_th < thermal.shape[0])

colors = sample_image(rgb, u_rgb, v_rgb, rgb_ok)
temps = sample_image(thermal, u_th, v_th, th_ok).astype(np.float32)
temps[temps <= 0] = np.nan  # out-of-frame thermal pixels are background

# Flatten to the valid depth pixels (one row per 3D point)
points = pts[valid]
colors = colors[valid].astype(float) / 255.0
intensity_vals = intensity[valid]
temps = temps[valid]

# ---------------------------------------------------------------------------
# 3) clean the cloud with Open3D filters
# ---------------------------------------------------------------------------
orig_points = points.copy()

pcd = o3d.geometry.PointCloud()
pcd.points = o3d.utility.Vector3dVector(points)

# Voxel grid filter: average points per 1 mm voxel (removes duplicated points).
pcd = pcd.voxel_down_sample(voxel_size=0.001)

# Statistical outlier removal: drop points whose neighbours are spread
# further than the mean + 2*std.
pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
points = np.asarray(pcd.points)

# The filters removed some points; map each kept point back to its nearest
# original point so the per-point RGB/intensity/thermal stay aligned.
_, nearest = cKDTree(orig_points).query(points, k=1)
colors = colors[nearest]
intensity_vals = intensity_vals[nearest]
temps = temps[nearest]

# ---------------------------------------------------------------------------
# 4) show the three coloured point clouds (one Open3D window after the other)
# ---------------------------------------------------------------------------
def centered_xy(width, height):
    """Screen-centre coordinates for a window; (-1, -1) = OS default if unknown."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.update()
        w, h = int(root.winfo_screenwidth()), int(root.winfo_screenheight())
        root.destroy()
        return max(0, (w - width) // 2), max(0, (h - height) // 2)
    except Exception:
        return -1, -1


def show(title, colors):
    """Show the points in one centred Open3D window (blocks until it closes)."""
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd.colors = o3d.utility.Vector3dVector(colors)

    app = gui.Application.instance
    app.initialize()
    width, height = 1000, 760
    x, y = centered_xy(width, height)
    window = app.create_window(
        f"{PLANT} {DATE}/{HOUR} (d{DEPTH_FRAME}/r{RGB_FRAME}/t{THERMAL_FRAME}) - {title}", width, height, x, y
    )

    scene = gui.SceneWidget()
    scene.scene = rendering.Open3DScene(window.renderer)
    window.add_child(scene)

    material = rendering.MaterialRecord()
    material.shader = "defaultUnlit"
    material.point_size = 5
    scene.scene.add_geometry("cloud", pcd, material)
    bounds = scene.scene.bounding_box
    scene.setup_camera(60, bounds, bounds.get_center())

    app.run()  # blocks until the window is closed


def colormap_colors(values, cmap):
    """Map scalar values to (N,3) float colours in [0, 1] via a matplotlib cmap."""
    vmin, vmax = np.nanmin(values), np.nanmax(values)
    norm = np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)
    return matplotlib.colormaps.get_cmap(cmap)(norm)[:, :3].astype(np.float32)


# RGB: points outside the RGB field of view have no colour -> light grey
rgb_view = colors.copy()
rgb_view[np.all(colors == 0.0, axis=1)] = (0.8, 0.8, 0.8)

# Thermal: points without a temperature sample are grey, the rest use inferno.
th_view = colormap_colors(temps, "inferno")
th_view[~np.isfinite(temps)] = (0.8, 0.8, 0.8)

# 1) NIR intensity
show("NIR intensity", colormap_colors(intensity_vals, "viridis"))

# 2) RGB
show("RGB", rgb_view)

# 3) thermal
show("thermal (°C)", th_view)