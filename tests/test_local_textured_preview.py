from __future__ import annotations

import hashlib
import json
import os
import subprocess
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
from pipeline.synthetic_village.elevated_topology import (
    canonical_elevated_topology_bytes,
)
from pipeline.synthetic_village.glb_material_audit import GlbMaterialAudit
from pipeline.synthetic_village.local_textured_preview import (
    LOCAL_TRAINING_BUILD_ENTRIES,
    LocalBlenderIdentity,
    LocalTexturedBuildReport,
    LocalTexturedPreviewError,
    LocalTexturedPreviewRequest,
    _expected_building_geometry,
    _publish_local_textured_training_build,
    build_local_textured_preview_manifest,
    build_local_textured_preview_request,
    canonical_local_glb_audit_bytes,
    canonical_local_textured_preview_request_bytes,
    verify_local_textured_training_build_layout,
    verify_stored_local_glb_audit,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
)
from pipeline.synthetic_village.production_render import (
    LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA,
    build_local_production_frame_request,
    canonical_local_production_render_request_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan
from pipeline.synthetic_village.surface_realism import (
    LEGACY_SURFACE_PROFILE_ID,
    SURFACE_PROFILE_V1,
    canonical_surface_realism_plan_bytes,
)
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
BLENDER_BUILDER = ROOT / "scripts/blender/build_synthetic_village.py"
BLENDER_RENDERER = ROOT / "scripts/blender/render_synthetic_village.py"
RUN_LOCAL_ELEVATED_BUILD = os.environ.get("NANTAI_RUN_LOCAL_ELEVATED_BUILD") == "1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    assert request.surface_realism_profile_id == SURFACE_PROFILE_V1
    assert request.surface_realism_plan is not None
    assert request.surface_realism_plan.plan_sha256 == hashlib.sha256(
        canonical_surface_realism_plan_bytes(request.surface_realism_plan),
    ).hexdigest()
    assert request.elevated_topology.scene_plan_sha256 == (
        request.source_hashes.scene_plan_sha256
    )
    assert request.source_hashes.elevated_topology_sha256 == hashlib.sha256(
        canonical_elevated_topology_bytes(request.elevated_topology),
    ).hexdigest()
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
    assert b"source-consistent-multiscale-surface-v1" in raw
    assert raw.endswith(b"\n")
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw


def test_local_request_cannot_validate_as_authoritative_request(tmp_path: Path) -> None:
    request = _local_request(tmp_path)

    with pytest.raises(ValidationError):
        TexturedBuildRequest.model_validate(request.model_dump())


def test_local_blender_rejects_readdressed_invalid_topology_before_staging(
    tmp_path: Path,
) -> None:
    if not LOCAL_BLENDER.is_file():
        pytest.skip("local Blender runtime is not installed")
    visual_root, bundle = publish_material_fixture(tmp_path / "bundle")
    request = build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=LocalBlenderIdentity(
            executable_sha256=_sha256_file(LOCAL_BLENDER),
            version="4.5.11",
            platform="macos-arm64",
            runtime_build_hash="4db51e9d1e1e",
            runtime_output_sha256=hashlib.sha256(b"runtime-probe").hexdigest(),
        ),
    )
    payload = request.model_dump(mode="json")
    payload["elevated_topology"]["semantic_id"] = 13
    payload["source_hashes"]["elevated_topology_sha256"] = hashlib.sha256(
        canary._canonical_json_bytes(payload["elevated_topology"]),
    ).hexdigest()
    unsigned = dict(payload)
    unsigned.pop("preview_id")
    payload["preview_id"] = hashlib.sha256(
        canary._canonical_json_bytes(unsigned),
    ).hexdigest()
    request_path = tmp_path / "invalid-topology-request.json"
    request_path.write_bytes(canary._canonical_json_bytes(payload))
    staging = tmp_path / "staging"

    result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(BLENDER_BUILDER),
            "--",
            "--request",
            str(request_path),
            "--materials",
            str(bundle.final_directory),
            "--staging",
            str(staging),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert result.returncode == 17
    assert "elevated topology provenance or scene binding is invalid" in (
        result.stdout + result.stderr
    )
    assert not staging.exists()


