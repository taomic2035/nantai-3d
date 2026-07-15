from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.camera_plan import build_camera_plan
from pipeline.synthetic_village.canary import (
    ARTIFACT_REQUESTS,
    AUXILIARY_REGISTRY,
    MATERIAL_FAMILIES,
    ArtifactRecord,
    BuildCounts,
    BuildDeterminism,
    BuildReport,
    BuildRequest,
    BuildValidation,
    CameraRegistryEntry,
    CanaryBuildError,
    PreviewCameraRecord,
    SemanticRegistryEntry,
    VisualSlotRegistryEntry,
    build_canary_request,
    canonical_build_report_bytes,
    canonical_build_request_bytes,
    load_build_report,
    run_canary_build,
    verify_build_report,
)
from pipeline.synthetic_village.defaults import (
    DEFAULT_RECIPE_PATH,
    DEFAULT_VISUAL_SLOTS_PATH,
)
from pipeline.synthetic_village.scene_plan import SEMANTIC_ORDER, build_scene_plan
from pipeline.synthetic_village.visual_sources import (
    VisualSourceManifest,
    VisualSourceRecord,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
VISUAL_PACK_ROOT = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"


def test_build_request_is_frozen_complete_and_content_addressed() -> None:
    scene = build_scene_plan()
    camera = build_camera_plan(scene)

    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=scene,
        camera_plan=camera,
        visual_pack_root=VISUAL_PACK_ROOT,
    )

    assert request.schema_version == "nantai.synthetic-village.blender-build-request.v1"
    assert request.synthetic is True
    assert request.verification_level == "L2"
    assert len(request.object_registry) == 126
    assert request.auxiliary_registry == AUXILIARY_REGISTRY
    assert len(request.visual_slot_registry) == 68
    assert tuple(entry.semantic_class for entry in request.semantic_registry) == (
        "background",
        "terrain",
        "support",
        *SEMANTIC_ORDER,
    )
    assert tuple(entry.semantic_id for entry in request.semantic_registry) == tuple(range(14))
    assert tuple(entry.scope for entry in request.semantic_registry[:3]) == (
        "background",
        "auxiliary",
        "auxiliary",
    )
    assert tuple(entry.material_family for entry in request.material_registry) == MATERIAL_FAMILIES
    assert tuple(entry.material_id for entry in request.material_registry) == tuple(range(1, 12))
    assert request.requested_artifacts == ARTIFACT_REQUESTS
    material_slots = [
        entry for entry in request.visual_slot_registry if entry.category == "material"
    ]
    assert len(material_slots) == 24
    assert all(entry.build_status == "instantiated" for entry in material_slots)
    assert all(entry.implementation == "pbr-material-v1" for entry in material_slots)
    assert (
        hashlib.sha256(
            canonical_build_request_bytes(request, exclude_build_id=True),
        ).hexdigest()
        == request.build_id
    )
    assert canonical_build_request_bytes(request).endswith(b"\n")
    assert b"D:\\" not in canonical_build_request_bytes(request)
    assert b".nantai-studio" not in canonical_build_request_bytes(request)
    with pytest.raises(ValidationError):
        request.build_id = "0" * 64


def test_request_records_each_visual_slot_as_reference_or_placeholder() -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )

    by_id = {entry.slot_id: entry for entry in request.visual_slot_registry}
    assert by_id["key-view-establishing-expanded-01"].usage_mode == ("design-reference-only")
    assert by_id["key-view-establishing-expanded-01"].source_sha256 == (
        "75e9dda41978e9ff9ce04da7269d52a40d6d2e40961559e337f9c9fc76d7dcbf"
    )
    assert by_id["material-rammed-earth-01"].usage_mode == ("procedural-placeholder-v1")
    assert by_id["material-rammed-earth-01"].source_sha256 is None
    assert by_id["material-rammed-earth-01"].build_status == "instantiated"
    assert all(
        entry.build_status == "instantiated"
        or entry.reference_status == "verified-design-reference"
        for entry in request.visual_slot_registry
        if entry.canary_critical
    )


