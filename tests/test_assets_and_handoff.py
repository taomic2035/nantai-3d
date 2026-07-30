"""素材注册表 (可替换) + GPT 交付物验收闭环"""

import hashlib
import json
import os
from pathlib import Path

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
    xyz = np.stack([rng.uniform(-4, 4, n), rng.uniform(-3, 3, n), rng.uniform(0, 5, n)], axis=1)
    rgb = np.clip(0.55 + rng.normal(0, 0.08, (n, 3)), 0, 1)
    s = GaussianScene(xyz, rgb, rng.uniform(0.5, 1, n), rng.uniform(0.02, 0.3, (n, 3)))
    p = tmp_path / "asset_src.ply"
    s.save_ply(p, flavor="3dgs")
    return p


class TestAssetRegistry:
    def test_register_and_resolve(self, tmp_path, asset_ply):
        reg = AssetRegistry(tmp_path / "assets")
        reg.register(
            "house_wood_01", asset_ply, kind="building", origin="gpt-mock", footprint_m=[8, 6, 5]
        )
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

        layout = ChunkLayout(
            **{
                "chunk_id": {"x": 0, "y": 0},
                "world_seed": 1,
                "geo_origin": {"lat": 26.0, "lon": 119.0, "alt": 50},
                "terrain": {"heightmap": "t.png", "elevation_range": [0, 10], "material_zones": []},
                "buildings": [
                    {
                        "id": "b1",
                        "asset_id": "house_wood_01",
                        "pos": [100, 100],
                        "rot_z": 0.0,
                        "scale": 1.0,
                    }
                ],
            }
        )
        # 无注册表: 合成盒子 (地面 4000 + 墙 600 + 顶 100)
        arr_synth = build_chunk_array(layout, registry=None)
        # 有注册表: 素材 2000 高斯替换盒子
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("house_wood_01", asset_ply, kind="building")
        arr_asset = build_chunk_array(layout, registry=reg)
        assert len(arr_asset) == 4000 + 2000
        assert len(arr_synth) != len(arr_asset)
        # 素材实例应落在建筑位置附近 (世界坐标 100,100)
        bx = arr_asset["x"][4000:]
        assert 90 < bx.mean() < 110


def _write_deliverable(d, items, ground_z=0.0, color_std=0.08, n=2000):
    """构造一个交付目录"""
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(5)
    manifest = {"handoff_id": "HANDOFF-T", "items": []}
    for item in items:
        w, dep, h = item.get("footprint", [8, 6, 5])
        xyz = np.stack(
            [
                rng.uniform(-w / 2, w / 2, n),
                rng.uniform(-dep / 2, dep / 2, n),
                rng.uniform(ground_z, ground_z + h, n),
            ],
            axis=1,
        )
        rgb = np.clip(0.5 + rng.normal(0, color_std, (n, 3)), 0, 1)
        s = GaussianScene(xyz, rgb, rng.uniform(0.5, 1, n), rng.uniform(0.02, 0.3, (n, 3)))
        s.save_ply(d / item["ply"], flavor="3dgs")
        manifest["items"].append(
            {
                "asset_id": item["asset_id"],
                "kind": "building",
                "ply": item["ply"],
                "footprint_m": item.get("footprint", [8, 6, 5]),
            }
        )
    (d / "manifest.json").write_text(json.dumps(manifest))


