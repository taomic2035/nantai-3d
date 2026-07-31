#!/usr/bin/env python3
"""把一个大重建 3DGS PLY 空间分块成可流式的 chunk + LOD + manifest。

为什么: 真实重建 (COLMAP + 云 GPU 3DGS) 产出【单个】可能上百万高斯的 .ply, viewer 只能
整块加载 —— 大场景下载慢、无空间裁剪。分块后 viewer 只载相机附近的块 (与合成村庄的分块
流式同构), 让大真实重建也能 360° 任意坐标漫游。

铁律: 纯空间重打包 —— 不改几何/坐标/provenance。每个高斯恰好落一个块 (无损不重复);
每块继承源的 frame_id/units/transform 历史, manifest 如实记录源坐标契约。分块**不会**
把 preview-only 变成 metric-aligned —— 米制要在对齐那步挣 (见 docs/real-data-workflow.md)。

用法 (通常在 normalize / flatten / import 之后, 对已对齐的重建产物做):
    python scripts/chunk_reconstruction.py trained/point_cloud.ply --out-dir web/data/recon-chunks
    python scripts/chunk_reconstruction.py scene.ply --out-dir out --chunk-size-m 25

仅依赖 numpy + plyfile (本仓库已装)。零 GPU。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from pipeline.gaussian_scene import GaussianScene
from pipeline.spatial_chunk import (
    DEFAULT_CHUNK_SIZE_M,
    DEFAULT_LOD_FRACTIONS,
    MANIFEST_NAME,
    partition_scene_to_chunks,
)


def _stable_read_bytes(path: Path) -> bytes:
    """Read an authoritative input through a single O_NOFOLLOW descriptor.

    ``lstat`` verifies the path is a regular file, ``os.open`` with
    ``O_NOFOLLOW`` opens the descriptor without following symlinks, and
    ``fstat`` re-verifies that the opened descriptor is the same regular file
    (type, inode, device) so a symlink swap between check and open is rejected.
    A post-read ``lstat`` recheck detects a swap between open and return.  The
    recon-manifest bytes feed ``recon_manifest_sha256`` (the chunk manifest's
    source provenance), so a symlinked manifest must not be hashed as if it
    were the intended regular file.
    """
    try:
        before = path.lstat()
    except OSError as exc:
        raise SystemExit(f"--recon-manifest 无法检查: {path}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise SystemExit(f"--recon-manifest 不是常规文件: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        stream = os.fdopen(descriptor, "rb")
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    with stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IFMT(opened.st_mode) != stat.S_IFMT(before.st_mode)
            or opened.st_ino != before.st_ino
            or opened.st_dev != before.st_dev
        ):
            raise SystemExit(f"--recon-manifest 在读取前被替换: {path}")
        payload = stream.read()
    try:
        after = path.lstat()
    except OSError as exc:
        raise SystemExit(f"--recon-manifest 在读取中被替换: {path}") from exc
    if (
        stat.S_IFMT(before.st_mode) != stat.S_IFMT(after.st_mode)
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
        or len(payload) != before.st_size
    ):
        raise SystemExit(f"--recon-manifest 在读取中被替换: {path}")
    return payload


def _source_provenance(recon_manifest: Path | None) -> dict | None:
    """从源 recon_manifest.json 取信任判定 + 其内容寻址 sha (供 chunks.json 诚实标注/回溯)。

    只搬运源已挣得的判定, 从不产生判定; manifest 缺 geometry_usability 时该键缺席 (未知)。
    """
    if recon_manifest is None:
        return None
    if not recon_manifest.is_file():
        raise SystemExit(f"--recon-manifest 文件不存在: {recon_manifest}")
    raw = _stable_read_bytes(recon_manifest)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"--recon-manifest 不是合法 JSON: {recon_manifest}: {exc}") from exc
    provenance: dict = {"recon_manifest_sha256": hashlib.sha256(raw).hexdigest()}
    usability = (parsed.get("provenance") or {}).get("geometry_usability")
    if usability is not None:  # 缺席即未知, 不编造
        provenance["geometry_usability"] = usability
    full_path = parsed.get("full_3dgs")
    full_descriptor = (parsed.get("artifacts") or {}).get("full_3dgs")
    if (
        isinstance(full_path, str)
        and isinstance(full_descriptor, dict)
        and full_descriptor.get("path") == full_path
        and full_descriptor.get("fidelity") == "full-3dgs"
    ):
        provenance["full_3dgs"] = True
        provenance["render_fidelity"] = "full-3dgs"
    return provenance


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="把大重建 3DGS PLY 空间分块为可流式 chunk + LOD + manifest"
    )
    ap.add_argument("ply", type=Path, help="输入 3DGS PLY (重建产物)")
    ap.add_argument(
        "--out-dir", type=Path, required=True, help="输出目录 (写 chunk_*.ply + LOD + chunks.json)"
    )
    ap.add_argument(
        "--chunk-size-m",
        type=float,
        default=DEFAULT_CHUNK_SIZE_M,
        help=f"XY 网格边长, 米 (默认 {DEFAULT_CHUNK_SIZE_M:g})",
    )
    ap.add_argument(
        "--recon-manifest",
        type=Path,
        default=None,
        help="源 recon_manifest.json: 把其 geometry_usability 判定 + 该 manifest "
        "的内容寻址 sha256 记入 chunks.json, 让消费者能诚实标注信任等级并回溯 "
        "(不提供则该字段缺席 = 未知, 绝不编造)",
    )
    args = ap.parse_args(argv)

    if not args.ply.is_file():
        raise SystemExit(f"文件不存在: {args.ply}")
    source_provenance = _source_provenance(args.recon_manifest)
    scene = GaussianScene.load_ply(args.ply)
    manifest = partition_scene_to_chunks(
        scene, args.out_dir, chunk_size_m=args.chunk_size_m, source_provenance=source_provenance
    )

    source = manifest["source"]
    print(
        f"[OK] {manifest['total_points']} 高斯 → {manifest['total_chunks']} 块 "
        f"({manifest['chunk_size_m']:g}m 网格) → {args.out_dir / MANIFEST_NAME}"
    )
    print(
        f"  源坐标契约 (未被分块改动): frame_id={source['frame_id']} "
        f"units={source['units']} "
        f"geometry_usability={source.get('geometry_usability', '未知(未给 --recon-manifest)')}"
    )
    print(
        f"  LOD: {dict(DEFAULT_LOD_FRACTIONS)} + lod2=全量; "
        f"bounds={manifest['bounds']['min']} .. {manifest['bounds']['max']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
