#!/usr/bin/env python3
"""Run the two safe actions exposed by an extracted Production runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
PRIVATE_OVERRIDE_NAMES = frozenset(
    {
        "ACCEPTANCE_ROOT",
        "ARCHIVE",
        "PRIVACY_POLICY",
        "PRIVACY_REPORT",
        "REAL_SCENE_IMPORT_ROOT",
        "RELEASE_DIR",
        "VERSION",
    }
)
ENV = {
    key: value
    for key, value in os.environ.items()
    if key not in PRIVATE_OVERRIDE_NAMES
}
ENV.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})


def _run(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=ENV,
        check=False,
    )
    return completed.returncode


def verify() -> int:
    return _run(
        [
            PYTHON,
            "scripts/verify_production_release.py",
            ".",
            "--json",
        ]
    )


def serve() -> int:
    return _run(
        [
            PYTHON,
            "-m",
            "pipeline.studio_server",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
    )


TARGETS: dict[str, Callable[[], int]] = {
    "verify": verify,
    "serve": serve,
}


def _print_help() -> None:
    print("Nantai 3D Production runtime")
    print("  python make.py help")
    print("  python make.py verify")
    print("  python make.py serve")


def main(argv: list[str]) -> int:
    arguments = argv[1:]
    if arguments == ["help"]:
        _print_help()
        return 0
    if len(arguments) != 1 or arguments[0] not in TARGETS:
        print(
            "expected exactly one target: help, verify, or serve",
            file=sys.stderr,
        )
        return 2
    return TARGETS[arguments[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
