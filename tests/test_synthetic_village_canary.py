from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import uuid
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary, tool_lock
from pipeline.synthetic_village.building_geometry import (
    BUILDING_GEOMETRY_V1,
    BUILDING_GEOMETRY_V2,
)
from pipeline.synthetic_village.camera_plan import build_camera_plan
from pipeline.synthetic_village.canary import (
    ARTIFACT_REQUESTS,
    AUXILIARY_REGISTRY,
    LOCAL_RENDER_REQUEST_SCHEMA,
    MATERIAL_FAMILIES,
    RENDER_REQUEST_SCHEMA,
    ArtifactRecord,
    BuildCounts,
    BuildDeterminism,
    BuildReport,
    BuildRequest,
    BuildValidation,
    CameraRegistryEntry,
    CanaryBuildError,
    PreviewCameraRecord,
    RenderFrameRequest,
    RenderSettings,
    SemanticRegistryEntry,
    TexturedBuildCounts,
    TexturedBuildReport,
    TexturedBuildRequest,
    TexturedBuildValidation,
    VisualSlotRegistryEntry,
    build_canary_request,
    build_textured_canary_request,
    canonical_build_report_bytes,
    canonical_build_request_bytes,
    canonical_textured_build_report_bytes,
    canonical_textured_build_request_bytes,
    load_build_report,
    run_canary_build,
    run_textured_canary_build,
    verify_build_report,
    verify_textured_build_report,
)
from pipeline.synthetic_village.defaults import (
    DEFAULT_RECIPE_PATH,
    DEFAULT_VISUAL_SLOTS_PATH,
)
from pipeline.synthetic_village.elevated_topology import (
    ElevatedTopologyPlan,
    build_elevated_topology_plan,
    canonical_elevated_topology_bytes,
)
from pipeline.synthetic_village.scene_plan import SEMANTIC_ORDER, build_scene_plan
from pipeline.synthetic_village.visual_sources import (
    VisualSourceManifest,
    VisualSourceRecord,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]
VISUAL_PACK_ROOT = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"


@pytest.fixture(scope="module", autouse=True)
def _hermetic_canary_inputs(tmp_path_factory: pytest.TempPathFactory):
    """Keep unit tests independent of private Release data and installed Blender."""

    global ROOT, VISUAL_PACK_ROOT

    source_root = ROOT
    fixture_root = tmp_path_factory.mktemp("canary-repo")
    for relative_path in (
        Path("assets/default-resources/synthetic-mountain-village-v1.json"),
        Path("assets/default-resources/synthetic-mountain-village-visual-slots-v1.json"),
        Path("scripts/blender/build_synthetic_village.py"),
        Path("scripts/blender/render_synthetic_village.py"),
        Path("tools.lock.json"),
    ):
        destination = fixture_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative_path, destination)

    visual_pack_root = (
        fixture_root / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"
    )
    object_root = visual_pack_root / "objects"
    object_root.mkdir(parents=True)
    expanded_payload = _flat_png(channels=3, bit_depth=8, value=127)
    expanded_sha256 = hashlib.sha256(expanded_payload).hexdigest()
    small_payload = _flat_png(channels=3, bit_depth=8, value=63)
    small_sha256 = hashlib.sha256(small_payload).hexdigest()
    (object_root / f"{expanded_sha256}.png").write_bytes(expanded_payload)
    (object_root / f"{small_sha256}.png").write_bytes(small_payload)
    visual_manifest = VisualSourceManifest(
        pack_id="synthetic-mountain-village-hybrid-v3",
        records=(
            VisualSourceRecord(
                slot_id="key-view-establishing-expanded-01",
                category="key-view",
                object_path=f"objects/{expanded_sha256}.png",
                sha256=expanded_sha256,
                bytes=len(expanded_payload),
                width=1024,
                height=576,
                prompt=(
                    "A deterministic synthetic expanded establishing view for the hermetic "
                    "canary request fixture."
                ),
                source_pack_id="canary-unit-fixture",
                source_manifest_sha256="1" * 64,
                generator_interface="pytest-generated-png",
                actual_model_id="deterministic-test-fixture",
            ),
            VisualSourceRecord(
                slot_id="key-view-establishing-small-01",
                category="key-view",
                object_path=f"objects/{small_sha256}.png",
                sha256=small_sha256,
                bytes=len(small_payload),
                width=1024,
                height=576,
                prompt=(
                    "A deterministic synthetic small establishing view for the hermetic "
                    "canary request fixture."
                ),
                source_pack_id="canary-unit-fixture",
                source_manifest_sha256="2" * 64,
                generator_interface="pytest-generated-png",
                actual_model_id="deterministic-test-fixture",
            ),
        ),
    )
    (visual_pack_root / "visual-sources.json").write_bytes(
        canonical_manifest_bytes(visual_manifest),
    )

    lock = tool_lock.load_tool_lock(fixture_root / "tools.lock.json").blender
    install_root = fixture_root / lock.install_dir
    install_root.mkdir(parents=True)
    executable_payload = b"hermetic blender executable fixture\n"
    (install_root / lock.executable).write_bytes(executable_payload)
    runtime_output = (
        f"{lock.version_output_prefix} (hash {lock.runtime_build_hash} "
        f"built {lock.runtime_build_timestamp})\n{lock.version_output_prefix}"
    )
    receipt = tool_lock.ToolInstallReceipt(
        tool_id="blender",
        version=lock.version,
        platform="windows-x64",
        archive_sha256=lock.archive_sha256,
        executable=lock.executable,
        executable_sha256=hashlib.sha256(executable_payload).hexdigest(),
        runtime_output=runtime_output,
    )
    (install_root / tool_lock.RECEIPT_NAME).write_bytes(
        tool_lock._canonical_model_bytes(receipt),
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        tool_lock,
        "run_blender_version",
        lambda _tool, _install_root: runtime_output,
    )
    ROOT = fixture_root
    VISUAL_PACK_ROOT = visual_pack_root
    try:
        yield
    finally:
        ROOT = source_root
        VISUAL_PACK_ROOT = (
            source_root / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"
        )
        monkeypatch.undo()


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
        "elevated-walkway",
    )
    assert tuple(entry.semantic_id for entry in request.semantic_registry) == tuple(range(15))
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


