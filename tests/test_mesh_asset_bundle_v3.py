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
from pipeline.synthetic_village.mesh_asset_bundle_v3 import (
    MESH_ASSET_BUNDLE_V3_SCHEMA,
    MeshAssetBundleV3Error,
    canonical_mesh_asset_bundle_v3_bytes,
    compose_mesh_asset_bundle_v3,
)
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
