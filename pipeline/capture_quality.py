"""采集预检: 在跑 COLMAP 之前, 诚实评估这批照片值不值得等。

**为什么存在**: 手册 §4 记录的实测是 ~300 张无序照片在本机 CPU 上要 2–5+ 小时, 且"若重叠
不足, mapper 可能只注册部分图或不产模型"。用户可能白等数小时才拿到一个空结果。本模块用
单图就能测到的证据 (张数/清晰度/分辨率/EXIF) 提前给出**启发式**预警。

**它测不到什么 (必须一起说出去, 否则就是在骗人)**:
- **重叠度**——真正决定 SfM 成败的因素。它是图**之间**的关系, 单图分析测不出来。要知道
  重叠够不够, 只有真跑 COLMAP (或做特征匹配, 那本身就是最贵的那一步)。
- 曝光一致性、运动模糊方向、纹理是否足够独特、场景是否有玻璃/水面/天空。
因此本模块**永远不预测 SfM 成败**, 只报告"已知的坏消息"。verdict=likely 的含义是
"没发现明显硬伤", **不是**"能重建成功"。

耗时估计锚定手册 §4 的本机实测数 (i7-14700 CPU): ~100 图 ≈ 20–60 min; ~300 图 ≈ 2–5+ 小时。
由这两个锚点解出线性代价模型 (特征提取 O(n) + 匹配 O(pairs)), 只是**粗估**。
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from pipeline.ingest import _read_photo_exif
from pipeline.ingest_manifest import PHOTO_SOURCE_SUFFIXES

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    cv2 = None
    HAS_CV2 = False

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False


SCHEMA_VERSION = 1

# 与 pipeline.ingest.DEFAULT_BLUR_THRESHOLD 一致: 抽帧丢弃与预检告警用同一把尺子,
# 否则用户会看到"预检说清晰, ingest 却丢了它"这种自相矛盾。
DEFAULT_BLUR_THRESHOLD = 80.0

MIN_USEFUL_IMAGES = 20      # 手册: <20 张基本无望
RECOMMENDED_MIN_IMAGES = 50  # 手册: 50~300 张为宜
RECOMMENDED_MAX_IMAGES = 300
MIN_MEGAPIXELS = 1.0        # 低于约 1MP 特征点太少
BLUR_RATIO_WARN = 0.15
BLUR_RATIO_BLOCK = 0.50
MAX_LISTED_FILES = 20

# COLMAP 顺序匹配默认每图只匹配相邻若干图 (SequentialMatching.overlap 默认 10)。
SEQUENTIAL_OVERLAP = 10

# 手册 §4 实测锚点: (张数, 低估分钟, 高估分钟)。改这里等于改对外承诺的耗时, 要有实测依据。
MANUAL_ANCHORS = ((100, 20.0, 60.0), (300, 120.0, 300.0))

# 手册 §1 另有一个更小的硬实测: 30 图 ≈ 46 秒 (合成小场景, 分辨率低)。它比本模型在 n=30
# 的低估 (~3.9 min) 快约 5x。不拿它做锚点: 它是小合成场景, 与 100/300 图真实照片的
# 预期不同量级, 三点拟合会让模型对不上手册对外承诺的数字。代价是小批量明显过估 ——
# 过估比低估安全 (不会害人白等), 但必须**主动声明**, 见 small_batch_caution。
SMALL_BATCH_IMAGES = 50
_SMALL_BATCH_CAUTION = (
    "小批量 (<50 图) 的估计明显偏保守: 手册 §1 实测 30 图 ≈ 46 秒 (小合成场景), "
    "比本模型快约 5x。本模型锚定的是 100/300 图真实照片的预期, 外推到小批量会过估。"
    "**实际大概率比这里说的快** —— 往这个方向错是有意的 (宁可让你早点动手, 不害你白等)。"
)

_FRAME_NAME_RE = re.compile(r"frame[_-]?\d{3,}$", re.IGNORECASE)
_ORDERING_EVIDENCE_RATIO = 0.8
_DENSE_INTERVAL_S = 5.0


class CaptureQualityError(RuntimeError):
    """没有可分析的证据, 拒绝给出任何结论 (fail-closed)。"""


def _exhaustive_pairs(count: int) -> int:
    return count * (count - 1) // 2


def _sequential_pairs(count: int) -> int:
    return count * min(SEQUENTIAL_OVERLAP, max(count - 1, 0))


def _solve_cost_model(bound: int) -> tuple[float, float]:
    """由手册两个锚点解出 (每图秒数, 每对秒数)。

    模型 t = a*n + b*pairs(n): 特征提取随图数线性, 匹配随图对数线性。两个锚点两个未知数,
    解是唯一的 —— 这样估计值与手册数字不会各说各话。
    """
    (n1, *bounds1), (n2, *bounds2) = MANUAL_ANCHORS
    t1, t2 = bounds1[bound] * 60.0, bounds2[bound] * 60.0
    p1, p2 = _exhaustive_pairs(n1), _exhaustive_pairs(n2)
    determinant = n1 * p2 - n2 * p1
    per_image = (t1 * p2 - t2 * p1) / determinant
    per_pair = (n1 * t2 - n2 * t1) / determinant
    return per_image, per_pair


_COST_LOW = _solve_cost_model(0)
_COST_HIGH = _solve_cost_model(1)


def estimate_colmap_cost(count: int, matcher: str = "exhaustive") -> dict[str, Any]:
    """粗估 COLMAP CPU 耗时。分开报告提取项与匹配项 —— 只有匹配项随匹配器改变。"""

    if matcher not in {"exhaustive", "sequential"}:
        raise ValueError(f"未知匹配器: {matcher}")
    pairs = _exhaustive_pairs(count) if matcher == "exhaustive" else _sequential_pairs(count)
    result: dict[str, Any] = {
        "matcher": matcher,
        "images": count,
        "pairs": pairs,
        "small_batch_caution": (
            _SMALL_BATCH_CAUTION if count < SMALL_BATCH_IMAGES else None
        ),
    }
    for label, (per_image, per_pair) in (("low", _COST_LOW), ("high", _COST_HIGH)):
        extract_s = per_image * count
        match_s = per_pair * pairs
        result[f"extract_minutes_{label}"] = round(extract_s / 60.0, 1)
        result[f"match_minutes_{label}"] = round(match_s / 60.0, 1)
        result[f"minutes_{label}"] = round((extract_s + match_s) / 60.0, 1)
    return result


def _iter_images(root: Path) -> list[str]:
    found: list[str] = []
    for directory, _, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in file_names:
            candidate = parent / name
            if candidate.suffix.lower() in PHOTO_SOURCE_SUFFIXES and candidate.is_file():
                found.append(candidate.relative_to(root).as_posix())
    return sorted(found)


def _blur_backend() -> str | None:
    if HAS_CV2:
        return "cv2"
    if HAS_PIL:
        return "numpy"
    return None


def _decode_gray(path: Path) -> np.ndarray | None:
    if HAS_CV2:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        return image if image is not None and image.size else None
    if HAS_PIL:
        try:
            with Image.open(path) as handle:
                return np.asarray(handle.convert("L"))
        except Exception:
            return None
    return None


def _read_size(path: Path) -> tuple[int, int] | None:
    """只读图头拿尺寸; PIL 不可用时退回 cv2 全解码。"""
    if HAS_PIL:
        try:
            with Image.open(path) as handle:
                return handle.size
        except Exception:
            return None
    if HAS_CV2:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or not image.size:
            return None
        height, width = image.shape[:2]
        return width, height
    return None


def _laplacian_variance(gray: np.ndarray) -> float:
    """Laplacian 方差 —— 图像越糊高频越少, 方差越低。是相对指标, 不是绝对清晰度。"""
    if HAS_CV2:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    values = gray.astype(np.float64)
    if min(values.shape[:2]) < 3:
        return 0.0
    laplacian = (
        -4.0 * values[1:-1, 1:-1]
        + values[:-2, 1:-1]
        + values[2:, 1:-1]
        + values[1:-1, :-2]
        + values[1:-1, 2:]
    )
    return float(laplacian.var())


def _percentile(values: list[float], fraction: float) -> float:
    return round(float(np.percentile(values, fraction * 100)), 2)


def _parse_exif_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _ordering_evidence(names: list[str], timestamps: list[datetime]) -> list[str]:
    """只在有**证据**时才认为这批图有序 —— 推错匹配器会直接毁掉重建, 宁可保守。"""
    evidence: list[str] = []
    if not names:
        return evidence
    frame_named = sum(1 for name in names if _FRAME_NAME_RE.search(Path(name).stem))
    if frame_named / len(names) >= _ORDERING_EVIDENCE_RATIO:
        evidence.append("video-frame-names")
    if len(timestamps) >= max(2, int(len(names) * _ORDERING_EVIDENCE_RATIO)):
        ordered = sorted(timestamps)
        gaps = [
            (later - earlier).total_seconds()
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        ]
        if gaps and statistics.median(gaps) <= _DENSE_INTERVAL_S:
            evidence.append("exif-dense-time-series")
    return evidence


def _analyze_blur(root: Path, names: list[str], threshold: float) -> dict[str, Any]:
    backend = _blur_backend()
    note = (
        f"阈值 {threshold} 是**启发式**经验值 (与 pipeline.ingest 抽帧阈值一致), "
        "不是精确判据: 分数受分辨率/纹理/曝光影响, 低分不等于一定匹配失败, "
        "高分也不保证能匹配上。仅供排查, 请自己抽查低分图再决定是否重拍。"
    )
    if backend is None:
        return {
            "available": False,
            "backend": None,
            "skipped_reason": (
                "cv2 与 Pillow 都不可用, 已**跳过**模糊度检测 —— "
                "本次报告没有任何清晰度证据, 不代表这批图清晰。"
                "装 opencv-python-headless 或 Pillow 后重跑可得到该项。"
            ),
            "threshold": threshold,
            "threshold_is_heuristic": True,
            "threshold_note": note,
            "scored": 0,
            "blurry_count": 0,
            "blurry_ratio": None,
            "blurry_files": [],
            "blurry_files_truncated": False,
            "scores": {},
            "median": None,
            "p10": None,
            "min": None,
            "max": None,
        }

    scores: dict[str, float] = {}
    unreadable: list[str] = []
    for name in names:
        gray = _decode_gray(root / name)
        if gray is None:
            unreadable.append(name)
            continue
        scores[name] = round(_laplacian_variance(gray), 2)

    values = list(scores.values())
    blurry = sorted(name for name, score in scores.items() if score < threshold)
    return {
        "available": bool(values),
        "backend": backend if values else None,
        "skipped_reason": None if values else "所有图片都无法解码, 模糊度未知",
        "threshold": threshold,
        "threshold_is_heuristic": True,
        "threshold_note": note,
        "scored": len(values),
        "unreadable": unreadable,
        "blurry_count": len(blurry),
        "blurry_ratio": round(len(blurry) / len(values), 4) if values else None,
        "blurry_files": blurry[:MAX_LISTED_FILES],
        "blurry_files_truncated": len(blurry) > MAX_LISTED_FILES,
        "scores": scores,
        "median": _percentile(values, 0.5) if values else None,
        "p10": _percentile(values, 0.1) if values else None,
        "min": round(min(values), 2) if values else None,
        "max": round(max(values), 2) if values else None,
    }


def _analyze_resolution(root: Path, names: list[str]) -> dict[str, Any]:
    if not (HAS_PIL or HAS_CV2):
        return {
            "available": False,
            "skipped_reason": "cv2 与 Pillow 都不可用, 已跳过分辨率检测 (未知, 非合格)",
            "median_megapixels": None,
            "min_megapixels": None,
            "max_megapixels": None,
            "below_min_count": 0,
            "below_min_files": [],
            "oversized_count": 0,
        }
    megapixels: dict[str, float] = {}
    for name in names:
        size = _read_size(root / name)
        if size is None:
            continue
        width, height = size
        megapixels[name] = width * height / 1_000_000
    values = list(megapixels.values())
    below = sorted(name for name, value in megapixels.items() if value < MIN_MEGAPIXELS)
    oversized = [name for name, value in megapixels.items() if value > 12.0]
    return {
        "available": bool(values),
        "skipped_reason": None if values else "没有能读出尺寸的图片",
        "median_megapixels": round(statistics.median(values), 2) if values else None,
        "min_megapixels": round(min(values), 2) if values else None,
        "max_megapixels": round(max(values), 2) if values else None,
        "below_min_count": len(below),
        "below_min_files": below[:MAX_LISTED_FILES],
        "oversized_count": len(oversized),
        "min_megapixels_threshold": MIN_MEGAPIXELS,
    }


@contextlib.contextmanager
def _quiet_exifread():
    """探测期间闭掉 exifread 的 WARNING。

    缺 EXIF 是**正常情况**而非异常: exifread 会对每张无 EXIF 的图 (如 PNG) 打一条
    WARNING, 一批几百张就把报告刷没了, 还让人以为工具坏了。缺失本身已在报告里如实
    计数并影响 verdict —— 那才是该说话的地方, 不需要日志再喊一遍。
    """
    exif_logger = logging.getLogger("exifread")
    previous = exif_logger.level
    exif_logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        exif_logger.setLevel(previous)


def _analyze_exif(root: Path, names: list[str]) -> tuple[dict[str, Any], list[datetime]]:
    with_datetime = 0
    with_gps = 0
    timestamps: list[datetime] = []
    for name in names:
        with _quiet_exifread():
            captured_at, gps = _read_photo_exif(root / name)
        if captured_at:
            with_datetime += 1
            parsed = _parse_exif_datetime(captured_at)
            if parsed is not None:
                timestamps.append(parsed)
        if gps is not None:
            with_gps += 1
    total = len(names)
    gps_note = (
        "有 GPS → 可试 `pipeline.alignment --from-gps` 做米制对齐。但**消费级 GPS 精度约 "
        "3~10m**, 噪声无法被相似变换解释, 所以默认 `--max-rms 2.0` 基本必然 fail-closed "
        "(这是正确的: 它拒绝为噪声盖米制章)。放宽到 5~10 才可能过门, 且精度不会好于 GPS "
        "本身。要厘米级请用实测控制点。详见 docs/real-data-workflow.md。"
        if with_gps
        else "没有任何图带 GPS → 无法走 --from-gps 米制对齐; 要米制请用实测控制点。"
    )
    report = {
        "with_datetime": with_datetime,
        "with_gps": with_gps,
        "datetime_ratio": round(with_datetime / total, 4) if total else 0.0,
        "gps_ratio": round(with_gps / total, 4) if total else 0.0,
        "can_try_from_gps": with_gps > 0,
        "gps_note": gps_note,
    }
    return report, timestamps


def _build_verdict(
    *,
    count: int,
    blur: dict[str, Any],
    resolution: dict[str, Any],
    unreadable: int,
) -> dict[str, Any]:
    """把已知的坏消息汇总成分级结论。永远不预测 SfM 成败, 只说"发现/没发现硬伤"。"""

    blockers: list[str] = []
    warnings: list[str] = []
    remedies: list[str] = []

    if count < MIN_USEFUL_IMAGES:
        blockers.append(
            f"只有 {count} 张图 —— 手册: <{MIN_USEFUL_IMAGES} 张基本无望重建"
        )
        remedies.append(
            f"补拍到至少 {RECOMMENDED_MIN_IMAGES} 张 (手册建议 "
            f"{RECOMMENDED_MIN_IMAGES}~{RECOMMENDED_MAX_IMAGES} 张), 相邻图重叠 ≥60%"
        )
    elif count < RECOMMENDED_MIN_IMAGES:
        warnings.append(
            f"{count} 张图偏少 (手册建议 {RECOMMENDED_MIN_IMAGES}~{RECOMMENDED_MAX_IMAGES} 张)"
        )
        remedies.append(f"补拍到 {RECOMMENDED_MIN_IMAGES} 张以上, 尤其补拍视角跳变处")
    elif count > RECOMMENDED_MAX_IMAGES:
        warnings.append(
            f"{count} 张图超出建议上限 {RECOMMENDED_MAX_IMAGES} —— 不是重建不了, "
            "而是穷举匹配 O(n^2) 会很慢"
        )
        remedies.append("抽稀到 300 张以内, 或确认有序后用 sequential 匹配器")

    if unreadable:
        warnings.append(f"{unreadable} 张图无法解码 (损坏或格式不支持, 如 HEIC 缺插件)")
        remedies.append("转成 jpg/png 后重跑, 或从相机重新导出")

    if blur["available"]:
        ratio = blur["blurry_ratio"]
        if ratio > BLUR_RATIO_BLOCK:
            blockers.append(
                f"{blur['blurry_count']}/{blur['scored']} 张图模糊 "
                f"({ratio:.0%} > {BLUR_RATIO_BLOCK:.0%}) —— 模糊图会让特征匹配失败"
            )
            remedies.append("重拍: 提高快门/开补光/用三脚架; 移动中拍摄要放慢")
        elif ratio > BLUR_RATIO_WARN:
            warnings.append(
                f"{blur['blurry_count']}/{blur['scored']} 张图模糊 ({ratio:.0%})"
            )
            remedies.append("删掉或补拍模糊图 (清单见 blur.blurry_files) 后重跑")
    else:
        warnings.append(
            "模糊度未检测 —— 缺解码器, 这项是**未知**而非合格"
        )
        remedies.append("装 opencv-python-headless 或 Pillow 后重跑以获得清晰度证据")

    if resolution["available"]:
        if resolution["below_min_count"]:
            message = (
                f"{resolution['below_min_count']} 张图分辨率低于 {MIN_MEGAPIXELS}MP "
                "—— 特征点可能不足"
            )
            if resolution["below_min_count"] == count:
                blockers.append(message)
            else:
                warnings.append(message)
            remedies.append("用更高分辨率重拍; 不要用缩略图/截图")
        if resolution["oversized_count"]:
            warnings.append(
                f"{resolution['oversized_count']} 张图 >12MP —— 能重建但 COLMAP 会更慢"
            )
            remedies.append("用 `pipeline.ingest --max-long-edge 2560` 缩边提速")
    else:
        warnings.append("分辨率未检测 —— 缺解码器, 这项是**未知**而非合格")

    if blockers:
        level = "unlikely"
    elif warnings:
        level = "risky"
    else:
        level = "likely"

    meanings = {
        "likely": "没发现明显硬伤。**这不等于能重建成功** —— 决定成败的重叠度本工具测不到。",
        "risky": "发现了会显著降低成功率的问题, 建议先按下面的建议处理再跑 COLMAP。",
        "unlikely": "发现硬伤, 照现在这批图跑 COLMAP 大概率白等。请先补救。",
    }
    return {
        "level": level,
        "meaning": meanings[level],
        "is_heuristic": True,
        "reasons": [*blockers, *warnings],
        "blockers": blockers,
        "warnings": warnings,
        "remedies": remedies,
    }


def analyze_capture(
    photos_dir: str | Path,
    *,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
) -> dict[str, Any]:
    """对一批照片做启发式采集预检, 返回可 JSON 序列化的报告。

    没有图片就抛 CaptureQualityError (fail-closed): 没有证据时不产出任何结论。
    """
    root = Path(photos_dir).expanduser()
    if not root.exists():
        raise CaptureQualityError(f"目录不存在: {root}")
    if not root.is_dir():
        raise CaptureQualityError(f"不是目录: {root}")

    names = _iter_images(root)
    if not names:
        raise CaptureQualityError(
            f"目录里没有支持的图片: {root} "
            f"(支持 {', '.join(sorted(PHOTO_SOURCE_SUFFIXES))}; "
            "视频请先用 `python -m pipeline.ingest` 抽帧)"
        )

    blur = _analyze_blur(root, names, blur_threshold)
    resolution = _analyze_resolution(root, names)
    exif, timestamps = _analyze_exif(root, names)
    unreadable = len(blur.get("unreadable", []))

    evidence = _ordering_evidence(names, timestamps)
    matcher = "sequential" if evidence else "exhaustive"
    estimate = estimate_colmap_cost(len(names), matcher=matcher)
    estimate["alternative"] = estimate_colmap_cost(
        len(names), matcher="exhaustive" if matcher == "sequential" else "sequential"
    )
    estimate["ordering_evidence"] = evidence
    estimate["matcher_recommended"] = matcher
    estimate["is_rough_estimate"] = True
    estimate["note"] = (
        "**粗估**: 由手册 §4 本机实测锚点 (i7-14700 CPU: ~100 图 20–60min; "
        "~300 图 2–5+ 小时) 解出的线性模型外推, 真实耗时随图像分辨率/纹理量/CPU 负载"
        "大幅波动, 且**重建失败时可能更快也可能更慢**。仅用于决定要不要现在跑。"
        + (
            ""
            if evidence
            else " 未发现顺序证据 → 建议 exhaustive; 若这批其实是视频抽帧或沿路径连续拍摄, "
            "用 sequential 可省下匹配时间 (见 alternative)。"
        )
    )

    suffix_counts: dict[str, int] = {}
    for name in names:
        suffix = Path(name).suffix.lower()
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

    verdict = _build_verdict(
        count=len(names), blur=blur, resolution=resolution, unreadable=unreadable
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "pipeline.capture_quality",
        "photos_dir": str(root),
        "count": {
            "images": len(names),
            "unreadable": unreadable,
            "by_suffix": dict(sorted(suffix_counts.items())),
            "recommended_range": [RECOMMENDED_MIN_IMAGES, RECOMMENDED_MAX_IMAGES],
        },
        "blur": blur,
        "resolution": resolution,
        "exif": exif,
        "colmap_estimate": estimate,
        "verdict": verdict,
        "honesty": {
            "limits": [
                "**重叠度测不到**: 相邻图重叠是否 ≥60% 是 SfM 成败的首要因素, 但它是图**之间**"
                "的关系, 单图分析测不出来。要确知只能真跑 COLMAP —— 那正是这一步最贵的部分。",
                "本预检**不能替代真跑 COLMAP**: 通过预检不保证能重建, 未通过也不保证一定失败。",
                "模糊度阈值是启发式经验值, 受分辨率/纹理/曝光影响, 不是精确判据。",
                "耗时是由手册实测锚点外推的粗估, 不是承诺。",
                "以下因素本工具一律测不到: 曝光一致性、纹理独特性、玻璃/水面/天空占比、"
                "移动物体、是否绕拍成环。",
            ],
        },
    }
