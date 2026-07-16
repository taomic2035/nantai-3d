"""
Nantai 无限村庄世界生成器 - 统一 CLI 入口

一键完成: layout 生成 → ply 渲染 → manifest 输出

用法:
    # 默认生成 5x5 世界
    python -m pipeline.generate_world

    # 生成 10x10 大世界
    python -m pipeline.generate_world --size 10

    # 使用 GLM-4.6 (需 ZHIPU_API_KEY)
    python -m pipeline.generate_world --use-glm

    # 指定 seed
    python -m pipeline.generate_world --seed 1234 --size 8

    # 跳过 ply 渲染 (只生成 layouts)
    python -m pipeline.generate_world --no-ply
"""
import argparse
import asyncio
import time
from pathlib import Path

from loguru import logger

from pipeline.mock_layout import MockLayoutGenerator
from pipeline.render_chunk_to_ply import render_chunkset


def _grid_range(size: int, center: bool = False) -> tuple[int, int]:
    """网格索引半开区间 [lo, hi)。center 时以原点为中心 (含负象限), 否则从 0 起。

    center: size=5 -> (-2, 3) 即 -2,-1,0,1,2; size=4 -> (-2, 2) 即 -2,-1,0,1。
    """
    if not center:
        return (0, size)
    lo = -(size // 2)
    return (lo, lo + size)


def generate_layouts_mock(
    size: int, seed: int, out_dir: Path, center: bool = False
) -> dict:
    """用 MockLayoutGenerator 生成 N×N 个 layout (center 时以原点为中心, 含负象限)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    gen = MockLayoutGenerator(world_seed=seed)
    n_buildings = 0
    n_roads = 0
    n_veg = 0
    n_water = 0
    lo, hi = _grid_range(size, center)
    for cx in range(lo, hi):
        for cy in range(lo, hi):
            layout = gen.generate_chunk(cx, cy)
            f = out_dir / f"chunk_{cx}_{cy}.json"
            f.write_text(layout.model_dump_json(indent=2), encoding="utf-8")
            n_buildings += len(layout.buildings)
            n_roads += len(layout.roads)
            n_veg += len(layout.vegetation)
            n_water += len(layout.water)
    return {
        "chunks": size * size,
        "buildings": n_buildings,
        "roads": n_roads,
        "vegetation": n_veg,
        "water": n_water,
    }


async def generate_layouts_glm(
    size: int, seed: int, out_dir: Path, center: bool = False
) -> dict:
    """用 GLM-4.6 异步生成 N×N 个 layout (center 时以原点为中心, 含负象限)"""
    from pipeline.glm_client import GLMLayoutGenerator
    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    gen = GLMLayoutGenerator()

    assets = [
        {"id": "house_wood_01", "category": "houses", "footprint": [4, 4]},
        {"id": "house_stone_01", "category": "houses", "footprint": [5, 4]},
        {"id": "tree_pine_01", "category": "trees"},
    ]
    climate = {"type": "hill", "density": 0.3, "vegetation": "subtropical"}

    n_buildings = 0
    n_roads = 0
    n_veg = 0
    n_water = 0

    lo, hi = _grid_range(size, center)
    for cx in range(lo, hi):
        for cy in range(lo, hi):
            layout = await gen.generate_chunk(
                cx, cy, world_seed=seed,
                climate=climate, assets=assets,
            )
            f = out_dir / f"chunk_{cx}_{cy}.json"
            f.write_text(layout.model_dump_json(indent=2), encoding="utf-8")
            n_buildings += len(layout.buildings)
            n_roads += len(layout.roads)
            n_veg += len(layout.vegetation)
            n_water += len(layout.water)

    return {
        "chunks": size * size,
        "buildings": n_buildings,
        "roads": n_roads,
        "vegetation": n_veg,
        "water": n_water,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Nantai 无限村庄世界生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--size", type=int, default=5, help="世界大小 N (N×N chunks, 默认 5)")
    parser.add_argument("--seed", type=int, default=42, help="世界种子 (默认 42)")
    parser.add_argument("--use-glm", action="store_true",
                        help="使用 GLM-4.6 (默认用 mock, 不需要 API key)")
    parser.add_argument("--no-ply", action="store_true",
                        help="跳过 ply 渲染, 只生成 layouts")
    parser.add_argument("--center", action="store_true",
                        help="以原点为中心生成 (含负象限 chunk), 而非从 (0,0) 起")
    parser.add_argument("--layouts-dir", default="layouts", help="layouts 输出目录")
    parser.add_argument("--web-data-dir", default="web/data", help="ply 输出目录")
    args = parser.parse_args()

    print("=" * 60)
    print("Nantai 世界生成器")
    print(f"  size: {args.size}x{args.size} = {args.size * args.size} chunks")
    print(f"  seed: {args.seed}")
    print(f"  generator: {'GLM-4.6' if args.use_glm else 'Mock'}")
    print(f"  ply 渲染: {'跳过' if args.no_ply else '启用'}")
    print("=" * 60)

    layouts_dir = Path(args.layouts_dir)
    t0 = time.time()

    # 阶段 1: 生成 layouts
    if args.use_glm:
        stats = asyncio.run(
            generate_layouts_glm(args.size, args.seed, layouts_dir, center=args.center))
    else:
        stats = generate_layouts_mock(args.size, args.seed, layouts_dir, center=args.center)

    t1 = time.time()
    logger.info(
        f"阶段 1 完成: {stats['chunks']} chunks, "
        f"{stats['buildings']} 建筑, {stats['roads']} 道路, "
        f"{stats['vegetation']} 植被, {stats['water']} 水系, "
        f"用时 {t1-t0:.2f}s"
    )

    # 阶段 2: 渲染 ply
    if not args.no_ply:
        lo, hi = _grid_range(args.size, args.center)
        manifest = render_chunkset(
            layouts_dir=layouts_dir,
            output_dir=args.web_data_dir,
            chunk_range=(lo, hi, lo, hi),
        )
        t2 = time.time()
        logger.info(
            f"阶段 2 完成: {manifest['total_chunks']} ply, "
            f"{manifest['total_points']} 高斯点, 用时 {t2-t1:.2f}s"
        )
        print(f"\n总用时: {t2-t0:.2f}s")
    else:
        print(f"\n总用时: {t1-t0:.2f}s")

    print("\n下一步:")
    print("  make serve")
    print("  浏览器打开: http://127.0.0.1:8000/web/studio/")


if __name__ == "__main__":
    main()
