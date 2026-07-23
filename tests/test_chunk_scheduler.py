"""chunk_scheduler 单元测试: LRU 缓存 + 无限世界 chunk 调度核心机制。

覆盖 LRUChunkCache (CRUD / LRU 淘汰 / 访问更新顺序 / 重复 put) 和
ChunkScheduler (world_to_chunk floor division / get_or_generate 三路径 /
get_visible_chunks 视野调度 / simulate_player_walk 模拟行走)。

本模块是 render-on-demand 内核的调度层, 纯 Python 无外部依赖。
"""

from __future__ import annotations

from pipeline.chunk_scheduler import ChunkScheduler, LRUChunkCache
from pipeline.mock_layout import MockLayoutGenerator
from pipeline.schema import ChunkLayout


def _make_layout(cx: int = 0, cy: int = 0, seed: int = 42) -> ChunkLayout:
    """生成一个确定性 layout 供 LRU 测试使用。"""
    return MockLayoutGenerator(world_seed=seed).generate_chunk(cx, cy)


# ============================================================
# LRUChunkCache
# ============================================================


class TestLRUChunkCache:
    """LRU 缓存基本行为 + 淘汰策略。"""

    def test_put_then_get(self):
        cache = LRUChunkCache(maxsize=4)
        layout = _make_layout(0, 0)
        assert cache.get("missing") is None
        cache.put("a", layout)
        assert cache.get("a") is layout

    def test_lru_eviction_order(self):
        """maxsize=2: put a, b, c → a 被淘汰 (最久未用)。"""
        cache = LRUChunkCache(maxsize=2)
        la = _make_layout(0, 0)
        lb = _make_layout(1, 0)
        lc = _make_layout(2, 0)
        cache.put("a", la)
        cache.put("b", lb)
        cache.put("c", lc)
        assert cache.get("a") is None  # 被淘汰
        assert cache.get("b") is lb
        assert cache.get("c") is lc

    def test_get_updates_lru_order(self):
        """maxsize=2: put a, b, get a, put c → b 被淘汰 (a 刚被访问)。"""
        cache = LRUChunkCache(maxsize=2)
        la = _make_layout(0, 0)
        lb = _make_layout(1, 0)
        lc = _make_layout(2, 0)
        cache.put("a", la)
        cache.put("b", lb)
        assert cache.get("a") is la  # 访问 a → 移到末尾
        cache.put("c", lc)
        assert cache.get("b") is None  # b 被淘汰
        assert cache.get("a") is la
        assert cache.get("c") is lc

    def test_put_existing_key_updates_value_and_moves_to_end(self):
        """重复 put 同一 key: 更新值, 移到末尾 (不被淘汰)。"""
        cache = LRUChunkCache(maxsize=2)
        la = _make_layout(0, 0)
        lb = _make_layout(1, 0)
        lc = _make_layout(2, 0)  # 新值替换 a
        cache.put("a", la)
        cache.put("b", lb)
        cache.put("a", lc)  # 更新 a 的值, a 移到末尾
        assert cache.get("a") is lc  # 值已更新
        # 此时顺序: b, a (a 在末尾)。再 put c → b 被淘汰
        ld = _make_layout(3, 0)
        cache.put("c", ld)
        assert cache.get("b") is None  # b 被淘汰
        assert cache.get("a") is lc


# ============================================================
# ChunkScheduler.world_to_chunk
# ============================================================


class TestWorldToChunk:
    """世界坐标 → chunk 坐标 (Python floor division 语义)。"""

    def test_origin(self):
        s = ChunkScheduler(chunk_size_m=200)
        assert s.world_to_chunk(0, 0) == (0, 0)

    def test_inside_chunk(self):
        s = ChunkScheduler(chunk_size_m=200)
        assert s.world_to_chunk(199, 199) == (0, 0)

    def test_chunk_boundary(self):
        s = ChunkScheduler(chunk_size_m=200)
        assert s.world_to_chunk(200, 0) == (1, 0)
        assert s.world_to_chunk(0, 200) == (0, 1)

    def test_negative_coordinates_floor(self):
        """负坐标用 floor division: -1 → chunk -1, -200 → chunk -1, -201 → chunk -2。"""
        s = ChunkScheduler(chunk_size_m=200)
        assert s.world_to_chunk(-1, 0) == (-1, 0)
        assert s.world_to_chunk(-200, 0) == (-1, 0)
        assert s.world_to_chunk(-201, 0) == (-2, 0)

    def test_custom_chunk_size(self):
        s = ChunkScheduler(chunk_size_m=100)
        assert s.world_to_chunk(99, 99) == (0, 0)
        assert s.world_to_chunk(100, 0) == (1, 0)


# ============================================================
# ChunkScheduler.get_or_generate
# ============================================================


