"""Read-only Studio server contracts.

The server is deliberately a snapshot adapter, not a reconstruction launcher.
Tests use only files on disk so missing or legacy evidence cannot be promoted to
"real" state by an engine-name heuristic.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
import warnings
from contextlib import contextmanager
from pathlib import Path

import pytest

from pipeline.studio_server import (
    PathAccessError,
    build_project_snapshot,
    make_server,
    resolve_static_path,
)

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


def _request(server, method: str, path: str, body: bytes | None = None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request(method, path, body=body)
    response = connection.getresponse()
    payload = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, headers, payload


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
        (tmp_path / "assets").symlink_to(outside, target_is_directory=True)

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
        evidence_path.symlink_to(outside)

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
            alias.symlink_to(outside)
            entry["ply"] = alias.name
            entry["sha256"] = _sha256(outside)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        snapshot = build_project_snapshot(tmp_path)

        item = snapshot["assets"]["items"][0]
        assert item["validated"] is False
        assert item["consumed"] is False
        assert item["reason"] == "payload-path-invalid"


class TestHttpContract:
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
            assert "connect-src 'self' data:" in actual["content-security-policy"]

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
        (tmp_path / "web/escape.txt").symlink_to(outside)

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
        (tmp_path / "web").symlink_to(tmp_path, target_is_directory=True)

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
        directory.joinpath("index.html").symlink_to(outside)

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
