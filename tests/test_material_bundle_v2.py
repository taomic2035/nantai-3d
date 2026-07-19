"""Dual-profile material bundle v2 contracts."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

import pipeline.synthetic_village.material_bundle_v2 as material_v2
from pipeline.synthetic_village.h3_material_sources import H3_HERO_SLOTS
from pipeline.synthetic_village.material_bundle import prepare_material_bundle
from pipeline.synthetic_village.material_bundle_v2 import (
    H2_PROFILE_ID,
    H3_PROFILE_ID,
    MATERIAL_BUNDLE_V2_SCHEMA,
    MaterialBundleV2Error,
    canonical_material_bundle_v2_bytes,
    compose_material_bundle_v2,
)
from tests.synthetic_material_fixtures import write_material_visual_pack


@pytest.fixture(scope="module")
def h2_bundle(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("material-v2-h2")
    visual = write_material_visual_pack(root / "visual")
    return prepare_material_bundle(
        visual_pack_root=visual,
        staging_root=root / "material",
    ).manifest


def _fake_ktx_descriptor(slot_id: str, role: str):
    payload = f"{slot_id}:{role}:ktx2".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return SimpleNamespace(
        role=role,
        object_path=f"objects/{digest}.ktx2",
        sha256=digest,
        bytes=len(payload),
        width=4096,
        height=4096,
        media_type="image/ktx2",
        transfer="srgb" if role == "base_color" else "linear",
    )


def _fake_h3_pack(*, missing_last: bool = False):
    slots = H3_HERO_SLOTS[:-1] if missing_last else H3_HERO_SLOTS
    return SimpleNamespace(
        pack_id="3" * 64,
        source_pack_id="1" * 64,
        authored_pack_id="2" * 64,
        synthetic=True,
        ai_generated=True,
        real_photo_textures=False,
        geometry_usability="preview-only",
        metric_alignment=False,
        verification_level="L0",
        records=tuple(
            SimpleNamespace(
                slot_id=slot_id,
                base_color=_fake_ktx_descriptor(slot_id, "base_color"),
                normal=_fake_ktx_descriptor(slot_id, "normal"),
                orm=_fake_ktx_descriptor(slot_id, "orm"),
            )
            for slot_id in slots
        ),
    )


def test_material_v2_binds_complete_h3_and_exact_h2_profiles(
    h2_bundle,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        material_v2,
        "ACCEPTED_H2_MATERIAL_BUNDLE_ID",
        h2_bundle.bundle_id,
    )
    bundle = compose_material_bundle_v2(h2_bundle, _fake_h3_pack())

    assert bundle.schema_version == MATERIAL_BUNDLE_V2_SCHEMA
    assert bundle.fallback_bundle_id == h2_bundle.bundle_id
    assert bundle.source_pack_id == "1" * 64
    assert bundle.authored_pack_id == "2" * 64
    assert bundle.ktx2_pack_id == "3" * 64
    assert set(bundle.profiles) == {H3_PROFILE_ID, H2_PROFILE_ID}
    assert len(bundle.profiles[H2_PROFILE_ID].textures) == 72
    assert len(bundle.profiles[H3_PROFILE_ID].textures) == 72
    assert (
        sum(
            texture.media_type == "image/ktx2"
            for texture in bundle.profiles[H3_PROFILE_ID].textures
        )
        == 24
    )
    assert (
        sum(
            texture.media_type == "image/png" for texture in bundle.profiles[H3_PROFILE_ID].textures
        )
        == 48
    )
    assert (
        hashlib.sha256(
            canonical_material_bundle_v2_bytes(
                bundle,
                exclude_bundle_id=True,
            ),
        ).hexdigest()
        == bundle.bundle_id
    )


def test_material_v2_preserves_every_h2_texture_identity(
    h2_bundle,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        material_v2,
        "ACCEPTED_H2_MATERIAL_BUNDLE_ID",
        h2_bundle.bundle_id,
    )
    bundle = compose_material_bundle_v2(h2_bundle, _fake_h3_pack())
    expected = {
        (record.slot_id, role, getattr(record, role).sha256)
        for record in h2_bundle.records
        for role in ("base_color", "normal", "orm")
    }
    actual = {
        (texture.slot_id, texture.role, texture.sha256)
        for texture in bundle.profiles[H2_PROFILE_ID].textures
    }
    assert actual == expected


def test_material_v2_rejects_wrong_h2_or_incomplete_h3(
    h2_bundle,
    monkeypatch,
) -> None:
    with pytest.raises(MaterialBundleV2Error, match="accepted H2"):
        compose_material_bundle_v2(h2_bundle, _fake_h3_pack())

    monkeypatch.setattr(
        material_v2,
        "ACCEPTED_H2_MATERIAL_BUNDLE_ID",
        h2_bundle.bundle_id,
    )
    with pytest.raises(MaterialBundleV2Error, match="eight|slots"):
        compose_material_bundle_v2(
            h2_bundle,
            _fake_h3_pack(missing_last=True),
        )
