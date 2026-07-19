"""Immutable Mesh Bundle v3 dual-profile contracts."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

import pipeline.synthetic_village.mesh_asset_bundle_v3 as mesh_v3
from pipeline.synthetic_village.glb_ktx2_variant import geometry_fingerprint_glb
from pipeline.synthetic_village.h3_material_sources import H3_HERO_SLOTS
from pipeline.synthetic_village.material_bundle_v2 import (
    H2_PROFILE_ID,
    H3_PROFILE_ID,
)
from pipeline.synthetic_village.mesh_asset_bundle import load_mesh_asset_bundle
from pipeline.synthetic_village.mesh_asset_bundle_v3 import (
    MESH_ASSET_BUNDLE_V3_SCHEMA,
    MeshAssetBundleV3Error,
    canonical_mesh_asset_bundle_v3_bytes,
    compose_mesh_asset_bundle_v3,
    load_mesh_asset_bundle_v3,
    prepare_mesh_asset_bundle_v3,
    publish_mesh_asset_bundle_v3,
    read_verified_mesh_texture_v3,
    read_verified_mesh_variant_glb,
)
from tests.test_ktx2_toolchain import _fake_ktx2
from tests.test_mesh_asset_bundle_v2 import _prepare_real_v2_fixture


def _ktx(slot_id: str, role: str):
    payload = f"{slot_id}:{role}:h3-ktx2".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return SimpleNamespace(
        slot_id=slot_id,
        role=role,
        object_path=f"objects/{digest}.ktx2",
        sha256=digest,
        bytes=len(payload),
        width=4096,
        height=4096,
        media_type="image/ktx2",
        transfer="srgb" if role == "base_color" else "linear",
    )


def _material_v2(h2_material_bundle_id: str):
    textures = tuple(
        _ktx(slot_id, role) for slot_id in H3_HERO_SLOTS for role in ("base_color", "normal", "orm")
    )
    return SimpleNamespace(
        bundle_id="4" * 64,
        fallback_bundle_id=h2_material_bundle_id,
        synthetic=True,
        ai_generated=True,
        real_photo_textures=False,
        geometry_usability="preview-only",
        metric_alignment=False,
        verification_level="L0",
        profiles={
            H3_PROFILE_ID: SimpleNamespace(textures=textures),
            H2_PROFILE_ID: SimpleNamespace(textures=()),
        },
    )


def _material_v2_with_ktx_files(tmp_path, h2_material_bundle_id: str):
    root = tmp_path / "ktx-pack"
    (root / "objects").mkdir(parents=True)
    textures = []
    payloads = {}
    for slot_id in H3_HERO_SLOTS:
        for role in ("base_color", "normal", "orm"):
            payload = _fake_ktx2(
                transfer=2 if role == "base_color" else 1,
                colour_model=166,
            )
            digest = hashlib.sha256(payload).hexdigest()
            descriptor = SimpleNamespace(
                slot_id=slot_id,
                role=role,
                object_path=f"objects/{digest}.ktx2",
                sha256=digest,
                bytes=len(payload),
                width=4096,
                height=4096,
                media_type="image/ktx2",
                transfer="srgb" if role == "base_color" else "linear",
            )
            textures.append(descriptor)
            payloads[digest] = payload
    for digest, payload in payloads.items():
        (root / f"objects/{digest}.ktx2").write_bytes(payload)
    material = _material_v2(h2_material_bundle_id)
    material.profiles[H3_PROFILE_ID].textures = tuple(textures)
    return material, root


def _fallback_glbs(prepared):
    return {
        record.asset_id: (prepared.staging_root / record.lod["2"].glb_object_path).read_bytes()
        for record in prepared.manifest.records
    }


def test_mesh_v3_binds_dual_lod2_without_changing_h2_lod01(
    tmp_path,
    monkeypatch,
) -> None:
    prepared, _objects = _prepare_real_v2_fixture(tmp_path)
    h2 = prepared.manifest
    monkeypatch.setattr(
        mesh_v3,
        "ACCEPTED_H2_MESH_BUNDLE_ID",
        h2.bundle_id,
    )

    bundle = compose_mesh_asset_bundle_v3(
        h2,
        _fallback_glbs(prepared),
        _material_v2(h2.material_bundle_id),
    )

    assert bundle.schema_version == MESH_ASSET_BUNDLE_V3_SCHEMA
    assert bundle.source_v2_bundle_id == h2.bundle_id
    assert bundle.material_bundle_v2_id == "4" * 64
    assert len(bundle.records) == len(h2.records)
    for source, record in zip(h2.records, bundle.records, strict=True):
        assert record.lod["0"] == source.lod["0"]
        assert record.lod["1"] == source.lod["1"]
        near = record.lod["2"]
        assert set(near.variants) == {H3_PROFILE_ID, H2_PROFILE_ID}
        primary = near.variants[H3_PROFILE_ID]
        fallback = near.variants[H2_PROFILE_ID]
        assert primary.geometry_fingerprint == fallback.geometry_fingerprint
        assert primary.geometry_fingerprint == near.geometry_fingerprint
        assert (
            geometry_fingerprint_glb(
                _fallback_glbs(prepared)[record.asset_id],
            )
            == near.geometry_fingerprint
        )
        assert any(binding.media_type == "image/ktx2" for binding in primary.texture_bindings)
        assert all(binding.media_type == "image/png" for binding in fallback.texture_bindings)

    assert (
        hashlib.sha256(
            canonical_mesh_asset_bundle_v3_bytes(
                bundle,
                exclude_bundle_id=True,
            ),
        ).hexdigest()
        == bundle.bundle_id
    )


def test_mesh_v3_rejects_wrong_h2_or_tampered_lod2_bytes(
    tmp_path,
    monkeypatch,
) -> None:
    prepared, _objects = _prepare_real_v2_fixture(tmp_path)
    h2 = prepared.manifest
    glbs = _fallback_glbs(prepared)
    materials = _material_v2(h2.material_bundle_id)

    with pytest.raises(MeshAssetBundleV3Error, match="accepted H2"):
        compose_mesh_asset_bundle_v3(h2, glbs, materials)

    monkeypatch.setattr(
        mesh_v3,
        "ACCEPTED_H2_MESH_BUNDLE_ID",
        h2.bundle_id,
    )
    asset_id = h2.records[0].asset_id
    tampered = dict(glbs)
    tampered[asset_id] = tampered[asset_id][:-1] + b"\x01"
    with pytest.raises(MeshAssetBundleV3Error, match="bytes|SHA"):
        compose_mesh_asset_bundle_v3(h2, tampered, materials)


def test_mesh_v3_prepares_and_verifies_complete_file_closure(
    tmp_path,
    monkeypatch,
) -> None:
    h2_prepared, _objects = _prepare_real_v2_fixture(tmp_path / "h2")
    h2 = h2_prepared.manifest
    monkeypatch.setattr(
        mesh_v3,
        "ACCEPTED_H2_MESH_BUNDLE_ID",
        h2.bundle_id,
    )
    material, ktx_root = _material_v2_with_ktx_files(
        tmp_path,
        h2.material_bundle_id,
    )

    prepared = prepare_mesh_asset_bundle_v3(
        source_v2_bundle_root=h2_prepared.staging_root,
        material_bundle_v2=material,
        ktx2_root=ktx_root,
        staging_root=tmp_path / "v3-staging",
    )
    loaded = load_mesh_asset_bundle_v3(prepared.staging_root)
    assert loaded == prepared.manifest

    for record in loaded.records:
        for profile_id in (H3_PROFILE_ID, H2_PROFILE_ID):
            payload = read_verified_mesh_variant_glb(
                prepared.staging_root,
                bundle=loaded,
                asset_id=record.asset_id,
                profile_id=profile_id,
            )
            assert geometry_fingerprint_glb(payload) == (record.lod["2"].geometry_fingerprint)

    ktx_object = next(row for row in loaded.texture_objects if row.media_type == "image/ktx2")
    ktx_payload = read_verified_mesh_texture_v3(
        prepared.staging_root,
        bundle=loaded,
        sha256=ktx_object.sha256,
        media_type="image/ktx2",
    )
    assert hashlib.sha256(ktx_payload).hexdigest() == ktx_object.sha256

    ktx_path = prepared.staging_root / ktx_object.object_path
    ktx_path.write_bytes(ktx_payload[:-1] + b"\x01")
    with pytest.raises(MeshAssetBundleV3Error, match="texture|KTX"):
        load_mesh_asset_bundle_v3(prepared.staging_root)


def test_mesh_v3_publication_is_absent_only_and_reusable(
    tmp_path,
    monkeypatch,
) -> None:
    h2_prepared, _objects = _prepare_real_v2_fixture(tmp_path / "h2")
    h2 = h2_prepared.manifest
    monkeypatch.setattr(
        mesh_v3,
        "ACCEPTED_H2_MESH_BUNDLE_ID",
        h2.bundle_id,
    )
    material, ktx_root = _material_v2_with_ktx_files(
        tmp_path,
        h2.material_bundle_id,
    )
    kwargs = {
        "source_v2_bundle_root": h2_prepared.staging_root,
        "material_bundle_v2": material,
        "ktx2_root": ktx_root,
        "publication_root": tmp_path / "published",
        "work_root": tmp_path / "work",
    }

    first = publish_mesh_asset_bundle_v3(**kwargs)
    second = publish_mesh_asset_bundle_v3(**kwargs)

    assert first.reused is False
    assert second.reused is True
    assert second.bundle_id == first.bundle_id
    assert second.final_directory == first.final_directory
    assert load_mesh_asset_bundle_v3(first.final_directory).bundle_id == (first.bundle_id)
    assert load_mesh_asset_bundle(first.final_directory).bundle_id == (first.bundle_id)


def test_mesh_v3_loader_rejects_tampered_reused_lod0(
    tmp_path,
    monkeypatch,
) -> None:
    h2_prepared, _objects = _prepare_real_v2_fixture(tmp_path / "h2")
    h2 = h2_prepared.manifest
    monkeypatch.setattr(
        mesh_v3,
        "ACCEPTED_H2_MESH_BUNDLE_ID",
        h2.bundle_id,
    )
    material, ktx_root = _material_v2_with_ktx_files(
        tmp_path,
        h2.material_bundle_id,
    )
    prepared = prepare_mesh_asset_bundle_v3(
        source_v2_bundle_root=h2_prepared.staging_root,
        material_bundle_v2=material,
        ktx2_root=ktx_root,
        staging_root=tmp_path / "v3-staging",
    )
    descriptor = prepared.manifest.records[0].lod["0"]
    path = prepared.staging_root / descriptor.glb_object_path
    payload = path.read_bytes()
    path.write_bytes(payload[:-1] + b"\x01")

    with pytest.raises(MeshAssetBundleV3Error, match="GLB bytes"):
        load_mesh_asset_bundle_v3(prepared.staging_root)
