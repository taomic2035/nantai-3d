"""KTX2-only GLB rewrites must preserve exact geometry identity."""

from __future__ import annotations

import copy
import hashlib
import struct
from types import SimpleNamespace

import pytest

from pipeline.synthetic_village.glb_ktx2_variant import (
    GlbKtx2VariantError,
    geometry_fingerprint_glb,
    rewrite_glb_for_ktx2,
)
from pipeline.synthetic_village.glb_material_audit import _load_glb_bytes
from tests.test_glb_shared_texture_audit import _fixture, _glb


def _replacement(binding):
    payload = f"{binding.material_slot_id}:{binding.role}:ktx2".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return SimpleNamespace(
        role=binding.role,
        object_path=f"objects/{digest}.ktx2",
        sha256=digest,
        bytes=len(payload),
        width=4096,
        height=4096,
        media_type="image/ktx2",
        transfer="srgb" if binding.role == "base_color" else "linear",
    )


def _replacements(bindings):
    return {binding.uri: _replacement(binding) for binding in bindings}


def test_rewrite_uses_basisu_and_preserves_geometry(tmp_path) -> None:
    _path, fallback, _document, bindings, _objects, _expected = _fixture(
        tmp_path,
    )
    replacements = _replacements(bindings)

    primary = rewrite_glb_for_ktx2(fallback, replacements)
    _raw, document, primary_binary = _load_glb_bytes(primary)
    _raw, _fallback_document, fallback_binary = _load_glb_bytes(fallback)

    assert document["extensionsUsed"] == ["KHR_texture_basisu"]
    assert "extensionsRequired" not in document
    assert primary_binary == fallback_binary
    assert [(image["uri"], image["mimeType"]) for image in document["images"][:3]] == [
        (binding.uri, "image/png") for binding in bindings
    ]
    assert [(image["uri"], image["mimeType"]) for image in document["images"][3:]] == [
        (
            f"../textures/{replacements[binding.uri].sha256}.ktx2",
            "image/ktx2",
        )
        for binding in bindings
    ]
    assert [texture["source"] for texture in document["textures"]] == list(
        range(3),
    )
    assert [texture["extensions"]["KHR_texture_basisu"] for texture in document["textures"]] == [
        {"source": index} for index in range(3, 6)
    ]
    assert geometry_fingerprint_glb(primary) == geometry_fingerprint_glb(
        fallback,
    )


def test_geometry_fingerprint_changes_only_with_geometry(tmp_path) -> None:
    _path, fallback, document, bindings, _objects, _expected = _fixture(
        tmp_path,
    )
    _raw, _parsed, binary = _load_glb_bytes(fallback)

    texture_only = copy.deepcopy(document)
    texture_only["images"][0]["uri"] = "../textures/" + "a" * 64 + ".png"
    assert geometry_fingerprint_glb(_glb(texture_only, binary)) == (
        geometry_fingerprint_glb(fallback)
    )

    changed_binary = bytearray(binary)
    struct.pack_into("<f", changed_binary, 0, 0.25)
    assert geometry_fingerprint_glb(_glb(document, bytes(changed_binary))) != (
        geometry_fingerprint_glb(fallback)
    )

    primary = rewrite_glb_for_ktx2(fallback, _replacements(bindings))
    assert geometry_fingerprint_glb(primary) == geometry_fingerprint_glb(
        fallback,
    )


def test_rewrite_accepts_mixed_subset_but_rejects_extra_or_unsafe_closure(
    tmp_path,
) -> None:
    _path, fallback, document, bindings, _objects, _expected = _fixture(
        tmp_path,
    )
    replacements = _replacements(bindings)

    partial = {bindings[0].uri: replacements[bindings[0].uri]}
    mixed = rewrite_glb_for_ktx2(fallback, partial)
    _raw, mixed_document, _binary = _load_glb_bytes(mixed)
    assert len(mixed_document["images"]) == 4
    assert "KHR_texture_basisu" in mixed_document["textures"][0]["extensions"]
    assert all(
        "KHR_texture_basisu" not in texture.get("extensions", {})
        for texture in mixed_document["textures"][1:]
    )

    extra = dict(replacements)
    extra["../textures/" + "f" * 64 + ".png"] = _replacement(bindings[0])
    with pytest.raises(GlbKtx2VariantError, match="closure"):
        rewrite_glb_for_ktx2(fallback, extra)

    unsafe_document = copy.deepcopy(document)
    unsafe_document["images"][0]["uri"] = "../../escape.png"
    unsafe = _glb(
        unsafe_document,
        _load_glb_bytes(fallback)[2],
    )
    unsafe_replacements = dict(replacements)
    unsafe_replacements["../../escape.png"] = unsafe_replacements.pop(
        bindings[0].uri,
    )
    with pytest.raises(GlbKtx2VariantError, match="URI"):
        rewrite_glb_for_ktx2(unsafe, unsafe_replacements)


def test_rewrite_rejects_swapped_texture_roles(tmp_path) -> None:
    _path, fallback, _document, bindings, _objects, _expected = _fixture(
        tmp_path,
    )
    replacements = _replacements(bindings)
    replacements[bindings[0].uri], replacements[bindings[1].uri] = (
        replacements[bindings[1].uri],
        replacements[bindings[0].uri],
    )

    with pytest.raises(GlbKtx2VariantError, match="role"):
        rewrite_glb_for_ktx2(fallback, replacements)
