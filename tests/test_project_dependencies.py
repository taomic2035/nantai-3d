from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_canary_depth_reader_dependency_is_declared() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    assert any(item.lower().startswith("openexr>=") for item in dependencies)


def test_real_golden_path_runtime_dependencies_are_declared() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = tuple(item.lower() for item in project["project"]["dependencies"])

    for required in (
        "pillow>=",
        "opencv-python-headless>=",
        "scikit-image>=",
        "pydantic>=",
        "numpy>=",
        "plyfile>=",
    ):
        assert any(item.startswith(required) for item in dependencies), required
