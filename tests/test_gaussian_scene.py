"""GaussianScene: IO roundtrip / 变换 / 拼接 / 去重 / 区域替换 / LOD"""
import numpy as np
import pytest

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import Sim3


class TestPlyIO:
    @pytest.mark.parametrize("flavor", ["simple", "3dgs"])
    def test_roundtrip_positions(self, small_scene, tmp_path, flavor):
        p = tmp_path / f"s_{flavor}.ply"
        small_scene.save_ply(p, flavor=flavor)
        loaded = GaussianScene.load_ply(p)
        assert len(loaded) == len(small_scene)
        assert np.allclose(loaded.xyz, small_scene.xyz, atol=1e-3)

    def test_3dgs_roundtrip_preserves_appearance(self, small_scene, tmp_path):
        p = tmp_path / "s.ply"
        small_scene.save_ply(p, flavor="3dgs")
        loaded = GaussianScene.load_ply(p)
        assert np.allclose(loaded.rgb, small_scene.rgb, atol=5e-3)
        assert np.allclose(loaded.opacity, small_scene.opacity, atol=1e-3)
        assert np.allclose(loaded.scale, small_scene.scale, rtol=1e-3)
        assert np.allclose(loaded.rot, small_scene.rot, atol=1e-3)

    def test_simple_flavor_quantizes_color(self, small_scene, tmp_path):
        p = tmp_path / "s.ply"
        small_scene.save_ply(p, flavor="simple")
        loaded = GaussianScene.load_ply(p)
        assert np.allclose(loaded.rgb, small_scene.rgb, atol=1 / 255 + 1e-6)

    def test_unknown_properties_rejected(self, tmp_path):
        # 手工构造一个只有 x,y,z 的 ply → 无法识别应报错
        from plyfile import PlyData, PlyElement
        arr = np.zeros(10, dtype=[('x', 'f4'), ('y', 'f4'), ('z', 'f4')])
        p = tmp_path / "bad.ply"
        PlyData([PlyElement.describe(arr, 'vertex')]).write(str(p))
        with pytest.raises(ValueError, match="无法识别"):
            GaussianScene.load_ply(p)


class TestTransform:
    def test_translation_and_scale(self, small_scene):
        orig = small_scene.xyz.copy()
        small_scene.transform(Sim3(scale=2.0, t_xyz=[100, -5, 3]))
        assert np.allclose(small_scene.xyz, orig * 2 + [100, -5, 3])

    def test_rotation_90deg_z(self):
        s = GaussianScene(np.array([[1.0, 0, 0]]), np.array([[1.0, 0, 0]]))
        half = np.pi / 4  # 90° 绕 Z
        s.transform(Sim3(quat_wxyz=[np.cos(half), 0, 0, np.sin(half)]))
        assert np.allclose(s.xyz[0], [0, 1, 0], atol=1e-9)

    def test_scale_applies_to_gaussian_size(self, small_scene):
        orig = small_scene.scale.copy()
        small_scene.transform(Sim3(scale=3.0))
        assert np.allclose(small_scene.scale, orig * 3)

    def test_transform_records_anonymous_history(self, small_scene):
        # An untracked transform must leave an honest trace in the history so it
        # cannot masquerade as a never-moved scene.
        assert small_scene.applied_transform_ids == []
        small_scene.transform(Sim3(scale=2.0, t_xyz=[3, 0, 0]))
        ids = small_scene.applied_transform_ids
        assert len(ids) == 1
        assert ids[0].startswith("anon-")
        # id/path union stay consistent so PLY metadata still roundtrips.
        assert small_scene.applied_transform_paths == [[ids[0]]]

    def test_anonymous_history_fails_closed_validation(self, small_scene):
        # The anon entry has no auditable FrameTransform definition, so the
        # provenance gate now rejects the moved scene instead of passing an
        # empty history "by convention".
        from pipeline.reconstruct import _validate_scene_history
        small_scene.transform(Sim3(scale=2.0))
        with pytest.raises(ValueError, match="auditable transform definition"):
            _validate_scene_history(small_scene, {}, label="asset")

    def test_repeated_transform_records_distinct_history(self, small_scene):
        # Reapplying even an identical Sim3 must not collide into a duplicate id.
        small_scene.transform(Sim3(scale=2.0))
        small_scene.transform(Sim3(scale=2.0))
        ids = small_scene.applied_transform_ids
        assert len(ids) == len(set(ids)) == 2
        assert all(tid.startswith("anon-") for tid in ids)

    def test_failed_transform_leaves_history_untouched(self):
        # High-order SH rotation fails closed; history must stay empty too.
        from pipeline.gaussian_scene import GaussianScene
        sh_rest = np.arange(24, dtype=np.float64).reshape(1, 24)
        s = GaussianScene([[1.0, 0, 0]], [[0.5, 0.5, 0.5]], sh_rest=sh_rest)
        assert s.sh_degree > 0
        half = np.pi / 4
        with pytest.raises(ValueError, match="SH|球谐|rotation"):
            s.transform(Sim3(quat_wxyz=[np.cos(half), 0, 0, np.sin(half)]))
        assert s.applied_transform_ids == []
        assert s.applied_transform_paths == []