def test_request_models_reject_unknown_fields_and_invalid_slot_provenance() -> None:
    with pytest.raises(ValidationError):
        SemanticRegistryEntry(
            semantic_class="building",
            semantic_id=3,
            scope="canonical-object",
            guessed=True,
        )
    with pytest.raises(ValidationError, match="design-reference-only"):
        VisualSlotRegistryEntry(
            slot_id="material-rammed-earth-01",
            category="material",
            usage_mode="design-reference-only",
            source_sha256=None,
            reference_status="verified-design-reference",
            canary_critical=False,
            build_status="instantiated",
            implementation="pbr-material-v1",
            component_tag="blender-material",
            evidence_ids=("material-rammed-earth-01",),
        )
    with pytest.raises(ValidationError, match="procedural-placeholder-v1"):
        VisualSlotRegistryEntry(
            slot_id="material-rammed-earth-01",
            category="material",
            usage_mode="procedural-placeholder-v1",
            source_sha256="0" * 64,
            reference_status="no-reference",
            canary_critical=False,
            build_status="instantiated",
            implementation="pbr-material-v1",
            component_tag="blender-material",
            evidence_ids=("material-rammed-earth-01",),
        )


def test_request_rejects_tampered_build_id() -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    payload = request.model_dump(mode="json")
    payload["build_id"] = "0" * 64

    with pytest.raises(ValidationError, match="build_id"):
        BuildRequest.model_validate_json(json.dumps(payload))


def test_request_hashes_the_tracked_inputs() -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )

    assert (
        request.source_hashes.default_recipe_sha256
        == hashlib.sha256(
            DEFAULT_RECIPE_PATH.read_bytes(),
        ).hexdigest()
    )
    assert (
        request.source_hashes.visual_catalog_sha256
        == hashlib.sha256(
            DEFAULT_VISUAL_SLOTS_PATH.read_bytes(),
        ).hexdigest()
    )
    script = ROOT / "scripts/blender/build_synthetic_village.py"
    assert (
        request.source_hashes.builder_script_sha256
        == hashlib.sha256(
            script.read_bytes(),
        ).hexdigest()
    )


def test_artifact_request_registry_has_only_portable_exact_names() -> None:
    assert tuple(entry.name for entry in ARTIFACT_REQUESTS) == (
        "preview-bridge.png",
        "preview-central.png",
        "preview-outer.png",
        "preview-upper.png",
        "village-canary.blend",
        "village-canary.glb",
    )
    assert all("/" not in entry.name and "\\" not in entry.name for entry in ARTIFACT_REQUESTS)


def test_build_counts_reject_unregistered_auxiliary_meshes() -> None:
    with pytest.raises(ValidationError):
        BuildCounts(
            canonical_roots=126,
            mesh_objects=130,
            scene_material_families=11,
            visual_materials=24,
            cameras=24,
            lights=3,
            auxiliary_semantic_objects=3,
        )


def _write_artifacts(staging: Path) -> tuple[ArtifactRecord, ...]:
    rows = []
    for index, requested in enumerate(ARTIFACT_REQUESTS, start=1):
        payload = f"artifact-{index}-{requested.name}".encode()
        (staging / requested.name).write_bytes(payload)
        rows.append(
            ArtifactRecord(
                name=requested.name,
                kind=requested.kind,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            ),
        )
    return tuple(rows)