@pytest.mark.skipif(
    not RUN_LOCAL_ELEVATED_BUILD,
    reason="set NANTAI_RUN_LOCAL_ELEVATED_BUILD=1 for the real local Blender build",
)
def test_local_blender_builds_four_registered_elevated_components(
    tmp_path: Path,
) -> None:
    visual_root, bundle = publish_material_fixture(tmp_path / "bundle")
    request = build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=LocalBlenderIdentity(
            executable_sha256=_sha256_file(LOCAL_BLENDER),
            version="4.5.11",
            platform="macos-arm64",
            runtime_build_hash="4db51e9d1e1e",
            runtime_output_sha256=hashlib.sha256(b"runtime-probe").hexdigest(),
        ),
    )
    request_path = tmp_path / "request.json"
    request_path.write_bytes(
        canonical_local_textured_preview_request_bytes(request),
    )
    invocation_root = tmp_path / "invocation"
    invocation_root.mkdir()
    canary.snapshot_material_inputs(
        request=request,  # type: ignore[arg-type]
        material_bundle_root=bundle.final_directory,
        invocation_root=invocation_root,
    )
    staging = tmp_path / "staging"
    result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(BLENDER_BUILDER),
            "--",
            "--request",
            str(request_path),
            "--materials",
            str(invocation_root / "material-inputs"),
            "--staging",
            str(staging),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(
        (staging / "build-report.json").read_text("utf-8"),
    )
    assert report["counts"]["canonical_roots"] == 130
    probe = tmp_path / "probe-elevated.py"
    probe.write_text(
        """
import bpy

expected = {
    "elevated-switchback-stair-v1": {
        "walkable-stair-treads",
        "collision-side-rails",
        "structural-supports",
    },
    "covered-timber-gallery-v1": {
        "walkable-timber-deck",
        "collision-side-rails",
        "covered-roof",
        "structural-supports",
    },
    "terrace-ramp-junction-v1": {
        "walkable-ramp-deck",
        "collision-side-rails",
        "drainage-separation",
        "structural-supports",
    },
    "cross-level-covered-passage-v1": {
        "walkable-cross-level-decks",
        "collision-side-rails",
        "covered-roof",
        "structural-supports",
    },
}
for instance_id, (component_id, parts) in enumerate(expected.items(), 127):
    root = bpy.data.objects.get(f"nv__{component_id}")
    assert root is not None
    assert root["nv_instance_id"] == instance_id
    assert root["nv_semantic_id"] == 14
    assert root["nv_semantic_class"] == "elevated-walkway"
    actual = {
        child["nv_part_id"]
        for child in root.children
        if child.type == "MESH"
    }
    assert actual == parts
    assert all(child.data.polygons for child in root.children if child.type == "MESH")
print("NANTAI_ELEVATED_COMPONENTS_OK", flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    probe_result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            str(staging / "village-canary.blend"),
            "--python",
            str(probe),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert probe_result.returncode == 0, probe_result.stdout + probe_result.stderr
    assert "NANTAI_ELEVATED_COMPONENTS_OK" in probe_result.stdout

    parsed_report = LocalTexturedBuildReport.model_validate_json(
        (staging / "build-report.json").read_bytes(),
    )
    frame_request = build_local_production_frame_request(
        plan=build_production_camera_plan(),
        camera_id="camera-elevated-pedestrian-001",
        build_id=parsed_report.preview_id,
        blender_executable_sha256=_sha256_file(LOCAL_BLENDER),
        renderer_script_sha256=_sha256_file(BLENDER_RENDERER),
        blend_sha256=_sha256_file(staging / "village-canary.blend"),
        build_report_sha256=_sha256_file(staging / "build-report.json"),
        object_registry=parsed_report.object_registry,
        auxiliary_registry=parsed_report.auxiliary_registry,
        semantic_registry=parsed_report.semantic_registry,
    )
    frame_request_path = tmp_path / "production-render-request.json"
    frame_request_path.write_bytes(
        canonical_local_production_render_request_bytes(frame_request),
    )
    frame_staging = tmp_path / "production-frame"
    render_result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            str(staging / "village-canary.blend"),
            "--python",
            str(BLENDER_RENDERER),
            "--",
            "--request",
            str(frame_request_path),
            "--staging",
            str(frame_staging),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert render_result.returncode == 0, render_result.stdout + render_result.stderr
    frame_report = json.loads(
        (frame_staging / "frame-report.json").read_text("utf-8"),
    )
    assert frame_report["schema_version"] == LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA
    assert frame_report["verification_level"] == "L0"
    assert frame_report["camera_id"] == "camera-elevated-pedestrian-001"
    assert frame_report["statistics"]["semantic_ids"][-1] == 14
    assert len(frame_report["artifacts"]) == 6


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


def test_historical_local_request_omits_absent_surface_defaults(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    payload = dict(request.__dict__)
    payload.pop("preview_id")
    payload.pop("surface_realism_profile_id")
    payload.pop("surface_realism_plan")
    historical_id = hashlib.sha256(canary._canonical_json_bytes(payload)).hexdigest()

    historical = LocalTexturedPreviewRequest(
        preview_id=historical_id,
        **payload,
    )
    raw = canonical_local_textured_preview_request_bytes(historical)

    assert historical.surface_realism_profile_id == LEGACY_SURFACE_PROFILE_ID
    assert historical.surface_realism_plan is None
    assert b"surface_realism" not in raw


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
        triangle_count=1,
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


def test_historical_local_glb_audit_remeasures_new_triangle_evidence(
    tmp_path: Path,
) -> None:
    measured = GlbMaterialAudit(
        glb_sha256="3" * 64,
        byte_count=1024,
        mesh_count=1,
        primitive_count=1,
        triangle_count=7,
        material_count=1,
        texture_count=3,
        embedded_image_count=3,
        textured_primitive_count=1,
        uv_primitive_count=1,
        tangent_primitive_count=1,
        slot_ids=("material-fieldstone-01",),
    )
    historical_payload = measured.model_dump(mode="json")
    historical_payload.pop("triangle_count")
    historical_payload.pop("building_geometry")
    audit_path = tmp_path / "glb-material-audit.json"
    audit_path.write_bytes(canary._canonical_json_bytes(historical_payload))

    assert (
        verify_stored_local_glb_audit(
            audit_path,
            measured_audit=measured,
        )
        == measured
    )

    historical_payload["primitive_count"] = 2
    audit_path.write_bytes(canary._canonical_json_bytes(historical_payload))
    with pytest.raises(LocalTexturedPreviewError, match="current GLB bytes"):
        verify_stored_local_glb_audit(
            audit_path,
            measured_audit=measured,
        )


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


def test_training_build_layout_is_report_content_addressed_and_exact(
    tmp_path: Path,
) -> None:
    report_bytes = b"canonical-build-report\n"
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    directory = tmp_path / report_sha256
    directory.mkdir()
    for name in LOCAL_TRAINING_BUILD_ENTRIES:
        (directory / name).write_bytes(
            report_bytes if name == "build-report.json" else name.encode("utf-8"),
        )

    assert (
        verify_local_textured_training_build_layout(
            directory,
            expected_report_sha256=report_sha256,
        )
        == directory
    )

    (directory / "unexpected.bin").write_bytes(b"no")
    with pytest.raises(LocalTexturedPreviewError, match="exact nine-file set"):
        verify_local_textured_training_build_layout(
            directory,
            expected_report_sha256=report_sha256,
        )


def test_training_build_publication_copies_exact_snapshot_once(
    tmp_path: Path,
) -> None:
    source = tmp_path / "verified-staging"
    source.mkdir()
    report_bytes = b"verified-report\n"
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    for name in LOCAL_TRAINING_BUILD_ENTRIES:
        (source / name).write_bytes(
            report_bytes if name == "build-report.json" else name.encode("utf-8"),
        )
    root = tmp_path / "training-builds"
    root.mkdir()

    published = _publish_local_textured_training_build(
        staging=source,
        training_root=root,
        build_report_sha256=report_sha256,
    )

    assert published == root / report_sha256
    assert source.is_dir()
    assert {
        path.name: path.read_bytes() for path in published.iterdir()
    } == {
        path.name: path.read_bytes() for path in source.iterdir()
    }
    assert (
        _publish_local_textured_training_build(
            staging=source,
            training_root=root,
            build_report_sha256=report_sha256,
        )
        == published
    )


def test_builder_keeps_local_schema_and_authoritative_schema_separate() -> None:
    source = (
        ROOT / "scripts/blender/build_synthetic_village.py"
    ).read_text("utf-8")

    assert "local-textured-preview-request.v1" in source
    assert "local-textured-preview-build-report.v1" in source
    assert 'scene["nv_authoritative"] = False' in source
    assert 'tool["platform"] != ("macos-arm64" if local else "windows-x64")' in source
