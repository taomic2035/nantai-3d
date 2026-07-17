"""合成村庄【多天气变体】的光照 profile —— 与 canary 24 帧契约【完全隔离】。

## 为什么它存在
3DGS 把光照【烘死】在高斯 SH 里, 所以"切天气"只能靠【在 build 侧改场景光照本身、
重渲、重训一套 3DGS】。本模块定义"改哪些光照数值"这件事的【唯一真源】:
每种天气 = 一套 sun/fill/rim/world 数值, 解算成 Blender 可直接落地的具体量。

## 内容寻址 (fail-closed 命脉)
天气差异【必须落在被摘要的字节里】, 否则就是"靠变体名自称天气"。
- `weather_scene_lighting(profile)` 产出【已完全解算】的光照块 (色温已转 rgb、高度角/
  方位已转 euler)。builder 是【哑执行者】: 直接读这些数值, 不做任何派生。
- `weather_lighting_digest(profile)` = 对该光照块的 sha256。它跟【光照物理量】走,
  【不跟名字走】: 只改 profile_id、数值不变 -> 摘要相同 (见测试)。
- `weather_request_block(profile_id)` 把该块塞进 build request payload ->
  build_id = sha256(request) 自动分叉 -> 输出根按 build_id 天然隔离。

## 诚实边界 (不许美化)
- **无真实天空模型**: World 是【平色环境光】(flat-color ambient), 没有 Nishita/HDRI/
  大气散射。overcast/clear/golden 都是 sun + 环境光的【近似】, 不是物理散射。
  `sky_model="flat-color-ambient-approximation"` 原样标注。
- **换光照【不】提升几何 trust**: synthetic=true / simplified-pbr-not-render-parity
  原样保留。天气只换光, 不让 geometry 更可信。
- **本仓库无 3DGS 训练器** (本机无 CUDA): 本模块只产【渲染输入 + 契约】。真正在 viewer
  里看到天气切换, 还差【每变体云端重训一套 3DGS】这一步。
- **blend_sha256 不在这里算**: 每个变体真正的 blend 字节只能由 Blender builder 实跑
  weather 块生成。本模块给出【会进入 build request、进而决定 build_id/blend 的那份
  被摘要的字节】, 但不假装算出了 .blend 摘要。
- **角色标签是场景图契约 token, 不是天气声明**: build 侧
  overcast-world-background 校验要求灯光角色恰为
  {neutral-overcast-key, neutral-sky-fill, terrain-separation}。所有天气都保留这三个,
  哪怕 clear-noon 叫 "overcast-key" 语义上别扭 —— 这是为了不破坏 68 槽位 visual-slot
  契约, 名字在此是冻结 token 而非光照描述。
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field

from .contracts import FrozenModel

WEATHER_PROFILE_SCHEMA = "nantai.synthetic-village.weather-profile.v1"
WEATHER_MANIFEST_SCHEMA = "nantai.synthetic-village.weather-variants.v1"

#: build 侧 overcast-world-background 校验要求的固定角色集合 (scene-graph token)。
#: 顺序固定, 供 sun/fill/rim 依次绑定。绝不随天气改动。
FIXED_LIGHT_ROLES: tuple[str, str, str] = (
    "neutral-overcast-key",
    "neutral-sky-fill",
    "terrain-separation",
)

#: 补光与边光位置沿用 canary 场景的几何布置 (与光照能量/色温无关)。
_FILL_LOCATION: tuple[float, float, float] = (-80.0, -120.0, 230.0)
_RIM_ROTATION_EULER_DEG: tuple[float, float, float] = (55.0, 0.0, 125.0)


class WeatherProfile(FrozenModel):
    """一套【场景光照】的命名定义 (物理量, 未解算)。

    strict + frozen + extra=forbid: 反序列化时不许静默强转、不许多字段。
    数值字段全用 float —— 天气是连续物理量, 不是枚举。
    """

    profile_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20)
    # 太阳 (直射 key)
    sun_energy: float = Field(gt=0, allow_inf_nan=False)
    sun_elevation_deg: float = Field(ge=0, le=90, allow_inf_nan=False)
    sun_azimuth_deg: float = Field(ge=0, lt=360, allow_inf_nan=False)
    sun_angle_deg: float = Field(ge=0, allow_inf_nan=False)  # 角直径 -> 阴影软硬
    sun_color_temp_k: float = Field(gt=0, allow_inf_nan=False)
    # 天空补光 (AREA fill)
    fill_energy: float = Field(ge=0, allow_inf_nan=False)
    fill_color_temp_k: float = Field(gt=0, allow_inf_nan=False)
    # 边光 (terrain separation rim)
    rim_energy: float = Field(ge=0, allow_inf_nan=False)
    rim_angle_deg: float = Field(ge=0, allow_inf_nan=False)
    # World 平色环境光
    world_color: tuple[float, float, float]
    world_strength: float = Field(ge=0, allow_inf_nan=False)

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def color_temp_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """黑体色温 -> 归一化 sRGB (0..1) 的【近似】(Tanner Helland 拟合)。

    这是【近似】, 不是分光辐射标定。用途只是让暖光偏红、冷光偏蓝, 给天气一个可溯的
    色调, 不声称色度学精确。夹在 1000..40000 K。
    """
    temp = max(1000.0, min(40000.0, kelvin)) / 100.0

    if temp <= 66.0:
        red = 255.0
    else:
        red = 329.698727446 * ((temp - 60.0) ** -0.1332047592)

    if temp <= 66.0:
        green = 99.4708025861 * _safe_log(temp) - 161.1195681661
    else:
        green = 288.1221695283 * ((temp - 60.0) ** -0.0755148492)

    if temp >= 66.0:
        blue = 255.0
    elif temp <= 19.0:
        blue = 0.0
    else:
        blue = 138.5177312231 * _safe_log(temp - 10.0) - 305.0447927307

    return (
        _clamp01(red / 255.0),
        _clamp01(green / 255.0),
        _clamp01(blue / 255.0),
    )


def _safe_log(value: float) -> float:
    import math

    return math.log(value) if value > 0 else 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def sun_rotation_euler_deg(
    *,
    elevation_deg: float,
    azimuth_deg: float,
) -> tuple[float, float, float]:
    """高度角/方位 -> Blender SUN 的 XYZ euler (度)。

    约定 (【场景约定, 非天文标定】): Blender 默认 SUN 沿 -Z 朝下 (euler 0 = 天顶正射)。
    绕 X 下倾 (90 - 高度角) 把太阳压到该仰角; 绕 Z 转方位。高度角越高 -> 下倾越小。
    这是一个【可复现的场景约定】, 不声称与真实太阳历一致。
    """
    return (round(90.0 - elevation_deg, 6), 0.0, round(azimuth_deg, 6))


def _round3(values: tuple[float, float, float]) -> list[float]:
    return [round(v, 6) for v in values]


def weather_scene_lighting(profile: WeatherProfile) -> dict:
    """把 profile 的物理量【完全解算】成 builder 可直接落地的具体数值块。

    builder 只读这些键、不做派生。这个块 (排除会话性字段) 就是 lighting_digest 摘要的对象。
    """
    return {
        "roles": list(FIXED_LIGHT_ROLES),
        "sun_energy": round(profile.sun_energy, 6),
        "sun_angle_deg": round(profile.sun_angle_deg, 6),
        "sun_rotation_euler_deg": list(
            sun_rotation_euler_deg(
                elevation_deg=profile.sun_elevation_deg,
                azimuth_deg=profile.sun_azimuth_deg,
            )
        ),
        "sun_color": _round3(color_temp_to_rgb(profile.sun_color_temp_k)),
        "fill_energy": round(profile.fill_energy, 6),
        "fill_color": _round3(color_temp_to_rgb(profile.fill_color_temp_k)),
        "fill_location": list(_FILL_LOCATION),
        "rim_energy": round(profile.rim_energy, 6),
        "rim_angle_deg": round(profile.rim_angle_deg, 6),
        "rim_rotation_euler_deg": list(_RIM_ROTATION_EULER_DEG),
        "world_color": _round3(profile.world_color),
        "world_strength": round(profile.world_strength, 6),
    }


def _canonical_block_bytes(block: dict) -> bytes:
    return json.dumps(
        block,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def weather_lighting_digest(profile: WeatherProfile) -> str:
    """光照块的 sha256 —— 内容寻址的天气身份。

    摘要对象是【已解算光照数值】, 【不含 profile_id/description】: 天气身份跟物理量走,
    不跟名字走。两种天气数值不同 -> 摘要必不同 (钉在测试里)。
    """
    return hashlib.sha256(
        _canonical_block_bytes(weather_scene_lighting(profile))
    ).hexdigest()


def resolve_weather_profile(profile_id: str) -> WeatherProfile:
    """按名取 profile; 未知名字【fail-closed】(KeyError), 绝不猜。"""
    return WEATHER_PROFILES[profile_id]


def weather_request_block(profile_id: str) -> dict:
    """构造塞进 build request payload 的 weather 块 (含 profile_id + 光照 + 摘要)。

    这份块进入 canonical request -> build_id = sha256(request) 自动按天气分叉。
    """
    profile = resolve_weather_profile(profile_id)
    block = weather_scene_lighting(profile)
    return {
        "profile_id": profile.profile_id,
        "schema": WEATHER_PROFILE_SCHEMA,
        "lighting_digest": weather_lighting_digest(profile),
        "lighting": block,
    }


def _variant_row(profile: WeatherProfile) -> dict:
    resolved = weather_scene_lighting(profile)
    return {
        "profile_id": profile.profile_id,
        "description": profile.description,
        # 如实记录 sun 高度角/方位/能量/色温 —— "这是哪种光照"机器可溯, 不靠名字。
        "sun_elevation_deg": profile.sun_elevation_deg,
        "sun_azimuth_deg": profile.sun_azimuth_deg,
        "sun_energy": profile.sun_energy,
        "sun_color_temp_k": profile.sun_color_temp_k,
        "sun_angle_deg": profile.sun_angle_deg,
        "fill_energy": profile.fill_energy,
        "fill_color_temp_k": profile.fill_color_temp_k,
        "rim_energy": profile.rim_energy,
        "world_color": list(profile.world_color),
        "world_strength": profile.world_strength,
        # 解算后的具体量 + 内容寻址摘要。
        "resolved_lighting": resolved,
        "lighting_digest": weather_lighting_digest(profile),
    }


def build_weather_variants_manifest() -> dict:
    """生成 weather-variants 清单: 每变体光照参数 + 摘要 + 一句人话 + 诚实边界。"""
    return {
        "schema": WEATHER_MANIFEST_SCHEMA,
        "synthetic": True,
        "geometry_trust": "simplified-pbr-not-render-parity",
        "sky_model": "flat-color-ambient-approximation",
        "light_role_labels": list(FIXED_LIGHT_ROLES),
        "weather_is_relighting_note": (
            "每种天气 = 在 build 侧改场景光照本身 (sun 能量/高度角/方位/色温 + World "
            "环境光), 产生新的 blend_sha256。绝不走 RenderSettings/色调映射冒充重光照。"
        ),
        "sky_model_note": (
            "World 是平色环境光, 无 Nishita/HDRI/大气散射; overcast/clear/golden 均为 "
            "sun + 环境光的近似, 不是物理天空散射。"
        ),
        "geometry_trust_note": (
            "换光照【不】提升几何 trust: synthetic=true / simplified-pbr-not-render-parity "
            "原样保留。"
        ),
        "pipeline_status_note": (
            "本仓库无 3DGS 训练器 (本机无 CUDA)。本清单只产【渲染输入 + 契约】; 要在 viewer "
            "里看到天气切换, 还差【每变体云端重训一套 3DGS】这一步。"
        ),
        "blend_build_note": (
            "每个变体真正的 blend_sha256 只能由 Blender builder 实跑 weather 块生成, "
            "不在此清单内计算。lighting_digest 是【会进入 build request/build_id 的被摘要字节】。"
        ),
        "light_role_labels_note": (
            "角色标签是 scene-graph 契约 token (overcast-world-background 校验要求), "
            "不是天气声明; 所有天气都保留这三个角色。"
        ),
        "variants": [_variant_row(WEATHER_PROFILES[pid]) for pid in sorted(WEATHER_PROFILES)],
    }


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """清单的 canonical JSON 字节 (sort_keys, 尾换行) —— 可稳定落盘/摘要。"""
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    return (payload + "\n").encode("utf-8")


#: 三种命名天气。数值即物理光照, 落到 build 侧改场景光照。
#: overcast 沿用 canary 场景的中性阴天基调, 但作为【独立 weather 变体】走独立 build。
WEATHER_PROFILES: dict[str, WeatherProfile] = {
    "clear-noon": WeatherProfile(
        profile_id="clear-noon",
        description=(
            "晴朗正午高日头: 硬阴影、强直射 key、偏冷天光环境。"
        ),
        sun_energy=5.0,
        sun_elevation_deg=78.0,
        sun_azimuth_deg=180.0,
        sun_angle_deg=0.53,
        sun_color_temp_k=5600.0,
        fill_energy=400.0,
        fill_color_temp_k=12000.0,
        rim_energy=0.7,
        rim_angle_deg=24.0,
        world_color=(0.52, 0.62, 0.78),
        world_strength=0.9,
    ),
    "overcast": WeatherProfile(
        profile_id="overcast",
        description=(
            "小雨初歇后的明亮中性阴天: 柔和低反差、天光主导、阴影极软。"
        ),
        sun_energy=2.2,
        sun_elevation_deg=35.0,
        sun_azimuth_deg=200.0,
        sun_angle_deg=14.0,
        sun_color_temp_k=6800.0,
        fill_energy=1400.0,
        fill_color_temp_k=7400.0,
        rim_energy=0.7,
        rim_angle_deg=24.0,
        world_color=(0.43, 0.55, 0.62),
        world_strength=0.62,
    ),
    "golden-hour": WeatherProfile(
        profile_id="golden-hour",
        description=(
            "傍晚低斜暖阳: 长阴影、暖色直射 key、冷而暗的阴影补光。"
        ),
        sun_energy=3.2,
        sun_elevation_deg=8.0,
        sun_azimuth_deg=285.0,
        sun_angle_deg=0.6,
        sun_color_temp_k=3200.0,
        fill_energy=250.0,
        fill_color_temp_k=4500.0,
        rim_energy=0.7,
        rim_angle_deg=24.0,
        world_color=(0.30, 0.26, 0.24),
        world_strength=0.35,
    ),
}


WeatherProfileId = Literal["clear-noon", "overcast", "golden-hour"]