class TestHandoffValidation:
    def test_good_deliverable_passes(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(
            d, [{"asset_id": "a1", "ply": "a1.ply"}, {"asset_id": "a2", "ply": "a2.ply"}]
        )
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
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}], ground_z=5.0)  # 悬空 5 米
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]
        assert any("z=" in p for p in r["results"]["a1"])

    def test_wrong_footprint_fails(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply", "footprint": [8, 6, 5]}])
        # 篡改 manifest 声明成 30m 宽
        m = json.loads((d / "manifest.json").read_text())
        m["items"][0]["footprint_m"] = [30, 6, 5]
        (d / "manifest.json").write_text(json.dumps(m))
        r = validate(d, feedback_dir=tmp_path / "fb")
        assert not r["all_pass"]

    def test_degenerate_color_fails(self, tmp_path):
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}], color_std=0.0)
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
        manifest["generator"] = {"name": "gpt-image2", "version": "3.1"}
        manifest["items"][0]["sha256"] = _sha256(d / "a1.ply")
        manifest_path.write_text(json.dumps(manifest))
        r = validate(
            d, feedback_dir=tmp_path / "fb", do_register=True, assets_dir=tmp_path / "assets"
        )
        assert r["registered"] == ["a1"]
        reg = AssetRegistry(tmp_path / "assets")
        # origin 须诚实反映来源: --register 只落 v2 正式交付 (schema_version<2 fatal),
        # 是真实交付素材 → "real", 绝不该谎称占位 "gpt-mock"。
        assert reg.doc.assets["a1"].origin == "real"

    def test_real_deliverable_replacing_mock_bumps_version_and_origin(
        self,
        tmp_path,
        asset_ply,
    ):
        """真实素材落地实景 (即将发生): GPT 的 v2 交付替换既有 gpt-mock 占位 →
        版本升 + origin 转 real (不谎称 mock)。版本升是 chunk_content_key 缓存失效 /
        重烘拾取新素材的依据。"""
        assets_dir = tmp_path / "assets"
        reg0 = AssetRegistry(assets_dir)
        reg0.register("a1", asset_ply, kind="building", origin="gpt-mock", footprint_m=[8, 6, 5])
        assert reg0.doc.assets["a1"].version == 1
        assert reg0.doc.assets["a1"].origin == "gpt-mock"

        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}])  # 不同几何 → 内容变化
        manifest = json.loads((d / "manifest.json").read_text())
        manifest["schema_version"] = 2
        manifest["coordinate_system"] = {"units": "meters", "axes": "local-z-up"}
        manifest["generator"] = {"name": "gpt-image2", "version": "3.1"}
        manifest["items"][0]["sha256"] = _sha256(d / "a1.ply")
        (d / "manifest.json").write_text(json.dumps(manifest))

        r = validate(d, feedback_dir=tmp_path / "fb", do_register=True, assets_dir=assets_dir)
        assert r["registered"] == ["a1"]
        e = AssetRegistry(assets_dir).doc.assets["a1"]
        assert e.origin == "real", "真实交付替换 mock 后 origin 须转 real"
        assert e.version == 2, "内容变化须升版 (chunk_content_key/重烘据此拾取新素材)"

    def test_multi_asset_batch_lands_all_as_real(self, tmp_path):
        """全批真实素材落地实景 (即将发生, 真实素材可能一次交付多个): 一个 v2 交付含
        多个素材 → 逐个注册, 全部 origin=real (非谎称 mock)。锁 register 循环对批量正确。"""
        assets_dir = tmp_path / "assets"
        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": f"a{i}", "ply": f"a{i}.ply"} for i in range(4)])
        manifest = json.loads((d / "manifest.json").read_text())
        manifest["schema_version"] = 2
        manifest["coordinate_system"] = {"units": "meters", "axes": "local-z-up"}
        manifest["generator"] = {"name": "gpt-image2", "version": "3.1"}
        for item in manifest["items"]:
            item["sha256"] = _sha256(d / item["ply"])
        (d / "manifest.json").write_text(json.dumps(manifest))

        r = validate(d, feedback_dir=tmp_path / "fb", do_register=True, assets_dir=assets_dir)
        assert sorted(r["registered"]) == ["a0", "a1", "a2", "a3"]
        reg = AssetRegistry(assets_dir)
        assert all(reg.doc.assets[f"a{i}"].origin == "real" for i in range(4))
        assert all(reg.doc.assets[f"a{i}"].version == 1 for i in range(4))

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


