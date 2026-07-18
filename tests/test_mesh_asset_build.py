"""Path-free identity contracts for production mesh-template builds."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import pipeline.synthetic_village.mesh_asset_build as mesh_asset_build
from pipeline.synthetic_village.local_textured_preview import LocalBlenderIdentity
from pipeline.synthetic_village.mesh_asset_build import (
    MeshAssetBuildError,
    MeshAssetBuildReport,
    MeshAssetBuildReportRow,
    MeshAssetBuildRequest,
    build_mesh_asset_request,
    canonical_mesh_asset_build_report_bytes,
    canonical_mesh_asset_build_request_bytes,
    run_mesh_asset_build,
)
from pipeline.synthetic_village.mesh_asset_bundle import Bounds3
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BLENDER = LocalBlenderIdentity(
    executable_sha256="1" * 64,
    version="4.5.11",
    platform="macos-arm64",
    runtime_build_hash="4db51e9d1e1e",
    runtime_output_sha256="2" * 64,
)


@pytest.fixture(scope="module")
def material_bundle(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("mesh-build-material")
    _visual_root, bundle = publish_material_fixture(root)
    return bundle


def _repo_fixture(root: Path, *, builder: bytes = b"# mesh builder v1\n") -> Path:
    (root / "assets").mkdir(parents=True)
    shutil.copyfile(ROOT / "assets/registry.json", root / "assets/registry.json")
    script = root / "scripts/blender/build_mesh_asset_bundle.py"
    script.parent.mkdir(parents=True)
    script.write_bytes(builder)
    return root


def _request(repo_root: Path, material_bundle) -> MeshAssetBuildRequest:
    return build_mesh_asset_request(
        repo_root=repo_root,
        material_bundle_root=material_bundle.final_directory,
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        blender_identity=LOCAL_BLENDER,
    )


def test_request_binds_exact_material_recipe_and_tool_identity(
    material_bundle,
    tmp_path: Path,
) -> None:
    repo_root = _repo_fixture(tmp_path / "repo")

    request = _request(repo_root, material_bundle)

    assert len(request.asset_ids) == 11
    assert request.asset_ids == tuple(sorted(request.asset_ids))
    assert request.lod_levels == (0, 1, 2)
    assert request.material_bundle_id == material_bundle.bundle_id
    assert len(request.material_input_registry) == 24
    assert request.blender_identity == LOCAL_BLENDER
    raw = canonical_mesh_asset_build_request_bytes(request)
    assert str(tmp_path).encode() not in raw
    assert b"/Users/" not in raw
    assert request.build_id == hashlib.sha256(
        canonical_mesh_asset_build_request_bytes(
            request,
            exclude_build_id=True,
        ),
    ).hexdigest()


def test_builder_or_registry_bytes_change_request_identity(
    material_bundle,
    tmp_path: Path,
) -> None:
    repo_root = _repo_fixture(tmp_path / "repo")
    baseline = _request(repo_root, material_bundle)

    builder = repo_root / "scripts/blender/build_mesh_asset_bundle.py"
    builder.write_bytes(b"# mesh builder v2\n")
    changed_builder = _request(repo_root, material_bundle)

    registry_path = repo_root / "assets/registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["assets"]["house_wood_01"]["footprint_m"][0] = 8.25
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    changed_registry = _request(repo_root, material_bundle)

    assert changed_builder.builder_script_sha256 != baseline.builder_script_sha256
    assert changed_builder.build_id != baseline.build_id
    assert changed_registry.asset_registry_sha256 != (
        changed_builder.asset_registry_sha256
    )
    assert changed_registry.recipes != changed_builder.recipes
    assert changed_registry.build_id != changed_builder.build_id


def test_request_rejects_redirected_builder(
    material_bundle,
    tmp_path: Path,
) -> None:
    repo_root = _repo_fixture(tmp_path / "repo")
    builder = repo_root / "scripts/blender/build_mesh_asset_bundle.py"
    outside = tmp_path / "outside.py"
    builder.rename(outside)
    try:
        builder.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable on this host: {exc}")

    with pytest.raises(MeshAssetBuildError, match="redirected|regular"):
        _request(repo_root, material_bundle)


def test_request_rejects_builder_changed_during_snapshot(
    material_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repo_fixture(tmp_path / "repo")
    builder = repo_root / "scripts/blender/build_mesh_asset_bundle.py"
    real_read = mesh_asset_build._read_regular_file
    builder_reads = 0

    def mutate_after_first_builder_read(path: Path, *, label: str) -> bytes:
        nonlocal builder_reads
        payload = real_read(path, label=label)
        if label == "mesh builder script":
            builder_reads += 1
            if builder_reads == 1:
                builder.write_bytes(b"# changed after first snapshot\n")
        return payload

    monkeypatch.setattr(
        mesh_asset_build,
        "_read_regular_file",
        mutate_after_first_builder_read,
    )

    with pytest.raises(MeshAssetBuildError, match="changed"):
        _request(repo_root, material_bundle)


def test_request_rejects_recipe_budget_or_material_drift(
    material_bundle,
    tmp_path: Path,
) -> None:
    request = _request(_repo_fixture(tmp_path / "repo"), material_bundle)
    payload = request.model_dump(mode="json")
    payload["recipes"][0]["lod_triangle_budgets"][2] += 1

    with pytest.raises(ValidationError, match="recipe"):
        MeshAssetBuildRequest.model_validate(payload)

    payload = request.model_dump(mode="json")
    payload["recipes"][0]["material_slot_ids"] = ["material-not-registered"]
    with pytest.raises(ValidationError, match="material"):
        MeshAssetBuildRequest.model_validate(payload)


def _report(request: MeshAssetBuildRequest) -> MeshAssetBuildReport:
    rows = []
    for recipe in request.recipes:
        for lod, triangles in enumerate((1, 2, 3)):
            rows.append(
                MeshAssetBuildReportRow(
                    asset_id=recipe.asset_id,
                    lod=lod,
                    artifact_path=f"artifacts/{recipe.asset_id}/lod{lod}.glb",
                    glb_sha256=hashlib.sha256(
                        f"{recipe.asset_id}:{lod}".encode(),
                    ).hexdigest(),
                    glb_bytes=1024 + lod,
                    triangle_count=triangles,
                    primitive_count=1,
                    material_slot_ids=recipe.material_slot_ids,
                    local_enu_aabb={
                        "min": (0.0, 0.0, 0.0),
                        "max": recipe.footprint_m,
                    },
                ),
            )
    return MeshAssetBuildReport(
        build_id=request.build_id,
        blender_identity=request.blender_identity,
        builder_script_sha256=request.builder_script_sha256,
        artifacts=tuple(rows),
    )


def test_report_requires_exact_sorted_33_rows_and_path_free_identity(
    material_bundle,
    tmp_path: Path,
) -> None:
    request = _request(_repo_fixture(tmp_path / "repo"), material_bundle)
    report = _report(request)

    assert len(report.artifacts) == 33
    assert canonical_mesh_asset_build_report_bytes(report).endswith(b"\n")
    assert str(tmp_path).encode() not in canonical_mesh_asset_build_report_bytes(
        report,
    )

    payload = report.model_dump(mode="json")
    payload["artifacts"].pop()
    with pytest.raises(ValidationError, match="33|artifact"):
        MeshAssetBuildReport.model_validate(payload)


def test_report_rejects_private_or_noncanonical_artifact_path(
    material_bundle,
    tmp_path: Path,
) -> None:
    request = _request(_repo_fixture(tmp_path / "repo"), material_bundle)
    payload = _report(request).model_dump(mode="json")
    payload["artifacts"][0]["artifact_path"] = str(
        tmp_path / "escaped.glb",
    )

    with pytest.raises(ValidationError, match="artifact"):
        MeshAssetBuildReport.model_validate(payload)


def _successful_fake_blender(
    calls: list[tuple[list[str], dict[str, object]]],
    *,
    triangle_offset: int = 0,
):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        request_path = Path(argv[argv.index("--request") + 1])
        material_root = Path(argv[argv.index("--materials") + 1])
        staging = Path(argv[argv.index("--staging") + 1])
        request = MeshAssetBuildRequest.model_validate_json(
            request_path.read_bytes(),
        )
        assert {path.name for path in material_root.iterdir()} == {
            f"{digest}.png"
            for row in request.material_input_registry
            for digest in (
                row.base_color_sha256,
                row.normal_sha256,
                row.orm_sha256,
            )
        }
        assert not staging.exists()
        rows = []
        for recipe in request.recipes:
            for lod, triangle_count in enumerate((1, 2, 3)):
                relative = Path("artifacts") / recipe.asset_id / f"lod{lod}.glb"
                artifact = staging / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                payload = f"glb:{recipe.asset_id}:{lod}".encode()
                artifact.write_bytes(payload)
                rows.append(
                    MeshAssetBuildReportRow(
                        asset_id=recipe.asset_id,
                        lod=lod,
                        artifact_path=relative.as_posix(),
                        glb_sha256=hashlib.sha256(payload).hexdigest(),
                        glb_bytes=len(payload),
                        triangle_count=triangle_count + triangle_offset,
                        primitive_count=1,
                        material_slot_ids=recipe.material_slot_ids,
                        local_enu_aabb=Bounds3(
                            min=(0.0, 0.0, 0.0),
                            max=recipe.footprint_m,
                        ),
                    ),
                )
        report = MeshAssetBuildReport(
            build_id=request.build_id,
            blender_identity=request.blender_identity,
            builder_script_sha256=request.builder_script_sha256,
            artifacts=tuple(rows),
        )
        (staging / "build-report.json").write_bytes(
            canonical_mesh_asset_build_report_bytes(report),
        )
        kwargs["stdout"].write(b"mesh builder stdout\n")
        kwargs["stderr"].write(b"mesh builder stderr\n")
        return subprocess.CompletedProcess(argv, 0)

    return run


def _install_fake_post_audits(
    monkeypatch: pytest.MonkeyPatch,
    material_bundle,
):
    material_manifest = mesh_asset_build.load_material_bundle(
        material_bundle.final_directory,
    )
    source_by_slot = {
        record.slot_id: record.source_sha256
        for record in material_manifest.records
    }

    def audit(path, *, expected_materials):
        payload = Path(path).read_bytes()
        _prefix, _asset_id, lod_text = payload.decode().split(":")
        assert all(
            row.source_sha256 == source_by_slot[row.slot_id]
            for row in expected_materials
        )
        return SimpleNamespace(
            glb_sha256=hashlib.sha256(payload).hexdigest(),
            byte_count=len(payload),
            triangle_count=int(lod_text) + 1,
            primitive_count=1,
            slot_ids=tuple(row.slot_id for row in expected_materials),
        )

    def bounds(payload):
        _prefix, asset_id, _lod_text = payload.decode().split(":")
        footprint = next(
            row.footprint_m
            for row in mesh_asset_build._recipes_from_registry(
                json.loads((ROOT / "assets/registry.json").read_text()),
            )
            if row.asset_id == asset_id
        )
        return Bounds3(min=(0.0, 0.0, 0.0), max=footprint)

    monkeypatch.setattr(mesh_asset_build, "audit_textured_glb", audit)
    monkeypatch.setattr(
        mesh_asset_build,
        "measure_mesh_template_enu_bounds",
        bounds,
    )


def test_runner_snapshots_invokes_cross_checks_and_publishes(
    material_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repo_fixture(tmp_path / "repo")
    blender = repo_root / "blender"
    blender.write_bytes(b"fake blender")
    blender_identity = LOCAL_BLENDER.model_copy(
        update={
            "executable_sha256": hashlib.sha256(
                blender.read_bytes(),
            ).hexdigest(),
        },
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    publications = []
    monkeypatch.setattr(
        mesh_asset_build,
        "probe_local_blender_identity",
        lambda executable: blender_identity,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _successful_fake_blender(calls),
    )
    _install_fake_post_audits(monkeypatch, material_bundle)

    def publish(**kwargs):
        publications.append(kwargs)
        final = kwargs["publication_root"] / ("a" * 64)
        return SimpleNamespace(
            bundle_id="a" * 64,
            final_directory=final,
            record_count=11,
            reused=False,
        )

    monkeypatch.setattr(mesh_asset_build, "publish_mesh_asset_bundle", publish)
    monkeypatch.setattr(
        mesh_asset_build,
        "load_mesh_asset_bundle",
        lambda path: SimpleNamespace(
            bundle_id="a" * 64,
            records=tuple(
                SimpleNamespace(asset_id=asset_id)
                for asset_id in sorted(mesh_asset_build.ASSET_RECIPE_CONTRACTS)
            ),
        ),
    )

    result = run_mesh_asset_build(
        repo_root=repo_root,
        material_bundle_root=material_bundle.final_directory,
        blender_executable=blender,
        builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
        work_root=tmp_path / "work",
        publication_root=tmp_path / "published",
        timeout_seconds=321,
    )

    assert result.stdout == "mesh builder stdout\n"
    assert result.stderr == "mesh builder stderr\n"
    assert len(publications) == 1
    assert len(publications[0]["sources"]) == 11
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
    assert kwargs["cwd"] == str(repo_root)
    assert kwargs["timeout"] == 321
    assert not list((tmp_path / "work").glob(".mesh-invocation-*"))
    assert not list((tmp_path / "work").glob(".mesh-builder-*"))


def test_runner_rejects_report_audit_disagreement_without_publication(
    material_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = _repo_fixture(tmp_path / "repo")
    blender = repo_root / "blender"
    blender.write_bytes(b"fake blender")
    blender_identity = LOCAL_BLENDER.model_copy(
        update={
            "executable_sha256": hashlib.sha256(
                blender.read_bytes(),
            ).hexdigest(),
        },
    )
    monkeypatch.setattr(
        mesh_asset_build,
        "probe_local_blender_identity",
        lambda executable: blender_identity,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _successful_fake_blender([], triangle_offset=1),
    )
    _install_fake_post_audits(monkeypatch, material_bundle)
    monkeypatch.setattr(
        mesh_asset_build,
        "publish_mesh_asset_bundle",
        lambda **kwargs: pytest.fail("mismatched evidence must not publish"),
    )

    with pytest.raises(MeshAssetBuildError, match="triangle|evidence"):
        run_mesh_asset_build(
            repo_root=repo_root,
            material_bundle_root=material_bundle.final_directory,
            blender_executable=blender,
            builder_script=Path("scripts/blender/build_mesh_asset_bundle.py"),
            work_root=tmp_path / "work",
            publication_root=tmp_path / "published",
        )
    assert not list((tmp_path / "work").glob(".mesh-invocation-*"))
    assert not list((tmp_path / "work").glob(".mesh-builder-*"))
