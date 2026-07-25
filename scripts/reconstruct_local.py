#!/usr/bin/env python3
"""本机一键重建：照片/视频目录 → COLMAP 位姿 → Brush 训练 3DGS → 导入本仓库。

把已实测跑通的全本机链路串成一条命令（无需 NVIDIA/CUDA；用 third/ 下的
COLMAP no-CUDA 与 Brush）。产物落到 web/data/recon，随后 `python make.py serve`
即可 360° 漫游。诚实：sfm-local 非米制 → 结果标 preview-only；要米制见
docs/real-data-workflow.md。用法与限制见 docs/manual/reconstruction-setup.md。

    python scripts/reconstruct_local.py <照片目录> [--steps 3000] [--max-res 1024]
    python scripts/reconstruct_local.py <照片目录> --resume   # 跳过输入未变的已完成阶段

依赖二进制（默认 third/，也接受 PATH）：
    third/colmap/bin/colmap.exe   third/brush/brush_app.exe
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 指纹跟踪哪些文件：直接用全仓库共享的那份，**不要**在这里另开一份清单。方向很关键 ——
# 指纹宁可过度包含（多跟踪几个文件 = 多重跑几次 = 保守），漏掉才是 fail-open：漏掉的
# 格式在指纹里等于不存在，两批彻底不同的照片会得到同一个指纹，--resume 就会静默复用
# 上一批的位姿。这里曾经写死 {".jpg",".jpeg",".png"}，对一批 .tif 恒产出空指纹。
from pipeline.ingest_manifest import PHOTO_SOURCE_SUFFIXES  # noqa: E402

FINGERPRINT_SUFFIXES = frozenset(PHOTO_SOURCE_SUFFIXES)

# 阶段顺序 == 依赖顺序：任一阶段重跑，其后所有阶段都不可复用。
STAGE_ORDER = ("frames", "colmap", "brush", "prepare", "import")
STATE_FILENAME = ".stage_state.json"
STATE_VERSION = 1
TEE_INTERVAL_S = 0.5  # 终端回显节流：每 0.5s 最多刷一行（日志始终全量）

FINGERPRINT_CAVEAT = (
    "指纹取 (路径, 字节数, mtime) + 参数 + 二进制，不读照片内容："
    "同名同大小同 mtime 的**不同内容**照片发现不了。这是避免每次 hash 几百 MB 的"
    "工程折中，不是密码学强度的校验。不放心就别加 --resume。")

# --precomputed-colmap：必需与可选的 COLMAP sparse/0 文件名。
# REVIEW-CODEX-030 P0 要求 cameras.bin / images.bin / points3D.bin 必须存在且
# 字节绑定；frames.bin / rigs.bin / project.ini 是 COLMAP 可选产物，存在则同样绑定，
# 缺失不阻断（旧版 COLMAP / 简化导入路径可能不输出）。
PRECOMPUTED_REQUIRED_BIN = ("cameras.bin", "images.bin", "points3D.bin")
PRECOMPUTED_OPTIONAL_BIN = ("frames.bin", "rigs.bin", "project.ini")


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


def _count_registered_images(model_dir: Path) -> int:
    """已注册影像数：读 images.bin 头 8 字节 (COLMAP 存 uint64 num_reg_images)，
    退化读 images.txt 的 '# Number of images:' 注释。拿不到返回 0。"""
    b = model_dir / "images.bin"
    if b.is_file():
        head = b.read_bytes()[:8]
        if len(head) == 8:
            return struct.unpack("<Q", head)[0]
    t = model_dir / "images.txt"
    if t.is_file():
        for line in t.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# Number of images:"):
                return int(line.split(":", 2)[1].split(",")[0].strip())
    return 0


def _select_best_colmap_model(sparse_dir: Path) -> tuple[int, int]:
    """真实照片有覆盖缺口时 COLMAP 会产出多个不连通子模型 (sparse/0,1,…)。选注册影像
    最多的那个，必要时挪到 sparse/0 供 Brush 使用。返回 (最佳注册数, 子模型数)。"""
    models = sorted(p for p in sparse_dir.glob("*")
                    if p.is_dir() and ((p / "images.bin").is_file()
                                       or (p / "images.txt").is_file()))
    if not models:
        raise SystemExit("COLMAP 未产出任何模型 (sparse/* 为空)：重叠不足？多拍/绕拍。")
    best = max(models, key=_count_registered_images)
    best_n = _count_registered_images(best)
    if best.name != "0":
        zero, stash = sparse_dir / "0", sparse_dir / "_notbest_0"
        if zero.exists():
            if stash.exists():
                shutil.rmtree(stash)
            zero.rename(stash)
        best.rename(zero)
    return best_n, len(models)


def _digest(payload: dict) -> str:
    """任意可 JSON 化的指纹载荷 → 稳定 sha256（sort_keys：字段顺序不影响结果）。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    """稳定 SHA-256（分块读，避免大 colmap.db 一次 load 进内存）。"""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_precomputed_manifest(colmap_ws: Path, photos: Path) -> dict:
    """为 --precomputed-colmap 构建字节绑定的源清单（fingerprint payload）。

    REVIEW-CODEX-030 P0 要求把 cameras.bin / images.bin / points3D.bin / colmap.db /
    caller argv 都绑进 colmap 阶段指纹的载荷字段。注意：本函数返回的是**载荷字段**
    （被 _fingerprint 消费成 digest），不是一份物化的、content-addressed 的独立
    报告——后者是 P7a-2 的工作（见 handoff）。载荷字段只能由 StageState 类写入
    .stage_state.json，避免重蹈 P7 手工写假指纹的覆辙。

    诚实边界：载荷字段本身**不是** tamper-evident——它只是 fingerprint digest 的
    输入。真正可审计的 source manifest 需要物化成独立文件并 content-addressed
    （P7a-2）。当前阶段，fingerprint digest 的可复现性已由 _digest 的 sort_keys
    保证，但"载荷不可被悄悄替换"这一层要等 P7a-2 物化后才闭合。

    fail-closed：
    - 缺 sparse/0 → 拒；
    - 缺任一必需 .bin → 拒；
    - 缺 images/ → 拒；
    - --photos 与 <colmap_ws>/images/ 的逐张 SHA-256 不同 → 拒（否则 Brush 会
      训在与 sparse/0 来源不同的另一批照片上，产出一个谎称来自这批 sparse 的
      重建。用 SHA-256 而非廉价指纹：同名同大小同 mtime 的字节篡改也要被发现）。

    返回字段全部是已实测 SHA-256；可选文件缺失则不绑（指纹载荷也不含），保证
    “删除可选文件”不会改变指纹——只有改必需文件或加/删可选文件才会变。
    """
    sparse_0 = colmap_ws / "sparse" / "0"
    if not sparse_0.is_dir():
        raise SystemExit(
            f"--precomputed-colmap: 缺 sparse/0 目录: {sparse_0} "
            f"(期望 <colmap_ws>/sparse/0/{{cameras,images,points3D}}.bin)")
    manifest: dict = {"mode": "precomputed",
                      "source_root": str(colmap_ws.resolve())}
    for name in PRECOMPUTED_REQUIRED_BIN:
        p = sparse_0 / name
        if not p.is_file():
            raise SystemExit(f"--precomputed-colmap: 缺必需文件 {p}")
        manifest[f"{name}_sha256"] = _sha256_file(p)
    for name in PRECOMPUTED_OPTIONAL_BIN:
        p = sparse_0 / name
        if p.is_file():
            manifest[f"{name}_sha256"] = _sha256_file(p)
    db = colmap_ws / "colmap.db"
    if db.is_file():
        manifest["colmap_db_sha256"] = _sha256_file(db)
    img_dir = colmap_ws / "images"
    if not img_dir.is_dir():
        raise SystemExit(
            f"--precomputed-colmap: 缺 images/ 目录: {img_dir} "
            f"(--photos 必须与产生 sparse/0 的那一批照片同源)")
    src_img_sha = _photos_sha256(img_dir)
    if not src_img_sha:
        raise SystemExit(f"--precomputed-colmap: images/ 为空: {img_dir}")
    caller_photos_sha = _photos_sha256(photos)
    if src_img_sha != caller_photos_sha:
        raise SystemExit(
            "--precomputed-colmap: --photos 与 <colmap_ws>/images/ 内容不一致 "
            "(逐张 SHA-256 不同；必须使用产生 sparse/0 的那一批照片，否则重建会谎称来源)")
    manifest["photos_sha256"] = caller_photos_sha
    # REVIEW-CODEX-030 P7a-6：源端语义校验——sparse/0 不仅要字节绑定，还要语义
    # 完整（image_name 无 phantom/duplicate、cameras 有限元）。源端 fail-closed
    # 阻止 bad source 进入；ws 端拷贝后再校验一次（防拷贝损坏）。
    _validate_sparse_semantics(sparse_0, photos)
    return manifest


def _materialize_source_manifest(
    ws: Path,
    manifest: dict,
    caller_argv: list[str],
    colmap_bin_sha: str,
) -> str:
    """物化 content-addressed source manifest 报告（可恢复 payload，不只是 digest）。

    REVIEW-CODEX-030 P7a-2：fingerprint digest 只是一个 sha256 字符串——reviewer
    无法从 digest 恢复出原始 payload（哪些文件、哪些 SHA、哪个 argv 消费了源）。
    本函数把完整的源清单 payload 写成独立 JSON 文件，文件名按 payload 自身的
    SHA-256 命名（content-addressed），让 reviewer 可以直接读取并复核。

    报告包含：
    - 所有源文件 SHA-256（cameras/images/points3D + optional bins + colmap.db）
    - photos_sha256（逐张照片字节级指纹）
    - caller_argv（有效调用意图——换 flag 即视为不同消费）
    - colmap_binary_sha256（COLMAP 二进制身份）
    - manifest_sha256（payload 自身的 content-address）
    - materialized_at_utc（物化时间戳）

    write-once：同 SHA 文件已存在 → no-op（幂等重跑）；不同 SHA 文件已存在 →
    fail-closed（拒绝覆盖，source manifest 是审计凭证，覆盖等于销毁证据）。

    返回 manifest_sha256（供 colmap extras 交叉引用 .stage_state.json 中的 digest）。
    """
    payload = {
        **manifest,  # mode, source_root, *_sha256, photos_sha256
        "caller_argv": caller_argv,
        "colmap_binary_sha256": colmap_bin_sha,
    }
    manifest_sha = _digest(payload)
    report = {
        **payload,
        "manifest_sha256": manifest_sha,
        "materialized_at_utc": datetime.now(UTC).isoformat(),
    }
    report_path = ws / f"source_manifest_{manifest_sha}.json"
    if report_path.is_file():
        existing = json.loads(report_path.read_text(encoding="utf-8"))
        if existing.get("manifest_sha256") != manifest_sha:
            raise SystemExit(
                f"source manifest 冲突: {report_path} 已存在但 manifest_sha256 不匹配 "
                f"(existing={existing.get('manifest_sha256')}, new={manifest_sha})；"
                f"拒绝覆盖审计凭证")
    else:
        blob = json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2)
        report_path.write_text(blob, encoding="utf-8")
    return manifest_sha


def _validate_ws_precomputed(ws: Path, manifest: dict) -> bool:
    """工作目录里的 precomputed 拷贝是否与源清单逐字节一致。

    任一文件缺失或 SHA 不匹配 → 返回 False（**不**抛错；由调用方决定是
    fail-closed 还是触发 re-copy）。读 PLY/colmap.db 几百 MB 时只算一次 SHA。

    REVIEW-CODEX-030 P7a-4：除逐文件 SHA 校验外，还要校验 ws/sparse/0 里的文件集
    **恰好**等于 manifest 声明的文件集——不多不少。多出来的文件（stale frames.bin
    / rigs.bin / project.ini / 未知文件）即使 SHA 不被校验，也可能误导下游或污染
    审计。_copy_precomputed_to_ws 的 fresh staging + 原子替换已保证不产生 stale，
    但这层校验是 fail-closed 的双保险。
    """
    sparse_0 = ws / "sparse" / "0"
    if not sparse_0.is_dir():
        return False
    # exact file set: ws/sparse/0 里的文件集应恰好等于 manifest 声明的 sparse 文件
    expected_sparse = set(PRECOMPUTED_REQUIRED_BIN)
    for name in PRECOMPUTED_OPTIONAL_BIN:
        if f"{name}_sha256" in manifest:
            expected_sparse.add(name)
    actual_sparse = {p.name for p in sparse_0.iterdir() if p.is_file()}
    if actual_sparse != expected_sparse:
        return False
    for name in PRECOMPUTED_REQUIRED_BIN:
        p = sparse_0 / name
        if not p.is_file():
            return False
        if _sha256_file(p) != manifest[f"{name}_sha256"]:
            return False
    for name in PRECOMPUTED_OPTIONAL_BIN:
        key = f"{name}_sha256"
        if key not in manifest:
            continue
        p = sparse_0 / name
        if not p.is_file() or _sha256_file(p) != manifest[key]:
            return False
    if "colmap_db_sha256" in manifest:
        db = ws / "colmap.db"
        if not db.is_file() or _sha256_file(db) != manifest["colmap_db_sha256"]:
            return False
    img_dir = ws / "images"
    if not img_dir.is_dir():
        return False
    # images/ 也必须与清单的照片 SHA 一致（防止外部把 ws/images 改了，包括
    # 同名同大小同 mtime 的字节篡改——廉价指纹发现不了的，SHA 能发现）。
    if _photos_sha256(img_dir) != manifest["photos_sha256"]:
        return False
    return True


# 权威映射：COLMAP 4.1.0 (Commit fa8e3b3) model_converter 实测全部 12 个模型。
# FULL_FOV 不被此版本接受——不得发明为 model id 8。
# 来源：Codex 独立用 model_converter 转换每个模型的文本相机，测量 one-camera BIN 字节数反推。
_COLMAP_MODEL_NUM_PARAMS: dict[int, int] = {
    0: 3,    # SIMPLE_PINHOLE
    1: 4,    # PINHOLE
    2: 4,    # SIMPLE_RADIAL
    3: 5,    # RADIAL
    4: 8,    # OPENCV
    5: 8,    # OPENCV_FISHEYE
    6: 12,   # FULL_OPENCV
    7: 5,    # FOV
    8: 4,    # SIMPLE_RADIAL_FISHEYE
    9: 5,    # RADIAL_FISHEYE
    10: 12,  # THIN_PRISM_FISHEYE
    11: 16,  # RAD_TAN_THIN_PRISM_FISHEYE
}

