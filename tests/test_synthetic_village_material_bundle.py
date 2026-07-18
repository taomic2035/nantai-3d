"""Deterministic derived PBR material-bundle contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pipeline.synthetic_village.defaults import load_default_visual_slots
from pipeline.synthetic_village.material_bundle import (
    MATERIAL_PARAMETERS,
    MaterialBundleError,
    canonical_material_bundle_bytes,
    prepare_material_bundle,
    verify_prepared_material_bundle,
)
from pipeline.synthetic_village.visual_sources import (
    VisualSourceManifest,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)
from tests.synthetic_material_fixtures import write_material_visual_pack


@pytest.fixture(scope="module")
def prepared_bundle(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("derived-material-bundle")
    visual_root = write_material_visual_pack(root / "visual")
    prepared = prepare_material_bundle(
        visual_pack_root=visual_root,
        staging_root=root / "staging",
    )
    return visual_root, prepared


def test_parameters_cover_exact_material_slot_contract() -> None:
    expected = {
        slot.slot_id
        for slot in load_default_visual_slots().slots
        if slot.category == "material"
    }

    assert set(MATERIAL_PARAMETERS) == expected
    assert len(MATERIAL_PARAMETERS) == 24


def test_prepare_derives_three_content_addressed_maps_for_all_slots(
    prepared_bundle,
) -> None:
    _, prepared = prepared_bundle
    manifest = prepared.manifest

    assert len(manifest.records) == 24
    assert [row.slot_id for row in manifest.records] == sorted(
        row.slot_id for row in manifest.records
    )
    for row in manifest.records:
        assert row.base_color.color_space == "srgb"
        assert row.normal.color_space == "non-color"
        assert row.orm.color_space == "non-color"
        for descriptor in (row.base_color, row.normal, row.orm):
            assert descriptor.width == descriptor.height == 1024
            assert descriptor.media_type == "image/png"
            assert descriptor.object_path == f"objects/{descriptor.sha256}.png"
            path = prepared.staging_root / descriptor.object_path
            assert path.is_file()
            payload = path.read_bytes()
            assert len(payload) == descriptor.bytes
            assert hashlib.sha256(payload).hexdigest() == descriptor.sha256
    assert verify_prepared_material_bundle(prepared.staging_root) == manifest
    canonical_without_id = canonical_material_bundle_bytes(
        manifest,
        exclude_bundle_id=True,
    )
    assert hashlib.sha256(canonical_without_id).hexdigest() == manifest.bundle_id
    assert canonical_material_bundle_bytes(manifest).endswith(b"\n")
    assert b".nantai-studio" not in canonical_material_bundle_bytes(manifest)


def test_base_color_is_repeatable_without_a_hard_edge(prepared_bundle) -> None:
    _, prepared = prepared_bundle
    image = prepared.open_map(prepared.manifest.records[0].base_color)
    pixels = np.asarray(image)

    assert np.array_equal(pixels[:, 0, :], pixels[:, -1, :])
    assert np.array_equal(pixels[0, :, :], pixels[-1, :, :])


def test_normal_and_orm_maps_have_bounded_physical_channels(prepared_bundle) -> None:
    _, prepared = prepared_bundle
    record = prepared.manifest.records[0]
    normal = np.asarray(prepared.open_map(record.normal), dtype=np.uint8)
    orm = np.asarray(prepared.open_map(record.orm), dtype=np.uint8)

    assert normal.shape == orm.shape == (1024, 1024, 3)
    decoded = normal.astype(np.float64) / 127.5 - 1.0
    lengths = np.linalg.norm(decoded, axis=2)
    assert np.isfinite(decoded).all()
    assert np.all(normal[..., 2] >= 128)
    assert float(np.min(lengths)) >= 0.98
    assert float(np.max(lengths)) <= 1.02
    assert int(orm.min()) >= 0
    assert int(orm.max()) <= 255
    assert np.unique(orm[..., 1]).size > 1


def test_identical_sources_produce_identical_manifest_and_objects(
    prepared_bundle,
    tmp_path: Path,
) -> None:
    visual_root, first = prepared_bundle
    second = prepare_material_bundle(
        visual_pack_root=visual_root,
        staging_root=tmp_path / "repeat",
    )

    assert second.manifest == first.manifest
    assert canonical_material_bundle_bytes(second.manifest) == (
        canonical_material_bundle_bytes(first.manifest)
    )
    for descriptor in (
        map_descriptor
        for row in first.manifest.records
        for map_descriptor in (row.base_color, row.normal, row.orm)
    ):
        assert (first.staging_root / descriptor.object_path).read_bytes() == (
            second.staging_root / descriptor.object_path
        ).read_bytes()


def test_replacing_one_source_changes_only_that_slot_and_bundle_identity(
    prepared_bundle,
    tmp_path: Path,
) -> None:
    visual_root, baseline = prepared_bundle
    replacement_root = write_material_visual_pack(tmp_path / "replacement")
    manifest_path = replacement_root / "visual-sources.json"
    manifest = load_visual_source_manifest(manifest_path)
    target = "material-fieldstone-01"
    original = next(row for row in manifest.records if row.slot_id == target)
    replacement_image = Image.new("RGB", (12, 8), (241, 17, 89))
    temporary = replacement_root / "replacement.png"
    replacement_image.save(temporary, format="PNG", compress_level=9, optimize=False)
    payload = temporary.read_bytes()
    temporary.unlink()
    digest = hashlib.sha256(payload).hexdigest()
    object_path = f"objects/{digest}.png"
    (replacement_root / object_path).write_bytes(payload)
    replaced = original.model_copy(
        update={
            "object_path": object_path,
            "sha256": digest,
            "bytes": len(payload),
            "source_manifest_sha256": "f" * 64,
        },
    )
    updated = VisualSourceManifest(
        pack_id=manifest.pack_id,
        records=tuple(
            replaced if row.slot_id == target else row
            for row in manifest.records
        ),
    )
    manifest_path.write_bytes(canonical_manifest_bytes(updated))

    changed = prepare_material_bundle(
        visual_pack_root=replacement_root,
        staging_root=tmp_path / "changed",
    )

    baseline_by_slot = {row.slot_id: row for row in baseline.manifest.records}
    changed_by_slot = {row.slot_id: row for row in changed.manifest.records}
    assert changed.manifest.bundle_id != baseline.manifest.bundle_id
    assert changed_by_slot[target] != baseline_by_slot[target]
    assert {
        slot_id
        for slot_id in baseline_by_slot
        if baseline_by_slot[slot_id] != changed_by_slot[slot_id]
    } == {target}
    assert visual_root != replacement_root


def test_missing_material_source_fails_without_partial_bundle(tmp_path: Path) -> None:
    visual_root = write_material_visual_pack(tmp_path / "visual")
    manifest_path = visual_root / "visual-sources.json"
    manifest = load_visual_source_manifest(manifest_path)
    reduced = VisualSourceManifest(
        pack_id=manifest.pack_id,
        records=manifest.records[:-1],
    )
    manifest_path.write_bytes(canonical_manifest_bytes(reduced))
    staging = tmp_path / "staging"

    with pytest.raises(MaterialBundleError, match="24 material"):
        prepare_material_bundle(
            visual_pack_root=visual_root,
            staging_root=staging,
        )

    assert not staging.exists()