def _valid_report(request: BuildRequest, staging: Path) -> BuildReport:
    return BuildReport(
        build_id=request.build_id,
        tool_identity=request.tool_identity,
        source_hashes=request.source_hashes,
        object_registry=request.object_registry,
        auxiliary_registry=request.auxiliary_registry,
        semantic_registry=request.semantic_registry,
        material_registry=request.material_registry,
        visual_slot_registry=request.visual_slot_registry,
        camera_registry=tuple(
            CameraRegistryEntry(
                camera_id=camera.camera_id,
                blender_camera_name=f"nv__{camera.camera_id}",
                requested_c2w_blender=camera.c2w_blender,
                measured_c2w_blender=camera.c2w_blender,
                max_translation_error_m=0.0,
                max_rotation_entry_error=0.0,
                translation_error_limit_m=0.00004,
                rotation_entry_error_limit=0.00000032,
            )
            for camera in request.camera_plan.cameras
        ),
        preview_registry=(
            PreviewCameraRecord(
                artifact_name="preview-bridge.png",
                blender_camera_name="nv__preview-camera-temporary",
                eye_xyz=(-92.0, -205.0, 108.0),
                target_xyz=(-175.0, -115.0, 43.0),
                lens_mm=46.0,
                clip_start_m=1.0,
                clip_end_m=2000.0,
                image_width_px=1024,
                image_height_px=576,
            ),
            PreviewCameraRecord(
                artifact_name="preview-central.png",
                blender_camera_name="nv__preview-camera-temporary",
                eye_xyz=(108.0, -142.0, 140.0),
                target_xyz=(0.0, 10.0, 71.0),
                lens_mm=42.0,
                clip_start_m=1.0,
                clip_end_m=2000.0,
                image_width_px=1024,
                image_height_px=576,
            ),
            PreviewCameraRecord(
                artifact_name="preview-outer.png",
                blender_camera_name="nv__preview-camera-temporary",
                eye_xyz=(330.0, -290.0, 225.0),
                target_xyz=(0.0, 15.0, 70.0),
                lens_mm=32.0,
                clip_start_m=1.0,
                clip_end_m=2000.0,
                image_width_px=1024,
                image_height_px=576,
            ),
            PreviewCameraRecord(
                artifact_name="preview-upper.png",
                blender_camera_name="nv__preview-camera-temporary",
                eye_xyz=(305.0, 5.0, 175.0),
                target_xyz=(170.0, 115.0, 94.0),
                lens_mm=44.0,
                clip_start_m=1.0,
                clip_end_m=2000.0,
                image_width_px=1024,
                image_height_px=576,
            ),
        ),
        counts=BuildCounts(
            canonical_roots=126,
            mesh_objects=130,
            scene_material_families=11,
            visual_materials=24,
            cameras=24,
            lights=3,
            auxiliary_semantic_objects=2,
        ),
        validation=BuildValidation(
            canonical_object_ids_match=True,
            camera_matrices_within_tolerance=True,
            finite_nonempty_meshes=True,
            semantic_ids_unique=True,
            material_ids_unique=True,
            auxiliary_semantics_present=True,
            all_visual_material_slots_built=True,
            canary_critical_slots_fulfilled=True,
            prop_type_counts={
                variant: 2
                for variant in (
                    "water-jar",
                    "firewood-stack",
                    "bamboo-basket",
                    "wooden-bench",
                    "farming-tools",
                    "grain-rack",
                    "stone-trough",
                    "handcart",
                )
            },
        ),
        determinism=BuildDeterminism(
            request_bytes="canonical-json-v1",
            scene_plan_bytes="canonical-json-v1",
            camera_plan_bytes="canonical-json-v1",
            blend_bytes="measured-not-guaranteed",
            glb_bytes="measured-not-guaranteed",
            preview_bytes="measured-not-guaranteed",
        ),
        artifacts=_write_artifacts(staging),
    )


def test_build_report_is_canonical_path_free_and_verifies_artifact_hashes(
    tmp_path: Path,
) -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    report = _valid_report(request, tmp_path)
    report_path = tmp_path / "build-report.json"
    report_path.write_bytes(canonical_build_report_bytes(report))

    loaded = load_build_report(report_path)
    verify_build_report(loaded, request=request, staging=tmp_path)

    raw = canonical_build_report_bytes(loaded)
    assert raw.endswith(b"\n")
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw


def test_report_loader_rejects_duplicate_noncanonical_and_private_path_json(
    tmp_path: Path,
) -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    report = _valid_report(request, tmp_path)
    canonical = canonical_build_report_bytes(report)
    report_path = tmp_path / "build-report.json"

    report_path.write_bytes(
        canonical.replace(
            b'{\n  "', b'{\n  "build_id": "' + request.build_id.encode() + b'",\n  "', 1
        )
    )
    with pytest.raises(CanaryBuildError, match="duplicate"):
        load_build_report(report_path)

    report_path.write_text(
        json.dumps(report.model_dump(mode="json", by_alias=True)),
        encoding="utf-8",
    )
    with pytest.raises(CanaryBuildError, match="canonical"):
        load_build_report(report_path)

    payload = report.model_dump(mode="json")
    payload["private_path"] = str(Path.home())
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(CanaryBuildError, match="private path|validation"):
        load_build_report(report_path)