class TestGetOrGenerate:
    """三路径: 实时生成 / 缓存命中 / 磁盘加载 + 持久化 + 确定性。"""

    def test_generate_increments_stats(self):
        s = ChunkScheduler(world_seed=42, cache_size=4)
        layout = s.get_or_generate(0, 0)
        assert isinstance(layout, ChunkLayout)
        assert s.stats["generated"] == 1
        assert s.stats["cached_hits"] == 0

    def test_cache_hit_increments_stats(self):
        s = ChunkScheduler(world_seed=42, cache_size=4)
        s.get_or_generate(0, 0)  # 生成
        s.get_or_generate(0, 0)  # 缓存命中
        assert s.stats["generated"] == 1
        assert s.stats["cached_hits"] == 1

    def test_persists_to_disk(self, tmp_path):
        layouts = tmp_path / "layouts"
        s = ChunkScheduler(world_seed=42, layouts_dir=layouts)
        s.get_or_generate(0, 0)
        assert (layouts / "chunk_0_0.json").exists()

    def test_disk_load_path(self, tmp_path):
        """layouts_dir 存在文件时, 新 scheduler 从磁盘加载 (cached_hits++)。"""
        layouts = tmp_path / "layouts"
        # 第一次: 生成 + 持久化
        s1 = ChunkScheduler(world_seed=42, layouts_dir=layouts)
        layout1 = s1.get_or_generate(0, 0)
        assert s1.stats["generated"] == 1
        # 第二次: 新 scheduler, 缓存空, 从磁盘加载
        s2 = ChunkScheduler(world_seed=42, layouts_dir=layouts)
        layout2 = s2.get_or_generate(0, 0)
        assert s2.stats["generated"] == 0
        assert s2.stats["cached_hits"] == 1
        # 字节一致 (round-trip)
        assert layout1.model_dump_json() == layout2.model_dump_json()

    def test_deterministic_same_seed(self):
        """相同 seed+chunk → 相同 layout (跨 scheduler 实例)。"""
        s1 = ChunkScheduler(world_seed=42)
        s2 = ChunkScheduler(world_seed=42)
        l1 = s1.get_or_generate(3, 7)
        l2 = s2.get_or_generate(3, 7)
        assert l1.model_dump_json() == l2.model_dump_json()

    def test_different_seed_different_layout(self):
        """不同 seed → 不同 layout (确定性 RNG 保证)。"""
        s1 = ChunkScheduler(world_seed=42)
        s2 = ChunkScheduler(world_seed=999999)
        l1 = s1.get_or_generate(0, 0)
        l2 = s2.get_or_generate(0, 0)
        assert l1.model_dump_json() != l2.model_dump_json()


# ============================================================
# ChunkScheduler.get_visible_chunks
# ============================================================


class TestGetVisibleChunks:
    """视野半径调度 + active 集合 + evicted 统计。"""

    def test_view_radius_0_returns_single_chunk(self):
        s = ChunkScheduler(view_radius=0, cache_size=4)
        chunks = s.get_visible_chunks(0, 0)
        assert len(chunks) == 1

    def test_view_radius_1_returns_9_chunks(self):
        s = ChunkScheduler(view_radius=1, cache_size=16)
        chunks = s.get_visible_chunks(0, 0)
        assert len(chunks) == 9

    def test_view_radius_2_returns_25_chunks(self):
        s = ChunkScheduler(view_radius=2, cache_size=64)
        chunks = s.get_visible_chunks(0, 0)
        assert len(chunks) == 25

    def test_active_set_and_evicted_stats(self):
        """移动出视野的 chunk 计入 evicted。"""
        s = ChunkScheduler(view_radius=0, chunk_size_m=200, cache_size=4)
        # 第一次: 在 (0,0), active = {"0_0"}, evicted += 0
        s.get_visible_chunks(0, 0)
        assert s.active == {"0_0"}
        assert s.stats["evicted"] == 0
        # 移动到 (200, 0) = chunk (1, 0), 旧 chunk 0_0 离开视野
        s.get_visible_chunks(200, 0)
        assert s.active == {"1_0"}
        assert s.stats["evicted"] == 1

    def test_cache_reuse_across_visible_calls(self):
        """相邻视野共享 chunk → 缓存命中。"""
        s = ChunkScheduler(view_radius=1, chunk_size_m=200, cache_size=16)
        s.get_visible_chunks(0, 0)  # 生成 9 chunks
        assert s.stats["generated"] == 9
        # 移动 100m (仍在 chunk 0,0), 视野完全重叠
        s.get_visible_chunks(100, 0)
        # 应该全部缓存命中
        assert s.stats["cached_hits"] == 9


# ============================================================
# ChunkScheduler.simulate_player_walk
# ============================================================


class TestSimulatePlayerWalk:
    """模拟玩家行走集成流程。"""

    def test_basic_walk(self):
        s = ChunkScheduler(view_radius=0, chunk_size_m=200, cache_size=4)
        result = s.simulate_player_walk((0, 0), (400, 0), steps=4)
        assert len(result["steps"]) == 5  # steps + 1
        assert result["steps"][0]["step"] == 0
        assert result["steps"][-1]["step"] == 4
        assert "final_stats" in result
        # 从 chunk 0,0 走到 chunk 2,0, 至少生成 1 个 chunk
        assert result["final_stats"]["generated"] >= 1

    def test_walk_stats_accumulate(self):
        """走过多个 chunk: 生成与缓存命中交替累积。"""
        s = ChunkScheduler(view_radius=0, chunk_size_m=200, cache_size=4)
        result = s.simulate_player_walk((0, 0), (400, 0), steps=4)
        # step 0: chunk (0,0) 生成; step 1: (0,0) 命中;
        # step 2: chunk (1,0) 生成; step 3: (1,0) 命中;
        # step 4: chunk (2,0) 生成 → generated=3, cached=2, total=5
        stats = result["final_stats"]
        total = stats["generated"] + stats["cached_hits"]
        assert total == 5  # 5 步, 每步 1 chunk (view_radius=0)
        assert stats["generated"] == 3
        assert stats["cached_hits"] == 2
