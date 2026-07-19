"""Path-free request and report identities for near-mesh v2 builds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import pipeline.synthetic_village.mesh_asset_build_v2 as mesh_asset_build_v2
from pipeline.synthetic_village.foliage_atlas import build_foliage_atlas_set
from pipeline.synthetic_village.glb_material_audit import ExpectedGlbMaterial
from pipeline.synthetic_village.local_textured_preview import LocalBlenderIdentity
from pipeline.synthetic_village.material_bundle import (
    canonical_material_bundle_bytes,
    load_material_bundle,
)
from pipeline.synthetic_village.mesh_asset_build import (
    ASSET_RECIPE_CONTRACTS,
    EXPECTED_ASSET_IDS,
)
from pipeline.synthetic_village.mesh_asset_build_v2 import (
    MeshAssetBuildReportRowV2,
    MeshAssetBuildReportV2,
    build_mesh_asset_request_v2,
    canonical_mesh_asset_build_report_v2_bytes,
    canonical_mesh_asset_build_request_v2_bytes,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    GLB_COORDINATE_ENCODING,
    MeshAssetBundle,
    MeshAssetRecord,
    MeshTemplateLod,
    canonical_mesh_asset_bundle_bytes,
)
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BLENDER = LocalBlenderIdentity(
    executable_sha256="a" * 64,
    version="4.5.11",
    platform="macos-arm64",
    runtime_build_hash="4db51e9d1e1e",
    runtime_output_sha256="b" * 64,
)


def _fake_v1_bundle(material_bundle) -> MeshAssetBundle:
    registry = json.loads((ROOT / "assets/registry.json").read_bytes())
    records = []
    for asset_id in EXPECTED_ASSET_IDS:
        kind, _recipe_id, slots = ASSET_RECIPE_CONTRACTS[asset_id]
        footprint = tuple(registry["assets"][asset_id]["footprint_m"])
        lod = {}
        for level, triangles in enumerate((1, 2, 3)):
            digest = hashlib.sha256(f"{asset_id}:{level}".encode()).hexdigest()
            lod[str(level)] = MeshTemplateLod(
                glb_object_path=f"objects/{digest}.glb",
                glb_sha256=digest,
                glb_bytes=128 + level,
                triangle_count=triangles,
                primitive_count=1,
                material_slot_ids=slots,
                aabb={
                    "min": (0.0, 0.0, 0.0),
                    "max": footprint,
                },
            )
        records.append(
            MeshAssetRecord(
                asset_id=asset_id,
                kind=kind,
                mesh_algorithm_id="synthetic-template-mesh-v1",
                footprint_m=footprint,
                lod=lod,
            ),
        )
    unsigned = {
        "schema_version": "nantai.synthetic-village.mesh-asset-bundle.v1",
        "coordinate_encoding": GLB_COORDINATE_ENCODING,
        "material_bundle_id": material_bundle.bundle_id,
        "material_bundle_manifest_sha256": hashlib.sha256(
            canonical_material_bundle_bytes(material_bundle),
        ).hexdigest(),
        "synthetic": True,
        "real_photo_textures": False,
        "build_tool_id": "pytest-v1-source",
        "verification_level": "L0",
        "material_registry": tuple(
            ExpectedGlbMaterial(
                slot_id=row.slot_id,
                source_sha256=row.source_sha256,
                bundle_id=material_bundle.bundle_id,
                algorithm_id=material_bundle.algorithm_id,
            )
            for row in material_bundle.records
        ),
        "records": tuple(records),
    }
    placeholder = MeshAssetBundle.model_construct(
        bundle_id="0" * 64,
        **unsigned,
    )
    bundle_id = hashlib.sha256(
        canonical_mesh_asset_bundle_bytes(
            placeholder,
            exclude_bundle_id=True,
        ),
    ).hexdigest()
    return MeshAssetBundle(bundle_id=bundle_id, **unsigned)


@pytest.fixture(scope="module")
def request_inputs(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("mesh-build-v2-request")
    _visual, material_result = publish_material_fixture(root / "material")
    material_root = material_result.final_directory
    material_bundle = load_material_bundle(material_root)
    atlas = build_foliage_atlas_set(material_root, root / "atlas")
    source_root = root / "source-v1"
    source_root.mkdir()
    return {
        "root": root,
        "material_root": material_root,
        "material_bundle": material_bundle,
        "atlas": atlas,
        "source_root": source_root,
        "source_bundle": _fake_v1_bundle(material_bundle),
    }


def test_request_binds_v1_reuse_and_only_rebuilds_lod2(
    request_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mesh_asset_build_v2,
        "load_mesh_asset_bundle",
        lambda _root: request_inputs["source_bundle"],
    )

    request = build_mesh_asset_request_v2(
        repo_root=ROOT,
        source_v1_bundle_root=request_inputs["source_root"],
        material_bundle_root=request_inputs["material_root"],
        foliage_atlas_set=request_inputs["atlas"],
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        blender_identity=LOCAL_BLENDER,
    )

    assert request.source_v1_bundle_id == request_inputs["source_bundle"].bundle_id
    assert request.lod_levels_to_build == (2,)
    assert len(request.reused_lods) == 22
    assert all(row.lod in {0, 1} for row in request.reused_lods)
    assert all(row.recipe_id.endswith("-near-v2") for row in request.recipes)
    assert b"/Users/" not in canonical_mesh_asset_build_request_v2_bytes(request)
    assert request.foliage_atlas_set == request_inputs["atlas"].manifest


@pytest.mark.parametrize(
    "field",
    (
        "source_v1_bundle_id",
        "builder_script_sha256",
        "asset_registry_sha256",
        "alpha_cutoff",
    ),
)
def test_request_identity_rejects_bound_input_drift(
    request_inputs,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setattr(
        mesh_asset_build_v2,
        "load_mesh_asset_bundle",
        lambda _root: request_inputs["source_bundle"],
    )
    request = build_mesh_asset_request_v2(
        repo_root=ROOT,
        source_v1_bundle_root=request_inputs["source_root"],
        material_bundle_root=request_inputs["material_root"],
        foliage_atlas_set=request_inputs["atlas"],
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        blender_identity=LOCAL_BLENDER,
    )
    payload = request.model_dump(mode="json")
    payload[field] = 0.5 if field == "alpha_cutoff" else "f" * 64

    with pytest.raises(ValidationError):
        type(request).model_validate_json(
            json.dumps(payload, sort_keys=True),
        )


def _report_payload(request) -> dict[str, object]:
    artifacts = []
    for recipe in request.recipes:
        digest = hashlib.sha256(recipe.asset_id.encode()).hexdigest()
        artifacts.append(
            MeshAssetBuildReportRowV2(
                asset_id=recipe.asset_id,
                lod=2,
                artifact_path=f"artifacts/{recipe.asset_id}/lod2.glb",
                glb_sha256=digest,
                glb_bytes=1024,
                triangle_count=recipe.lod2_triangle_min,
                primitive_count=1,
                material_slot_ids=recipe.material_slot_ids,
                local_enu_aabb={
                    "min": (0.0, 0.0, 0.0),
                    "max": recipe.footprint_m,
                },
                texture_bindings=(),
            ),
        )
    return {
        "schema_version": "nantai.synthetic-village.mesh-asset-build-report.v2",
        "build_id": request.build_id,
        "synthetic": True,
        "verification_level": "L0",
        "coordinate_encoding": GLB_COORDINATE_ENCODING,
        "blender_identity": request.blender_identity,
        "builder_script_sha256": request.builder_script_sha256,
        "artifacts": tuple(artifacts),
    }


def test_report_requires_exact_sorted_lod2_matrix(
    request_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mesh_asset_build_v2,
        "load_mesh_asset_bundle",
        lambda _root: request_inputs["source_bundle"],
    )
    request = build_mesh_asset_request_v2(
        repo_root=ROOT,
        source_v1_bundle_root=request_inputs["source_root"],
        material_bundle_root=request_inputs["material_root"],
        foliage_atlas_set=request_inputs["atlas"],
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        blender_identity=LOCAL_BLENDER,
    )
    payload = _report_payload(request)
    report = MeshAssetBuildReportV2.model_validate(payload)

    assert len(report.artifacts) == 11
    assert canonical_mesh_asset_build_report_v2_bytes(report).startswith(b"{")

    missing = {**payload, "artifacts": payload["artifacts"][:-1]}
    with pytest.raises(ValidationError, match="exact sorted"):
        MeshAssetBuildReportV2.model_validate(missing)
    wrong_lod = payload["artifacts"][0].model_copy(update={"lod": 0})
    with pytest.raises(ValidationError):
        MeshAssetBuildReportV2.model_validate(
            {
                **payload,
                "artifacts": (wrong_lod, *payload["artifacts"][1:]),
            },
        )
