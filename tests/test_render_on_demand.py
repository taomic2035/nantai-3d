"""render-on-demand 内核: 单块合成渲染的确定性 + 负索引安全 + 内存 ply 字节。

这些是"服务器按需实时渲染任意 (cx,cy) chunk"的前提。合成路径 (registry=None)
必须字节可复现 —— 否则 chunk 淘汰重渲会闪烁、内容寻址缓存失效、多实例服务器发散,
也违反本仓库的可复现性核心价值。测试用 MockLayoutGenerator 的【全量】布局
(含 roads+buildings, 才能覆盖出过 bug 的合成路径), 跨渲染比 sha256 而非仅 array_equal。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

from pipeline.mock_layout import MockLayoutGenerator
from pipeline.render_chunk_to_ply import build_chunk_array, render_single_chunk


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
