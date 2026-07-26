#!/usr/bin/env python3
"""跨平台任务运行器 —— 在没有 GNU make 的机器上（尤其 Windows）复现 Makefile 的门禁。

用法:
    python make.py <target> [<target> ...]
    python make.py help

设计要点:
- 用运行本脚本的解释器 (sys.executable) 作为 PY，天然指向当前 venv，无需 PY= 传参。
- 强制子进程 UTF-8 (PYTHONUTF8/PYTHONIOENCODING)，规避 Windows cp936/cp1252 下
  CJK/emoji 输出在管道或 CI 中触发 UnicodeEncodeError。
- node --test 的 glob 在 Python 内展开后再传给 node，不依赖 POSIX shell 的通配。
- clean 用 shutil.rmtree 取代 `rm -rf`。

带参数的 target 经环境变量传参（对应 Makefile 的 `make <target> VAR=...`）：
    PHOTOS=<照片目录>        check-capture（默认 photos）
    MANIFEST=<manifest 路径>  inspect-recon / verify-recon-artifacts
                            （默认 web/data/recon/recon_manifest.json）
    DELIV=<交付目录>         validate-handoff（默认 HANDOFF-002）
    DIST=<发布目录>           build-preview 输出目录
    ARCHIVE=<ZIP 路径>        build-preview / verify-preview 的精确归档路径

与 Makefile 保持等价的 target 名称；Makefile 仍保留给有 make 的 POSIX 环境。
例外：doctor / check-capture / inspect-recon / verify-recon-artifacts 目前只有本脚本有，
Makefile 尚未补。
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
# HANDOFF-002 is the cross-platform-reproducible (quantized) asset baseline;
# HANDOFF-001 stays as history (its bytes are not reproducible off macOS).
ASSET_DELIVERABLE = "handoff/deliverables/HANDOFF-002"
PREVIEW_ARCHIVE_NAME = "nantai-3d-v1.0.0-preview.2-runtime.zip"
PREVIEW_DIST = ".nantai-studio/releases/v1.0.0-preview.2"
REAL_CANARY_SOURCE = "config/real-scene/nerfstudio-poster.json"
REAL_SCENE_TARGETS = frozenset(
    {
        "fetch",
        "sfm",
        "train-preview",
        "train-production",
        "import",
        "accept",
        "serve",
        "all",
    }
)
REAL_OPTION_FLAGS = {
    "RUN_ID": "--run-id",
    "WORKSPACE": "--workspace",
    "MEDIA_ROOT": "--media-root",
    "RIGHTS": "--rights",
    "POLICY": "--policy",
    "CONTROL_POINTS": "--control-points",
    "GEO_ORIGIN": "--geo-origin",
    "REMOTE_CONFIG": "--remote-config",
    "VIEWER_POLICY": "--viewer-policy",
    "VIEWER_REPORT": "--viewer-report",
    "HUMAN_REVIEW_POLICY": "--human-review-policy",
    "HUMAN_VISUAL_REVIEW": "--human-visual-review",
    "CHUNK_SIZE": "--chunk-size",
}
REAL_BOOLEAN_OPTIONS = frozenset({"RESUME", "RETRY"})

# UTF-8-safe environment for every child process.
ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    """Run a command, echoing it; raise SystemExit(code) on failure."""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(cwd or ROOT), env=ENV)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def node_test(pattern: str) -> None:
    files = sorted(glob.glob(str(ROOT / pattern)))
    if not files:
        raise SystemExit(f"no node test files matched: {pattern}")
    run(["node", "--test", *files])


# ============ targets ============
def setup() -> None:
    run([PY, "-m", "pip", "install", "-e", ".[dev]"])


def test() -> None:
    run([PY, "-m", "pytest", "tests/", "-q"])
    node_test("web/viewer/*.test.mjs")
    node_test("web/studio/*.test.mjs")


def lint() -> None:
    run([PY, "-m", "ruff", "check", "pipeline", "tests"])


def doctor() -> None:
    # 退出码恒为 0（缺 COLMAP/GPU 是体检的结论，不是体检的失败），故不会中断 target 串。
    run([PY, "scripts/doctor.py"])


def ingest() -> None:
    run([PY, "-m", "pipeline.ingest", "--input", "input", "--output", "photos"])


def check_capture() -> None:
    photos = os.environ.get("PHOTOS", "photos")
    run([PY, "scripts/check_capture.py", photos])


def reconstruct() -> None:
    run([PY, "-m", "pipeline.reconstruct", "--photos", "photos"])


def inspect_recon() -> None:
    manifest = os.environ.get("MANIFEST", "web/data/recon/recon_manifest.json")
    run([PY, "scripts/inspect_recon.py", manifest])


def verify_recon_artifacts() -> None:
    # 退出码 2 = 发现任何 mismatch / 路径安全 / chunks 异常 / 矛盾 (可当 CI 门用);
    # 与 inspect_recon 一致。MANIFEST 默认指向同一份官方合成 manifest。
    manifest = os.environ.get("MANIFEST", "web/data/recon/recon_manifest.json")
    run([PY, "scripts/verify_recon_artifacts.py", manifest])


def world() -> None:
    run([PY, "-m", "pipeline.generate_world", "--size", "5", "--seed", "42"])


def assets() -> None:
    run([PY, f"{ASSET_DELIVERABLE}/scripts/generate.py", "--output", ASSET_DELIVERABLE])
    run(
        [
            PY,
            "-m",
            "pipeline.validate_handoff",
            ASSET_DELIVERABLE,
            "--feedback-dir",
            "handoff",
            "--register",
            "--assets-dir",
            "assets",
        ]
    )


def validate_handoff() -> None:
    deliv = os.environ.get("DELIV", ASSET_DELIVERABLE)
    run([PY, "-m", "pipeline.validate_handoff", deliv])


def serve() -> None:
    run([PY, "-m", "pipeline.studio_server", "--host", "127.0.0.1", "--port", "8000"])


def _preview_archive() -> str:
    explicit = os.environ.get("ARCHIVE")
    if explicit:
        return Path(explicit).as_posix()
    dist = os.environ.get("DIST", PREVIEW_DIST)
    return (Path(dist) / PREVIEW_ARCHIVE_NAME).as_posix()


def build_preview() -> None:
    run([PY, "scripts/build_preview_release.py", "--output", _preview_archive()])


def verify_preview() -> None:
    run([PY, "scripts/verify_preview_release.py", _preview_archive()])


def verify() -> None:
    test()
    assets()
    world()
    run(
        [PY, "-m", "json.tool", "docs/contracts/studio-adapter-v2.schema.json"],
    )
    run([PY, "-m", "json.tool", "web/data/manifest.json"])
    run([PY, "verification/verify_3dtiles_conversion.py"])
    run([PY, "verification/verify_glm_layout.py"])


def clean() -> None:
    for name in ("corpus", "layouts", "scenes", "recon", "web/data/recon", "verification/output"):
        target = ROOT / name
        if target.exists():
            print(f"rm -rf {name}")
            shutil.rmtree(target, ignore_errors=True)


def _real_boolean(name: str, value: str) -> bool:
    normalized = value.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of 1/0, true/false, yes/no, on/off")


def _real_scene_command(mode: str, tokens: list[str]) -> list[str]:
    options: dict[str, str] = {}
    subtargets: list[str] = []
    allowed = {
        "SOURCE",
        *REAL_OPTION_FLAGS,
        *REAL_BOOLEAN_OPTIONS,
    }
    for token in tokens:
        if "=" not in token:
            subtargets.append(token)
            continue
        name, value = token.split("=", 1)
        if name not in allowed:
            raise ValueError(f"unknown {mode} option: {name}")
        if name in options:
            raise ValueError(f"duplicate {mode} option: {name}")
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError(f"{mode} option {name} is empty or unsafe")
        options[name] = value
    if len(subtargets) != 1 or subtargets[0] not in REAL_SCENE_TARGETS:
        raise ValueError(f"{mode} requires exactly one known real-scene subtarget")
    if mode == "real-canary":
        forbidden = {
            "SOURCE",
            "MEDIA_ROOT",
            "RIGHTS",
            "POLICY",
            "CONTROL_POINTS",
            "GEO_ORIGIN",
        }
        supplied = sorted(forbidden & options.keys())
        if supplied:
            raise ValueError(f"real-canary forbids overrides: {', '.join(supplied)}")
        source = REAL_CANARY_SOURCE
    else:
        source = options.pop("SOURCE", None)
        if source is None:
            raise ValueError("real-scene requires exactly one SOURCE=")
    if _real_boolean("RESUME", options.get("RESUME", "0")) and _real_boolean(
        "RETRY", options.get("RETRY", "0")
    ):
        raise ValueError("real-scene RESUME and RETRY are mutually exclusive")

    command = [
        PY,
        "-m",
        "scripts.real_scene",
        subtargets[0],
        "--source",
        source,
    ]
    for name, flag in REAL_OPTION_FLAGS.items():
        if name in options:
            command.extend((flag, options[name]))
    for name, flag in (("RESUME", "--resume"), ("RETRY", "--retry")):
        if name in options and _real_boolean(name, options[name]):
            command.append(flag)
    return command


def real_scene(mode: str, tokens: list[str]) -> None:
    run(_real_scene_command(mode, tokens))


TARGETS = {
    "setup": setup,
    "test": test,
    "lint": lint,
    "doctor": doctor,
    "ingest": ingest,
    "check-capture": check_capture,
    "reconstruct": reconstruct,
    "inspect-recon": inspect_recon,
    "verify-recon-artifacts": verify_recon_artifacts,
    "world": world,
    "assets": assets,
    "validate-handoff": validate_handoff,
    "serve": serve,
    "build-preview": build_preview,
    "verify-preview": verify_preview,
    "verify": verify,
    "clean": clean,
}


def help_() -> None:
    print(__doc__)
    print("targets:")
    for name in TARGETS:
        print(f"  {name}")
    print("  real-canary <KEY=VALUE...> <subtarget>")
    print("  real-scene SOURCE=<source.json> <KEY=VALUE...> <subtarget>")


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args or args[0] in ("help", "-h", "--help"):
        help_()
        return 0
    if args[0] in {"real-canary", "real-scene"}:
        try:
            real_scene(args[0], args[1:])
        except ValueError as exc:
            print(f"{args[0]}: {exc}", file=sys.stderr)
            return 2
        return 0
    if any(name in {"real-canary", "real-scene"} for name in args):
        print(
            "real-scene targets cannot be mixed with ordinary targets",
            file=sys.stderr,
        )
        return 2
    for name in args:
        fn = TARGETS.get(name)
        if fn is None:
            print(f"unknown target: {name!r} (try: python make.py help)", file=sys.stderr)
            return 2
        fn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
