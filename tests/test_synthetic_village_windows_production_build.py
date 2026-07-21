"""Windows production builds are verified independently from Mac L0 previews."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.local_production_runner import (
    DEFAULT_WINDOWS_PRODUCTION_RENDER_ROOT,
)
from pipeline.synthetic_village.local_textured_preview import LocalBlenderIdentity
from pipeline.synthetic_village.windows_production_build import (
    WindowsProductionBuildError,
    verify_windows_production_build,
)

ARTIFACTS = (
    ("preview-bridge.png", "rgb-preview"),
    ("preview-central.png", "rgb-preview"),
    ("preview-outer.png", "rgb-preview"),
    ("preview-upper.png", "rgb-preview"),
    ("village-canary.blend", "blender-scene"),
    ("village-canary.glb", "gltf-binary"),
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, SimpleNamespace, SimpleNamespace]:
    repo_root = tmp_path / "repo"
    private_root = repo_root / ".nantai-studio"
    private_root.mkdir(parents=True)
    executable = repo_root / "third/blender/blender.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"pinned-blender")

    artifact_records = []
    payloads = {}
    for name, kind in ARTIFACTS:
        payload = f"verified:{name}".encode()
        payloads[name] = payload
        artifact_records.append(
            SimpleNamespace(
                name=name,
                kind=kind,
                sha256=_sha256(payload),
                size_bytes=len(payload),
            ),
        )

    build_id = "a" * 64
    directory = private_root / "work/canary" / build_id
    directory.mkdir(parents=True)
    for name, _kind in ARTIFACTS:
        (directory / name).write_bytes(payloads[name])
    report_bytes = b'{"canonical":"v2-report"}\n'
    (directory / "build-report.json").write_bytes(report_bytes)

    tool_identity = SimpleNamespace(
        platform="windows-x64",
        executable_sha256=_sha256(b"pinned-blender"),
    )
    source_hashes = SimpleNamespace(scene_plan_sha256="b" * 64)
    report = SimpleNamespace(
        schema_version=canary.TEXTURED_BUILD_REPORT_SCHEMA,
        build_id=build_id,
        verification_level="L2",
        synthetic=True,
        geometry_usability="preview-only",
        tool_identity=tool_identity,
        source_hashes=source_hashes,
        object_registry=("object-registry",),
        auxiliary_registry=("auxiliary-registry",),
        semantic_registry=("semantic-registry",),
        artifacts=tuple(artifact_records),
    )
    request = SimpleNamespace(
        build_id=build_id,
        tool_identity=tool_identity,
    )
    monkeypatch.setattr(
        canary,
        "build_textured_canary_request",
        lambda **_kwargs: request,
    )
    monkeypatch.setattr(
        canary,
        "load_textured_build_report",
        lambda _path: report,
    )
    verified_calls = []
    monkeypatch.setattr(
        canary,
        "verify_textured_build_report",
        lambda loaded, *, request, staging: verified_calls.append(
            (loaded, request, staging),
        ),
    )
    request.verified_calls = verified_calls
    return directory, executable, report, request


def test_verified_windows_build_binds_directory_report_blend_and_registries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, executable, report, request = _fixture(tmp_path, monkeypatch)

    verified = verify_windows_production_build(
        directory=directory,
        material_bundle_root=tmp_path / "materials",
        repo_root=tmp_path / "repo",
        executable=executable,
        surface_realism_profile_id="source-consistent-multiscale-surface-v1",
    )

    report_path = directory / "build-report.json"
    blend_path = directory / "village-canary.blend"
    assert verified.adapter == "windows-textured-v2"
    assert verified.directory == directory.absolute()
    assert verified.build_id == report.build_id == directory.name
    assert verified.build_report_sha256 == _sha256(report_path.read_bytes())
    assert verified.blend_sha256 == _sha256(blend_path.read_bytes())
    assert verified.blend_size_bytes == blend_path.stat().st_size
    assert verified.blender_executable_sha256 == _sha256(executable.read_bytes())
    assert verified.verification_level == "L2"
    assert verified.synthetic is True
    assert verified.geometry_usability == "preview-only"
    assert verified.object_registry == report.object_registry
    assert verified.auxiliary_registry == report.auxiliary_registry
    assert verified.semantic_registry == report.semantic_registry
    assert request.verified_calls == [(report, request, directory.absolute())]


def test_windows_build_requires_exact_directory_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, executable, _report, _request = _fixture(tmp_path, monkeypatch)
    renamed = directory.with_name("b" * 64)
    directory.rename(renamed)

    with pytest.raises(
        WindowsProductionBuildError,
        match="directory name",
    ):
        verify_windows_production_build(
            directory=renamed,
            material_bundle_root=tmp_path / "materials",
            repo_root=tmp_path / "repo",
            executable=executable,
            surface_realism_profile_id="source-consistent-multiscale-surface-v1",
        )


def test_windows_build_rejects_unregistered_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, executable, _report, _request = _fixture(tmp_path, monkeypatch)
    (directory / "unregistered.tmp").write_bytes(b"not evidence")

    with pytest.raises(
        WindowsProductionBuildError,
        match="exact seven-file set",
    ):
        verify_windows_production_build(
            directory=directory,
            material_bundle_root=tmp_path / "materials",
            repo_root=tmp_path / "repo",
            executable=executable,
            surface_realism_profile_id="source-consistent-multiscale-surface-v1",
        )


def test_windows_build_rejects_tampered_blend_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, executable, _report, _request = _fixture(tmp_path, monkeypatch)
    (directory / "village-canary.blend").write_bytes(b"tampered")

    with pytest.raises(
        WindowsProductionBuildError,
        match="artifact digest or size",
    ):
        verify_windows_production_build(
            directory=directory,
            material_bundle_root=tmp_path / "materials",
            repo_root=tmp_path / "repo",
            executable=executable,
            surface_realism_profile_id="source-consistent-multiscale-surface-v1",
        )


def test_windows_build_rejects_wrong_or_redirected_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, executable, report, _request = _fixture(tmp_path, monkeypatch)
    report.tool_identity.executable_sha256 = "c" * 64

    with pytest.raises(
        WindowsProductionBuildError,
        match="executable digest",
    ):
        verify_windows_production_build(
            directory=directory,
            material_bundle_root=tmp_path / "materials",
            repo_root=tmp_path / "repo",
            executable=executable,
            surface_realism_profile_id="source-consistent-multiscale-surface-v1",
        )


def test_windows_adapter_does_not_relax_mac_preview_platform() -> None:
    platform_annotation = LocalBlenderIdentity.model_fields["platform"].annotation

    assert getattr(platform_annotation, "__args__", ()) == ("macos-arm64",)


def test_windows_production_cli_selects_v2_build_explicitly() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/synthetic_village.py",
            "render-production-windows",
            "--help",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--verified-v2-build" in completed.stdout
    assert "--surface-realism-profile" in completed.stdout
    assert "--post-render-policy" in completed.stdout
    assert "--local-preview-build" not in completed.stdout


def test_windows_default_render_root_leaves_room_for_atomic_report_path() -> None:
    longest_atomic_report = (
        DEFAULT_WINDOWS_PRODUCTION_RENDER_ROOT
        / ("a" * 64)
        / ("b" * 64)
        / f".preflight-report.json.tmp-{'c' * 12}"
    )

    assert len(str(longest_atomic_report)) <= 240