def test_report_verifier_rejects_tampered_registry_and_artifact(
    tmp_path: Path,
) -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    report = _valid_report(request, tmp_path)
    tampered = report.model_copy(
        update={
            "source_hashes": report.source_hashes.model_copy(
                update={"builder_script_sha256": "0" * 64},
            ),
        },
    )
    with pytest.raises(CanaryBuildError, match="source hashes"):
        verify_build_report(tampered, request=request, staging=tmp_path)

    (tmp_path / report.artifacts[0].name).write_bytes(b"tampered")
    with pytest.raises(CanaryBuildError, match="artifact"):
        verify_build_report(report, request=request, staging=tmp_path)


def test_report_rejects_malicious_artifact_name() -> None:
    with pytest.raises(ValidationError):
        ArtifactRecord(
            name="../village-canary.blend",
            kind="blender-scene",
            sha256="0" * 64,
            size_bytes=1,
        )


def test_camera_report_preserves_requested_and_measured_blender_matrix() -> None:
    camera = build_camera_plan().cameras[0]
    measured = [
        [struct.unpack("f", struct.pack("f", value))[0] for value in row]
        for row in camera.c2w_blender
    ]
    measured[2][2] = 0.33058303594589233
    translation_error = max(abs(measured[row][3] - camera.c2w_blender[row][3]) for row in range(3))
    rotation_error = max(
        abs(measured[row][column] - camera.c2w_blender[row][column])
        for row in range(3)
        for column in range(3)
    )
    accepted = CameraRegistryEntry(
        camera_id=camera.camera_id,
        blender_camera_name=f"nv__{camera.camera_id}",
        requested_c2w_blender=camera.c2w_blender,
        measured_c2w_blender=tuple(tuple(row) for row in measured),
        max_translation_error_m=round(translation_error, 12),
        max_rotation_entry_error=round(rotation_error, 12),
        translation_error_limit_m=0.00004,
        rotation_entry_error_limit=0.00000032,
    )
    assert accepted.measured_c2w_blender != accepted.requested_c2w_blender

    measured[0][3] = camera.c2w_blender[0][3] + 0.00005
    with pytest.raises(ValidationError, match="tolerance"):
        CameraRegistryEntry(
            camera_id=camera.camera_id,
            blender_camera_name=f"nv__{camera.camera_id}",
            requested_c2w_blender=camera.c2w_blender,
            measured_c2w_blender=tuple(tuple(row) for row in measured),
            max_translation_error_m=0.00005,
            max_rotation_entry_error=round(rotation_error, 12),
            translation_error_limit_m=0.00004,
            rotation_entry_error_limit=0.00000032,
        )

    reflected = [list(row) for row in camera.c2w_blender]
    for row in range(3):
        reflected[row][0] *= -1
    with pytest.raises(ValidationError, match="tolerance|right-handed|rigid"):
        CameraRegistryEntry(
            camera_id=camera.camera_id,
            blender_camera_name=f"nv__{camera.camera_id}",
            requested_c2w_blender=camera.c2w_blender,
            measured_c2w_blender=tuple(tuple(row) for row in reflected),
            max_translation_error_m=0.0,
            max_rotation_entry_error=0.0,
            translation_error_limit_m=0.00004,
            rotation_entry_error_limit=0.00000032,
        )

    bridge = next(
        item for item in build_camera_plan().cameras if item.camera_id == "camera-bridge-001"
    )
    bridge_measured = [list(row) for row in bridge.c2w_blender]
    bridge_measured[2][2] = 0.02645203471183777
    bridge_rotation_error = max(
        abs(bridge_measured[row][column] - bridge.c2w_blender[row][column])
        for row in range(3)
        for column in range(3)
    )
    bridge_record = CameraRegistryEntry(
        camera_id=bridge.camera_id,
        blender_camera_name=f"nv__{bridge.camera_id}",
        requested_c2w_blender=bridge.c2w_blender,
        measured_c2w_blender=tuple(tuple(row) for row in bridge_measured),
        max_translation_error_m=0.0,
        max_rotation_entry_error=round(bridge_rotation_error, 12),
        translation_error_limit_m=0.00004,
        rotation_entry_error_limit=0.00000032,
    )
    assert bridge_record.max_rotation_entry_error == 0.000000291288


