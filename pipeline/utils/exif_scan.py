"""
L0 工具: EXIF / 元数据扫描
扫描照片 EXIF + 视频元数据, 输出设备/GPS/批次分布报告
支持手机/相机/无人机混合来源 (照片 + 视频)

用法:
    python -m pipeline.utils.exif_scan <目录> [输出.csv]
"""
import csv
import sys
from collections import Counter
from pathlib import Path

from loguru import logger

try:
    import exifread
except ImportError:
    print("请先安装: pip install exifread")
    sys.exit(1)


PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".flv", ".webm"}


def get_exif(path: Path) -> dict:
    """提取单张照片的 EXIF 元数据"""
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)

        def g(name):
            return str(tags.get(name, "")).strip()

        return {
            "file": path.name,
            "type": "photo",
            "path": str(path),
            "size_kb": round(path.stat().st_size / 1024, 1),
            "make": g("Image Make"),
            "model": g("Image Model"),
            "focal_mm": g("EXIF FocalLength"),
            "gps_lat": g("GPS GPSLatitude"),
            "gps_lon": g("GPS GPSLongitude"),
            "datetime": g("EXIF DateTimeOriginal"),
            "width": g("EXIF ExifImageWidth"),
            "height": g("EXIF ExifImageHeight"),
            "duration_s": "",
            "fps": "",
        }
    except Exception as e:
        logger.warning(f"读取 {path.name} 失败: {e}")
        return {"file": path.name, "type": "photo", "path": str(path),
                "size_kb": 0, "error": str(e)}


def get_video_meta(path: Path) -> dict:
    """提取视频元数据 (用 cv2)"""
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return {"file": path.name, "type": "video", "path": str(path),
                    "size_kb": round(path.stat().st_size / 1024, 1), "error": "open_failed"}

        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = total / fps if fps > 0 else 0
        cap.release()

        return {
            "file": path.name,
            "type": "video",
            "path": str(path),
            "size_kb": round(path.stat().st_size / 1024, 1),
            "make": "",
            "model": "",
            "focal_mm": "",
            "gps_lat": "",
            "gps_lon": "",
            "datetime": "",
            "width": w,
            "height": h,
            "duration_s": round(duration, 2),
            "fps": round(fps, 2),
        }
    except ImportError:
        return {"file": path.name, "type": "video", "path": str(path),
                "size_kb": round(path.stat().st_size / 1024, 1), "error": "cv2 not installed"}


def scan_all(src_dir: str | Path, output: str | Path | None = None) -> dict:
    """扫描目录下所有照片和视频"""
    src = Path(src_dir)
    if not src.exists():
        raise FileNotFoundError(f"目录不存在: {src}")

    files = sorted(p for p in src.rglob("*") if p.is_file())
    photos = [p for p in files if p.suffix.lower() in PHOTO_EXTS]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXTS]

    if not photos and not videos:
        logger.warning(f"未在 {src} 找到照片或视频")
        return {"items": [], "devices": {}, "gps_count": 0}

    logger.info(f"扫描 {len(photos)} 张照片 + {len(videos)} 个视频...")
    rows = [get_exif(p) for p in photos] + [get_video_meta(v) for v in videos]

    # CSV 输出
    if output:
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        logger.info(f"CSV 报告: {out}")

    # 摘要
    devices = Counter(
        f"{r['make']} {r['model']}".strip()
        for r in rows if r.get("make")
    )
    gps_count = sum(1 for r in rows if r.get("gps_lat"))
    days = Counter(r["datetime"][:10] for r in rows if r.get("datetime"))

    # 视频统计
    video_total_duration = sum(r.get("duration_s", 0) or 0 for r in rows if r["type"] == "video")
    video_total_size_mb = sum(r.get("size_kb", 0) for r in rows if r["type"] == "video") / 1024

    summary = {
        "items": rows,
        "total": len(rows),
        "photo_count": len(photos),
        "video_count": len(videos),
        "devices": dict(devices.most_common()),
        "gps_count": gps_count,
        "gps_coverage": f"{gps_count}/{len(rows)}",
        "shoot_days": dict(sorted(days.items())),
        "video_total_duration_s": round(video_total_duration, 2),
        "video_total_size_mb": round(video_total_size_mb, 2),
    }

    # 控制台打印
    print(f"\n{'=' * 50}")
    print(
        f"总数: {summary['total']} "
        f"(照片 {summary['photo_count']} + 视频 {summary['video_count']})"
    )
    print("\n=== 设备分布 ===")
    for k, v in summary["devices"].items():
        print(f"  {v:3d}  {k}")
    print("\n=== GPS 覆盖 ===")
    print(f"  {summary['gps_coverage']} 张带 GPS")
    print("\n=== 拍摄批次 ===")
    for d, c in summary["shoot_days"].items():
        print(f"  {d}: {c} 张")
    if videos:
        print("\n=== 视频统计 ===")
        print(f"  总时长: {video_total_duration:.1f}s ({video_total_duration/60:.1f} 分钟)")
        print(f"  总大小: {video_total_size_mb:.1f} MB")
    print(f"{'=' * 50}\n")

    return summary


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m pipeline.utils.exif_scan <目录> [输出.csv]")
        sys.exit(1)
    scan_all(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
