"""
素材注册表: 可替换的 3DGS 资产管理

- assets/registry.json 记录 asset_id → ply 文件 / 版本 / 来源
- replace() 原子替换素材并保留历史版本 → 布局 JSON 不变, 重渲染即生效
- instantiate() 把素材放置到世界坐标 (旋转/缩放/平移), 供 chunk 渲染直接拼接
- 素材来源: synthetic (内置合成) / gpt-mock (GPT 生成模拟) / real (真实重建)

素材 ply 作者约定 (handoff 文档同步此约定):
- 局部坐标系: Z 向上, 米制, XY 原点在素材水平中心, 地面 z=0
- 加载时默认 normalize 兜底 (重心归零 + 落地), 容忍不完全规范的交付物
"""
import hashlib
import json
import shutil
from pathlib import Path
from typing import Literal

import numpy as np
from loguru import logger
from pydantic import BaseModel, Field

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import Sim3

DEFAULT_ASSETS_DIR = "assets"
REGISTRY_FILE = "registry.json"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


class AssetEntry(BaseModel):
    kind: Literal["building", "vegetation", "prop", "ground", "other"] = "other"
    ply: str  # 相对 assets 目录
    version: int = Field(default=1, ge=1)
    origin: Literal["synthetic", "gpt-mock", "real"] = "synthetic"
    footprint_m: list[float] | None = None  # [宽, 深, 高] 名义尺寸
    history: list[str] = []  # 被替换下来的历史 ply 文件名
    sha256: str | None = None  # 当前 ply 内容校验 (可移植性: fresh clone 重建后自验)


class RegistryDoc(BaseModel):
    schema_version: int = 1
    assets: dict[str, AssetEntry] = {}