def test_verified_staging_is_durably_flushed_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import pipeline.synthetic_village.canary as canary

    for name in ["build-report.json", *(item.name for item in ARTIFACT_REQUESTS)]:
        (tmp_path / name).write_bytes(b"verified")
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        canary,
        "_flush_file",
        lambda path: events.append(("file", Path(path).name)),
    )
    monkeypatch.setattr(
        canary,
        "_flush_directory",
        lambda path: events.append(("directory", Path(path).name)),
    )

    canary._durably_flush_verified_staging(tmp_path)

    assert events == [
        ("file", name)
        for name in sorted(["build-report.json", *(item.name for item in ARTIFACT_REQUESTS)])
    ] + [("directory", tmp_path.name)]


def test_sourced_material_keeps_reference_provenance_and_pbr_build_record(
    tmp_path: Path,
) -> None:
    import pipeline.synthetic_village.canary as canary

    objects = tmp_path / "objects"
    objects.mkdir()
    payload = b"replaceable-design-reference"
    digest = hashlib.sha256(payload).hexdigest()
    (objects / f"{digest}.png").write_bytes(payload)
    baseline_manifest = load_visual_source_manifest(VISUAL_PACK_ROOT / "visual-sources.json")
    for record in baseline_manifest.records:
        shutil.copy2(VISUAL_PACK_ROOT / record.object_path, tmp_path / record.object_path)
    manifest = VisualSourceManifest(
        pack_id="synthetic-mountain-village-hybrid-v3",
        records=(
            *baseline_manifest.records,
            VisualSourceRecord(
                slot_id="material-rammed-earth-01",
                category="material",
                object_path=f"objects/{digest}.png",
                sha256=digest,
                bytes=len(payload),
                width=1,
                height=1,
                prompt="A complete replaceable synthetic rammed-earth material reference prompt.",
                source_pack_id="test-image2-pack",
                source_manifest_sha256="1" * 64,
                generator_interface="test image2 interface",
                actual_model_id="unknown",
                reference_sha256=(),
                synthetic=True,
            ),
        ),
    )
    (tmp_path / "visual-sources.json").write_bytes(canonical_manifest_bytes(manifest))

    rows, _, _ = canary._visual_slot_registry(ROOT, tmp_path)
    material = next(row for row in rows if row.slot_id == "material-rammed-earth-01")

    assert material.usage_mode == "design-reference-only"
    assert material.reference_status == "verified-design-reference"
    assert material.build_status == "instantiated"
    assert material.implementation == "pbr-material-v1"
    assert material.component_tag == "blender-material"
    assert material.evidence_ids == ("material-rammed-earth-01",)


def test_visual_slot_build_evidence_matches_actual_scene_components() -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    slots = {entry.slot_id: entry for entry in request.visual_slot_registry}

    prop_mapping = {
        "prop-water-jar-01": "water-jar",
        "prop-firewood-stack-01": "firewood-stack",
        "prop-bamboo-basket-01": "bamboo-basket",
        "prop-wooden-bench-01": "wooden-bench",
        "prop-farming-tools-01": "farming-tools",
        "prop-grain-rack-01": "grain-rack",
        "prop-stone-trough-01": "stone-trough",
        "prop-handcart-01": "handcart",
    }
    prop_registry = {
        entry.object_id: entry for entry in request.object_registry if entry.variant_id
    }
    for slot_id, variant in prop_mapping.items():
        record = slots[slot_id]
        assert record.build_status == "instantiated"
        assert record.component_tag == variant
        assert len(record.evidence_ids) == 2
        assert all(prop_registry[item].variant_id == variant for item in record.evidence_ids)

    environments = [entry for entry in slots.values() if entry.category == "environment"]
    assert len(environments) == 8
    assert all(entry.build_status == "instantiated" for entry in environments)
    assert all(entry.evidence_ids and entry.component_tag for entry in environments)

    implemented_details = {
        "detail-timber-door-01",
        "detail-timber-window-01",
        "detail-tile-eave-01",
        "detail-roof-ridge-01",
        "detail-courtyard-joint-01",
        "detail-bridge-parapet-01",
    }
    details = [entry for entry in slots.values() if entry.category == "detail"]
    assert {entry.slot_id for entry in details if entry.build_status == "instantiated"} == (
        implemented_details
    )
    assert all(
        entry.evidence_ids and entry.component_tag
        for entry in details
        if entry.slot_id in implemented_details
    )
    assert all(
        entry.evidence_ids == () and entry.component_tag is None
        for entry in details
        if entry.slot_id not in implemented_details
    )


