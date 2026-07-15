"""素材注册表 (可替换) + GPT 交付物验收闭环"""
import hashlib
import json

import numpy as np
import pytest

from pipeline.assets import AssetRegistry
from pipeline.gaussian_scene import GaussianScene
from pipeline.validate_handoff import validate


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def asset_ply(tmp_path):
    """一个 8x6x5m 的合成建筑素材 (局部坐标, 地面 z=0)"""
    rng = np.random.default_rng(11)
    n = 2000
    xyz = np.stack([rng.uniform(-4, 4, n), rng.uniform(-3, 3, n),
                    rng.uniform(0, 5, n)], axis=1)
    rgb = np.clip(0.55 + rng.normal(0, 0.08, (n, 3)), 0, 1)
    s = GaussianScene(xyz, rgb, rng.uniform(0.5, 1, n),
                      rng.uniform(0.02, 0.3, (n, 3)))
    p = tmp_path / "asset_src.ply"
    s.save_ply(p, flavor="3dgs")
    return p


class TestAssetRegistry:
    def test_register_and_resolve(self, tmp_path, asset_ply):
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("house_wood_01", asset_ply, kind="building",
                     origin="gpt-mock", footprint_m=[8, 6, 5])
        assert reg.resolve("house_wood_01").name == "house_wood_01_v1.ply"
        assert reg.resolve("nonexistent") is None

    def test_replace_bumps_version_keeps_history(self, tmp_path, asset_ply):
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("h", asset_ply)
        reg.replace("h", asset_ply, origin="real")
        e = reg.doc.assets["h"]
        assert e.version == 2
        assert [item.ply for item in e.history] == ["h_v1.ply"]
        assert e.origin == "real"
        # 持久化后重新加载仍是 v2
        reg2 = AssetRegistry(tmp_path / "assets")
        assert reg2.doc.assets["h"].version == 2

    def test_replace_unknown_raises(self, tmp_path, asset_ply):
        reg = AssetRegistry(tmp_path / "assets")
        with pytest.raises(KeyError):
            reg.replace("ghost", asset_ply)

    def test_instantiate_places_at_world_pos(self, tmp_path, asset_ply):
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("h", asset_ply)
        inst = reg.instantiate("h", pos_xy=(150, 60), rot_z_deg=90, scale=2.0)
        assert abs(inst.xyz[:, 0].mean() - 150) < 1.0
        assert abs(inst.xyz[:, 1].mean() - 60) < 1.0
        # 90° 旋转 + 2 倍缩放: 原 8m 宽(X) 变为 Y 向 16m
        y_extent = inst.xyz[:, 1].max() - inst.xyz[:, 1].min()
        assert 14 < y_extent < 18
        assert inst.xyz[:, 2].min() >= -0.01  # 仍落地

    def test_missing_asset_returns_none(self, tmp_path):
        reg = AssetRegistry(tmp_path / "assets")
        assert reg.instantiate("ghost", (0, 0)) is None


class TestRendererUsesRegistry:
    def test_building_rendered_from_registered_asset(self, tmp_path, asset_ply):
        """建筑素材注册后, chunk 渲染应实例化素材而非合成盒子"""
        from pipeline.render_chunk_to_ply import build_chunk_array
        from pipeline.schema import ChunkLayout

        layout = ChunkLayout(**{
            "chunk_id": {"x": 0, "y": 0}, "world_seed": 1,
            "geo_origin": {"lat": 26.0, "lon": 119.0, "alt": 50},
            "terrain": {"heightmap": "t.png", "elevation_range": [0, 10],
                        "material_zones": []},
            "buildings": [{"id": "b1", "asset_id": "house_wood_01",
                           "pos": [100, 100], "rot_z": 0.0, "scale": 1.0}],
        })
        # 无注册表: 合成盒子 (地面 4000 + 墙 600 + 顶 100)
        arr_synth = build_chunk_array(layout, registry=None)
        # 有注册表: 素材 2000 高斯替换盒子
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("house_wood_01", asset_ply, kind="building")
        arr_asset = build_chunk_array(layout, registry=reg)
        assert len(arr_asset) == 4000 + 2000
        assert len(arr_synth) != len(arr_asset)
        # 素材实例应落在建筑位置附近 (世界坐标 100,100)
        bx = arr_asset['x'][4000:]
        assert 90 < bx.mean() < 110


