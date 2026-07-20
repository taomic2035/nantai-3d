"""Read-only Studio server contracts.

The server is deliberately a snapshot adapter, not a reconstruction launcher.
Tests use only files on disk so missing or legacy evidence cannot be promoted to
"real" state by an engine-name heuristic.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import threading
import warnings
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

import pipeline.synthetic_village.material_bundle_v2 as material_v2_module
import pipeline.synthetic_village.mesh_asset_bundle_v3 as mesh_v3_module
from pipeline.studio_server import (
    PathAccessError,
    build_project_snapshot,
    make_server,
    resolve_static_path,
)
from pipeline.synthetic_village.infinite_terrain import TERRAIN_ALGORITHM_ID
from pipeline.synthetic_village.local_textured_preview import (
    LOCAL_LIMITATIONS,
    LocalTexturedPreviewManifest,
    canonical_local_textured_preview_manifest_bytes,
)
from pipeline.synthetic_village.material_bundle import (
    ALGORITHM_ID,
    MATERIAL_PARAMETERS,
    DerivedMaterialBundle,
    canonical_material_bundle_bytes,
    load_material_bundle,
)
from pipeline.synthetic_village.material_bundle_v2 import (
    H2_PROFILE_ID,
    H3_PROFILE_ID,
    compose_material_bundle_v2,
    publish_material_bundle_v2,
)
from pipeline.synthetic_village.mesh_asset_build import (
    ASSET_RECIPE_CONTRACTS,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    canonical_mesh_asset_bundle_bytes,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MeshAssetBundleV2,
    MeshAssetLod2SourceV2,
    TextureObjectV2,
    prepare_mesh_asset_bundle_v2,
)
from pipeline.synthetic_village.mesh_asset_bundle_v3 import (
    publish_mesh_asset_bundle_v3,
)
from tests.test_glb_shared_texture_audit import (
    _fixture as _write_shared_texture_glb_fixture,
)
from tests.test_glb_shared_texture_audit import (
    _rewrite_with_original_binary,
)
from tests.test_material_bundle_v2 import _write_fake_ktx_pack
from tests.test_mesh_asset_bundle import _glb_payload
from tests.test_mesh_chunk import _bundle


def _symlink_or_skip(link: Path, target, *, target_is_directory: bool = False) -> None:
    """Create a symlink for a symlink-escape test, or skip if the OS forbids it.

    Stock Windows raises OSError (WinError 1314) from os.symlink without
    Developer Mode / admin rights. The defenses under test (strict resolve +
    containment + is_symlink rechecks) are platform-agnostic and stay covered on
    POSIX CI, so skipping the setup here avoids a false failure without silently
    dropping the guarantee. Only the code paths that truly need a symlink skip;
    sibling parametrizations (traversal, absolute, dot, ...) still run.
    """
    try:
        Path(link).symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlink creation not permitted on this host: {exc}")

CORE_PROPERTIES = (
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
)
SIMPLE_PROPERTIES = ("x", "y", "z", "r", "g", "b", "scale")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def _write_ply(
    path: Path,
    *,
    properties: tuple[str, ...] = CORE_PROPERTIES,
    values: dict[str, str | int | float] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    row_values: dict[str, str | int | float] = {
        "rot_0": 1,
        "scale": 0.05,
    }
    row_values.update(values or {})
    lines = ["ply", "format ascii 1.0", "element vertex 1"]
    lines.extend(f"property float {name}" for name in properties)
    lines.extend(
        [
            "end_header",
            " ".join(str(row_values.get(name, 0)) for name in properties),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="ascii")
    return path


def _write_ply_with_non_finite_list_extra(path: Path) -> Path:
    _write_ply(path)
    lines = path.read_text(encoding="ascii").splitlines()
    header_end = lines.index("end_header")
    lines.insert(header_end, "property list uchar float hidden_values")
    lines[header_end + 2] += " 1 nan"
    path.write_text("\n".join([*lines, ""]), encoding="ascii")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frame(frame_id: str, *, provenance: str) -> dict:
    return {
        "frame_id": frame_id,
        "handedness": "right",
        "axes": "enu-z-up",
        "units": "meters",
        "metric_status": "metric",
        "geo_aligned": "aligned",
        "provenance": provenance,
        "evidence": ["survey-control-point-01"],
    }


def _write_v2_project(root: Path) -> None:
    (root / "web/studio").mkdir(parents=True)
    (root / "web/studio/index.html").write_text("<h1>Studio</h1>", encoding="utf-8")
    (root / "web/studio/app.mjs").write_text("export const ok = true;", encoding="utf-8")

    (root / "input").mkdir()
    (root / "input/photo.jpg").write_bytes(b"jpeg")
    (root / "input/orbit.mp4").write_bytes(b"video")
    (root / "photos/orbit").mkdir(parents=True)
    (root / "photos/orbit/frame_000.jpg").write_bytes(b"frame")
    (root / "photos/photo.jpg").write_bytes(b"photo")

    full = _write_ply(root / "recon/scene_full.ply")
    lod = _write_ply(root / "web/data/recon/recon_lod0.ply", properties=("x", "y", "z"))
    manifest = {
        "schema_version": 2,
        "gaussian_count": 1,
        "bounds": {"min": [0, 0, 0], "max": [1, 1, 1]},
        "full_3dgs": "recon/scene_full.ply",
        "artifacts": {
            "full_3dgs": {
                "path": "recon/scene_full.ply",
                "sha256": _sha256(full),
                "bytes": full.stat().st_size,
            }
        },
        "lod": {"0": "recon_lod0.ply"},
        "sessions": [
            {"session_id": "photos_batch_0", "kind": "photo_batch", "n_images": 1},
            {"session_id": "video_orbit", "kind": "video", "n_images": 1},
        ],
        "coordinate_contract": {
            "pose_frame": _frame("capture-enu", provenance="measured"),
            "target_frame": _frame("world-enu", provenance="measured"),
            "alignment_status": "aligned",
            "metric_evidence": ["survey-control-point-01"],
            "transform_chain": [],
            "applied_transform_ids": [],
            "ancestry": [{
                "kind": "import-splat",
                "source_frame": _frame("capture-enu", provenance="measured"),
            }],
        },
        "provenance": {
            "requested_reconstruction_engine": "import",
            "actual_reconstruction_engine": "imported-3dgs",
            "requested_registration_engine": "colmap",
            "actual_registration_engine": "colmap",
            "synthetic": False,
            "geometry_usability": "metric-aligned",
            "render_fidelity": "dc-point-preview",
        },
    }
    manifest_path = root / "web/data/recon/recon_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registration = {
        "schema_version": 2,
        "sessions": manifest["sessions"],
        "poses": [{"image": "photo.jpg"}, {"image": "orbit/frame_000.jpg"}],
    }
    (root / "recon/registration.json").write_text(json.dumps(registration), encoding="utf-8")

    asset = _write_ply(root / "assets/tree_v1.ply")
    registry = {
        "schema_version": 2,
        "assets": {
            "tree": {
                "kind": "vegetation",
                "ply": asset.name,
                "version": 1,
                "origin": "local-test",
                "footprint_m": [3, 3, 5],
                "sha256": _sha256(asset),
                "registered_at": "2026-07-14T00:00:00Z",
                "history": [],
            }
        },
    }
    (root / "assets/registry.json").write_text(json.dumps(registry), encoding="utf-8")
    chunk = _write_ply(root / "web/data/chunk_0_0.ply", properties=SIMPLE_PROPERTIES)
    world_manifest = {
        "chunks": [{"id": "0_0", "ply_file": chunk.name, "point_count": 1}],
        "asset_consumption": [
            {
                "asset_id": "tree",
                "renderer": "vegetation",
                "chunk_id": "0_0",
                "version": 1,
                "sha256": _sha256(asset),
                "instances": 3,
                "point_count": 1,
            }
        ]
    }
    (root / "web/data/manifest.json").write_text(json.dumps(world_manifest), encoding="utf-8")

    assert full.is_file() and lod.is_file()


@contextmanager
def _running_server(root: Path):
    server = make_server(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(
    server,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
):
    connection = http.client.HTTPConnection(*server.server_address, timeout=30)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, headers, payload


def _write_local_textured_preview(
    root: Path,
    *,
    preview_id: str = "a" * 64,
    glb: bytes = b"glTF-local-preview",
) -> tuple[Path, LocalTexturedPreviewManifest]:
    directory = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/local-previews"
        / preview_id
    )
    directory.mkdir(parents=True)
    (directory / "village-canary.glb").write_bytes(glb)
    manifest = LocalTexturedPreviewManifest(
        preview_id=preview_id,
        model_url=(
            f"/api/local-textured-preview/{preview_id}/village-canary.glb"
        ),
        glb_sha256=hashlib.sha256(glb).hexdigest(),
        glb_bytes=len(glb),
        build_report_sha256="b" * 64,
        audit_sha256="c" * 64,
        material_bundle_id="d" * 64,
        limitations=LOCAL_LIMITATIONS,
    )
    (directory / "manifest.json").write_bytes(
        canonical_local_textured_preview_manifest_bytes(manifest),
    )
    return directory, manifest


def _write_mesh_world_bundle(root: Path):
    glbs = tuple(
        _glb_payload(triangle_count=triangle_count)
        for triangle_count in (1, 2, 3)
    )
    lod_templates = tuple(
        (hashlib.sha256(glb).hexdigest(), len(glb), level + 1)
        for level, glb in enumerate(glbs)
    )
    material_payloads = {}
    for role, color in (
        ("base_color", (112, 94, 62)),
        ("normal", (128, 128, 255)),
        ("orm", (255, 210, 0)),
    ):
        output = io.BytesIO()
        Image.new("RGB", (1024, 1024), color).save(
            output,
            format="PNG",
            compress_level=9,
            optimize=False,
        )
        material_payloads[role] = output.getvalue()
    source_registry = {
        row.slot_id: row.source_sha256
        for row in _bundle().material_registry
    }
    records = []
    for index, slot_id in enumerate(sorted(MATERIAL_PARAMETERS), start=1):
        parameters = MATERIAL_PARAMETERS[slot_id]
        descriptors = {}
        for role, color_space in (
            ("base_color", "srgb"),
            ("normal", "non-color"),
            ("orm", "non-color"),
        ):
            payload = material_payloads[role]
            digest = hashlib.sha256(payload).hexdigest()
            descriptors[role] = {
                "object_path": f"objects/{digest}.png",
                "sha256": digest,
                "bytes": len(payload),
                "width": 1024,
                "height": 1024,
                "media_type": "image/png",
                "color_space": color_space,
            }
        records.append({
            "slot_id": slot_id,
            "source_sha256": source_registry.get(slot_id, f"{index + 100:064x}"),
            "source_width": 12,
            "source_height": 8,
            **descriptors,
            "uv_policy": parameters.uv_policy,
            "nominal_tile_m": parameters.nominal_tile_m,
            "normal_strength": parameters.normal_strength,
            "roughness_center": parameters.roughness_center,
            "metallic": parameters.metallic,
            "replacement_contract_sha256": f"{index + 200:064x}",
            "synthetic": True,
        })
    material_identity = {
        "schema_version": "nantai.synthetic-village.derived-material-bundle.v1",
        "synthetic": True,
        "source_pack_id": "pytest-material-runtime",
        "source_manifest_sha256": "6" * 64,
        "algorithm_id": ALGORITHM_ID,
        "python_version": "pytest",
        "pillow_version": "pytest",
        "module_sha256": "7" * 64,
        "records": tuple(records),
    }
    material_bundle_id = hashlib.sha256(_canonical(material_identity)).hexdigest()
    material_bundle = DerivedMaterialBundle(
        bundle_id=material_bundle_id,
        **material_identity,
    )
    material_manifest = canonical_material_bundle_bytes(material_bundle)
    material_directory = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles"
        / material_bundle.bundle_id
    )
    material_objects = material_directory / "objects"
    material_objects.mkdir(parents=True)
    for payload in material_payloads.values():
        digest = hashlib.sha256(payload).hexdigest()
        (material_objects / f"{digest}.png").write_bytes(payload)
    (material_directory / "manifest.json").write_bytes(material_manifest)

    glbs = tuple(
        _glb_payload(
            triangle_count=triangle_count,
            source_sha256=source_registry["material-fieldstone-01"],
            material_bundle_id=material_bundle.bundle_id,
            material_algorithm_id=ALGORITHM_ID,
        )
        for triangle_count in (1, 2, 3)
    )
    lod_templates = tuple(
        (hashlib.sha256(glb).hexdigest(), len(glb), level + 1)
        for level, glb in enumerate(glbs)
    )
    bundle = _bundle(
        material_bundle_id=material_bundle.bundle_id,
        material_bundle_manifest_sha256=hashlib.sha256(
            material_manifest,
        ).hexdigest(),
        material_algorithm_id=ALGORITHM_ID,
        lod_templates=lod_templates,
        descriptor_aabb={
            "min": [0.0, 0.0, 0.0],
            "max": [1.0, 0.0, 1.0],
        },
    )
    directory = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles"
        / bundle.bundle_id
    )
    object_root = directory / "objects"
    object_root.mkdir(parents=True)
    for glb, (digest, _byte_count, _triangle_count) in zip(
        glbs,
        lod_templates,
        strict=True,
    ):
        (object_root / f"{digest}.glb").write_bytes(glb)
    (directory / "manifest.json").write_bytes(
        canonical_mesh_asset_bundle_bytes(bundle),
    )
    manifest_path = root / "web/data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mesh_grid"] = {
        "on_demand": True,
        "url_template": "/api/world/mesh-chunk/{x}/{y}.json",
        "asset_url_template": (
            "/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb"
        ),
        "world_seed": 42,
        "layout_engine": "mock",
        "terrain_algorithm_id": TERRAIN_ALGORITHM_ID,
        "mesh_asset_bundle_id": bundle.bundle_id,
        "material_bundle_id": bundle.material_bundle_id,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return directory, bundle, glbs[1]


def _mesh_v2_grid_geometry(
    triangle_count: int,
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[int, ...],
]:
    columns = 50
    rows, remainder = divmod(triangle_count, columns * 2)
    assert rows > 0 and remainder == 0
    positions = tuple(
        (
            -0.2 + 0.4 * column / columns,
            0.0,
            -0.04 + 0.08 * row / rows,
        )
        for row in range(rows + 1)
        for column in range(columns + 1)
    )
    indices = []
    stride = columns + 1
    for row in range(rows):
        for column in range(columns):
            lower_left = row * stride + column
            lower_right = lower_left + 1
            upper_left = lower_left + stride
            upper_right = upper_left + 1
            indices.extend(
                (
                    lower_left,
                    lower_right,
                    upper_right,
                    lower_left,
                    upper_right,
                    upper_left,
                ),
            )
    return positions, tuple(indices)


def _write_mesh_world_bundle_v2(
    root: Path,
) -> tuple[Path, MeshAssetBundleV2]:
    source_directory, source_bundle, _glb = _write_mesh_world_bundle(root)
    source_material = next(
        row
        for row in source_bundle.material_registry
        if row.slot_id == "material-fieldstone-01"
    )
    triangle_counts = {
        "building": 8_000,
        "vegetation": 6_000,
        "prop": 1_000,
    }
    near_sources = {}
    texture_objects: dict[str, TextureObjectV2] = {}
    texture_root = root / ".pytest-mesh-v2-textures"
    (texture_root / "textures").mkdir(parents=True)
    for kind, triangle_count in triangle_counts.items():
        positions, indices = _mesh_v2_grid_geometry(triangle_count)
        near_root = root / f".pytest-mesh-v2-{kind}"
        glb_path, _payload, document, bindings, objects, _expected = (
            _write_shared_texture_glb_fixture(
                near_root,
                kind=kind,
                material_algorithm_id=source_material.algorithm_id,
                positions=positions,
                indices=indices,
            )
        )
        if kind == "vegetation":
            bindings = tuple(
                binding.model_copy(
                    update={"material_slot_id": source_material.slot_id},
                )
                for binding in bindings
            )
        extras = document["materials"][0]["extras"]
        extras.update(
            {
                "slot_id": source_material.slot_id,
                "source_sha256": source_material.source_sha256,
                "bundle_id": source_material.bundle_id,
                "algorithm_id": source_material.algorithm_id,
            },
        )
        _rewrite_with_original_binary(glb_path, document)
        for descriptor in objects:
            payload = (
                near_root
                / "texture-root"
                / descriptor.object_path
            ).read_bytes()
            target = texture_root / descriptor.object_path
            if target.exists():
                assert target.read_bytes() == payload
            else:
                target.write_bytes(payload)
            texture_objects[descriptor.sha256] = descriptor
        near_sources[kind] = (glb_path, bindings)
    lod2_sources = tuple(
        MeshAssetLod2SourceV2(
            asset_id=record.asset_id,
            glb_path=near_sources[record.kind][0],
            recipe_id=(
                ASSET_RECIPE_CONTRACTS[record.asset_id][1].removesuffix(
                    "-v1",
                )
                + "-near-v2"
            ),
            texture_bindings=near_sources[record.kind][1],
        )
        for record in source_bundle.records
    )
    prepared = prepare_mesh_asset_bundle_v2(
        source_v1_bundle_root=source_directory,
        lod2_sources=lod2_sources,
        texture_root=texture_root,
        texture_objects=tuple(
            sorted(
                texture_objects.values(),
                key=lambda row: row.object_path,
            ),
        ),
        staging_root=root / ".pytest-mesh-v2-staging",
        build_tool_id="pytest-studio-mesh-v2",
    )
    final_directory = source_directory.parent / prepared.manifest.bundle_id
    prepared.staging_root.rename(final_directory)
    manifest_path = root / "web/data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mesh_grid"]["mesh_asset_bundle_id"] = (
        prepared.manifest.bundle_id
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return final_directory, prepared.manifest


def _write_mesh_world_bundle_v3(
    root: Path,
    monkeypatch,
):
    source_directory, source_bundle = _write_mesh_world_bundle_v2(root)
    h2_material_directory = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles"
        / source_bundle.material_bundle_id
    )
    h2_material = load_material_bundle(h2_material_directory)
    ktx_root = root / ".pytest-h3-ktx"
    ktx_pack = _write_fake_ktx_pack(ktx_root)
    monkeypatch.setattr(
        material_v2_module,
        "ACCEPTED_H2_MATERIAL_BUNDLE_ID",
        h2_material.bundle_id,
    )
    material_v2 = compose_material_bundle_v2(
        h2_material,
        ktx_pack,
    )
    material_result = publish_material_bundle_v2(
        h2_bundle_root=h2_material_directory,
        ktx2_root=ktx_root,
        bundle=material_v2,
        publication_root=root / ".nantai-studio/h3/material-bundles",
        work_root=root / ".nantai-studio/h3/work/material",
    )
    monkeypatch.setattr(
        mesh_v3_module,
        "ACCEPTED_H2_MESH_BUNDLE_ID",
        source_bundle.bundle_id,
    )
    mesh_result = publish_mesh_asset_bundle_v3(
        source_v2_bundle_root=source_directory,
        material_bundle_v2=material_v2,
        ktx2_root=ktx_root,
        publication_root=root / ".nantai-studio/h3/mesh-bundles",
        work_root=root / ".nantai-studio/h3/work/mesh",
    )
    mesh_v3 = mesh_v3_module.load_mesh_asset_bundle_v3(
        mesh_result.final_directory,
    )
    manifest_path = root / "web/data/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mesh_grid"] = {
        "runtime_schema": (
            "nantai.synthetic-village.mesh-chunk-runtime.v3"
        ),
        "on_demand": True,
        "url_template": "/api/world/mesh-chunk/{x}/{y}.json",
        "asset_url_template": (
            "/api/world/mesh-assets/{bundle_id}/{profile_id}/"
            "{asset_id}/lod{lod}.glb"
        ),
        "texture_url_template": (
            "/api/world/mesh-textures/{bundle_id}/{profile_id}/"
            "{sha256}.{extension}"
        ),
        "world_seed": 42,
        "layout_engine": "mock",
        "terrain_algorithm_id": TERRAIN_ALGORITHM_ID,
        "source_mesh_asset_bundle_id": source_bundle.bundle_id,
        "mesh_asset_bundle_id": mesh_v3.bundle_id,
        "fallback_material_bundle_id": h2_material.bundle_id,
        "material_bundle_id": material_v2.bundle_id,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return (
        source_directory,
        source_bundle,
        material_result.final_directory,
        material_v2,
        mesh_result.final_directory,
        mesh_v3,
    )


class TestProjectSnapshot:
    def test_v2_snapshot_is_directly_consumable_by_studio_model(self, tmp_path):
        _write_v2_project(tmp_path)

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["schema_version"] == 2
        assert snapshot["project"]["name"] == tmp_path.name
        assert snapshot["adapter"] == {"kind": "local", "connected": True}
        assert snapshot["sources"]["images"] == 1
        assert snapshot["sources"]["videos"] == 1
        assert snapshot["sources"]["frames"] == 2
        assert snapshot["coordinate"] == {
            "source_frame": "capture-enu",
            "world_frame": "world-enu",
            "source_provenance": "measured",
            "world_provenance": "measured",
            "contributor_provenance": ["measured"],
            "units": "meters",
            "handedness": "right",
            "up_axis": "z",
            "transform_chain": [],
            "metric_evidence": ["survey-control-point-01"],
            "registered_images": 2,
            "total_images": 2,
        }
        reconstruction = snapshot["reconstruction"]
        assert reconstruction["requested_engine"] == "import"
        assert reconstruction["actual_engine"] == "imported-3dgs"
        assert reconstruction["synthetic"] is False
        assert reconstruction["geometry_usability"] == "metric-aligned"
        assert reconstruction["gaussian_count"] == 1
        assert reconstruction["lod"] == [0]
        assert reconstruction["renderer_capabilities"] == ["dc-color"]
        assert reconstruction["attributes"] == list(CORE_PROPERTIES)
        assert reconstruction["artifact"]["sha256"] == _sha256(
            tmp_path / "recon/scene_full.ply"
        )
        assert reconstruction["artifact"]["uri"] == "/recon/scene_full.ply"
        assert reconstruction["artifact"]["immutable"] is False
        assert snapshot["assets"]["registered"] == 1
        assert snapshot["assets"]["consumed"] == 1
        assert snapshot["assets"]["blocked"] == 0
        assert snapshot["assets"]["items"][0]["validated"] is True
        assert snapshot["assets"]["items"][0]["consumed"] is True
        assert set(snapshot["pipeline"]) == {
            "sources", "align", "reconstruct", "stitch", "assets", "review"
        }

    def test_asset_registry_handoff_requires_exact_id_and_sha_match(self, tmp_path):
        _write_v2_project(tmp_path)
        registry = json.loads(
            (tmp_path / "assets/registry.json").read_text(encoding="utf-8")
        )
        tree_sha = registry["assets"]["tree"]["sha256"]
        deliverable = tmp_path / "handoff/deliverables/HANDOFF-TEST"
        deliverable.mkdir(parents=True)
        manifest_path = deliverable / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "handoff_id": "HANDOFF-TEST",
                    "generator": {"source_handoff": "HANDOFF-DESIGN"},
                    "items": [
                        {
                            "asset_id": "tree",
                            "kind": "vegetation",
                            "ply": "tree.ply",
                            "footprint_m": [3, 3, 5],
                            "sha256": tree_sha,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        assets = build_project_snapshot(tmp_path)["assets"]

        assert assets["current_handoff"] == {
            "id": "HANDOFF-TEST",
            "item_count": 1,
            "manifest_sha256": _sha256(manifest_path),
            "source_handoff": "HANDOFF-DESIGN",
        }

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["items"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        assert build_project_snapshot(tmp_path)["assets"]["current_handoff"] is None

    def test_missing_reconstruction_payload_fails_closed(self, tmp_path):
        _write_v2_project(tmp_path)
        (tmp_path / "recon/scene_full.ply").unlink()

        snapshot = build_project_snapshot(tmp_path)

        assert "artifact" not in snapshot["reconstruction"]
        assert snapshot["reconstruction"]["synthetic"] is True
        assert snapshot["reconstruction"]["evidence_status"] == "missing-artifact"
        assert snapshot["pipeline"]["reconstruct"]["availability"] == "missing"
        assert snapshot["pipeline"]["review"]["trust"] == "untrusted"

    def test_declared_preview_only_geometry_survives_the_snapshot_boundary(self, tmp_path):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provenance"]["geometry_usability"] = "preview-only"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["reconstruction"]["synthetic"] is False
        assert snapshot["reconstruction"]["geometry_usability"] == "preview-only"

    @pytest.mark.parametrize("case", ["missing", "path", "sha256", "bytes"])
    def test_v2_full_artifact_descriptor_must_match_live_payload(self, tmp_path, case):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if case == "missing":
            manifest.pop("artifacts")
        elif case == "path":
            manifest["artifacts"]["full_3dgs"]["path"] = "recon/other.ply"
        elif case == "sha256":
            manifest["artifacts"]["full_3dgs"]["sha256"] = "0" * 64
        else:
            manifest["artifacts"]["full_3dgs"]["bytes"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert "artifact" not in snapshot["reconstruction"]
        assert snapshot["reconstruction"]["synthetic"] is True
        assert snapshot["reconstruction"]["geometry_usability"] == "preview-only"
        assert (
            snapshot["reconstruction"]["evidence_status"]
            == "invalid-artifact-descriptor"
        )
        assert "reconstruction-artifact:invalid-descriptor" in snapshot["diagnostics"]
        assert snapshot["pipeline"]["reconstruct"]["availability"] == "missing"

    def test_v2_manifest_does_not_substitute_an_unrelated_orphan_artifact(self, tmp_path):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["full_3dgs"] = "recon/not-the-declared-artifact.ply"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert (tmp_path / "recon/scene_full.ply").is_file()
        assert "artifact" not in snapshot["reconstruction"]
        assert (
            snapshot["reconstruction"]["evidence_status"]
            == "invalid-artifact-descriptor"
        )

    @pytest.mark.parametrize("payload", [
        b"",
        b"not a ply",
        (
            b"ply\nformat ascii 1.0\nelement vertex 1\n"
            b"property float x\nproperty float y\nproperty float z\n"
            b"property float f_dc_0\nproperty float f_dc_1\n"
            b"property float f_dc_2\nproperty float opacity\n"
            b"property float scale_0\nproperty float scale_1\n"
            b"property float scale_2\nproperty float rot_0\n"
            b"property float rot_1\nproperty float rot_2\n"
            b"property float rot_3\nend_header\n"
        ),
    ])
    def test_matching_descriptor_cannot_promote_a_non_ply_artifact(
        self, tmp_path, payload
    ):
        _write_v2_project(tmp_path)
        full_path = tmp_path / "recon/scene_full.ply"
        full_path.write_bytes(payload)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        descriptor = manifest["artifacts"]["full_3dgs"]
        descriptor["sha256"] = _sha256(full_path)
        descriptor["bytes"] = full_path.stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert "artifact" not in snapshot["reconstruction"]
        assert snapshot["reconstruction"]["geometry_usability"] == "preview-only"
        assert (
            snapshot["reconstruction"]["evidence_status"]
            == "invalid-artifact-payload"
        )
        assert snapshot["pipeline"]["reconstruct"]["availability"] == "missing"

    @pytest.mark.parametrize(
        "case",
        [
            "non-finite",
            "infinite",
            "zero-quaternion",
            "non-unit-quaternion",
            "gapped-sh",
            "incomplete-sh",
            "overflow-scale",
            "underflow-scale",
        ],
    )
    def test_matching_descriptor_cannot_promote_invalid_gaussian_semantics(
        self, tmp_path, case
    ):
        _write_v2_project(tmp_path)
        full_path = tmp_path / "recon/scene_full.ply"
        properties = CORE_PROPERTIES
        values: dict[str, str | int | float] = {}
        if case == "non-finite":
            values["x"] = "nan"
        elif case == "infinite":
            values["x"] = "inf"
        elif case == "zero-quaternion":
            values["rot_0"] = 0
        elif case == "non-unit-quaternion":
            values["rot_0"] = 2
        elif case == "gapped-sh":
            properties = (*CORE_PROPERTIES, "f_rest_1")
        elif case == "incomplete-sh":
            properties = (*CORE_PROPERTIES, "f_rest_0")
        elif case == "overflow-scale":
            values["scale_0"] = 1000
        else:
            values["scale_0"] = -1000
        _write_ply(full_path, properties=properties, values=values)

        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        descriptor = manifest["artifacts"]["full_3dgs"]
        descriptor["sha256"] = _sha256(full_path)
        descriptor["bytes"] = full_path.stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            snapshot = build_project_snapshot(tmp_path)

        assert "artifact" not in snapshot["reconstruction"]
        assert snapshot["reconstruction"]["geometry_usability"] == "preview-only"
        assert (
            snapshot["reconstruction"]["evidence_status"]
            == "invalid-artifact-payload"
        )
        assert snapshot["pipeline"]["reconstruct"]["availability"] == "missing"

    def test_vertex_list_property_cannot_hide_non_finite_values(self, tmp_path):
        _write_v2_project(tmp_path)
        full_path = _write_ply_with_non_finite_list_extra(
            tmp_path / "recon/scene_full.ply"
        )
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        descriptor = manifest["artifacts"]["full_3dgs"]
        descriptor["sha256"] = _sha256(full_path)
        descriptor["bytes"] = full_path.stat().st_size
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert "artifact" not in snapshot["reconstruction"]
        assert (
            snapshot["reconstruction"]["evidence_status"]
            == "invalid-artifact-payload"
        )

    def test_incoherent_v2_coordinate_claim_is_reduced_to_unknown(self, tmp_path):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        target = manifest["coordinate_contract"]["target_frame"]
        target["metric_status"] = "arbitrary"
        target["geo_aligned"] = "unaligned"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["coordinate"]["world_frame"] == "unknown"
        assert snapshot["coordinate"]["units"] == "unknown"
        assert snapshot["coordinate"]["metric_evidence"] == []
        assert snapshot["pipeline"]["align"]["trust"] == "untrusted"

    def test_unknown_frame_provenance_cannot_be_promoted_by_manifest_label(
        self, tmp_path
    ):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["coordinate_contract"]["pose_frame"]["provenance"] = "unknown"
        manifest["coordinate_contract"]["target_frame"]["provenance"] = "unknown"
        manifest["provenance"]["geometry_usability"] = "metric-aligned"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["coordinate"]["source_provenance"] == "unknown"
        assert snapshot["coordinate"]["world_provenance"] == "unknown"
        assert snapshot["reconstruction"]["geometry_usability"] == "preview-only"
        assert snapshot["pipeline"]["align"]["trust"] == "untrusted"

    def test_unknown_contributor_provenance_blocks_forged_metric_label(
        self, tmp_path
    ):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["coordinate_contract"]["ancestry"][0]["source_frame"][
            "provenance"
        ] = "unknown"
        manifest["provenance"]["geometry_usability"] = "metric-aligned"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["coordinate"]["source_provenance"] == "measured"
        assert snapshot["coordinate"]["world_provenance"] == "measured"
        assert snapshot["coordinate"]["contributor_provenance"] == ["unknown"]
        assert snapshot["reconstruction"]["geometry_usability"] == "preview-only"
        assert snapshot["pipeline"]["align"]["trust"] == "untrusted"

    def test_legacy_manifest_is_visible_but_never_promoted_to_metric_or_real(self, tmp_path):
        _write_v2_project(tmp_path)
        legacy = {
            "schema_version": 1,
            "engine": "mock",
            "world_convention": "ENU, Z-up, meters",
            "full_3dgs": "recon/scene_full.ply",
            "gaussian_count": 1,
        }
        (tmp_path / "web/data/recon/recon_manifest.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["coordinate"]["source_frame"] == "unknown"
        assert snapshot["coordinate"]["world_frame"] == "unknown"
        assert snapshot["coordinate"]["units"] == "unknown"
        assert snapshot["coordinate"]["metric_evidence"] == []
        assert snapshot["reconstruction"]["requested_engine"] == "unknown"
        assert snapshot["reconstruction"]["actual_engine"] == "unknown"
        assert snapshot["reconstruction"]["synthetic"] is True
        assert snapshot["reconstruction"]["evidence_status"] == "legacy-manifest"
        assert snapshot["reconstruction"]["artifact"]["kind"] == "legacy-ply"

    def test_asset_hash_mismatch_separates_registration_from_validation(self, tmp_path):
        _write_v2_project(tmp_path)
        (tmp_path / "assets/tree_v1.ply").write_bytes(b"tampered")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["assets"]["registered"] == 1
        assert snapshot["assets"]["consumed"] == 0
        assert snapshot["assets"]["blocked"] == 1
        item = snapshot["assets"]["items"][0]
        assert item["validated"] is False
        assert item["consumed"] is False
        assert item["reason"] == "payload-sha256-mismatch"

    def test_registered_but_unconsumed_assets_are_not_marked_verified(self, tmp_path):
        _write_v2_project(tmp_path)
        world_path = tmp_path / "web/data/manifest.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        world["asset_consumption"] = []
        world_path.write_text(json.dumps(world), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["assets"]["registered"] == 1
        assert snapshot["assets"]["consumed"] == 0
        assert snapshot["assets"]["blocked"] == 1
        assert snapshot["pipeline"]["assets"]["trust"] == "proxy"

    def test_preview_only_geometry_cannot_make_alignment_verified(self, tmp_path):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/recon/recon_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provenance"]["geometry_usability"] = "preview-only"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["reconstruction"]["synthetic"] is False
        assert snapshot["pipeline"]["align"]["trust"] == "untrusted"

    @pytest.mark.parametrize(
        "case",
        [
            "zero-instances", "zero-points", "unknown-chunk", "missing-ply",
            "outside-ply", "empty-ply", "non-ply", "renderer-mismatch",
            "chunk-count-mismatch", "header-only-ply", "points-exceed-chunk",
            "aggregate-points-exceed-chunk",
        ],
    )
    def test_asset_consumption_requires_positive_counts_and_live_chunk(
        self, tmp_path, case
    ):
        _write_v2_project(tmp_path)
        world_path = tmp_path / "web/data/manifest.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        row = world["asset_consumption"][0]

        if case == "zero-instances":
            row["instances"] = 0
        elif case == "zero-points":
            row["point_count"] = 0
        elif case == "unknown-chunk":
            row["chunk_id"] = "missing"
        elif case == "missing-ply":
            world["chunks"][0]["ply_file"] = "missing.ply"
        elif case == "empty-ply":
            chunk_path = tmp_path / "web/data/chunk_0_0.ply"
            chunk_path.write_text(
                "ply\nformat ascii 1.0\nelement vertex 0\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n",
                encoding="ascii",
            )
        elif case == "non-ply":
            (tmp_path / "web/data/chunk_0_0.ply").write_bytes(b"not a ply")
        elif case == "header-only-ply":
            (tmp_path / "web/data/chunk_0_0.ply").write_text(
                "ply\nformat ascii 1.0\nelement vertex 1\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n",
                encoding="ascii",
            )
        elif case == "renderer-mismatch":
            row["renderer"] = "building"
        elif case == "chunk-count-mismatch":
            world["chunks"][0]["point_count"] = 999
        elif case == "points-exceed-chunk":
            row["point_count"] = 90
        elif case == "aggregate-points-exceed-chunk":
            world["asset_consumption"].append(dict(row))
        else:
            outside = tmp_path.parent / f"{tmp_path.name}-outside-chunk.ply"
            _write_ply(outside, properties=("x", "y", "z"))
            world["chunks"][0]["ply_file"] = str(outside)
        world_path.write_text(json.dumps(world), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["assets"]["consumed"] == 0
        assert snapshot["assets"]["blocked"] == 1
        assert snapshot["assets"]["items"][0]["consumed"] is False

    def test_non_finite_live_chunk_cannot_supply_asset_consumption(self, tmp_path):
        _write_v2_project(tmp_path)
        _write_ply(
            tmp_path / "web/data/chunk_0_0.ply",
            properties=SIMPLE_PROPERTIES,
            values={"x": "nan"},
        )

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["assets"]["consumed"] == 0
        assert snapshot["assets"]["blocked"] == 1
        assert snapshot["assets"]["items"][0]["consumed"] is False

    def test_asset_payload_hash_does_not_validate_non_ply_bytes(self, tmp_path):
        _write_v2_project(tmp_path)
        payload = tmp_path / "assets/tree_v1.ply"
        payload.write_bytes(b"not a ply")
        registry_path = tmp_path / "assets/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["assets"]["tree"]["sha256"] = _sha256(payload)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        item = snapshot["assets"]["items"][0]
        assert item["validated"] is False
        assert item["consumed"] is False
        assert item["reason"] == "payload-ply-invalid"

    def test_asset_payload_hash_does_not_validate_non_finite_gaussians(
        self, tmp_path
    ):
        _write_v2_project(tmp_path)
        payload = _write_ply(
            tmp_path / "assets/tree_v1.ply",
            values={"x": "nan"},
        )
        registry_path = tmp_path / "assets/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["assets"]["tree"]["sha256"] = _sha256(payload)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        world_path = tmp_path / "web/data/manifest.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        world["asset_consumption"][0]["sha256"] = _sha256(payload)
        world_path.write_text(json.dumps(world), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        item = snapshot["assets"]["items"][0]
        assert item["validated"] is False
        assert item["consumed"] is False
        assert item["reason"] == "payload-ply-invalid"

    def test_positive_simple_asset_remains_valid_and_consumed(self, tmp_path):
        _write_v2_project(tmp_path)
        payload = _write_ply(
            tmp_path / "assets/tree_v1.ply",
            properties=SIMPLE_PROPERTIES,
        )
        registry_path = tmp_path / "assets/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["assets"]["tree"]["sha256"] = _sha256(payload)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        world_path = tmp_path / "web/data/manifest.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        world["asset_consumption"][0]["sha256"] = _sha256(payload)
        world_path.write_text(json.dumps(world), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        item = snapshot["assets"]["items"][0]
        assert item["validated"] is True
        assert item["consumed"] is True
        assert item.get("reason") is None

    def test_assets_root_symlink_is_not_a_trusted_registry_boundary(self, tmp_path):
        _write_v2_project(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-external-assets"
        (tmp_path / "assets").rename(outside)
        _symlink_or_skip(tmp_path / "assets", outside, target_is_directory=True)

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["assets"]["registered"] == 0
        assert snapshot["assets"]["registry_revision"] == "missing-or-invalid"
        assert snapshot["pipeline"]["assets"]["trust"] == "untrusted"

    @pytest.mark.parametrize("evidence", ["recon-manifest", "world-manifest", "registry"])
    def test_evidence_json_symlinks_are_not_trusted(self, tmp_path, evidence):
        _write_v2_project(tmp_path)
        paths = {
            "recon-manifest": tmp_path / "web/data/recon/recon_manifest.json",
            "world-manifest": tmp_path / "web/data/manifest.json",
            "registry": tmp_path / "assets/registry.json",
        }
        evidence_path = paths[evidence]
        outside = tmp_path.parent / f"{tmp_path.name}-{evidence}.json"
        evidence_path.rename(outside)
        _symlink_or_skip(evidence_path, outside)

        snapshot = build_project_snapshot(tmp_path)

        if evidence == "recon-manifest":
            assert "artifact" not in snapshot["reconstruction"]
            assert snapshot["pipeline"]["align"]["trust"] == "untrusted"
        elif evidence == "world-manifest":
            assert snapshot["assets"]["consumed"] == 0
            assert snapshot["pipeline"]["assets"]["trust"] != "verified"
        else:
            assert snapshot["assets"]["registered"] == 0
            assert snapshot["pipeline"]["assets"]["trust"] == "untrusted"

    def test_duplicate_chunk_ids_cannot_supply_consumption_evidence(self, tmp_path):
        _write_v2_project(tmp_path)
        world_path = tmp_path / "web/data/manifest.json"
        world = json.loads(world_path.read_text(encoding="utf-8"))
        duplicate_path = _write_ply(
            tmp_path / "web/data/chunk_duplicate.ply",
            properties=("x", "y", "z"),
        )
        world["chunks"].append({
            "id": "0_0",
            "ply_file": duplicate_path.name,
            "point_count": 1,
        })
        world_path.write_text(json.dumps(world), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        assert snapshot["assets"]["consumed"] == 0
        assert snapshot["pipeline"]["assets"]["trust"] != "verified"

    @pytest.mark.parametrize("case", ["parent", "absolute", "dot", "symlink"])
    def test_asset_payload_must_resolve_strictly_below_assets_root(self, tmp_path, case):
        _write_v2_project(tmp_path)
        registry_path = tmp_path / "assets/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = registry["assets"]["tree"]
        outside = tmp_path / "recon/scene_full.ply"

        if case == "parent":
            entry["ply"] = "../recon/scene_full.ply"
            entry["sha256"] = _sha256(outside)
        elif case == "absolute":
            entry["ply"] = str(outside)
            entry["sha256"] = _sha256(outside)
        elif case == "dot":
            entry["ply"] = "./tree_v1.ply"
        else:
            alias = tmp_path / "assets/alias.ply"
            _symlink_or_skip(alias, outside)
            entry["ply"] = alias.name
            entry["sha256"] = _sha256(outside)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        item = snapshot["assets"]["items"][0]
        assert item["validated"] is False
        assert item["consumed"] is False
        assert item["reason"] == "payload-path-invalid"


class TestHttpContract:
    @staticmethod
    def _enable_on_demand_world(
        root: Path,
        *,
        world_seed: int = 42,
        uses_assets: bool = False,
        layout_engine: str | None = "mock",
    ) -> None:
        manifest_path = root / "web/data/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["grid"] = {
            "on_demand": True,
            "url_template": "/api/world/chunk/{x}/{y}.ply",
            "world_seed": world_seed,
            "layout_engine": layout_engine,
            "uses_assets": uses_assets,
            "terrain_algorithm_id": TERRAIN_ALGORITHM_ID,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_local_textured_preview_is_exact_hash_verified_and_cacheable(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        _directory, manifest = _write_local_textured_preview(tmp_path)
        manifest_path = (
            f"/api/local-textured-preview/{manifest.preview_id}/manifest.json"
        )
        glb_path = manifest.model_url

        with _running_server(tmp_path) as server:
            manifest_status, manifest_headers, manifest_payload = _request(
                server,
                "GET",
                manifest_path,
            )
            glb_status, glb_headers, glb_payload = _request(server, "GET", glb_path)
            head_status, head_headers, head_payload = _request(
                server,
                "HEAD",
                glb_path,
            )
            cached_status, cached_headers, cached_payload = _request(
                server,
                "GET",
                glb_path,
                headers={"If-None-Match": glb_headers["etag"]},
            )

        assert manifest_status == glb_status == head_status == 200
        assert cached_status == 304
        assert json.loads(manifest_payload) == manifest.model_dump(mode="json")
        assert glb_payload == b"glTF-local-preview"
        assert head_payload == cached_payload == b""
        assert manifest_headers["content-type"] == "application/json; charset=utf-8"
        assert manifest_headers["cache-control"] == "no-store"
        assert glb_headers["content-type"] == "model/gltf-binary"
        assert glb_headers["cache-control"] == "public, max-age=0, must-revalidate"
        assert glb_headers["etag"] == head_headers["etag"] == cached_headers["etag"]
        assert glb_headers["content-length"] == head_headers["content-length"]

    @pytest.mark.parametrize(
        "path",
        (
            "/api/local-textured-preview/../manifest.json",
            "/api/local-textured-preview/%2e%2e/manifest.json",
            f"/api/local-textured-preview/{'a' * 63}/manifest.json",
            f"/api/local-textured-preview/{'a' * 64}/nested/manifest.json",
            f"/api/local-textured-preview/{'f' * 64}/manifest.json",
            f"/api/local-textured-preview/{'a' * 64}/build-report.json",
        ),
    )
    def test_local_textured_preview_rejects_nonexact_routes(
        self,
        tmp_path: Path,
        path: str,
    ) -> None:
        _write_v2_project(tmp_path)
        _write_local_textured_preview(tmp_path)

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(server, "GET", path)

        assert status in {400, 403, 404}
        assert b"glTF-local-preview" not in payload

    @pytest.mark.parametrize("case", ("changed-glb", "noncanonical-manifest", "symlink-root"))
    def test_local_textured_preview_fails_closed_on_private_byte_or_path_tampering(
        self,
        tmp_path: Path,
        case: str,
    ) -> None:
        _write_v2_project(tmp_path)
        directory, manifest = _write_local_textured_preview(tmp_path)
        if case == "changed-glb":
            (directory / "village-canary.glb").write_bytes(b"tampered")
        elif case == "noncanonical-manifest":
            payload = manifest.model_dump(mode="json")
            (directory / "manifest.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
        else:
            previews = directory.parent
            outside = tmp_path.parent / f"{tmp_path.name}-local-previews"
            previews.rename(outside)
            _symlink_or_skip(previews, outside, target_is_directory=True)

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(
                server,
                "GET",
                manifest.model_url,
            )

        assert status in {403, 404, 409, 500}
        assert b"glTF-local-preview" not in payload

    def test_mesh_chunk_and_template_are_verified_cacheable_and_stream_only(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        _directory, bundle, expected_glb = _write_mesh_world_bundle(tmp_path)

        with _running_server(tmp_path) as server:
            status, headers, payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/-2/3.json?lod=1",
            )
            head_status, head_headers, head_payload = _request(
                server,
                "HEAD",
                "/api/world/mesh-chunk/-2/3.json?lod=1",
            )
            cached_status, cached_headers, cached_payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/-2/3.json?lod=1",
                headers={"If-None-Match": headers.get("etag", "")},
            )
            runtime = json.loads(payload)
            asset_url = runtime["asset_urls"][0]["url"]
            asset_status, asset_headers, asset_payload = _request(
                server,
                "GET",
                asset_url,
            )
            asset_head_status, asset_head_headers, asset_head_payload = _request(
                server,
                "HEAD",
                asset_url,
            )
            asset_cached_status, _, asset_cached_payload = _request(
                server,
                "GET",
                asset_url,
                headers={"If-None-Match": asset_headers.get("etag", "")},
            )
            material = runtime["surface_materials"][0]
            map_descriptor = material["base_color"]
            map_status, map_headers, map_payload = _request(
                server,
                "GET",
                map_descriptor["url"],
            )
            map_head_status, map_head_headers, map_head_payload = _request(
                server,
                "HEAD",
                map_descriptor["url"],
            )
            map_cached_status, _, map_cached_payload = _request(
                server,
                "GET",
                map_descriptor["url"],
                headers={"If-None-Match": map_headers.get("etag", "")},
            )

        assert (
            status
            == head_status
            == asset_status
            == asset_head_status
            == map_status
            == map_head_status
            == 200
        )
        assert cached_status == asset_cached_status == map_cached_status == 304
        assert runtime["chunk"]["chunk_id"] == {"x": -2, "y": 3}
        assert runtime["chunk"]["world_offset"] == [-400.0, 600.0, 0.0]
        assert runtime["chunk"]["mesh_asset_bundle_id"] == bundle.bundle_id
        assert all(
            row["url"].startswith(
                f"/api/world/mesh-assets/{bundle.bundle_id}/",
            )
            for row in runtime["asset_urls"]
        )
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert headers["cache-control"] == "no-store"
        assert headers["etag"] == head_headers["etag"] == cached_headers["etag"]
        assert headers["content-length"] == head_headers["content-length"]
        assert asset_headers["content-type"] == "model/gltf-binary"
        assert asset_headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert asset_headers["etag"] == asset_head_headers["etag"]
        assert asset_payload == expected_glb
        assert map_headers["content-type"] == "image/png"
        assert map_headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert map_headers["etag"] == map_head_headers["etag"]
        assert len(map_payload) == map_descriptor["bytes"]
        assert hashlib.sha256(map_payload).hexdigest() == map_descriptor["sha256"]
        assert head_payload == cached_payload == b""
        assert asset_head_payload == asset_cached_payload == b""
        assert map_head_payload == map_cached_payload == b""
        assert not (tmp_path / "web/data/chunk_-2_3.json").exists()

    def test_mesh_v2_runtime_and_texture_route_are_exact_and_fail_closed(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        directory, bundle = _write_mesh_world_bundle_v2(tmp_path)

        with _running_server(tmp_path) as server:
            chunk_status, _chunk_headers, chunk_payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/-2/3.json?lod=2",
            )
            runtime = json.loads(chunk_payload)
            asset = runtime["asset_urls"][0]
            dependency = asset["texture_dependencies"][0]
            asset_status, asset_headers, asset_payload = _request(
                server,
                "GET",
                asset["url"],
            )
            texture_status, texture_headers, texture_payload = _request(
                server,
                "GET",
                dependency["url"],
            )
            head_status, head_headers, head_payload = _request(
                server,
                "HEAD",
                dependency["url"],
            )
            cached_status, cached_headers, cached_payload = _request(
                server,
                "GET",
                dependency["url"],
                headers={"If-None-Match": texture_headers.get("etag", "")},
            )
            invalid_results = [
                _request(server, "GET", path)
                for path in (
                    dependency["url"] + "?download=1",
                    dependency["url"] + "/",
                    dependency["url"].replace(
                        dependency["sha256"],
                        dependency["sha256"].upper(),
                    ),
                    dependency["url"].replace(
                        dependency["sha256"],
                        dependency["sha256"][:-1],
                    ),
                    dependency["url"].replace(
                        f"{dependency['sha256']}.png",
                        "../manifest.json",
                    ),
                    (
                        "/api/world/mesh-assets/"
                        f"{'f' * 64}/textures/{dependency['sha256']}.png"
                    ),
                    (
                        "/api/world/mesh-assets/"
                        f"{bundle.bundle_id}/textures/{'e' * 64}.png"
                    ),
                )
            ]
            target = directory / f"textures/{dependency['sha256']}.png"
            target.write_bytes(target.read_bytes() + b"\0")
            tampered_status, _tampered_headers, tampered_payload = _request(
                server,
                "GET",
                dependency["url"],
            )

        assert chunk_status == 200
        assert runtime["schema_version"] == (
            "nantai.synthetic-village.mesh-chunk-runtime.v2"
        )
        assert asset_status == 200
        assert asset_headers["content-type"] == "model/gltf-binary"
        assert len(asset_payload) == asset["glb_bytes"]
        assert hashlib.sha256(asset_payload).hexdigest() == (
            asset["glb_sha256"]
        )
        assert texture_status == head_status == 200
        assert cached_status == 304
        assert texture_headers["content-type"] == "image/png"
        assert texture_headers["content-length"] == str(len(texture_payload))
        assert texture_headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert texture_headers["x-content-type-options"] == "nosniff"
        assert texture_headers["etag"] == head_headers["etag"]
        assert cached_headers["etag"] == texture_headers["etag"]
        assert texture_headers["etag"] == (
            f"\"sha256:{dependency['sha256']}\""
        )
        assert len(texture_payload) == dependency["bytes"]
        assert hashlib.sha256(texture_payload).hexdigest() == (
            dependency["sha256"]
        )
        assert head_payload == cached_payload == b""
        assert all(status == 404 for status, _headers, _payload in invalid_results)
        assert tampered_status == 500
        assert json.loads(tampered_payload)["error"]["code"] == (
            "mesh_asset_bundle_invalid"
        )

    def test_mesh_v1_bundle_does_not_expose_v2_texture_route(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        _directory, bundle, _glb = _write_mesh_world_bundle(tmp_path)

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(
                server,
                "GET",
                (
                    "/api/world/mesh-assets/"
                    f"{bundle.bundle_id}/textures/{'e' * 64}.png"
                ),
            )

        assert status == 404
        assert json.loads(payload)["error"]["code"] == (
            "mesh_texture_not_found"
        )

    def test_mesh_v3_runtime_and_profile_routes_are_verified(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        _write_v2_project(tmp_path)
        (
            _source_directory,
            _source_bundle,
            material_directory,
            _material_v2,
            mesh_directory,
            mesh_v3,
        ) = _write_mesh_world_bundle_v3(tmp_path, monkeypatch)

        with _running_server(tmp_path) as server:
            chunk_status, chunk_headers, chunk_payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/-2/3.json?lod=2",
            )
            runtime = json.loads(chunk_payload)
            h3 = runtime["profiles"][H3_PROFILE_ID]
            h2 = runtime["profiles"][H2_PROFILE_ID]
            h3_asset = h3["asset_urls"][0]
            h2_asset = h2["asset_urls"][0]
            asset_results = []
            for descriptor in (h3_asset, h2_asset):
                get_result = _request(
                    server,
                    "GET",
                    descriptor["url"],
                )
                head_result = _request(
                    server,
                    "HEAD",
                    descriptor["url"],
                )
                cached_result = _request(
                    server,
                    "GET",
                    descriptor["url"],
                    headers={
                        "If-None-Match": get_result[1]["etag"],
                    },
                )
                asset_results.append(
                    (get_result, head_result, cached_result),
                )
            mesh_dependency = h2_asset["texture_dependencies"][0]
            mesh_dependency_result = _request(
                server,
                "GET",
                mesh_dependency["url"],
            )
            ktx = next(
                row
                for row in h3["textures"]
                if row["media_type"] == "image/ktx2"
            )
            png = next(
                row
                for row in h2["textures"]
                if row["media_type"] == "image/png"
            )
            texture_results = {}
            for descriptor in (ktx, png):
                get_result = _request(
                    server,
                    "GET",
                    descriptor["url"],
                )
                head_result = _request(
                    server,
                    "HEAD",
                    descriptor["url"],
                )
                cached_result = _request(
                    server,
                    "GET",
                    descriptor["url"],
                    headers={
                        "If-None-Match": get_result[1]["etag"],
                    },
                )
                texture_results[descriptor["media_type"]] = (
                    descriptor,
                    get_result,
                    head_result,
                    cached_result,
                )
            invalid_statuses = [
                _request(server, "GET", path)[0]
                for path in (
                    ktx["url"] + "?download=1",
                    ktx["url"] + "/",
                    ktx["url"].replace(H3_PROFILE_ID, "unknown-profile"),
                    ktx["url"].replace(mesh_v3.bundle_id, "f" * 64),
                    ktx["url"].replace(".ktx2", ".png"),
                    ktx["url"].replace(ktx["sha256"], ktx["sha256"].upper()),
                    h3_asset["url"].replace(H3_PROFILE_ID, "unknown-profile"),
                )
            ]

        assert chunk_status == 200
        assert chunk_headers["content-type"] == (
            "application/json; charset=utf-8"
        )
        assert runtime["schema_version"] == (
            "nantai.synthetic-village.mesh-chunk-runtime.v3"
        )
        assert runtime["chunk"]["chunk_id"] == {"x": -2, "y": 3}
        assert runtime["chunk"]["world_offset"] == [-400.0, 600.0, 0.0]
        assert runtime["mesh_asset_bundle_id"] == mesh_v3.bundle_id
        assert set(runtime["profiles"]) == {
            H3_PROFILE_ID,
            H2_PROFILE_ID,
        }
        assert (
            h3_asset["geometry_fingerprint"]
            == h2_asset["geometry_fingerprint"]
        )
        for descriptor, (
            (status, headers, payload),
            (head_status, head_headers, head_payload),
            (cached_status, cached_headers, cached_payload),
        ) in zip(
            (h3_asset, h2_asset),
            asset_results,
            strict=True,
        ):
            assert status == head_status == 200
            assert cached_status == 304
            assert headers["content-type"] == "model/gltf-binary"
            assert headers["content-length"] == str(
                descriptor["glb_bytes"],
            )
            assert headers["etag"] == (
                f"\"sha256:{descriptor['glb_sha256']}\""
            )
            assert head_headers["etag"] == cached_headers["etag"]
            assert len(payload) == descriptor["glb_bytes"]
            assert hashlib.sha256(payload).hexdigest() == (
                descriptor["glb_sha256"]
            )
            assert head_payload == cached_payload == b""
        (
            mesh_dependency_status,
            mesh_dependency_headers,
            mesh_dependency_payload,
        ) = mesh_dependency_result
        assert mesh_dependency_status == 200
        assert mesh_dependency_headers["content-type"] == (
            mesh_dependency["media_type"]
        )
        assert hashlib.sha256(mesh_dependency_payload).hexdigest() == (
            mesh_dependency["sha256"]
        )
        for media_type, (
            descriptor,
            (status, headers, payload),
            (head_status, head_headers, head_payload),
            (cached_status, cached_headers, cached_payload),
        ) in texture_results.items():
            assert status == head_status == 200
            assert cached_status == 304
            assert headers["content-type"] == media_type
            assert headers["content-length"] == str(
                descriptor["bytes"],
            )
            assert headers["etag"] == (
                f"\"sha256:{descriptor['sha256']}\""
            )
            assert head_headers["etag"] == cached_headers["etag"]
            assert hashlib.sha256(payload).hexdigest() == (
                descriptor["sha256"]
            )
            assert head_payload == cached_payload == b""
        assert all(status == 404 for status in invalid_statuses)
        assert not (tmp_path / "web/data/chunk_-2_3.json").exists()

        mesh_extension = (
            "ktx2"
            if mesh_dependency["media_type"] == "image/ktx2"
            else "png"
        )
        mesh_target = (
            mesh_directory
            / "textures"
            / f"{mesh_dependency['sha256']}.{mesh_extension}"
        )
        mesh_original = mesh_target.read_bytes()
        mesh_target.write_bytes(mesh_original + b"tampered")
        with _running_server(tmp_path) as server:
            mesh_tampered_status, _headers, mesh_tampered_payload = (
                _request(
                    server,
                    "GET",
                    mesh_dependency["url"],
                )
            )
        assert mesh_tampered_status == 500
        assert json.loads(mesh_tampered_payload)["error"]["code"] == (
            "mesh_asset_bundle_v3_invalid"
        )
        mesh_target.write_bytes(mesh_original)

        target = material_directory / ktx["url"].rsplit("/", 1)[-1]
        assert not target.exists()
        target = material_directory / f"objects/{ktx['sha256']}.ktx2"
        target.write_bytes(target.read_bytes() + b"tampered")
        with _running_server(tmp_path) as server:
            tampered_status, _headers, tampered_payload = _request(
                server,
                "GET",
                ktx["url"],
            )
        assert tampered_status == 500
        assert json.loads(tampered_payload)["error"]["code"] == (
            "material_bundle_v2_invalid"
        )

    def test_mesh_v3_manifest_requires_exact_counterparts_and_routes(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        _write_v2_project(tmp_path)
        _write_mesh_world_bundle_v3(tmp_path, monkeypatch)
        manifest_path = tmp_path / "web/data/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        wrong_template = json.loads(json.dumps(manifest))
        wrong_template["mesh_grid"]["asset_url_template"] = (
            "/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb"
        )
        manifest_path.write_text(
            json.dumps(wrong_template),
            encoding="utf-8",
        )
        with _running_server(tmp_path) as server:
            wrong_status, _headers, wrong_payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/0/0.json?lod=2",
            )

        missing_counterpart = json.loads(json.dumps(manifest))
        del missing_counterpart["mesh_grid"]["material_bundle_id"]
        manifest_path.write_text(
            json.dumps(missing_counterpart),
            encoding="utf-8",
        )
        with _running_server(tmp_path) as server:
            missing_status, _headers, missing_payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/0/0.json?lod=2",
            )

        assert wrong_status == missing_status == 409
        assert json.loads(wrong_payload)["error"]["code"] == (
            "mesh_on_demand_unavailable"
        )
        assert json.loads(missing_payload)["error"]["code"] == (
            "mesh_on_demand_unavailable"
        )

    def test_mesh_routes_fail_closed_without_exact_manifest_opt_in(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/0/0.json?lod=2",
            )

        assert status == 409
        assert json.loads(payload)["error"]["code"] == "mesh_on_demand_unavailable"

    def test_mesh_routes_reject_tampered_template_bytes(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        directory, _bundle_value, _glb = _write_mesh_world_bundle(tmp_path)
        template = next((directory / "objects").glob("*.glb"))
        template.write_bytes(template.read_bytes() + b"\0")

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/0/0.json?lod=2",
            )

        assert status == 500
        assert json.loads(payload)["error"]["code"] == "mesh_asset_bundle_invalid"

    def test_mesh_routes_reject_tampered_surface_material_bytes(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        _write_mesh_world_bundle(tmp_path)
        material_root = (
            tmp_path
            / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles"
        )
        material_map = next(material_root.glob("*/objects/*.png"))
        material_map.write_bytes(material_map.read_bytes() + b"\0")

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/0/0.json?lod=2",
            )

        assert status == 500
        assert json.loads(payload)["error"]["code"] == "material_bundle_invalid"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/world/mesh-chunk/not-an-int/1.json?lod=2",
            "/api/world/mesh-chunk/1/1.5.json?lod=2",
            "/api/world/mesh-chunk/1/2.json",
            "/api/world/mesh-chunk/1/2.json?lod=3",
            "/api/world/mesh-chunk/1/2.json?lod=0&lod=1",
        ],
    )
    def test_mesh_chunk_rejects_invalid_coordinates_and_query(
        self,
        tmp_path: Path,
        path: str,
    ) -> None:
        _write_v2_project(tmp_path)
        _write_mesh_world_bundle(tmp_path)

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(server, "GET", path)

        assert status == 400
        assert json.loads(payload)["error"]["code"] == "invalid_mesh_chunk_request"

    def test_mesh_chunk_distinguishes_geographic_envelope(
        self,
        tmp_path: Path,
    ) -> None:
        _write_v2_project(tmp_path)
        _write_mesh_world_bundle(tmp_path)

        with _running_server(tmp_path) as server:
            status, _headers, payload = _request(
                server,
                "GET",
                "/api/world/mesh-chunk/0/32001.json?lod=2",
            )

        assert status == 422
        assert json.loads(payload)["error"]["code"] == "mesh_world_bounds_exceeded"

    def test_on_demand_world_chunk_is_deterministic_lod_cacheable_and_stream_only(
        self, tmp_path,
    ):
        _write_v2_project(tmp_path)
        self._enable_on_demand_world(tmp_path)

        with _running_server(tmp_path) as server:
            status, headers, payload = _request(
                server, "GET", "/api/world/chunk/2/-3.ply?lod=0",
            )
            repeat_status, repeat_headers, repeat_payload = _request(
                server, "GET", "/api/world/chunk/2/-3.ply?lod=0",
            )
            lod1_status, lod1_headers, lod1_payload = _request(
                server, "GET", "/api/world/chunk/2/-3.ply?lod=1",
            )
            cached_status, cached_headers, cached_payload = _request(
                server,
                "GET",
                "/api/world/chunk/2/-3.ply?lod=0",
                headers={"If-None-Match": headers["etag"]},
            )
            head_status, head_headers, head_payload = _request(
                server, "HEAD", "/api/world/chunk/2/-3.ply?lod=0",
            )

        assert status == repeat_status == lod1_status == head_status == 200
        assert cached_status == 304
        assert payload.startswith(b"ply\n")
        assert repeat_payload == payload
        assert len(payload) < len(lod1_payload)
        assert headers["content-type"] == "application/octet-stream"
        assert headers["cache-control"] == "public, max-age=0, must-revalidate"
        assert headers["etag"] == repeat_headers["etag"] == head_headers["etag"]
        assert headers["etag"] != lod1_headers["etag"]
        assert headers["content-length"] == head_headers["content-length"] == str(len(payload))
        assert cached_headers["etag"] == headers["etag"]
        assert cached_payload == head_payload == b""
        assert not (tmp_path / "web/data/chunk_2_-3.ply").exists()

    def test_on_demand_world_chunk_uses_asset_registry_when_declared(
        self, tmp_path,
    ):
        from pipeline.assets import AssetRegistry
        from pipeline.mock_assets import seed_registry
        from pipeline.render_chunk_to_ply import render_single_chunk

        _write_v2_project(tmp_path)
        (tmp_path / "assets/registry.json").unlink()
        seed_registry(tmp_path / "assets")
        self._enable_on_demand_world(tmp_path, uses_assets=True)
        registry_path = tmp_path / "assets/registry.json"
        registry_before = registry_path.read_bytes()
        expected = render_single_chunk(
            1, 1, world_seed=42, registry=AssetRegistry(tmp_path / "assets"), lod=0,
        )
        proxy = render_single_chunk(1, 1, world_seed=42, registry=None, lod=0)
        assert expected != proxy

        with _running_server(tmp_path) as server:
            status, _, payload = _request(
                server, "GET", "/api/world/chunk/1/1.ply?lod=0",
            )

        assert status == 200
        assert payload == expected
        assert registry_path.read_bytes() == registry_before

    def test_on_demand_world_declared_assets_missing_registry_fails_closed(
        self, tmp_path,
    ):
        _write_v2_project(tmp_path)
        (tmp_path / "assets/registry.json").unlink()
        self._enable_on_demand_world(tmp_path, uses_assets=True)

        with _running_server(tmp_path) as server:
            status, _, payload = _request(
                server, "GET", "/api/world/chunk/1/1.ply?lod=0",
            )

        assert status == 500
        assert json.loads(payload)["error"]["code"] == "world_chunk_render_failed"

    def test_studio_runtime_enables_valid_world_grid_without_rewriting_manifest(
        self, tmp_path,
    ):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["grid"] = {
            "on_demand": False,
            "url_template": "/api/world/chunk/{x}/{y}.ply",
            "world_seed": 42,
            "layout_engine": "mock",
            "uses_assets": False,
            "terrain_algorithm_id": TERRAIN_ALGORITHM_ID,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with _running_server(tmp_path) as server:
            status, headers, payload = _request(server, "GET", "/web/data/manifest.json")
            chunk_status, _, chunk_payload = _request(
                server, "GET", "/api/world/chunk/-1/0.ply?lod=0",
            )

        runtime_manifest = json.loads(payload)
        persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert status == chunk_status == 200
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert headers["cache-control"] == "no-store"
        assert runtime_manifest["grid"]["on_demand"] is True
        assert persisted_manifest["grid"]["on_demand"] is False
        assert chunk_payload.startswith(b"ply\n")

    def test_on_demand_world_rejects_non_mock_layout_engine(self, tmp_path):
        _write_v2_project(tmp_path)
        self._enable_on_demand_world(tmp_path, layout_engine="glm")

        with _running_server(tmp_path) as server:
            status, _, payload = _request(
                server, "GET", "/api/world/chunk/0/0.ply?lod=2",
            )

        assert status == 409
        assert json.loads(payload)["error"]["code"] == "world_on_demand_unavailable"

    def test_runtime_manifest_forces_invalid_persisted_opt_in_off(self, tmp_path):
        _write_v2_project(tmp_path)
        self._enable_on_demand_world(tmp_path, layout_engine="glm")
        manifest_path = tmp_path / "web/data/manifest.json"

        with _running_server(tmp_path) as server:
            status, _, payload = _request(server, "GET", "/web/data/manifest.json")

        runtime_manifest = json.loads(payload)
        persisted_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert status == 200
        assert runtime_manifest["grid"]["on_demand"] is False
        assert persisted_manifest["grid"]["on_demand"] is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/world/chunk/not-an-int/1.ply",
            "/api/world/chunk/1/1.5.ply",
            "/api/world/chunk/1/2.ply?lod=3",
            "/api/world/chunk/1/2.ply?lod=0&lod=1",
            "/api/world/chunk/1/2.ply?unexpected=1",
        ],
    )
    def test_on_demand_world_chunk_rejects_invalid_coordinates_and_query(
        self, tmp_path, path,
    ):
        _write_v2_project(tmp_path)
        self._enable_on_demand_world(tmp_path)

        with _running_server(tmp_path) as server:
            status, _, payload = _request(server, "GET", path)

        assert status == 400
        assert json.loads(payload)["error"]["code"] == "invalid_world_chunk_request"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/world/chunk/30501/0.ply?lod=2",
            "/api/world/chunk/-149501/0.ply?lod=2",
            "/api/world/chunk/0/32001.ply?lod=2",
            "/api/world/chunk/0/-58001.ply?lod=2",
        ],
    )
    def test_on_demand_world_chunk_distinguishes_geographic_envelope(
        self, tmp_path, path,
    ):
        _write_v2_project(tmp_path)
        self._enable_on_demand_world(tmp_path)

        with _running_server(tmp_path) as server:
            status, _, payload = _request(server, "GET", path)

        assert status == 422
        assert json.loads(payload)["error"]["code"] == "world_bounds_exceeded"

    @pytest.mark.parametrize(
        "grid",
        [
            None,
            {"world_seed": 42},
            {"on_demand": "false", "world_seed": 42},
            {"on_demand": True, "world_seed": None},
            {"on_demand": True, "world_seed": True},
            {
                "on_demand": False,
                "url_template": "/api/world/chunk/{x}/{y}.ply",
                "world_seed": 42,
                "layout_engine": "mock",
            },
            {
                "on_demand": False,
                "url_template": "/api/world/chunk/{x}/{y}.ply",
                "world_seed": 42,
                "layout_engine": "glm",
                "uses_assets": False,
                "terrain_algorithm_id": TERRAIN_ALGORITHM_ID,
            },
            {
                "on_demand": False,
                "url_template": "/api/world/chunk/{x}/{y}.ply",
                "world_seed": 42,
                "layout_engine": "mock",
                "uses_assets": False,
                "terrain_algorithm_id": "mock-flat-ground-v1",
            },
        ],
    )
    def test_on_demand_world_chunk_fails_closed_without_valid_manifest_opt_in(
        self, tmp_path, grid,
    ):
        _write_v2_project(tmp_path)
        manifest_path = tmp_path / "web/data/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if grid is not None:
            manifest["grid"] = grid
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with _running_server(tmp_path) as server:
            status, _, payload = _request(
                server, "GET", "/api/world/chunk/0/0.ply?lod=2",
            )

        assert status == 409
        assert json.loads(payload)["error"]["code"] == "world_on_demand_unavailable"

    def test_api_project_and_runs_have_json_and_security_headers(self, tmp_path):
        _write_v2_project(tmp_path)
        ledger_dir = tmp_path / ".nantai-studio"
        ledger_dir.mkdir()
        ledger_dir.joinpath("runs.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "items": [
                        {
                            "id": "run-local-1",
                            "command": "reconstruct",
                            "status": "succeeded",
                            "adapter_kind": "local",
                            "started_at": "2026-07-14T00:00:00Z",
                            "finished_at": "2026-07-14T00:01:00Z",
                            "artifact_ids": ["recon-scene-full"],
                            "last_event_id": "event-1",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with _running_server(tmp_path) as server:
            status, headers, payload = _request(server, "GET", "/api/project")
            runs_status, runs_headers, runs_payload = _request(server, "GET", "/api/runs")

        assert status == runs_status == 200
        assert json.loads(payload)["schema_version"] == 2
        runs = json.loads(runs_payload)
        assert [item["id"] for item in runs["items"]] == ["run-local-1"]
        assert isinstance(runs["cursor"], str) and runs["cursor"]
        for actual in (headers, runs_headers):
            assert actual["content-type"] == "application/json; charset=utf-8"
            assert actual["cache-control"] == "no-store"
            assert actual["x-content-type-options"] == "nosniff"
            assert actual["referrer-policy"] == "no-referrer"
            assert "default-src 'self'" in actual["content-security-policy"]
            assert "https://cdn.jsdelivr.net" in actual["content-security-policy"]
            assert "'wasm-unsafe-eval'" in actual["content-security-policy"]
            assert "connect-src 'self' data: blob:" in actual["content-security-policy"]

    def test_static_files_are_root_relative_with_mime_and_no_directory_listing(self, tmp_path):
        _write_v2_project(tmp_path)

        with _running_server(tmp_path) as server:
            status, headers, payload = _request(server, "GET", "/web/studio/app.mjs")
            directory_status, _, _ = _request(server, "GET", "/assets/")

        assert status == 200
        assert headers["content-type"] == "text/javascript; charset=utf-8"
        assert headers["cache-control"] == "no-cache"
        assert payload == b"export const ok = true;"
        assert directory_status == 404

    def test_path_traversal_and_symlink_escape_are_blocked(self, tmp_path):
        _write_v2_project(tmp_path)
        outside = tmp_path.parent / "outside-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        _symlink_or_skip(tmp_path / "web/escape.txt", outside)

        with pytest.raises(PathAccessError):
            resolve_static_path(tmp_path, "/%2e%2e/outside-secret.txt")
        with pytest.raises(PathAccessError):
            resolve_static_path(tmp_path, "/web/escape.txt")

        with _running_server(tmp_path) as server:
            traversal_status, _, traversal_payload = _request(
                server, "GET", "/%2e%2e/outside-secret.txt"
            )
            symlink_status, _, symlink_payload = _request(server, "GET", "/web/escape.txt")

        assert traversal_status == symlink_status == 403
        assert json.loads(traversal_payload)["error"]["code"] == "path_forbidden"
        assert json.loads(symlink_payload)["error"]["code"] == "path_forbidden"

    def test_approved_static_root_itself_cannot_be_a_symlink(self, tmp_path):
        (tmp_path / "input").mkdir()
        (tmp_path / "input/secret.txt").write_text("secret", encoding="utf-8")
        _symlink_or_skip(tmp_path / "web", tmp_path, target_is_directory=True)

        with pytest.raises(PathAccessError):
            resolve_static_path(tmp_path, "/web/input/secret.txt")

        with _running_server(tmp_path) as server:
            status, _, payload = _request(server, "GET", "/web/input/secret.txt")

        assert status == 403
        assert json.loads(payload)["error"]["code"] == "path_forbidden"

    def test_directory_index_symlink_is_rechecked_before_serving(self, tmp_path):
        _write_v2_project(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-outside-index.html"
        outside.write_text("outside", encoding="utf-8")
        directory = tmp_path / "web/leak"
        directory.mkdir()
        _symlink_or_skip(directory / "index.html", outside)

        with _running_server(tmp_path) as server:
            status, _, payload = _request(server, "GET", "/web/leak/")

        assert status == 403
        assert json.loads(payload)["error"]["code"] == "path_forbidden"

    def test_non_get_methods_return_structured_error_without_starting_jobs(self, tmp_path):
        _write_v2_project(tmp_path)

        with _running_server(tmp_path) as server:
            status, headers, payload = _request(
                server, "POST", "/api/jobs", body=b'{"command":"reconstruct"}'
            )

        assert status == 405
        assert headers["allow"] == "GET, HEAD"
        error = json.loads(payload)
        assert error == {
            "schema_version": 1,
            "error": {
                "code": "method_not_allowed",
                "message": "This Studio server is read-only; no job was started.",
                "status": 405,
            },
        }
