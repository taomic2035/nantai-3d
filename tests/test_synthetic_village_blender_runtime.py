from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLENDER = ROOT / "third" / "blender" / "blender.exe"
BUILDER = ROOT / "scripts" / "blender" / "build_synthetic_village.py"


pytestmark = pytest.mark.skipif(
    not BLENDER.is_file(),
    reason="locked private Blender runtime is not installed",
)

RUN_END_TO_END = os.environ.get("NANTAI_RUN_BLENDER_RUNTIME_TESTS") == "1"


def _run_builder(
    *runtime_args: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(BUILDER),
            "--",
            *runtime_args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def test_runtime_rejects_missing_request_with_stable_error(tmp_path: Path) -> None:
    result = _run_builder(
        "--request",
        str(tmp_path / "missing.json"),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request file does not exist" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_relative_argv_before_path_resolution(tmp_path: Path) -> None:
    result = _run_builder(
        "--request",
        "relative-request.json",
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request and staging paths must be absolute" in (
        result.stdout + result.stderr
    )
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_duplicate_request_keys_before_creating_staging(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_bytes(b'{"schema_version":"first","schema_version":"second"}\n')

    result = _run_builder(
        "--request",
        str(request_path),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request contains duplicate JSON key: schema_version" in (
        result.stdout + result.stderr
    )
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_redirected_request_leaf_before_reading(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}\n")
    redirected = tmp_path / "redirected.json"
    try:
        os.symlink(target, redirected)
    except OSError as exc:
        pytest.skip(f"file symlink creation is unavailable: {exc}")

    result = _run_builder(
        "--request",
        str(redirected),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request path is redirected" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_junction_request_parent_before_reading(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    (real_parent / "request.json").write_bytes(b"{}\n")
    junction = tmp_path / "redirected-parent"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_parent)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {created.stdout}{created.stderr}")
    try:
        result = _run_builder(
            "--request",
            str(junction / "request.json"),
            "--staging",
            str(tmp_path / "staging"),
        )
    finally:
        os.rmdir(junction)

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request path is redirected" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


@pytest.mark.skipif(
    not RUN_END_TO_END,
    reason="set NANTAI_RUN_BLENDER_RUNTIME_TESTS=1 for the real Blender build",
)
def test_runtime_builds_and_reports_the_complete_canary(tmp_path: Path) -> None:
    from pipeline.synthetic_village.camera_plan import build_camera_plan
    from pipeline.synthetic_village.canary import (
        build_canary_request,
        canonical_build_request_bytes,
    )
    from pipeline.synthetic_village.scene_plan import build_scene_plan

    scene = build_scene_plan()
    camera = build_camera_plan(scene)
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=scene,
        camera_plan=camera,
        visual_pack_root=(ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"),
    )
    request_path = tmp_path / "request.json"
    request_path.write_bytes(canonical_build_request_bytes(request))
    staging = tmp_path / "staging"

    result = _run_builder(
        "--request",
        str(request_path),
        "--staging",
        str(staging),
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    expected = {
        "build-report.json",
        "preview-bridge.png",
        "preview-central.png",
        "preview-outer.png",
        "preview-upper.png",
        "village-canary.blend",
        "village-canary.glb",
    }
    assert {path.name for path in staging.iterdir()} == expected
    report = json.loads((staging / "build-report.json").read_text("utf-8"))
    assert report["build_id"] == request.build_id
    assert report["fidelity"] == "simplified-pbr-not-render-parity"
    assert report["counts"]["canonical_roots"] == 126
    assert report["counts"]["visual_materials"] == 24
    assert report["counts"]["cameras"] == 24
    assert report["counts"]["auxiliary_semantic_objects"] == 2
    assert report["validation"]["finite_nonempty_meshes"] is True
    assert report["validation"]["all_visual_material_slots_built"] is True
    assert report["validation"]["canary_critical_slots_fulfilled"] is True
    assert report["validation"]["prop_type_counts"] == {
        "bamboo-basket": 2,
        "farming-tools": 2,
        "firewood-stack": 2,
        "grain-rack": 2,
        "handcart": 2,
        "stone-trough": 2,
        "water-jar": 2,
        "wooden-bench": 2,
    }
    assert all(
        path.stat().st_size > 0 for path in staging.iterdir() if path.name != "build-report.json"
    )
