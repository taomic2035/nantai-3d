"""合成村庄【多天气变体】契约测试 (TDD)。

核心护栏 (fail-closed 命脉): 两种天气【必须】产生不同的、被内容寻址摘要的字节。
"靠变体名自称天气" = 谎言; 摘要必须跟【光照数值】走, 不跟名字走。

本仓库【无 3DGS 训练器】(本机无 CUDA), 本任务只产【渲染输入 + 契约】。
真正的 blend_sha256 只能由 Blender builder 实跑 weather 块生成 —— 这里不跑 Blender,
所以我们钉住【会进入 build request、进而决定 build_id/blend 的那份被摘要的字节】。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipeline.synthetic_village import weather_profile as wp

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "blender" / "build_synthetic_village.py"


def test_registry_has_the_three_named_weathers() -> None:
    assert set(wp.WEATHER_PROFILES) == {"clear-noon", "overcast", "golden-hour"}
    for profile_id, profile in wp.WEATHER_PROFILES.items():
        assert profile.profile_id == profile_id


def test_each_profile_keeps_the_three_frozen_light_roles() -> None:
    # 角色标签是【场景图契约 token】(visual-slot overcast-world-background 校验),
    # 不是天气声明。所有天气都必须保留这三个角色, 否则 build 侧 fail-closed。
    for profile in wp.WEATHER_PROFILES.values():
        block = wp.weather_scene_lighting(profile)
        assert tuple(block["roles"]) == (
            "neutral-overcast-key",
            "neutral-sky-fill",
            "terrain-separation",
        )


def test_lighting_digest_differs_between_every_weather_pair() -> None:
    # ★护栏★: 任意两种天气的内容寻址摘要必须不同。
    digests = {
        pid: wp.weather_lighting_digest(profile)
        for pid, profile in wp.WEATHER_PROFILES.items()
    }
    assert len(set(digests.values())) == len(digests), (
        f"两种天气撞了同一个 lighting_digest, 内容寻址无法区分它们: {digests}"
    )
    for digest in digests.values():
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


def test_digest_tracks_lighting_values_not_the_name() -> None:
    # 反"名字冒充变体": 只改名字、光照数值不变 -> 摘要必须【相同】。
    base = wp.WEATHER_PROFILES["overcast"]
    renamed = base.model_copy(update={"profile_id": "clear-noon", "description": base.description})
    # profile_id 不参与 lighting_digest (它摘要的是光照物理量, 不是名字)。
    assert wp.weather_lighting_digest(renamed) == wp.weather_lighting_digest(base)


def test_digest_changes_when_any_physical_light_value_changes() -> None:
    base = wp.WEATHER_PROFILES["overcast"]
    for field, bump in (
        ("sun_energy", base.sun_energy + 1.0),
        ("sun_elevation_deg", base.sun_elevation_deg + 5.0),
        ("sun_azimuth_deg", base.sun_azimuth_deg + 5.0),
        ("sun_color_temp_k", base.sun_color_temp_k + 500.0),
        ("world_strength", base.world_strength + 0.1),
    ):
        mutated = base.model_copy(update={field: bump})
        assert wp.weather_lighting_digest(mutated) != wp.weather_lighting_digest(base), (
            f"改了物理光照量 {field} 但摘要没变 —— 内容寻址漏字节"
        )


def test_scene_lighting_is_fully_resolved_concrete_numbers() -> None:
    # builder 是【哑执行者】: weather 块必须是已解算好的 Blender 具体数值,
    # 派生逻辑 (色温->rgb, 高度角/方位->euler) 全在 pipeline 侧, 不在 Blender 脚本里。
    block = wp.weather_scene_lighting(wp.WEATHER_PROFILES["golden-hour"])
    for key in (
        "sun_energy",
        "sun_angle_deg",
        "sun_rotation_euler_deg",
        "sun_color",
        "fill_energy",
        "fill_color",
        "fill_location",
        "rim_energy",
        "rim_angle_deg",
        "rim_rotation_euler_deg",
        "world_color",
        "world_strength",
        "roles",
    ):
        assert key in block, f"weather 块缺少 builder 需要的键: {key}"
    assert len(block["sun_color"]) == 3
    assert len(block["sun_rotation_euler_deg"]) == 3
    # JSON 可序列化且无 NaN/Inf (contract 给反序列化兜底)。
    json.dumps(block, allow_nan=False)


def test_color_temp_to_rgb_is_warm_low_and_cool_high() -> None:
    warm = wp.color_temp_to_rgb(3200.0)
    neutral = wp.color_temp_to_rgb(6500.0)
    cool = wp.color_temp_to_rgb(12000.0)
    for rgb in (warm, neutral, cool):
        assert len(rgb) == 3
        assert all(0.0 <= c <= 1.0 for c in rgb)
    # 暖光: 红 > 蓝; 冷光: 蓝 >= 红。
    assert warm[0] > warm[2]
    assert cool[2] >= cool[0]


def test_sun_rotation_reflects_elevation() -> None:
    # 高度角越高 -> 太阳越接近天顶 -> 绕 X 的下倾角越小。约定必须单调, 不是装饰。
    low = wp.sun_rotation_euler_deg(elevation_deg=8.0, azimuth_deg=180.0)
    high = wp.sun_rotation_euler_deg(elevation_deg=78.0, azimuth_deg=180.0)
    assert low[0] > high[0]


def test_golden_hour_is_lower_and_warmer_than_clear_noon() -> None:
    noon = wp.WEATHER_PROFILES["clear-noon"]
    golden = wp.WEATHER_PROFILES["golden-hour"]
    assert golden.sun_elevation_deg < noon.sun_elevation_deg
    assert golden.sun_color_temp_k < noon.sun_color_temp_k


def test_manifest_records_provenance_and_honest_limits() -> None:
    manifest = wp.build_weather_variants_manifest()
    assert manifest["synthetic"] is True
    # 换光照【不】提升几何 trust。
    assert manifest["geometry_trust"] == "simplified-pbr-not-render-parity"
    variants = {row["profile_id"]: row for row in manifest["variants"]}
    assert set(variants) == set(wp.WEATHER_PROFILES)
    for row in variants.values():
        # 每个变体如实记录 sun 高度角/方位/能量/色温 + world + digest + 一句人话。
        assert isinstance(row["description"], str) and row["description"]
        assert row["sun_elevation_deg"] is not None
        assert row["sun_azimuth_deg"] is not None
        assert row["sun_energy"] is not None
        assert row["sun_color_temp_k"] is not None
        assert row["world_color"] is not None
        assert row["world_strength"] is not None
        assert len(row["lighting_digest"]) == 64
    # 诚实边界必须出现在【操作者实际读的那份输出】里。
    honesty = json.dumps(manifest, ensure_ascii=False)
    assert "3DGS" in honesty or "retrain" in honesty.lower()
    # World 是平色环境光, 不是真实天空散射 —— 不许把近似写成真散射。
    assert manifest["sky_model"] == "flat-color-ambient-approximation"


def test_manifest_variant_digests_match_registry_digests() -> None:
    manifest = wp.build_weather_variants_manifest()
    for row in manifest["variants"]:
        profile = wp.WEATHER_PROFILES[row["profile_id"]]
        assert row["lighting_digest"] == wp.weather_lighting_digest(profile)


def test_manifest_is_canonical_json_roundtrip() -> None:
    manifest = wp.build_weather_variants_manifest()
    raw = wp.canonical_manifest_bytes(manifest)
    assert raw.endswith(b"\n")
    assert json.loads(raw) == manifest


def test_weather_request_block_carries_profile_id_and_digest() -> None:
    # weather 块进入 build request payload -> build_id 自动分叉 (内容寻址)。
    block = wp.weather_request_block("clear-noon")
    assert block["profile_id"] == "clear-noon"
    assert block["lighting_digest"] == wp.weather_lighting_digest(
        wp.WEATHER_PROFILES["clear-noon"]
    )
    # 两种天气的 request 块必须字节不同, 否则 build_id 不会分叉。
    other = wp.weather_request_block("overcast")
    assert wp._canonical_block_bytes(block) != wp._canonical_block_bytes(other)


def test_resolve_unknown_profile_fails_closed() -> None:
    with pytest.raises(KeyError):
        wp.resolve_weather_profile("monsoon-typhoon")


def test_request_block_digest_matches_the_builder_canonical_form() -> None:
    # ★内容寻址端到端★: block 里的 lighting_digest 必须等于 builder 会复算的那份
    # canonical 字节的 sha256 (builder 用 sort_keys + compact separators)。对不上 ->
    # builder fail-closed。这里【复刻 builder 的校验】以免两侧 canonical 形不一致。
    for profile_id in wp.WEATHER_PROFILES:
        block = wp.weather_request_block(profile_id)
        builder_canonical = json.dumps(
            block["lighting"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert hashlib.sha256(builder_canonical).hexdigest() == block["lighting_digest"]


def test_builder_actually_consumes_the_weather_block() -> None:
    # 无法在此实跑 Blender (import bpy)。源码守卫: 确认 builder 从 request 读 weather
    # 并把 sun/fill 颜色与能量绑到 weather 数值上 —— 防止有人把光照回退成硬编码常量
    # (那样 build_id 分叉却 blend 不变 = 内容寻址撒谎)。
    source = BUILDER.read_text(encoding="utf-8")
    assert 'request.get("weather")' in source
    assert 'lighting["sun_energy"]' in source
    assert 'lighting["sun_color"]' in source
    assert 'lighting["world_color"]' in source
    # 且 builder 对 weather 块做内容寻址 fail-closed 校验。
    assert "weather lighting_digest does not match lighting bytes" in source


def test_cli_weather_variants_emits_manifest_and_request_block(tmp_path: Path) -> None:
    import scripts.synthetic_village as cli

    manifest_path = tmp_path / "weather-variants.json"
    block_path = tmp_path / "clear-noon.weather.json"
    code = cli.main(
        [
            "weather-variants",
            "--manifest",
            str(manifest_path),
            "--profile",
            "clear-noon",
            "--request-block",
            str(block_path),
        ]
    )
    assert code == 0
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["schema"] == wp.WEATHER_MANIFEST_SCHEMA
    assert {row["profile_id"] for row in manifest["variants"]} == set(wp.WEATHER_PROFILES)
    block = json.loads(block_path.read_bytes())
    assert block["profile_id"] == "clear-noon"
    assert block["lighting_digest"] == wp.weather_lighting_digest(
        wp.WEATHER_PROFILES["clear-noon"]
    )
