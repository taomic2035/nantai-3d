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

与 Makefile 保持等价的 target 名称；Makefile 仍保留给有 make 的 POSIX 环境。
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


def ingest() -> None:
    run([PY, "-m", "pipeline.ingest", "--input", "input", "--output", "photos"])


def reconstruct() -> None:
    run([PY, "-m", "pipeline.reconstruct", "--photos", "photos"])


def world() -> None:
    run([PY, "-m", "pipeline.generate_world", "--size", "5", "--seed", "42"])


def assets() -> None:
    run([PY, f"{ASSET_DELIVERABLE}/scripts/generate.py", "--output", ASSET_DELIVERABLE])
    run([PY, "-m", "pipeline.validate_handoff", ASSET_DELIVERABLE,
         "--feedback-dir", "handoff", "--register", "--assets-dir", "assets"])


def validate_handoff() -> None:
    deliv = os.environ.get("DELIV", ASSET_DELIVERABLE)
    run([PY, "-m", "pipeline.validate_handoff", deliv])


def serve() -> None:
    run([PY, "-m", "pipeline.studio_server", "--host", "127.0.0.1", "--port", "8000"])


def verify() -> None:
    test()
    assets()
    world()
    run([PY, "-m", "json.tool", "docs/contracts/studio-adapter-v2.schema.json"],
        )
    run([PY, "-m", "json.tool", "web/data/manifest.json"])
    run([PY, "verification/verify_3dtiles_conversion.py"])
    run([PY, "verification/verify_glm_layout.py"])


def clean() -> None:
    for name in ("corpus", "layouts", "scenes", "recon", "web/data/recon",
                 "verification/output"):
        target = ROOT / name
        if target.exists():
            print(f"rm -rf {name}")
            shutil.rmtree(target, ignore_errors=True)


TARGETS = {
    "setup": setup, "test": test, "lint": lint, "ingest": ingest,
    "reconstruct": reconstruct, "world": world, "assets": assets,
    "validate-handoff": validate_handoff, "serve": serve, "verify": verify,
    "clean": clean,
}


def help_() -> None:
    print(__doc__)
    print("targets:")
    for name in TARGETS:
        print(f"  {name}")


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args or args[0] in ("help", "-h", "--help"):
        help_()
        return 0
    for name in args:
        fn = TARGETS.get(name)
        if fn is None:
            print(f"unknown target: {name!r} (try: python make.py help)", file=sys.stderr)
            return 2
        fn()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
