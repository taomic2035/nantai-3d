"""Deterministic cutout-atlas evidence for the three H2 foliage slots."""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import pipeline.synthetic_village.foliage_atlas as foliage_atlas
from pipeline.synthetic_village.foliage_atlas import (
    FOLIAGE_SLOTS,
    FoliageAtlasError,
    build_foliage_atlas_set,
    canonical_foliage_atlas_set_bytes,
)
from pipeline.synthetic_village.material_bundle import load_material_bundle
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def material_bundle_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    _visual_root, result = publish_material_fixture(
        tmp_path_factory.mktemp("foliage-atlas-materials"),
    )
    return result.final_directory


def test_atlas_is_deterministic_rgba_and_nonuniform(
    material_bundle_root: Path,
    tmp_path: Path,
) -> None:
    first = build_foliage_atlas_set(
        material_bundle_root,
        tmp_path / "first",
    )
    second = build_foliage_atlas_set(
        material_bundle_root,
        tmp_path / "second",
    )

    assert canonical_foliage_atlas_set_bytes(first.manifest) == (
        canonical_foliage_atlas_set_bytes(second.manifest)
    )
    assert str(tmp_path).encode() not in canonical_foliage_atlas_set_bytes(
        first.manifest,
    )
    for slot_id in FOLIAGE_SLOTS:
        record = first.manifest.by_slot[slot_id]
        with Image.open(first.root / record.base_color.object_path) as image:
            image.load()
            assert image.mode == "RGBA"
            assert image.size == (1024, 1024)
            alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
        assert set(np.unique(alpha)) == {0, 255}
        measured = np.count_nonzero(alpha) / alpha.size
        assert record.alpha_coverage == pytest.approx(measured, abs=1e-9)
        assert record.coverage_min <= measured <= record.coverage_max
        for output in (record.base_color, record.normal, record.orm):
            payload = (first.root / output.object_path).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == output.sha256
            assert len(payload) == output.bytes


def test_atlas_binds_exact_verified_material_inputs(
    material_bundle_root: Path,
    tmp_path: Path,
) -> None:
    source = load_material_bundle(material_bundle_root)
    prepared = build_foliage_atlas_set(
        material_bundle_root,
        tmp_path / "atlas",
    )
    source_by_slot = {record.slot_id: record for record in source.records}

    assert prepared.manifest.source_material_bundle_id == source.bundle_id
    for record in prepared.manifest.records:
        source_record = source_by_slot[record.slot_id]
        assert tuple(
            (row.role, row.sha256, row.bytes)
            for row in record.source_maps
        ) == (
            (
                "base_color",
                source_record.base_color.sha256,
                source_record.base_color.bytes,
            ),
            (
                "normal",
                source_record.normal.sha256,
                source_record.normal.bytes,
            ),
            (
                "orm",
                source_record.orm.sha256,
                source_record.orm.bytes,
            ),
        )


def test_atlas_is_cross_process_deterministic(
    material_bundle_root: Path,
    tmp_path: Path,
) -> None:
    script = """
from pathlib import Path
import sys
from pipeline.synthetic_village.foliage_atlas import build_foliage_atlas_set
build_foliage_atlas_set(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    outputs = (tmp_path / "process-a", tmp_path / "process-b")
    for output in outputs:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(material_bundle_root),
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    assert (outputs[0] / "manifest.json").read_bytes() == (
        outputs[1] / "manifest.json"
    ).read_bytes()
    assert {
        path.name: path.read_bytes()
        for path in (outputs[0] / "textures").iterdir()
    } == {
        path.name: path.read_bytes()
        for path in (outputs[1] / "textures").iterdir()
    }


def test_atlas_rejects_output_overwrite_or_redirected_source(
    material_bundle_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "atlas"
    build_foliage_atlas_set(material_bundle_root, output)
    with pytest.raises(FoliageAtlasError, match="already exists"):
        build_foliage_atlas_set(material_bundle_root, output)

    redirected = tmp_path / "redirected-materials"
    redirected.symlink_to(material_bundle_root, target_is_directory=True)
    with pytest.raises(FoliageAtlasError, match="source material bundle"):
        build_foliage_atlas_set(redirected, tmp_path / "redirect-output")


def test_atlas_rejects_changed_source_map_bytes(
    material_bundle_root: Path,
    tmp_path: Path,
) -> None:
    source = load_material_bundle(material_bundle_root)
    descriptor = next(
        record.base_color
        for record in source.records
        if record.slot_id == FOLIAGE_SLOTS[0]
    )
    target = material_bundle_root / descriptor.object_path
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\0")
        with pytest.raises(FoliageAtlasError, match="source material bundle"):
            build_foliage_atlas_set(
                material_bundle_root,
                tmp_path / "atlas",
            )
    finally:
        target.write_bytes(original)
        load_material_bundle(material_bundle_root)


def test_atlas_rejects_non_png_or_wrong_size_source_bytes() -> None:
    payload = b"not-a-png"
    with pytest.raises(FoliageAtlasError, match="not a PNG"):
        foliage_atlas._decode_source_map(
            payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_bytes=len(payload),
        )

    output = io.BytesIO()
    Image.new("RGB", (2, 2), (32, 64, 96)).save(output, format="PNG")
    payload = output.getvalue()
    with pytest.raises(FoliageAtlasError, match="1024 px"):
        foliage_atlas._decode_source_map(
            payload,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_bytes=len(payload),
        )


def test_atlas_rejects_missing_slots_or_bad_alpha_coverage(
    material_bundle_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = load_material_bundle(material_bundle_root)
    monkeypatch.setattr(
        foliage_atlas,
        "load_material_bundle",
        lambda _root: source.model_copy(
            update={
                "records": tuple(
                    record
                    for record in source.records
                    if record.slot_id != FOLIAGE_SLOTS[0]
                ),
            },
        ),
    )
    with pytest.raises(FoliageAtlasError, match="foliage slots"):
        build_foliage_atlas_set(
            material_bundle_root,
            tmp_path / "missing",
        )

    monkeypatch.setattr(foliage_atlas, "load_material_bundle", lambda _root: source)
    monkeypatch.setattr(
        foliage_atlas,
        "_inside_leaf",
        lambda _shape, _x, _y: _x < 0.8,
    )
    with pytest.raises(FoliageAtlasError, match="alpha coverage"):
        build_foliage_atlas_set(
            material_bundle_root,
            tmp_path / "coverage",
        )
