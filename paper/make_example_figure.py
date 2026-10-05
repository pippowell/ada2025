"""
make_example_figure.py

Creates a 4-panel publication figure showing one representative image from
each modality in the ADA-2025 dataset: RGB, Thermal (JET), HSI NDVI, Depth.

Run with:
  python make_example_figure.py  (imagecreate env)

Developed with assistance from Claude (Anthropic) via Claude Code.
"""

import argparse
import json
from pathlib import Path

import os
import pathlib
import sys

# On Windows, openjp2.dll may not be on PATH unless the active conda env's
# Library\bin directory is included. Detect it dynamically from sys.prefix so
# this works in any conda environment (e.g. ada2025) without hardcoded paths.
if os.name == "nt":
    _openjpeg_bin = str(pathlib.Path(sys.prefix) / "Library" / "bin")
    if pathlib.Path(_openjpeg_bin).is_dir():
        os.environ["PATH"] = _openjpeg_bin + ";" + os.environ.get("PATH", "")
        _glymur_home = pathlib.Path.home() / ".glymur"
        _glymur_home.mkdir(exist_ok=True)
        (_glymur_home / "glymurrc").write_text(
            f"[library]\nopenjp2 = {_openjpeg_bin}\\openjp2.dll\n", encoding="utf-8"
        )

import glymur
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import tifffile
from PIL import Image

matplotlib.use("Agg")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_parser = argparse.ArgumentParser(description="Four-panel modality example figure.")
_parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"),
                     help="Dataset folder holding rgb/ thermal/ hsi/ depth/ (default: the repo's data/ folder)")
STAGED = Path(_parser.parse_args().data_dir)
PLANT = "W1_A2"
DATE = "2025_09_03"
HOUR = "15"
INNER = f"{PLANT}/{DATE}/{HOUR}"

RGB_PATH = STAGED / "rgb" / INNER / "rgb_007.jpg"
THERMAL_PATH = STAGED / "thermal" / INNER / "thermal_002.tif"
HSI_PATH = STAGED / "hsi" / INNER / "hsi_000.jp2"
HSI_JSON = STAGED / "hsi" / INNER / "hsi_000.json"
DEPTH_PATH = STAGED / "depth" / INNER / "depth_007.tif"

HERE = Path(__file__).parent
OUTPUT = HERE.parent / "analysis" / "figures" / "example_modalities.png"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Load ADA overview image and crop to 1:2 portrait (spans full figure height)
# ---------------------------------------------------------------------------
ada_raw = np.array(Image.open(HERE / "ada.jpg"))
ada_h, ada_w = ada_raw.shape[:2]         # 3072 x 4080
# ADA_RATIO controls how wide the left panel is relative to each square panel.
# 1.5 → 3:4 portrait (shows more of the scene width than the original 1:2).
ADA_RATIO = 2.0
ada_crop_w = int(ada_h * ADA_RATIO / 2)  # 2304 px
ada_c0 = (ada_w - ada_crop_w) // 2
ada = ada_raw[:, ada_c0:ada_c0 + ada_crop_w]
print(f"ADA  crop shape={ada.shape}")

# ---------------------------------------------------------------------------
# Load RGB
# ---------------------------------------------------------------------------
rgb = np.array(Image.open(RGB_PATH))
print(f"RGB  shape={rgb.shape}  dtype={rgb.dtype}")

# ---------------------------------------------------------------------------
# Load Thermal (values are in °C)
# ---------------------------------------------------------------------------
thermal = tifffile.imread(THERMAL_PATH).astype(np.float32)
print(f"Thermal  shape={thermal.shape}  min={thermal.min():.1f}  max={thermal.max():.1f}")

# ---------------------------------------------------------------------------
# Load HSI and compute NDVI
# ---------------------------------------------------------------------------
with open(HSI_JSON) as f:
    hsi_meta = json.load(f)

wavelengths = np.array(hsi_meta["wavelengths"])
red_idx = int(np.argmin(np.abs(wavelengths - 670)))  # ~670 nm
nir_idx = int(np.argmin(np.abs(wavelengths - 800)))  # ~800 nm
print(f"HSI  red band {red_idx} ({wavelengths[red_idx]:.1f} nm)  "
      f"nir band {nir_idx} ({wavelengths[nir_idx]:.1f} nm)")

jp2 = glymur.Jp2k(str(HSI_PATH))
hsi_data = jp2[:].astype(np.float32)  # (H, W, 300) or (300, H, W)
print(f"HSI  raw shape={hsi_data.shape}")

if hsi_data.ndim == 3 and hsi_data.shape[2] == len(wavelengths):
    red = hsi_data[:, :, red_idx]
    nir = hsi_data[:, :, nir_idx]
elif hsi_data.ndim == 3 and hsi_data.shape[0] == len(wavelengths):
    red = hsi_data[red_idx]
    nir = hsi_data[nir_idx]
else:
    raise ValueError(f"Unexpected HSI shape: {hsi_data.shape}")

ndvi = (nir - red) / (nir + red + 1e-6)
ndvi = np.clip(ndvi, -1.0, 1.0)
print(f"NDVI  min={ndvi.min():.3f}  max={ndvi.max():.3f}  mean={ndvi.mean():.3f}")

# ---------------------------------------------------------------------------
# Load Depth (mm); mask zeros (no-return pixels)
# ---------------------------------------------------------------------------
depth_raw = tifffile.imread(DEPTH_PATH).astype(np.float32)
depth = np.where(depth_raw == 0, np.nan, depth_raw)
print(f"Depth  shape={depth_raw.shape}  min={np.nanmin(depth):.1f}  max={np.nanmax(depth):.1f}")

# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------
def crop_square(arr, row_center=None, col_center=None, size=None):
    """Crop a square from arr, optionally specifying center pixel and crop size."""
    h, w = arr.shape[:2]
    if size is None:
        size = min(h, w)
    rc = h // 2 if row_center is None else row_center
    cc = w // 2 if col_center is None else col_center
    r0 = int(np.clip(rc - size // 2, 0, h - size))
    c0 = int(np.clip(cc - size // 2, 0, w - size))
    return arr[r0:r0 + size, c0:c0 + size]

def plant_col_center(mask):
    """Return the column centroid of a boolean plant mask."""
    col_weights = mask.sum(axis=0).astype(float)
    if col_weights.sum() == 0:
        return mask.shape[1] // 2
    return int(np.average(np.arange(mask.shape[1]), weights=col_weights))

# RGB: centroid of green-dominant pixels for horizontal alignment; crop size
# is reduced to 960 so there is vertical room to shift the view upward and
# align the leaf position with the depth panel.
r_, g_, b_ = rgb[:,:,0].astype(float), rgb[:,:,1].astype(float), rgb[:,:,2].astype(float)
green_excess = g_ - 0.5 * (r_ + b_)
rgb_plant_mask = green_excess > np.percentile(green_excess, 60)
RGB_CROP = 960          # px — smaller than h=1080 to allow vertical movement
RGB_ROW_SHIFT = -100    # px — negative shifts the crop toward the top of the frame
rgb_sq = crop_square(rgb,
                     row_center=rgb.shape[0] // 2 + RGB_ROW_SHIFT,
                     col_center=plant_col_center(rgb_plant_mask),
                     size=RGB_CROP)

# Thermal: no rotation — center on the pot (hottest region).
thermal_rot = thermal
THERMAL_ROW_SHIFT =  30   # px — positive shifts crop downward
THERMAL_COL_SHIFT =  30   # px — positive shifts crop rightward
hot_thresh = np.percentile(thermal_rot, 90)
hot_rows, hot_cols = np.where(thermal_rot >= hot_thresh)
thermal_sq = crop_square(thermal_rot,
                         row_center=int(np.median(hot_rows)) + THERMAL_ROW_SHIFT,
                         col_center=int(np.median(hot_cols)) + THERMAL_COL_SHIFT)

# NDVI: centroid of high-vegetation pixels (NDVI > 0.2).
# Image is already 256×256 (square) so cropping is a no-op, but we compute
# the centroid anyway in case a future image has more room.
ndvi_rot = np.rot90(ndvi, 1)   # 90° CCW from original = 90° CW from previous 180° view
ndvi_plant_mask = ndvi_rot > 0.2
ndvi_sq = crop_square(ndvi_rot, col_center=plant_col_center(ndvi_plant_mask))

# Depth: plant = closest to sensor (lowest valid depth values, bottom 30th pct).
depth_thresh = np.nanpercentile(depth, 30)
depth_plant_mask = (depth <= depth_thresh) & ~np.isnan(depth)
# Depth: use a slightly smaller crop so there is vertical room to shift the
# view upward, which pushes the plant toward the bottom of the pane.
DEPTH_CROP = 140        # px — smaller than h=171 to allow vertical movement
DEPTH_ROW_SHIFT = -40   # px — negative shifts crop toward top of image (plant moves down)
depth_sq = crop_square(depth,
                       row_center=depth.shape[0] // 2 + DEPTH_ROW_SHIFT,
                       col_center=plant_col_center(depth_plant_mask),
                       size=DEPTH_CROP)

# Figure: ADA panel (col 0, spans both rows) + 2x2 grid (cols 1-2)
# width_ratios=[1,1,1] → ADA is 1 unit wide × 2 units tall (1:2 portrait)
cell = 3.5  # inches per square panel
fig = plt.figure(figsize=((ADA_RATIO + 2) * cell, 2 * cell))
gs = fig.add_gridspec(2, 3, width_ratios=[ADA_RATIO, 1, 1],
                      wspace=0, hspace=0,
                      left=0, right=1, top=1, bottom=0)

ax_ada     = fig.add_subplot(gs[:, 0])
ax_rgb     = fig.add_subplot(gs[0, 1])
ax_thermal = fig.add_subplot(gs[0, 2])
ax_ndvi    = fig.add_subplot(gs[1, 1])
ax_depth   = fig.add_subplot(gs[1, 2])

panels = [
    (ax_ada,     ada,        None,       None),
    (ax_rgb,     rgb_sq,     None,       "A"),
    (ax_thermal, thermal_sq, "jet",      "B"),
    (ax_ndvi,    ndvi_sq,    "RdYlGn",   "C"),
    (ax_depth,   depth_sq,   "plasma",   "D"),
]

_label_kw = dict(
    transform=None,   # set per-axis below
    fontsize=24, fontweight="bold", color="white",
    va="top", ha="left",
    bbox=dict(boxstyle="square,pad=0.25", facecolor="black", alpha=0.6, edgecolor="none"),
)

for ax, data, cmap, label in panels:
    kw = {} if cmap is None else {"cmap": cmap}
    ax.imshow(data, **kw)
    ax.set_axis_off()
    if label:
        ax.text(0.03, 0.97, label, transform=ax.transAxes, **{k: v for k, v in _label_kw.items() if k != "transform"})

fig.savefig(str(OUTPUT), dpi=300, bbox_inches="tight", pad_inches=0, facecolor="white")
print(f"\nSaved -> {OUTPUT}")
