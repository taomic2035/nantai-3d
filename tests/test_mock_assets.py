"""P0#8 可移植性: 素材生成器确定性 + registry sha256 自校验。

保证 fresh clone 只凭 tracked 生成器 + registry.json 就能还原并验证素材库。
"""
import numpy as np

from pipeline.assets import sha256_file
from pipeline.gaussian_scene import GaussianScene
from pipeline.mock_assets import SPECS, build_scene, seed_registry


def test_seed_registry_reproducible_across_runs(tmp_path):
    """两个独立目录各生成一次, 11 个 ply 的 sha256 必须逐项一致 (跨进程确定性)。"""
    r1 = seed_registry(tmp_path / "a")
    r2 = seed_registry(tmp_path / "b")
    sha1 = {k: v.sha256 for k, v in r1.doc.assets.items()}
    sha2 = {k: v.sha256 for k, v in r2.doc.assets.items()}
    assert len(sha1) == len(SPECS) == 11
    assert sha1 == sha2
    assert all(v for v in sha1.values())


def test_registry_verify_detects_tampering(tmp_path):
    reg = seed_registry(tmp_path / "assets")
    assert reg.verify() == {}  # 刚生成, 全部一致

    # 篡改一个 ply → verify 必须报告不匹配
    victim = next(iter(reg.doc.assets.values()))
    (reg.assets_dir / victim.ply).write_bytes(b"corrupted")
    problems = reg.verify()
    assert len(problems) == 1


def test_verify_reports_missing_file(tmp_path):
    reg = seed_registry(tmp_path / "assets")
    victim_id = next(iter(reg.doc.assets))
    (reg.assets_dir / reg.doc.assets[victim_id].ply).unlink()
    assert victim_id in reg.verify()


def test_build_scene_grounded_and_nondegenerate(tmp_path):
    """每个素材落地 (z≈0)、颜色非退化、可 3dgs round-trip。"""
    for spec in SPECS:
        scene = build_scene(spec)
        assert len(scene) > 200
        lo, _ = scene.bounds()
        assert abs(lo[2]) < 1.0, f"{spec.asset_id} 未落地"
        assert float(scene.rgb.std()) > 0.01, f"{spec.asset_id} 颜色退化"
        p = tmp_path / f"{spec.asset_id}.ply"
        scene.save_ply(p, flavor="3dgs")
        reloaded = GaussianScene.load_ply(p)
        assert len(reloaded) == len(scene)
        assert sha256_file(p)  # 可计算校验值


def test_registered_registry_loads_and_instantiates(tmp_path):
    reg = seed_registry(tmp_path / "assets")
    inst = reg.instantiate("house_wood_01", pos_xy=(100.0, 50.0), rot_z_deg=45.0)
    assert inst is not None
    # 实例化后 XY 重心应落在放置点附近
    assert abs(float(np.mean(inst.xyz[:, 0])) - 100.0) < 5.0