def test_build_request_identity_binds_exact_elevated_topology() -> None:
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    payload = topology.model_dump(mode="json")
    payload["edges"][0]["collision"]["deck_thickness_m"] = 0.25
    changed_topology = ElevatedTopologyPlan.model_validate_json(
        json.dumps(payload),
    )

    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=scene,
        elevated_topology=topology,
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    changed = build_canary_request(
        repo_root=ROOT,
        scene_plan=scene,
        elevated_topology=changed_topology,
        visual_pack_root=VISUAL_PACK_ROOT,
    )

    topology_bytes = canonical_elevated_topology_bytes(topology)
    assert request.elevated_topology == topology
    assert request.source_hashes.elevated_topology_sha256 == hashlib.sha256(
        topology_bytes,
    ).hexdigest()
    assert request.build_id != changed.build_id
    assert b"component-elevated-switchback-stair-01.png" not in (
        canonical_build_request_bytes(request)
    )


@pytest.fixture(scope="module")
def textured_material_inputs(
    _hermetic_canary_inputs,
    tmp_path_factory: pytest.TempPathFactory,
):
    del _hermetic_canary_inputs
    return publish_material_fixture(tmp_path_factory.mktemp("textured-canary-materials"))


def test_textured_request_binds_exact_material_bundle_without_private_paths(
    textured_material_inputs,
) -> None:
    visual_root, bundle = textured_material_inputs
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )

    assert request.schema_version == "nantai.synthetic-village.blender-build-request.v2"
    assert request.building_geometry_profile_id == BUILDING_GEOMETRY_V1
    assert len(request.material_input_registry) == 24
    assert all(
        row.usage_mode == "runtime-material-source-v1"
        and row.implementation == "derived-pbr-material-v1"
        for row in request.visual_slot_registry
        if row.category == "material"
    )
    assert request.material_algorithm_id == "edge-feather-sobel-orm-v2"
    raw = canonical_textured_build_request_bytes(request)
    assert b"building_geometry_profile_id" not in raw
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw
    assert (
        hashlib.sha256(
            canonical_textured_build_request_bytes(
                request,
                exclude_build_id=True,
            ),
        ).hexdigest()
        == request.build_id
    )


def test_builder_source_contains_verified_texture_uv_and_tangent_path() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/build_synthetic_village.py"
    ).read_text("utf-8")

    for required in (
        '"--materials"',
        "ShaderNodeTexImage",
        "ShaderNodeNormalMap",
        "ShaderNodeSeparateColor",
        "uv_layers.new",
        "calc_tangents",
        "export_tangents=textured",
        "runtime-material-source-v1",
        "derived-pbr-material-v1",
    ):
        assert required in source


def test_textured_builder_uses_baked_normal_strength_once_and_zones_terrain() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts/blender/build_synthetic_village.py"
    ).read_text("utf-8")

    assert 'normal_map.inputs["Strength"].default_value = 1.0' in source
    assert "TERRAIN_TEXTURE_SCALE = 3.0" in source
    assert "_assign_textured_terrain_materials" in source
    for slot_id in (
        "material-moss-stone-01",
        "material-packed-earth-01",
        "material-terrace-soil-01",
    ):
        assert slot_id in source


def test_textured_report_preserves_unfulfilled_critical_slot_evidence(
    tmp_path: Path,
    textured_material_inputs,
) -> None:
    visual_root, bundle = textured_material_inputs
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    report = _valid_textured_report(
        request,
        tmp_path,
        canary_critical_slots_fulfilled=False,
    )

    assert report.validation.canary_critical_slots_fulfilled is False
    assert report.geometry_usability == "preview-only"


def _rebuild_textured_payload(
    request: TexturedBuildRequest,
    **updates,
) -> dict:
    candidate = request.model_copy(update=updates)
    payload = dict(candidate.__dict__)
    payload["build_id"] = hashlib.sha256(
        canonical_textured_build_request_bytes(
            candidate,
            exclude_build_id=True,
        ),
    ).hexdigest()
    return payload


@pytest.mark.parametrize(
    "mutation",
    ["missing-record", "duplicate-slot", "duplicate-map", "v1-material-usage"],
)
def test_textured_request_rejects_incomplete_or_downgraded_material_identity(
    textured_material_inputs,
    mutation: str,
) -> None:
    visual_root, bundle = textured_material_inputs
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    material_rows = list(request.material_input_registry)
    visual_rows = list(request.visual_slot_registry)
    if mutation == "missing-record":
        material_rows.pop()
    elif mutation == "duplicate-slot":
        material_rows[-1] = material_rows[-1].model_copy(
            update={"slot_id": material_rows[0].slot_id},
        )
    elif mutation == "duplicate-map":
        material_rows[0] = material_rows[0].model_copy(
            update={"normal_sha256": material_rows[0].base_color_sha256},
        )
    else:
        target_index = next(
            index for index, row in enumerate(visual_rows) if row.category == "material"
        )
        visual_rows[target_index] = visual_rows[target_index].model_copy(
            update={"usage_mode": "design-reference-only"},
        )

    payload = _rebuild_textured_payload(
        request,
        material_input_registry=tuple(material_rows),
        visual_slot_registry=tuple(visual_rows),
    )
    with pytest.raises(ValidationError):
        TexturedBuildRequest.model_validate(payload)


def test_textured_request_build_id_includes_bundle_and_map_identity(
    textured_material_inputs,
) -> None:
    visual_root, bundle = textured_material_inputs
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    payload = {
        **request.__dict__,
        "material_bundle_manifest_sha256": "3" * 64,
    }

    with pytest.raises(ValidationError, match="build_id"):
        TexturedBuildRequest.model_validate(payload)

    material_rows = list(request.material_input_registry)
    material_rows[0] = material_rows[0].model_copy(
        update={"base_color_sha256": "4" * 64},
    )
    payload = {
        **request.__dict__,
        "material_input_registry": tuple(material_rows),
    }
    with pytest.raises(ValidationError, match="build_id|distinct"):
        TexturedBuildRequest.model_validate(payload)