class AssetRegistry:
    """素材注册表 (目录 + registry.json)"""

    def __init__(self, assets_dir: str | Path = DEFAULT_ASSETS_DIR):
        self.assets_dir = Path(assets_dir)
        self.registry_path = self.assets_dir / REGISTRY_FILE
        if self.registry_path.exists():
            self.doc = RegistryDoc(**json.loads(
                self.registry_path.read_text(encoding="utf-8")))
        else:
            self.doc = RegistryDoc()

    def save(self) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            self.doc.model_dump_json(indent=2), encoding="utf-8")

    # ============ 注册 / 替换 ============
    def register(self, asset_id: str, ply_path: str | Path,
                 kind: str = "other", origin: str = "synthetic",
                 footprint_m: list[float] | None = None) -> AssetEntry:
        """注册新素材: 把 ply 拷入 assets 目录, 版本从 1 开始。
        已存在同 id 时等价于 replace()。
        """
        if asset_id in self.doc.assets:
            return self.replace(asset_id, ply_path, origin=origin)

        self.assets_dir.mkdir(parents=True, exist_ok=True)
        dst_name = f"{asset_id}_v1.ply"
        shutil.copy2(ply_path, self.assets_dir / dst_name)
        entry = AssetEntry(kind=kind, ply=dst_name, version=1,
                           origin=origin, footprint_m=footprint_m,
                           sha256=sha256_file(self.assets_dir / dst_name))
        self.doc.assets[asset_id] = entry
        self.save()
        logger.info(f"素材注册: {asset_id} v1 ({origin}) ← {ply_path}")
        return entry

    def replace(self, asset_id: str, new_ply_path: str | Path,
                origin: str = "real") -> AssetEntry:
        """替换素材: 版本 +1, 旧 ply 存档进 history。
        引用该 asset_id 的所有布局无需改动, 重渲染即用新素材。
        """
        if asset_id not in self.doc.assets:
            raise KeyError(f"素材不存在, 无法替换: {asset_id} (先 register)")
        entry = self.doc.assets[asset_id]
        entry.history.append(entry.ply)
        entry.version += 1
        entry.origin = origin  # type: ignore[assignment]
        dst_name = f"{asset_id}_v{entry.version}.ply"
        shutil.copy2(new_ply_path, self.assets_dir / dst_name)
        entry.ply = dst_name
        entry.sha256 = sha256_file(self.assets_dir / dst_name)
        self.save()
        logger.info(f"素材替换: {asset_id} → v{entry.version} ({origin})")
        return entry

    # ============ 解析 / 实例化 ============
    def resolve(self, asset_id: str) -> Path | None:
        entry = self.doc.assets.get(asset_id)
        if entry is None:
            return None
        p = self.assets_dir / entry.ply
        return p if p.exists() else None

    def load_scene(self, asset_id: str, normalize: bool = True) -> GaussianScene | None:
        """加载素材为 GaussianScene (局部坐标); 找不到返回 None (调用方降级)"""
        p = self.resolve(asset_id)
        if p is None:
            return None
        scene = GaussianScene.load_ply(p)
        if normalize and len(scene) > 0:
            # 兜底规范化: XY 重心归零, 最低点落地 z=0
            center = scene.xyz.mean(axis=0)
            scene.xyz[:, 0] -= center[0]
            scene.xyz[:, 1] -= center[1]
            scene.xyz[:, 2] -= scene.xyz[:, 2].min()
        return scene

    def instantiate(self, asset_id: str, pos_xy, rot_z_deg: float = 0.0,
                    scale: float = 1.0, z: float = 0.0) -> GaussianScene | None:
        """素材 → 世界坐标实例 (旋转/缩放/平移), 供场景拼接"""
        scene = self.load_scene(asset_id)
        if scene is None:
            return None
        half = np.radians(rot_z_deg) / 2.0
        sim3 = Sim3(
            scale=scale,
            quat_wxyz=[float(np.cos(half)), 0.0, 0.0, float(np.sin(half))],
            t_xyz=[float(pos_xy[0]), float(pos_xy[1]), float(z)],
        )
        return scene.transform(sim3)

    def list_assets(self) -> dict[str, AssetEntry]:
        return dict(self.doc.assets)

    def verify(self) -> dict[str, str]:
        """校验磁盘上每个素材 ply 与 registry 记录的 sha256 一致。
        返回 {asset_id: 问题描述} (空 = 全部一致); 用于 fresh clone 重建后的可移植性自检。
        """
        problems: dict[str, str] = {}
        for aid, entry in self.doc.assets.items():
            p = self.assets_dir / entry.ply
            if not p.exists():
                problems[aid] = f"缺失 {entry.ply} (运行 make assets 重建)"
            elif entry.sha256 and sha256_file(p) != entry.sha256:
                problems[aid] = f"{entry.ply} sha256 不匹配 (素材被改动或生成器漂移)"
        return problems


if __name__ == "__main__":
    # 自验证: 注册 → 解析 → 替换 → 版本递增
    import tempfile

    rng = np.random.default_rng(1)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        s = GaussianScene(rng.uniform(-3, 3, (100, 3)), rng.uniform(0, 1, (100, 3)))
        src = d / "src.ply"
        s.save_ply(src, flavor="3dgs")

        reg = AssetRegistry(d / "assets")
        reg.register("house_test", src, kind="building", origin="gpt-mock",
                     footprint_m=[8, 6, 6])
        assert reg.resolve("house_test") is not None
        inst = reg.instantiate("house_test", pos_xy=(50, 60), rot_z_deg=90, scale=2.0)
        assert inst is not None and len(inst) == 100
        assert abs(inst.xyz[:, 0].mean() - 50) < 1.0

        reg.replace("house_test", src, origin="real")
        e = reg.doc.assets["house_test"]
        assert e.version == 2 and len(e.history) == 1

        reg2 = AssetRegistry(d / "assets")  # 重新加载持久化
        assert reg2.doc.assets["house_test"].version == 2
    print("[OK] assets 自验证通过")