class TestSha256FileIntegrity:
    """Security boundary tests for validate_handoff._sha256_file."""

    def test_returns_correct_digest(self, tmp_path):
        from pipeline.validate_handoff import _sha256_file

        data = b"hello world\n"
        path = tmp_path / "a.ply"
        path.write_bytes(data)
        assert _sha256_file(path) == hashlib.sha256(data).hexdigest()

    def test_rejects_ancestor_reparse(self, tmp_path, monkeypatch):
        import pipeline.validate_handoff as vh
        from pipeline.validate_handoff import _HandoffIntegrityError, _sha256_file

        target = tmp_path / "a.ply"
        target.write_bytes(b"ply data\n")
        sentinel = tmp_path / "ancestor-reparse"
        original = vh.first_linklike_path

        def fake_first_linklike_path(root, leaf):
            if Path(leaf) == target:
                return sentinel
            return original(root, leaf)

        monkeypatch.setattr(vh, "first_linklike_path", fake_first_linklike_path)
        with pytest.raises(
            _HandoffIntegrityError,
            match="regular non-link file|redirected|unsafe",
        ):
            _sha256_file(target)

    def test_rejects_path_swap_before_open(self, tmp_path, monkeypatch):
        from pipeline.validate_handoff import _HandoffIntegrityError, _sha256_file

        original_path = tmp_path / "a.ply"
        original_path.write_bytes(b"original\n")
        swap_count = 0
        original_open = os.open

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swap_count
            swap_count += 1
            if swap_count == 1:
                original_path.write_bytes(b"swapped content\n")
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swapping_open)
        with pytest.raises(
            _HandoffIntegrityError,
            match="changed before hash|changed while",
        ):
            _sha256_file(original_path)

    def test_check_item_reports_integrity_violation(self, tmp_path, monkeypatch):
        """End-to-end: a reparse-point ancestor yields a problem, not a crash."""
        import pipeline.validate_handoff as vh
        from pipeline.validate_handoff import DeliverableItem, check_item

        d = tmp_path / "deliv"
        _write_deliverable(d, [{"asset_id": "a1", "ply": "a1.ply"}])
        manifest = json.loads((d / "manifest.json").read_text())
        manifest["schema_version"] = 2
        manifest["coordinate_system"] = {"units": "meters", "axes": "local-z-up"}
        manifest["generator"] = {"name": "test", "version": "1"}
        manifest["items"][0]["sha256"] = _sha256(d / "a1.ply")
        (d / "manifest.json").write_text(json.dumps(manifest))

        item = DeliverableItem(**manifest["items"][0])
        sentinel = tmp_path / "ancestor-reparse"
        original = vh.first_linklike_path

        def fake_first_linklike_path(root, leaf):
            if Path(leaf).name == "a1.ply":
                return sentinel
            return original(root, leaf)

        monkeypatch.setattr(vh, "first_linklike_path", fake_first_linklike_path)
        problems = check_item(item, d)
        assert any("完整性校验失败" in p for p in problems)


class TestAssetRegistryReaderIntegrity:
    """Security boundary tests for assets.sha256_file and _read_doc.

    The registry carries asset sha256/version/origin and payload PLYs are
    validated by hash. A check-then-reopen (exists/is_file then read_bytes/
    sha256_file) leaves a TOCTOU window where the file can be swapped
    between validation and reading. The secure pattern binds a single
    descriptor from os.open+O_NOFOLLOW for the entire read.
    """

    def test_sha256_file_rejects_ancestor_reparse(self, tmp_path, monkeypatch):
        import pipeline.assets as assets
        from pipeline.assets import _AssetIntegrityError, sha256_file

        target = tmp_path / "asset.ply"
        target.write_bytes(b"ply data\n")
        sentinel = tmp_path / "ancestor-reparse"
        original = assets.first_linklike_path

        def fake_first_linklike_path(root, leaf):
            if Path(leaf) == target:
                return sentinel
            return original(root, leaf)

        monkeypatch.setattr(assets, "first_linklike_path", fake_first_linklike_path)
        with pytest.raises(_AssetIntegrityError, match="regular non-link file"):
            sha256_file(target)

    def test_sha256_file_rejects_path_swap_before_open(self, tmp_path, monkeypatch):
        from pipeline.assets import _AssetIntegrityError, sha256_file

        target = tmp_path / "asset.ply"
        target.write_bytes(b"original-bytes\n")
        swap_count = 0
        original_open = os.open

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swap_count
            swap_count += 1
            if swap_count == 1:
                target.write_bytes(b"swapped-bytes-payload\n")
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swapping_open)
        with pytest.raises(_AssetIntegrityError, match="changed before hash|cannot be"):
            sha256_file(target)

    def test_sha256_file_rejects_swap_during_read(self, tmp_path, monkeypatch):
        import pipeline.assets as assets
        from pipeline.assets import _AssetIntegrityError, sha256_file

        target = tmp_path / "asset.ply"
        target.write_bytes(b"asset-payload-bytes\n")
        monkeypatch.setattr(assets, "first_linklike_path", lambda root, leaf: None)
        original_lstat = Path.lstat
        swap_state = {"target_lstat_calls": 0}

        def swapping_lstat(self):
            if self == target:
                swap_state["target_lstat_calls"] += 1
                if swap_state["target_lstat_calls"] == 2:
                    target.write_bytes(b"swapped-after-read\n")
            return original_lstat(self)

        monkeypatch.setattr(Path, "lstat", swapping_lstat)
        with pytest.raises(_AssetIntegrityError, match="changed while being hashed"):
            sha256_file(target)

    def test_sha256_file_matches_stable_read(self, tmp_path):
        from pipeline.assets import sha256_file

        target = tmp_path / "asset.ply"
        payload = b"stable-asset-bytes\n"
        target.write_bytes(payload)
        assert sha256_file(target) == hashlib.sha256(payload).hexdigest()

    def test_sha256_file_rejects_symlink_target(self, tmp_path):
        from pipeline.assets import _AssetIntegrityError, sha256_file

        real = tmp_path / "real.ply"
        real.write_bytes(b"real-bytes\n")
        link = tmp_path / "link.ply"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation is unavailable")
        with pytest.raises(_AssetIntegrityError, match="regular non-link file"):
            sha256_file(link)

    def test_read_doc_loads_registry_securely(self, tmp_path):
        from pipeline.assets import AssetRegistry

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        reg = AssetRegistry(assets_dir)
        ply = tmp_path / "a.ply"
        GaussianScene(
            np.zeros((1, 3)),
            np.zeros((1, 3)),
        ).save_ply(ply, flavor="3dgs")
        import shutil

        shutil.copy(ply, assets_dir / "a1.ply")
        reg.register("a1", assets_dir / "a1.ply", origin="synthetic")
        reg.save()

        reg2 = AssetRegistry(assets_dir)
        assert "a1" in reg2.doc.assets
        assert reg2.doc.assets["a1"].sha256 == reg.doc.assets["a1"].sha256

    def test_read_doc_missing_registry_returns_empty(self, tmp_path):
        from pipeline.assets import AssetRegistry, RegistryDoc

        reg = AssetRegistry(tmp_path / "assets")
        assert reg.doc == RegistryDoc()
        assert reg._last_read_revision is None