def test_textured_request_rejects_corrupt_bundle_bytes(
    textured_material_inputs,
) -> None:
    visual_root, bundle = textured_material_inputs
    manifest_path = bundle.final_directory / "manifest.json"
    original_manifest = manifest_path.read_bytes()
    try:
        manifest_path.write_bytes(original_manifest + b" ")
        with pytest.raises(CanaryBuildError, match="material bundle"):
            build_textured_canary_request(
                repo_root=ROOT,
                visual_pack_root=visual_root,
                material_bundle_root=bundle.final_directory,
            )
    finally:
        manifest_path.write_bytes(original_manifest)

    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    first_map = request.material_input_registry[0].base_color_sha256
    map_path = bundle.final_directory / "objects" / f"{first_map}.png"
    original_map = map_path.read_bytes()
    try:
        map_path.write_bytes(b"corrupt")
        with pytest.raises(CanaryBuildError, match="material bundle"):
            build_textured_canary_request(
                repo_root=ROOT,
                visual_pack_root=visual_root,
                material_bundle_root=bundle.final_directory,
            )
    finally:
        map_path.write_bytes(original_map)


def test_request_records_each_visual_slot_as_reference_or_placeholder() -> None:
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )

    by_id = {entry.slot_id: entry for entry in request.visual_slot_registry}
    assert by_id["key-view-establishing-expanded-01"].usage_mode == ("design-reference-only")
    manifest = load_visual_source_manifest(VISUAL_PACK_ROOT / "visual-sources.json")
    assert by_id["key-view-establishing-expanded-01"].source_sha256 == manifest.records[0].sha256
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


def _valid_textured_report(
    request: TexturedBuildRequest,
    staging: Path,
    *,
    canary_critical_slots_fulfilled: bool = True,
) -> TexturedBuildReport:
    return TexturedBuildReport(
        build_id=request.build_id,
        tool_identity=request.tool_identity,
        source_hashes=request.source_hashes,
        object_registry=request.object_registry,
        auxiliary_registry=request.auxiliary_registry,
        semantic_registry=request.semantic_registry,
        material_registry=request.material_registry,
        visual_slot_registry=request.visual_slot_registry,
        material_bundle_manifest_sha256=request.material_bundle_manifest_sha256,
        material_bundle_id=request.material_bundle_id,
        material_algorithm_id=request.material_algorithm_id,
        material_input_registry=request.material_input_registry,
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
        counts=TexturedBuildCounts(
            canonical_roots=126,
            mesh_objects=130,
            scene_material_families=11,
            visual_materials=24,
            cameras=24,
            lights=3,
            auxiliary_semantic_objects=2,
            glb_primitives=130,
            glb_embedded_images=72,
            glb_textures=72,
            glb_uv_primitives=130,
            glb_tangent_primitives=130,
        ),
        validation=TexturedBuildValidation(
            canonical_object_ids_match=True,
            camera_matrices_within_tolerance=True,
            finite_nonempty_meshes=True,
            semantic_ids_unique=True,
            material_ids_unique=True,
            auxiliary_semantics_present=True,
            all_visual_material_slots_built=True,
            canary_critical_slots_fulfilled=canary_critical_slots_fulfilled,
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


def test_textured_report_requires_exact_v2_building_geometry_evidence(
    tmp_path: Path,
    textured_material_inputs,
) -> None:
    visual_root, bundle = textured_material_inputs
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    report = _valid_textured_report(request, tmp_path)
    payload = dict(report.__dict__)
    payload["building_geometry_profile_id"] = BUILDING_GEOMETRY_V2

    with pytest.raises(ValidationError, match="building geometry.*evidence"):
        TexturedBuildReport.model_validate(payload)

    payload["building_geometry"] = {
        "profile_id": BUILDING_GEOMETRY_V2,
        "building_count": 70,
        "covered_elevations": ("front", "left", "rear", "right"),
        "variant_counts": {
            "balanced-residence": 21,
            "rear-service-house": 20,
            "side-entry-workshop": 29,
        },
        "added_face_count": 1000,
        "maximum_added_faces_per_building": 20,
        "new_mesh_object_count": 0,
    }
    validated = TexturedBuildReport.model_validate(payload)

    assert validated.building_geometry is not None
    assert validated.building_geometry.added_face_count == 1000


def test_textured_report_verifier_rejects_geometry_profile_mismatch(
    tmp_path: Path,
    textured_material_inputs,
) -> None:
    visual_root, bundle = textured_material_inputs
    request = build_textured_canary_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
    )
    v2_payload = _rebuild_textured_payload(
        request,
        building_geometry_profile_id=BUILDING_GEOMETRY_V2,
    )
    v2_request = TexturedBuildRequest.model_validate(v2_payload)
    v1_report = _valid_textured_report(v2_request, tmp_path)

    with pytest.raises(CanaryBuildError, match="geometry profile"):
        verify_textured_build_report(
            v1_report,
            request=v2_request,
            staging=tmp_path,
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


def _canonical_test_json(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _flat_png(*, channels: int, bit_depth: int, value: int = 0) -> bytes:
    width, height = 1024, 576
    sample = value.to_bytes(bit_depth // 8, "big")
    row = sample * channels * width
    raw = b"".join(b"\0" + row for _ in range(height))
    color_type = 2 if channels == 3 else 0
    header = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 1))
        + _png_chunk(b"IEND", b"")
    )


def _private_render_fixture() -> tuple[Path, Path, BuildRequest]:
    container = _private_work_root()
    container.mkdir(parents=True)
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    build_directory = container / request.build_id
    build_directory.mkdir()
    report = _valid_report(request, build_directory)
    (build_directory / "build-report.json").write_bytes(canonical_build_report_bytes(report))
    return container, build_directory, request


def test_local_render_request_schema_is_l0_only_and_separate_from_l2() -> None:
    build = build_canary_request(
        repo_root=ROOT,
        scene_plan=build_scene_plan(),
        camera_plan=build_camera_plan(),
        visual_pack_root=VISUAL_PACK_ROOT,
    )
    object_registry_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001 - tests the wire contract
            [row.model_dump(mode="json") for row in build.object_registry],
        ),
    ).hexdigest()
    payload = {
        "schema_version": LOCAL_RENDER_REQUEST_SCHEMA,
        "render_id": "1" * 64,
        "build_id": build.build_id,
        "synthetic": True,
        "verification_level": "L0",
        "fidelity": "simplified-pbr-not-render-parity",
        "blender_executable_sha256": "2" * 64,
        "renderer_script_sha256": "3" * 64,
        "blend_sha256": "4" * 64,
        "build_report_sha256": "5" * 64,
        "object_registry_sha256": object_registry_sha256,
        "settings": RenderSettings(),
        "camera": build.camera_plan.cameras[0],
        "measured_c2w_blender": build.camera_plan.cameras[0].c2w_blender,
        "object_registry": build.object_registry,
        "auxiliary_registry": build.auxiliary_registry,
        "semantic_registry": build.semantic_registry,
    }

    local = RenderFrameRequest.model_validate(payload)
    assert local.schema_version == LOCAL_RENDER_REQUEST_SCHEMA
    assert local.verification_level == "L0"

    with pytest.raises(ValidationError, match="schema.*verification"):
        RenderFrameRequest.model_validate({**payload, "verification_level": "L2"})
    with pytest.raises(ValidationError, match="schema.*verification"):
        RenderFrameRequest.model_validate(
            {
                **payload,
                "schema_version": RENDER_REQUEST_SCHEMA,
                "verification_level": "L0",
            },
        )