# Explicit focal-parameter layout from the same COLMAP 4.1.0 model table.
# Models with independent fx/fy must validate both; parameter count alone is
# not a safe way to infer focal layout.
_COLMAP_MODEL_FOCAL_INDICES: dict[int, tuple[int, ...]] = {
    0: (0,),
    1: (0, 1),
    2: (0,),
    3: (0,),
    4: (0, 1),
    5: (0, 1),
    6: (0, 1),
    7: (0, 1),
    8: (0,),
    9: (0,),
    10: (0, 1),
    11: (0, 1),
}


def _parse_colmap_cameras_bin(path: Path) -> list[dict]:
    """解析 COLMAP cameras.bin 二进制 → list of camera dicts。

    格式（COLMAP 4.x，实测 P5b cameras.bin 校验）：
      num_cameras(uint64) + 每相机 {camera_id(uint32), model(int32),
      width(uint64), height(uint64), params(double[num_params])}。

    **注意**：COLMAP 4.x **不存储 num_params**——参数数量由 model_id 推导
    （SIMPLE_RADIAL=2→4 params, PINHOLE=1→4, RADIAL=3→5, OPENCV=4→8, …）。
    旧版解析器错误地从文件读 num_params，实际读到了 params 的第一个 double，
    导致 nparams=4.6e18 → struct.calcsize 爆炸。用 _COLMAP_MODEL_NUM_PARAMS
    查表；未知 model → ValueError（fail-closed，不让残缺/未知模型静默通过）。

    解析失败（文件过短/字段越界/未知模型）→ ValueError（由调用方决定 fail-closed）。
    """
    data = path.read_bytes()
    pos = 0

    def read(fmt: str) -> tuple:
        nonlocal pos
        size = struct.calcsize(fmt)
        if pos + size > len(data):
            raise ValueError(
                f"cameras.bin 在 offset {pos} 读 {fmt} 越界（文件 {len(data)} 字节）")
        val = struct.unpack_from(fmt, data, pos)
        pos += size
        return val

    num = read("<Q")[0]
    cameras: list[dict] = []
    for i in range(num):
        camera_id = read("<I")[0]
        model = read("<i")[0]
        width = read("<Q")[0]
        height = read("<Q")[0]
        nparams = _COLMAP_MODEL_NUM_PARAMS.get(model)
        if nparams is None:
            raise ValueError(
                f"cameras.bin 相机 #{i} (camera_id={camera_id}) model={model} "
                f"不在已知 COLMAP 模型表 → 无法确定参数数量（拒绝猜测）")
        params = list(read(f"<{nparams}d")) if nparams else []
        cameras.append({"index": i, "camera_id": camera_id, "model": model,
                        "width": width, "height": height, "params": params})
    # 尾字节检查：合法 COLMAP cameras.bin 解析完后 pos == len(data)。若有
    # 残余字节，说明 header count 与实际记录数不符（或文件被拼接/截断）→
    # fail-closed，不让"header 说 3 但实际有 12 records"的文件静默通过。
    if pos != len(data):
        raise ValueError(
            f"cameras.bin header 声明 {num} 个相机，但解析后仍有 "
            f"{len(data) - pos} 字节尾数据（header 与记录数不符）")
    return cameras


def _parse_colmap_images_bin(path: Path) -> list[dict]:
    """解析 COLMAP images.bin 二进制 → list of image dicts。

    格式参考 COLMAP src/base/reconstruction.cc ReadImagesBinary:
      num_reg_images(uint64) + 每图像 {image_id(uint32), qvec(4*float64),
      tvec(3*float64), camera_id(uint32), image_name(null-terminated string),
      num_points2D(uint64), points2D(2*num_points2D*float64 + num_points2D*int64)}。

    只提取 image_id/name/camera_id/qvec/tvec，跳过 points2D 字节。
    解析失败 → ValueError。
    """
    data = path.read_bytes()
    pos = 0

    def read(fmt: str) -> tuple:
        nonlocal pos
        size = struct.calcsize(fmt)
        if pos + size > len(data):
            raise ValueError(
                f"images.bin 在 offset {pos} 读 {fmt} 越界（文件 {len(data)} 字节）")
        val = struct.unpack_from(fmt, data, pos)
        pos += size
        return val

    num = read("<Q")[0]
    images: list[dict] = []
    for i in range(num):
        image_id = read("<I")[0]
        qvec = list(read("<4d"))
        tvec = list(read("<3d"))
        camera_id = read("<I")[0]
        end = data.find(b"\x00", pos)
        if end == -1:
            raise ValueError(f"images.bin 图像 #{i} name 未找到 null 终止符")
        try:
            name = data[pos:end].decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(
                f"images.bin 图像 #{i} name 不是合法 UTF-8: {e}") from e
        pos = end + 1
        npts = read("<Q")[0]
        # points2D: 2*npts float64 (x,y) + npts int64 (point3D_id)
        pts_size = 2 * npts * 8 + npts * 8
        if pos + pts_size > len(data):
            raise ValueError(
                f"images.bin 图像 #{i} points2D 越界（需 {pts_size} 字节，"
                f"剩余 {len(data) - pos}）")
        pos += pts_size
        images.append({"index": i, "image_id": image_id, "qvec": qvec,
                       "tvec": tvec, "camera_id": camera_id, "name": name})
    # 尾字节检查：合法 COLMAP images.bin 解析完后 pos == len(data)。若有
    # 残余字节，说明 header count 与实际记录数不符（或文件被拼接/截断）→
    # fail-closed。这挡住"header 说 3 但实际有 12 records"的静默截断。
    if pos != len(data):
        raise ValueError(
            f"images.bin header 声明 {num} 张图像，但解析后仍有 "
            f"{len(data) - pos} 字节尾数据（header 与记录数不符）")
    return images


def _normalize_colmap_image_name(name: str) -> str:
    """Return one host-independent safe POSIX-relative COLMAP image name."""
    if not isinstance(name, str) or not name:
        raise ValueError("image_name 必须是非空 UTF-8 字符串")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(
            f"image_name={name!r} 是绝对/驱动器路径（必须为相对路径）"
        )
    parts = normalized.split("/")
    if any(part == "" for part in parts):
        raise ValueError(
            f"image_name={name!r} 含空路径组件或重复分隔符"
        )
    if any(part in {".", ".."} for part in parts):
        raise ValueError(
            f"image_name={name!r} 含不安全路径组件 '.' 或 '..'"
        )
    return "/".join(parts)


def _validate_sparse_semantics(sparse_0: Path, photos: Path) -> None:
    """校验 precomputed COLMAP sparse/0 的语义完整性（不止前 8 字节 header）。

    HANDOFF-GLM-008 Task 2：仅读 images.bin 头 8 字节 num_reg_images 不等于一个
    合法的 recovered camera track。一个可信的 sparse model 需要：
    1. cameras.bin：无重复/零 camera_id、非零 width/height、params 全 finite、
       每个模型声明的 fx/fy 焦距均为正；
    2. images.bin：无重复/零 image_id、规范化后 name 无碰撞、name 满足统一的
       POSIX-relative grammar、qvec/tvec 全 finite、qvec 可归一化、camera_id 引用存在；
    3. 每个 image_name 在 photos/ 找得到对应文件（无 phantom image）。

    不要求 num_reg_images == len(photos)：COLMAP 正常会丢弃未注册的图像，这是
    算法结果不是错误。本函数只挡 sparse model **自相矛盾**或**与 photos 不一致**
    的情形。

    解析失败 / 语义错误 → SystemExit（fail-closed，不让 Brush 训在残缺 track 上）。
    """
    cameras_path = sparse_0 / "cameras.bin"
    images_path = sparse_0 / "images.bin"
    try:
        cameras = _parse_colmap_cameras_bin(cameras_path)
    except (OSError, ValueError, struct.error) as e:
        raise SystemExit(
            f"--precomputed-colmap: cameras.bin 语义校验失败（解析错误）: {e}") from e
    try:
        images = _parse_colmap_images_bin(images_path)
    except (OSError, ValueError, struct.error) as e:
        raise SystemExit(
            f"--precomputed-colmap: images.bin 语义校验失败（解析错误）: {e}") from e

    # --- cameras.bin 语义校验 ---
    cam_ids: set[int] = set()
    for cam in cameras:
        cid = cam["camera_id"]
        if cid == 0:
            raise SystemExit(
                "--precomputed-colmap: cameras.bin 有零 camera_id（COLMAP 从 1 开始）")
        if cid in cam_ids:
            raise SystemExit(
                f"--precomputed-colmap: cameras.bin 有重复 camera_id={cid}")
        cam_ids.add(cid)
        if cam["width"] == 0 or cam["height"] == 0:
            raise SystemExit(
                f"--precomputed-colmap: cameras.bin camera_id={cid} "
                f"维度为零 ({cam['width']}x{cam['height']})")
        for j, p in enumerate(cam["params"]):
            if not math.isfinite(p):
                raise SystemExit(
                    f"--precomputed-colmap: cameras.bin camera_id={cid} "
                    f"param[{j}]={p} 非有限（NaN/Inf）→ 位姿不可信")
        focal_indices = _COLMAP_MODEL_FOCAL_INDICES.get(cam["model"])
        if focal_indices is None:
            raise SystemExit(
                f"--precomputed-colmap: cameras.bin camera_id={cid} "
                f"model={cam['model']} 缺少焦距布局合同"
            )
        for focal_index in focal_indices:
            focal = cam["params"][focal_index]
            if focal <= 0:
                raise SystemExit(
                    f"--precomputed-colmap: cameras.bin camera_id={cid} "
                    f"焦距 params[{focal_index}]={focal} 非正 → 位姿不可信"
                )

    # --- images.bin 语义校验 ---
    img_ids: set[int] = set()
    seen_names: set[str] = set()
    for img in images:
        iid = img["image_id"]
        if iid == 0:
            raise SystemExit(
                "--precomputed-colmap: images.bin 有零 image_id（COLMAP 从 1 开始）")
        if iid in img_ids:
            raise SystemExit(
                f"--precomputed-colmap: images.bin 有重复 image_id={iid}")
        img_ids.add(iid)

        raw_name = img["name"]
        try:
            name = _normalize_colmap_image_name(raw_name)
        except ValueError as exc:
            raise SystemExit(
                f"--precomputed-colmap: images.bin image_name 无效: {exc}"
            ) from exc
        if name in seen_names:
            raise SystemExit(
                "--precomputed-colmap: images.bin image_name 规范化后重复: "
                f"{raw_name!r} -> {name!r}"
            )
        seen_names.add(name)
        img["normalized_name"] = name

        # qvec/tvec finite
        qvec = img["qvec"]
        tvec = img["tvec"]
        if not all(math.isfinite(v) for v in qvec):
            raise SystemExit(
                f"--precomputed-colmap: images.bin image_id={iid} "
                f"qvec={qvec} 含非有限值")
        if not all(math.isfinite(v) for v in tvec):
            raise SystemExit(
                f"--precomputed-colmap: images.bin image_id={iid} "
                f"tvec={tvec} 含非有限值")

        # qvec 可归一化（非近零）
        qnorm = math.hypot(*qvec)
        if not math.isfinite(qnorm):
            raise SystemExit(
                f"--precomputed-colmap: images.bin image_id={iid} "
                f"qvec 范数非有限 → 不可归一化 → 位姿不可信"
            )
        if qnorm < 1e-12:
            raise SystemExit(
                f"--precomputed-colmap: images.bin image_id={iid} "
                f"qvec 范数 {qnorm:.2e} 近零 → 不可归一化 → 位姿不可信")

        # camera_id 引用必须存在
        if img["camera_id"] not in cam_ids:
            raise SystemExit(
                f"--precomputed-colmap: images.bin image_id={iid} "
                f"引用 camera_id={img['camera_id']} 不在 cameras.bin 中")

    # --- image_name ↔ photos 绑定 ---
    names = [img["normalized_name"] for img in images]
    photo_names: set[str] = set()
    for p in photos.rglob("*"):
        if p.is_file() and p.suffix.lower() in FINGERPRINT_SUFFIXES:
            relative = p.relative_to(photos).as_posix()
            try:
                normalized = _normalize_colmap_image_name(relative)
            except ValueError as exc:
                raise SystemExit(
                    "--precomputed-colmap: photos 目录含不可绑定的路径 "
                    f"{relative!r}: {exc}"
                ) from exc
            if normalized in photo_names:
                raise SystemExit(
                    "--precomputed-colmap: photos 路径规范化碰撞: "
                    f"{relative!r} -> {normalized!r}"
                )
            photo_names.add(normalized)
    missing = [name for name in names if name not in photo_names]
    if missing:
        raise SystemExit(
            f"--precomputed-colmap: images.bin 引用了 photos 目录中不存在的图像: "
            f"{missing[:3]}")


# ============================================================================
# HANDOFF-GLM-008 Task 3 — transactional three-target replacement
#
# REVIEW-CODEX-030 P0 (commit 0978ee7 held): three independent renames are
# not one atomic replacement. Codex injected a failure into the database
# replacement after the sparse directory swap and measured mixed_generation
# = true (sparse=NEW, db=OLD, images=OLD). The fix is a transaction journal
# (prepared → swapping → verified → committed) with backup, full rollback
# on any swap failure, and restart recovery.
#
# Three targets: ws/sparse/0, ws/colmap.db, ws/images.
# Backup lives in ws/.precomputed_backup/{sparse_0,colmap_db,images}.
# Journal lives in ws/.precomputed_txn.json.
# Staging lives in ws/.staging_precomputed/.
# ============================================================================

_PRECOMPUTED_TXN_JOURNAL = ".precomputed_txn.json"
_PRECOMPUTED_STAGING = ".staging_precomputed"
_PRECOMPUTED_BACKUP = ".precomputed_backup"
_TXN_VERSION = 2

