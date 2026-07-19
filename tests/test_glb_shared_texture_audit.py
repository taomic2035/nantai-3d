"""Adversarial audit of GLBs with exact shared PNG dependency closures."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import struct
from pathlib import Path

import pytest
from PIL import Image

from pipeline.synthetic_village.glb_material_audit import ExpectedGlbMaterial
from pipeline.synthetic_village.glb_shared_texture_audit import (
    SharedTextureGlbAuditError,
    audit_shared_textured_glb,
    hydrate_shared_texture_glb,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    TextureBindingV2,
    TextureObjectV2,
)

BUNDLE_ID = "2" * 64
SOURCE_SHA256 = "1" * 64


def _glb(document: dict[str, object], binary: bytes) -> bytes:
    json_bytes = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    json_bytes += b" " * (-len(json_bytes) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return b"".join(
        (
            struct.pack("<4sII", b"glTF", 2, total),
            struct.pack("<I4s", len(json_bytes), b"JSON"),
            json_bytes,
            struct.pack("<I4s", len(binary), b"BIN\0"),
            binary,
        ),
    )


def _png_bytes(
    role: str,
    *,
    foliage: bool,
    alpha_coverage: float = 0.30,
) -> bytes:
    output = io.BytesIO()
    colours = {
        "base_color": (57, 112, 49),
        "normal": (128, 128, 255),
        "orm": (255, 192, 0),
    }
    if role == "base_color" and foliage:
        image = Image.new("RGBA", (1024, 1024), (*colours[role], 0))
        opaque_rows = round(1024 * alpha_coverage)
        image.paste(
            Image.new("RGBA", (1024, opaque_rows), (*colours[role], 255)),
            (0, 0),
        )
    else:
        image = Image.new("RGB", (1024, 1024), colours[role])
    image.save(output, format="PNG", compress_level=9, optimize=False)
    return output.getvalue()


def _fixture(
    root: Path,
    *,
    kind: str = "building",
    positions: tuple[tuple[float, float, float], ...] = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    ),
    indices: tuple[int, ...] = (0, 1, 2),
    alpha_coverage: float = 0.30,
) -> tuple[
    Path,
    bytes,
    dict[str, object],
    tuple[TextureBindingV2, ...],
    tuple[TextureObjectV2, ...],
    tuple[ExpectedGlbMaterial, ...],
]:
    foliage = kind == "vegetation"
    slot_id = (
        "material-bamboo-leaf-01"
        if foliage
        else "material-fieldstone-01"
    )
    texture_root = root / "texture-root"
    (texture_root / "textures").mkdir(parents=True)
    bindings = []
    objects = []
    dependency_bytes = {}
    for role in ("base_color", "normal", "orm"):
        payload = _png_bytes(
            role,
            foliage=foliage,
            alpha_coverage=alpha_coverage,
        )
        digest = hashlib.sha256(payload).hexdigest()
        dependency_bytes[digest] = payload
        binding = TextureBindingV2(
            uri=f"../textures/{digest}.png",
            sha256=digest,
            role=role,
            colour_space="srgb" if role == "base_color" else "non-color",
            material_slot_id=slot_id,
            derivation_algorithm_id=(
                "deterministic-foliage-cutout-v1"
                if foliage
                else "edge-feather-sobel-orm-v2"
            ),
        )
        bindings.append(binding)
        objects.append(
            TextureObjectV2(
                object_path=f"textures/{digest}.png",
                sha256=digest,
                bytes=len(payload),
            ),
        )
        (texture_root / f"textures/{digest}.png").write_bytes(payload)
    bindings = tuple(
        sorted(
            bindings,
            key=lambda row: (
                row.material_slot_id,
                row.role,
                row.sha256,
                row.derivation_algorithm_id,
            ),
        ),
    )
    objects = tuple(sorted(objects, key=lambda row: row.object_path))

    binary = bytearray()
    views = []

    def append(payload: bytes, *, target: int | None = None) -> int:
        binary.extend(b"\0" * (-len(binary) % 4))
        offset = len(binary)
        binary.extend(payload)
        view = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(payload),
        }
        if target is not None:
            view["target"] = target
        views.append(view)
        return len(views) - 1

    position_view = append(
        b"".join(struct.pack("<3f", *row) for row in positions),
        target=34962,
    )
    normal_view = append(
        b"".join(struct.pack("<3f", 0, 0, 1) for _row in positions),
        target=34962,
    )
    uv_view = append(
        b"".join(struct.pack("<2f", 0, 0) for _row in positions),
        target=34962,
    )
    tangent_view = append(
        b"".join(struct.pack("<4f", 1, 0, 0, 1) for _row in positions),
        target=34962,
    )
    index_view = append(
        b"".join(struct.pack("<H", value) for value in indices),
        target=34963,
    )
    document = {
        "asset": {"generator": "pytest-shared-texture", "version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": [
            {
                "bufferView": position_view,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
            },
            {
                "bufferView": normal_view,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC3",
            },
            {
                "bufferView": uv_view,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC2",
            },
            {
                "bufferView": tangent_view,
                "componentType": 5126,
                "count": len(positions),
                "type": "VEC4",
            },
            {
                "bufferView": index_view,
                "componentType": 5123,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
        "images": [
            {"uri": row.uri, "mimeType": "image/png"}
            for row in bindings
        ],
        "textures": [{"source": index} for index in range(3)],
        "materials": [
            {
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0, "texCoord": 0},
                    "metallicRoughnessTexture": {"index": 2, "texCoord": 0},
                },
                "normalTexture": {"index": 1, "texCoord": 0},
                "extras": {
                    "slot_id": slot_id,
                    "source_sha256": SOURCE_SHA256,
                    "bundle_id": BUNDLE_ID,
                    "algorithm_id": "edge-feather-sobel-orm-v2",
                    "synthetic": True,
                    "uv_policy": "leaf-card" if foliage else "dominant-axis-box",
                },
                **(
                    {
                        "alphaMode": "MASK",
                        "alphaCutoff": 0.45,
                        "doubleSided": True,
                    }
                    if foliage
                    else {}
                ),
            },
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            "TEXCOORD_0": 2,
                            "TANGENT": 3,
                        },
                        "indices": 4,
                        "material": 0,
                        "mode": 4,
                    },
                ],
            },
        ],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    payload = _glb(document, bytes(binary))
    glb_path = root / "external-textures.glb"
    glb_path.write_bytes(payload)
    expected = (
        ExpectedGlbMaterial(
            slot_id=slot_id,
            source_sha256=SOURCE_SHA256,
            bundle_id=BUNDLE_ID,
            algorithm_id="edge-feather-sobel-orm-v2",
        ),
    )
    return (
        glb_path,
        payload,
        document,
        bindings,
        objects,
        expected,
    )


def _audit(
    glb_path: Path,
    *,
    texture_root: Path,
    bindings: tuple[TextureBindingV2, ...],
    objects: tuple[TextureObjectV2, ...],
    expected: tuple[ExpectedGlbMaterial, ...],
    kind: str,
):
    return audit_shared_textured_glb(
        glb_path,
        expected_materials=expected,
        texture_root=texture_root,
        bindings=bindings,
        objects=objects,
        kind=kind,
        footprint_m=(2.0, 2.0, 2.0),
    )


def test_valid_opaque_shared_texture_glb_is_hydrated_in_memory(
    tmp_path: Path,
) -> None:
    glb_path, payload, _document, bindings, objects, expected = _fixture(
        tmp_path,
    )

    audit = _audit(
        glb_path,
        texture_root=tmp_path / "texture-root",
        bindings=bindings,
        objects=objects,
        expected=expected,
        kind="building",
    )

    assert audit.glb_sha256 == hashlib.sha256(payload).hexdigest()
    assert audit.byte_count == len(payload)
    assert audit.triangle_count == 1
    assert audit.slot_ids == ("material-fieldstone-01",)
    dependencies = {
        row.uri: (
            tmp_path / "texture-root" / f"textures/{row.sha256}.png"
        ).read_bytes()
        for row in bindings
    }
    hydrated = hydrate_shared_texture_glb(payload, dependencies)
    assert b"../textures/" not in hydrated
    assert payload == glb_path.read_bytes()


def test_valid_foliage_requires_mask_cutoff_double_side_and_binary_alpha(
    tmp_path: Path,
) -> None:
    glb_path, _payload, _document, bindings, objects, expected = _fixture(
        tmp_path,
        kind="vegetation",
    )

    audit = _audit(
        glb_path,
        texture_root=tmp_path / "texture-root",
        bindings=bindings,
        objects=objects,
        expected=expected,
        kind="vegetation",
    )

    assert audit.foliage_alpha is not None
    assert audit.foliage_alpha.records[0].alpha_coverage == pytest.approx(
        0.30,
        abs=0.001,
    )


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("http-uri", "URI"),
        ("data-uri", "URI"),
        ("query", "URI"),
        ("outside-parent", "URI"),
        ("external-buffer", "buffer"),
        ("missing-binding", "closure"),
        ("duplicate-binding", "closure"),
        ("missing-uv", "TEXCOORD_0"),
        ("missing-tangent", "TANGENT"),
        ("non-indexed", "indexed"),
        ("non-triangle", "triangle"),
        ("unused-mesh", "unused mesh"),
    ),
)
def test_shared_audit_rejects_uri_closure_or_structure_drift(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    glb_path, _payload, document, bindings, objects, expected = _fixture(
        tmp_path,
    )
    mutated = copy.deepcopy(document)
    mutable_bindings = bindings
    mutable_objects = objects
    primitive = mutated["meshes"][0]["primitives"][0]
    if case == "http-uri":
        mutated["images"][0]["uri"] = "https://example.invalid/map.png"
    elif case == "data-uri":
        mutated["images"][0]["uri"] = "data:image/png;base64,AAAA"
    elif case == "query":
        mutated["images"][0]["uri"] += "?v=1"
    elif case == "outside-parent":
        mutated["images"][0]["uri"] = "../../textures/map.png"
    elif case == "external-buffer":
        mutated["buffers"][0]["uri"] = "geometry.bin"
    elif case == "missing-binding":
        mutable_bindings = bindings[:-1]
        mutable_objects = tuple(
            row
            for row in objects
            if row.sha256 in {binding.sha256 for binding in mutable_bindings}
        )
    elif case == "duplicate-binding":
        mutable_bindings = (*bindings, bindings[0])
    elif case == "missing-uv":
        primitive["attributes"].pop("TEXCOORD_0")
    elif case == "missing-tangent":
        primitive["attributes"].pop("TANGENT")
    elif case == "non-indexed":
        primitive.pop("indices")
    elif case == "non-triangle":
        primitive["mode"] = 1
    elif case == "unused-mesh":
        mutated["meshes"].append(copy.deepcopy(mutated["meshes"][0]))
    else:  # pragma: no cover - parametrization is closed
        raise AssertionError(case)
    if case not in {"missing-binding", "duplicate-binding"}:
        _rewrite_with_original_binary(glb_path, mutated)

    with pytest.raises(SharedTextureGlbAuditError, match=message):
        _audit(
            glb_path,
            texture_root=tmp_path / "texture-root",
            bindings=mutable_bindings,
            objects=mutable_objects,
            expected=expected,
            kind="building",
        )


def _rewrite_with_original_binary(
    glb_path: Path,
    document: dict[str, object],
) -> None:
    raw = glb_path.read_bytes()
    json_length = struct.unpack_from("<I", raw, 12)[0]
    binary_length = struct.unpack_from("<I", raw, 20 + json_length)[0]
    binary_start = 28 + json_length
    binary = raw[binary_start : binary_start + binary_length]
    glb_path.write_bytes(_glb(document, binary))


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("blend", "MASK"),
        ("cutoff", "cutoff"),
        ("single-sided", "double-sided"),
    ),
)
def test_foliage_rejects_material_mode_drift(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    glb_path, _payload, document, bindings, objects, expected = _fixture(
        tmp_path,
        kind="vegetation",
    )
    material = document["materials"][0]
    if case == "blend":
        material["alphaMode"] = "BLEND"
    elif case == "cutoff":
        material["alphaCutoff"] = 0.5
    else:
        material["doubleSided"] = False
    _rewrite_with_original_binary(glb_path, document)

    with pytest.raises(SharedTextureGlbAuditError, match=message):
        _audit(
            glb_path,
            texture_root=tmp_path / "texture-root",
            bindings=bindings,
            objects=objects,
            expected=expected,
            kind="vegetation",
        )


@pytest.mark.parametrize(
    ("positions", "indices", "message"),
    (
        (
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (0, 0, 2),
            "degenerate",
        ),
        (
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (0, 1, 2, 0, 1, 2),
            "duplicate",
        ),
        (
            ((0.0, 0.0, 0.0), (math.nan, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (0, 1, 2),
            "non-finite",
        ),
        (
            ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            (0, 1, 2),
            "footprint",
        ),
    ),
)
def test_shared_audit_rejects_bad_topology_or_footprint(
    tmp_path: Path,
    positions: tuple[tuple[float, float, float], ...],
    indices: tuple[int, ...],
    message: str,
) -> None:
    glb_path, _payload, _document, bindings, objects, expected = _fixture(
        tmp_path,
        positions=positions,
        indices=indices,
    )

    with pytest.raises(SharedTextureGlbAuditError, match=message):
        _audit(
            glb_path,
            texture_root=tmp_path / "texture-root",
            bindings=bindings,
            objects=objects,
            expected=expected,
            kind="building",
        )


def test_shared_audit_rejects_tampered_redirected_or_bad_alpha_texture(
    tmp_path: Path,
) -> None:
    glb_path, _payload, _document, bindings, objects, expected = _fixture(
        tmp_path,
        kind="vegetation",
    )
    base = next(row for row in objects if row.sha256 == bindings[0].sha256)
    target = tmp_path / "texture-root" / base.object_path
    original = target.read_bytes()
    target.write_bytes(original + b"\0")
    with pytest.raises(SharedTextureGlbAuditError, match="texture"):
        _audit(
            glb_path,
            texture_root=tmp_path / "texture-root",
            bindings=bindings,
            objects=objects,
            expected=expected,
            kind="vegetation",
        )

    target.write_bytes(original)
    redirected_target = tmp_path / "redirect.png"
    target.rename(redirected_target)
    target.symlink_to(redirected_target)
    with pytest.raises(SharedTextureGlbAuditError, match="redirect"):
        _audit(
            glb_path,
            texture_root=tmp_path / "texture-root",
            bindings=bindings,
            objects=objects,
            expected=expected,
            kind="vegetation",
        )


@pytest.mark.parametrize("coverage", (0.0, 1.0, 0.10, 0.80))
def test_foliage_rejects_missing_uniform_or_out_of_band_alpha(
    tmp_path: Path,
    coverage: float,
) -> None:
    glb_path, _payload, _document, bindings, objects, expected = _fixture(
        tmp_path,
        kind="vegetation",
        alpha_coverage=coverage,
    )

    with pytest.raises(SharedTextureGlbAuditError, match="alpha"):
        _audit(
            glb_path,
            texture_root=tmp_path / "texture-root",
            bindings=bindings,
            objects=objects,
            expected=expected,
            kind="vegetation",
        )
