"""render-on-demand 内核: 单块合成渲染的确定性 + 负索引安全 + 内存 ply 字节。

这些是"服务器按需实时渲染任意 (cx,cy) chunk"的前提。合成路径 (registry=None)
必须字节可复现 —— 否则 chunk 淘汰重渲会闪烁、内容寻址缓存失效、多实例服务器发散,
也违反本仓库的可复现性核心价值。测试用 MockLayoutGenerator 的【全量】布局
(含 roads+buildings, 才能覆盖出过 bug 的合成路径), 跨渲染比 sha256 而非仅 array_equal。
"""
import hashlib
import json
import math
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData

from pipeline.generate_world import _grid_range, generate_layouts_mock
from pipeline.mock_layout import MockLayoutGenerator
from pipeline.render_chunk_to_ply import (
    DEFAULT_LOD_FRACTIONS,
    build_chunk_array,
    render_chunkset,
    render_single_chunk,
)


def _ply_count(data: bytes) -> int:
    return PlyData.read(BytesIO(data))['vertex'].count


def _bake(tmp_path, chunk_range, seed=42):
    """辅助: 生成 layout + 渲染 chunkset, 返回 manifest dict。"""
    layouts = tmp_path / "layouts"
    layouts.mkdir()
    gen = MockLayoutGenerator(world_seed=seed)
    x_min, x_max, y_min, y_max = chunk_range
    for cx in range(x_min, x_max):
        for cy in range(y_min, y_max):
            layout = gen.generate_chunk(cx, cy)
            (layouts / f"chunk_{cx}_{cy}.json").write_text(
                layout.model_dump_json(indent=2), encoding="utf-8")
    return render_chunkset(
        layouts_dir=layouts, output_dir=tmp_path / "web",
        chunk_range=chunk_range, assets_dir=None, lod_levels={0: 0.1},
    )


def _sha_of_array(arr) -> str:
    return hashlib.sha256(arr.tobytes()).hexdigest()


def test_synthetic_chunk_render_is_deterministic():
    """同一 chunk 的全量合成布局连渲两次, 字节必须一致 (BUG #2 回归)。"""
    layout = MockLayoutGenerator(world_seed=42).generate_chunk(1, 2)
    assert layout.roads and layout.buildings, "布局须含道路+建筑才能覆盖 bug 路径"
    first = build_chunk_array(layout, registry=None)
    second = build_chunk_array(layout, registry=None)
    assert len(first) == len(second)
    assert _sha_of_array(first) == _sha_of_array(second)


def test_negative_index_chunk_renders_and_is_deterministic():
    """负象限 chunk 必须能渲染 (BUG #1: _emit_ground 负种子崩溃) 且确定。"""
    layout = MockLayoutGenerator(world_seed=42).generate_chunk(-1, -2)
    first = build_chunk_array(layout, registry=None)
    second = build_chunk_array(layout, registry=None)
    assert len(first) > 0
    assert _sha_of_array(first) == _sha_of_array(second)


def test_ground_seed_stays_byte_stable_for_nonnegative_offsets():
    """回归保护: 负索引修复 (掩码) 不得改变现有非负网格的地面种子。

    现有 0..4 网格 offset ≤ 800, x*31+y*7+1 最大 30401 ≪ 2^32, 掩码后不变。
    """
    for x_off, y_off in [(0, 0), (200, 400), (800, 800)]:
        raw = x_off * 31 + y_off * 7 + 1
        assert (raw & 0xFFFFFFFF) == raw


def test_render_single_chunk_returns_deterministic_ply_bytes():
    """render-on-demand 内核纯函数: 返回 ply 字节 (内存, 不落盘), 跨调用一致。"""
    a = render_single_chunk(3, 1, world_seed=42)
    b = render_single_chunk(3, 1, world_seed=42)
    assert a[:3] == b"ply"
    assert a == b
    # 不同 chunk 应产出不同几何
    c = render_single_chunk(0, 0, world_seed=42)
    assert c[:3] == b"ply"
    assert c != a


def test_render_single_chunk_negative_index():
    """内核对负坐标也返回有效 ply (无限世界必含负象限)。"""
    data = render_single_chunk(-2, -3, world_seed=42)
    assert data[:3] == b"ply"
    assert len(data) > 1000