# v2 transaction phases (in order). Each destructive rename boundary gets its
# own phase so recovery can resume precisely. REVIEW-CODEX-034 #4: each
# destructive action is preceded by an ``intent_*`` phase (write-ahead) —
# recovery seeing an intent phase must NOT assume the mutation ran; it must
# recompute exact bytes (REVIEW-CODEX-034 #6) to decide whether the mutation
# completed (→ advance to the matching ``*_moved``/``*_done`` phase) or did
# not run (→ revert to the prior phase).
#
#   prepared                  : staging copy + semantic validate done; no backup
#   intent_backup_sparse      : about to rename old sparse/0 → backup
#   backup_sparse_moved       : old sparse/0 moved to backup
#   intent_backup_db          : about to rename/unlink old colmap.db
#   backup_db_moved           : old colmap.db handled
#   intent_backup_images      : about to rename old images/
#   backup_images_moved       : old images/ moved to backup
#   intent_install_sparse     : about to swap new sparse → dst
#   install_sparse_done       : new sparse installed
#   intent_install_db         : about to swap new db → dst
#   install_db_done           : new db installed
#   intent_install_images     : about to swap new images → dst
#   install_images_done       : all three new installed; verify pending
#   verified                  : post-swap byte+semantic verify passed
#   committed                 : cleanup done; transaction complete
#   recovery_required         : ambiguous state; preserve evidence, raise
_PHASE_PREPARED = "prepared"
_PHASE_INTENT_BACKUP_SPARSE = "intent_backup_sparse"
_PHASE_BACKUP_SPARSE = "backup_sparse_moved"
_PHASE_INTENT_BACKUP_DB = "intent_backup_db"
_PHASE_BACKUP_DB = "backup_db_moved"
_PHASE_INTENT_BACKUP_IMAGES = "intent_backup_images"
_PHASE_BACKUP_IMAGES = "backup_images_moved"
_PHASE_INTENT_INSTALL_SPARSE = "intent_install_sparse"
_PHASE_INSTALL_SPARSE = "install_sparse_done"
_PHASE_INTENT_INSTALL_DB = "intent_install_db"
_PHASE_INSTALL_DB = "install_db_done"
_PHASE_INTENT_INSTALL_IMAGES = "intent_install_images"
_PHASE_INSTALL_IMAGES = "install_images_done"
_PHASE_VERIFIED = "verified"
_PHASE_COMMITTED = "committed"
_PHASE_RECOVERY_REQUIRED = "recovery_required"

# Ordered list for "have we passed this boundary" checks.
_PHASE_ORDER = (
    _PHASE_PREPARED,
    _PHASE_INTENT_BACKUP_SPARSE,
    _PHASE_BACKUP_SPARSE,
    _PHASE_INTENT_BACKUP_DB,
    _PHASE_BACKUP_DB,
    _PHASE_INTENT_BACKUP_IMAGES,
    _PHASE_BACKUP_IMAGES,
    _PHASE_INTENT_INSTALL_SPARSE,
    _PHASE_INSTALL_SPARSE,
    _PHASE_INTENT_INSTALL_DB,
    _PHASE_INSTALL_DB,
    _PHASE_INTENT_INSTALL_IMAGES,
    _PHASE_INSTALL_IMAGES,
    _PHASE_VERIFIED,
    _PHASE_COMMITTED,
)

# Map each intent phase → (matching complete phase, prior phase). Used by
# recovery to advance or revert when it sees an intent phase on disk.
_INTENT_NEXT = {
    _PHASE_INTENT_BACKUP_SPARSE: _PHASE_BACKUP_SPARSE,
    _PHASE_INTENT_BACKUP_DB: _PHASE_BACKUP_DB,
    _PHASE_INTENT_BACKUP_IMAGES: _PHASE_BACKUP_IMAGES,
    _PHASE_INTENT_INSTALL_SPARSE: _PHASE_INSTALL_SPARSE,
    _PHASE_INTENT_INSTALL_DB: _PHASE_INSTALL_DB,
    _PHASE_INTENT_INSTALL_IMAGES: _PHASE_INSTALL_IMAGES,
}
_INTENT_PRIOR = {
    _PHASE_INTENT_BACKUP_SPARSE: _PHASE_PREPARED,
    _PHASE_INTENT_BACKUP_DB: _PHASE_BACKUP_SPARSE,
    _PHASE_INTENT_BACKUP_IMAGES: _PHASE_BACKUP_DB,
    _PHASE_INTENT_INSTALL_SPARSE: _PHASE_BACKUP_IMAGES,
    _PHASE_INTENT_INSTALL_DB: _PHASE_INSTALL_SPARSE,
    _PHASE_INTENT_INSTALL_IMAGES: _PHASE_INSTALL_DB,
}


class RecoveryRequired(SystemExit):
    """Recovery could not safely converge — evidence preserved for manual
    inspection. Caller must abort; do not start a new transaction."""


