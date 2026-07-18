from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.building_geometry import (
    BUILDING_GEOMETRY_V2,
    expected_variant_counts,
)
from pipeline.synthetic_village.canary import TexturedBuildRequest
from pipeline.synthetic_village.glb_material_audit import GlbMaterialAudit
from pipeline.synthetic_village.local_textured_preview import (
    LocalBlenderIdentity,
    LocalTexturedPreviewError,
    LocalTexturedPreviewRequest,
    _expected_building_geometry,
    build_local_textured_preview_manifest,
    build_local_textured_preview_request,
    canonical_local_glb_audit_bytes,
    canonical_local_textured_preview_request_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan
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
    assert request.building_geometry_profile_id == BUILDING_GEOMETRY_V2
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
    assert (
        b'"building_geometry_profile_id": "four-sided-rural-building-v2"'
        in raw
    )
    assert raw.endswith(b"\n")
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw


def test_local_request_cannot_validate_as_authoritative_request(tmp_path: Path) -> None:
    request = _local_request(tmp_path)

    with pytest.raises(ValidationError):
        TexturedBuildRequest.model_validate(request.model_dump())


def test_historical_local_request_omits_absent_geometry_profile(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    payload = dict(request.__dict__)
    payload.pop("preview_id")
    payload.pop("building_geometry_profile_id")
    historical_id = hashlib.sha256(canary._canonical_json_bytes(payload)).hexdigest()

    historical = LocalTexturedPreviewRequest(
        preview_id=historical_id,
        **payload,
    )
    raw = canonical_local_textured_preview_request_bytes(historical)

    assert historical.building_geometry_profile_id == "front-facade-box-v1"
    assert b"building_geometry_profile_id" not in raw


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


def test_historical_local_glb_audit_omits_absent_geometry_evidence() -> None:
    audit = GlbMaterialAudit(
        glb_sha256="3" * 64,
        byte_count=1024,
        mesh_count=1,
        primitive_count=1,
        material_count=1,
        texture_count=3,
        embedded_image_count=3,
        textured_primitive_count=1,
        uv_primitive_count=1,
        tangent_primitive_count=1,
        slot_ids=("material-fieldstone-01",),
    )

    raw = canonical_local_glb_audit_bytes(audit)

    assert audit.building_geometry is None
    assert b"building_geometry" not in raw


def test_local_v2_report_derives_exact_glb_geometry_expectation() -> None:
    building_ids = tuple(
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    )
    report = SimpleNamespace(
        building_geometry_profile_id=BUILDING_GEOMETRY_V2,
        building_geometry=SimpleNamespace(
            added_face_count=8659,
            maximum_added_faces_per_building=124,
            variant_counts=expected_variant_counts(
                building_ids,
                BUILDING_GEOMETRY_V2,
            ),
        ),
        counts=SimpleNamespace(glb_primitives=544),
        semantic_registry=(
            SimpleNamespace(semantic_class="building", semantic_id=3),
        ),
        object_registry=tuple(
            SimpleNamespace(object_id=object_id, semantic_id=3)
            for object_id in building_ids
        ),
    )

    expected = _expected_building_geometry(report)

    assert expected is not None
    assert expected.expected_building_ids == building_ids
    assert expected.expected_primitive_count == 544
    assert expected.expected_added_face_count == 8659
    assert expected.expected_maximum_added_faces_per_building == 124

    tampered_rows = list(report.object_registry)
    tampered_rows[0] = SimpleNamespace(
        object_id="building-tampered-001",
        semantic_id=3,
    )
    report.object_registry = tuple(tampered_rows)
    with pytest.raises(LocalTexturedPreviewError, match="canonical scene set"):
        _expected_building_geometry(report)


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
