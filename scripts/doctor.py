#!/usr/bin/env python3
"""环境体检: 实测这台机器能跑真实重建的哪几步, 哪几步必须走外部/云。

手册 §1/§2 手工记着"机器现实"(有无 COLMAP / 有无 Brush / 有无 CUDA), 人得手工核对。
本命令**实测**同样的事实并诚实报告: 能做什么、不能做什么、缺什么怎么补。

诚实性约束 (本文件的存在理由, 改代码时别破坏):
- 探测不到 → status=unknown 并说明为什么探不到; 绝不因为"大概率没有"就报 missing。
- 探不到版本/SIFT 选项组 → 留 None。注意 pipeline.registration._colmap_sift_group
  探测失败时兜底返回 'Feature'(为了让重建能跑下去) —— 体检是报告不是兜底, 这里不许兜。
- 能力小结里, 探不准的步骤进 unclear, 不进 can/cannot —— 不替用户下结论。
- 默认不校验素材 sha (哈希全部 PLY 很慢); 没校验就写明"未校验", 不假装校验过。

退出码语义: **总是 0** (体检自身崩溃时才非 0)。
    退出码报的是"体检跑成没跑成", 不是"这台机器合不合格"。"缺 COLMAP" 是体检的
    **结论**(而且是本机的正常状态), 不是体检的失败 —— 让它非 0 会逼 CI/脚本把一份
    正常报告当故障处理。要按结论决策请读 --json 的 checks[*].status。

用法:
    python scripts/doctor.py                    # 中文人类报告
    python scripts/doctor.py --json             # 机器可读
    python scripts/doctor.py --verify-assets    # 额外实测校验素材 sha (慢: 哈希全部 PLY)
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

OK = "ok"
MISSING = "missing"
DEGRADED = "degraded"
UNKNOWN = "unknown"

# 探测子进程上界: 只跑 -h/--version, 正常亚秒级; 超时按"探不到"处理而非按缺失。
PROBE_TIMEOUT_S = 30.0
# 重建产物 (COLMAP database + sparse + 训练输出 PLY) 几百 MB~GB 级, 低于此值提示紧张。
DISK_WARN_GB = 20.0

_COLMAP_CANDIDATES = (
    "third/colmap/bin/colmap.exe",
    "third/colmap/colmap.exe",
    "third/colmap/bin/colmap",
    "third/colmap/colmap",
)
_BRUSH_CANDIDATES = (
    "third/brush/brush_app.exe",
    "third/brush/brush_app",
)

# 模块名 → pip 包名 (二者常不同, remedy 要给能直接粘的包名)
REQUIRED_MODULES = {
    "numpy": "numpy",
    "plyfile": "plyfile",
    "pydantic": "pydantic",
    "loguru": "loguru",
    "PIL": "Pillow",
    "cv2": "opencv-python-headless",
    "exifread": "exifread",
}
OPTIONAL_MODULES = {
    "trimesh": "trimesh",
    "py3dtiles": "py3dtiles",
    "OpenEXR": "OpenEXR",
    "psutil": "psutil",
    "rich": "rich",
}


def _default_run(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    # errors="replace": 探测输出只用来解析版本号; 某个 build 吐了非法字节不该让体检崩。
    return subprocess.run(list(cmd), capture_output=True, text=True,
                          timeout=PROBE_TIMEOUT_S, check=False, errors="replace")


def _default_is_file(path: Path) -> bool:
    return Path(path).is_file()


def _default_disk_free_bytes(path: Path) -> int:
    return shutil.disk_usage(str(path)).free


def _default_open_registry(assets_dir: Path):
    # 延迟导入: pipeline.assets 依赖 pydantic/numpy。体检必须在依赖坏掉时**仍能跑**
    # (那正是最需要它的时候), 所以模块顶层只碰标准库。
    from pipeline.assets import AssetRegistry

    return AssetRegistry(assets_dir)


@dataclass(frozen=True)
class Probes:
    """所有对外部世界的探测集中在此, 便于测试注入 (不依赖本机真装了什么)。"""

    which: Callable[[str], str | None] = field(default=shutil.which)
    is_file: Callable[[Path], bool] = field(default=_default_is_file)
    run: Callable[[Sequence[str]], subprocess.CompletedProcess] = field(default=_default_run)
    import_module: Callable[[str], Any] = field(default=importlib.import_module)
    disk_free_bytes: Callable[[Path], int] = field(default=_default_disk_free_bytes)
    open_registry: Callable[[Path], Any] = field(default=_default_open_registry)


def _default_probes() -> Probes:
    return Probes()


def _find_binary(root: Path, candidates: Sequence[str], path_name: str,
                 probes: Probes) -> tuple[str | None, str | None]:
    """先找 third/ 下的自带二进制, 再退回 PATH; 返回 (路径, 来源)。"""
    for rel in candidates:
        candidate = root / rel
        try:
            if probes.is_file(candidate):
                return str(candidate), "third"
        except OSError:
            continue
    try:
        found = probes.which(path_name)
    except OSError:
        found = None
    return (found, "PATH") if found else (None, None)


def _check_colmap(root: Path, probes: Probes) -> dict:
    path, source = _find_binary(root, _COLMAP_CANDIDATES, "colmap", probes)
    if path is None:
        return {
            "status": MISSING,
            "detail": ("未找到 colmap 可执行文件 (third/colmap/ 下与 PATH 都没有)。"
                       "无 COLMAP 时 pipeline.registration 只能回退 mock 引擎 —— "
                       "那是绕锚点的合成位姿, **非真实重建**, 不可用于交付。"),
            "remedy": ("下载 COLMAP (no-CUDA 版即可) 到 third/colmap/, "
                       "见 third/README.md; 或加入 PATH。"),
            "path": None, "source": None, "version": None, "sift_group": None, "cuda": None,
        }

    try:
        # 一次 -h 同时问出三件事: 版本 / SIFT 选项组命名 / 该 build 有没有 CUDA。
        # banner 走 stderr (实测 COLMAP 4.1.0 如此), 故 stdout+stderr 一起读。
        proc = probes.run([path, "feature_extractor", "-h"])
        text = (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": DEGRADED,
            "detail": (f"找到 colmap ({path}) 但无法执行 `feature_extractor -h`: {exc}。"
                       f"版本 / SIFT 选项组 / CUDA 支持均**未知** —— 不猜; "
                       f"二进制可能损坏、架构不符或被安全软件拦截。"),
            "remedy": f"手工跑一次 `{path} feature_extractor -h` 看真实报错。",
            "path": path, "source": source, "version": None, "sift_group": None, "cuda": None,
        }

    version_match = re.search(r"COLMAP\s+(\d[\w.]*)", text)
    version = version_match.group(1) if version_match else None
    # 与 pipeline.registration._colmap_sift_group 同一判据: 旧版叫 Sift*, 现行叫 Feature*
    if "SiftExtraction.use_gpu" in text and "FeatureExtraction.use_gpu" not in text:
        sift_group = "Sift"
    elif "FeatureExtraction.use_gpu" in text:
        sift_group = "Feature"
    else:
        sift_group = None
    if "without CUDA" in text:
        cuda = False
    elif "with CUDA" in text:
        cuda = True
    else:
        cuda = None

    cuda_note = {
        False: " 该 build 无 CUDA: 稀疏 SfM (本仓库唯一用到的阶段) CPU 可跑; dense/MVS 不可用。",
        True: " 该 build 带 CUDA。",
        None: " 该 build 是否带 CUDA 未知 (banner 没写)。",
    }[cuda]
    detail = (f"找到 colmap ({source}): {path}; 版本 "
              f"{version or '未知 (banner 未匹配到版本号)'}; SIFT 选项组 "
              f"{sift_group or '未知'}。{cuda_note}"
              " CPU SfM 可跑但慢 (手册 §4: 无序 ~300 图 exhaustive 匹配 2-5+ 小时)。")
    return {
        "status": OK, "detail": detail, "path": path, "source": source,
        "version": version, "sift_group": sift_group, "cuda": cuda,
    }


def _check_brush(root: Path, probes: Probes) -> dict:
    path, source = _find_binary(root, _BRUSH_CANDIDATES, "brush_app", probes)
    if path is None:
        return {
            "status": MISSING,
            "detail": ("未找到 brush_app (third/brush/ 下与 PATH 都没有)。"
                       "Brush 是本仓库唯一无需 CUDA 的本机 3DGS 训练器; 没有它, "
                       "训练必须上**云 GPU**。"),
            "remedy": "下载 Brush 到 third/brush/ (见 third/README.md); 或改用云 GPU 训练。",
            "path": None, "source": None, "version": None,
        }
    try:
        proc = probes.run([path, "--version"])
        version = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        version = version[0].strip() if version else None
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": DEGRADED,
            "detail": (f"找到 brush_app ({path}) 但 `--version` 执行失败: {exc}。"
                       f"版本**未知**, 能否训练未经验证。"),
            "remedy": f"手工跑一次 `{path} --version` 看真实报错。",
            "path": path, "source": source, "version": None,
        }
    return {
        "status": OK,
        "detail": (f"找到 brush_app ({source}): {path}; 版本 {version or '未知'}。"
                   f"无 CUDA 也能训练 (wgpu 后端), 但集显上慢且规模受限。"),
        "path": path, "source": source, "version": version,
    }


def _check_gpu(probes: Probes) -> dict:
    no_cuda_remedy = ("训练走云 GPU, 或用本机 Brush (受限)。"
                      "详见 docs/manual/reconstruction-setup.md。")
    try:
        smi = probes.which("nvidia-smi")
    except OSError as exc:
        return {
            "status": UNKNOWN,
            "detail": f"查找 nvidia-smi 时出错: {exc}; 有无 NVIDIA GPU **未知**, 不猜。",
            "remedy": no_cuda_remedy, "name": None,
        }
    if smi is None:
        # nvidia-smi 随 NVIDIA 驱动一起安装。探不到它 → "无可用 CUDA 栈"是从证据推出的
        # 结论, 不是猜测。措辞只声明"未探测到可用 CUDA 栈", 不声称硬件层面绝无 N 卡。
        return {
            "status": MISSING,
            "detail": ("未找到 nvidia-smi (它随 NVIDIA 驱动一起安装) → 判定本机**无可用的 "
                       "NVIDIA CUDA 栈**。因此 gsplat / nerfstudio 跑不了 (二者强依赖 CUDA)。"),
            "remedy": no_cuda_remedy, "name": None,
        }
    try:
        proc = probes.run([smi, "--query-gpu=name,driver_version",
                           "--format=csv,noheader"])
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": UNKNOWN,
            "detail": (f"找到 nvidia-smi ({smi}) 但执行失败: {exc}; "
                       f"有无可用 GPU **未知** —— 既不按「没有」处理, 也不按「有」处理。"),
            "remedy": f"手工跑一次 `{smi}` 看真实报错。", "name": None,
        }
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0 or not output:
        return {
            "status": DEGRADED,
            "detail": (f"nvidia-smi 存在但查询失败 (返回码 {proc.returncode}): "
                       f"{output or '无输出'}。驱动可能装了但没正常工作 —— "
                       f"既不能算有可用 GPU, 也不能断言无 N 卡。"),
            "remedy": "检查/重装 NVIDIA 驱动; 或训练走云 GPU。", "name": None,
        }
    name = output.splitlines()[0].strip()
    return {
        "status": OK,
        "detail": f"检测到 NVIDIA GPU: {name} (nvidia-smi: {smi})。CUDA 训练器本机可用。",
        "name": name,
    }


def _check_python_deps(probes: Probes) -> dict:
    missing_required: list[str] = []
    missing_optional: list[str] = []
    broken: list[str] = []

    for module in list(REQUIRED_MODULES) + list(OPTIONAL_MODULES):
        required = module in REQUIRED_MODULES
        try:
            probes.import_module(module)
        except ModuleNotFoundError:
            # 明确"没装" —— 这是可判定的缺失
            (missing_required if required else missing_optional).append(module)
        except Exception as exc:
            # 装了但导入炸了 (DLL 加载失败 / 循环导入 / ABI 不符)。"坏"不等于"缺",
            # 报 missing 会误导用户去 pip install 一个已经装了的包。
            broken.append(f"{module} ({type(exc).__name__}: {exc})")

    def _pip(modules: list[str]) -> str:
        return " ".join(sorted({(REQUIRED_MODULES | OPTIONAL_MODULES)[m] for m in modules}))

    if broken:
        return {
            "status": UNKNOWN,
            "detail": ("以下包已安装但导入失败, 是否可用**未知** (不等于未安装): "
                       + "; ".join(broken)),
            "remedy": "在 .venv 里手工 `import` 复现真实报错; 多为 ABI/DLL 不匹配, 需重装该包。",
            "missing_required": missing_required, "missing_optional": missing_optional,
            "broken": broken,
        }
    if missing_required:
        return {
            "status": MISSING,
            "detail": ("缺必需 Python 包: " + ", ".join(missing_required)
                       + " —— 管线核心步骤跑不了。"),
            "remedy": f".venv/Scripts/python.exe -m pip install {_pip(missing_required)}",
            "missing_required": missing_required, "missing_optional": missing_optional,
            "broken": broken,
        }
    if missing_optional:
        return {
            "status": DEGRADED,
            "detail": ("必需包齐全; 缺可选包: " + ", ".join(missing_optional)
                       + " —— 核心重建不受影响, 相关旁支功能不可用。"),
            "remedy": f".venv/Scripts/python.exe -m pip install {_pip(missing_optional)}",
            "missing_required": [], "missing_optional": missing_optional, "broken": broken,
        }
    return {
        "status": OK,
        "detail": (f"必需包 ({len(REQUIRED_MODULES)}) 与可选包 "
                   f"({len(OPTIONAL_MODULES)}) 全部可导入。"),
        "missing_required": [], "missing_optional": [], "broken": [],
    }


def _check_assets_registry(root: Path, probes: Probes, verify_assets: bool) -> dict:
    registry_path = root / "assets" / "registry.json"
    try:
        exists = probes.is_file(registry_path)
    except OSError as exc:
        return {"status": UNKNOWN, "detail": f"无法判断 {registry_path} 是否存在: {exc}",
                "count": None, "without_sha": None, "sha_mismatched": None}
    if not exists:
        return {
            "status": MISSING,
            "detail": f"{registry_path} 不存在 —— 尚无已注册素材 (全新仓库属正常)。",
            "remedy": "注册素材后自动生成; 无素材不影响重建本身。",
            "count": 0, "without_sha": None, "sha_mismatched": None,
        }
    try:
        registry = probes.open_registry(root / "assets")
        entries = dict(registry.doc.assets)
    except Exception as exc:
        return {
            "status": DEGRADED,
            "detail": f"registry.json 存在但无法解析: {exc} —— 素材加载会 fail-closed 拒绝。",
            "remedy": "检查 assets/registry.json 是否被手工改坏或截断。",
            "count": None, "without_sha": None, "sha_mismatched": None,
        }

    count = len(entries)
    without_sha = sorted(aid for aid, entry in entries.items()
                         if not getattr(entry, "sha256", ""))
    mismatched: list[str] | None = None
    if verify_assets:
        # verified_sha256 只在实测摘要与登记一致时返回摘要, 否则 None (含文件缺失/未登记 sha)
        mismatched = sorted(aid for aid in entries
                            if registry.verified_sha256(aid) is None)

    if verify_assets:
        sha_note = (f"实测校验: {count - len(mismatched or [])}/{count} 条通过"
                    + (f"; 未通过: {', '.join(mismatched or [])}" if mismatched else ""))
    else:
        sha_note = "sha **未校验** (默认跳过: 需哈希全部 PLY, 慢); 加 --verify-assets 实测校验"

    result: dict[str, Any] = {
        "detail": f"registry.json 可解析, 共 {count} 条素材; {sha_note}。",
        "count": count, "without_sha": without_sha, "sha_mismatched": mismatched,
    }
    if mismatched:
        result["status"] = DEGRADED
        result["remedy"] = "重新注册这些素材 (payload 与登记摘要不符, 加载会被 fail-closed 拒绝)。"
    elif without_sha:
        result["status"] = DEGRADED
        result["detail"] += f" {len(without_sha)} 条未登记 sha: {', '.join(without_sha)}。"
        result["remedy"] = "为这些素材补登 sha256; 缺 sha 的素材会被 fail-closed 拒绝加载。"
    else:
        result["status"] = OK
    return result


def _check_disk(root: Path, probes: Probes) -> dict:
    try:
        free_gb = probes.disk_free_bytes(root) / (1024 ** 3)
    except OSError as exc:
        return {"status": UNKNOWN,
                "detail": f"无法读取 {root} 所在磁盘的可用空间: {exc} —— 不猜。",
                "free_gb": None}
    free_gb = round(free_gb, 1)
    if free_gb < DISK_WARN_GB:
        return {
            "status": DEGRADED,
            "detail": (f"{root} 所在盘可用 {free_gb} GB, 低于建议的 {DISK_WARN_GB} GB。"
                       f"重建产物 (COLMAP database/sparse + 训练 PLY) 常达几百 MB~GB。"),
            "remedy": "清理磁盘, 或把 --work / 输出目录指到空间更足的盘。",
            "free_gb": free_gb,
        }
    return {"status": OK, "detail": f"{root} 所在盘可用 {free_gb} GB, 足够重建产物。",
            "free_gb": free_gb}


def _summarize(checks: dict[str, dict]) -> dict:
    """基于实测结论给能力小结。探不准的一律进 unclear —— 不替用户下结论。"""
    can: list[str] = []
    cannot: list[str] = []
    unclear: list[str] = []

    deps = checks["python_deps"]["status"]
    colmap = checks["colmap"]["status"]
    brush = checks["brush"]["status"]
    gpu = checks["gpu"]["status"]

    def by_deps(step: str) -> None:
        if deps in (OK, DEGRADED):
            can.append(f"{step} —— 本机可跑 (纯 CPU)")
        elif deps == MISSING:
            cannot.append(f"{step} —— 缺必需 Python 包")
        else:
            unclear.append(f"{step} —— Python 依赖状态未知, 无法判定")

    by_deps("ingest 摄取 (照片/视频抽帧/EXIF GPS 读取)")

    sfm = "COLMAP SfM 求相机位姿"
    if colmap == OK:
        can.append(f"{sfm} —— 本机 CPU 可跑但**慢** (手册 §4: 无序 ~300 图 2-5+ 小时)")
    elif colmap == MISSING:
        cannot.append(f"{sfm} —— 未装 COLMAP; 只能回退 mock 引擎 (合成位姿, 非真实重建)")
    else:
        unclear.append(f"{sfm} —— COLMAP 探测不完整 ({colmap}), 无法判定")

    train = "3DGS 训练"
    if gpu == OK:
        can.append(f"{train} —— 检测到 NVIDIA GPU, 本机可跑 (Brush / gsplat / nerfstudio)")
    elif gpu == MISSING and brush == OK:
        can.append(f"{train} —— 无 CUDA, 只能用 Brush; **受限** (wgpu 走集显: 慢、规模小)")
    elif gpu == MISSING and brush == MISSING:
        cannot.append(f"{train} —— 无 CUDA 且无 Brush, 必须上**云 GPU**")
    else:
        unclear.append(f"{train} —— GPU({gpu}) / Brush({brush}) 探测不完整, 无法判定")

    if gpu == MISSING:
        cannot.append("gsplat / nerfstudio 训练 —— 强依赖 CUDA, 本机无 NVIDIA 栈")

    by_deps("导入本仓库 / 米制·地理对齐 / 分块流式 / Spark viewer 漫游")

    return {"can": can, "cannot": cannot, "unclear": unclear}


def diagnose(root: Path | None = None, probes: Probes | None = None,
             verify_assets: bool = False) -> dict:
    """实测本机能力, 返回可 JSON 序列化的报告 (纯函数: 一切外部探测经 probes 注入)。"""
    root = Path(root) if root is not None else ROOT
    probes = probes or _default_probes()
    checks = {
        "colmap": _check_colmap(root, probes),
        "brush": _check_brush(root, probes),
        "gpu": _check_gpu(probes),
        "python_deps": _check_python_deps(probes),
        "assets_registry": _check_assets_registry(root, probes, verify_assets),
        "disk": _check_disk(root, probes),
    }
    return {
        "root": str(root),
        "platform": f"{sys.platform} / python {sys.version.split()[0]}",
        "checks": checks,
        "summary": _summarize(checks),
    }


_LABELS = {
    "colmap": "COLMAP (SfM 求相机位姿)",
    "brush": "Brush (无 CUDA 的 3DGS 训练器)",
    "gpu": "GPU / CUDA",
    "python_deps": "Python 依赖",
    "assets_registry": "素材注册表",
    "disk": "磁盘可用空间",
}
_MARKS = {OK: "[可用]", MISSING: "[缺失]", DEGRADED: "[降级]", UNKNOWN: "[未知]"}


def _render(report: dict) -> str:
    lines = ["=" * 72, "环境体检 —— 这台机器到底能做什么", "=" * 72,
             f"仓库: {report['root']}", f"平台: {report['platform']}", ""]
    for key, check in report["checks"].items():
        lines.append(f"{_MARKS[check['status']]} {_LABELS[key]}")
        lines.append(f"       {check['detail']}")
        if check.get("remedy"):
            lines.append(f"       补救: {check['remedy']}")
        lines.append("")
    summary = report["summary"]
    lines += ["-" * 72, "能力小结 (基于以上实测, 非推测)", "-" * 72]
    for title, items in (("本机能跑", summary["can"]), ("本机不能跑",
                         summary["cannot"]), ("无法判定 (探测不确定, 不猜)", summary["unclear"])):
        if items:
            lines.append(f"{title}:")
            lines += [f"  - {item}" for item in items]
            lines.append("")
    lines.append("退出码恒为 0: 体检报告的是机器状态, 缺件不是本命令的失败。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="环境体检: 实测本机能跑真实重建的哪几步 (退出码恒为 0)")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--verify-assets", action="store_true",
                        help="额外实测校验素材 sha256 (慢: 需哈希全部 PLY)")
    args = parser.parse_args(argv)

    report = diagnose(probes=_default_probes(), verify_assets=args.verify_assets)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