def _write_txn_journal(path: Path, journal: dict) -> None:
    """Atomic journal write: temp file in same dir + os.replace.

    REVIEW-CODEX-033 #4: direct write_text can truncate the journal on
    interruption. A same-directory temp + atomic replace guarantees the
    journal is either fully the old version or fully the new version —
    never a partial mix. Temp-write and replace failures raise (caller
    decides); the existing journal is left intact.
    """
    blob = json.dumps(journal, sort_keys=True, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise SystemExit(f"事务日志写入失败 (atomic): {e}") from e


def _recursive_manifest(root: Path) -> dict[str, dict[str, int | str]]:
    """Recursive byte manifest of a directory tree.

    Returns ``{relative_posix_path: {"size": int, "sha256": str}}`` for
    every regular file under ``root``. Same-name changed bytes produce a
    different sha256, so byte-level mutation is detectable. Symlinks are
    rejected (provenance safety — a symlink could point anywhere).

    Nonexistent root → empty manifest (caller decides what to do).
    """
    manifest: dict[str, dict[str, int | str]] = {}
    if not root.exists():
        return manifest
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise SystemExit(
                f"拒绝符号链接（provenance safety）: {p}")
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        manifest[rel] = {
            "size": p.stat().st_size,
            "sha256": _sha256_file(p),
        }
    return manifest


def _manifests_equal(a: dict, b: dict) -> tuple[bool, str]:
    """Compare two recursive manifests. Returns (equal, reason).

    Used by post-swap verification. Both manifests must have the same set
    of relative paths, and each entry's size + sha256 must match.
    """
    a_keys = set(a)
    b_keys = set(b)
    if a_keys != b_keys:
        only_a = sorted(a_keys - b_keys)
        only_b = sorted(b_keys - a_keys)
        return False, (
            f"file set mismatch: only_in_actual={only_a} only_in_expected={only_b}")
    for k in a_keys:
        if a[k] != b[k]:
            return False, f"byte mismatch at {k}: actual={a[k]} expected={b[k]}"
    return True, ""


def _dirs_byte_equal(a: Path, b: Path) -> bool:
    """True iff two directories have identical recursive byte manifests.

    REVIEW-CODEX-034 #9: restore helpers use this to detect a partial dst
    left by an interrupted copy (e.g. ``shutil.copytree`` crashed mid-way).
    If dst exists but bytes differ from backup, the restore must rmtree
    dst and re-copy so 2nd/3rd recovery converges to one exact generation
    instead of stalling at ``recovery_required`` with a partial live dst.
    """
    if not a.is_dir() or not b.is_dir():
        return False
    ma = _recursive_manifest(a)
    mb = _recursive_manifest(b)
    ok, _ = _manifests_equal(ma, mb)
    return ok


def _dst_matches_generation(ws: Path, manifest: dict) -> bool:
    """Check if the current destination bytes exactly match a generation
    manifest.

    REVIEW-CODEX-034 #6: recovery must identify a complete candidate by
    recomputing exact bytes, not from phase names or path existence.

    The manifest has the shape::

        {
            "sparse/0": {rel_path: {size, sha256}, ...} | {},
            "colmap.db": {size, sha256} | None,
            "images":    {rel_path: {size, sha256}, ...} | {},
        }

    An empty sparse/0 manifest means the old generation had no sparse
    (first install). A None colmap.db means that generation had no db.

    Returns True only if ALL three targets match exactly: same file set,
    same sizes, same SHA-256s. Any mismatch → False (caller decides
    whether to rollback or preserve evidence).
    """
    # sparse/0
    actual_sparse = _recursive_manifest(ws / "sparse" / "0")
    expected_sparse = manifest.get("sparse/0") or {}
    ok, _ = _manifests_equal(actual_sparse, expected_sparse)
    if not ok:
        return False
    # colmap.db
    expected_db = manifest.get("colmap.db")
    dst_db = ws / "colmap.db"
    if expected_db is None:
        if dst_db.is_file():
            return False  # expected no db, but dst has one
    else:
        if not dst_db.is_file():
            return False  # expected db, but dst missing
        actual_db = {"size": dst_db.stat().st_size,
                     "sha256": _sha256_file(dst_db)}
        if actual_db != expected_db:
            return False
    # images/
    actual_img = _recursive_manifest(ws / "images")
    expected_img = manifest.get("images") or {}
    ok, _ = _manifests_equal(actual_img, expected_img)
    if not ok:
        return False
    return True


def _validate_journal_v2(journal: dict) -> tuple[bool, str]:
    """Strict validation of a v2 journal before any cleanup decision.

    REVIEW-CODEX-034 #3/#5: a parseable but structurally invalid journal
    must NOT authorize backup deletion. Validates:
    - version == 2
    - txn_id is a non-empty string
    - phase is in the allowlist
    - has_db is a real bool
    - new_generation_manifest is structurally valid (has sparse/0 key)
    - old_generation_manifest is structurally valid (has sparse/0 key)

    Returns (valid, reason). Invalid journals with real backup evidence
    must preserve everything and raise RecoveryRequired.
    """
    if not isinstance(journal, dict):
        return False, "journal is not a dict"
    if journal.get("version") != 2:
        return False, f"version != 2 (got {journal.get('version')!r})"
    txn_id = journal.get("txn_id")
    if not isinstance(txn_id, str) or not txn_id:
        return False, f"txn_id missing or not a non-empty string ({txn_id!r})"
    phase = journal.get("phase")
    if phase not in _PHASE_ORDER and phase != _PHASE_RECOVERY_REQUIRED:
        return False, f"phase {phase!r} not in allowlist"
    has_db = journal.get("has_db")
    if not isinstance(has_db, bool):
        return False, f"has_db is not a bool ({has_db!r})"
    new_manifest = journal.get("new_generation_manifest")
    if not isinstance(new_manifest, dict) or "sparse/0" not in new_manifest:
        return False, "new_generation_manifest missing or invalid (no sparse/0 key)"
    old_manifest = journal.get("old_generation_manifest")
    if not isinstance(old_manifest, dict) or "sparse/0" not in old_manifest:
        return False, "old_generation_manifest missing or invalid (no sparse/0 key)"
    return True, ""


def _has_real_backup_evidence(backup: Path) -> bool:
    """True iff backup dir contains at least one real backup member
    (sparse_0 dir, colmap_db file, or images dir).

    Used by recovery to decide whether to preserve evidence vs cleanup
    stray staging/backup dirs. A backup dir containing only junk files
    (e.g. from a corrupt test setup) is NOT real evidence.
    """
    if not backup.is_dir():
        return False
    return (backup / "sparse_0").is_dir() \
        or (backup / "colmap_db").is_file() \
        or (backup / "images").is_dir()


def _restore_sparse_from_backup(ws: Path, backup: Path) -> None:
    """Restore only sparse/0 from backup (idempotent, COPY — preserves backup).

    If backup has sparse_0 and destination doesn't: copy backup → dst.
    If backup has sparse_0 and destination also has sparse: leave both
    (already restored — idempotent). If backup has no sparse_0: noop.

    Uses COPY (shutil.copytree) not rename, so the backup member is preserved
    as audit evidence. Callers that have already verified the destination
    must explicitly rmtree backup when commit/cleanup is appropriate.
    """
    bk_sparse = backup / "sparse_0"
    dst_sparse = ws / "sparse" / "0"
    if not bk_sparse.is_dir():
        return  # nothing to restore
    if dst_sparse.exists():
        if _dirs_byte_equal(dst_sparse, bk_sparse):
            return
        shutil.rmtree(dst_sparse)
    (ws / "sparse").mkdir(parents=True, exist_ok=True)
    shutil.copytree(bk_sparse, dst_sparse)


def _restore_db_from_backup(ws: Path, backup: Path) -> None:
    """Restore only colmap.db from backup (idempotent, COPY — preserves backup).

    If backup has colmap_db and destination doesn't: copy backup → dst.
    If backup has colmap_db and destination has colmap.db: leave both.
    If backup has no colmap_db: noop (do NOT delete destination db — that's
    a destructive action reserved for commit/cleanup).
    """
    bk_db = backup / "colmap_db"
    dst_db = ws / "colmap.db"
    if not bk_db.is_file():
        return
    if dst_db.exists():
        if dst_db.is_file() \
                and dst_db.stat().st_size == bk_db.stat().st_size \
                and _sha256_file(dst_db) == _sha256_file(bk_db):
            return
        dst_db.unlink()
    shutil.copy2(bk_db, dst_db)


def _restore_images_from_backup(ws: Path, backup: Path) -> None:
    """Restore only images/ from backup (idempotent, COPY — preserves backup).

    If backup has images/ and destination doesn't: copy backup → dst.
    If both have images/: leave both (idempotent).
    If backup has no images/: noop.
    """
    bk_img = backup / "images"
    dst_img = ws / "images"
    if not bk_img.is_dir():
        return
    if dst_img.exists():
        if _dirs_byte_equal(dst_img, bk_img):
            return
        shutil.rmtree(dst_img)
    shutil.copytree(bk_img, dst_img)


def _restore_backup(ws: Path, backup: Path) -> None:
    """Restore all three targets from backup (idempotent, full rollback).

    Calls per-target restore helpers. Each target is only renamed if the
    backup has it AND the destination doesn't (idempotent — safe to call
    multiple times during multi-pass recovery).
    """
    if not backup.is_dir():
        return
    _restore_sparse_from_backup(ws, backup)
    _restore_db_from_backup(ws, backup)
    _restore_images_from_backup(ws, backup)


def _resolve_intent_phase(
        intent_phase: str,
        ws: Path,
        backup: Path,
        staging: Path,
        old_manifest: dict,
        new_manifest: dict,
        has_db: bool) -> str:
    """Decide whether the destructive mutation described by an intent phase
    actually ran, by recomputing exact bytes against immutable manifests.

    REVIEW-CODEX-034 #4/#6: write-ahead logging means the journal may
    persist the intent phase BEFORE the mutation runs (e.g. a crash or
    injected journal-write failure between ``_write_txn_journal`` and the
    following ``rename``/``unlink``). Recovery cannot assume the mutation
    completed just because the intent is on disk.

    Decision matrix per intent phase (using sparse/db/images target pairs):

    backup_sparse intent (rename old dst sparse → backup/sparse_0):
      - dst sparse/0 exists and bytes match old_manifest.sparse/0 →
        mutation did NOT run → return "prior" (revert to prepared)
      - dst sparse/0 absent AND backup/sparse_0 exists and bytes match
        old_manifest.sparse/0 → mutation DID run → return "complete"
      - else → "ambiguous"

    backup_db intent (rename/unlink old dst db):
      - has_db=True (mutation = rename old db → backup/colmap_db):
          * dst db exists and matches old_manifest.colmap.db → NOT run
          * dst db absent AND backup/colmap_db exists and matches
            old_manifest.colmap.db → complete
          * else → ambiguous
      - has_db=False (mutation = unlink stale dst db if exists):
          * dst db exists and matches old_manifest.colmap.db (which is
            None) — wait, has_db=False means new gen has no db, but the
            OLD gen might still have a db that needs unlinking.
            old_manifest.colmap.db describes the OLD generation's db.
            If old_manifest.colmap.db is None → old gen had no db,
            mutation is a no-op → "complete" (idempotent noop).
            Else → dst db should not exist post-mutation.
              * dst db absent → complete
              * dst db present and matches old_manifest.colmap.db → NOT run
              * else → ambiguous

    backup_images intent (rename old dst images → backup/images):
      - dst images/ exists and bytes match old_manifest.images → NOT run
      - dst images/ absent AND backup/images/ exists and bytes match
        old_manifest.images → complete
      - else → ambiguous

    install_sparse intent (swap staging sparse → dst sparse):
      - staging sparse/0 exists and bytes match new_manifest.sparse/0 →
        mutation did NOT run → "prior"
      - staging sparse/0 absent AND dst sparse/0 exists and bytes match
        new_manifest.sparse/0 → mutation DID run → "complete"
      - else → ambiguous

    install_db intent (swap staging db → dst db):
      - has_db=True:
          * staging db exists and matches new_manifest.colmap.db → NOT run
          * staging db absent AND dst db matches new_manifest.colmap.db → complete
          * else → ambiguous
      - has_db=False: mutation is a no-op (no new db to install; dst db
        was already unlinked in backup_db phase) → "complete"

    install_images intent (swap staging images → dst images):
      - staging images/ exists and matches new_manifest.images → NOT run
      - staging images/ absent AND dst images/ matches new_manifest.images → complete
      - else → ambiguous

    Manifest-shape note:
      - new_manifest["sparse/0"] and old_manifest["sparse/0"] are dicts
        ``{rel_path: {size, sha256}, ...}`` (possibly empty for old gen
        on first install — empty dict means "that generation had no sparse
        dir", not "missing").
      - new_manifest["colmap.db"] and old_manifest["colmap.db"] are either
        None (no db) or ``{"size": int, "sha256": str}``.
      - new_manifest["images"] and old_manifest["images"] are dicts.
    """
    # Helper: does a target's bytes match a manifest entry?
    def _sparse_matches(actual_dir: Path, expected: dict) -> bool:
        if not expected:
            return False  # empty expected → no sparse dir; caller handles
        if not actual_dir.is_dir():
            return False
        actual = _recursive_manifest(actual_dir)
        ok, _ = _manifests_equal(actual, expected)
        return ok

    def _db_matches(actual_path: Path, expected) -> bool:
        if expected is None:
            return not actual_path.is_file()
        if not actual_path.is_file():
            return False
        return (actual_path.stat().st_size == expected["size"]
                and _sha256_file(actual_path) == expected["sha256"])

    def _images_matches(actual_dir: Path, expected: dict) -> bool:
        if not actual_dir.is_dir():
            return False
        actual = _recursive_manifest(actual_dir)
        ok, _ = _manifests_equal(actual, expected)
        return ok

    dst_sparse = ws / "sparse" / "0"
    dst_db = ws / "colmap.db"
    dst_img = ws / "images"
    staging_sparse = staging / "sparse" / "0"
    staging_db = staging / "colmap.db"
    staging_img = staging / "images"
    bk_sparse = backup / "sparse_0"
    bk_db = backup / "colmap_db"
    bk_img = backup / "images"

    old_sparse = old_manifest.get("sparse/0") or {}
    old_db = old_manifest.get("colmap.db")
    old_images = old_manifest.get("images") or {}
    new_sparse = new_manifest.get("sparse/0") or {}
    new_db = new_manifest.get("colmap.db")
    new_images = new_manifest.get("images") or {}

    if intent_phase == _PHASE_INTENT_BACKUP_SPARSE:
        # Mutation: rename old dst sparse → backup/sparse_0 (only if old
        # gen actually had a sparse dir).
        if not old_sparse:
            # Old gen had no sparse (first install). Mutation is a no-op.
            return "complete"
        if _sparse_matches(dst_sparse, old_sparse):
            return "prior"  # mutation did NOT run; dst still has old sparse
        if (not dst_sparse.exists() and bk_sparse.is_dir()
                and _sparse_matches(bk_sparse, old_sparse)):
            return "complete"
        return "ambiguous"

    if intent_phase == _PHASE_INTENT_BACKUP_DB:
        # Backup behavior depends only on OLD db presence. NEW db presence
        # controls the later install step and must never erase old evidence.
        if old_db is None:
            # Old generation had no db, so backup is a no-op.
            return "complete"
        if _db_matches(dst_db, old_db):
            return "prior"
        if (not dst_db.is_file() and bk_db.is_file()
                and _db_matches(bk_db, old_db)):
            return "complete"
        return "ambiguous"

    if intent_phase == _PHASE_INTENT_BACKUP_IMAGES:
        if not old_images:
            return "complete"  # no-op
        if _images_matches(dst_img, old_images):
            return "prior"
        if (not dst_img.exists() and bk_img.is_dir()
                and _images_matches(bk_img, old_images)):
            return "complete"
        return "ambiguous"

    if intent_phase == _PHASE_INTENT_INSTALL_SPARSE:
        # Mutation: swap staging sparse → dst sparse (staging → dst).
        if _sparse_matches(staging_sparse, new_sparse):
            return "prior"  # staging still has new sparse → not installed
        if (not staging_sparse.exists() and dst_sparse.is_dir()
                and _sparse_matches(dst_sparse, new_sparse)):
            return "complete"
        return "ambiguous"

    if intent_phase == _PHASE_INTENT_INSTALL_DB:
        if not has_db:
            # No new db to install. Mutation is a no-op.
            return "complete"
        if _db_matches(staging_db, new_db):
            return "prior"
        if (not staging_db.is_file() and _db_matches(dst_db, new_db)):
            return "complete"
        return "ambiguous"

    if intent_phase == _PHASE_INTENT_INSTALL_IMAGES:
        if _images_matches(staging_img, new_images):
            return "prior"
        if (not staging_img.exists() and dst_img.is_dir()
                and _images_matches(dst_img, new_images)):
            return "complete"
        return "ambiguous"

    return "ambiguous"


def _recover_precomputed_transaction(ws: Path) -> None:
    """Inspect journal from a previous crashed transaction and restore a
    coherent destination.

    Called at the top of _copy_precomputed_to_ws and safe to call
    independently. Phase-based decision matrix (v2):

    Ambiguous cases (preserve all evidence, raise RecoveryRequired):
      - missing / corrupt / wrong-version / unknown-phase journal AND
        backup has real evidence (sparse_0 / colmap_db / images).
      - v1 ``state=prepared`` + real backup (the data-loss case Codex
        reproduced on d12e265).
      - v2 ``phase=install_*_done`` + first install (no backup) +
        destination fails byte verify (can't safely commit).
      - ``phase=recovery_required`` (already marked by a prior recovery).

    Recovery-completes-silently cases:
      - no journal + no real backup → noop (clean stray staging/backup).
      - corrupt / unknown journal + no real backup → cleanup (no evidence
        to preserve).
      - v2 ``phase=prepared`` → cleanup staging + journal (no swap started).
      - v2 ``phase=backup_sparse_moved`` → restore sparse from backup
        (idempotent; db+images still at dst, untouched).
      - v2 ``phase=backup_db_moved`` → restore sparse + db.
      - v2 ``phase=backup_images_moved`` → restore all three.
      - v2 ``phase=install_sparse_done`` / ``install_db_done`` → rollback
        partial install: restore from backup if exists; else delete partial
        new content (first install).
      - v2 ``phase=install_images_done`` → run post-swap verify against
        new_generation_manifest; commit if pass, else mark recovery_required.
      - v2 ``phase=verified`` → commit (cleanup staging + backup + journal).
      - v2 ``phase=committed`` → noop (already done; clean stray staging).
      - v1 ``state=swapping`` / ``state=verified`` → restore from backup
        (legacy backward compat for tests that write v1 journals).
      - v1 ``state=committed`` → noop.
    """
    journal_path = ws / _PRECOMPUTED_TXN_JOURNAL
    staging = ws / _PRECOMPUTED_STAGING
    backup = ws / _PRECOMPUTED_BACKUP

    def _cleanup_strays() -> None:
        """Remove staging + backup (only if no real evidence) + journal."""
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not _has_real_backup_evidence(backup):
            shutil.rmtree(backup, ignore_errors=True)
        journal_path.unlink(missing_ok=True)

    def _preserve_and_raise(reason: str) -> None:
        """Mark recovery_required in journal (if writable) and raise.

        REVIEW-CODEX-033 #9: "Never delete staging/backup/journal until one
        live generation has passed the expected exact-byte manifest plus
        semantic validation. If state is ambiguous or both generations fail,
        stop with a recovery-required error and preserve all evidence."

        Before raising, attempt to copy-restore any missing piece from backup
        → destination (idempotent: skips targets that already exist at dst,
        which may be a partial new install we cannot safely overwrite without
        a journal telling us the phase). This leaves the destination as
        coherent as the evidence allows without destroying the backup. The
        backup is preserved verbatim for human audit.
        """
        _restore_backup(ws, backup)
        try:
            existing = json.loads(journal_path.read_text(encoding="utf-8")) \
                if journal_path.is_file() else {}
        except (OSError, ValueError, json.JSONDecodeError):
            existing = {}
        existing["version"] = _TXN_VERSION
        existing["phase"] = _PHASE_RECOVERY_REQUIRED
        existing["recovery_reason"] = reason
        existing["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        try:
            _write_txn_journal(journal_path, existing)
        except SystemExit:
            pass  # journal write failure shouldn't mask the original reason
        raise RecoveryRequired(
            f"恢复中止：{reason}；保留 staging/backup/journal 等待人工审计 "
            f"(ws={ws})")

    # --- no journal ---
    if not journal_path.is_file():
        if _has_real_backup_evidence(backup):
            _preserve_and_raise(
                "journal 缺失但 backup 含真实证据 — 状态不明确")
        _cleanup_strays()
        return

    # --- parse journal ---
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        if _has_real_backup_evidence(backup):
            _preserve_and_raise("journal 损坏且 backup 含真实证据")
        _cleanup_strays()
        return

    version = journal.get("version")
    if version == 1:
        # Legacy v1 journal — only handle swapping/verified (existing tests).
        # state=prepared + real backup → ambiguous (data-loss case).
        state = journal.get("state", "")
        if state == "swapping":
            # v1 state=swapping means "old → backup done, new → live in
            # progress, crashed mid-install." Destination may hold a partial
            # NEW install that must be overwritten (not preserved alongside
            # backup — that would mix generations). Use _force_restore_backup
            # (rmtree dst member, copy backup → dst) so the destination ends
            # up as the verified-old generation. Backup is preserved (copy
            # semantics); explicit rmtree at commit.
            _force_restore_backup(ws, backup)
            _cleanup_strays_with_evidence(backup, journal_path, staging)
            return
        if state == "verified":
            # Post-swap verify passed; only cleanup was interrupted. Don't
            # restore — destination is the verified new generation.
            _cleanup_strays_with_evidence(backup, journal_path, staging)
            return
        if state == "committed":
            _cleanup_strays()
            return
        # state=prepared / unknown state + real backup → ambiguous
        if _has_real_backup_evidence(backup):
            _preserve_and_raise(
                f"v1 state={state!r} + 真实 backup — 状态不明确")
        _cleanup_strays()
        return

    if version != _TXN_VERSION:
        if _has_real_backup_evidence(backup):
            _preserve_and_raise(
                f"journal version={version!r} 不支持且 backup 含真实证据")
        _cleanup_strays()
        return

    # REVIEW-CODEX-034 #3: strict v2 journal validation BEFORE any cleanup
    # decision. A parseable but structurally invalid journal (missing
    # txn_id, unknown phase, non-bool has_db, missing/invalid manifests)
    # must NOT authorize backup deletion — preserve evidence and raise.
    valid, reason = _validate_journal_v2(journal)
    if not valid:
        if _has_real_backup_evidence(backup):
            _preserve_and_raise(
                f"journal 结构无效 ({reason}) 且 backup 含真实证据")
        # No real backup evidence → safe to clean up strays (no data to lose).
        _cleanup_strays()
        return

    phase = journal.get("phase", "")
    new_manifest = journal.get("new_generation_manifest") or {}
    has_db = journal.get("has_db", True)
    old_manifest = journal.get("old_generation_manifest") or {}

    # --- REVIEW-CODEX-034 #4/#6: intent phase resolution ---
    # Write-ahead logging means an intent phase on disk describes what was
    # ABOUT to happen, not what did happen. We must recompute exact bytes
    # against the immutable old/new manifests to decide:
    #   - mutation did NOT run → revert journal to the prior phase and
    #     re-dispatch (fall through to the prior phase's recovery branch);
    #   - mutation DID run → advance journal to the matching complete phase
    #     and re-dispatch (fall through to the complete phase's branch);
    #   - ambiguous (both/neither match, or backup member mismatch) →
    #     preserve all evidence and raise RecoveryRequired.
    if phase in _INTENT_NEXT:
        resolved = _resolve_intent_phase(
            phase, ws, backup, staging,
            old_manifest, new_manifest, has_db)
        if resolved == "ambiguous":
            _preserve_and_raise(
                f"intent phase {phase!r} 状态不明确 "
                f"(dst/backup/manifests 不可判定 mutation 是否已完成)")
        new_phase = _INTENT_NEXT[phase] if resolved == "complete" \
            else _INTENT_PRIOR[phase]
        journal["phase"] = new_phase
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        journal["intent_resolution"] = {
            "from": phase,
            "to": new_phase,
            "evidence": resolved,
            "resolved_at_utc": datetime.now(UTC).isoformat(),
        }
        _write_txn_journal(journal_path, journal)
        phase = new_phase  # fall through to the resolved phase's branch

    # --- v2 phase-based recovery ---
    if phase == _PHASE_COMMITTED:
        # REVIEW-CODEX-034 #8: a parseable phase=committed journal does NOT
        # bypass validation when a real backup still exists. If cleanup
        # was interrupted mid-way (backup survives), recompute dst bytes
        # against new_generation_manifest before authorizing backup removal.
        # A forged or stale committed journal with a tampered destination
        # must not authorize destroying the only rollback evidence.
        if not _dst_matches_generation(ws, new_manifest):
            _preserve_and_raise(
                "phase=committed 但 dst 字节与 new_generation_manifest "
                "不匹配 — 拒绝清理事务证据"
            )
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if _has_real_backup_evidence(backup):
            shutil.rmtree(backup, ignore_errors=True)
        elif backup.exists():
            # No real evidence in backup — safe to remove stray dir.
            shutil.rmtree(backup, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        return

    if phase == _PHASE_VERIFIED:
        # REVIEW-CODEX-034 #7/#8: post-swap verify was claimed, but if a
        # real backup still exists the cleanup was interrupted. Recompute
        # dst bytes against new_generation_manifest before deleting backup.
        # If dst fails verification, either rollback to backup (if backup
        # holds a complete old generation) or preserve all evidence.
        if _dst_matches_generation(ws, new_manifest):
            # dst verified as complete new generation — safe to clean all
            # redundant transaction evidence, whether backup exists or not.
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            journal_path.unlink(missing_ok=True)
            return
        if _has_real_backup_evidence(backup):
            # dst does not match new manifest. Try rolling back to backup
            # (the old generation). If backup is also incomplete, preserve.
            _force_restore_backup(ws, backup)
            if _dst_matches_generation(ws, old_manifest):
                # Successfully rolled back to old generation.
                shutil.rmtree(backup, ignore_errors=True)
                journal_path.unlink(missing_ok=True)
                return
            _preserve_and_raise(
                "phase=verified: dst 不匹配 new_manifest 且 rollback 后"
                "也不匹配 old_manifest — 双向都不完整")
        _preserve_and_raise(
            "phase=verified 且无 backup，但 dst 字节不匹配 "
            "new_generation_manifest — 拒绝隐藏损坏"
        )

    if phase == _PHASE_PREPARED:
        # Staging done, no backup yet. Safe to clean up — destination is
        # still the old committed generation.
        _cleanup_strays()
        return

    if phase in (_PHASE_BACKUP_SPARSE, _PHASE_BACKUP_DB, _PHASE_BACKUP_IMAGES):
        # Crashed during old→backup moves. Restore incrementally based on
        # what's in backup. Idempotent — safe to call multiple times.
        if phase == _PHASE_BACKUP_SPARSE:
            _restore_sparse_from_backup(ws, backup)
        elif phase == _PHASE_BACKUP_DB:
            _restore_sparse_from_backup(ws, backup)
            _restore_db_from_backup(ws, backup)
        else:  # _PHASE_BACKUP_IMAGES
            _restore_backup(ws, backup)
        # REVIEW-CODEX-034 #7: verify the restored destination exactly
        # matches the old_generation_manifest before deleting backup. If
        # restore was partial or backup was already incomplete, preserve.
        if not _dst_matches_generation(ws, old_manifest):
            _preserve_and_raise(
                f"phase={phase}: restore 后 dst 不匹配 old_generation_manifest "
                f"— 拒绝删除 backup")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        return

    if phase in (_PHASE_INSTALL_SPARSE, _PHASE_INSTALL_DB):
        # Partial new install at destination. Rollback to old generation.
        if _has_real_backup_evidence(backup):
            # Replace: restore from backup (overwrites partial new install).
            # _restore_*_from_backup is idempotent but won't overwrite an
            # existing dst member. For install_*_done rollback we MUST
            # overwrite (the new install is invalid). Use _force_restore.
            _force_restore_backup(ws, backup)
            # REVIEW-CODEX-034 #7: verify exact bytes after rollback.
            if not _dst_matches_generation(ws, old_manifest):
                _preserve_and_raise(
                    f"phase={phase}: rollback 后 dst 不匹配 "
                    f"old_generation_manifest — 拒绝删除 backup")
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
            journal_path.unlink(missing_ok=True)
            return
        # First install (no backup): delete partial new install. Only safe
        # if there's no old generation to preserve.
        _delete_partial_install(ws, has_db)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        return

    if phase == _PHASE_INSTALL_IMAGES:
        # All three new installed but post-swap verify pending. Try to
        # verify; commit if pass, else preserve evidence.
        if not new_manifest:
            _preserve_and_raise(
                "phase=install_images_done 但 new_generation_manifest 缺失 "
                "— 无法验证新安装")
        try:
            _verify_destination_post_swap(ws, new_manifest, has_db)
        except SystemExit as e:
            # Verify failed — destination has unverified content. Rollback
            # if we have a backup; else preserve evidence.
            if _has_real_backup_evidence(backup):
                _force_restore_backup(ws, backup)
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                shutil.rmtree(backup, ignore_errors=True)
                journal_path.unlink(missing_ok=True)
                raise SystemExit(
                    f"install_images verify 失败，已回滚到旧 generation: {e}"
                ) from e
            _preserve_and_raise(
                f"install_images verify 失败且无 backup: {e}")
        # Verify passed → commit (cleanup).
        journal["phase"] = _PHASE_VERIFIED
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        journal["phase"] = _PHASE_COMMITTED
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)
        journal_path.unlink(missing_ok=True)
        return

    if phase == _PHASE_RECOVERY_REQUIRED:
        # Already marked by prior recovery — preserve evidence.
        raise RecoveryRequired(
            f"journal 已标记 recovery_required — 等待人工审计 (ws={ws})")

    # Unknown phase
    if _has_real_backup_evidence(backup):
        _preserve_and_raise(f"未知 phase={phase!r} 且 backup 含真实证据")
    _cleanup_strays()


def _cleanup_strays_with_evidence(backup: Path, journal_path: Path,
                                    staging: Path) -> None:
    """After restoring from backup, clean up everything (backup is now empty
    or only has stale members that were already renamed back)."""
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    journal_path.unlink(missing_ok=True)


def _force_restore_backup(ws: Path, backup: Path) -> None:
    """Aggressive restore: overwrite any partial new install at destination
    with backup members. Used for install_*_done rollback and v1
    state=swapping (legacy journal where dst may hold partial new install).

    Unlike _restore_backup (idempotent, preserves existing dst), this
    rmtree/unlinks the destination member before copying backup → dst.
    Uses COPY (shutil.copytree / copy2) so the backup member is preserved
    as audit evidence — callers must explicitly rmtree backup at commit.
    """
    if not backup.is_dir():
        return
    bk_sparse = backup / "sparse_0"
    dst_sparse = ws / "sparse" / "0"
    if bk_sparse.is_dir():
        (ws / "sparse").mkdir(parents=True, exist_ok=True)
        if dst_sparse.exists():
            shutil.rmtree(dst_sparse)
        shutil.copytree(bk_sparse, dst_sparse)
    bk_db = backup / "colmap_db"
    dst_db = ws / "colmap.db"
    if bk_db.is_file():
        if dst_db.exists():
            dst_db.unlink()
        shutil.copy2(bk_db, dst_db)
    elif dst_db.exists():
        dst_db.unlink()  # old gen had no db → remove stale
    bk_img = backup / "images"
    dst_img = ws / "images"
    if bk_img.is_dir():
        if dst_img.exists():
            shutil.rmtree(dst_img)
        shutil.copytree(bk_img, dst_img)


def _rollback_to_old_generation(
        ws: Path,
        backup: Path,
        old_manifest: dict,
        has_db: bool,
) -> None:
    """Restore one exact old generation or preserve evidence and abort."""
    old_has_content = bool(old_manifest.get("sparse/0")) \
        or old_manifest.get("colmap.db") is not None \
        or bool(old_manifest.get("images"))
    if old_has_content:
        if not _has_real_backup_evidence(backup):
            raise RecoveryRequired(
                "旧 generation 有内容但 backup 缺失，拒绝清理事务证据"
            )
        _force_restore_backup(ws, backup)
    else:
        # First install: there is no committed old generation to restore.
        _delete_partial_install(ws, has_db)
    if not _dst_matches_generation(ws, old_manifest):
        raise RecoveryRequired(
            "rollback 后 dst 不匹配 old_generation_manifest，拒绝清理事务证据"
        )


def _delete_partial_install(ws: Path, has_db: bool) -> None:
    """Delete partial new install at destination (first install rollback).

    Used when phase=install_sparse_done / install_db_done and there's no
    backup (first install). The partial new install is unverified and
    cannot be committed — delete it so destination returns to empty.
    """
    dst_sparse = ws / "sparse" / "0"
    if dst_sparse.exists():
        shutil.rmtree(dst_sparse, ignore_errors=True)
    dst_db = ws / "colmap.db"
    if dst_db.exists():
        dst_db.unlink()
    dst_img = ws / "images"
    if dst_img.exists():
        shutil.rmtree(dst_img, ignore_errors=True)


def _swap_sparse(staging_sparse: Path, dst_sparse: Path) -> None:
    """Swap staging sparse/0 → destination sparse/0 (atomic rename).

    Caller has already moved the old destination to backup, so dst must not
    exist. If it does (partial prior swap), remove it before renaming.
    """
    if not staging_sparse.is_dir():
        raise SystemExit(f"swap_sparse 失败: staging 源不存在 {staging_sparse}")
    if dst_sparse.exists():
        shutil.rmtree(dst_sparse)
    staging_sparse.rename(dst_sparse)


def _swap_db(staging_db: Path, dst_db: Path, has_db: bool) -> None:
    """Swap staging colmap.db → destination.

    If source has a db, rename staging db → dst (dst was moved to backup
    already). If source has no db, remove any stale dst db (also already
    moved to backup, so normally a noop).
    """
    if has_db:
        if not staging_db.is_file():
            raise SystemExit(f"swap_db 失败: staging 源不存在 {staging_db}")
        if dst_db.exists():
            dst_db.unlink()
        staging_db.rename(dst_db)
    else:
        if dst_db.exists():
            dst_db.unlink()


def _swap_images(staging_img: Path, dst_img: Path) -> None:
    """Swap staging images/ → destination images/ (atomic rename)."""
    if not staging_img.is_dir():
        raise SystemExit(f"swap_images 失败: staging 源不存在 {staging_img}")
    if dst_img.exists():
        shutil.rmtree(dst_img)
    staging_img.rename(dst_img)


def _verify_destination_post_swap(ws: Path, expected_manifest: dict,
                                   has_db: bool) -> None:
    """Post-swap recursive byte-manifest verification.

    REVIEW-CODEX-033 #6: previous impl compared only top-level sparse
    filenames + existence of db/images. A byte mutation during rename
    (disk corruption) or a same-name changed bytes attack would pass.
    This impl walks sparse/0, colmap.db and images/ recursively and
    compares size + sha256 against ``expected_manifest`` (built from the
    staging copy before swap).

    ``expected_manifest`` shape::

        {
            "sparse/0": {rel_posix_path: {size, sha256}, ...},
            "colmap.db": {size, sha256} | None,
            "images":    {rel_posix_path: {size, sha256}, ...},
        }

    On any mismatch (file set, size, or sha256), raises SystemExit so the
    caller rolls back to backup. Also re-runs semantic validation on the
    swapped-in sparse/0 to catch format-level corruption that byte-equality
    can't detect (e.g. a structurally invalid cameras.bin with same bytes
    as another valid file — extremely unlikely but defense-in-depth).
    """
    sparse_0 = ws / "sparse" / "0"
    if not sparse_0.is_dir():
        raise SystemExit(f"post-swap 校验失败: {sparse_0} 不存在")
    actual_sparse = _recursive_manifest(sparse_0)
    expected_sparse_manifest = expected_manifest.get("sparse/0") or {}
    ok, reason = _manifests_equal(actual_sparse, expected_sparse_manifest)
    if not ok:
        raise SystemExit(f"post-swap 校验失败: sparse/0 字节 manifest 不匹配 — {reason}")
    # Semantic re-validation on the swapped-in destination
    _validate_sparse_semantics(sparse_0, ws / "images")
    dst_db = ws / "colmap.db"
    expected_db = expected_manifest.get("colmap.db")
    if has_db and expected_db is not None:
        if not dst_db.is_file():
            raise SystemExit("post-swap 校验失败: 期望 colmap.db 存在但缺失")
        actual_db_size = dst_db.stat().st_size
        actual_db_sha = _sha256_file(dst_db)
        if actual_db_size != expected_db["size"] \
                or actual_db_sha != expected_db["sha256"]:
            raise SystemExit(
                f"post-swap 校验失败: colmap.db 字节不匹配 "
                f"(actual=({actual_db_size},{actual_db_sha}), "
                f"expected=({expected_db['size']},{expected_db['sha256']}))")
    elif not has_db and expected_db is None:
        if dst_db.is_file():
            raise SystemExit("post-swap 校验失败: 源无 db 但目标有 stale db")
    else:
        raise SystemExit(
            f"post-swap 校验失败: colmap.db 期望状态 (has_db={has_db}, "
            f"expected_present={expected_db is not None}) 不一致")
    dst_img = ws / "images"
    if not dst_img.is_dir():
        raise SystemExit("post-swap 校验失败: images/ 缺失")
    actual_img = _recursive_manifest(dst_img)
    expected_img_manifest = expected_manifest.get("images") or {}
    ok, reason = _manifests_equal(actual_img, expected_img_manifest)
    if not ok:
        raise SystemExit(f"post-swap 校验失败: images/ 字节 manifest 不匹配 — {reason}")


def _build_generation_manifest_for_ws(ws: Path) -> dict:
    """Build a generation manifest (recursive byte snapshot) of the current
    destination sparse/0 + colmap.db + images/.

    Used to capture the OLD generation manifest before backup, so recovery
    can verify which generation is complete. Also used in tests to compare
    pre/post snapshots.
    """
    sparse_0 = ws / "sparse" / "0"
    manifest: dict = {
        "sparse/0": _recursive_manifest(sparse_0),
        "colmap.db": None,
        "images": _recursive_manifest(ws / "images"),
    }
    dst_db = ws / "colmap.db"
    if dst_db.is_file():
        manifest["colmap.db"] = {
            "size": dst_db.stat().st_size,
            "sha256": _sha256_file(dst_db),
        }
    return manifest


def _build_new_generation_manifest_from_staging(
        staging: Path, has_db: bool) -> dict:
    """Build a manifest of the staging copy (the NEW generation about to be
    installed). Same shape as _build_generation_manifest_for_ws.
    """
    staging_sparse = staging / "sparse" / "0"
    staging_img = staging / "images"
    manifest: dict = {
        "sparse/0": _recursive_manifest(staging_sparse),
        "colmap.db": None,
        "images": _recursive_manifest(staging_img),
    }
    staging_db = staging / "colmap.db"
    if has_db and staging_db.is_file():
        manifest["colmap.db"] = {
            "size": staging_db.stat().st_size,
            "sha256": _sha256_file(staging_db),
        }
    return manifest


def _copy_precomputed_to_ws(colmap_ws: Path, ws: Path) -> None:
    """Transactional three-target replacement: sparse/0 + colmap.db + images/.

    REVIEW-CODEX-033 v2 (held d12e265 transaction): the v1 single-state
    journal could not distinguish "crashed during old→backup moves" from
    "crashed during new→live installs" — recovery deleted the only complete
    backup in both cases. The v2 journal records a phase per destructive
    rename boundary so recovery can resume precisely or preserve evidence
    when state is ambiguous.

    Phase progression:
      prepared → backup_sparse_moved → backup_db_moved → backup_images_moved
              → install_sparse_done → install_db_done → install_images_done
              → verified → committed

    Each phase is written atomically (temp + os.replace) AFTER the rename
    completes. A crash leaves the journal at the last completed phase;
    _recover_precomputed_transaction resumes from there.

    Failure handling:
      - staging validate fail → cleanup staging + journal (no backup yet).
      - swap fail → _force_restore_backup (overwrite partial new install),
        cleanup, re-raise.
      - post-swap verify fail → _force_restore_backup, cleanup, re-raise.
      - any failure leaves the LAST VERIFIED destination intact and never
        runs COLMAP (precomputed branch skips COLMAP entirely).

    A failed run preserves a coherent verified destination. An ambiguous
    restart (corrupt journal + real backup) preserves all evidence and
    raises RecoveryRequired.
    """
    _recover_precomputed_transaction(ws)

    staging = ws / _PRECOMPUTED_STAGING
    backup = ws / _PRECOMPUTED_BACKUP
    journal_path = ws / _PRECOMPUTED_TXN_JOURNAL

    # --- Step 1: fresh staging ---
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    src_sparse = colmap_ws / "sparse" / "0"
    staging_sparse = staging / "sparse" / "0"
    staging_sparse.mkdir(parents=True)
    for name in PRECOMPUTED_REQUIRED_BIN + PRECOMPUTED_OPTIONAL_BIN:
        s = src_sparse / name
        if s.is_file():
            shutil.copy2(s, staging_sparse / name)
    db = colmap_ws / "colmap.db"
    staging_db = staging / "colmap.db"
    has_db = db.is_file()
    if has_db:
        shutil.copy2(db, staging_db)
    src_img = colmap_ws / "images"
    staging_img = staging / "images"
    try:
        shutil.copytree(src_img, staging_img)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise SystemExit(f"staging 拷贝失败 (copytree): {e}") from e

    # --- Step 2: staging semantic validation ---
    # Catches source corruption between manifest build and staging copy. If
    # this fails, no backup has been made and no swap happened — just clean
    # up staging. Destination is untouched.
    try:
        _validate_sparse_semantics(staging_sparse, src_img)
    except SystemExit:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # --- Step 3: build manifests + journal phase=prepared ---
    new_manifest = _build_new_generation_manifest_from_staging(staging, has_db)
    old_manifest = _build_generation_manifest_for_ws(ws)
    txn_id = uuid.uuid4().hex
    started_at = datetime.now(UTC).isoformat()
    journal = {
        "version": _TXN_VERSION,
        "txn_id": txn_id,
        "phase": _PHASE_PREPARED,
        "has_db": has_db,
        "new_generation_manifest": new_manifest,
        "old_generation_manifest": old_manifest,
        "started_at_utc": started_at,
        "phase_updated_at_utc": started_at,
    }
    _write_txn_journal(journal_path, journal)

    # --- Step 4: backup old destination (per-target, WAL + complete) ---
    # REVIEW-CODEX-034 #4: write intent phase BEFORE each destructive
    # rename/unlink. If recovery sees an intent phase on disk, it must
    # recompute exact bytes (REVIEW-CODEX-034 #6) to decide whether the
    # mutation ran (→ advance to matching complete phase) or did not
    # (→ revert to prior phase). Recovery never assumes mutation completed.
    backup.mkdir(parents=True, exist_ok=True)
    dst_sparse = ws / "sparse" / "0"
    dst_db = ws / "colmap.db"
    dst_img = ws / "images"

    # 4a. backup sparse (intent → mutate → complete)
    journal["phase"] = _PHASE_INTENT_BACKUP_SPARSE
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)
    if dst_sparse.is_dir():
        dst_sparse.rename(backup / "sparse_0")
    journal["phase"] = _PHASE_BACKUP_SPARSE
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)

    # 4b. backup db (intent → mutate → complete)
    # REVIEW-CODEX-034 #5: never unlink a live old db before its bytes are
    # captured in backup. The OLD generation's db presence is independent
    # of the NEW generation's db presence (has_db). If old_manifest has a
    # db, ALWAYS rename it to backup — even when the new gen has no db
    # (the new gen's "no db" is enforced at install_db by not swapping a
    # new db in; the old db must still be preserved as rollback evidence).
    journal["phase"] = _PHASE_INTENT_BACKUP_DB
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)
    old_has_db = old_manifest.get("colmap.db") is not None
    if old_has_db:
        if dst_db.is_file():
            dst_db.rename(backup / "colmap_db")
        # If dst_db absent but old_manifest says it should exist: the live
        # destination is already inconsistent — treat as ambiguous (recovery
        # will preserve evidence). For now, leave the backup empty; the
        # post-swap verify will fail and trigger rollback.
    else:
        # Old gen had no db. has_db=False → mutation is a no-op.
        # has_db=True → no old db to remove; new db will be installed at
        # install_db phase. Either way, no destructive action here.
        pass
    journal["phase"] = _PHASE_BACKUP_DB
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)

    # 4c. backup images (intent → mutate → complete)
    journal["phase"] = _PHASE_INTENT_BACKUP_IMAGES
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)
    if dst_img.is_dir():
        dst_img.rename(backup / "images")
    journal["phase"] = _PHASE_BACKUP_IMAGES
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)

    # --- Step 5: install new (per-target, WAL + complete) ---
    try:
        (ws / "sparse").mkdir(parents=True, exist_ok=True)
        journal["phase"] = _PHASE_INTENT_INSTALL_SPARSE
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)
        _swap_sparse(staging_sparse, dst_sparse)
        journal["phase"] = _PHASE_INSTALL_SPARSE
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)

        journal["phase"] = _PHASE_INTENT_INSTALL_DB
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)
        _swap_db(staging_db, dst_db, has_db)
        journal["phase"] = _PHASE_INSTALL_DB
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)

        journal["phase"] = _PHASE_INTENT_INSTALL_IMAGES
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)
        _swap_images(staging_img, dst_img)
        journal["phase"] = _PHASE_INSTALL_IMAGES
        journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
        _write_txn_journal(journal_path, journal)
    except (SystemExit, Exception):
        # Partial install — restore the exact old generation. On a first
        # install the old manifest is empty, so remove all partial new targets.
        _rollback_to_old_generation(ws, backup, old_manifest, has_db)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        raise

    # --- Step 6: post-swap verify (phase=verified) ---
    try:
        _verify_destination_post_swap(ws, new_manifest, has_db)
    except SystemExit:
        _rollback_to_old_generation(ws, backup, old_manifest, has_db)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        journal_path.unlink(missing_ok=True)
        raise

    journal["phase"] = _PHASE_VERIFIED
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)

    # --- Step 7: commit (cleanup staging + backup) ---
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    journal["phase"] = _PHASE_COMMITTED
    journal["phase_updated_at_utc"] = datetime.now(UTC).isoformat()
    _write_txn_journal(journal_path, journal)
    # Journal removed once committed — a clean workspace has no journal.
    # _recover_precomputed_transaction treats "no journal + no real backup"
    # as noop, so this is safe.
    journal_path.unlink(missing_ok=True)
    shutil.rmtree(backup, ignore_errors=True)
    journal_path.unlink(missing_ok=True)


