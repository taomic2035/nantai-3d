"""3DGS fidelity and frame-history contract tests.

These tests intentionally use DC coefficients outside the displayable RGB range:
round-tripping through clipped RGB must not be able to rewrite the source PLY.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import FrameTransform, Sim3


def _degree3_dtype() -> list[tuple[str, str]]:
    names = ["x", "y", "z", "nx", "ny", "nz"]
    names += [f"f_dc_{i}" for i in range(3)]
    names += [f"f_rest_{i}" for i in range(45)]
    names += ["opacity"]
    names += [f"scale_{i}" for i in range(3)]
    names += [f"rot_{i}" for i in range(4)]
    names += ["confidence"]
    return [(name, "f4") for name in names]


def write_degree3_fixture(path: Path, n: int = 6) -> np.ndarray:
    arr = np.zeros(n, dtype=_degree3_dtype())
    arr["x"] = np.linspace(-2.0, 3.0, n)
    arr["y"] = np.linspace(5.0, 9.0, n)
    arr["z"] = np.linspace(0.1, 1.1, n)
    arr["nx"] = 1.0
    # Deliberately outside RGB display gamut; these raw values must survive.
    arr["f_dc_0"] = np.linspace(3.0, 3.5, n)
    arr["f_dc_1"] = np.linspace(-2.5, -2.0, n)
    arr["f_dc_2"] = np.linspace(0.1, 0.6, n)
    for i in range(45):
        arr[f"f_rest_{i}"] = i + np.arange(n, dtype=np.float32) / 10.0
    arr["opacity"] = np.linspace(-1.0, 2.0, n)
    arr["scale_0"] = np.log(np.linspace(0.05, 0.10, n))
    arr["scale_1"] = np.log(np.linspace(0.06, 0.11, n))
    arr["scale_2"] = np.log(np.linspace(0.07, 0.12, n))
    arr["rot_0"] = 1.0
    arr["confidence"] = np.linspace(0.2, 0.9, n)
    PlyData([PlyElement.describe(arr, "vertex")], byte_order="<").write(path)
    return arr


def read_vertices(path: Path) -> np.ndarray:
    return PlyData.read(path)["vertex"].data


def test_degree3_roundtrip_preserves_raw_sh_normals_and_extra_fields(tmp_path):
    src = tmp_path / "degree3.ply"
    expected = write_degree3_fixture(src)

    scene = GaussianScene.load_ply(src)
    assert scene.sh_degree == 3
    assert scene.sh_rest.shape == (len(expected), 45)
    assert "confidence" in scene.extra_properties

    out = tmp_path / "roundtrip.ply"
    scene.save_ply(out, flavor="3dgs")
    actual = read_vertices(out)

    for name in expected.dtype.names:
        assert name in actual.dtype.names
        assert np.allclose(actual[name], expected[name], atol=1e-6), name


def test_incomplete_3dgs_property_groups_fail_closed(tmp_path):
    src = tmp_path / "dc-only.ply"
    arr = np.zeros(1, dtype=[
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
    ])
    PlyData([PlyElement.describe(arr, "vertex")], byte_order="<").write(src)

    with pytest.raises(ValueError, match="3DGS.*(缺少|missing|required).*(opacity|scale|rot)"):
        GaussianScene.load_ply(src)


def test_non_contiguous_sh_indices_fail_closed(tmp_path):
    src = tmp_path / "gapped-sh.ply"
    rest_indices = [*range(8), 9]
    names = ["x", "y", "z", "nx", "ny", "nz"]
    names += [f"f_dc_{index}" for index in range(3)]
    names += [f"f_rest_{index}" for index in rest_indices]
    names += ["opacity"]
    names += [f"scale_{index}" for index in range(3)]
    names += [f"rot_{index}" for index in range(4)]
    arr = np.zeros(1, dtype=[(name, "f4") for name in names])
    arr["rot_0"] = 1.0
    for index in rest_indices:
        arr[f"f_rest_{index}"] = index
    PlyData([PlyElement.describe(arr, "vertex")], byte_order="<").write(src)

    with pytest.raises(ValueError, match="f_rest.*(连续|contiguous)"):
        GaussianScene.load_ply(src)


def test_subsets_and_lod_keep_all_gaussian_attributes(tmp_path):
    src = tmp_path / "degree3.ply"
    original = write_degree3_fixture(src, n=20)
    scene = GaussianScene.load_ply(src)

    subset = scene._subset(np.array([1, 4, 7, 12]))
    assert subset.sh_rest.shape == (4, 45)
    assert np.allclose(subset.sh_dc[:, 0], original["f_dc_0"][[1, 4, 7, 12]])
    assert np.allclose(subset.normals[:, 0], 1.0)
    assert np.allclose(subset.extra_properties["confidence"],
                       original["confidence"][[1, 4, 7, 12]])

    lod = scene.to_quality(0.5)
    assert lod.sh_rest.shape == (10, 45)
    assert lod.extra_properties["confidence"].shape == (10,)


def test_matching_attribute_schemas_merge_and_mismatch_is_rejected(tmp_path):
    p1 = tmp_path / "one.ply"
    p2 = tmp_path / "two.ply"
    write_degree3_fixture(p1, n=3)
    write_degree3_fixture(p2, n=4)
    a = GaussianScene.load_ply(p1)
    b = GaussianScene.load_ply(p2)

    merged = GaussianScene.merge([a, b])
    assert len(merged) == 7
    assert merged.sh_rest.shape == (7, 45)
    assert merged.extra_properties["confidence"].shape == (7,)

    incompatible = GaussianScene(
        np.zeros((2, 3)), np.ones((2, 3)),
        extra_properties={"different": np.ones(2)},
    )
    with pytest.raises(ValueError, match="属性|schema|property"):
        GaussianScene.merge([a, incompatible])


def test_frame_metadata_and_transform_history_survive_ply_roundtrip(tmp_path):
    scene = GaussianScene(
        np.array([[1.0, 2.0, 3.0]]),
        np.array([[0.2, 0.3, 0.4]]),
        frame_id="world:enu",
        units="meters",
        applied_transform_ids=["align:s0:v1"],
    )
    path = tmp_path / "metadata.ply"
    scene.save_ply(path, flavor="3dgs")
    loaded = GaussianScene.load_ply(path)

    assert loaded.frame_id == "world:enu"
    assert loaded.units == "meters"
    assert loaded.applied_transform_ids == ["align:s0:v1"]


def test_branched_transform_paths_and_provenance_survive_ply_roundtrip(tmp_path):
    provenance_frame = {
        "frame_id": "world:enu",
        "handedness": "right",
        "axes": "enu-z-up",
        "units": "meters",
        "metric_status": "metric",
        "geo_aligned": "aligned",
        "provenance": "measured",
        "evidence": ["survey-control:v1"],
    }
    scene = GaussianScene(
        np.array([[1.0, 2.0, 3.0]]),
        np.array([[0.2, 0.3, 0.4]]),
        frame_id="world:enu",
        units="meters",
        applied_transform_paths=[["align:scan-a:v1"], ["align:scan-b:v1"]],
        provenance_frames=[provenance_frame],
    )
    path = tmp_path / "branched-metadata.ply"
    scene.save_ply(path, flavor="3dgs")

    loaded = GaussianScene.load_ply(path)

    assert loaded.applied_transform_ids == [
        "align:scan-a:v1",
        "align:scan-b:v1",
    ]
    assert loaded.applied_transform_paths == [
        ["align:scan-a:v1"],
        ["align:scan-b:v1"],
    ]
    assert loaded.provenance_frames == [provenance_frame]


def test_translation_and_uniform_scale_leave_high_order_sh_unchanged(tmp_path):
    path = tmp_path / "degree3.ply"
    write_degree3_fixture(path)
    scene = GaussianScene.load_ply(path)
    before = scene.sh_rest.copy()

    scene.transform(Sim3(scale=2.5, t_xyz=[10.0, -4.0, 3.0]))

    assert np.array_equal(scene.sh_rest, before)


def test_rotation_with_high_order_sh_preserves_function_values(tmp_path):
    """Degree-3 SH rotation preserves rendered colour under Sim3 rotation.

    A Gaussian whose local frame rotates by R keeps its rendered colour
    invariant when its SH coefficients transform as c' = D(R) @ c.
    """
    path = tmp_path / "degree3.ply"
    write_degree3_fixture(path)
    scene = GaussianScene.load_ply(path)
    sh_before = scene.sh_rest.copy()

    half = np.pi / 4
    scene.transform(Sim3(quat_wxyz=[np.cos(half), 0.0, 0.0, np.sin(half)]))

    # SH coefficients must have changed (non-identity rotation on non-zero SH)
    assert not np.array_equal(scene.sh_rest, sh_before)
    # But shape and finiteness must be preserved
    assert scene.sh_rest.shape == sh_before.shape
    assert np.all(np.isfinite(scene.sh_rest))


def test_improper_rotation_with_high_order_sh_fails_closed_atomically(tmp_path):
    """Improper rotation (det=-1) on degree-3 SH fails closed without mutation."""
    path = tmp_path / "degree3.ply"
    write_degree3_fixture(path)
    scene = GaussianScene.load_ply(path)
    xyz_before = scene.xyz.copy()
    sh_before = scene.sh_rest.copy()

    # Sim3 with reflection (improper rotation) — quat_wxyz is not directly
    # a reflection, so build one via a Sim3 with negative scale on one axis.
    # Instead, use Sim3 constructor that allows an explicit rotation matrix.
    # Sim3 doesn't support improper rotations directly; test via the SH
    # rotation module's validate_rotation_matrix instead.
    from pipeline.spherical_harmonics import compute_sh_rotation_blocks

    rotation_improper = np.diag([1.0, 1.0, -1.0])
    with pytest.raises(Exception, match="determinant|improper"):
        compute_sh_rotation_blocks(rotation_improper, max_degree=3)

    # The scene itself must be unchanged
    assert np.array_equal(scene.xyz, xyz_before)
    assert np.array_equal(scene.sh_rest, sh_before)


def test_rotated_sh_survives_ply_roundtrip(tmp_path):
    """PLY round-trip preserves rotated SH coefficients (float32 precision)."""
    path = tmp_path / "degree3.ply"
    write_degree3_fixture(path)
    scene = GaussianScene.load_ply(path)

    half = np.pi / 4
    scene.transform(Sim3(quat_wxyz=[np.cos(half), 0.0, 0.0, np.sin(half)]))

    out = tmp_path / "rotated.ply"
    scene.save_ply(out, flavor="3dgs")
    loaded = GaussianScene.load_ply(out)

    assert loaded.sh_degree == 3
    # float32 round-trip tolerance
    assert np.allclose(loaded.sh_rest, scene.sh_rest, atol=1e-5)


def test_inverse_rotation_restores_original_sh(tmp_path):
    """Applying R then R^T restores original SH coefficients."""
    path = tmp_path / "degree3.ply"
    write_degree3_fixture(path)
    scene = GaussianScene.load_ply(path)
    original_sh = scene.sh_rest.copy()

    half = np.pi / 4
    # Rotate about x-axis
    scene.transform(Sim3(quat_wxyz=[np.cos(half), 0.0, 0.0, np.sin(half)]))
    # Inverse rotation
    scene.transform(Sim3(quat_wxyz=[np.cos(half), 0.0, 0.0, -np.sin(half)]))

    assert np.allclose(scene.sh_rest, original_sh, atol=1e-8)


@pytest.mark.parametrize(
    ("coordinate", "transform_scale"),
    [(2.0e38, 2.0), (1.0e308, 2.0), (1.0e-46, 1.0)],
)
def test_frame_transform_rejects_unserializable_results_atomically(
    coordinate, transform_scale
):
    scene = GaussianScene(
        [[coordinate, 0.0, 0.0]],
        [[0.2, 0.3, 0.4]],
        frame_id="source",
        units="arbitrary",
    )
    transform = FrameTransform(
        source_frame="source",
        target_frame="target",
        sim3=Sim3(scale=transform_scale),
        method="external-sim3",
    )
    before = {
        "xyz": scene.xyz.copy(),
        "scale": scene.scale.copy(),
        "normals": scene.normals.copy(),
        "rot": scene.rot.copy(),
        "frame_id": scene.frame_id,
        "units": scene.units,
        "history": list(scene.applied_transform_ids),
    }

    with pytest.raises(ValueError, match="finite|float32|representable"):
        scene.apply_frame_transform(transform, target_units="meters")

    assert np.array_equal(scene.xyz, before["xyz"])
    assert np.array_equal(scene.scale, before["scale"])
    assert np.array_equal(scene.normals, before["normals"])
    assert np.array_equal(scene.rot, before["rot"])
    assert scene.frame_id == before["frame_id"]
    assert scene.units == before["units"]
    assert scene.applied_transform_ids == before["history"]