def test_blender_renderer_declares_separate_local_scene_provenance_path() -> None:
    source = (ROOT / "scripts/blender/render_synthetic_village.py").read_text(
        encoding="utf-8",
    )

    assert "local-textured-render-frame-request.v1" in source
    assert 'scene.get("nv_preview_id")' in source
    assert 'scene.get("nv_authoritative") is not False' in source


def _write_fake_frame(
    argv: list[str],
    *,
    payload_tag: bytes = b"frame",
    incomplete: bool = False,
    **kwargs,
) -> subprocess.CompletedProcess:
    import pipeline.synthetic_village.canary as canary

    request_path = Path(argv[argv.index("--request") + 1])
    staging = Path(argv[argv.index("--staging") + 1])
    request = json.loads(request_path.read_text("utf-8"))
    camera = request["camera"]
    camera_id = camera["camera_id"]
    assert not staging.exists()
    staging.mkdir()
    rgb = _flat_png(channels=3, bit_depth=8, value=31)
    instance = _flat_png(channels=1, bit_depth=16, value=0)
    semantic = _flat_png(channels=1, bit_depth=8, value=0)
    camera_payload = {
        "schema_version": "nantai.synthetic-village.camera-metadata.v1",
        "build_id": request["build_id"],
        "render_id": request["render_id"],
        "synthetic": True,
        "verification_level": "L2",
        "blender_executable_sha256": request["blender_executable_sha256"],
        "camera_id": camera_id,
        "category": camera["category"],
        "split": camera["split"],
        "image_width_px": 1024,
        "image_height_px": 576,
        "coordinate_system": "opencv-c2w-right-down-forward-meters",
        "pixel_origin": "top-left",
        "pixel_center_offset": [0.5, 0.5],
        "depth_encoding": "euclidean-camera-center-range-m",
        "depth_units": "m",
        "depth_invalid_value_m": 0.0,
        "normal_encoding": "world-space-unit-vector",
        "normal_axes": "blender-right-handed-z-up",
        "normal_background_xyz": [0.0, 0.0, 0.0],
        "clip_start_m": 0.1,
        "clip_end_m": 1200.0,
        "depth_channel_layout": "V-float32-zip",
        "normal_channel_layout": "X,Y,Z-float32-zip",
        "instance_pixel_type": "uint16-grayscale-png",
        "semantic_pixel_type": "uint8-grayscale-png",
        "settings_sha256": hashlib.sha256(
            _canonical_test_json(request["settings"]),
        ).hexdigest(),
        "intrinsics": camera["intrinsics"],
        "requested_c2w_opencv": camera["c2w_opencv"],
        "requested_c2w_blender": camera["c2w_blender"],
        "measured_c2w_opencv": canary._blender_c2w_to_opencv(
            request["measured_c2w_blender"],
        ),
        "measured_c2w_blender": request["measured_c2w_blender"],
        "object_registry_sha256": request["object_registry_sha256"],
        "semantic_registry": request["semantic_registry"],
    }
    payloads = {
        f"rgb/{camera_id}.png": rgb + payload_tag,
        f"depth/{camera_id}.exr": b"\x76\x2f\x31\x01depth-" + payload_tag,
        f"normal/{camera_id}.exr": b"\x76\x2f\x31\x01normal-" + payload_tag,
        f"instance/{camera_id}.png": instance,
        f"semantic/{camera_id}.png": semantic,
        f"cameras/{camera_id}.json": _canonical_test_json(camera_payload),
    }
    selected = list(payloads.items())[:1] if incomplete else list(payloads.items())
    artifacts = []
    kind_by_directory = {
        "rgb": "rgb",
        "depth": "depth",
        "normal": "normal",
        "instance": "instance-mask",
        "semantic": "semantic-mask",
        "cameras": "camera-metadata",
    }
    for portable_path, payload in selected:
        path = staging / Path(portable_path)
        path.parent.mkdir()
        path.write_bytes(payload)
        artifacts.append(
            {
                "kind": kind_by_directory[portable_path.split("/", 1)[0]],
                "path": portable_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    if not incomplete:
        report = {
            "schema_version": "nantai.synthetic-village.render-frame-report.v1",
            "build_id": request["build_id"],
            "render_id": request["render_id"],
            "synthetic": True,
            "verification_level": "L2",
            "fidelity": "simplified-pbr-not-render-parity",
            "blender_executable_sha256": request["blender_executable_sha256"],
            "camera_id": camera_id,
            "image_width_px": 1024,
            "image_height_px": 576,
            "depth_encoding": "euclidean-camera-center-range-m",
            "normal_encoding": "world-space-unit-vector",
            "depth_channel_layout": "V-float32-zip",
            "normal_channel_layout": "X,Y,Z-float32-zip",
            "instance_pixel_type": "uint16-grayscale-png",
            "semantic_pixel_type": "uint8-grayscale-png",
            "settings_sha256": hashlib.sha256(
                _canonical_test_json(request["settings"]),
            ).hexdigest(),
            "artifacts": artifacts,
            "statistics": {
                "depth_min_m": 1.0,
                "depth_max_m": 100.0,
                "depth_background_pixels": 0,
                "depth_max_range_error_m": 0.0001,
                "normal_max_unit_error": 0.0001,
                "instance_ids": [0],
                "semantic_ids": [0],
            },
            "validation": {
                "dimensions_match": True,
                "depth_finite_nonnegative": True,
                "depth_camera_range_consistent": True,
                "normal_finite_unit_world_space": True,
                "instance_ids_registered": True,
                "semantic_ids_registered": True,
                "camera_metadata_matches": True,
            },
        }
        report["content_sha256"] = hashlib.sha256(_canonical_test_json(report)).hexdigest()
        (staging / "frame-report.json").write_bytes(_canonical_test_json(report))
    stdout = kwargs["stdout"]
    stderr = kwargs["stderr"]
    stdout.write(b"render stdout\n")
    stderr.write(b"render stderr\n")
    return subprocess.CompletedProcess(argv, 0)


def test_axial_z_conversion_produces_euclidean_off_axis_camera_range() -> None:
    import pipeline.synthetic_village.canary as canary

    center = canary._axial_depth_to_euclidean_range_m(
        5.0,
        u_px=511.5,
        v_px=287.5,
        fx=512.0,
        fy=512.0,
        cx=512.0,
        cy=288.0,
    )
    corner = canary._axial_depth_to_euclidean_range_m(
        5.0,
        u_px=0.5,
        v_px=0.5,
        fx=512.0,
        fy=512.0,
        cx=512.0,
        cy=288.0,
    )

    assert center == pytest.approx(5.000004768369308)
    assert corner == pytest.approx(7.604860944711831)
    assert corner > center


def test_render_settings_pin_zero_rgb_dither() -> None:
    import pipeline.synthetic_village.canary as canary

    settings = canary.RenderSettings()

    assert settings.dither_intensity == 0.0
    assert settings.model_dump(mode="json")["dither_intensity"] == 0.0


def test_render_settings_pin_single_rgb_render_thread() -> None:
    import pipeline.synthetic_village.canary as canary

    settings = canary.RenderSettings()

    assert settings.rgb_render_threads == 1
    assert settings.model_dump(mode="json")["rgb_render_threads"] == 1


def test_render_rejects_explicit_empty_camera_selection_without_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, _request = _private_render_fixture()
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Blender must not run for an empty camera selection")

    monkeypatch.setattr(subprocess, "run", forbidden)
    try:
        with pytest.raises(CanaryBuildError, match="camera IDs"):
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(),
            )
        assert calls == 0
    finally:
        shutil.rmtree(container, ignore_errors=True)


@pytest.mark.parametrize(
    "selection",
    [
        (),
        [],
        "camera-outer-001",
        ("camera-outer-001", []),
        (1,),
        ("camera-outer-001", "camera-outer-001"),
        ("not-a-camera",),
    ],
)
def test_render_camera_selection_rejects_non_tuple_and_invalid_entries(
    selection: object,
) -> None:
    import pipeline.synthetic_village.canary as canary

    with pytest.raises(CanaryBuildError, match="camera IDs"):
        canary._normalize_render_camera_ids(selection)


def test_render_camera_selection_none_and_tuple_use_canonical_order() -> None:
    import pipeline.synthetic_village.canary as canary

    assert canary._normalize_render_camera_ids(None) == canary.RENDER_CAMERA_IDS
    selected = (
        canary.RENDER_CAMERA_IDS[7],
        canary.RENDER_CAMERA_IDS[0],
        canary.RENDER_CAMERA_IDS[3],
    )
    assert canary._normalize_render_camera_ids(selected) == (
        canary.RENDER_CAMERA_IDS[0],
        canary.RENDER_CAMERA_IDS[3],
        canary.RENDER_CAMERA_IDS[7],
    )


def test_render_quarantine_rejects_redirected_layer_without_moving_external_file(
    tmp_path: Path,
) -> None:
    import pipeline.synthetic_village.canary as canary

    render_root = tmp_path / "renders"
    external = tmp_path / "external"
    render_root.mkdir()
    external.mkdir()
    layer = render_root / "rgb"
    try:
        layer.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable: {exc}")
    camera_id = "camera-outer-001"
    external_file = external / f"{camera_id}.png"
    external_file.write_bytes(b"external-evidence")

    with pytest.raises(CanaryBuildError, match="redirected|layer"):
        canary._quarantine_frame_outputs(render_root, camera_id)

    assert external_file.read_bytes() == b"external-evidence"
    assert not (render_root / ".q").exists()


def test_render_quarantine_preflights_linklike_layer_before_any_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    render_root = tmp_path / "renders"
    layer = render_root / "rgb"
    layer.mkdir(parents=True)
    camera_id = "camera-outer-001"
    evidence = layer / f"{camera_id}.png"
    evidence.write_bytes(b"evidence")
    original_is_linklike = canary._is_linklike
    moves = 0

    def simulated_link(path: Path) -> bool:
        return Path(path) == layer or original_is_linklike(path)

    def forbidden_move(source: Path, destination: Path) -> None:
        nonlocal moves
        moves += 1
        raise AssertionError("preflight must reject before moving")

    monkeypatch.setattr(canary, "_is_linklike", simulated_link)
    monkeypatch.setattr(canary, "_move_directory_noreplace", forbidden_move)

    with pytest.raises(CanaryBuildError, match="symlink|junction|redirected"):
        canary._quarantine_frame_outputs(render_root, camera_id)

    assert evidence.read_bytes() == b"evidence"
    assert moves == 0
    assert not (render_root / ".q").exists()


def test_directory_creation_rejects_linklike_managed_root_before_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    target = managed_root / "new"
    original_is_linklike = canary._is_linklike
    monkeypatch.setattr(
        canary,
        "_is_linklike",
        lambda path: Path(path) == managed_root or original_is_linklike(path),
    )

    with pytest.raises(CanaryBuildError, match="symlink|junction|redirected"):
        canary._ensure_real_directory_tree(target, repo_root=managed_root)

    assert not target.exists()


def test_owned_cleanup_requires_real_root_and_exact_expected_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    work_root = tmp_path / "work"
    work_root.mkdir()
    exact = work_root / ".rs-exact"
    prefix_decoy = work_root / ".rs-exact-extra"
    exact.mkdir()
    prefix_decoy.mkdir()

    canary._cleanup_owned_directory(
        prefix_decoy,
        work_root=work_root,
        expected_name=exact.name,
    )
    assert prefix_decoy.is_dir()

    original_is_linklike = canary._is_linklike
    monkeypatch.setattr(
        canary,
        "_is_linklike",
        lambda path: Path(path) == work_root or original_is_linklike(path),
    )
    canary._cleanup_owned_directory(
        exact,
        work_root=work_root,
        expected_name=exact.name,
    )
    assert exact.is_dir()

    monkeypatch.setattr(canary, "_is_linklike", original_is_linklike)
    canary._cleanup_owned_directory(
        exact,
        work_root=work_root,
        expected_name=exact.name,
    )
    assert not exact.exists()
    assert prefix_decoy.is_dir()


def test_render_quarantine_preserves_already_moved_evidence_on_mid_move_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    render_root = tmp_path / "renders"
    camera_id = "camera-outer-001"
    payloads = {"rgb": b"rgb-evidence", "depth": b"depth-evidence"}
    for layer, payload in payloads.items():
        path = render_root / layer / f"{camera_id}.{'png' if layer == 'rgb' else 'exr'}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    original_move = canary._move_directory_noreplace
    calls = 0

    def fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CanaryBuildError("injected quarantine move failure")
        original_move(source, destination)

    monkeypatch.setattr(canary, "_move_directory_noreplace", fail_second)
    with pytest.raises(CanaryBuildError, match="injected"):
        canary._quarantine_frame_outputs(render_root, camera_id)

    evidence = {path.read_bytes() for path in render_root.rglob(f"{camera_id}.*") if path.is_file()}
    assert evidence == set(payloads.values())


def test_render_quarantine_failure_is_recorded_as_failed_prepare_without_blender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    monkeypatch.setattr(subprocess, "run", _write_fake_frame)
    try:
        result = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        (result.render_root / f"rgb/{camera_id}.png").write_bytes(b"tampered")
        blender_calls = 0
        observed_states: list[str] = []
        original_write = canary._write_render_journal

        def quarantine_failure(*args, **kwargs):
            raise CanaryBuildError("injected quarantine failure")

        def forbidden_blender(*args, **kwargs):
            nonlocal blender_calls
            blender_calls += 1
            raise AssertionError("Blender must not run after quarantine failure")

        def observe_journal(path, journal):
            observed_states.append(journal.frames[0].state)
            return original_write(path, journal)

        monkeypatch.setattr(canary, "_quarantine_frame_outputs", quarantine_failure)
        monkeypatch.setattr(canary, "_write_render_journal", observe_journal)
        monkeypatch.setattr(subprocess, "run", forbidden_blender)
        with pytest.raises(CanaryBuildError, match="quarantine"):
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(camera_id,),
            )
        failed = canary.load_render_journal(result.journal_path).frames[0]
        assert failed.state == "failed"
        assert failed.error.stage == "prepare"
        assert blender_calls == 0
        assert observed_states == ["failed"]
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_blender_to_opencv_matrix_conversion_preserves_pose_and_flips_axes() -> None:
    import pipeline.synthetic_village.canary as canary

    blender = (
        (1.0, 2.0, 3.0, 4.0),
        (5.0, 6.0, 7.0, 8.0),
        (9.0, 10.0, 11.0, 12.0),
        (0.0, 0.0, 0.0, 1.0),
    )

    assert canary._blender_c2w_to_opencv(blender) == (
        (1.0, -2.0, -3.0, 4.0),
        (5.0, -6.0, -7.0, 8.0),
        (9.0, -10.0, -11.0, 12.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def test_formal_camera_registry_contains_measured_nonzero_pose_delta() -> None:
    formal = (
        ROOT
        / ".nantai-studio/synthetic-village/hybrid-v3/work/canary"
        / "344e643c81753e986d8945ca2b4a8713f26efedc755ab2055bd4235b1c656d1b"
    )
    if not (formal / "build-report.json").is_file():
        pytest.skip("formal Task 7 report is unavailable")
    report = load_build_report(formal / "build-report.json")

    assert any(
        camera.requested_c2w_blender != camera.measured_c2w_blender
        for camera in report.camera_registry
    )


def test_render_timeout_cleans_runtime_owned_sibling_work_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id

    def timeout(argv, **kwargs):
        request_path = Path(argv[argv.index("--request") + 1])
        staging = Path(argv[argv.index("--staging") + 1])
        render_request = json.loads(request_path.read_text("utf-8"))
        runtime_work = staging.with_name(
            f".{staging.name}.tmp-{render_request['render_id'][:12]}",
        )
        runtime_work.mkdir()
        (runtime_work / "partial.exr").write_bytes(b"partial")
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    try:
        with pytest.raises(CanaryBuildError, match="timeout"):
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(camera_id,),
                timeout_seconds=1,
            )
        assert not list(build_directory.parent.glob("..rs-*.tmp-*"))
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_timeout_rechecks_request_snapshot_and_integrity_error_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id

    def mutate_then_timeout(argv, **kwargs):
        request_path = Path(argv[argv.index("--request") + 1])
        request_path.write_bytes(request_path.read_bytes() + b" ")
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(subprocess, "run", mutate_then_timeout)
    try:
        with pytest.raises(CanaryBuildError, match="canary input changed") as raised:
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(camera_id,),
                timeout_seconds=1,
            )
        assert "timeout" not in str(raised.value).lower()
        journal = canary.load_render_journal(build_directory / "renders/render-journal.json")
        assert journal.frames[0].state == "failed"
        assert journal.frames[0].error.stage == "invoke"
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_rejects_frame_report_with_wrong_settings_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id

    def tampered(argv, **kwargs):
        completed = _write_fake_frame(argv, **kwargs)
        staging = Path(argv[argv.index("--staging") + 1])
        report_path = staging / "frame-report.json"
        report = json.loads(report_path.read_text("utf-8"))
        report["settings_sha256"] = "0" * 64
        report.pop("content_sha256")
        report["content_sha256"] = hashlib.sha256(_canonical_test_json(report)).hexdigest()
        report_path.write_bytes(_canonical_test_json(report))
        return completed

    monkeypatch.setattr(subprocess, "run", tampered)
    try:
        with pytest.raises(CanaryBuildError, match="settings"):
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(camera_id,),
            )
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_records_blender_executable_digest_in_request_and_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    captured = {}

    def capture(argv, **kwargs):
        request_path = Path(argv[argv.index("--request") + 1])
        captured.update(json.loads(request_path.read_text("utf-8")))
        return _write_fake_frame(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture)
    try:
        result = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        expected = request.tool_identity.executable_sha256
        journal = canary.load_render_journal(result.journal_path)
        assert captured["blender_executable_sha256"] == expected
        assert journal.blender_executable_sha256 == expected
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_journal_retries_one_windows_sharing_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    monkeypatch.setattr(subprocess, "run", _write_fake_frame)
    try:
        result = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        journal = canary.load_render_journal(result.journal_path)
        original_replace = canary.os.replace
        calls = 0

        def flaky(source, destination):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError(5, "sharing violation")
            return original_replace(source, destination)

        monkeypatch.setattr(canary.os, "replace", flaky)
        monkeypatch.setattr(canary, "_is_retryable_windows_replace_error", lambda _exc: True)
        monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)
        canary._write_render_journal(result.journal_path, journal)
        assert calls == 2
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_journal_sharing_violation_retry_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    monkeypatch.setattr(subprocess, "run", _write_fake_frame)
    try:
        result = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        journal = canary.load_render_journal(result.journal_path)
        calls = 0

        def always_denied(source, destination):
            nonlocal calls
            calls += 1
            raise PermissionError(5, "sharing violation")

        monkeypatch.setattr(canary.os, "replace", always_denied)
        monkeypatch.setattr(canary, "_is_retryable_windows_replace_error", lambda _exc: True)
        monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)
        with pytest.raises(CanaryBuildError, match="durably update"):
            canary._write_render_journal(result.journal_path, journal)
        assert calls == canary.JOURNAL_REPLACE_ATTEMPTS
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_journal_temporary_cleanup_never_masks_durability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    monkeypatch.setattr(subprocess, "run", _write_fake_frame)
    try:
        result = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        journal = canary.load_render_journal(result.journal_path)
        original_unlink = canary.Path.unlink

        def replace_failure(*_args, **_kwargs):
            raise OSError("primary replace failure")

        def cleanup_failure(path, *args, **kwargs):
            if Path(path).name.startswith(".render-journal-"):
                raise OSError("secondary cleanup failure")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(canary.os, "replace", replace_failure)
        monkeypatch.setattr(canary.Path, "unlink", cleanup_failure)
        with pytest.raises(CanaryBuildError, match="primary replace failure"):
            canary._write_render_journal(result.journal_path, journal)
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_journal_transitions_planned_rendering_verified_and_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    states: list[str] = []
    original_write = canary._write_render_journal

    def observe(path, journal):
        states.append(journal.frames[0].state)
        return original_write(path, journal)

    monkeypatch.setattr(canary, "_write_render_journal", observe)
    monkeypatch.setattr(subprocess, "run", _write_fake_frame)
    try:
        canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
            timeout_seconds=123,
        )
        assert states[:3] == ["planned", "rendering", "verified"]

        second_container, second_build, second_request = _private_render_fixture()
        second_camera = second_request.camera_plan.cameras[0].camera_id

        def fail(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 17)

        states.clear()
        monkeypatch.setattr(subprocess, "run", fail)
        with pytest.raises(CanaryBuildError, match="render failed"):
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=second_build,
                camera_ids=(second_camera,),
            )
        assert states[:3] == ["planned", "rendering", "failed"]
        failed = canary.load_render_journal(second_build / "renders/render-journal.json")
        assert failed.frames[0].error.stage == "invoke"
    finally:
        shutil.rmtree(container, ignore_errors=True)
        if "second_container" in locals():
            shutil.rmtree(second_container, ignore_errors=True)


