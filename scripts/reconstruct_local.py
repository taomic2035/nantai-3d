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
    """阶段指纹状态 (ws/.stage_state.json) —— --resume 的信任根。

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

    def record(self, stage: str, fingerprint: str) -> None:
        self.stages[stage] = {
            "fingerprint": fingerprint,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        # 状态文件是 --resume 的信任根：证明不了输入的指纹要当场标出来，别让它看起来
        # 像个能用的指纹（也让用户看得懂为什么 --resume 老是重跑这一阶段）。
        if self._unprovable.get(stage):
            self.stages[stage]["unprovable"] = self._unprovable[stage]
        self._save()

    def _save(self) -> None:
        payload = {"version": STATE_VERSION,
                   "fingerprint_caveat": FINGERPRINT_CAVEAT,
                   "stages": {s: self.stages[s] for s in STAGE_ORDER if s in self.stages}}
        # newline="\n": 状态文件是 --resume 的信任根, LF 让字节跨平台可复现。
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
    args = ap.parse_args(argv)

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
        run([colmap, "feature_extractor", "--database_path", str(db),
             "--image_path", str(args.photos), "--ImageReader.camera_model",
             "SIMPLE_RADIAL", f"--{grp}Extraction.use_gpu", gpu], log=clog, tee=True)
        run([colmap, matcher, "--database_path", str(db),
             f"--{grp}Matching.use_gpu", gpu], log=clog, tee=True)
        sparse.mkdir(exist_ok=True)
        run([colmap, "mapper", "--database_path", str(db),
             "--image_path", str(args.photos), "--output_path", str(sparse)],
            log=clog, tee=True)
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
        state.record("colmap", parent)
    else:
        print(f"    复用已有位姿: sparse/0 注册 {_count_registered_images(sparse / '0')}/{n} 张")

    print(f"\n=== 2/4 Brush 训练 3DGS ({args.steps} 步, max-res {args.max_res}) ===")
    trained = ws / "trained.ply"
    parent, unprovable = _fingerprint("brush", {
        "parent": parent, "steps": args.steps, "max_res": args.max_res,
        "binary": _file_fp(Path(brush))})
    if state.begin("brush", parent, unprovable=unprovable,
                   outputs_ok=trained.is_file() or next(ws.glob("*.ply"), None) is not None,
                   outputs_desc="工作目录里没有 .ply"):
        run([brush, str(ws), "--total-steps", str(args.steps),
             "--max-resolution", str(args.max_res), "--export-every", str(args.steps),
             "--export-path", str(ws), "--export-name", "trained.ply"],
            log=ws / "brush.log", tee=True)
        state.record("brush", parent)
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
