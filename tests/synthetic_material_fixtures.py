"""Hermetic generated inputs for synthetic material-bundle tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from pipeline.synthetic_village.defaults import load_default_visual_slots
from pipeline.synthetic_village.material_bundle import publish_material_bundle
from pipeline.synthetic_village.visual_sources import (
    VisualSourceManifest,
    VisualSourceRecord,
    canonical_manifest_bytes,
)


def _fixture_image(slot_index: int) -> Image.Image:
    image = Image.new("RGB", (12, 8))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = (
                (slot_index * 17 + x * 13) % 256,
                (slot_index * 29 + y * 19) % 256,
                (slot_index * 37 + x * 7 + y * 11) % 256,
            )
    return image


def write_material_visual_pack(root: Path) -> Path:
    """Write a canonical private pack with one distinct PNG per material slot."""

    root = Path(root)
    object_root = root / "objects"
    object_root.mkdir(parents=True)
    records = []
    material_slots = sorted(
        (
            slot
            for slot in load_default_visual_slots().slots
            if slot.category == "material"
        ),
        key=lambda slot: slot.slot_id,
    )
    for index, slot in enumerate(material_slots, start=1):
        temporary = root / f"{slot.slot_id}.png"
        _fixture_image(index).save(
            temporary,
            format="PNG",
            compress_level=9,
            optimize=False,
        )
        payload = temporary.read_bytes()
        temporary.unlink()
        digest = hashlib.sha256(payload).hexdigest()
        object_path = f"objects/{digest}.png"
        (root / object_path).write_bytes(payload)
        records.append(
            VisualSourceRecord(
                slot_id=slot.slot_id,
                category="material",
                object_path=object_path,
                sha256=digest,
                bytes=len(payload),
                width=12,
                height=8,
                prompt=slot.prompt,
                source_pack_id="pytest-material-fixture",
                source_manifest_sha256=f"{index:064x}",
                generator_interface="pytest-pillow-generated",
                actual_model_id="deterministic-test-fixture",
            ),
        )
    manifest = VisualSourceManifest(
        pack_id="synthetic-mountain-village-hybrid-v3",
        records=tuple(records),
    )
    (root / "visual-sources.json").write_bytes(canonical_manifest_bytes(manifest))
    return root


def publish_material_fixture(root: Path):
    """Publish one complete material bundle for downstream hermetic tests."""

    root = Path(root)
    visual_root = write_material_visual_pack(root / "visual")
    result = publish_material_bundle(
        visual_pack_root=visual_root,
        publication_root=root / "material-bundles",
        work_root=root / "material-work",
    )
    return visual_root, result
