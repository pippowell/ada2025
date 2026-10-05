# Camera calibration and 3D coordinate transforms

This document describes the camera intrinsics, the rigid transforms between
the sensors of the ADA robot camera package, and how to apply them to fuse
depth, RGB and thermal data into a 3D point cloud.  It is the reference for
the numbers in `analysis/combined/camera_calibration.py` and the
implementation in
`analysis/combined/3d_viewer.py` (the latter contains ready-to-use
`build_point_cloud`, `colorize_depth_with_thermal` and `frustum_lineset`
functions).

## Sensors

| Sensor | Role | Resolution | Calibrated |
|---|---|---|---|
| Pico Flexx | depth + NIR intensity | 224 x 171 | intrinsics + distortion |
| C920 Logitech webcam | RGB | 1920 x 1080 | intrinsics + distortion + extrinsics to depth |
| FLIR A70 | thermal | 640 x 480 | intrinsics + distortion + extrinsics to depth |
| Pika L (HSI) | hyperspectral | 300 bands | **not** registered to the other sensors |

The hyperspectral camera has no calibrated extrinsics in this dataset, so it
cannot be placed in the 3D scene.

## Coordinate frames

* **Depth camera frame** — the reference frame of the 3D point cloud.  `+z`
  points along the optical axis (away from the camera, i.e. into the scene),
  `x`/`y` span the image plane (pinhole camera convention).
* **Sensor frames** — the RGB and FLIR cameras each have their own camera
  frame.  The extrinsics below transform *from the depth frame into that
  sensor frame*.
* **Robot frame** — the pose reported per frame in `frames_*.csv`
  (`position_robot_x/y/z`).  The depth camera is mounted rotated 90° around
  the robot `z`-axis, so a robot position is rotated into the depth frame
  before it is used as a translation (`ROBOT_TO_DEPTH_YAW_DEG = 90`).

## Intrinsics

Pinhole camera matrices `K` map camera-frame points to pixels:

```
u = fx * x / z + cx        v = fy * y / z + cy
```

All numbers are stored in `analysis/combined/camera_calibration.py`.

| Sensor | `K` (fx, fy, cx, cy) | distortion `D` (k1 k2 p1 p2 k3) |
|---|---|---|
| depth | 213.0786, 213.4774, 106.3087, 87.4824 | 0.06564, -2.14629, 0.00050, -0.00332, 3.22935 |
| RGB | 1397.1694, 1391.8124, 932.7569, 566.6212 | 0.14612, -0.30648, 0.00189, -0.00622, 0.24885 |
| FLIR | 1235.8578, 1236.4101, 289.0805, 265.5148 | 0.07142, 5.28314, 0.01630, -0.01628, -40.61658 |

Distortion uses OpenCV's 5-parameter radial + tangential model
(`cv2.undistort(image, K, D)`).

## Extrinsics (depth → sensor)

Each extrinsic is a rigid 4x4 transform `T_depth_to_sensor` that maps a point
`p` expressed in the **depth** camera frame into the **sensor** camera frame:
`p_sensor = T_depth_to_sensor · [p, 1]`.  The rotation part is stored as a
Rodrigues vector `rvec` plus a translation `tvec`.

| Transform | `rvec` | `tvec` (m) |
|---|---|---|
| `T_depth_to_rgb` | (−0.0104, 0.0578, 3.1367) | (0.0538, 0.0429, −0.00196) |
| `T_depth_to_flir` | (0.0035, 0.0756, −0.0362) | (0.0293, −0.0517, −0.0149) |

`analysis/combined/camera_calibration.py` provides the `rotation_matrix()` /
`transformation_matrix()` helpers and the precomputed `T_DEPTH_TO_RGB`,
`T_DEPTH_TO_FLIR`, `R_ROBOT_TO_DEPTH` matrices (also used by `3d_viewer.py`).

## Per-frame robot motion

The robot moves along the plant row while capturing, so every frame has its
own pose.  For a frame `k` with robot position `p_robot[k]` (from the
`frames_*.csv` of that sensor), build the shift matrices

