from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/releases/1.0-preview.2.md"
ARCHIVE = "nantai-3d-v1.0.0-preview.2-runtime.zip"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_preview2_is_the_current_documented_package_version() -> None:
    project = tomllib.loads(_read("pyproject.toml"))["project"]
    readme = _read("README.md")
    docs_index = _read("docs/README.md")
    preview1 = _read("docs/releases/1.0-preview.md")

    assert project["version"] == "1.0.0rc2"
    assert "docs/releases/1.0-preview.2.md" in readme
    assert "releases/1.0-preview.2.md" in docs_index
    assert "Preview1（历史版本）" in readme
    assert "历史版本" in preview1
    assert "1.0-preview.2.md" in preview1


def test_preview2_guide_is_an_exact_clean_room_runbook() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert ARCHIVE in guide
    assert f"{ARCHIVE}.sha256" in guide
    for command in (
        "Get-FileHash",
        "Expand-Archive",
        r".\.venv\Scripts\python.exe scripts\verify_preview_release.py .",
        "shasum -a 256",
        "unzip",
        ".venv/bin/python scripts/verify_preview_release.py .",
        "python make.py serve",
    ):
        assert command in guide
    assert "http://127.0.0.1:8000/web/studio/" in guide
    assert "查看高斯 / 点云" in guide
    assert "查看整村模型" in guide
    assert guide.index(r"python scripts\verify_preview_release.py .") < guide.index(
        "python -m venv .venv"
    )
    assert guide.index("python3 scripts/verify_preview_release.py .") < guide.index(
        "python3 -m venv .venv"
    )


def test_preview2_guide_keeps_package_integrity_separate_from_scene_trust() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    for limit in (
        "synthetic",
        "preview-only",
        "arbitrary",
        "unaligned",
        "无照片纹理",
        "没有完成真实照片重建",
        "trust_effect=none",
    ):
        assert limit in guide
    assert "Batch35 不在本包内" in guide
    assert "私有 PBR bundle 不在本包内" in guide
