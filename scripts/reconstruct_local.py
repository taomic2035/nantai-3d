#!/usr/bin/env python3
"""本机一键重建：照片/视频目录 → COLMAP 位姿 → Brush 训练 3DGS → 导入本仓库。

把已实测跑通的全本机链路串成一条命令（无需 NVIDIA/CUDA；用 third/ 下的
COLMAP no-CUDA 与 Brush）。产物落到 web/data/recon，随后 `python make.py serve`
即可 360° 漫游。诚实：sfm-local 非米制 → 结果标 preview-only；要米制见
docs/real-data-workflow.md。用法与限制见 docs/manual/reconstruction-setup.md。

    python scripts/reconstruct_local.py <照片目录> [--steps 3000] [--max-res 1024]

依赖二进制（默认 third/，也接受 PATH）：
    third/colmap/bin/colmap.exe   third/brush/brush_app.exe
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _find(name: str, *candidates: Path) -> str:
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit(
        f"找不到 {name}；请下载到 third/（见 third/README.md）或加入 PATH。")


def _colmap_group(colmap: str) -> str:
    """COLMAP use_gpu 选项组：'Feature'(现行)/'Sift'(旧)——探测已装 build。"""
    try:
        out = subprocess.run([colmap, "feature_extractor", "-h"],
                             capture_output=True, text=True, timeout=30)
        text = (out.stdout or "") + (out.stderr or "")
        if "SiftExtraction.use_gpu" in text and "FeatureExtraction.use_gpu" not in text:
            return "Sift"
    except (OSError, subprocess.SubprocessError):
        pass
    return "Feature"


def run(cmd: list[str], *, log: Path | None = None) -> None:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    if log is not None:
        with log.open("a", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT)
    else:
        proc = subprocess.run(cmd)
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log else ""
        raise SystemExit(f"命令失败 (exit {proc.returncode})\n{tail}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本机一键 3DGS 重建 (COLMAP+Brush)")
    ap.add_argument("photos", type=Path, help="图片目录 (含重叠照片/视频帧)")
    ap.add_argument("--work", type=Path, default=ROOT / "recon" / "local_ws",
                    help="工作目录 (默认 recon/local_ws)")
    ap.add_argument("--steps", type=int, default=3000,
                    help="Brush 训练步数 (越多越好越慢; 默认 3000)")
    ap.add_argument("--max-res", type=int, default=1024, help="训练最大分辨率")
    ap.add_argument("--fps", type=float, default=2.0, help="视频抽帧帧率 (仅视频输入)")
    ap.add_argument("--max-frames", type=int, default=300,
                    help="视频抽帧上限 (仅视频输入; COLMAP CPU 建议 ≤300)")
    ap.add_argument("--sequential", action="store_true",
                    help="图片按拍摄顺序命名(航拍/环绕连拍)时用 sequential_matcher; "
                         "视频输入自动开启")
    ap.add_argument("--colmap-gpu", action="store_true",
                    help="COLMAP SIFT 用 GPU (默认 CPU, 无 N 卡/headless 更可靠)")
    ap.add_argument("--web", type=Path, default=ROOT / "web" / "data" / "recon",
                    help="viewer 数据输出 (默认 web/data/recon)")
    args = ap.parse_args(argv)

    colmap = _find("colmap", ROOT / "third/colmap/bin/colmap.exe",
                   ROOT / "third/colmap/colmap.exe")
    brush = _find("brush_app", ROOT / "third/brush/brush_app.exe")
    py = sys.executable
    ws = args.work
    ws.mkdir(parents=True, exist_ok=True)

    # 输入可以是图片目录, 或单个视频文件 (自动抽帧)。
    from pipeline.ingest import is_video
    photos = args.photos
    ordered = args.sequential  # 视频帧时序连续 -> sequential_matcher
    if photos.is_file() and is_video(photos):
        print(f"\n=== 0/4 视频抽帧 (fps={args.fps}, 上限 {args.max_frames}) ===")
        vin = ws / "video_in"
        vin.mkdir(parents=True, exist_ok=True)
        shutil.copy2(photos, vin / photos.name)
        frames = ws / "frames"
        run([py, "-m", "pipeline.ingest", "--input", str(vin), "--output", str(frames),
             "--fps", str(args.fps), "--max-frames", str(args.max_frames)])
        photos = frames
        ordered = True  # 抽帧 frame_000000.jpg… 字典序即时序
    elif not photos.is_dir() or not any(photos.iterdir()):
        raise SystemExit(f"输入需为非空图片目录或视频文件: {photos}")
    args.photos = photos  # 后续步骤统一用抽帧后的目录
    db = ws / "colmap.db"
    sparse = ws / "sparse"
    clog = ws / "colmap.log"
    clog.write_text("", encoding="utf-8")

    print("\n=== 1/4 COLMAP 位姿 (CPU) —— 图多会较慢 ===")
    grp = _colmap_group(colmap)
    gpu = "1" if args.colmap_gpu else "0"
    run([colmap, "feature_extractor", "--database_path", str(db),
         "--image_path", str(args.photos), "--ImageReader.camera_model",
         "SIMPLE_RADIAL", f"--{grp}Extraction.use_gpu", gpu], log=clog)
    # COLMAP 数据集布局: Brush 要 <root>/images/ + <root>/sparse/0/
    # 时序连续帧(视频/--sequential): 只配相邻帧, CPU 上远快于 O(n²) 全配对;
    # 无序照片: ≤400 用 exhaustive, 更多退化到 sequential(仍需按拍摄顺序命名)。
    n = sum(1 for p in args.photos.rglob("*") if p.is_file() and p.suffix.lower()
            in {".jpg", ".jpeg", ".png"})
    matcher = "sequential_matcher" if (ordered or n > 400) else "exhaustive_matcher"
    print(f"    匹配器: {matcher} ({'时序连续' if ordered else '无序'}, {n} 图)")
    run([colmap, matcher, "--database_path", str(db),
         f"--{grp}Matching.use_gpu", gpu], log=clog)
    sparse.mkdir(exist_ok=True)
    run([colmap, "mapper", "--database_path", str(db),
         "--image_path", str(args.photos), "--output_path", str(sparse)], log=clog)
    if not (sparse / "0").is_dir():
        raise SystemExit("COLMAP 未产出模型 (sparse/0 不存在)：重叠不足？多拍/绕拍。")
    images_dir = ws / "images"
    if not images_dir.exists():
        shutil.copytree(args.photos, images_dir)

    print(f"\n=== 2/4 Brush 训练 3DGS ({args.steps} 步, max-res {args.max_res}) ===")
    trained = ws / "trained.ply"
    run([brush, str(ws), "--total-steps", str(args.steps),
         "--max-resolution", str(args.max_res), "--export-every", str(args.steps),
         "--export-path", str(ws), "--export-name", "trained.ply"], log=ws / "brush.log")
    export = trained if trained.is_file() else next(ws.glob("*.ply"), None)
    if export is None:
        raise SystemExit("Brush 未导出 .ply：见 brush.log（可能显存不足，调小 --max-res）")

    print("\n=== 3/4 归一化四元数 + 生成导入契约 ===")
    run([py, str(ROOT / "scripts/normalize_ply_quats.py"), str(export)])
    run([py, str(ROOT / "scripts/prepare_import.py"), str(export),
         "--out-dir", str(ws)])

    print("\n=== 4/4 导入 → viewer 数据 ===")
    run([py, "-m", "pipeline.reconstruct", "--engine", "import",
         "--registration", str(ws / "registration.json"),
         "--splat", str(ws / "splat-input.json"),
         "--out", str(ws / "out"), "--web", str(args.web),
         "--dedup-voxel", "0", "--replace-margin", "0", "--photos", str(args.photos)])

    print(f"\n[OK] 本机重建完成 → {args.web}")
    print("查看 360° 漫游:  python make.py serve   # http://127.0.0.1:8000/web/studio/")
    print("结果为 preview-only(非米制)；要真实尺度见 docs/real-data-workflow.md。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