def _assert_no_overlap(roots: dict[str, Path]) -> None:
    """拒绝相等或嵌套的已解析根路径——在任何 rmtree 前。

    REVIEW-CODEX-030 P7a-5：若 --work == --precomputed-colmap（或 --photos 在
    --work 内），rmtree(ws/images) 会删掉源照片/sparse。这些路径重叠意味着工作
    目录的清理操作会破坏输入，是破坏性的。用 resolve() 比较绝对路径，挡住相等和
    父子嵌套两种情况。
    """
    resolved = {name: p.resolve() for name, p in roots.items() if p is not None}
    names = list(resolved)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ra, rb = resolved[a], resolved[b]
            if ra == rb or ra in rb.parents or rb in ra.parents:
                raise SystemExit(
                    f"路径重叠（拒绝执行避免 rmtree 删源）: "
                    f"{a}={ra} 与 {b}={rb} 重叠")


def _file_fp(path: Path) -> list:
    """单文件廉价指纹 (名字, 字节数, mtime_ns)。局限见 FINGERPRINT_CAVEAT。"""
    st = path.stat()
    return [path.name, st.st_size, st.st_mtime_ns]


def _photos_fp(d: Path) -> list[list]:
    """照片集廉价指纹：(相对路径, 字节数, mtime_ns) 的排序列表。

    诚实局限：**不读文件内容**。同名 + 同字节数 + 同 mtime 的不同内容照片不会被
    发现（例如外部工具原地改图后回写 mtime）。对几百张照片全量 sha256 要读几百 MB，
    这是刻意的工程折中 —— 它挡的是"换了一批照片却复用旧位姿"，不是恶意篡改。
    """
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in FINGERPRINT_SUFFIXES:
            st = p.stat()
            out.append([p.relative_to(d).as_posix(), st.st_size, st.st_mtime_ns])
    return out