def test_render_single_chunk_deterministic_across_processes():
    """跨进程字节一致: render-on-demand 多实例服务器/内容寻址缓存的硬要求。

    ply 无时间戳/无熵, 且渲染用 chunk_id 派生的本地 RNG -> 两个独立解释器
    渲染同一 chunk 必得同一 sha256。
    """
    root = Path(__file__).resolve().parent.parent
    code = (
        "import hashlib;"
        "from pipeline.render_chunk_to_ply import render_single_chunk;"
        "print(hashlib.sha256(render_single_chunk(2, -1, world_seed=7)).hexdigest())"
    )
    def run():
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=root,
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()
    assert run() == run()


def test_manifest_carries_infinite_grid_metadata(tmp_path):
    """render_chunkset 的 manifest 须含无限网格声明 + 全局 bounds + per-chunk aabb,
    让 viewer 能区分'越界→请求'与'真无内容', 并用真实 z_range 取代硬编码 0。"""
    manifest = _bake(tmp_path, (0, 2, 0, 2))

    # top-level 无限网格声明: on_demand 默认 false(保持静态行为), 带请求模板 + 恒定 seed
    grid = manifest["grid"]
    assert grid["on_demand"] is False
    assert grid["url_template"] == "/api/world/chunk/{x}/{y}.ply"
    assert grid["world_seed"] == 42
    # 不得引入 nested chunk_size_m (viewer 读 flat manifest.chunk_size_m)
    assert "chunk_size_m" not in grid
    assert manifest["chunk_size_m"] == 200

    # 全局 AABB 带真实 z 跨度
    b = manifest["bounds"]
    assert len(b["min"]) == 3 and len(b["max"]) == 3
    assert b["max"][2] > b["min"][2]  # z 有实际跨度, 非硬编码 0

    # 已烘焙索引范围 (闭区间)
    assert manifest["baked_extent"] == {"x_min": 0, "x_max": 1, "y_min": 0, "y_max": 1}

    # per-chunk aabb
    for chunk in manifest["chunks"]:
        aabb = chunk["aabb"]
        assert len(aabb["min"]) == 3 and len(aabb["max"]) == 3
        assert aabb["max"][0] >= aabb["min"][0]


def test_manifest_is_json_serializable_with_native_floats(tmp_path):
    """aabb/bounds 必须是原生 float(非 np.float32), 否则 json.dumps 崩。"""
    manifest = _bake(tmp_path, (0, 1, 0, 1))
    # render_chunkset 已写盘, 再 round-trip 一次确认可序列化
    json.dumps(manifest)
    assert isinstance(manifest["bounds"]["min"][0], float)


def test_grid_range_centered_vs_origin():
    """--center 让网格以原点为中心(含负象限); 默认从 0 起。"""
    assert _grid_range(5, center=False) == (0, 5)
    assert _grid_range(5, center=True) == (-2, 3)   # -2,-1,0,1,2
    assert _grid_range(4, center=True) == (-2, 2)   # -2,-1,0,1
    assert _grid_range(1, center=True) == (0, 1)


def test_render_single_chunk_lod_returns_deterministic_subset():
    """render-on-demand 端点须能按 LOD 返回(省带宽): lod0(8%)<lod1(30%)<full;
    lod=None/2 全量; 子集确定(跨调用一致)。"""
    full = render_single_chunk(1, 1, world_seed=42)
    n_full = _ply_count(full)
    lod0 = render_single_chunk(1, 1, world_seed=42, lod=0)
    lod1 = render_single_chunk(1, 1, world_seed=42, lod=1)
    assert _ply_count(lod0) == max(1, int(n_full * DEFAULT_LOD_FRACTIONS[0]))
    assert _ply_count(lod1) == max(1, int(n_full * DEFAULT_LOD_FRACTIONS[1]))
    assert _ply_count(lod0) < _ply_count(lod1) < n_full
    assert lod0[:3] == b"ply"
    # lod=2 或 None → 全量
    assert render_single_chunk(1, 1, world_seed=42, lod=2) == full
    # 确定性: 同 lod 跨调用字节一致
    assert render_single_chunk(1, 1, world_seed=42, lod=0) == lod0