def test_render_verified_reuse_skips_blender_and_preserves_six_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    calls = 0

    def render(argv, **kwargs):
        nonlocal calls
        calls += 1
        return _write_fake_frame(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", render)
    try:
        first = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        second = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )

        assert calls == 1
        assert first.rendered_count == 1
        assert second.reused_count == 1
        journal = canary.load_render_journal(first.journal_path)
        assert journal.frames[0].state == "verified"
        assert len(journal.frames[0].artifacts) == 6
        assert (
            journal.journal_sha256
            == hashlib.sha256(
                canary.canonical_render_journal_bytes(journal, exclude_sha256=True),
            ).hexdigest()
        )
        raw = first.journal_path.read_bytes()
        assert raw == canary.canonical_render_journal_bytes(journal)
        assert b".nantai-studio" not in raw and str(Path.home()).encode() not in raw
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_camera_metadata_records_exact_pixel_and_channel_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    monkeypatch.setattr(subprocess, "run", _write_fake_frame)
    try:
        result = canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )
        metadata = json.loads(
            (result.render_root / f"cameras/{camera_id}.json").read_text("utf-8"),
        )

        assert metadata["pixel_origin"] == "top-left"
        assert metadata["pixel_center_offset"] == [0.5, 0.5]
        assert metadata["depth_units"] == "m"
        assert metadata["depth_invalid_value_m"] == 0.0
        assert metadata["normal_axes"] == "blender-right-handed-z-up"
        assert metadata["normal_background_xyz"] == [0.0, 0.0, 0.0]
        assert metadata["clip_start_m"] == 0.1
        assert metadata["clip_end_m"] == 1200.0
        assert metadata["depth_channel_layout"] == "V-float32-zip"
        assert metadata["normal_channel_layout"] == "X,Y,Z-float32-zip"
        assert metadata["instance_pixel_type"] == "uint16-grayscale-png"
        assert metadata["semantic_pixel_type"] == "uint8-grayscale-png"
        assert (
            metadata["settings_sha256"]
            == hashlib.sha256(
                canary._canonical_json_bytes(canary.RenderSettings().model_dump(mode="json"))
            ).hexdigest()
        )
    finally:
        shutil.rmtree(container, ignore_errors=True)


