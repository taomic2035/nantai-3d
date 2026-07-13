"""
验证项: L2-LLM GLM-4.6 API 调用与结构化 JSON 布局生成
目标: 验证 GLM 能产出符合 schema 的村庄 chunk 布局 JSON

使用方法:
    1. 设置环境变量: $env:ZHIPU_API_KEY="你的key"
       或在 verification/.env 中写入 ZHIPU_API_KEY=xxx
    2. python verify_glm_layout.py
    3. 无 key 时会跳过实际调用, 仅验证 schema

获取 key: https://open.bigmodel.cn/usercenter/apikeys
新用户有免费额度 (GLM-4.6 约 2000万 tokens)
"""
import os
import json
import sys
from pathlib import Path

# 加载 .env (可选)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ---------- pydantic schema (强校验) ----------
from pydantic import BaseModel, Field, ValidationError

class Building(BaseModel):
    id: str
    asset_id: str
    pos: list[float]
    rot_z: float = Field(ge=0, le=360)
    scale: float = Field(default=1.0, gt=0, le=3.0)

class Road(BaseModel):
    id: str
    type: str = Field(pattern="^(main|trail|path)$")
    width: float = Field(gt=0, le=10)
    points: list[list[float]]

class VegetationCluster(BaseModel):
    id: str
    type: str = "tree_cluster"
    center: list[float]
    radius: float = Field(gt=0, le=50)
    density: float = Field(ge=0, le=1)
    asset_ids: list[str]

class ChunkLayout(BaseModel):
    chunk_id: dict
    world_seed: int
    size_m: int = 200
    geo_origin: dict
    terrain: dict
    roads: list[Road]
    buildings: list[Building]
    vegetation: list[VegetationCluster]
    water: list[dict] = []
    props: list[dict] = []

# ---------- GLM 调用 ----------
SYSTEM_PROMPT = """你是一位中国南方山村的村庄规划师。生成一个 200m×200m 区域的布局JSON。

要求:
1. 建筑必须从给定资产清单中选择 asset_id
2. 建筑朝向遵循"坐北朝南"原则, 主入口朝南 (rot_z 在 150-210 度)
3. 建筑密度根据 climate.density 调整
4. 树木集群分布在建筑外围和道路两侧
5. 输出严格符合 schema, 不要 markdown, 不要解释, 直接输出 JSON

Schema:
{
  "chunk_id": {"x": int, "y": int},
  "world_seed": int,
  "size_m": 200,
  "geo_origin": {"lat": float, "lon": float, "alt": float},
  "terrain": {
    "heightmap": "chunk_0_0_terrain.png",
    "elevation_range": [int, int],
    "material_zones": [{"type": "grass", "polygon": [[0,0],[200,0],[200,200],[0,200]]}]
  },
  "roads": [{"id":"r1","type":"main","width":4.0,"points":[[0,100],[200,105]]}],
  "buildings": [{"id":"b1","asset_id":"house_wood_01","pos":[50,60],"rot_z":180.0,"scale":1.0}],
  "vegetation": [{"id":"v1","type":"tree_cluster","center":[120,30],"radius":15.0,"density":0.6,"asset_ids":["tree_01"]}],
  "water": [],
  "props": []
}"""

USER_PROMPT_TEMPLATE = """生成 chunk (0, 0):
- 世界种子: 42
- 气候/地形: {{"type":"hill","density":0.3,"vegetation":"subtropical"}}
- 可用资产: {assets}
- 生成 5-8 栋建筑, 2-3 条道路, 3-5 个植被集群
- 随机种子: 12345
"""


def test_schema_validation():
    """验证 pydantic schema 本身可用"""
    print("[Step 1] 验证 pydantic schema...")
    sample = {
        "chunk_id": {"x": 0, "y": 0},
        "world_seed": 42,
        "geo_origin": {"lat": 26.0, "lon": 119.0, "alt": 50},
        "terrain": {
            "heightmap": "test.png",
            "elevation_range": [50, 180],
            "material_zones": [{"type": "grass", "polygon": [[0,0],[200,0]]}]
        },
        "roads": [{"id":"r1","type":"main","width":4.0,"points":[[0,100],[200,105]]}],
        "buildings": [{"id":"b1","asset_id":"house_wood_01","pos":[50,60],"rot_z":180.0,"scale":1.0}],
        "vegetation": [{"id":"v1","type":"tree_cluster","center":[120,30],"radius":15.0,"density":0.6,"asset_ids":["tree_01"]}]
    }
    layout = ChunkLayout(**sample)
    print(f"[OK] schema 验证通过, 建筑数: {len(layout.buildings)}")
    return True


def test_glm_call():
    """验证 GLM API 调用"""
    print("\n[Step 2] 验证 GLM-4.6 API 调用...")
    api_key = os.getenv("ZHIPU_API_KEY")

    if not api_key:
        print("[SKIP] 未设置 ZHIPU_API_KEY 环境变量")
        print("       获取 key: https://open.bigmodel.cn/usercenter/apikeys")
        print("       设置方法: $env:ZHIPU_API_KEY='你的key'")
        print("       或在 verification/.env 中写入 ZHIPU_API_KEY=xxx")
        return None

    try:
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key=api_key)
    except Exception as e:
        print(f"[FAIL] zhipuai 初始化失败: {e}")
        return False

    assets = [
        {"id": "house_wood_01", "category": "houses", "footprint": [4, 4]},
        {"id": "house_stone_01", "category": "houses", "footprint": [5, 5]},
        {"id": "tree_01", "category": "trees"},
        {"id": "tree_03", "category": "trees"},
    ]

    try:
        response = client.chat.completions.create(
            model="glm-4.6",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT_TEMPLATE.format(assets=json.dumps(assets, ensure_ascii=False))}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=4000
        )
        content = response.choices[0].message.content
        print(f"[OK] API 响应长度: {len(content)} 字符")
        print(f"     模型: {response.model}")
        print(f"     tokens: prompt={response.usage.prompt_tokens}, completion={response.usage.completion_tokens}")

        # JSON 解析
        layout_data = json.loads(content)
        print(f"[OK] JSON 解析成功")

        # pydantic 校验
        layout = ChunkLayout(**layout_data)
        print(f"[OK] pydantic schema 校验通过")
        print(f"     建筑数: {len(layout.buildings)}")
        print(f"     道路数: {len(layout.roads)}")
        print(f"     植被数: {len(layout.vegetation)}")

        # 保存
        out = Path(__file__).parent / "output" / "glm_layout_test.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(layout_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 布局已保存: {out}")

        return True
    except ValidationError as e:
        print(f"[FAIL] schema 校验失败: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON 解析失败: {e}")
        print(f"     原始响应前500字符: {content[:500]}")
        return False
    except Exception as e:
        print(f"[FAIL] API 调用失败: {e}")
        return False


def main():
    print("=" * 60)
    print("验证项: L2-LLM GLM-4.6 结构化布局生成")
    print("=" * 60)
    ok1 = test_schema_validation()
    ok2 = test_glm_call()

    print("\n" + "=" * 60)
    if ok2 is True:
        print("结论: GLM-4.6 可用, 结构化 JSON 输出符合 schema")
    elif ok2 is None:
        print("结论: schema 可用, API 待用户提供 key 后验证")
    else:
        print("结论: 需排查 API 调用问题")
    print("=" * 60)


if __name__ == "__main__":
    main()