```
T_shift_depth = I4 with translation rotz(90°)·p_robot_depth[k]
T_shift_rgb   = I4 with translation rotz(90°)·p_robot_rgb[k]
T_shift_flir  = I4 with translation rotz(90°)·p_robot_flir[k]
```

and correct the fixed extrinsics by the relative pose difference:

```
T_depth_to_rgb_shifted = T_depth_to_rgb  @ inv(T_shift_rgb)  @ T_shift_depth
T_depth_to_flir_shifted = T_depth_to_flir @ inv(T_shift_flir) @ T_shift_depth
```

This is what makes the depth, RGB and thermal images of the *same* scene
point align.

## Building the point cloud (the math)

1. **Undistort** depth, RGB and FLIR with their intrinsics/distortion.
2. **Unproject** every valid depth pixel `(u, v, z)` into a 3D point in the
   depth frame:
   ```
   x = (u − cx_d) · z / fx_d      y = (v − cy_d) · z / fy_d
   ```
   where `z` is the depth (in meters) and `z > 0` marks valid pixels.
3. **Sample colors/temperature** — for the same depth points, project into
   the RGB and FLIR cameras and remap their pixels back onto the depth grid:
   ```
   p_sensor = T_depth_to_sensor_shifted · [x, y, z, 1]
   u = fx_sensor · p_sensor.x / p_sensor.z + cx_sensor
   v = fy_sensor · p_sensor.y / p_sensor.z + cy_sensor
   ```
   Only pixels with `p_sensor.z > 0` inside the image bounds are valid.
4. The second page of the depth TIFF is the NIR intensity; per-point values
   are read at the same (u, v).

## How to use it

* All constants and helpers live in **`analysis/combined/camera_calibration.py`**:
  `K_DEPTH/K_RGB/K_FLIR`, `*_DISTORTION`, `RVEC/TVEC_DEPTH_TO_*`,
  `ROBOT_TO_DEPTH_YAW_DEG`, `DEPTH_W/RGB_W/FLIR_W/H`, `T_DEPTH_TO_RGB`,
  `T_DEPTH_TO_FLIR`, `R_ROBOT_TO_DEPTH`, `robot_to_depth_position()`,
  `shift_matrix()`.
* The complete, tested pipeline is `analysis/combined/3d_viewer.py`
  (`build_point_cloud()`, `colorize_depth_with_thermal()`,
  `depth_image_to_point_cloud()`, `frustum_lineset()`).  It loads the images,
  reads `frames_*.csv`, builds the shifted extrinsics and produces
  `points (N,3)`, `colors (N,3)`, `temperature (N,)`, `intensity (N,)` plus
  the shifted transforms.
* Minimal reprojection of one depth pixel:
  ```python
  import numpy as np
  from camera_calibration import K_DEPTH, K_RGB, T_DEPTH_TO_RGB

  # point in depth frame
  p_depth = np.array([(u - cx_d) * z / fx_d, (v - cy_d) * z / fy_d, z, 1.0])
  p_rgb = T_DEPTH_TO_RGB @ p_depth
  u_rgb = K_RGB[0, 0] * p_rgb[0] / p_rgb[2] + K_RGB[0, 2]
  ```

## Provenance

The RGB, thermal and depth cameras were calibrated jointly with a chessboard,
with extrinsics estimated relative to the depth camera by bundle adjustment.
The calibration script (`align_test.py`) is not included: it only runs on the
calibration recordings (ROS bag files) made for this rig, which are not part
of the dataset.  The resulting numbers are hardcoded in
`analysis/combined/camera_calibration.py`; the FLIR intrinsics and
depth→FLIR extrinsic are also in `dataset-checker-v2/app/camera_params.py`.
Keep all copies in sync.

Reprojection errors reported by the calibration:

| Sensor | Reprojection error (px) |
|---|---|
| RGB | 1.11 |
| thermal (FLIR) | 1.55 |
| depth | 1.23 |