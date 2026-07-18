from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.canary import TexturedBuildRequest
from pipeline.synthetic_village.local_textured_preview import (
    LocalBlenderIdentity,
    LocalTexturedPreviewRequest,
    build_local_textured_preview_manifest,
    build_local_textured_preview_request,
    canonical_local_textured_preview_request_bytes,
)
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]


def _local_request(tmp_path: Path) -> LocalTexturedPreviewRequest:
    visual_root, bundle = publish_material_fixture(tmp_path)
    identity = LocalBlenderIdentity(
        executable_sha256="1" * 64,
        version="4.5.11",
        platform="macos-arm64",
        runtime_build_hash="4db51e9d1e1e",
        runtime_output_sha256="2" * 64,
    )
    return build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=identity,
    )


def test_local_request_is_content_addressed_but_never_authoritative(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)

    assert request.verification_level == "L0"
    assert request.authoritative is False
    assert request.release_channel == "local-preview-only"
    assert request.tool_identity.platform == "macos-arm64"
    assert request.material_algorithm_id == "edge-feather-sobel-orm-v2"
    assert (
        hashlib.sha256(
            canonical_local_textured_preview_request_bytes(
                request,
                exclude_preview_id=True,
            ),
        ).hexdigest()
        == request.preview_id
    )
    raw = canonical_local_textured_preview_request_bytes(request)
    assert raw.endswith(b"\n")
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw


def test_local_request_cannot_validate_as_authoritative_request(tmp_path: Path) -> None:
    request = _local_request(tmp_path)

    with pytest.raises(ValidationError):
        TexturedBuildRequest.model_validate(request.model_dump())


def test_local_manifest_is_preview_only_and_not_real_photo_texture(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    manifest = build_local_textured_preview_manifest(
        request=request,
        glb_sha256="3" * 64,
        glb_bytes=1024,
        build_report_sha256="4" * 64,
        audit_sha256="5" * 64,
    )

    assert manifest.schema_version == 2
    assert manifest.synthetic is True
    assert manifest.geometry_usability == "preview-only"
    assert manifest.material_fidelity == "synthetic-derived-pbr"
    assert manifest.synthetic_pbr_textures is True
    assert manifest.real_photo_textures is False
    assert manifest.dynamic_mesh_relighting is True
    assert manifest.splat_relighting is False
    assert manifest.authoritative is False
    assert manifest.verification_level == "L0"
    assert manifest.release_channel == "local-preview-only"
    assert manifest.model_url.endswith(
        f"/{request.preview_id}/village-canary.glb",
    )
    assert "local-preview-only" in manifest.limitations


def test_local_models_reject_trust_or_texture_upgrades(tmp_path: Path) -> None:
    request = _local_request(tmp_path)
    request_payload = request.model_dump()
    request_payload["authoritative"] = True
    with pytest.raises(ValidationError):
        LocalTexturedPreviewRequest.model_validate(request_payload)

    manifest = build_local_textured_preview_manifest(
        request=request,
        glb_sha256="3" * 64,
        glb_bytes=1024,
        build_report_sha256="4" * 64,
        audit_sha256="5" * 64,
    )
    for key, value in (
        ("real_photo_textures", True),
        ("geometry_usability", "metric-aligned"),
        ("splat_relighting", True),
        ("authoritative", True),
    ):
        payload = manifest.model_dump()
        payload[key] = value
        with pytest.raises(ValidationError):
            type(manifest).model_validate(payload)


def test_builder_keeps_local_schema_and_authoritative_schema_separate() -> None:
    source = (
        ROOT / "scripts/blender/build_synthetic_village.py"
    ).read_text("utf-8")

    assert "local-textured-preview-request.v1" in source
    assert "local-textured-preview-build-report.v1" in source
    assert 'scene["nv_authoritative"] = False' in source
    assert 'tool["platform"] != ("macos-arm64" if local else "windows-x64")' in source