def test_lod_subset_is_stable_across_processes():
    """LOD 子集选择(argsort scale)须跨进程/平台确定 —— stable argsort, 非默认 quicksort。"""
    root = Path(__file__).resolve().parent.parent
    code = (
        "import hashlib;"
        "from pipeline.render_chunk_to_ply import render_single_chunk;"
        "print(hashlib.sha256(render_single_chunk(2, 3, world_seed=9, lod=0)).hexdigest())"
    )
    def run():
        proc = subprocess.run([sys.executable, "-c", code], cwd=root,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()
    assert run() == run()


def test_manifest_and_layout_written_lf_not_crlf(tmp_path):
    """world manifest 与 layout JSON 须 LF 字节可复现(跨平台一致), 与 trust root
    (registration.json/recon_manifest.json 已强制 LF)惯例统一。Windows write_text
    默认把 \\n 转 \\r\\n → 跨平台字节分歧, 会破坏 render-on-demand 的 layout 缓存一致性。"""
    layouts = tmp_path / "layouts"
    generate_layouts_mock(1, 42, layouts)   # 生产路径写 layout JSON
    render_chunkset(
        layouts_dir=layouts, output_dir=tmp_path / "web",
        chunk_range=(0, 1, 0, 1), assets_dir=None, lod_levels={0: 0.1},
    )
    assert b"\r\n" not in (tmp_path / "web" / "manifest.json").read_bytes()
    assert b"\r\n" not in (layouts / "chunk_0_0.json").read_bytes()


def test_render_single_chunk_with_registry_is_deterministic_and_pure(tmp_path):
    """HANDOFF-003 §4 锁定: render-on-demand 端点若服务真实素材 (registry≠None), 须
    (a) 跨调用字节确定 (内容寻址缓存前提), (b) 真消费素材 (与合成代理字节不同),
    (c) 纯读无副作用 (绝不动 registry.json trust root)。此前只在 scratchpad 临测,
    现落库为回归, 保护 codex 即将在此内核上建的端点。"""
    from pipeline.assets import AssetRegistry
    from pipeline.mock_assets import seed_registry

    assets_dir = tmp_path / "assets"
    seed_registry(assets_dir)
    registry_path = assets_dir / "registry.json"
    before = registry_path.read_bytes()

    a = render_single_chunk(1, 2, world_seed=42, registry=AssetRegistry(assets_dir))
    b = render_single_chunk(1, 2, world_seed=42, registry=AssetRegistry(assets_dir))
    proxy = render_single_chunk(1, 2, world_seed=42, registry=None)

    assert a[:3] == b"ply"
    assert a == b, "真实素材路径须字节确定 (内容寻址缓存前提)"
    assert a != proxy, "registry≠None 须真消费素材, 与合成代理字节不同"
    assert registry_path.read_bytes() == before, \
        "渲染须纯读: 绝不写 registry.json trust root (无副作用)"


@pytest.mark.parametrize("bad", [1.5, math.nan, math.inf, -math.inf, "3", None])
def test_render_single_chunk_rejects_non_integer_coords(bad):
    """内核对非整数/NaN/inf 坐标须 fail-closed 给出清晰 ValueError, 而非在
    MockLayoutGenerator 深处抛未分类 TypeError。codex 未来的 /api/world/chunk
    路由把 URL 段传进来前, 这是最后一道类型闸。"""
    with pytest.raises(ValueError, match="integer"):
        render_single_chunk(bad, 0, world_seed=42)
    with pytest.raises(ValueError, match="integer"):
        render_single_chunk(0, bad, world_seed=42)


def test_render_single_chunk_accepts_numpy_integers():
    """np.int64 等 numpy 整数应被接受 (coerce 到 int), 与 python int 字节一致 ——
    路由/调度层常产出 numpy 整数, 不该误伤。"""
    a = render_single_chunk(np.int64(3), np.int64(1), world_seed=42)
    b = render_single_chunk(3, 1, world_seed=42)
    assert a == b


def test_centered_bake_includes_negative_chunks(tmp_path):
    """中心化烘焙 + 负索引渲染修复 → manifest 含负坐标 chunk 且渲染成功。"""
    lo, hi = _grid_range(3, center=True)  # (-1, 2)
    out = tmp_path / "layouts"
    generate_layouts_mock(3, 42, out, center=True)
    assert (out / "chunk_-1_-1.json").exists()
    manifest = render_chunkset(
        layouts_dir=out, output_dir=tmp_path / "web",
        chunk_range=(lo, hi, lo, hi), assets_dir=None, lod_levels={0: 0.1},
    )
    coords = {(c["x"], c["y"]) for c in manifest["chunks"]}
    assert (-1, -1) in coords
    assert manifest["baked_extent"] == {"x_min": -1, "x_max": 1, "y_min": -1, "y_max": 1}
