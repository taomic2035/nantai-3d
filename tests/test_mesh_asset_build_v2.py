"""Path-free request and report identities for near-mesh v2 builds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

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
    MeshAssetBuildError,
)
from pipeline.synthetic_village.mesh_asset_build_v2 import (
    MeshAssetBuildReportRowV2,
    MeshAssetBuildReportV2,
    _expected_texture_bindings,
    _report_sources_and_texture_objects,
    build_mesh_asset_request_v2,
    canonical_mesh_asset_build_report_v2_bytes,
    canonical_mesh_asset_build_request_v2_bytes,
    run_mesh_asset_build_v2,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    GLB_COORDINATE_ENCODING,
    MeshAssetBundle,
    MeshAssetBundleResult,
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


def test_request_texture_closure_replaces_only_foliage_sources(
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
    recipe = next(
        row for row in request.recipes if row.asset_id == "tree_bamboo_01"
    )
    bindings = _expected_texture_bindings(request, recipe)
    by_semantic = {
        (row.material_slot_id, row.role): row
        for row in bindings
    }
    atlas = request.foliage_atlas_set.by_slot[
        "material-bamboo-leaf-01"
    ]
    material = next(
        row
        for row in request.material_input_registry
        if row.slot_id == "material-bamboo-stem-01"
    )

    assert len(bindings) == 6
    assert (
        by_semantic[
            ("material-bamboo-leaf-01", "base_color")
        ].sha256
        == atlas.base_color.sha256
    )
    assert (
        by_semantic[
            ("material-bamboo-leaf-01", "base_color")
        ].derivation_algorithm_id
        == request.foliage_atlas_set.algorithm_id
    )
    assert (
        by_semantic[
            ("material-bamboo-stem-01", "base_color")
        ].sha256
        == material.base_color_sha256
    )
    assert (
        by_semantic[
            ("material-bamboo-stem-01", "base_color")
        ].derivation_algorithm_id
        == request.material_algorithm_id
    )
    assert bindings == tuple(
        sorted(
            bindings,
            key=lambda row: (
                row.material_slot_id,
                row.role,
                row.sha256,
                row.derivation_algorithm_id,
            ),
        ),
    )


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


def _write_fake_builder_outputs(
    root: Path,
    request,
    request_inputs,
) -> MeshAssetBuildReportV2:
    (root / "artifacts").mkdir()
    (root / "textures").mkdir()
    material_payloads = {}
    for record in request_inputs["material_bundle"].records:
        for descriptor in (record.base_color, record.normal, record.orm):
            material_payloads[descriptor.sha256] = (
                request_inputs["material_root"] / descriptor.object_path
            ).read_bytes()
    atlas_payloads = {}
    for record in request_inputs["atlas"].manifest.records:
        for descriptor in (record.base_color, record.normal, record.orm):
            atlas_payloads[descriptor.sha256] = (
                request_inputs["atlas"].root / descriptor.object_path
            ).read_bytes()
    expected_bindings = {
        recipe.asset_id: _expected_texture_bindings(request, recipe)
        for recipe in request.recipes
    }
    for digest in sorted({
        binding.sha256
        for bindings in expected_bindings.values()
        for binding in bindings
    }):
        payload = atlas_payloads.get(digest, material_payloads.get(digest))
        assert payload is not None
        (root / f"textures/{digest}.png").write_bytes(payload)
    rows = []
    for recipe in request.recipes:
        directory = root / "artifacts" / recipe.asset_id
        directory.mkdir()
        artifact = directory / "lod2.glb"
        payload = b"glTF-near-v2:" + recipe.asset_id.encode()
        artifact.write_bytes(payload)
        rows.append(
            MeshAssetBuildReportRowV2(
                asset_id=recipe.asset_id,
                lod=2,
                artifact_path=f"artifacts/{recipe.asset_id}/lod2.glb",
                glb_sha256=hashlib.sha256(payload).hexdigest(),
                glb_bytes=len(payload),
                triangle_count=recipe.lod2_triangle_min,
                primitive_count=1,
                material_slot_ids=recipe.material_slot_ids,
                local_enu_aabb={
                    "min": (0.0, 0.0, 0.0),
                    "max": recipe.footprint_m,
                },
                texture_bindings=expected_bindings[recipe.asset_id],
            ),
        )
    report = MeshAssetBuildReportV2(
        build_id=request.build_id,
        blender_identity=request.blender_identity,
        builder_script_sha256=request.builder_script_sha256,
        artifacts=tuple(rows),
    )
    (root / "build-report.json").write_bytes(
        canonical_mesh_asset_build_report_v2_bytes(report),
    )
    return report


def test_report_crosscheck_produces_exact_lod2_and_texture_sources(
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
    staging = request_inputs["root"] / "fake-builder-output"
    staging.mkdir()
    report = _write_fake_builder_outputs(staging, request, request_inputs)
    recipes = {row.asset_id: row for row in request.recipes}

    def audit(path: Path, **_kwargs):
        row = next(
            item
            for item in report.artifacts
            if path == staging / item.artifact_path
        )
        payload = path.read_bytes()
        return SimpleNamespace(
            glb_sha256=hashlib.sha256(payload).hexdigest(),
            byte_count=len(payload),
            triangle_count=row.triangle_count,
            primitive_count=row.primitive_count,
            slot_ids=row.material_slot_ids,
            topology=SimpleNamespace(aabb=row.local_enu_aabb),
        )

    monkeypatch.setattr(
        mesh_asset_build_v2,
        "audit_shared_textured_glb",
        audit,
    )
    sources, texture_objects = _report_sources_and_texture_objects(
        request=request,
        report=report,
        staging=staging,
    )

    assert tuple(row.asset_id for row in sources) == EXPECTED_ASSET_IDS
    assert all(row.glb_path.is_file() for row in sources)
    assert all(
        row.recipe_id == recipes[row.asset_id].recipe_id
        for row in sources
    )
    assert tuple(row.object_path for row in texture_objects) == tuple(
        sorted(row.object_path for row in texture_objects)
    )
    assert {row.sha256 for row in texture_objects} == {
        binding.sha256
        for row in report.artifacts
        for binding in row.texture_bindings
    }

    first = report.artifacts[0]
    incomplete = first.model_copy(
        update={"texture_bindings": first.texture_bindings[:-1]},
    )
    tampered = report.model_copy(
        update={"artifacts": (incomplete, *report.artifacts[1:])},
    )
    with pytest.raises(MeshAssetBuildError, match="texture closure"):
        _report_sources_and_texture_objects(
            request=request,
            report=tampered,
            staging=staging,
        )


def test_run_near_build_snapshots_invokes_publishes_and_cleans(
    request_inputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = request_inputs["source_root"]
    source_bundle = request_inputs["source_bundle"]
    work_root = request_inputs["root"] / "orchestrator-work"
    publication_root = request_inputs["root"] / "orchestrator-published"
    blender = request_inputs["root"] / "Blender"
    blender.write_bytes(b"verified-test-blender")
    blender_identity = LOCAL_BLENDER.model_copy(
        update={
            "executable_sha256": hashlib.sha256(
                blender.read_bytes(),
            ).hexdigest(),
        },
    )
    final_directory = publication_root / ("d" * 64)
    calls = {}

    monkeypatch.setattr(
        mesh_asset_build_v2,
        "load_mesh_asset_bundle",
        lambda root: (
            source_bundle
            if Path(root) == source_root
            else (_ for _ in ()).throw(AssertionError(root))
        ),
    )
    monkeypatch.setattr(
        mesh_asset_build_v2,
        "_collect_source_v1_snapshots",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        mesh_asset_build_v2,
        "probe_local_blender_identity",
        lambda _path: blender_identity,
    )

    def run_process(**kwargs):
        calls["process"] = kwargs
        request = mesh_asset_build_v2.MeshAssetBuildRequestV2.model_validate_json(
            kwargs["request_path"].read_bytes(),
        )
        kwargs["staging"].mkdir()
        _write_fake_builder_outputs(
            kwargs["staging"],
            request,
            request_inputs,
        )
        return 0, "fake Blender complete", ""

    monkeypatch.setattr(
        mesh_asset_build_v2,
        "_run_blender_process_v2",
        run_process,
    )

    def audit(path: Path, **_kwargs):
        output_root = path.parents[2]
        report = mesh_asset_build_v2.load_mesh_asset_build_report_v2(
            output_root / "build-report.json",
        )
        row = next(
            item
            for item in report.artifacts
            if path == output_root / item.artifact_path
        )
        payload = path.read_bytes()
        return SimpleNamespace(
            glb_sha256=hashlib.sha256(payload).hexdigest(),
            byte_count=len(payload),
            triangle_count=row.triangle_count,
            primitive_count=row.primitive_count,
            slot_ids=row.material_slot_ids,
            topology=SimpleNamespace(aabb=row.local_enu_aabb),
        )

    monkeypatch.setattr(
        mesh_asset_build_v2,
        "audit_shared_textured_glb",
        audit,
    )

    def publish(**kwargs):
        calls["publish"] = kwargs
        assert kwargs["source_v1_bundle_root"] == source_root
        assert kwargs["texture_root"].is_dir()
        assert len(kwargs["lod2_sources"]) == 11
        assert len(kwargs["texture_objects"]) > 0
        final_directory.mkdir(parents=True)
        return MeshAssetBundleResult(
            bundle_id="d" * 64,
            final_directory=final_directory,
            record_count=11,
            reused=False,
        )

    monkeypatch.setattr(
        mesh_asset_build_v2,
        "publish_mesh_asset_bundle_v2",
        publish,
    )
    monkeypatch.setattr(
        mesh_asset_build_v2,
        "_verify_published_mesh_asset_build_v2",
        lambda **kwargs: calls.setdefault("verified", kwargs),
    )

    result = run_mesh_asset_build_v2(
        repo_root=ROOT,
        source_v1_bundle_root=source_root,
        material_bundle_root=request_inputs["material_root"],
        foliage_atlas_set=request_inputs["atlas"],
        blender_executable=blender,
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        work_root=work_root,
        publication_root=publication_root,
        timeout_seconds=90,
    )

    assert result.bundle.bundle_id == "d" * 64
    assert result.report.build_id == result.request.build_id
    assert result.stdout == "fake Blender complete"
    assert calls["process"]["timeout_seconds"] == 90
    assert calls["publish"]["build_tool_id"] == (
        f"mesh-asset-build-v2-{result.request.build_id}"
    )
    assert calls["verified"]["result"] == result.bundle
    assert not tuple(work_root.glob(".mesh-near-invocation-*"))
    assert not tuple(work_root.glob(".mesh-near-builder-*"))


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