@pytest.mark.parametrize("mode", ["partial", "hash-mismatch"])
def test_render_partial_and_hash_mismatch_are_quarantined_before_rerender(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    render_root = build_directory / "renders"
    calls = 0

    def render(argv, **kwargs):
        nonlocal calls
        calls += 1
        return _write_fake_frame(argv, payload_tag=f"frame-{calls}".encode(), **kwargs)

    monkeypatch.setattr(subprocess, "run", render)
    try:
        if mode == "hash-mismatch":
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(camera_id,),
            )
            (render_root / f"rgb/{camera_id}.png").write_bytes(b"tampered")
        else:
            (render_root / "rgb").mkdir(parents=True)
            (render_root / f"rgb/{camera_id}.png").write_bytes(b"partial")

        canary.run_canary_render(
            repo_root=ROOT,
            build_directory=build_directory,
            camera_ids=(camera_id,),
        )

        assert calls == (2 if mode == "hash-mismatch" else 1)
        quarantined = list((render_root / ".q").rglob(f"{camera_id}.png"))
        assert quarantined
        expected = b"tampered" if mode == "hash-mismatch" else b"partial"
        assert any(path.read_bytes() == expected for path in quarantined)
        assert canary.load_render_journal(render_root / "render-journal.json").frames[0].state == (
            "verified"
        )
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_incomplete_temporary_output_never_publishes_verified_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    container, build_directory, request = _private_render_fixture()
    camera_id = request.camera_plan.cameras[0].camera_id
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: _write_fake_frame(argv, incomplete=True, **kwargs),
    )
    try:
        with pytest.raises(CanaryBuildError, match="frame report|incomplete"):
            canary.run_canary_render(
                repo_root=ROOT,
                build_directory=build_directory,
                camera_ids=(camera_id,),
            )
        render_root = build_directory / "renders"
        assert not any(
            (render_root / path).exists()
            for path in (
                f"rgb/{camera_id}.png",
                f"depth/{camera_id}.exr",
                f"normal/{camera_id}.exr",
                f"instance/{camera_id}.png",
                f"semantic/{camera_id}.png",
                f"cameras/{camera_id}.json",
            )
        )
        failed = canary.load_render_journal(render_root / "render-journal.json")
        assert failed.frames[0].state == "failed"
        assert failed.frames[0].artifacts == ()
    finally:
        shutil.rmtree(container, ignore_errors=True)


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


