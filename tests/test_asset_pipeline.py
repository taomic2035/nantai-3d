"""Asset pipeline contracts: content addressing, deterministic generation and use."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

import pipeline.assets as assets_module
from pipeline.assets import AssetRegistry
from pipeline.gaussian_scene import GaussianScene
from pipeline.render_chunk_to_ply import (
    VEGETATION_POINT_BUDGET,
    build_chunk_array,
    render_chunkset,
)
from pipeline.schema import ChunkLayout
from pipeline.validate_handoff import validate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_asset(path: Path, seed: int, *, n: int = 900) -> Path:
    rng = np.random.default_rng(seed)
    xyz = np.column_stack(
        [
            rng.uniform(-1.5, 1.5, n),
            rng.uniform(-1.5, 1.5, n),
            rng.uniform(0.0, 5.0, n),
        ]
    )
    rgb = np.clip(rng.normal(0.45 + seed * 0.01, 0.12, (n, 3)), 0, 1)
    scene = GaussianScene(
        xyz,
        rgb,
        rng.uniform(0.65, 0.95, n),
        rng.uniform(0.03, 0.14, (n, 3)),
    )
    scene.save_ply(path, flavor="3dgs")
    return path


def _disk_snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(item.relative_to(path)): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _v2_manifest(*, item: dict, handoff_id: str = "HANDOFF-SECURITY") -> dict:
    return {
        "schema_version": 2,
        "handoff_id": handoff_id,
        "coordinate_system": {"units": "meters", "axes": "local-z-up"},
        "generator": {"name": "test", "version": "1"},
        "items": [item],
    }


def _layout(*, vegetation=None, buildings=None, props=None) -> ChunkLayout:
    return ChunkLayout(
        **{
            "chunk_id": {"x": 0, "y": 0},
            "world_seed": 123,
            "geo_origin": {"lat": 26.0, "lon": 119.0, "alt": 50.0},
            "terrain": {
                "heightmap": "terrain.png",
                "elevation_range": [0, 10],
                "material_zones": [],
            },
            "vegetation": vegetation or [],
            "buildings": buildings or [],
            "props": props or [],
        }
    )


class TestContentAddressedRegistry:
    def test_registering_same_sha_is_idempotent(self, tmp_path):
        source = _write_asset(tmp_path / "tree.ply", 1)
        reg = AssetRegistry(tmp_path / "assets")

        first = reg.register("tree", source, kind="vegetation", origin="gpt-mock")
        registry_bytes = reg.registry_path.read_bytes()
        second = reg.register("tree", source, kind="vegetation", origin="gpt-mock")

        assert first.version == second.version == 1
        assert second.sha256 == _sha256(source)
        assert second.history == []
        assert reg.registry_path.read_bytes() == registry_bytes

    def test_same_sha_recovers_missing_payload_without_version_bump(self, tmp_path):
        source = _write_asset(tmp_path / "tree.ply", 2)
        reg = AssetRegistry(tmp_path / "assets")
        entry = reg.register("tree", source, kind="vegetation")
        registry_bytes = reg.registry_path.read_bytes()
        reg.resolve("tree").unlink()

        recovered = reg.register("tree", source, kind="vegetation")

        assert recovered.version == entry.version == 1
        assert reg.resolve("tree").is_file()
        assert _sha256(reg.resolve("tree")) == _sha256(source)
        assert reg.registry_path.read_bytes() == registry_bytes

    def test_same_sha_repairs_corrupt_payload_without_version_bump(self, tmp_path):
        source = _write_asset(tmp_path / "tree.ply", 13)
        reg = AssetRegistry(tmp_path / "assets")
        entry = reg.register("tree", source, kind="vegetation")
        registry_bytes = reg.registry_path.read_bytes()
        reg.resolve("tree").write_bytes(b"corrupt")

        repaired = reg.register("tree", source, kind="vegetation")

        assert repaired.version == entry.version == 1
        assert _sha256(reg.resolve("tree")) == _sha256(source)
        assert reg.registry_path.read_bytes() == registry_bytes

    def test_different_sha_creates_exactly_one_new_version(self, tmp_path):
        v1 = _write_asset(tmp_path / "tree-v1.ply", 3)
        v2 = _write_asset(tmp_path / "tree-v2.ply", 4)
        reg = AssetRegistry(tmp_path / "assets")
        first = reg.register("tree", v1, kind="vegetation")

        second = reg.register("tree", v2, kind="vegetation", origin="real")

        assert first.version == 1
        assert second.version == 2
        assert second.sha256 == _sha256(v2)
        assert len(second.history) == 1
        assert second.history[0].version == 1
        assert second.history[0].sha256 == _sha256(v1)
        assert reg.resolve("tree").name == "tree_v2.ply"

    def test_expected_version_prevents_lost_update(self, tmp_path):
        v1 = _write_asset(tmp_path / "tree-v1.ply", 5)
        v2 = _write_asset(tmp_path / "tree-v2.ply", 6)
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("tree", v1)

        with pytest.raises(ValueError, match="expected version 0.*actual 1"):
            reg.replace("tree", v2, expected_version=0)

        assert reg.doc.assets["tree"].version == 1
        assert reg.doc.assets["tree"].sha256 == _sha256(v1)

    @pytest.mark.parametrize(
        "asset_id",
        ["../escape", "UPPER", "nested/name", ".hidden", "has space", "a" * 65],
    )
    def test_register_rejects_noncanonical_asset_ids(self, tmp_path, asset_id):
        source = _write_asset(tmp_path / "tree.ply", 31)
        reg = AssetRegistry(tmp_path / "assets")

        with pytest.raises(ValueError, match="asset_id"):
            reg.register(asset_id, source)

        assert not (tmp_path / "escape_v1.ply").exists()

    def test_registry_rejects_payload_path_outside_assets_directory(self, tmp_path):
        assets = tmp_path / "assets"
        assets.mkdir()
        outside = _write_asset(tmp_path / "outside.ply", 32)
        (assets / "registry.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "assets": {
                        "tree": {
                            "kind": "vegetation",
                            "ply": "../outside.ply",
                            "version": 1,
                            "sha256": _sha256(outside),
                        }
                    },
                }
            )
        )

        with pytest.raises(ValueError, match="越出素材目录"):
            AssetRegistry(assets)

    def test_replace_copy_failure_leaves_memory_and_disk_unchanged(
        self, tmp_path, monkeypatch
    ):
        v1 = _write_asset(tmp_path / "tree-v1.ply", 33)
        v2 = _write_asset(tmp_path / "tree-v2.ply", 34)
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("tree", v1)
        before_doc = reg.doc.model_dump(mode="json")
        before_disk = _disk_snapshot(reg.assets_dir)

        def fail_copy(*_args, **_kwargs):
            raise OSError("injected copy failure")

        monkeypatch.setattr(assets_module.shutil, "copy2", fail_copy)
        with pytest.raises(OSError, match="injected copy failure"):
            reg.replace("tree", v2, expected_version=1)

        assert reg.doc.model_dump(mode="json") == before_doc
        assert _disk_snapshot(reg.assets_dir) == before_disk

    def test_replace_registry_write_failure_rolls_back_new_payload(
        self, tmp_path, monkeypatch
    ):
        v1 = _write_asset(tmp_path / "tree-v1.ply", 46)
        v2 = _write_asset(tmp_path / "tree-v2.ply", 47)
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("tree", v1)
        before_doc = reg.doc.model_dump(mode="json")
        before_disk = _disk_snapshot(reg.assets_dir)
        real_replace = assets_module.os.replace

        def fail_registry_replace(source, destination):
            if Path(destination) == reg.registry_path:
                raise OSError("injected registry failure")
            return real_replace(source, destination)

        monkeypatch.setattr(assets_module.os, "replace", fail_registry_replace)
        with pytest.raises(OSError, match="injected registry failure"):
            reg.replace("tree", v2, expected_version=1)

        assert reg.doc.model_dump(mode="json") == before_doc
        assert _disk_snapshot(reg.assets_dir) == before_disk

    def test_expected_version_is_checked_against_disk_across_instances(self, tmp_path):
        v1 = _write_asset(tmp_path / "tree-v1.ply", 35)
        v2 = _write_asset(tmp_path / "tree-v2.ply", 36)
        v3 = _write_asset(tmp_path / "tree-v3.ply", 37)
        first = AssetRegistry(tmp_path / "assets")
        first.register("tree", v1)
        stale = AssetRegistry(tmp_path / "assets")

        first.replace("tree", v2, expected_version=1)
        before_stale_doc = stale.doc.model_dump(mode="json")
        before_disk = _disk_snapshot(first.assets_dir)

        with pytest.raises(ValueError, match="expected version 1.*actual 2"):
            stale.replace("tree", v3, expected_version=1)

        assert stale.doc.model_dump(mode="json") == before_stale_doc
        assert _disk_snapshot(first.assets_dir) == before_disk

    def test_stale_save_cannot_rollback_a_newer_registry_revision(self, tmp_path):
        v1 = _write_asset(tmp_path / "tree-v1.ply", 48)
        v2 = _write_asset(tmp_path / "tree-v2.ply", 49)
        writer = AssetRegistry(tmp_path / "assets")
        writer.register("tree", v1)
        stale = AssetRegistry(tmp_path / "assets")
        writer.replace("tree", v2, expected_version=1)

        with pytest.raises(ValueError, match="registry changed since load"):
            stale.save()

        fresh = AssetRegistry(tmp_path / "assets")
        entry = fresh.doc.assets["tree"]
        assert entry.version == 2
        assert entry.ply == "tree_v2.ply"
        assert entry.sha256 == _sha256(v2)
        assert [version.version for version in entry.history] == [1]
        assert (fresh.assets_dir / "tree_v2.ply").is_file()

    def test_load_and_instantiate_fail_closed_on_sha_mismatch(self, tmp_path):
        source = _write_asset(tmp_path / "tree.ply", 38)
        replacement = _write_asset(tmp_path / "other.ply", 39)
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("tree", source)
        reg.resolve("tree").write_bytes(replacement.read_bytes())

        assert reg.load_scene("tree") is None
        assert reg.instantiate("tree", (0, 0)) is None

    def test_load_fails_closed_when_legacy_entry_has_no_verifiable_sha(self, tmp_path):
        source = _write_asset(tmp_path / "tree.ply", 40)
        reg = AssetRegistry(tmp_path / "assets")
        reg.register("tree", source)
        reg.doc.assets["tree"].sha256 = ""
        reg.save()

        assert reg.load_scene("tree") is None


class TestPortableDeliverable:
    def test_generator_is_deterministic_and_manifest_hashes_payloads(self, tmp_path):
        script = (
            Path(__file__).parents[1]
            / "handoff/deliverables/HANDOFF-001/scripts/generate.py"
        )
        runs = []
        for name in ("first", "second"):
            output = tmp_path / name
            subprocess.run(
                [sys.executable, str(script), "--output", str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            manifest = json.loads((output / "manifest.json").read_text())
            assert manifest["schema_version"] == 2
            assert manifest["coordinate_system"] == {
                "units": "meters",
                "axes": "local-z-up",
            }
            assert manifest["generator"]["version"]
            declared = {item["asset_id"]: item["sha256"] for item in manifest["items"]}
            actual = {
                item["asset_id"]: _sha256(output / item["ply"])
                for item in manifest["items"]
            }
            assert declared == actual
            assert len(actual) == 11
            runs.append(actual)

        assert runs[0] == runs[1]

    def test_validation_rejects_payload_hash_mismatch(self, tmp_path):
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        payload = _write_asset(deliverable / "tree.ply", 7)
        manifest = _v2_manifest(
            handoff_id="HANDOFF-HASH",
            item={
                "asset_id": "tree",
                "kind": "vegetation",
                "ply": payload.name,
                "footprint_m": [3, 3, 5],
                "sha256": "0" * 64,
            },
        )
        (deliverable / "manifest.json").write_text(json.dumps(manifest))

        result = validate(deliverable, feedback_dir=tmp_path / "feedback")

        assert not result["all_pass"]
        assert any("SHA-256" in issue for issue in result["results"]["tree"])

    def test_v2_manifest_requires_explicit_metric_local_z_up_coordinates(self, tmp_path):
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        payload = _write_asset(deliverable / "tree.ply", 41)
        item = {
            "asset_id": "tree",
            "kind": "vegetation",
            "ply": payload.name,
            "footprint_m": [3, 3, 5],
            "sha256": _sha256(payload),
        }
        manifest = _v2_manifest(item=item)
        manifest.pop("coordinate_system")
        (deliverable / "manifest.json").write_text(json.dumps(manifest))

        result = validate(deliverable, feedback_dir=tmp_path / "feedback")

        assert not result["all_pass"]
        assert result["fatal"] and "coordinate_system" in result["fatal"]

    def test_duplicate_asset_ids_are_a_fatal_manifest_error(self, tmp_path):
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        payload = _write_asset(deliverable / "tree.ply", 42)
        item = {
            "asset_id": "tree",
            "kind": "vegetation",
            "ply": payload.name,
            "footprint_m": [3, 3, 5],
            "sha256": _sha256(payload),
        }
        manifest = _v2_manifest(item=item)
        manifest["items"].append(dict(item))
        (deliverable / "manifest.json").write_text(json.dumps(manifest))

        result = validate(deliverable, feedback_dir=tmp_path / "feedback")

        assert not result["all_pass"]
        assert result["fatal"] and "重复" in result["fatal"]

    @pytest.mark.parametrize(
        "footprint",
        [[3, 0, 5], [3, -1, 5], [3, 3], [3, 3, float("nan")]],
    )
    def test_invalid_footprints_are_rejected_by_manifest_schema(
        self, tmp_path, footprint
    ):
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        payload = _write_asset(deliverable / "tree.ply", 43)
        manifest = _v2_manifest(
            item={
                "asset_id": "tree",
                "kind": "vegetation",
                "ply": payload.name,
                "footprint_m": footprint,
                "sha256": _sha256(payload),
            }
        )
        (deliverable / "manifest.json").write_text(json.dumps(manifest))

        result = validate(deliverable, feedback_dir=tmp_path / "feedback")

        assert not result["all_pass"]
        assert result["fatal"] and "footprint_m" in result["fatal"]

    @pytest.mark.parametrize("field", ["xyz", "scale", "quat"])
    def test_nonfinite_or_invalid_gaussian_fields_fail_validation(
        self, tmp_path, field
    ):
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        payload = _write_asset(deliverable / "tree.ply", 44)
        # mmap=False: the default memory-map keeps tree.ply open, and Windows
        # refuses to reopen a mapped file for writing (EINVAL) on the rewrite below.
        ply = PlyData.read(str(payload), mmap=False)
        vertex = ply["vertex"].data
        if field == "xyz":
            vertex["x"][0] = np.nan
        elif field == "scale":
            vertex["scale_0"][0] = np.nan
        else:
            for name in ("rot_0", "rot_1", "rot_2", "rot_3"):
                vertex[name][0] = 0.0
        PlyData([PlyElement.describe(vertex, "vertex")], byte_order="<").write(
            str(payload)
        )
        manifest = _v2_manifest(
            item={
                "asset_id": "tree",
                "kind": "vegetation",
                "ply": payload.name,
                "footprint_m": [3, 3, 5],
                "sha256": _sha256(payload),
            }
        )
        (deliverable / "manifest.json").write_text(json.dumps(manifest))

        result = validate(deliverable, feedback_dir=tmp_path / "feedback")

        assert not result["all_pass"]
        assert any(
            marker in result["results"]["tree"][0]
            for marker in ("finite", "四元数", "scale")
        )

    def test_v1_deliverable_can_validate_but_cannot_be_registered(self, tmp_path):
        deliverable = tmp_path / "deliverable"
        deliverable.mkdir()
        payload = _write_asset(deliverable / "tree.ply", 45)
        (deliverable / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "handoff_id": "HANDOFF-LEGACY",
                    "items": [
                        {
                            "asset_id": "tree",
                            "kind": "vegetation",
                            "ply": payload.name,
                            "footprint_m": [3, 3, 5],
                        }
                    ],
                }
            )
        )

        result = validate(
            deliverable,
            feedback_dir=tmp_path / "feedback",
            do_register=True,
            assets_dir=tmp_path / "assets",
        )

        assert not result["all_pass"]
        assert result["fatal"] and "schema_version 1" in result["fatal"]
        assert result["registered"] == []


class TestVegetationAssetConsumption:
    def _registry(self, tmp_path) -> AssetRegistry:
        reg = AssetRegistry(tmp_path / "assets")
        reg.register(
            "tree_a",
            _write_asset(tmp_path / "tree-a.ply", 8, n=1800),
            kind="vegetation",
        )
        reg.register(
            "tree_b",
            _write_asset(tmp_path / "tree-b.ply", 9, n=1500),
            kind="vegetation",
        )
        return reg

    def test_vegetation_uses_declared_assets_deterministically_with_budget(self, tmp_path):
        reg = self._registry(tmp_path)
        layout = _layout(
            vegetation=[
                {
                    "id": "grove",
                    "center": [80, 90],
                    "radius": 20,
                    "density": 0.9,
                    "asset_ids": ["tree_a", "tree_b"],
                }
            ]
        )
        first_report: list[dict] = []
        second_report: list[dict] = []

        first = build_chunk_array(layout, registry=reg, consumption=first_report)
        second = build_chunk_array(layout, registry=reg, consumption=second_report)

        assert np.array_equal(first, second)
        assert first_report == second_report
        used = {row["asset_id"] for row in first_report}
        assert used == {"tree_a", "tree_b"}
        vegetation_points = len(first) - 4000
        assert 0 < vegetation_points <= VEGETATION_POINT_BUDGET
        assert sum(row["point_count"] for row in first_report) == vegetation_points

    def test_radius_and_density_control_instances_but_budget_caps_points(self, tmp_path):
        reg = self._registry(tmp_path)
        sparse = _layout(
            vegetation=[
                {
                    "id": "grove",
                    "center": [80, 90],
                    "radius": 5,
                    "density": 0.2,
                    "asset_ids": ["tree_a"],
                }
            ]
        )
        dense = _layout(
            vegetation=[
                {
                    "id": "grove",
                    "center": [80, 90],
                    "radius": 25,
                    "density": 1.0,
                    "asset_ids": ["tree_a"],
                }
            ]
        )
        sparse_report: list[dict] = []
        dense_report: list[dict] = []

        sparse_arr = build_chunk_array(sparse, registry=reg, consumption=sparse_report)
        dense_arr = build_chunk_array(dense, registry=reg, consumption=dense_report)

        assert sparse_report[0]["instances"] < dense_report[0]["instances"]
        assert len(sparse_arr) - 4000 <= VEGETATION_POINT_BUDGET
        assert len(dense_arr) - 4000 <= VEGETATION_POINT_BUDGET

    def test_replacing_tree_changes_same_layout_output(self, tmp_path):
        reg = self._registry(tmp_path)
        layout = _layout(
            vegetation=[
                {
                    "id": "grove",
                    "center": [80, 90],
                    "radius": 12,
                    "density": 0.6,
                    "asset_ids": ["tree_a"],
                }
            ]
        )
        before = build_chunk_array(layout, registry=reg)

        replacement = _write_asset(tmp_path / "tree-a-real.ply", 12, n=1800)
        reg.replace("tree_a", replacement, origin="real", expected_version=1)
        after = build_chunk_array(layout, registry=reg)

        assert not np.array_equal(before, after)

    def test_render_manifest_reports_each_consumed_asset(self, tmp_path):
        reg = self._registry(tmp_path)
        reg.register(
            "house",
            _write_asset(tmp_path / "house.ply", 10),
            kind="building",
        )
        reg.register(
            "lamp",
            _write_asset(tmp_path / "lamp.ply", 11),
            kind="prop",
        )
        layout = _layout(
            buildings=[
                {
                    "id": "home",
                    "asset_id": "house",
                    "pos": [40, 50],
                    "rot_z": 0,
                    "scale": 1,
                }
            ],
            vegetation=[
                {
                    "id": "grove",
                    "center": [80, 90],
                    "radius": 6,
                    "density": 0.3,
                    "asset_ids": ["tree_a"],
                }
            ],
            props=[{"id": "light", "asset_id": "lamp", "pos": [60, 60]}],
        )
        layouts = tmp_path / "layouts"
        layouts.mkdir()
        (layouts / "chunk_0_0.json").write_text(layout.model_dump_json())

        manifest = render_chunkset(
            layouts_dir=layouts,
            output_dir=tmp_path / "output",
            chunk_range=(0, 1, 0, 1),
            assets_dir=reg.assets_dir,
            lod_levels={},
        )

        report = manifest["asset_consumption"]
        by_asset = {row["asset_id"]: row for row in report}
        assert set(by_asset) == {"house", "tree_a", "lamp"}
        assert {row["renderer"] for row in report} == {
            "building",
            "vegetation",
            "prop",
        }
        assert all(row["chunk_id"] == "0_0" for row in report)
        assert all(row["instances"] >= 1 for row in report)
        assert all(row["sha256"] == reg.doc.assets[aid].sha256 for aid, row in by_asset.items())

    def test_corrupt_asset_is_not_consumed_or_reported(self, tmp_path):
        reg = self._registry(tmp_path)
        reg.resolve("tree_a").write_bytes(b"corrupt")
        layout = _layout(
            vegetation=[
                {
                    "id": "grove",
                    "center": [80, 90],
                    "radius": 8,
                    "density": 0.4,
                    "asset_ids": ["tree_a"],
                }
            ]
        )
        report: list[dict] = []

        result = build_chunk_array(layout, registry=reg, consumption=report)

        assert len(result) > 4000  # deterministic proxy remains available
        assert report == []


def test_simple_normals_are_known_properties_not_duplicate_extras(tmp_path):
    n = 4
    arr = np.zeros(
        n,
        dtype=[
            ("x", "f4"),
            ("y", "f4"),
            ("z", "f4"),
            ("nx", "f4"),
            ("ny", "f4"),
            ("nz", "f4"),
            ("r", "u1"),
            ("g", "u1"),
            ("b", "u1"),
            ("scale", "f4"),
        ],
    )
    arr["nx"] = 1.0
    arr["r"], arr["g"], arr["b"] = 64, 128, 192
    arr["scale"] = 0.1
    simple = tmp_path / "simple-with-normals.ply"
    PlyData([PlyElement.describe(arr, "vertex")], byte_order="<").write(str(simple))

    scene = GaussianScene.load_ply(simple)
    output = tmp_path / "converted-3dgs.ply"
    scene.save_ply(output, flavor="3dgs")

    assert np.allclose(GaussianScene.load_ply(output).normals[:, 0], 1.0)