class TestMergeAndStitch:
    def test_merge_concatenates(self, small_scene):
        other = GaussianScene(small_scene.xyz + 100, small_scene.rgb)
        m = GaussianScene.merge([small_scene, other])
        assert len(m) == 1000

    def test_merge_empty_list(self):
        m = GaussianScene.merge([])
        assert len(m) == 0

    def test_dedup_removes_overlap(self, small_scene):
        # 同一场景拼接自身 → 每个体素只留一个
        dup = GaussianScene(small_scene.xyz.copy(), small_scene.rgb.copy(),
                            small_scene.opacity.copy(), small_scene.scale.copy(),
                            small_scene.rot.copy())
        m = GaussianScene.merge([small_scene, dup], dedup_voxel=0.05)
        assert len(m) <= len(small_scene)

    def test_replace_region_swaps_content(self):
        rng = np.random.default_rng(3)
        base = GaussianScene(rng.uniform(0, 100, (2000, 3)),
                             np.full((2000, 3), 0.3))
        # 新重建覆盖 [40,60]x[40,60] 区域
        new = GaussianScene(rng.uniform(40, 60, (500, 3)),
                            np.full((500, 3), 0.9))
        out = base.replace_region(new, margin=0.0)
        # 替换区域 = 新重建的实际 XY 包围盒
        lo = new.xyz[:, :2].min(axis=0)
        hi = new.xyz[:, :2].max(axis=0)
        in_region = ((out.xyz[:, 0] >= lo[0]) & (out.xyz[:, 0] < hi[0]) &
                     (out.xyz[:, 1] >= lo[1]) & (out.xyz[:, 1] < hi[1]))
        # 区域内的旧高斯 (暗色) 应全被剔除, 只剩新重建 (亮色)
        assert np.all(out.rgb[in_region] > 0.5)
        n_removed = int(np.sum(
            (base.xyz[:, 0] >= lo[0]) & (base.xyz[:, 0] < hi[0])
            & (base.xyz[:, 1] >= lo[1]) & (base.xyz[:, 1] < hi[1])))
        assert len(out) == 2000 - n_removed + 500

    def test_crop_aabb(self, small_scene):
        c = small_scene.crop_aabb([0, 0], [5, 5])
        assert np.all(c.xyz[:, 0] < 5) and np.all(c.xyz[:, 1] < 5)


class TestQualityLevels:
    def test_to_quality_fraction(self, small_scene):
        assert len(small_scene.to_quality(0.1)) == 50
        assert len(small_scene.to_quality(1.0)) == 500

    def test_quality_keeps_most_important(self, small_scene):
        sub = small_scene.to_quality(0.1)
        thresh = np.sort(small_scene.importance())[-50]
        assert np.all(sub.importance() >= thresh - 1e-12)

    def test_export_lod_files(self, small_scene, tmp_path):
        files = small_scene.export_lod(tmp_path, "test")
        assert set(files) == {0, 1, 2}
        counts = {}
        for level, fname in files.items():
            loaded = GaussianScene.load_ply(tmp_path / fname)
            counts[level] = len(loaded)
        assert counts[0] < counts[1] < counts[2] == 500