def _successful_textured_fake_subprocess(
    calls: list[tuple[list[str], dict[str, object]]],
):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        request_path = Path(argv[argv.index("--request") + 1])
        material_root = Path(argv[argv.index("--materials") + 1])
        staging = Path(argv[argv.index("--staging") + 1])
        request = TexturedBuildRequest.model_validate_json(request_path.read_bytes())
        assert material_root.parent == request_path.parent
        assert {path.name for path in material_root.iterdir()} == {
            f"{digest}.png"
            for row in request.material_input_registry
            for digest in (
                row.base_color_sha256,
                row.normal_sha256,
                row.orm_sha256,
            )
        }
        assert not staging.exists(), "runtime must own absent-only staging publication"
        staging.mkdir()
        report = _valid_textured_report(request, staging)
        (staging / "build-report.json").write_bytes(
            canonical_textured_build_report_bytes(report),
        )
        kwargs["stdout"].write(b"textured blender stdout\n")
        kwargs["stderr"].write(b"textured blender stderr\n")
        return subprocess.CompletedProcess(argv, 0)

    return run


def test_textured_runner_snapshots_materials_and_uses_exact_argv(
    textured_material_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pipeline.synthetic_village.canary as canary

    visual_root, bundle = textured_material_inputs
    work_root = _private_work_root()
    calls: list[tuple[list[str], dict[str, object]]] = []
    audits = []
    monkeypatch.setattr(subprocess, "run", _successful_textured_fake_subprocess(calls))

    def audit_glb(path, expected_materials):
        audits.append((Path(path), expected_materials))
        artifact = next(
            row
            for row in load_textured_report_from_staging(Path(path).parent).artifacts
            if row.name == "village-canary.glb"
        )
        return SimpleNamespace(glb_sha256=artifact.sha256)

    def load_textured_report_from_staging(staging: Path):
        return TexturedBuildReport.model_validate_json(
            (staging / "build-report.json").read_bytes(),
        )

    monkeypatch.setattr(canary, "audit_textured_glb", audit_glb)
    try:
        result = run_textured_canary_build(
            repo_root=ROOT,
            visual_pack_root=visual_root,
            material_bundle_root=bundle.final_directory,
            work_root=work_root,
            timeout_seconds=321,
        )

        assert result.final_directory == work_root / result.report.build_id
        assert result.stdout == "textured blender stdout\n"
        assert result.stderr == "textured blender stderr\n"
        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[-6:] == [
            "--request",
            argv[-5],
            "--materials",
            argv[-3],
            "--staging",
            argv[-1],
        ]
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == str(ROOT.absolute())
        assert kwargs["timeout"] == 321
        assert len(audits) == 2
        assert len(audits[0][1]) == 24
        assert not list(work_root.glob(".staging-*"))
        assert not list(work_root.glob(".invocation-*"))
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def test_textured_runner_rejects_snapshot_mutation_and_cleans_private_work(
    textured_material_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, bundle = textured_material_inputs
    work_root = _private_work_root()

    def mutate_snapshot(argv, **kwargs):
        material_root = Path(argv[argv.index("--materials") + 1])
        first = sorted(material_root.iterdir())[0]
        first.write_bytes(first.read_bytes() + b"tampered")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", mutate_snapshot)
    try:
        with pytest.raises(CanaryBuildError, match="changed during build"):
            run_textured_canary_build(
                repo_root=ROOT,
                visual_pack_root=visual_root,
                material_bundle_root=bundle.final_directory,
                work_root=work_root,
            )
        if work_root.exists():
            assert not list(work_root.glob(".staging-*"))
            assert not list(work_root.glob(".invocation-*"))
            assert not any(
                path.is_dir() and len(path.name) == 64
                for path in work_root.iterdir()
            )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


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
        "final_directory": str(Path("D:/private/canary/build-id")),
        "preview_count": 4,
        "verification_level": "L2",
    }


@pytest.mark.parametrize(
    ("arguments", "expected_camera_ids"),
    [
        (["render-canary", "--timeout-seconds", "456"], None),
        (
            [
                "render-canary",
                "--camera",
                "camera-outer-001",
                "--camera",
                "camera-bridge-004",
                "--timeout-seconds",
                "456",
            ],
            ("camera-outer-001", "camera-bridge-004"),
        ),
    ],
)
def test_render_canary_cli_uses_private_defaults_and_prints_resume_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected_camera_ids: tuple[str, ...] | None,
) -> None:
    from scripts import synthetic_village as cli

    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            render_id="3" * 64,
            render_root=Path("D:/private/canary/build-id/renders"),
            journal_path=Path("D:/private/canary/build-id/renders/render-journal.json"),
            rendered_count=2,
            reused_count=22,
        )

    monkeypatch.setattr(cli, "_run_canary_render", lambda: fake_run, raising=False)

    assert cli.main(arguments) == 0

    assert calls == [
        {
            "repo_root": cli.ROOT,
            "camera_ids": expected_camera_ids,
            "timeout_seconds": 456,
        },
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "journal_path": str(Path("D:/private/canary/build-id/renders/render-journal.json")),
        "render_id": "3" * 64,
        "render_root": str(Path("D:/private/canary/build-id/renders")),
        "rendered_count": 2,
        "reused_count": 22,
    }
