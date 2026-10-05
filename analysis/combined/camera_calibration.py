"""Camera intrinsics/extrinsics for the ADA 2025 camera package.

This is the single source of truth for the calibration of the robot's depth,
RGB and thermal (FLIR) cameras and for the helpers that compose the rigid
transforms.  The depth/RGB numbers were determined by ``align_test.py``; the
FLIR numbers come from the same rig (``dataset-checker-v2/app/camera_params.py``).

The coordinate frames, the per-frame robot-shift composition and the
projection/unprojection math are documented in
``docs/camera_calibration.md``.  Everything here is consumed by
``analysis/combined/3d_viewer.py`` and ``analysis/plant_height/``.

"""

from __future__ import annotations

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Intrinsics (pinhole camera matrices) and distortion (OpenCV 5-param model)
# ---------------------------------------------------------------------------

K_DEPTH = np.array(
    [
        [213.07864182105573, 0.0, 106.30867219370424],
        [0.0, 213.4773675713787, 87.48242329736486],
        [0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)

DEPTH_DISTORTION = np.array(
    [
        0.06563883079346727,
        -2.1462902300917204,
        0.0004996931251144205,
        -0.003320348250271367,
        3.229347799887664,
    ],
    dtype=np.float64,
)

K_RGB = np.array(
    [
        [1397.1693759311743, 0.0, 932.7568965495196],
        [0.0, 1391.8123785653943, 566.6211724978231],
        [0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)

RGB_DISTORTION = np.array(
    [
        0.14612012441410802,
        -0.3064798846037176,
        0.0018885253236924308,
        -0.006218715146850731,
        0.2488477543929147,
    ],
    dtype=np.float64,
)

K_FLIR = np.array(
    [
        [1235.8577572886502, 0.0, 289.0805485094724],
        [0.0, 1236.4101137173182, 265.5147977940737],
        [0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)

FLIR_DISTORTION = np.array(
    [
        0.07142030350380577,
        5.283136483147005,
        0.01630229908552685,
        -0.01628107985657429,
        -40.61658327283593,
    ],
    dtype=np.float64,
)

# ---------------------------------------------------------------------------
# Extrinsics: depth camera frame -> sensor frame (Rodrigues vector + tvec)
# ---------------------------------------------------------------------------

RVEC_DEPTH_TO_RGB = (-0.010361366746608086, 0.057755279059599554, 3.1366704147707054)
TVEC_DEPTH_TO_RGB = (0.0537813722846987, 0.04292643179470712, -0.0019552023110125907)

RVEC_DEPTH_TO_FLIR = (0.0034610286791345066, 0.07564865751693899, -0.03625049923293348)
TVEC_DEPTH_TO_FLIR = (0.029305079934097405, -0.05172043896398373, -0.01493650923515098)

# ---------------------------------------------------------------------------
# Robot frame -> depth frame
# ---------------------------------------------------------------------------

ROBOT_TO_DEPTH_YAW_DEG = 90.0

DEPTH_W, DEPTH_H = 224, 171
RGB_W, RGB_H = 1920, 1080
FLIR_W, FLIR_H = 640, 480

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rotation_matrix(rvec) -> np.ndarray:
    """3x3 rotation matrix from a Rodrigues rotation vector."""
    rvec_arr = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    rmat, _ = cv2.Rodrigues(rvec_arr)
    return rmat


def transformation_matrix(rvec, tvec) -> np.ndarray:
    """4x4 rigid transform from a Rodrigues rotation vector and a translation."""
    tfm = np.eye(4, dtype=np.float64)
    tfm[:3, :3] = rotation_matrix(rvec)
    tfm[:3, 3] = np.asarray(tvec, dtype=np.float64)
    return tfm


def rotz(theta_rad: float) -> np.ndarray:
    """3x3 rotation about the z-axis."""
    c, s = np.cos(theta_rad), np.sin(theta_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def robot_to_depth_position(position_robot) -> np.ndarray:
    """Rotate a robot-frame position into the depth camera frame."""
    return R_ROBOT_TO_DEPTH @ np.asarray(position_robot, dtype=np.float64)


def shift_matrix(position) -> np.ndarray:
    """4x4 translation-only transform for a position."""
    tfm = np.eye(4, dtype=np.float64)
    tfm[:3, 3] = position
    return tfm


T_DEPTH_TO_RGB = transformation_matrix(RVEC_DEPTH_TO_RGB, TVEC_DEPTH_TO_RGB)
T_DEPTH_TO_FLIR = transformation_matrix(RVEC_DEPTH_TO_FLIR, TVEC_DEPTH_TO_FLIR)
R_ROBOT_TO_DEPTH = rotz(np.deg2rad(ROBOT_TO_DEPTH_YAW_DEG))