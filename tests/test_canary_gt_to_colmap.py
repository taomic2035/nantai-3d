"""Tests for scripts/canary_gt_to_colmap.py.

The script converts synthetic-village canary renders (GT camera poses +
depth EXR) into a COLMAP text dataset for direct 3DGS training (bypassing
SfM). Its three internal self-checks (R rigidity, quaternion round-trip,
cross-camera depth consistency) all depend on these pure-numpy functions
being correct. A bug here would silently corrupt every camera pose or
point cloud in the output.

Part 1 covers the math layer (rotmat↔quat, backproject↔project).
Part 2 covers ``main()`` integration: the three self-checks fire on bad
input, and a valid render set produces a well-formed COLMAP dataset.
``load_depth`` is monkeypatched to avoid constructing real EXR files —
the EXR I/O is a thin wrapper; the logic under test is the validation
and file-generation flow.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.canary_gt_to_colmap import (  # noqa: E402
    _c2w_opencv,
    backproject,
    main,
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


class TestC2wFieldResolution:
    """``_c2w_opencv`` must support both v1 ``c2w_opencv`` and ``measured_c2w_opencv``."""

    def _identity_c2w(self) -> list[list[float]]:
        return [[1, 0, 0, 5], [0, 1, 0, 3], [0, 0, 1, 2], [0, 0, 0, 1]]

    def test_prefers_measured_when_present(self):
        meta = {
            "measured_c2w_opencv": self._identity_c2w(),
            "c2w_opencv": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        }
        result = _c2w_opencv(meta)
        assert result[0, 3] == 5

    def test_falls_back_to_c2w_opencv_v1(self):
        meta = {"c2w_opencv": self._identity_c2w()}
        result = _c2w_opencv(meta)
        assert result.shape == (4, 4)
        assert result[0, 3] == 5
        assert result[2, 3] == 2

    def test_raises_on_missing_both(self):
        with pytest.raises(KeyError):
            _c2w_opencv({"camera_id": "x"})


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


# ────────────────────────────────────────────────────────────────────
# Part 2: main() integration — three self-checks + COLMAP file generation.
# ────────────────────────────────────────────────────────────────────

_INTR_A = {"fx": 100, "fy": 100, "cx": 50, "cy": 50,
           "width_px": 100, "height_px": 100}
_INTR_B = {"fx": 200, "fy": 200, "cx": 50, "cy": 50,
           "width_px": 100, "height_px": 100}
_EXPECTED_COORD = "opencv-c2w-right-down-forward-meters"


def _make_camera_json(cam_id: str, c2w: np.ndarray, intr: dict,
                      coord: str | None = None) -> str:
    return json.dumps({
        "camera_id": cam_id,
        "intrinsics": intr,
        "measured_c2w_opencv": c2w.tolist(),
        "coordinate_system": coord or _EXPECTED_COORD,
    }, ensure_ascii=False)


def _write_png(path: Path, w: int = 100, h: int = 100) -> None:
    img = np.full((h, w, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(path), img)


def _make_renders(
    tmp_path: Path,
    *,
    n_cams: int = 2,
    coord_override: str | None = None,
    c2w_override: np.ndarray | None = None,
    depth_values: list[float] | None = None,
) -> tuple[Path, dict[str, np.ndarray]]:
    """Build a minimal canary render directory.

    Returns ``(renders_dir, depth_maps)`` where ``depth_maps`` maps
    ``cam_id → numpy array`` for monkeypatching ``load_depth``.
    """
    renders = tmp_path / "renders"
    (renders / "cameras").mkdir(parents=True)
    (renders / "rgb").mkdir(parents=True)
    (renders / "depth").mkdir(parents=True)

    identity = np.eye(4)
    intr_sets = [_INTR_A, _INTR_B]
    depths = depth_values or [10.0] * n_cams
    depth_maps: dict[str, np.ndarray] = {}

    for i in range(n_cams):
        cid = f"cam_{i:03d}"
        c2w = c2w_override if c2w_override is not None else identity
        intr = intr_sets[i % len(intr_sets)]
        coord = coord_override or _EXPECTED_COORD

        (renders / "cameras" / f"{cid}.json").write_text(
            _make_camera_json(cid, c2w, intr, coord), encoding="utf-8")
        _write_png(renders / "rgb" / f"{cid}.png",
                   intr["width_px"], intr["height_px"])
        depth_maps[cid] = np.full(
            (intr["height_px"], intr["width_px"]), depths[i])

    return renders, depth_maps


def _patch_load_depth(monkeypatch, depth_maps: dict[str, np.ndarray]) -> None:
    """Monkeypatch ``load_depth`` to return constructed numpy arrays."""
    import scripts.canary_gt_to_colmap as mod
    monkeypatch.setattr(
        mod, "load_depth",
        lambda p: depth_maps[Path(p).stem])


class TestMainSelfChecks:
    """The three self-checks must fire before any COLMAP file is written."""

    def test_no_camera_jsons_exits(self, tmp_path):
        renders = tmp_path / "renders"
        (renders / "cameras").mkdir(parents=True)
        with pytest.raises(SystemExit, match="无相机 JSON"):
            main([str(renders), str(tmp_path / "out")])

    def test_wrong_coordinate_system_exits(self, tmp_path, monkeypatch):
        renders, dm = _make_renders(
            tmp_path, coord_override="blender-c2w-left-up-forward")
        _patch_load_depth(monkeypatch, dm)
        with pytest.raises(SystemExit, match="坐标系非预期"):
            main([str(renders), str(tmp_path / "out")])

    def test_reflection_matrix_exits(self, tmp_path, monkeypatch):
        # diag(1, -1, 1) is orthogonal (R@R^T=I) but det=-1 → reflection
        bad_c2w = np.diag([1.0, -1.0, 1.0, 1.0])
        renders, dm = _make_renders(tmp_path, c2w_override=bad_c2w)
        _patch_load_depth(monkeypatch, dm)
        with pytest.raises(SystemExit, match="R 非刚性"):
            main([str(renders), str(tmp_path / "out")])

    def test_non_orthogonal_matrix_exits(self, tmp_path, monkeypatch):
        # Shear: R@R^T != I, det ≈ 1 → fails rigid check
        bad_rot = np.array([[1.0, 0.1, 0.0],
                            [0.0, 1.0, 0.0],
                            [0.0, 0.0, 1.0]])
        bad_c2w = np.eye(4)
        bad_c2w[:3, :3] = bad_rot
        renders, dm = _make_renders(tmp_path, c2w_override=bad_c2w)
        _patch_load_depth(monkeypatch, dm)
        with pytest.raises(SystemExit, match="R 非刚性"):
            main([str(renders), str(tmp_path / "out")])

    def test_cross_camera_depth_mismatch_exits(self, tmp_path, monkeypatch):
        # Two cameras at same position, depths 10 vs 20 → rel error = 1.0
        renders, dm = _make_renders(tmp_path, depth_values=[10.0, 20.0])
        _patch_load_depth(monkeypatch, dm)
        with pytest.raises(SystemExit, match="跨相机深度不一致"):
            main([str(renders), str(tmp_path / "out"), "--stride", "5"])


class TestMainSuccess:
    """A valid render set produces a well-formed COLMAP text dataset."""

    def test_generates_colmap_files(self, tmp_path, monkeypatch):
        renders, dm = _make_renders(tmp_path)
        _patch_load_depth(monkeypatch, dm)
        out = tmp_path / "out"
        rc = main([str(renders), str(out), "--stride", "5"])
        assert rc == 0

        assert (out / "sparse" / "0" / "cameras.txt").is_file()
        assert (out / "sparse" / "0" / "images.txt").is_file()
        assert (out / "sparse" / "0" / "points3D.txt").is_file()
        assert (out / "images" / "cam_000.png").is_file()
        assert (out / "images" / "cam_001.png").is_file()

    def test_two_distinct_intrinsics_yield_two_cameras(
            self, tmp_path, monkeypatch):
        renders, dm = _make_renders(tmp_path)
        _patch_load_depth(monkeypatch, dm)
        out = tmp_path / "out"
        main([str(renders), str(out), "--stride", "5"])

        cam_text = (out / "sparse" / "0" / "cameras.txt").read_text(
            encoding="utf-8")
        assert cam_text.count("PINHOLE") == 2

    def test_identical_intrinsics_dedup_to_one_camera(
            self, tmp_path, monkeypatch):
        renders, dm = _make_renders(tmp_path)
        _patch_load_depth(monkeypatch, dm)
        # Rewrite cam_001 with same intrinsics as cam_000
        cam0 = json.loads(
            (renders / "cameras" / "cam_000.json").read_text(encoding="utf-8"))
        cam1 = json.loads(
            (renders / "cameras" / "cam_001.json").read_text(encoding="utf-8"))
        cam1["intrinsics"] = cam0["intrinsics"]
        (renders / "cameras" / "cam_001.json").write_text(
            json.dumps(cam1, ensure_ascii=False), encoding="utf-8")

        out = tmp_path / "out"
        main([str(renders), str(out), "--stride", "5"])

        cam_text = (out / "sparse" / "0" / "cameras.txt").read_text(
            encoding="utf-8")
        assert cam_text.count("PINHOLE") == 1

    def test_images_txt_has_quaternion_and_translation(
            self, tmp_path, monkeypatch):
        renders, dm = _make_renders(tmp_path)
        _patch_load_depth(monkeypatch, dm)
        out = tmp_path / "out"
        main([str(renders), str(out), "--stride", "5"])

        img_text = (out / "sparse" / "0" / "images.txt").read_text(
            encoding="utf-8")
        lines = [line for line in img_text.strip().split("\n")
                 if line and not line.startswith("#")]
        assert len(lines) == 2  # 2 cameras → 2 data lines
        # Each line: IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME (10 fields)
        parts = lines[0].split()
        assert len(parts) == 10
        # Identity c2w → q = [1, 0, 0, 0], t = [0, 0, 0]
        assert float(parts[1]) == pytest.approx(1.0)  # QW
        assert parts[-1] == "cam_000.png"  # NAME

    def test_points3d_has_backprojected_points(self, tmp_path, monkeypatch):
        renders, dm = _make_renders(tmp_path)
        _patch_load_depth(monkeypatch, dm)
        out = tmp_path / "out"
        main([str(renders), str(out), "--stride", "5"])

        pts_text = (out / "sparse" / "0" / "points3D.txt").read_text(
            encoding="utf-8")
        lines = [line for line in pts_text.strip().split("\n")
                 if line and not line.startswith("#")]
        # stride=5 on 100×100 → 20×20 = 400 pts/cam × 2 cams = 800
        assert len(lines) == 800
        # Each line: POINT3D_ID X Y Z R G B ERROR
        parts = lines[0].split()
        assert len(parts) == 8
        # Identity c2w + depth 10 → all points at Euclidean distance ≈ 10
        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        assert abs(np.sqrt(x ** 2 + y ** 2 + z ** 2) - 10.0) < 0.5


# ============================================================
# check_cross_camera_depth: isolated unit tests
# ============================================================

class TestCheckCrossCameraDepth:
    """Isolated tests for check_cross_camera_depth beyond the main() path.

    The existing test_cross_camera_depth_mismatch_exits exercises the
    mismatch → SystemExit path via main().  These tests cover the skip
    branch (insufficient overlap) and the pass branch (consistent depth)
    by calling check_cross_camera_depth directly.
    """

    def test_skips_pair_when_overlap_insufficient(self, tmp_path, monkeypatch):
        """When in-frame projection < 50 points, the pair is skipped (no exit)."""
        import scripts.canary_gt_to_colmap as mod

        # Camera A at origin looking +Z; Camera B translated far away so
        # A's points project outside B's frame → inb.sum() < 50 → skip.
        intr = _INTR_A
        c2w_a = np.eye(4)
        c2w_b = np.eye(4).copy()
        c2w_b[0, 3] = 5000.0  # huge translation → points project way off-frame

        metas = [
            {"camera_id": "cam_000", "intrinsics": intr,
             "measured_c2w_opencv": c2w_a.tolist()},
            {"camera_id": "cam_001", "intrinsics": intr,
             "measured_c2w_opencv": c2w_b.tolist()},
        ]
        renders = tmp_path / "renders"
        (renders / "depth").mkdir(parents=True)
        depth = np.full((intr["height_px"], intr["width_px"]), 10.0)
        _patch_load_depth(monkeypatch, {"cam_000": depth, "cam_001": depth})

        # Should NOT raise — both pairs skip due to insufficient overlap.
        mod.check_cross_camera_depth(metas, renders, stride=5)

    def test_passes_when_depths_consistent(self, tmp_path, monkeypatch):
        """When two cameras share consistent depth, no SystemExit is raised."""
        import scripts.canary_gt_to_colmap as mod

        intr = _INTR_A
        # Both cameras at origin, same depth → rel error ≈ 0 → pass.
        c2w = np.eye(4)
        metas = [
            {"camera_id": "cam_000", "intrinsics": intr,
             "measured_c2w_opencv": c2w.tolist()},
            {"camera_id": "cam_001", "intrinsics": intr,
             "measured_c2w_opencv": c2w.tolist()},
        ]
        renders = tmp_path / "renders"
        (renders / "depth").mkdir(parents=True)
        depth = np.full((intr["height_px"], intr["width_px"]), 10.0)
        _patch_load_depth(monkeypatch, {"cam_000": depth, "cam_001": depth})

        # Should NOT raise — depths are identical, rel error ≈ 0.
        mod.check_cross_camera_depth(metas, renders, stride=5)

    def test_raises_when_depth_mismatch(self, tmp_path, monkeypatch):
        """When cross-camera depth median rel error > tol, SystemExit is raised."""
        import scripts.canary_gt_to_colmap as mod

        intr = _INTR_A
        c2w = np.eye(4)
        metas = [
            {"camera_id": "cam_000", "intrinsics": intr,
             "measured_c2w_opencv": c2w.tolist()},
            {"camera_id": "cam_001", "intrinsics": intr,
             "measured_c2w_opencv": c2w.tolist()},
        ]
        renders = tmp_path / "renders"
        (renders / "depth").mkdir(parents=True)
        depth_a = np.full((intr["height_px"], intr["width_px"]), 10.0)
        depth_b = np.full((intr["height_px"], intr["width_px"]), 20.0)
        _patch_load_depth(monkeypatch, {"cam_000": depth_a, "cam_001": depth_b})

        with pytest.raises(SystemExit, match="跨相机深度不一致"):
            mod.check_cross_camera_depth(metas, renders, stride=5)