def _photos_sha256(d: Path) -> list[list[str]]:
    """照片集**字节级**指纹：(相对路径, SHA-256) 的排序列表。

    与 _photos_fp 的区别：读文件内容算 SHA-256，所以同名同大小同 mtime 的**不同
    内容**照片也能被发现。用于 --precomputed-colmap 路径——那里要求精确字节绑定
    (源 sparse/0 必须与产生它的那批照片字节一致)，廉价指纹不足以证明这一点。

    正常 --resume 路径仍用 _photos_fp：那里要挡的是"换了一批照片"，廉价指纹够用，
    且对几百张照片不必每次 hash 几百 MB。precomputed 路径是信任关键点，值得这个成本。
    """
    out: list[list[str]] = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix.lower() in FINGERPRINT_SUFFIXES:
            out.append([p.relative_to(d).as_posix(), _sha256_file(p)])
    return out


def _fingerprint(stage: str, payload: dict) -> tuple[str, str | None]:
    """算阶段指纹 → (digest, 不可证明的原因 或 None)。

    结构性 fail-closed 门：载荷里任何**空清单**都意味着我们**一个输入证据都没观察到**，
    这样的指纹在原理上无法证明"输入未变" —— 两批彻底不同的照片会得到同一个空清单，
    于是同一个 digest。所以空清单 → 永不可跳过。

    这道门是按"形状"挡的，不是按扩展名挡的，所以它挡的是**整类** bug 而不只是某一次
    的清单落差：将来谁再改 FINGERPRINT_SUFFIXES、或 rglob 因权限/符号链接漏掉文件，
    洞也开不出来。误判方向也是安全的 —— 真有个合法的空清单，后果只是多重跑一次。
    """
    empty = sorted(k for k, v in payload.items() if isinstance(v, list) and not v)
    reason = None if not empty else (
        f"指纹里 {'、'.join(empty)} 是空清单（一个输入证据都没观察到，"
        f"证明不了输入未变）")
    return _digest({"stage": stage, **payload}), reason


