"""Deterministic 4096 authoring and PBR evidence for H3 material sources."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pipeline.synthetic_village.h3_material_authoring import (
    H3_AUTHORED_PACK_SCHEMA,
    H3_AUTHORING_ALGORITHM_ID,
    H3_MASTER_SIZE,
    H3AuthoredMaterialError,
    _author_master,
    _blend_patch,
    _minimum_error_path,
    _srgb_to_linear,
    build_h3_authored_material_pack,
    canonical_h3_authored_pack_bytes,
    load_h3_authored_material_pack,
    read_verified_h3_authored_map,
)
from pipeline.synthetic_village.h3_material_sources import (
    H3_HERO_SLOTS,
    prepare_h3_source_pack,
)
from tests.test_h3_material_sources import _write_selection_receipt


@pytest.fixture(scope="module")
def authored_pack(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("h3-authored-materials")
    receipt = _write_selection_receipt(root / "input")
    source_pack = prepare_h3_source_pack(receipt, root / "sources")
    authored = build_h3_authored_material_pack(
        source_pack.root,
        root / "authored",
    )
    return source_pack, authored


def test_small_master_quilting_is_byte_deterministic_and_seamless() -> None:
    pixels = np.zeros((96, 128, 3), dtype=np.uint8)
    pixels[..., 0] = np.arange(128, dtype=np.uint8)
    pixels[..., 1] = np.arange(96, dtype=np.uint8)[:, None]
    pixels[..., 2] = 91
    source = Image.fromarray(pixels, mode="RGB")
    source_sha256 = hashlib.sha256(source.tobytes()).hexdigest()

    first = _author_master(
        source,
        source_sha256=source_sha256,
        output_size=256,
        patch_size=64,
        overlap=16,
        edge_band=16,
    )
    second = _author_master(
        source,
        source_sha256=source_sha256,
        output_size=256,
        patch_size=64,
        overlap=16,
        edge_band=16,
    )
    first_pixels = np.asarray(first)

    assert first.tobytes() == second.tobytes()
    assert first.mode == "RGB"
    assert first.size == (256, 256)
    assert np.array_equal(first_pixels[:, 0], first_pixels[:, -1])
    assert np.array_equal(first_pixels[0], first_pixels[-1])
    assert not np.array_equal(first_pixels, first_pixels[:, ::-1])


def test_quilting_uses_linear_light_minimum_error_cuts() -> None:
    linear = _srgb_to_linear(
        np.array([0.0, 128.0, 255.0], dtype=np.float32),
    )
    assert linear.tolist() == pytest.approx(
        [0.0, 0.2158605, 1.0],
        abs=1e-6,
    )

    cost = np.array(
        [
            [8.0, 1.0, 8.0],
            [8.0, 1.0, 8.0],
            [8.0, 1.0, 8.0],
            [8.0, 1.0, 8.0],
        ],
        dtype=np.float32,
    )
    assert _minimum_error_path(cost).tolist() == [1, 1, 1, 1]

    region = np.zeros((6, 6, 3), dtype=np.float32)
    patch = np.full((6, 6, 3), 255.0, dtype=np.float32)
    covered = np.zeros((6, 6), dtype=bool)
    covered[:, :3] = True
    _blend_patch(
        region,
        patch,
        covered,
        x=3,
        y=0,
        overlap=3,
    )

    assert set(np.unique(region).tolist()) <= {0.0, 255.0}
    assert np.all(covered)


def test_authored_pack_has_exact_4k_roles_and_truth(authored_pack) -> None:
    source_pack, authored = authored_pack
    pack = authored.manifest

    assert pack.schema_version == H3_AUTHORED_PACK_SCHEMA
    assert pack.algorithm_id == H3_AUTHORING_ALGORITHM_ID
    assert pack.source_pack_id == source_pack.manifest.source_pack_id
    assert pack.synthetic is True
    assert pack.ai_generated is True
    assert pack.real_photo_textures is False
    assert pack.geometry_usability == "preview-only"
    assert pack.metric_alignment is False
    assert pack.verification_level == "L0"
    assert tuple(record.slot_id for record in pack.records) == H3_HERO_SLOTS

    expected_mips = tuple(
        (size, size)
        for size in (4096, 2048, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1)
    )
    for record in pack.records:
        assert record.mip_dimensions == expected_mips
        assert record.material_measurement == "none"
        assert record.normal_derivation == "synthetic-image-gradient"
        assert record.roughness_derivation == "synthetic-luminance-statistics"
        assert record.metalness_policy == "slot-constant-or-zero"
        assert record.master.sha256 == record.base_color.sha256
        assert record.seam_discontinuity == 0.0
        assert 0.0 <= record.full_source_ssim <= 1.0
        assert 0.0 <= record.interior_source_ssim <= 1.0
        for role, descriptor in (
            ("master", record.master),
            ("base_color", record.base_color),
            ("normal", record.normal),
            ("orm", record.orm),
        ):
            assert descriptor.role == role
            assert descriptor.width == descriptor.height == H3_MASTER_SIZE
            assert descriptor.mode == "RGB"
            assert descriptor.media_type == "image/png"
            assert descriptor.object_path == f"objects/{descriptor.sha256}.png"
            if role in {"master", "base_color"}:
                assert descriptor.colour_space == "srgb"
            else:
                assert descriptor.colour_space == "linear"
            payload = read_verified_h3_authored_map(
                authored.root,
                pack=pack,
                slot_id=record.slot_id,
                role=role,
            )
            assert len(payload) == descriptor.bytes
            assert hashlib.sha256(payload).hexdigest() == descriptor.sha256


def test_authored_maps_are_seamless_and_pbr_channels_are_bounded(
    authored_pack,
) -> None:
    _, authored = authored_pack
    record = authored.manifest.records[0]

    images = {}
    for role in ("base_color", "normal", "orm"):
        payload = read_verified_h3_authored_map(
            authored.root,
            pack=authored.manifest,
            slot_id=record.slot_id,
            role=role,
        )
        path = authored.root / f"{role}.inspection.png"
        path.write_bytes(payload)
        try:
            with Image.open(path) as image:
                image.load()
                images[role] = np.asarray(image, dtype=np.uint8)
        finally:
            path.unlink()

    for pixels in images.values():
        assert np.array_equal(pixels[:, 0], pixels[:, -1])
        assert np.array_equal(pixels[0], pixels[-1])
    normal = images["normal"]
    decoded = normal.astype(np.float64) / 127.5 - 1.0
    lengths = np.linalg.norm(decoded, axis=2)
    assert np.isfinite(decoded).all()
    assert float(lengths.min()) >= 0.98
    assert float(lengths.max()) <= 1.02
    assert np.all(normal[..., 2] >= 128)
    orm = images["orm"]
    assert np.all(orm[..., 2] == 0)
    assert int(orm[..., 1].min()) >= 0
    assert int(orm[..., 1].max()) <= 255


def test_authored_pack_is_content_idempotent(authored_pack) -> None:
    _, authored = authored_pack
    second = build_h3_authored_material_pack(
        authored.root.parents[1] / "sources" / authored.manifest.source_pack_id,
        authored.root.parent,
    )

    assert second == authored
    assert canonical_h3_authored_pack_bytes(second.manifest) == (
        authored.root / "manifest.json"
    ).read_bytes()


def test_load_authored_pack_rejects_tampered_object(
    authored_pack,
    tmp_path: Path,
) -> None:
    _, authored = authored_pack
    copied = tmp_path / "copied"
    shutil.copytree(authored.root, copied)
    descriptor = authored.manifest.records[0].normal
    (copied / descriptor.object_path).write_bytes(b"tampered")

    with pytest.raises(H3AuthoredMaterialError, match="SHA-256"):
        load_h3_authored_material_pack(copied)


def test_load_authored_pack_rejects_extra_directory(
    authored_pack,
    tmp_path: Path,
) -> None:
    _, authored = authored_pack
    copied = tmp_path / "copied"
    shutil.copytree(authored.root, copied)
    (copied / "unexpected").mkdir()

    with pytest.raises(H3AuthoredMaterialError, match="directory closure"):
        load_h3_authored_material_pack(copied)
