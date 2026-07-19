"""Source and real-Blender gates for the H2 v1/v2 contact sheet."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import struct
import subprocess
from pathlib import Path

import pytest

from pipeline.synthetic_village.mesh_asset_build import EXPECTED_ASSET_IDS

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts/blender/render_mesh_asset_comparison.py"
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
PRIVATE_ROOT = ROOT / ".nantai-studio/synthetic-village/hybrid-v3"
V1_BUNDLE = (
    PRIVATE_ROOT
    / "mesh-asset-bundles"
    / "2fbf8692ca8b1442c72177dc1954fb81959933bafd46623c1817002fc732c3e8"
)
V2_BUNDLE = (
    PRIVATE_ROOT
    / "mesh-asset-bundles"
    / "866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6"
)
REPORT_KEYS = {
    "asset_ids",
    "camera_matrix",
    "image_bytes",
    "image_sha256",
    "pairs",
    "schema_version",
    "synthetic",
    "trust_effect",
    "v1_bundle_id",
    "v2_bundle_id",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_size(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()[:24]
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    return struct.unpack(">II", raw[16:24])


def _run_renderer(output: Path, report: Path) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [
            str(BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(RENDERER),
            "--",
            "--v1-bundle",
            str(V1_BUNDLE),
            "--v2-bundle",
            str(V2_BUNDLE),
            "--output",
            str(output),
            "--report",
            str(report),
        ],
        check=False,
        shell=False,
        cwd=ROOT,
        env=environment,
        timeout=900,
        capture_output=True,
        text=True,
    )


def test_comparison_renderer_source_declares_fail_closed_visual_contract() -> None:
    source = RENDERER.read_text(encoding="utf-8")
    for token in (
        "nantai.synthetic-village.mesh-near-comparison.v1",
        "none-visual-review-only",
        "EXPECTED_ASSET_IDS",
        "BLENDER_EEVEE_NEXT",
        "3840",
        "2160",
        "camera_matrix",
        "image_sha256",
        "bpy.ops.import_scene.gltf",
        "_canonicalize_png_chunks",
        "use_taa_reprojection = False",
        "_verify_snapshots_unchanged",
        "--v1-bundle",
        "--v2-bundle",
        "--output",
        "--report",
    ):
        assert token in source

    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "random" not in imported
    assert "time" not in imported


def test_real_comparison_render_is_complete_and_repeatable(tmp_path: Path) -> None:
    if os.environ.get("NANTAI_RUN_REAL_MESH_COMPARISON") != "1":
        pytest.skip(
            "set NANTAI_RUN_REAL_MESH_COMPARISON=1 for the real contact-sheet gate",
        )
    missing = [
        path
        for path in (BLENDER, V1_BUNDLE, V2_BUNDLE)
        if not path.exists()
    ]
    if missing:
        pytest.skip(f"real comparison inputs are absent: {missing}")

    first_image = tmp_path / "first.png"
    first_report_path = tmp_path / "first.json"
    first_process = _run_renderer(first_image, first_report_path)
    assert first_process.returncode == 0, (
        first_process.stdout,
        first_process.stderr,
    )
    first_raw = first_report_path.read_bytes()
    first = json.loads(first_raw)

    assert first_raw == (
        json.dumps(first, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    assert set(first) == REPORT_KEYS
    assert first["schema_version"] == (
        "nantai.synthetic-village.mesh-near-comparison.v1"
    )
    assert first["v1_bundle_id"] == V1_BUNDLE.name
    assert first["v2_bundle_id"] == V2_BUNDLE.name
    assert tuple(first["asset_ids"]) == EXPECTED_ASSET_IDS
    assert len(first["camera_matrix"]) == 16
    assert all(math.isfinite(value) for value in first["camera_matrix"])
    assert re.fullmatch(r"[0-9a-f]{64}", first["image_sha256"])
    assert first["image_sha256"] == _sha256(first_image)
    assert first["image_bytes"] == first_image.stat().st_size
    assert first["image_bytes"] > 0
    assert first["synthetic"] is True
    assert first["trust_effect"] == "none-visual-review-only"
    assert _png_size(first_image) == (3840, 2160)

    assert [row["asset_id"] for row in first["pairs"]] == list(
        EXPECTED_ASSET_IDS,
    )
    for row in first["pairs"]:
        assert set(row) == {
            "asset_id",
            "v1_glb_sha256",
            "v1_triangle_count",
            "v2_glb_sha256",
            "v2_triangle_count",
        }
        assert re.fullmatch(r"[0-9a-f]{64}", row["v1_glb_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["v2_glb_sha256"])
        assert row["v1_triangle_count"] > 0
        assert row["v2_triangle_count"] > row["v1_triangle_count"]

    second_image = tmp_path / "second.png"
    second_report_path = tmp_path / "second.json"
    second_process = _run_renderer(second_image, second_report_path)
    assert second_process.returncode == 0, (
        second_process.stdout,
        second_process.stderr,
    )
    second = json.loads(second_report_path.read_bytes())
    assert second == first
    assert second_image.read_bytes() == first_image.read_bytes()


def test_real_comparison_rejects_redirected_bundle(tmp_path: Path) -> None:
    if os.environ.get("NANTAI_RUN_REAL_MESH_COMPARISON") != "1":
        pytest.skip(
            "set NANTAI_RUN_REAL_MESH_COMPARISON=1 for the real contact-sheet gate",
        )
    redirected = tmp_path / V1_BUNDLE.name
    redirected.symlink_to(V1_BUNDLE, target_is_directory=True)
    process = subprocess.run(
        [
            str(BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(RENDERER),
            "--",
            "--v1-bundle",
            str(redirected),
            "--v2-bundle",
            str(V2_BUNDLE),
            "--output",
            str(tmp_path / "redirected.png"),
            "--report",
            str(tmp_path / "redirected.json"),
        ],
        check=False,
        shell=False,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=60,
        capture_output=True,
        text=True,
    )
    assert process.returncode != 0
    assert "redirected" in (process.stdout + process.stderr).lower()
    assert not (tmp_path / "redirected.png").exists()
    assert not (tmp_path / "redirected.json").exists()