class TestHandoffReaderIntegrity:
    """Security boundary tests for validate_handoff trust-critical readers."""

    def test_validate_does_not_use_path_read_text_for_manifest(self, tmp_path, monkeypatch):
        """RED->GREEN: validate must not use Path.read_text for manifest.json.

        The deliverable manifest declares asset paths and is a trust-critical
        input to the validator. Reading it via Path.read_text leaves a
        check-then-reopen TOCTOU window. It must go through
        _read_stable_bytes (single descriptor, identity recheck).
        """
        from pipeline.validate_handoff import validate

        # Build a minimal valid deliverable dir
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        manifest = {
            "schema_version": 1,
            "handoff_id": "h1",
            "items": [
                {
                    "asset_id": "a1",
                    "ply": "a1.ply",
                    "footprint_m": [1.0, 1.0, 1.0],
                    "ground_z_m": 0.0,
                }
            ],
        }
        (deliverable / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
        # Minimal PLY content; check_item will fail on shape, but we only
        # care that the manifest was parsed (no Path.read_text) and the
        # fatal error does NOT blame manifest.json schema/read.
        (deliverable / "a1.ply").write_bytes(b"ply bytes\n")

        def reject_read_text(*_args, **_kwargs):
            raise AssertionError("validate must not use Path.read_text for manifest.json")

        monkeypatch.setattr(Path, "read_text", reject_read_text)
        result = validate(deliverable, feedback_dir=str(tmp_path / "fb"))
        fatal = str(result.get("fatal", ""))
        # Manifest must be parsed: schema/read failures are unacceptable.
        assert "manifest.json 不符合 schema" not in fatal
        assert "无法安全读取" not in fatal

    def test_read_stable_bytes_rejects_ancestor_reparse(self, tmp_path, monkeypatch):
        """RED->GREEN: _read_stable_bytes must reject ancestor reparse points."""
        import pipeline.validate_handoff as vh
        from pipeline.validate_handoff import _read_stable_bytes

        target = tmp_path / "manifest.json"
        target.write_bytes(b'{"schema_version": 2}')
        sentinel = tmp_path / "ancestor-reparse"

        def fake_first_linklike_path(root, leaf):
            if Path(leaf) == target:
                return sentinel
            return vh.first_linklike_path(root, leaf)

        monkeypatch.setattr(vh, "first_linklike_path", fake_first_linklike_path)
        from pipeline.validate_handoff import _HandoffIntegrityError

        with pytest.raises(_HandoffIntegrityError, match="regular file|inspected"):
            _read_stable_bytes(target, label="manifest.json")
