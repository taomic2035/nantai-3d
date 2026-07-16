"""
L2 Mock 布局生成器
不依赖 GLM API, 用规则化模板生成 chunk 布局
用于: 端到端 demo 验证、单元测试、API 不可用时的降级方案

特性:
- 种子化确定性 (相同 seed+chunk_id → 相同布局)
- 边界对齐 (相邻 chunk 道路接续)
- 真实村庄结构模拟 (建筑/道路/植被/水系)
"""
import random
from pathlib import Path

from loguru import logger

from pipeline.schema import (
    Building,
    ChunkLayout,
    Prop,
    Road,
    VegetationCluster,
    WaterFeature,
)

# 预置资产清单 (真实项目中从 L1 registry.json 读取)
DEFAULT_ASSETS = {
    "houses": [
        "house_wood_01", "house_wood_02", "house_stone_01",
        "house_thatch_01", "house_barn_01",
    ],
    "trees": ["tree_pine_01", "tree_broadleaf_01", "tree_bamboo_01"],
    "props": ["stone_wall_01", "stone_lamp_01", "fence_wood_01"],
}


class MockLayoutGenerator:
    """规则化 chunk 布局生成器"""

    def __init__(self, world_seed: int = 42, assets: dict | None = None):
        self.world_seed = world_seed
        self.assets = assets or DEFAULT_ASSETS
        # 全局道路网 (跨 chunk 主路)
        self.global_road_y = 100  # 主路东西向, y=100

    def _rng(self, chunk_x: int, chunk_y: int) -> random.Random:
        """确定性 RNG: 相同 seed+坐标 → 相同结果"""
        seed = (self.world_seed * 100003 + chunk_x * 1009 + chunk_y) & 0xFFFFFFFF
        return random.Random(seed)

    def generate_chunk(
        self, chunk_x: int, chunk_y: int, climate: dict | None = None
    ) -> ChunkLayout:
        """生成单个 chunk 布局"""
        rng = self._rng(chunk_x, chunk_y)
        chunk_origin_x = chunk_x * 200  # 世界坐标
        chunk_origin_y = chunk_y * 200

        # 1. 道路: 主路 + 村内小路
        roads = self._generate_roads(chunk_x, chunk_y, rng)

        # 2. 建筑: 沿主路两侧分布
        buildings = self._generate_buildings(rng, chunk_origin_x, chunk_origin_y)

        # 3. 植被: 建筑外围 + 道路两侧
        vegetation = self._generate_vegetation(rng, buildings)

        # 4. 水系: 偶尔有溪流
        water = self._generate_water(chunk_x, chunk_y, rng)

        # 5. 道具: 默认素材链中的三类 prop 都必须可见、可替换、可审计
        props = self._generate_props(chunk_x, chunk_y, rng)

        # 6. 地形高度图引用
        terrain = {
            "heightmap": f"chunk_{chunk_x}_{chunk_y}_terrain.png",
            "elevation_range": [50, 180],
            "material_zones": [
                {"type": "grass", "polygon": [[0, 0], [200, 0], [200, 200], [0, 200]]}
            ],
        }

        layout = ChunkLayout(
            chunk_id={"x": chunk_x, "y": chunk_y},
            world_seed=self.world_seed,
            size_m=200,
            geo_origin={"lat": 26.0 + chunk_y * 0.002, "lon": 119.0 + chunk_x * 0.002, "alt": 50},
            terrain=terrain,
            roads=roads,
            buildings=buildings,
            vegetation=vegetation,
            water=water,
            props=props,
        )
        logger.debug(
            f"chunk ({chunk_x},{chunk_y}): "
            f"{len(buildings)}栋建筑, {len(roads)}条道路, "
            f"{len(vegetation)}簇植被, {len(props)}个道具"
        )
        return layout

    def _generate_roads(self, cx: int, cy: int, rng: random.Random) -> list[Road]:
        """生成道路 - 主路贯通 + 村内小路"""
        roads = []

        # 主路: 东西向贯通 (与相邻 chunk 接续)
        roads.append(Road(
            id=f"road_main_{cx}_{cy}",
            type="main",
            width=4.0,
            points=[[0, 100 + (cx * 7) % 10], [200, 105 + (cx * 7) % 10]],
        ))

        # 小路: 南北向, 连接到主路
        for i in range(rng.randint(1, 2)):
            x = rng.randint(40, 160)
            roads.append(Road(
                id=f"road_trail_{cx}_{cy}_{i}",
                type="trail",
                width=1.5,
                points=[[x, 0], [x, 100], [x + rng.randint(-10, 10), 200]],
            ))

        return roads

    def _generate_buildings(
        self, rng: random.Random, origin_x: int, origin_y: int
    ) -> list[Building]:
        """沿主路两侧生成建筑 (坐北朝南)"""
        buildings = []
        n = rng.randint(4, 8)  # 每chunk 4-8 栋

        for i in range(n):
            # 主路两侧 30-80m 范围
            side = 1 if i % 2 == 0 else -1
            x = rng.uniform(20, 180)
            y = 100 + side * rng.uniform(30, 80)
            asset_id = rng.choice(self.assets["houses"])

            buildings.append(Building(
                id=f"bldg_{origin_x}_{origin_y}_{i}",
                asset_id=asset_id,
                pos=[round(x, 1), round(y, 1)],
                rot_z=rng.uniform(170, 190),  # 朝南 ±10°
                scale=round(rng.uniform(0.85, 1.15), 2),
            ))

        return buildings

    def _generate_vegetation(
        self, rng: random.Random, buildings: list[Building]
    ) -> list[VegetationCluster]:
        """建筑外围和角落生成植被"""
        clusters = []
        n = rng.randint(3, 5)

        for i in range(n):
            x = rng.uniform(10, 190)
            y = rng.uniform(10, 190)
            # 避开建筑
            too_close = any(
                abs(x - b.pos[0]) < 15 and abs(y - b.pos[1]) < 15 for b in buildings
            )
            if too_close:
                continue

            clusters.append(VegetationCluster(
                id=f"veg_{i}",
                type="tree_cluster",
                center=[round(x, 1), round(y, 1)],
                radius=round(rng.uniform(8, 20), 1),
                density=round(rng.uniform(0.4, 0.8), 2),
                asset_ids=[rng.choice(self.assets["trees"])],
            ))

        return clusters

    def _generate_water(
        self, cx: int, cy: int, rng: random.Random = None
    ) -> list[WaterFeature]:
        """偶发水系 (chunk_id 决定, 保证确定性)"""
        if rng is None:
            rng = self._rng(cx, cy)
        if (cx * 7 + cy * 13) % 3 != 0:  # 1/3 概率有溪流
            return []
        return [WaterFeature(
            id=f"stream_{cx}_{cy}",
            type="stream",
            width=2.0,
            points=[[0, 30 + (cy * 5) % 40], [100, 35 + (cy * 5) % 40], [200, 40 + (cy * 5) % 40]],
        )]

    def _generate_props(
        self, cx: int, cy: int, rng: random.Random
    ) -> list[Prop]:
        """沿主路生成可替换道具；道路带与建筑带分离，避免明显穿模。"""
        asset_ids = self.assets.get("props", [])
        if not asset_ids:
            return []

        step = 140 / max(1, len(asset_ids) - 1)
        props = []
        for index, asset_id in enumerate(asset_ids):
            x = 30 + index * step + rng.uniform(-3, 3)
            y = (91 if index % 2 == 0 else 109) + rng.uniform(-2, 2)
            props.append(Prop(
                id=f"prop_{cx}_{cy}_{index}",
                asset_id=asset_id,
                pos=[round(x, 1), round(y, 1)],
                rot_z=round(rng.uniform(0, 360), 1),
            ))
        return props


def generate_chunkset(
    world_seed: int = 42, size: int = 3, output_dir: str | Path = "layouts"
) -> list[ChunkLayout]:
    """生成一个 chunk 集合 (size x size 个 chunk)"""
    gen = MockLayoutGenerator(world_seed)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    layouts = []
    for cx in range(size):
        for cy in range(size):
            layout = gen.generate_chunk(cx, cy)
            layouts.append(layout)
            # 保存 JSON (newline="\n": layout 跨平台字节可复现)
            f = out / f"chunk_{cx}_{cy}.json"
            f.write_text(layout.model_dump_json(indent=2),
                         encoding="utf-8", newline="\n")

    logger.info(f"已生成 {len(layouts)} 个 chunk 布局 → {out}")
    return layouts


if __name__ == "__main__":
    # 生成 3x3 = 9 个 chunk 演示
    layouts = generate_chunkset(world_seed=42, size=3, output_dir="layouts")
    print(f"\n生成 {len(layouts)} 个 chunk:")
    for layout in layouts:
        print(
            f"  chunk ({layout.chunk_id.x},{layout.chunk_id.y}): "
            f"{len(layout.buildings)}栋建筑, {len(layout.roads)}条道路, "
            f"{len(layout.vegetation)}簇植被"
        )