class StageState:
    """阶段指纹状态 (ws/.stage_state.json) —— --resume 的本地可审计状态。

    **诚实边界（REVIEW-CODEX-030 P7a-7）**：这是一份工作目录里的普通 JSON 文件，
    任何能写该目录的进程都能改它——它**不是** immutable、也**不是**
    tamper-evident（防篡改）。它只是 --resume 在本机本工作目录上的"上次跑到哪、
    指纹是什么"的备忘，让重跑不必从零开始。**生产 acceptance 不写在这里**：真正的
    验收凭证应放在独立的、content-addressed 的 verifier report（见
    `handoff/FEEDBACK-HANDOFF-GLM-007-*` 系列与 P7a-2 的 source manifest 物化）。

    fail-closed：只有 (开了 --resume) + (指纹逐字节相同) + (产物齐全) 三者同时成立
    才跳过。指纹不同 / 无记录 / 状态文件损坏 / 产物缺失 → 重跑，并打印为什么。
    "输出文件存在"本身**从来不是**跳过的理由。

    下游连坐：阶段指纹里含上游指纹，上游输入一变下游指纹跟着变。但仅靠链式还不够
    —— COLMAP mapper 本身不确定，同样输入重跑也未必产出同样位姿。所以任一阶段真的
    要跑之前，先把它和所有下游的记录抹掉**并落盘**：这样中途 Ctrl-C / 崩溃也不会
    留下"下游已完成"的假记录。

    另有一道结构性门（见 _fingerprint）：证明不了"输入未变"的指纹永不可跳过。
    """

    def __init__(self, path: Path, *, resume: bool):
        self.path = path
        self.resume = resume
        self.stages: dict[str, dict] = {}
        self.note = ""
        self._unprovable: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self.note = "无阶段状态文件（首次跑此工作目录）→ 全部阶段跑"
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("顶层不是对象")
            if data.get("version") != STATE_VERSION:
                raise ValueError(f"版本 {data.get('version')!r} != {STATE_VERSION}")
            stages = data.get("stages")
            if not isinstance(stages, dict):
                raise ValueError("缺 stages 或 stages 不是对象")
            for name, ent in stages.items():
                if not isinstance(ent, dict) or not isinstance(ent.get("fingerprint"), str):
                    raise ValueError(f"阶段 {name!r} 记录不完整")
            self.stages = stages
        except (OSError, ValueError, TypeError) as e:
            # fail-closed：读不懂就当什么都没做过，绝不猜测哪些阶段还有效。
            self.stages = {}
            self.note = f"阶段状态文件损坏/不可读（{e}）→ 全部阶段重跑"

    def begin(self, stage: str, fingerprint: str, *, outputs_ok: bool, outputs_desc: str,
              unprovable: str | None = None) -> bool:
        """返回 True = 本阶段必须跑（并已抹掉本阶段+下游记录）。False = 可安全跳过。

        unprovable 非 None = 这个指纹证明不了"输入未变" → 无条件重跑（见 _fingerprint）。
        """
        self._unprovable[stage] = unprovable or ""
        if not self._can_skip(stage, fingerprint, outputs_ok=outputs_ok,
                              outputs_desc=outputs_desc):
            self._invalidate_from(stage)
            return True
        return False

    def _can_skip(self, stage: str, fingerprint: str, *,
                  outputs_ok: bool, outputs_desc: str) -> bool:
        if not self.resume:
            return False
        # 结构性门放在最前：指纹自己都证明不了输入未变时，拿它去比对毫无意义。
        if self._unprovable.get(stage):
            print(f"    重跑 {stage} 阶段：{self._unprovable[stage]}")
            return False
        ent = self.stages.get(stage)
        if ent is None:
            print(f"    重跑 {stage} 阶段：没有它的已完成记录")
            return False
        if ent["fingerprint"] != fingerprint:
            print(f"    重跑 {stage} 阶段：指纹变了（输入或参数已改）"
                  f"{ent['fingerprint'][:12]}… → {fingerprint[:12]}…")
            return False
        if not outputs_ok:
            print(f"    重跑 {stage} 阶段：记录说做完了，但产物不齐（{outputs_desc}）")
            return False
        print(f"    跳过 {stage} 阶段（输入未变，指纹 {fingerprint[:12]}…，"
              f"完成于 {ent.get('finished_at', '?')}）")
        return True

    def _invalidate_from(self, stage: str) -> None:
        """抹掉本阶段及所有下游的记录并立刻落盘（崩溃也不留假记录）。"""
        for s in STAGE_ORDER[STAGE_ORDER.index(stage):]:
            self.stages.pop(s, None)
        self._save()

    def record(self, stage: str, fingerprint: str, *,
               extras: dict[str, object] | None = None) -> None:
        """记录阶段完成。fingerprint 是跑**前**算的；extras 是跑**后**测的
        (log SHA / PLY SHA / argv / returncode / UTC 起止)——后者不参与跳过
        判定（_can_skip 只比对 fingerprint），只为审计留下不可篡改的运行证据。
        """
        entry: dict[str, object] = {
            "fingerprint": fingerprint,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if extras:
            # 拷一份避免外部改字典影响状态文件；值必须是 JSON 可序列化的。
            entry.update(extras)
        self.stages[stage] = entry
        # 状态文件是 --resume 的本地可审计状态（非 tamper-evident）：证明不了输入的
        # 指纹要当场标出来，别让它看起来像个能用的指纹（也让用户看得懂为什么
        # --resume 老是重跑这一阶段）。
        if self._unprovable.get(stage):
            self.stages[stage]["unprovable"] = self._unprovable[stage]
        self._save()

    def _save(self) -> None:
        payload = {"version": STATE_VERSION,
                   "fingerprint_caveat": FINGERPRINT_CAVEAT,
                   "stages": {s: self.stages[s] for s in STAGE_ORDER if s in self.stages}}
        # newline="\n": 状态文件是 --resume 的本地可审计状态（非 tamper-evident），
        # LF 让字节跨平台可复现。
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8", newline="\n")


def _tee(cmd: list[str], log: Path) -> int:
    """跑子进程：输出**全量**写日志，**节流**回显终端。返回 returncode。

    诚实边界：这是逐行透传，**不是进度百分比** —— 我们不解析 COLMAP/Brush 的输出
    语义，只证明"它还在动"，让用户能区分卡死和在跑。COLMAP 用 \\r 原地刷新进度行、
    Brush 输出量大，所以终端每 TEE_INTERVAL_S 最多刷一行；tty 上用 \\r 覆盖同一行
    （不滚屏），代价是终端只留最新一行 —— 完整输出在日志里。

    日志写的是子进程**原始字节**，与不开 tee 时的 fd 重定向逐字节一致（第三方二进制
    在 Windows 上未必输出 UTF-8，这里不做转码猜测）；只有回显到终端时才按 UTF-8
    宽松解码，解不出的字节显示成替代符。
    """
    tty = sys.stdout.isatty()
    width = max(20, shutil.get_terminal_size((100, 24)).columns - 1)
    last, overwrote, buf = 0.0, False, b""
    with log.open("ab") as fh:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # read1: 有多少读多少, 不等凑满缓冲 —— 否则低产出阶段的进度会卡住不显示。
        for chunk in iter(lambda: proc.stdout.read1(65536), b""):
            fh.write(chunk)
            buf += chunk
            *lines, buf = re.split(rb"\r\n|\r|\n", buf)  # \r 刷新的进度行也算一行
            now = time.monotonic()
            if not lines or now - last < TEE_INTERVAL_S:
                continue
            latest = next((ln for ln in reversed(lines) if ln.strip()), None)
            if latest is None:
                continue
            last = now
            s = latest.decode("utf-8", errors="replace").strip()
            if tty:
                print("\r" + s[:width].ljust(width), end="", flush=True)
                overwrote = True
            else:
                print(f"    {s}", flush=True)
        proc.stdout.close()
        rc = proc.wait()
    if overwrote:
        print()
    return rc


def run(cmd: list[str], *, log: Path | None = None, tee: bool = False) -> None:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    if log is None:
        rc = subprocess.run(cmd).returncode
    elif tee:
        rc = _tee(cmd, log)
    else:
        with log.open("a", encoding="utf-8") as fh:
            rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log else ""
        raise SystemExit(f"命令失败 (exit {rc})\n{tail}")


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
    ap.add_argument("--chunk-size-m", type=float, default=None, metavar="METERS",
                    help=("额外产出可流式空间分块 (XY 网格边长米数)：大场景重建让 viewer "
                          "只载相机附近的块才漫游得动；本次重建的信任判定自动随分块产物走。"
                          "缺省不分块"))
    ap.add_argument("--web", type=Path, default=ROOT / "web" / "data" / "recon",
                    help="viewer 数据输出 (默认 web/data/recon)")
    ap.add_argument("--resume", action="store_true",
                    help=("跳过已完成且输入未变的阶段 (Brush 挂了不用重做几小时 COLMAP)。"
                          "只在阶段指纹逐字节相同才复用；指纹变了/记录缺失/产物不齐 → "
                          "重跑该阶段及其所有下游。指纹局限见 --resume 启动时的提示"))
    ap.add_argument("--precomputed-colmap", type=Path, default=None,
                    metavar="COLMAP_WS",
                    help=("使用**已算好的** COLMAP 稀疏模型，跳过 COLMAP 执行。"
                          "<COLMAP_WS> 须含 sparse/0/{cameras,images,points3D}.bin "
                          "和 images/。--photos 必须与 <COLMAP_WS>/images/ 同源。"
                          "fail-closed：源字节变化或拷贝失配 → 重拷（不重跑 COLMAP）；"
                          "缺必需文件 → 拒。本模式仍输出 preview-only (sfm-local)，"
                          "不提升任何信任。"))
    args = ap.parse_args(argv)
    # REVIEW-CODEX-030 P1 (P6c)：caller_argv 必须绑定真实传入的 argv（测试/库调用
    # 传 argv=… 时 sys.argv 是 pytest/IDE 的，不是 reconstruct_local 的），这样
    # matcher 子命令等身份可从 extras 直接验证，无需从日志文本推断。
    raw_argv = list(argv) if argv is not None else list(sys.argv)
    # --resume 是 flow-control（决定是否复用），不是 consumption intent（决定如何
    # 消费源）。剥掉它，否则加 --resume 会改变 caller_argv → 改变 fingerprint /
    # source manifest SHA → 触发重拷（P7a-2/P7a-3 回归：幂等重跑应产生同一 manifest）。
    caller_argv: list[str] = [a for a in raw_argv if a != "--resume"]

    # REVIEW-CODEX-030 P7a-5：在任何 rmtree/mkdir 前拒绝路径重叠——否则
    # rmtree(ws/images) 会删掉源照片/sparse。
    _assert_no_overlap({
        "--work": args.work,
        "--photos": args.photos,
        "--precomputed-colmap": args.precomputed_colmap,
    })

    if args.precomputed_colmap is not None and not args.precomputed_colmap.is_dir():
        raise SystemExit(
            f"--precomputed-colmap: 不是目录: {args.precomputed_colmap}")

    colmap = _find("colmap", ROOT / "third/colmap/bin/colmap.exe",
                   ROOT / "third/colmap/colmap.exe")
    brush = _find("brush_app", ROOT / "third/brush/brush_app.exe")
    py = sys.executable
    ws = args.work
    ws.mkdir(parents=True, exist_ok=True)

    # 状态**总是**写（这样第一次跑挂了，下次 --resume 才有东西可复用）；但只有给了
    # --resume 才会去读它跳过阶段 —— 不给 --resume 时所有阶段照跑，行为不变。
    state = StageState(ws / STATE_FILENAME, resume=args.resume)
    if args.resume:
        print(f"--resume: {FINGERPRINT_CAVEAT}")
        if state.note:
            print(f"    {state.note}")

    # 输入可以是图片目录, 或单个视频文件 (自动抽帧)。
    from pipeline.ingest import is_video
    photos = args.photos
    ordered = args.sequential  # 视频帧时序连续 -> sequential_matcher
    parent = ""  # 阶段指纹链: 每级都含上游指纹, 上游输入一变下游指纹跟着变
    if photos.is_file() and is_video(photos):
        print(f"\n=== 0/4 视频抽帧 (fps={args.fps}, 上限 {args.max_frames}) ===")
        frames = ws / "frames"
        parent, unprovable = _fingerprint("frames", {"video": _file_fp(photos),
                                                     "fps": args.fps,
                                                     "max_frames": args.max_frames})
        if state.begin("frames", parent, unprovable=unprovable,
                       outputs_ok=frames.is_dir() and any(frames.iterdir()),
                       outputs_desc=f"{frames} 不存在或为空"):
            vin = ws / "video_in"
            vin.mkdir(parents=True, exist_ok=True)
            shutil.copy2(photos, vin / photos.name)
            run([py, "-m", "pipeline.ingest", "--input", str(vin), "--output", str(frames),
                 "--fps", str(args.fps), "--max-frames", str(args.max_frames)])
            state.record("frames", parent)
        photos = frames
        ordered = True  # 抽帧 frame_000000.jpg… 字典序即时序
    elif not photos.is_dir() or not any(photos.iterdir()):
        raise SystemExit(f"输入需为非空图片目录或视频文件: {photos}")
    args.photos = photos  # 后续步骤统一用抽帧后的目录
    db = ws / "colmap.db"
    sparse = ws / "sparse"
    clog = ws / "colmap.log"
    images_dir = ws / "images"

    print("\n=== 1/4 COLMAP 位姿 (CPU) —— 图多会较慢 ===")
    grp = _colmap_group(colmap)
    gpu = "1" if args.colmap_gpu else "0"
    # COLMAP 数据集布局: Brush 要 <root>/images/ + <root>/sparse/0/
    # 时序连续帧(视频/--sequential): 只配相邻帧, CPU 上远快于 O(n²) 全配对;
    # 无序照片: ≤400 用 exhaustive, 更多退化到 sequential(仍需按拍摄顺序命名)。
    photos_fp = _photos_fp(args.photos)
    # n 与指纹刻意用同一个集合, 但理由不同, 不是为了整齐: n 要的是"目录里有多少张候选
    # 照片"—— 这个问句用共享清单回答就是**字面属实**的, 不需要猜。想让 n 精确等于
    # "COLMAP 实际读得了几张"反而要去猜某个 build 的 FreeImage 带不带 HEIF/WebP 解码,
    # 那是不可机器验证的假设。宁可不猜: 数多了的后果有界(见下面注册率那段)。
    n = len(photos_fp)
    if args.precomputed_colmap is not None:
        # --precomputed-colmap：跳过 COLMAP 执行，用源字节绑定 colmap 阶段指纹。
        # REVIEW-CODEX-030 P0：之前 P7 在 .stage_state.json 写了 'p7_reused_from_p5b'
        # 这样的伪造字符串，生产 caller --resume 重算自己的 digest 后必失败 → COLMAP
        # 重跑。本分支只让 StageState 写它**自己**算出的、源字节真实的 digest；
        # 唯一的"重跑"路径是把源字节重新拷进 ws（绝不执行 COLMAP）。
        print(f"    模式: precomputed (跳过 COLMAP; 源 = {args.precomputed_colmap})")
        manifest = _build_precomputed_manifest(args.precomputed_colmap, args.photos)
        # REVIEW-CODEX-030 P7a-3：caller_argv + 二进制 SHA-256 纳入指纹。
        # caller_argv 绑定"哪个命令消费了源"——换 flag（如 --sequential）即视为
        # 不同的消费意图，触发重拷（便宜）。binary 用 SHA-256 而非 (name/size/mtime)：
        # 同名同大小同 mtime 的不同 build 也要被发现。
        colmap_bin_sha = _sha256_file(Path(colmap))
        # REVIEW-CODEX-030 P7a-2：物化 content-addressed source manifest 报告。
        # digest 只是 sha256 字符串，reviewer 无法从它恢复原始 payload。本调用把
        # 完整源清单（所有 SHA + caller_argv + binary SHA + provenance）写成独立
        # JSON 文件，文件名按 payload 自身 SHA-256 命名。在 state.begin 之外调用：
        # source manifest 描述的是**源**，与本次是否拷贝无关（--resume 跳过拷贝时
        # 也要物化，让 reviewer 能独立复核源清单）。
        source_manifest_sha = _materialize_source_manifest(
            ws, manifest, caller_argv, colmap_bin_sha)
        parent, unprovable = _fingerprint("colmap", {
            "parent": parent,
            **manifest,
            "caller_argv": caller_argv,
            "binary_sha256": colmap_bin_sha,
        })
        outputs_ok = _validate_ws_precomputed(ws, manifest)
        if state.begin("colmap", parent, unprovable=unprovable,
                       outputs_ok=outputs_ok,
                       outputs_desc=f"{ws}/sparse/0 与源字节不一致或缺失"):
            _copy_precomputed_to_ws(args.precomputed_colmap, ws)
            # 拷贝后**立即**二次校验：磁盘满 / 拷贝中断 / 权限回写 → fail-closed，
            # 不让 Brush 训在残缺的 sparse/0 上。
            if not _validate_ws_precomputed(ws, manifest):
                raise SystemExit(
                    f"--precomputed-colmap: 拷贝到 {ws}/sparse/0 后字节校验失败 "
                    f"(源 {args.precomputed_colmap} 与工作目录不一致；检查磁盘空间/权限)")
            # REVIEW-CODEX-030 P7a-6：ws 端语义校验——字节 SHA 通过后，再校验
            # images.bin/cameras.bin 的语义完整性。拷贝本身不改字节，所以这层
            # 主要挡的是源端语义错误但源端被绕过（例如源 manifest 是旧版本算的、
            # 或源 sparse/0 被外部替换）和拷贝中途的位翻转（SHA 碰巧没变但语义
            # 已坏——理论极小概率，但 fail-closed 不留窗口）。
            _validate_sparse_semantics(ws / "sparse" / "0", args.photos)
            best_n = _count_registered_images(ws / "sparse" / "0")
            print(f"    已拷贝预计算 COLMAP: 注册 {best_n}/{n} 张 "
                  f"(来自 {args.precomputed_colmap})")
            # 记录 post-copy 实测 SHA：fingerprint 绑的是源 SHA，extras 绑的是工作
            # 目录拷贝后的实测 SHA。两者应一致——不一致就在上面 raise 了；记下来
            # 是为了让审计能直接读 state 文件确认 ws 字节 == 源字节。
            ws_sparse_0 = ws / "sparse" / "0"
            colmap_extras: dict[str, object] = {
                "caller_argv": caller_argv,
                "colmap_binary_sha256": colmap_bin_sha,
                "source_manifest_sha256": source_manifest_sha,
                "precomputed_source_root": str(args.precomputed_colmap.resolve()),
                "precomputed_post_copy_validated": True,
                "precomputed_ws_cameras_bin_sha256":
                    _sha256_file(ws_sparse_0 / "cameras.bin"),
                "precomputed_ws_images_bin_sha256":
                    _sha256_file(ws_sparse_0 / "images.bin"),
                "precomputed_ws_points3D_bin_sha256":
                    _sha256_file(ws_sparse_0 / "points3D.bin"),
            }
            state.record("colmap", parent, extras=colmap_extras)
        else:
            best_n = _count_registered_images(ws / "sparse" / "0")
            print(f"    复用预计算 COLMAP: 注册 {best_n}/{n} 张 "
                  f"(工作目录字节与源一致)")
    else:
        matcher = "sequential_matcher" if (ordered or n > 400) else "exhaustive_matcher"
        parent, unprovable = _fingerprint("colmap", {
            "parent": parent, "photos": photos_fp, "matcher": matcher, "gpu": gpu,
            "group": grp, "camera_model": "SIMPLE_RADIAL", "binary": _file_fp(Path(colmap))})
        # 产物齐全 = db + images/ + sparse/0 里真有已注册影像（空模型不可信 → 重跑）。
        model_ok = (db.is_file() and images_dir.is_dir()
                    and _count_registered_images(sparse / "0") > 0)
        if state.begin("colmap", parent, unprovable=unprovable, outputs_ok=model_ok,
                       outputs_desc="缺 colmap.db / images/ / sparse/0 中的有效模型"):
            clog.write_text("", encoding="utf-8")
            print(f"    匹配器: {matcher} ({'时序连续' if ordered else '无序'}, {n} 图)")
            # REVIEW-CODEX-030 P1 (P6c)：绑定真实 COLMAP subprocess argv +
            # matcher 子命令 + UTC + log SHA，不再靠日志文本推断 matcher 身份。
            colmap_start = datetime.now(UTC)
            feature_argv = [colmap, "feature_extractor", "--database_path", str(db),
                            "--image_path", str(args.photos), "--ImageReader.camera_model",
                            "SIMPLE_RADIAL", f"--{grp}Extraction.use_gpu", gpu]
            run(feature_argv, log=clog, tee=True)
            matcher_argv = [colmap, matcher, "--database_path", str(db),
                            f"--{grp}Matching.use_gpu", gpu]
            run(matcher_argv, log=clog, tee=True)
            sparse.mkdir(exist_ok=True)
            mapper_argv = [colmap, "mapper", "--database_path", str(db),
                           "--image_path", str(args.photos), "--output_path", str(sparse)]
            run(mapper_argv, log=clog, tee=True)
            colmap_end = datetime.now(UTC)
            best_n, n_models = _select_best_colmap_model(sparse)
            frac = best_n / n if n else 0.0
            split = "" if n_models == 1 else f"，COLMAP 分裂成 {n_models} 个子模型(用最大的)"
            print(f"    COLMAP 注册 {best_n}/{n} 张 ({frac:.0%}){split}")
            if frac < 0.6:
                print(f"    ⚠ 注册率偏低 ({frac:.0%})：重叠不足会导致大量空洞/漂浮。"
                      "建议加拍过渡角度、放慢绕拍、避开纯无纹理/反光面。")
                # 分母数的是"候选照片", 不是"COLMAP 解得开的照片"。别让用户拿着一个其实是
                # 格式问题的低注册率跑去重拍 —— 但也别反过来断言 COLMAP 读不了什么, 那要看
                # 该 build 的 FreeImage, 跑之前无法验证。只陈述事实, 让用户自己判。
                exotic = sorted({Path(f[0]).suffix.lower() for f in photos_fp}
                                - {".jpg", ".jpeg", ".png"})
                if exotic:
                    print(f"    ⚠ 也可能不是重叠问题：分母里含 {'、'.join(exotic)}，"
                          "COLMAP 解不解得开取决于该 build 的 FreeImage 带哪些格式"
                          "（跑之前没法验证）。若这些图一张都没进模型，先转成 JPEG 再试。")
            # 重开 images/：它必须是产出 sparse/0 的那一批照片, 留旧副本会让 Brush 训在
            # 旧图上, 出一个谎称来自这批照片的重建。
            if images_dir.exists():
                shutil.rmtree(images_dir)
            shutil.copytree(args.photos, images_dir)
            colmap_extras = {
                "caller_argv": caller_argv,
                "colmap_started_at": colmap_start.isoformat(timespec="seconds"),
                "colmap_finished_at": colmap_end.isoformat(timespec="seconds"),
                "colmap_returncode": 0,  # run() 在非 0 时已 SystemExit
                "colmap_log_sha256": (_sha256_file(clog)
                                      if clog.is_file() else None),
                "colmap_matcher_subcommand": matcher,
                "colmap_feature_extractor_argv": feature_argv,
                "colmap_matcher_argv": matcher_argv,
                "colmap_mapper_argv": mapper_argv,
                "colmap_registered_images": best_n,
                "colmap_submodel_count": n_models,
                "colmap_binary_sha256": _sha256_file(Path(colmap)),
                "colmap_images_input_count": n,
            }
            state.record("colmap", parent, extras=colmap_extras)
        else:
            reuse_n = _count_registered_images(sparse / "0")
            print(f"    复用已有位姿: sparse/0 注册 {reuse_n}/{n} 张")

    print(f"\n=== 2/4 Brush 训练 3DGS ({args.steps} 步, max-res {args.max_res}) ===")
    trained = ws / "trained.ply"
    brush_log = ws / "brush.log"
    brush_argv = [brush, str(ws), "--total-steps", str(args.steps),
                  "--max-resolution", str(args.max_res),
                  "--export-every", str(args.steps),
                  "--export-path", str(ws), "--export-name", "trained.ply"]
    parent, unprovable = _fingerprint("brush", {
        "parent": parent, "steps": args.steps, "max_res": args.max_res,
        "binary": _file_fp(Path(brush))})
    if state.begin("brush", parent, unprovable=unprovable,
                   outputs_ok=trained.is_file() or any(
                       p.name != "trained.brush-export.ply"
                       for p in ws.glob("*.ply")),
                   outputs_desc="工作目录里没有 .ply"):
        # REVIEW-CODEX-030 P0 #5：post-run 绑定完整 argv + UTC 起止 + returncode
        # + log SHA + PLY SHA。run() 在 rc!=0 时抛 SystemExit，所以走到 record
        # 时 rc 必为 0；不抛这一支的 rc 用 None 表示（理论上不可达，写出来防漂移）。
        brush_start = datetime.now(UTC)
        run(brush_argv, log=brush_log, tee=True)
        brush_end = datetime.now(UTC)
        export_now = trained if trained.is_file() else next(ws.glob("*.ply"), None)
        # Brush 导出后立即快照：prepare 阶段 normalize_ply_quats.py 会 in-place
        # 覆盖 trained.ply，所以 Brush extras 的 SHA 必须绑定不可变快照
        # （trained.brush-export.ply），审计者才能重新算 SHA 验证。
        brush_export_snapshot = ws / "trained.brush-export.ply"
        if export_now and export_now.is_file():
            shutil.copy2(export_now, brush_export_snapshot)
        brush_extras = {
            "brush_argv": brush_argv,
            "caller_argv": caller_argv,
            "brush_started_at": brush_start.isoformat(timespec="seconds"),
            "brush_finished_at": brush_end.isoformat(timespec="seconds"),
            "brush_returncode": 0,  # run() 在非 0 时已 SystemExit
            "brush_log_sha256": (_sha256_file(brush_log)
                                 if brush_log.is_file() else None),
            "brush_export_ply_path": brush_export_snapshot.name,
            "brush_export_ply_sha256": (_sha256_file(brush_export_snapshot)
                                        if brush_export_snapshot.is_file() else None),
            "brush_export_ply_size_bytes": (brush_export_snapshot.stat().st_size
                                            if brush_export_snapshot.is_file() else None),
            # 注：trained.ply 本身会被 prepare 阶段 normalize_ply_quats.py 覆盖；
            # 它的 post-prepare SHA 由 prepare extras 绑定，这里只绑不可变快照。
        }
        state.record("brush", parent, extras=brush_extras)
    export = trained if trained.is_file() else next(ws.glob("*.ply"), None)
    if export is None:
        raise SystemExit("Brush 未导出 .ply：见 brush.log（可能显存不足，调小 --max-res）")

    print("\n=== 3/4 归一化四元数 + 生成导入契约 ===")
    reg, splat = ws / "registration.json", ws / "splat-input.json"
    parent, unprovable = _fingerprint("prepare", {"parent": parent, "export": export.name})
    if state.begin("prepare", parent, unprovable=unprovable,
                   outputs_ok=reg.is_file() and splat.is_file(),
                   outputs_desc="缺 registration.json / splat-input.json"):
        run([py, str(ROOT / "scripts/normalize_ply_quats.py"), str(export)])
        run([py, str(ROOT / "scripts/prepare_import.py"), str(export),
             "--out-dir", str(ws)])
        state.record("prepare", parent)

    print("\n=== 4/4 导入 → viewer 数据 ===")
    import_cmd = [py, "-m", "pipeline.reconstruct", "--engine", "import",
                  "--registration", str(reg), "--splat", str(splat),
                  "--out", str(ws / "out"), "--web", str(args.web),
                  "--dedup-voxel", "0", "--replace-margin", "0",
                  "--photos", str(args.photos)]
    if args.chunk_size_m is not None:
        import_cmd += ["--chunk-size-m", str(args.chunk_size_m)]
    parent, unprovable = _fingerprint("import", {"parent": parent, "cmd": import_cmd[1:]})
    if state.begin("import", parent, unprovable=unprovable,
                   outputs_ok=(ws / "out").exists() and args.web.exists(),
                   outputs_desc=f"缺 {ws / 'out'} / {args.web}"):
        run(import_cmd)
        state.record("import", parent)

    print(f"\n[OK] 本机重建完成 → {args.web}")
    print("查看 360° 漫游:  python make.py serve   # http://127.0.0.1:8000/web/studio/")
    print("结果为 preview-only(非米制)；要真实尺度见 docs/real-data-workflow.md。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
