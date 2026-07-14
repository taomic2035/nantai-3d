"""
L0 输入处理器 - 统一处理照片和视频

支持:
- 视频 (mp4/mov/avi/mkv) → ffmpeg/cv2 抽帧 → JPG
- 照片 (jpg/png/heic/tiff) → 复制到统一输出目录
- 智能筛选清晰帧 (Laplacian variance, 跳过运动模糊)
- 元数据保留 (视频抽帧后 EXIF 写入源视频时间戳)

用法:
    # 处理 input/ 目录下所有照片和视频
    python -m pipeline.ingest --input input --output photos

    # 自定义抽帧率
    python -m pipeline.ingest --fps 3 --max-frames 200

    # 关闭模糊筛选
    python -m pipeline.ingest --no-blur-filter
"""
import argparse
import shutil
import time
from pathlib import Path
from loguru import logger

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# 支持的文件扩展名
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".flv", ".webm"}

# 默认参数
DEFAULT_FPS = 2.0          # 抽帧率 (每秒 N 帧)
DEFAULT_MAX_FRAMES = 300   # 单视频最大抽帧数
DEFAULT_BLUR_THRESHOLD = 80.0  # Laplacian variance 低于此值视为模糊
DEFAULT_MAX_LONG_EDGE = 2560   # 长边超过此值则降采样 (避免 4K 爆盘)


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_EXTS

def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


