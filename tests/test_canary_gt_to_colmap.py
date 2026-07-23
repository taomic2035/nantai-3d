"""Tests for scripts/canary_gt_to_colmap.py coordinate-conversion math.

The script converts synthetic-village canary renders (GT camera poses +
depth EXR) into a COLMAP text dataset for direct 3DGS training (bypassing
SfM). Its three internal self-checks (R rigidity, quaternion round-trip,
cross-camera depth consistency) all depend on these pure-numpy functions
being correct. A bug here would silently corrupt every camera pose or
point cloud in the output.

These tests cover the math layer only — ``main()`` requires actual render
files (cameras/*.json, depth/*.exr, rgb/*.png) and is exercised by the
synthetic-village canary pipeline, not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.canary_gt_to_colmap import (  # noqa: E402
    backproject,
    project,
    quat_to_rotmat,
    rotmat_to_quat,
)


def _rot_x(deg: float) -> np.ndarray:
    r = np.radians(deg)
    c, s = np.cos(r), np.sin(r)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def _rot_y(deg: float) -> np.ndarray:
    r = np.radians(deg)
    c, s = np.cos(r), np.sin(r)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _rot_z(deg: float) -> np.ndarray:
    r = np.radians(deg)
    c, s = np.cos(r), np.sin(r)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class TestRotmatQuatRoundTrip:
    """Self-check 2: rotmat → quat → rotmat must reconstruct the rotation."""

    @pytest.mark.parametrize("deg", [0, 30, 45, 90, 120, 180, 270, 359])
    def test_rotation_x_round_trip(self, deg):
        r = _rot_x(deg)
        q = rotmat_to_quat(r)
        r2 = quat_to_rotmat(q)
        assert np.allclose(r, r2, atol=1e-10)

    @pytest.mark.parametrize("deg", [0, 30, 45, 90, 120, 180, 270, 359])
    def test_rotation_y_round_trip(self, deg):
        r = _rot_y(deg)
        q = rotmat_to_quat(r)
        assert np.allclose(r, quat_to_rotmat(q), atol=1e-10)

    @pytest.mark.parametrize("deg", [0, 30, 45, 90, 120, 180, 270, 359])
    def test_rotation_z_round_trip(self, deg):
        r = _rot_z(deg)
        q = rotmat_to_quat(r)
        assert np.allclose(r, quat_to_rotmat(q), atol=1e-10)

    def test_composite_rotation_round_trip(self):
        # R = Rz @ Ry @ Rx (common Euler sequence)
        r = _rot_z(37) @ _rot_y(53) @ _rot_x(19)
        q = rotmat_to_quat(r)
        assert np.allclose(r, quat_to_rotmat(q), atol=1e-10)

    def test_identity_round_trip(self):
        r = np.eye(3)
        q = rotmat_to_quat(r)
        assert np.allclose(q, [1, 0, 0, 0], atol=1e-10)
        assert np.allclose(quat_to_rotmat(q), r, atol=1e-10)

    def test_quaternion_is_unit_norm(self):
        for r in [_rot_x(45), _rot_y(90), _rot_z(120),
                  _rot_z(37) @ _rot_y(53) @ _rot_x(19)]:
            q = rotmat_to_quat(r)
            assert abs(np.linalg.norm(q) - 1.0) < 1e-10

    def test_det_plus_one_preserved(self):
        # COLMAP requires det=+1 (no reflections); the conversion must not flip it
        for r in [_rot_x(45), _rot_y(90), _rot_z(120),
                  _rot_z(37) @ _rot_y(53) @ _rot_x(19)]:
            q = rotmat_to_quat(r)
            r2 = quat_to_rotmat(q)
            assert abs(np.linalg.det(r2) - 1.0) < 1e-8

    def test_180_degree_rotation_quaternion_w_near_zero(self):
        # 180° rotation → w ≈ 0, tests the non-trace-dominant branch
        r = _rot_x(180)
        q = rotmat_to_quat(r)
        assert abs(q[0]) < 1e-6  # w ≈ 0
        assert np.allclose(quat_to_rotmat(q), r, atol=1e-10)


class TestBackprojectProject:
    """Self-check 3 (basis): backproject → project must recover the original
    depth and pixel coordinates for a single camera.

    If these are inconsistent, cross-camera depth checks will also fail —
    but a single-camera round-trip catches the basic convention error first.
    """

    def _make_intrinsics(self, w=100, h=80, fx=200, fy=200):
        return {"fx": fx, "fy": fy, "cx": w / 2, "cy": h / 2,
                "width_px": w, "height_px": h}

    def test_backproject_then_project_recovers_depth(self):
        """Points backprojected from depth, then re-projected, must recover
        the same depth (Euclidean distance from camera center)."""
        h, w = 80, 100
        intr = self._make_intrinsics(w, h)
        # A simple depth map: constant 10m (Euclidean distance)
        depth = np.full((h, w), 10.0)
        # Camera at origin looking along +Z (OpenCV c2w = identity)
        c2w = np.eye(4)
        c2w[3, 3] = 1.0

        pts_w, uvs = backproject(depth, intr, c2w, stride=10)
        uv2, rng_pred, z = project(pts_w, intr, c2w)

        # Recovered depth (Euclidean) must match original 10m
        assert np.allclose(rng_pred, 10.0, atol=1e-6)
        # Recovered pixels must match original sampling pixels
        assert np.allclose(uvs, uv2, atol=0.5)

    def test_backproject_with_translated_camera(self):
        """Camera translated to [5, 0, 0], looking along +Z → world points
        shifted by +5 in X."""
        h, w = 40, 50
        intr = self._make_intrinsics(w, h, fx=100, fy=100)
        depth = np.full((h, w), 5.0)
        c2w = np.eye(4)
        c2w[0, 3] = 5.0  # translate X
        c2w[3, 3] = 1.0

        pts_w, _ = backproject(depth, intr, c2w, stride=10)
        # The center-pixel point should be near [5, 0, 5]
        # (depth 5 along +Z from camera at [5,0,0])
        dists = np.linalg.norm(pts_w - np.array([5.0, 0.0, 5.0]), axis=1)
        assert dists.min() < 1.0

    def test_backproject_with_rotated_camera(self):
        """Camera rotated 90° around Y → looks along +X instead of +Z.
        A point at depth d along the camera's forward should appear at
        world X = d, Z = 0."""
        h, w = 40, 50
        intr = self._make_intrinsics(w, h, fx=100, fy=100)
        depth = np.full((h, w), 7.0)
        c2w = np.eye(4)
        c2w[:3, :3] = _rot_y(90)  # rotate 90° around Y → forward = +X
        c2w[3, 3] = 1.0

        pts_w, _ = backproject(depth, intr, c2w, stride=10)
        # Center pixel ray → world [7, 0, 0] (depth 7 along +X)
        dists = np.linalg.norm(pts_w - np.array([7.0, 0.0, 0.0]), axis=1)
        assert dists.min() < 1.0

    def test_invalid_depth_zero_is_skipped(self):
        """depth=0 means invalid (depth_invalid_value_m=0) → must be excluded."""
        h, w = 20, 20
        intr = self._make_intrinsics(w, h, fx=50, fy=50)
        depth = np.zeros((h, w))
        depth[10, 10] = 5.0  # one valid point
        c2w = np.eye(4)
        c2w[3, 3] = 1.0

        pts_w, uvs = backproject(depth, intr, c2w, stride=1)
        assert len(pts_w) == 1  # only the valid pixel
        assert np.allclose(uvs[0], [10.5, 10.5], atol=0.01)

    def test_stride_subsamples_pixels(self):
        """stride > 1 should produce fewer points than stride=1."""
        h, w = 40, 40
        intr = self._make_intrinsics(w, h, fx=50, fy=50)
        depth = np.full((h, w), 5.0)
        c2w = np.eye(4)
        c2w[3, 3] = 1.0

        pts_full, _ = backproject(depth, intr, c2w, stride=1)
        pts_strided, _ = backproject(depth, intr, c2w, stride=10)
        assert len(pts_strided) < len(pts_full)
        # stride=10 on 40×40 → ~16 points (4×4 grid)
        assert len(pts_strided) == 16


class TestExpectedCoordinateConvention:
    """The script hard-codes EXPECTED_COORD = "opencv-c2w-right-down-forward-meters".
    These tests document what that convention means numerically — a future
    refactor that changes the convention should fail here.
    """

    def test_opencv_c2w_identity_means_forward_is_plus_z(self):
        """OpenCV c2w=identity → camera looks along +Z (right=X, down=Y, forward=Z).
        Depth is Euclidean distance from camera center, so the center-pixel
        point at depth d should be at [0, 0, d]; off-center points are at
        Euclidean distance d but have Z < d (ray angle spreads them)."""
        h, w = 20, 20
        intr = {"fx": 50, "fy": 50, "cx": 10, "cy": 10,
                "width_px": w, "height_px": h}
        depth = np.full((h, w), 3.0)
        c2w = np.eye(4)
        c2w[3, 3] = 1.0
        pts_w, _ = backproject(depth, intr, c2w, stride=5)
        # All points at Euclidean distance 3.0 from camera center (origin)
        dists = np.linalg.norm(pts_w, axis=1)
        assert np.allclose(dists, 3.0, atol=1e-6)
        # Center-pixel point (nearest to [0,0,3]) is at Z ≈ 3
        center_dist = np.linalg.norm(pts_w - np.array([0, 0, 3.0]), axis=1)
        assert center_dist.min() < 0.5

    def test_opencv_c2w_rotated_90_y_means_forward_is_plus_x(self):
        """90° Y rotation → forward=+X. Center-pixel point at depth d at [d, 0, 0].
        All points still at Euclidean distance d from origin."""
        h, w = 20, 20
        intr = {"fx": 50, "fy": 50, "cx": 10, "cy": 10,
                "width_px": w, "height_px": h}
        depth = np.full((h, w), 3.0)
        c2w = np.eye(4)
        c2w[:3, :3] = _rot_y(90)
        c2w[3, 3] = 1.0
        pts_w, _ = backproject(depth, intr, c2w, stride=5)
        # All points at Euclidean distance 3.0 from camera center (origin)
        dists = np.linalg.norm(pts_w, axis=1)
        assert np.allclose(dists, 3.0, atol=1e-6)
        # Center-pixel point (nearest to [3,0,0]) is at X ≈ 3
        center_dist = np.linalg.norm(pts_w - np.array([3.0, 0, 0]), axis=1)
        assert center_dist.min() < 0.5
