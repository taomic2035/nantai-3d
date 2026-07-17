"""splat_provenance 的护栏测试。

核心不对称性 (每个测试都在守它):
- ``CONTRADICTED`` 是强证据 -> fail-closed。
- ``NOT_CONTRADICTED`` **不是**"通过"、不是证明,只是"没发现矛盾"。
- 读不到证据 -> ``UNKNOWN``，绝不退化成 NOT_CONTRADICTED。
"""
from __future__ import annotations

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from pipeline.splat_provenance import (
    CONTRADICTION_RATIO,
    Verdict,
    check_splat_against_sparse,
)


def write_points3d(path, pts):
    lines = ["# 3D point list: POINT3D_ID X Y Z R G B ERROR TRACK[]"]
    for i, (x, y, z) in enumerate(pts, start=1):
        lines.append(f"{i} {x:.6f} {y:.6f} {z:.6f} 128 128 128 0.5")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_ply(path, xyz):
    arr = np.array([tuple(p) for p in xyz], dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(str(path))


@pytest.fixture
def sparse_pts():
    rng = np.random.default_rng(0)
    return rng.uniform(0, 10, size=(3000, 3))


def test_consistent_splat_is_not_contradicted(tmp_path, sparse_pts):
    """训练产物稠密覆盖 sparse -> 没发现矛盾 (但这不叫"通过")。"""
    rng = np.random.default_rng(1)
    xyz = np.repeat(sparse_pts, 3, axis=0) + rng.normal(0, 0.01, size=(9000, 3))
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    write_ply(tmp_path / "t.ply", xyz)

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.NOT_CONTRADICTED
    assert r.signal_ratio > CONTRADICTION_RATIO
    # 语义护栏: 绝不能把"没发现矛盾"说成证明/通过/已验证。
    # 只查**肯定式**断言 —— "也不证明 ply 来自该 workspace" 这类否定式是诚实措辞，必须放行。
    assert not r.proves_provenance
    blob = f"{r.verdict.value} {r.reason} {r.summary()}".lower()
    for claim in ("verified", "proven", "已证明", "已验证", "确认来自", "证明了", "通过校验"):
        assert claim not in blob


def test_uniform_noise_is_contradicted(tmp_path, sparse_pts):
    """与 sparse 无关的均匀噪声 -> 与随机点云无异 -> 强矛盾。"""
    rng = np.random.default_rng(2)
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    write_ply(tmp_path / "t.ply", rng.uniform(0, 10, size=(9000, 3)))

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.CONTRADICTED
    assert r.signal_ratio == pytest.approx(1.0, abs=0.5)  # null model 锚点


def test_adding_gaussians_cannot_launder_noise(tmp_path, sparse_pts):
    """密度不变性: 噪声加到 20 倍点数也翻不了案。

    这是判据的**存在理由** —— 绝对容差判据 ("X% 的点在 Y 内有高斯") 会被
    稠密噪声平凡满足 (实测: 2M 点噪声在 0.5% diag 下覆盖率 100%)。
    """
    rng = np.random.default_rng(3)
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    write_ply(tmp_path / "t.ply", rng.uniform(0, 10, size=(200_000, 3)))

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.CONTRADICTED
    assert r.signal_ratio == pytest.approx(1.0, abs=0.5)


def test_disjoint_scene_is_contradicted(tmp_path, sparse_pts):
    """别的场景 (bbox 完全不相交) -> 强矛盾, 无需统计。"""
    rng = np.random.default_rng(4)
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    write_ply(tmp_path / "t.ply", rng.uniform(500, 510, size=(9000, 3)))

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.CONTRADICTED
    assert "bbox" in r.reason.lower()


def test_rescaled_splat_is_contradicted(tmp_path, sparse_pts):
    """nerfstudio 风格 re-center+auto_scale 后的产物不在 sparse 坐标系里。

    实测 (canary): ratio=0.00x。判 contradicted 是**事实正确**的 ——
    那个 ply 确实不在这个 sparse 的坐标系。调用方要自己知道:对重跑 COLMAP
    的云端路线, 应该根本不调用本校验 (无声称 -> 无可矛盾)。
    """
    rng = np.random.default_rng(5)
    xyz = np.repeat(sparse_pts, 3, axis=0) + rng.normal(0, 0.01, size=(9000, 3))
    centered = (xyz - xyz.mean(0)) / np.abs(xyz - xyz.mean(0)).max()
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    write_ply(tmp_path / "t.ply", centered)

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.CONTRADICTED


def test_missing_sparse_is_unknown_not_pass(tmp_path, sparse_pts):
    """拿不到 sparse -> unknown。绝不能退化成"没发现矛盾"。"""
    write_ply(tmp_path / "t.ply", sparse_pts)

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "nope.txt")

    assert r.verdict is Verdict.UNKNOWN
    assert r.signal_ratio is None


def test_unreadable_ply_is_unknown(tmp_path, sparse_pts):
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    (tmp_path / "t.ply").write_bytes(b"not a ply at all")

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.UNKNOWN


def test_colmap_bin_is_unknown_not_guessed(tmp_path, sparse_pts):
    """只实现了 points3D.txt。.bin 不猜 -> unknown (fail-closed)。"""
    write_ply(tmp_path / "t.ply", sparse_pts)
    (tmp_path / "points3D.bin").write_bytes(b"\x00" * 64)

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.bin")

    assert r.verdict is Verdict.UNKNOWN
    assert "txt" in r.reason.lower() or "bin" in r.reason.lower()


def test_too_few_points_is_unknown(tmp_path):
    """样本太少 -> 统计量无意义 -> unknown, 不做任何声称。"""
    write_points3d(tmp_path / "points3D.txt", np.random.default_rng(6).uniform(0, 10, (5, 3)))
    write_ply(tmp_path / "t.ply", np.random.default_rng(7).uniform(0, 10, (5, 3)))

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.UNKNOWN


def test_degenerate_flat_sparse_is_unknown(tmp_path):
    """共面 sparse -> bbox 体积为 0 -> null model 无定义 -> unknown。"""
    rng = np.random.default_rng(8)
    pts = np.column_stack([rng.uniform(0, 10, 3000), rng.uniform(0, 10, 3000), np.zeros(3000)])
    write_points3d(tmp_path / "points3D.txt", pts)
    write_ply(tmp_path / "t.ply", pts)

    r = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert r.verdict is Verdict.UNKNOWN


def test_result_is_deterministic(tmp_path, sparse_pts):
    """子采样必须用固定 seed —— 同输入必须同结论 (可审计)。"""
    rng = np.random.default_rng(9)
    xyz = np.repeat(sparse_pts, 3, axis=0) + rng.normal(0, 0.01, size=(9000, 3))
    write_points3d(tmp_path / "points3D.txt", sparse_pts)
    write_ply(tmp_path / "t.ply", xyz)

    a = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")
    b = check_splat_against_sparse(tmp_path / "t.ply", tmp_path / "points3D.txt")

    assert a.signal_ratio == b.signal_ratio
    assert a.verdict == b.verdict