def test_request_rejects_tampered_visual_slot_build_evidence() -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    slots = tuple(
        entry.model_copy(update={"evidence_ids": ("material-pale-plaster-01",)})
        if entry.slot_id == "material-rammed-earth-01"
        else entry
        for entry in request.visual_slot_registry
    )
    payload = {**request.__dict__, "visual_slot_registry": slots}

    with pytest.raises(ValidationError, match="visual slot build evidence"):
        BuildRequest.model_validate(payload)


def test_visual_slot_record_rejects_claimed_evidence_when_not_instantiated() -> None:
    with pytest.raises(ValidationError, match="must not claim component evidence"):
        VisualSlotRegistryEntry(
            slot_id="key-view-community-hall-01",
            category="key-view",
            usage_mode="procedural-placeholder-v1",
            source_sha256=None,
            reference_status="no-reference",
            canary_critical=False,
            build_status="declared-not-instantiated",
            implementation="not-instantiated-v1",
            component_tag="preview-artifact",
            evidence_ids=("preview-central.png",),
        )


def _private_work_root() -> Path:
    return ROOT / ".nantai-studio/synthetic-village/hybrid-v3/work/tests" / uuid.uuid4().hex


def _successful_fake_subprocess(calls: list[tuple[list[str], dict[str, object]]]):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        request_index = argv.index("--request") + 1
        staging_index = argv.index("--staging") + 1
        request = BuildRequest.model_validate_json(Path(argv[request_index]).read_bytes())
        staging = Path(argv[staging_index])
        assert not staging.exists(), "runtime must own absent-only staging publication"
        staging.mkdir()
        report = _valid_report(request, staging)
        (staging / "build-report.json").write_bytes(canonical_build_report_bytes(report))
        kwargs["stdout"].write(b"blender stdout\n")
        kwargs["stderr"].write(b"blender stderr\n")
        return subprocess.CompletedProcess(argv, 0)

    return run


def test_runner_uses_fixed_argv_minimum_environment_and_absent_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_root = _private_work_root()
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(subprocess, "run", _successful_fake_subprocess(calls))
    try:
        result = run_canary_build(
            repo_root=ROOT,
            visual_pack_root=VISUAL_PACK_ROOT,
            work_root=work_root,
            timeout_seconds=321,
        )

        assert result.final_directory == work_root / result.report.build_id
        assert result.final_directory.is_dir()
        assert result.stdout == "blender stdout\n"
        assert result.stderr == "blender stderr\n"
        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv == [
            str((ROOT / "third/blender/blender.exe").absolute()),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            "scripts/blender/build_synthetic_village.py",
            "--",
            "--request",
            argv[10],
            "--staging",
            argv[12],
        ]
        assert Path(argv[10]).is_absolute()
        assert Path(argv[12]).is_absolute()
        assert kwargs["shell"] is False
        assert kwargs["check"] is False
        assert kwargs["cwd"] == str(ROOT.absolute())
        assert kwargs["timeout"] == 321
        assert kwargs["stdin"] is subprocess.DEVNULL
        env = kwargs["env"]
        assert "PATH" not in env
        assert "HOME" not in env
        assert "USERPROFILE" not in env
        assert env["PYTHONHASHSEED"] == "0"
        assert env["PYTHONNOUSERSITE"] == "1"
        assert not list(work_root.glob(".staging-*"))
        assert not list(work_root.glob(".invocation-*"))
        assert sorted(path.name for path in result.final_directory.iterdir()) == sorted(
            ["build-report.json", *(item.name for item in ARTIFACT_REQUESTS)],
        )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