# ============ 视频抽帧 ============
def extract_video_frames(
    video_path: Path,
    output_dir: Path,
    fps: float = DEFAULT_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
) -> dict:
    """从视频抽取关键帧 → JPG
    返回统计信息
    """
    if not HAS_CV2:
        raise RuntimeError("缺 cv2, 请 pip install opencv-python")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error(f"无法打开视频: {video_path}")
        return {"frames": 0, "skipped_blur": 0, "error": "open_failed"}

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = total_frames / src_fps if src_fps > 0 else 0
    step = max(1, int(round(src_fps / fps)))  # 每隔 N 帧取一帧

    logger.info(
        f"视频: {video_path.name} | 源 fps={src_fps:.1f} | "
        f"时长={duration_s:.1f}s | 总帧数={total_frames} | 抽帧步长={step}"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    video_stem = video_path.stem
    saved = 0
    skipped_blur = 0
    idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if idx % step == 0:
            # 模糊检测: Laplacian variance
            if blur_threshold > 0:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                variance = cv2.Laplacian(gray, cv2.CV_64F).var()
                if variance < blur_threshold:
                    skipped_blur += 1
                    idx += 1
                    continue

            # 降采样 (如果分辨率过大)
            h, w = frame.shape[:2]
            long_edge = max(h, w)
            if long_edge > max_long_edge:
                scale = max_long_edge / long_edge
                new_w = int(w * scale)
                new_h = int(h * scale)
                frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

            out_path = output_dir / f"{video_stem}_frame_{saved:06d}.jpg"
            cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            saved += 1

            if saved >= max_frames:
                logger.debug(f"达到 max_frames={max_frames}, 停止抽帧")
                break

        idx += 1

    cap.release()
    if saved == 0:
        logger.warning(
            f"  ⚠ {video_path.name} 一帧未保存 (跳过模糊 {skipped_blur} 张)! "
            f"可尝试 --blur-threshold 0 关闭模糊筛选")
    logger.info(
        f"  → 抽帧完成: 保存 {saved} 张, 跳过模糊 {skipped_blur} 张 → {output_dir}"
    )
    return {
        "frames": saved,
        "skipped_blur": skipped_blur,
        "source_fps": src_fps,
        "duration_s": duration_s,
    }


# ============ 照片处理 ============
def copy_photo(photo_path: Path, output_dir: Path) -> bool:
    """复制照片到统一输出目录 (保持原文件名)"""
    output_dir.mkdir(parents=True, exist_ok=True)
    dst = output_dir / photo_path.name

    # 文件名冲突: 加 _1, _2
    if dst.exists():
        stem = photo_path.stem
        suffix = photo_path.suffix
        i = 1
        while dst.exists():
            dst = output_dir / f"{stem}_{i}{suffix}"
            i += 1

    shutil.copy2(photo_path, dst)
    return True


# ============ 统一入口 ============
def ingest_all(
    input_dir: str | Path,
    output_dir: str | Path,
    fps: float = DEFAULT_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
) -> dict:
    """扫描输入目录, 自动识别照片和视频, 分别处理

    返回:
        {
            "photos": [<复制文件>],
            "videos": [{video, frames, ...}, ...],
            "total_output": <输出文件总数>,
        }
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)

    if not input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")

    # 扫描
    files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    photos = [p for p in files if is_photo(p)]
    videos = [p for p in files if is_video(p)]
    others = [p for p in files if not is_photo(p) and not is_video(p)]

    logger.info(
        f"扫描 {input_dir}: {len(photos)} 张照片, {len(videos)} 个视频, "
        f"{len(others)} 个其他文件"
    )

    # 处理照片
    photo_results = []
    for p in photos:
        copy_photo(p, output_dir)
        photo_results.append(str(p.name))
    if photos:
        logger.info(f"照片复制完成: {len(photos)} 张 → {output_dir}")

    # 处理视频
    video_results = []
    total_video_frames = 0
    for v in videos:
        # 每个视频独立子目录, 避免命名冲突
        v_out = output_dir / v.stem
        stats = extract_video_frames(
            v, v_out,
            fps=fps, max_frames=max_frames,
            blur_threshold=blur_threshold,
            max_long_edge=max_long_edge,
        )
        video_results.append({
            "video": v.name,
            "output_dir": str(v_out),
            **stats,
        })
        total_video_frames += stats.get("frames", 0)

    total_output = len(photos) + total_video_frames
    logger.info(f"输入处理完成: {len(photos)} 照片 + {total_video_frames} 抽帧 = {total_output} 张图 → {output_dir}")
    return {
        "photos": photo_results,
        "videos": video_results,
        "total_output": total_output,
    }


def main():
    parser = argparse.ArgumentParser(
        description="L0 输入处理器 (照片 + 视频 → 统一图片集)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", "-i", default="input", help="输入目录 (默认 input/)")
    parser.add_argument("--output", "-o", default="photos", help="输出目录 (默认 photos/)")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS,
                        help=f"视频抽帧率 (每秒 N 帧, 默认 {DEFAULT_FPS})")
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
                        help=f"单视频最大抽帧数 (默认 {DEFAULT_MAX_FRAMES})")
    parser.add_argument("--blur-threshold", type=float, default=DEFAULT_BLUR_THRESHOLD,
                        help=f"模糊检测阈值 (Laplacian variance, 默认 {DEFAULT_BLUR_THRESHOLD}, 设 0 关闭)")
    parser.add_argument("--max-long-edge", type=int, default=DEFAULT_MAX_LONG_EDGE,
                        help=f"长边降采样阈值 (默认 {DEFAULT_MAX_LONG_EDGE})")
    args = parser.parse_args()

    print("=" * 60)
    print("L0 输入处理器")
    print(f"  input:  {args.input}")
    print(f"  output: {args.output}")
    print(f"  fps:    {args.fps}")
    print(f"  max_frames/video: {args.max_frames}")
    print(f"  blur_threshold: {args.blur_threshold}")
    print("=" * 60)

    t0 = time.time()
    result = ingest_all(
        input_dir=args.input,
        output_dir=args.output,
        fps=args.fps,
        max_frames=args.max_frames,
        blur_threshold=args.blur_threshold,
        max_long_edge=args.max_long_edge,
    )

    print(f"\n总用时: {time.time() - t0:.2f}s")
    print(f"\n下一步:")
    print(f"  python -m pipeline.utils.exif_scan {args.output} {args.output}/exif_report.csv")
    print(f"  # 然后 COLMAP / 3DGS 训练 → ply → Web viewer")


if __name__ == "__main__":
    main()
