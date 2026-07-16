"""
L2 布局生成的核心 Schema (pydantic)
所有 chunk 布局 JSON 必须符合此 schema, 保证 L3 UE5 PCG 可正确消费
"""
from pydantic import BaseModel, Field


class ChunkID(BaseModel):
    x: int
    y: int


class GeoOrigin(BaseModel):
    # 与坐标信任根 recon_schema.GeoAnchor 对齐: 拒绝越界/非有限 GPS, 避免越界地理
    # 原点被静默接受 (GeoOrigin 从外部 layout JSON 加载, 非仅内部产出)。
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    alt: float = Field(allow_inf_nan=False)


class MaterialZone(BaseModel):
    type: str = Field(pattern="^(grass|stone|dirt|water|sand)$")
    polygon: list[list[float]]


class Terrain(BaseModel):
    heightmap: str  # PNG 路径, 256x256 灰度
    elevation_range: list[int]  # [min, max] 米
    material_zones: list[MaterialZone]


class Road(BaseModel):
    id: str
    type: str = Field(pattern="^(main|trail|path)$")
    width: float = Field(gt=0, le=10)
    points: list[list[float]]  # [[x,y], ...] 米


class Building(BaseModel):
    id: str
    asset_id: str  # 引用 assets/registry.json
    pos: list[float]  # [x, y] 米, chunk 内坐标
    rot_z: float = Field(ge=0, le=360)  # 度
    scale: float = Field(default=1.0, gt=0, le=3.0)


class VegetationCluster(BaseModel):
    id: str
    type: str = "tree_cluster"
    center: list[float]
    radius: float = Field(gt=0, le=50)
    density: float = Field(ge=0, le=1)
    asset_ids: list[str]


class WaterFeature(BaseModel):
    id: str
    type: str = Field(pattern="^(stream|pond|river)$")
    width: float = Field(default=2.0, gt=0)
    points: list[list[float]]


class Prop(BaseModel):
    id: str
    asset_id: str
    pos: list[float]
    rot_z: float = Field(default=0, ge=0, le=360)


class ChunkLayout(BaseModel):
    """单个 chunk (200m x 200m) 的完整布局"""
    chunk_id: ChunkID
    world_seed: int
    size_m: int = 200
    geo_origin: GeoOrigin
    terrain: Terrain
    roads: list[Road] = []
    buildings: list[Building] = []
    vegetation: list[VegetationCluster] = []
    water: list[WaterFeature] = []
    props: list[Prop] = []


if __name__ == "__main__":
    # 自验证

    sample = {
        "chunk_id": {"x": 0, "y": 0},
        "world_seed": 42,
        "geo_origin": {"lat": 26.0, "lon": 119.0, "alt": 50},
        "terrain": {
            "heightmap": "chunk_0_0_terrain.png",
            "elevation_range": [50, 180],
            "material_zones": [
                {"type": "grass", "polygon": [[0, 0], [200, 0], [200, 200], [0, 200]]}
            ],
        },
        "roads": [
            {"id": "r1", "type": "main", "width": 4.0, "points": [[0, 100], [200, 105]]}
        ],
        "buildings": [
            {"id": "b1", "asset_id": "house_wood_01", "pos": [50, 60], "rot_z": 180.0, "scale": 1.0}
        ],
        "vegetation": [
            {
                "id": "v1",
                "type": "tree_cluster",
                "center": [120, 30],
                "radius": 15.0,
                "density": 0.6,
                "asset_ids": ["tree_01"],
            }
        ],
    }
    layout = ChunkLayout(**sample)
    print("[OK] schema 验证通过")
    print(f"  chunk: {layout.chunk_id}")
    print(f"  建筑: {len(layout.buildings)} 栋")
    print(f"  道路: {len(layout.roads)} 条")
    print(f"  植被: {len(layout.vegetation)} 簇")
