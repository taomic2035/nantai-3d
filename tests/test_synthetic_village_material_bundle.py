"""Deterministic derived PBR material-bundle contracts."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import pipeline.synthetic_village.material_bundle as material_bundle
from pipeline.synthetic_village.defaults import load_default_visual_slots
from pipeline.synthetic_village.material_bundle import (
    MATERIAL_PARAMETERS,
    MaterialBundleError,
    PreparedMaterialBundle,
    canonical_material_bundle_bytes,
    load_material_bundle,
    prepare_material_bundle,
    publish_material_bundle,
    read_verified_material_map,
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


@pytest.mark.parametrize("role", ["base_color", "normal", "orm"])
def test_exact_material_map_read_rechecks_selected_bundle(
    prepared_bundle,
    role: str,
) -> None:
    _, prepared = prepared_bundle
    record = prepared.manifest.records[0]
    descriptor = getattr(record, role)

    assert read_verified_material_map(
        prepared.staging_root,
        bundle=prepared.manifest,
        slot_id=record.slot_id,
        role=role,
    ) == (prepared.staging_root / descriptor.object_path).read_bytes()


def test_exact_material_map_read_rejects_changed_object(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, prepared = prepared_bundle
    copied = tmp_path / "bundle"
    shutil.copytree(prepared.staging_root, copied)
    bundle = load_material_bundle(copied)
    verification_calls = 0
    original_verify = material_bundle.verify_prepared_material_bundle

    def count_verification(root: Path):
        nonlocal verification_calls
        verification_calls += 1
        return original_verify(root)

    monkeypatch.setattr(
        material_bundle,
        "verify_prepared_material_bundle",
        count_verification,
    )
    assert load_material_bundle(copied) == bundle
    assert verification_calls == 0
    descriptor = bundle.records[0].base_color
    (copied / descriptor.object_path).write_bytes(b"altered")

    with pytest.raises(MaterialBundleError, match="does not match|verification"):
        read_verified_material_map(
            copied,
            bundle=bundle,
            slot_id=bundle.records[0].slot_id,
            role="base_color",
        )
    assert verification_calls == 1


def test_base_color_is_repeatable_without_a_hard_edge(prepared_bundle) -> None:
    _, prepared = prepared_bundle
    image = prepared.open_map(prepared.manifest.records[0].base_color)
    pixels = np.asarray(image)

    assert np.array_equal(pixels[:, 0, :], pixels[:, -1, :])
    assert np.array_equal(pixels[0, :, :], pixels[-1, :, :])


def test_base_color_tiling_does_not_create_an_exact_mirror_kaleidoscope(
    prepared_bundle,
) -> None:
    _, prepared = prepared_bundle
    image = prepared.open_map(prepared.manifest.records[0].base_color)
    pixels = np.asarray(image)

    assert material_bundle.ALGORITHM_ID == "edge-feather-sobel-orm-v2"
    assert not np.array_equal(pixels, pixels[:, ::-1, :])
    assert not np.array_equal(pixels, pixels[::-1, :, :])


def test_dark_timber_shadow_lift_keeps_the_material_dark_but_readable() -> None:
    source = Image.new("RGB", (8, 8), (44, 34, 28))

    lifted = np.asarray(material_bundle._lift_dark_timber_shadows(source))
    luminance = material_bundle._luminance(lifted)

    assert float(np.median(luminance)) >= 55.0
    assert float(np.median(luminance)) < 100.0
    assert np.all(lifted[..., 0] > lifted[..., 1])
    assert np.all(lifted[..., 1] > lifted[..., 2])


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


def _use_prepared_template(
    monkeypatch: pytest.MonkeyPatch,
    prepared: PreparedMaterialBundle,
):
    def copy_prepared(*, visual_pack_root: Path, staging_root: Path):
        del visual_pack_root
        shutil.copytree(prepared.staging_root, staging_root)
        return PreparedMaterialBundle(
            staging_root=Path(staging_root),
            manifest=prepared.manifest,
        )

    monkeypatch.setattr(material_bundle, "prepare_material_bundle", copy_prepared)


def _material_staging_directories(work_root: Path) -> list[Path]:
    return [
        path
        for path in work_root.glob(".material-*")
        if path.name != ".material-bundle.lock"
    ]


def test_publish_material_bundle_is_absent_only_and_idempotent(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, prepared = prepared_bundle
    _use_prepared_template(monkeypatch, prepared)
    publication_root = tmp_path / ".nantai-studio/material-bundles"
    work_root = tmp_path / ".nantai-studio/work"

    first = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=publication_root,
        work_root=work_root,
    )
    second = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=publication_root,
        work_root=work_root,
    )

    assert second.final_directory == first.final_directory
    assert second.reused is True
    assert first.reused is False
    assert first.record_count == 24
    assert load_material_bundle(first.final_directory).bundle_id == first.bundle_id
    assert not _material_staging_directories(work_root)


def test_publish_rejects_altered_existing_bundle(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, prepared = prepared_bundle
    _use_prepared_template(monkeypatch, prepared)
    publication_root = tmp_path / "material-bundles"
    work_root = tmp_path / "work"
    first = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=publication_root,
        work_root=work_root,
    )
    descriptor = load_material_bundle(first.final_directory).records[0].normal
    (first.final_directory / descriptor.object_path).write_bytes(b"altered")

    with pytest.raises(MaterialBundleError, match="does not match|verification"):
        publish_material_bundle(
            visual_pack_root=visual_root,
            publication_root=publication_root,
            work_root=work_root,
        )

    assert not _material_staging_directories(work_root)


def test_publish_rejects_redirected_bundle_object(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, prepared = prepared_bundle
    _use_prepared_template(monkeypatch, prepared)
    first = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=tmp_path / "material-bundles",
        work_root=tmp_path / "work",
    )
    descriptor = load_material_bundle(first.final_directory).records[0].base_color
    target = first.final_directory / descriptor.object_path
    target.unlink()
    target.symlink_to(tmp_path / "outside.png")

    with pytest.raises(MaterialBundleError, match="redirected|object set"):
        load_material_bundle(first.final_directory)


def test_publish_rejects_symlinked_root(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, prepared = prepared_bundle
    _use_prepared_template(monkeypatch, prepared)
    real_root = tmp_path / "real"
    real_root.mkdir()
    redirected = tmp_path / "redirected"
    redirected.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(MaterialBundleError, match="real directory|redirected"):
        publish_material_bundle(
            visual_pack_root=visual_root,
            publication_root=redirected,
            work_root=tmp_path / "work",
        )


def test_publish_rejects_source_change_after_snapshot_and_cleans_staging(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_visual_root, prepared = prepared_bundle
    visual_root = tmp_path / "visual"
    shutil.copytree(template_visual_root, visual_root)
    source_manifest = visual_root / "visual-sources.json"

    def mutate_source(*, visual_pack_root: Path, staging_root: Path):
        assert Path(visual_pack_root) == visual_root
        shutil.copytree(prepared.staging_root, staging_root)
        source_manifest.write_bytes(source_manifest.read_bytes() + b" ")
        return PreparedMaterialBundle(
            staging_root=Path(staging_root),
            manifest=prepared.manifest,
        )

    monkeypatch.setattr(material_bundle, "prepare_material_bundle", mutate_source)
    work_root = tmp_path / "work"

    with pytest.raises(MaterialBundleError, match="source visual pack changed"):
        publish_material_bundle(
            visual_pack_root=visual_root,
            publication_root=tmp_path / "material-bundles",
            work_root=work_root,
        )

    assert not _material_staging_directories(work_root)


def test_publish_propagates_directory_flush_failure_and_cleans_staging(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, prepared = prepared_bundle
    _use_prepared_template(monkeypatch, prepared)
    real_flush = material_bundle._flush_directory

    def fail_staging_flush(path: Path):
        if Path(path).name.startswith(".material-"):
            raise OSError("injected directory flush failure")
        return real_flush(path)

    monkeypatch.setattr(material_bundle, "_flush_directory", fail_staging_flush)
    work_root = tmp_path / "work"

    with pytest.raises(MaterialBundleError, match="filesystem failure"):
        publish_material_bundle(
            visual_pack_root=visual_root,
            publication_root=tmp_path / "material-bundles",
            work_root=work_root,
        )

    assert not _material_staging_directories(work_root)


def test_publish_cleans_owned_staging_after_interrupted_move(
    prepared_bundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_root, prepared = prepared_bundle
    _use_prepared_template(monkeypatch, prepared)

    def interrupt_move(source: Path, destination: Path):
        del source, destination
        raise OSError("injected interrupted move")

    monkeypatch.setattr(material_bundle, "_move_directory_noreplace", interrupt_move)
    work_root = tmp_path / "work"

    with pytest.raises(MaterialBundleError, match="filesystem failure"):
        publish_material_bundle(
            visual_pack_root=visual_root,
            publication_root=tmp_path / "material-bundles",
            work_root=work_root,
        )

    assert not _material_staging_directories(work_root)