def _write_deliverable(d, items, ground_z=0.0, color_std=0.08, n=2000):
    """构造一个交付目录"""
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(5)
    manifest = {"handoff_id": "HANDOFF-T", "items": []}
    for item in items:
        w, dep, h = item.get("footprint", [8, 6, 5])
        xyz = np.stack([rng.uniform(-w / 2, w / 2, n),
                        rng.uniform(-dep / 2, dep / 2, n),
                        rng.uniform(ground_z, ground_z + h, n)], axis=1)
        rgb = np.clip(0.5 + rng.normal(0, color_std, (n, 3)), 0, 1)
        s = GaussianScene(xyz, rgb, rng.uniform(0.5, 1, n),
                          rng.uniform(0.02, 0.3, (n, 3)))
        s.save_ply(d / item["ply"], flavor="3dgs")
        manifest["items"].append({
            "asset_id": item["asset_id"], "kind": "building",
            "ply": item["ply"], "footprint_m": item.get("footprint", [8, 6, 5]),
        })
    (d / "manifest.json").write_text(json.dumps(manifest))


class TestHandoffValidation:
    def test_good_deliverable_passes(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"},
                               {"asset_id": "a2", "ply": "a2.ply"}])
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert r["all_pass"] and r["n_pass"] == 2
        fb = (tmp_path / "fb" / "FEEDBACK-HANDOFF-T.md").read_text(encoding="utf-8")
        assert "全部通过" in fb

    def test_missing_ply_fails(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}])
        (d / "a1.ply").unlink()
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]
        assert "缺失" in r["results"]["a1"][0]

    def test_floating_asset_fails(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}],
                           ground_z=5.0)  # 悬空 5 米
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]
        assert any("z=" in p for p in r["results"]["a1"])

    def test_wrong_footprint_fails(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply",
                                "footprint": [8, 6, 5]}])
        # 篡改 manifest 声明成 30m 宽
        m = json.loads((d / "manifest.json").read_text())
        m["items"][0]["footprint_m"] = [30, 6, 5]
        (d / "manifest.json").write_text(json.dumps(m))
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]

    def test_degenerate_color_fails(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}],
                           color_std=0.0)
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]
        assert any("颜色退化" in p for p in r["results"]["a1"])

    def test_missing_manifest_is_fatal(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"] and r["fatal"]

    def test_register_after_pass(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}])
        manifest_path = d / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 2
        manifest["coordinate_system"] = {
            "units": "meters",
            "axes": "local-z-up",
        }
        manifest["generator"] = {"name": "test", "version": "1"}
        manifest["items"][0]["sha256"] = _sha256(d / "a1.ply")
        manifest_path.write_text(json.dumps(manifest))
        r = validate(d, feedback_dir=tmp_path / "fb",
                     do_register=True, assets_dir=tmp_path / "assets")
        assert r["registered"] == ["a1"]
        reg = AssetRegistry(tmp_path / "assets")
        assert reg.doc.assets["a1"].origin == "gpt-mock"

    def test_v2_manifest_requires_sha_for_every_item(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}])
        manifest = json.loads((d / "manifest.json").read_text())
        manifest["schema_version"] = 2
        manifest["coordinate_system"] = {
            "units": "meters",
            "axes": "local-z-up",
        }
        manifest["generator"] = {"name": "test", "version": "1"}
        (d / "manifest.json").write_text(json.dumps(manifest))

        result = validate(d, feedback_dir=tmp_path / "fb")

        assert not result["all_pass"]
        assert result["fatal"] and "sha256" in result["fatal"]

    def test_validation_preserves_manual_feedback_tail(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}])
        feedback_dir = tmp_path / "fb"
        feedback_dir.mkdir()
        feedback = feedback_dir / "FEEDBACK-HANDOFF-T.md"
        feedback.write_text(
            "# stale generated content\n\n## 人工备注\n\n- keep this handoff evidence\n",
            encoding="utf-8",
        )

        validate(d, feedback_dir=feedback_dir)

        refreshed = feedback.read_text(encoding="utf-8")
        assert "验收结果: ✅ 全部通过" in refreshed
        assert refreshed.count("## 人工备注") == 1
        assert "keep this handoff evidence" in refreshed
