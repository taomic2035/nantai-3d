"""
L2 布局生成器 - GLM-4.6 客户端 (带 mock 降级)
从 L1 资产清单 + 气候信息生成 chunk 布局 JSON

无 ZHIPU_API_KEY 时自动降级为 MockLayoutGenerator, 保证管线可在离线环境跑通。
"""
import os
import json
from pathlib import Path
from loguru import logger
from pipeline.schema import ChunkLayout


SYSTEM_PROMPT = """你是一位中国南方山村的村庄规划师。生成一个 200m×200m 区域的布局JSON。

要求:
1. 建筑必须从给定资产清单中选择 asset_id
2. 建筑朝向遵循"坐北朝南"原则, 主入口朝南 (rot_z 在 150-210 度)
3. 建筑密度根据 climate.density 调整 (0.1-0.5)
4. 树木集群分布在建筑外围和道路两侧
5. 道路须与邻接 chunk 的道路接续 (给出邻接端点)
6. 输出严格符合 schema, 不要 markdown, 不要解释, 直接输出 JSON

Schema:
{
  "chunk_id": {"x": int, "y": int},
  "world_seed": int,
  "size_m": 200,
  "geo_origin": {"lat": float, "lon": float, "alt": float},
  "terrain": {
    "heightmap": "chunk_X_Y_terrain.png",
    "elevation_range": [int, int],
    "material_zones": [{"type": "grass|stone|dirt", "polygon": [[x,y],...]}]
  },
  "roads": [{"id":"string","type":"main|trail","width":float,"points":[[x,y],...]}],
  "buildings": [{"id":"string","asset_id":"string","pos":[x,y],"rot_z":float,"scale":float}],
  "vegetation": [{"id":"string","type":"tree_cluster","center":[x,y],"radius":float,"density":float,"asset_ids":["string"]}],
  "water": [{"id":"string","type":"stream|pond","width":float,"points":[[x,y],...]}],
  "props": [{"id":"string","asset_id":"string","pos":[x,y],"rot_z":float}]
}"""


USER_TEMPLATE = """生成 chunk ({x}, {y}):
- 世界种子: {seed}
- 气候/地形: {climate}
- 可用资产: {assets}
- 邻接chunk道路端点(需接续): {neighbors}
- 随机种子(确定性): {local_seed}
- 生成 {n_buildings} 栋建筑, {n_roads} 条道路, {n_veg} 个植被集群"""


class GLMLayoutGenerator:
    """GLM-4.6 布局生成器 (无 API key 时自动降级为 Mock)"""

    def __init__(self, api_key=None, model="glm-4.6", temperature=0.7):
        api_key = api_key or os.getenv("ZHIPU_API_KEY")
        self.use_mock = False
        if not api_key:
            logger.warning(
                "未设置 ZHIPU_API_KEY, 自动降级为 MockLayoutGenerator。"
                "获取 key: https://bigmodel.cn/usercenter/apikeys"
            )
            self.use_mock = True
            from pipeline.mock_layout import MockLayoutGenerator
            self._mock = MockLayoutGenerator()
            return

        from zhipuai import ZhipuAI
        self.client = ZhipuAI(api_key=api_key)
        self.model = model
        self.temperature = temperature

    async def generate_chunk(
        self,
        chunk_x: int,
        chunk_y: int,
        world_seed: int = 42,
        climate: dict | None = None,
        assets: list[dict] | None = None,
        neighbor_roads: dict | None = None,
        n_buildings: int = 6,
        n_roads: int = 2,
        n_veg: int = 4,
    ) -> ChunkLayout:
        """生成单个 chunk 布局"""
        # Mock 降级路径
        if self.use_mock:
            self._mock.world_seed = world_seed
            return self._mock.generate_chunk(chunk_x, chunk_y, climate)

        # GLM API 路径
        local_seed = hash((chunk_x, chunk_y, world_seed)) % 100000
        prompt = USER_TEMPLATE.format(
            x=chunk_x, y=chunk_y, seed=world_seed,
            climate=json.dumps(climate or {}, ensure_ascii=False),
            assets=json.dumps((assets or [])[:20], ensure_ascii=False),
            neighbors=json.dumps(neighbor_roads or {}, ensure_ascii=False),
            local_seed=local_seed,
            n_buildings=n_buildings, n_roads=n_roads, n_veg=n_veg,
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            max_tokens=4000,
        )

        content = response.choices[0].message.content
        layout_data = json.loads(content)

        layout = ChunkLayout(**layout_data)
        logger.info(
            f"chunk ({chunk_x},{chunk_y}) 生成完成 [GLM]: "
            f"{len(layout.buildings)}栋建筑, {len(layout.roads)}条道路"
        )
        return layout


if __name__ == "__main__":
    # 自测: 无 API key 时验证降级路径
    import asyncio

    async def test():
        climate = {"type": "hill", "density": 0.3, "vegetation": "subtropical"}
        gen = GLMLayoutGenerator()  # 无 key 会自动降级
        layout = await gen.generate_chunk(0, 0, 42, climate)
        print(f"[OK] 生成完成 mode={'mock' if gen.use_mock else 'glm'}")
        print(f"  建筑: {len(layout.buildings)}")
        print(f"  道路: {len(layout.roads)}")
        print(f"  植被: {len(layout.vegetation)}")

    asyncio.run(test())
