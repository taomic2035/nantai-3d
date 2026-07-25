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
import re
import shutil
import struct
import subprocess
import sys
import time
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


_COLMAP_MODEL_NUM_PARAMS: dict[int, int] = {
    0: 3,    # SIMPLE_PINHOLE
    1: 4,    # PINHOLE
    2: 4,    # SIMPLE_RADIAL
    3: 5,    # RADIAL
    4: 8,    # OPENCV
    5: 8,    # OPENCV_FISHEYE
    6: 12,   # FULL_OPENCV
    7: 5,    # FOV
    8: 6,    # FULL_FOV
    9: 5,    # SIMPLE_RADIAL_FISHEYE
    10: 6,   # RADIAL_FISHEYE
    11: 12,  # THIN_PRISM_FISHEYE
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
        name = data[pos:end].decode("utf-8", errors="replace")
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


def _validate_sparse_semantics(sparse_0: Path, photos: Path) -> None:
    """校验 precomputed COLMAP sparse/0 的语义完整性（不止前 8 字节 header）。

    REVIEW-CODEX-030 P7a-6：仅读 images.bin 头 8 字节 num_reg_images 不等于一个
    合法的 recovered camera track。一个可信的 sparse model 需要：
    1. images.bin 可完整解析（header count == 实际记录数；否则格式残缺）；
    2. image_name 在 photos/ 目录都能找到对应文件（无 phantom image，否则
       Brush 会训在一批不存在的照片的"位姿"上）；
    3. image_name 无重复（无 ghost track，否则同一张照片被算两次）；
    4. cameras.bin params 全部 finite（无 NaN/Inf，否则位姿不可数值使用）。

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

    # 1. cameras.bin params 全部 finite
    for cam in cameras:
        for j, p in enumerate(cam["params"]):
            if not math.isfinite(p):
                raise SystemExit(
                    f"--precomputed-colmap: cameras.bin camera_id={cam['camera_id']} "
                    f"param[{j}]={p} 非有限（NaN/Inf）→ 位姿不可信")

    # 2. image_name 无重复
    names = [img["name"] for img in images]
    seen: set[str] = set()
    dups: list[str] = []
    for name in names:
        if name in seen and name not in dups:
            dups.append(name)
        seen.add(name)
    if dups:
        raise SystemExit(
            f"--precomputed-colmap: images.bin 有重复 image_name: {dups[:3]}")

    # 3. 每个 image_name 在 photos/ 找得到对应文件
    photo_names: set[str] = set()
    for p in photos.rglob("*"):
        if p.is_file() and p.suffix.lower() in FINGERPRINT_SUFFIXES:
            photo_names.add(p.relative_to(photos).as_posix())
    missing = [name for name in names if name not in photo_names]
    if missing:
        raise SystemExit(
            f"--precomputed-colmap: images.bin 引用了 photos 目录中不存在的图像: "
            f"{missing[:3]}")


def _atomic_replace_dir(src: Path, dst: Path) -> None:
    """原子替换目录：rename dst → dst.old，rename src → dst，rmtree dst.old。

    REVIEW-CODEX-030 P7a-4：中途崩溃留下 dst.old → 下次调用前先清理（见下）。
    Windows rename 要求目标不存在，所以先 rename 旧 dst 到 dst.old。
    """
    if not src.is_dir():
        raise SystemExit(f"原子替换失败：源目录不存在 {src}")
    old = dst.parent / f"{dst.name}.old"
    if old.exists():
        shutil.rmtree(old)
    if dst.exists():
        dst.rename(old)
    src.rename(dst)
    if old.exists():
        shutil.rmtree(old)


def _atomic_replace_file(src: Path, dst: Path) -> None:
    """原子替换文件：rename dst → dst.old，rename src → dst，unlink dst.old。"""
    if not src.is_file():
        raise SystemExit(f"原子替换失败：源文件不存在 {src}")
    old = dst.parent / f"{dst.name}.old"
    if old.exists():
        old.unlink()
    if dst.exists():
        dst.rename(old)
    src.rename(dst)
    if old.exists():
        old.unlink()


def _copy_precomputed_to_ws(colmap_ws: Path, ws: Path) -> None:
    """把源 sparse/0/*.bin + colmap.db + images/ 字节拷贝进 ws（fresh staging + 原子替换）。

    REVIEW-CODEX-030 P7a-4：旧实现直接往 ws/sparse/0 里 copy2，若源删除了某个
    optional 文件（如 frames.bin），ws 里的旧文件会残留且不被 _validate_ws_precomputed
    校验（它只校验 manifest 里有的文件）。本实现：
    1. 拷到 fresh staging 目录 ws/.staging_precomputed/（先清空任何残留 staging/old）；
    2. 只拷源里**实际存在**的 required + optional 文件 + colmap.db + images/；
    3. 原子替换 ws/sparse/0、ws/colmap.db、ws/images —— 旧目录被 rename 到 *.old
       后 rmtree，新内容从 staging rename 进来，保证 ws 里恰好只有源里有的文件。
    中途崩溃留下 .staging_precomputed/ 或 *.old → 下次调用开头清理。
    """
    staging = ws / ".staging_precomputed"
    if staging.exists():
        shutil.rmtree(staging)
    # 也清理任何上次崩溃留下的 *.old
    for old_name in ("sparse/0.old", "colmap.db.old", "images.old"):
        old = ws / old_name
        if old.exists():
            if old.is_dir():
                shutil.rmtree(old)
            else:
                old.unlink()
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
    shutil.copytree(src_img, staging_img)

    # 原子替换三个目标。先确保 ws/sparse 父目录存在。
    (ws / "sparse").mkdir(parents=True, exist_ok=True)
    _atomic_replace_dir(staging_sparse, ws / "sparse" / "0")
    if has_db:
        _atomic_replace_file(staging_db, ws / "colmap.db")
    elif (ws / "colmap.db").exists():
        # 新源没有 colmap.db → 删旧（不能原子，但 staging 已无 db，旧的也没用）
        (ws / "colmap.db").unlink()
    _atomic_replace_dir(staging_img, ws / "images")

    # 清理 staging（成功路径）
    shutil.rmtree(staging)


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