@pytest.mark.parametrize("failure_mode", ["missing-report", "nonzero", "changed-request"])
def test_runner_failure_leaves_no_final_directory_or_report(
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    work_root = _private_work_root()

    def fail(argv, **kwargs):
        request_path = Path(argv[argv.index("--request") + 1])
        if failure_mode == "changed-request":
            request_path.write_bytes(request_path.read_bytes() + b" ")
        return subprocess.CompletedProcess(argv, 17 if failure_mode == "nonzero" else 0)

    monkeypatch.setattr(subprocess, "run", fail)
    try:
        with pytest.raises(CanaryBuildError):
            run_canary_build(
                repo_root=ROOT,
                visual_pack_root=VISUAL_PACK_ROOT,
                work_root=work_root,
            )
        if work_root.exists():
            assert not any(path.name == "build-report.json" for path in work_root.rglob("*"))
            assert not any(path.is_dir() and len(path.name) == 64 for path in work_root.iterdir())
            assert not list(work_root.glob(".staging-*"))
            assert not list(work_root.glob(".invocation-*"))
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_runner_bounds_logs_and_rejects_existing_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_root = _private_work_root()

    def noisy(argv, **kwargs):
        kwargs["stdout"].write(b"x" * (1024 * 1024 + 1))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", noisy)
    try:
        with pytest.raises(CanaryBuildError, match="log"):
            run_canary_build(
                repo_root=ROOT,
                visual_pack_root=VISUAL_PACK_ROOT,
                work_root=work_root,
            )

        calls: list[tuple[list[str], dict[str, object]]] = []
        monkeypatch.setattr(subprocess, "run", _successful_fake_subprocess(calls))
        first = run_canary_build(
            repo_root=ROOT,
            visual_pack_root=VISUAL_PACK_ROOT,
            work_root=work_root,
        )
        with pytest.raises(CanaryBuildError, match="already exists"):
            run_canary_build(
                repo_root=ROOT,
                visual_pack_root=VISUAL_PACK_ROOT,
                work_root=work_root,
            )
        assert len(calls) == 1
        assert first.final_directory.is_dir()
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_runner_rejects_redirected_private_work_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_root = _private_work_root()
    original = __import__("pipeline.synthetic_village.canary", fromlist=["_is_linklike"])
    original_is_linklike = original._is_linklike
    monkeypatch.setattr(
        original,
        "_is_linklike",
        lambda path: Path(path) == work_root.absolute() or original_is_linklike(path),
    )
    try:
        with pytest.raises(CanaryBuildError, match="symlink|junction|redirected"):
            run_canary_build(
                repo_root=ROOT,
                visual_pack_root=VISUAL_PACK_ROOT,
                work_root=work_root,
            )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_build_canary_cli_uses_private_defaults_and_prints_verified_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import synthetic_village as cli

    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            final_directory=Path("D:/private/canary/build-id"),
            report=SimpleNamespace(
                build_id="2" * 64,
                verification_level="L2",
                artifacts=ARTIFACT_REQUESTS,
                camera_registry=tuple(range(24)),
                preview_registry=tuple(range(4)),
            ),
        )

    monkeypatch.setattr(cli, "_run_canary_build", lambda: fake_run)

    assert cli.main(["build-canary", "--timeout-seconds", "123"]) == 0

    assert calls == [
        {
            "repo_root": cli.ROOT,
            "visual_pack_root": cli.DEFAULT_VISUAL_PACK_ROOT,
            "timeout_seconds": 123,
        },
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "artifact_count": 6,
        "build_id": "2" * 64,
        "camera_count": 24,
        "final_directory": "D:\\private\\canary\\build-id",
        "preview_count": 4,
        "verification_level": "L2",
    }
