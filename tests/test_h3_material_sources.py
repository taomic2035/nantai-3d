"""Fail-closed contracts for private H3 AI material source packs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from pipeline.synthetic_village.h3_material_sources import (
    H3_CANDIDATE_AUDIT_ALGORITHM_ID,
    H3_GENERATION_POLICY_ID,
    H3_HERO_SLOTS,
    H3_SOURCE_PACK_SCHEMA,
    H3MaterialSourceError,
    audit_h3_candidate,
    canonical_h3_source_pack_bytes,
    load_h3_source_pack,
    prepare_h3_source_pack,
    read_verified_h3_source,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_candidate(path: Path, *, colour: tuple[int, ...]) -> dict[str, object]:
    mode = "RGBA" if len(colour) == 4 else "RGB"
    Image.new(mode, (1024, 1024), colour).save(path, format="PNG")
    payload = path.read_bytes()
    descriptor = {
        "source_path": path.name,
        "sha256": _sha256(payload),
        "bytes": len(payload),
        "width": 1024,
        "height": 1024,
        "media_type": "image/png",
    }
    descriptor["audit"] = audit_h3_candidate(path).model_dump(mode="json")
    return descriptor


def _replace_candidate_with_edge_outlier(
    receipt: Path,
    *,
    selected: bool,
) -> None:
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    record = payload["records"][0]
    candidate = record["candidates"][0]
    candidate_path = receipt.parent / candidate["source_path"]
    image = Image.new("RGB", (1024, 1024), (16, 16, 16))
    image.paste((240, 240, 240), (512, 0, 1024, 1024))
    image.save(candidate_path, format="PNG")
    descriptor = {
        "source_path": candidate["source_path"],
        "sha256": _sha256(candidate_path.read_bytes()),
        "bytes": candidate_path.stat().st_size,
        "width": 1024,
        "height": 1024,
        "media_type": "image/png",
        "audit": audit_h3_candidate(candidate_path).model_dump(mode="json"),
    }
    record["candidates"][0] = descriptor
    payload["audit_policy"]["maximum_opposite_edge_disagreement"] = 0.1
    if selected:
        record["selection"]["selected_candidate_sha256"] = descriptor["sha256"]
    receipt.write_bytes(
        (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )


def _write_selection_receipt(root: Path) -> Path:
    candidates_root = root / "candidates"
    candidates_root.mkdir(parents=True)
    records: list[dict[str, object]] = []
    for slot_index, slot_id in enumerate(H3_HERO_SLOTS):
        candidates = []
        for candidate_index in range(3):
            value = 32 + slot_index * 17 + candidate_index * 3
            path = candidates_root / f"{slot_id}-{candidate_index + 1}.png"
            descriptor = _write_candidate(
                path,
                colour=(value, min(value + 20, 255), min(value + 40, 255)),
            )
            descriptor["source_path"] = f"candidates/{path.name}"
            candidates.append(descriptor)
        prompt = (
            f"Synthetic orthographic material candidate for {slot_id}; "
            "flat diffuse illumination, no text, logo, object, border, "
            "perspective corner, scene horizon, or real-photo claim."
        )
        records.append(
            {
                "slot_id": slot_id,
                "prompt": prompt,
                "prompt_sha256": _sha256(prompt.encode("utf-8")),
                "generator_product": "openai-image-generation",
                "generator_version": None,
                "generator_version_evidence": (
                    "not-exposed-by-generation-response"
                ),
                "candidates": candidates,
                "selection": {
                    "selected_candidate_sha256": candidates[1]["sha256"],
                    "review_kind": "human-visual-review",
                    "review_reason": (
                        "Candidate has the clearest material scale and least "
                        "baked directional lighting."
                    ),
                    "trust_effect": "none-appearance-only",
                },
                "rights_review": {
                    "status": "private-project-use-only",
                    "evidence": "user-approved-ai-generation-workflow",
                    "public_release_authorized": False,
                },
            },
        )
    receipt = root / "selection-receipt.json"
    receipt.write_bytes(
        (
            json.dumps(
                {
                    "schema_version": (
                        "nantai.h3-ai-material-selection-receipt.v1"
                    ),
                    "generation_policy_id": H3_GENERATION_POLICY_ID,
                    "synthetic": True,
                    "ai_generated": True,
                    "real_photo_textures": False,
                    "audit_policy": {
                        "policy_id": "h3-ai-candidate-audit-policy-v1",
                        "algorithm_id": H3_CANDIDATE_AUDIT_ALGORITHM_ID,
                        "minimum_width": 1024,
                        "minimum_height": 1024,
                        "maximum_alpha_nonopaque_fraction": 0.0,
                        "maximum_clipped_fraction": 0.02,
                        "maximum_dominant_perspective_score": 0.35,
                        "maximum_edge_energy": 1.0,
                        "maximum_opposite_edge_disagreement": 1.0,
                        "frozen_before_selection": True,
                    },
                    "records": records,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    )
    return receipt


def test_candidate_audit_is_deterministic_and_bounded(tmp_path: Path) -> None:
    source = tmp_path / "candidate.png"
    _write_candidate(source, colour=(64, 96, 128))

    first = audit_h3_candidate(source)
    second = audit_h3_candidate(source)

    assert first == second
    assert first.algorithm_id == H3_CANDIDATE_AUDIT_ALGORITHM_ID
    assert (first.width, first.height, first.colour_mode) == (1024, 1024, "RGB")
    assert first.alpha_nonopaque_fraction == 0.0
    assert 0.0 <= first.clipped_fraction <= 1.0
    assert 0.0 <= first.contrast_stddev <= 1.0
    assert 0.0 <= first.edge_energy <= 1.0
    assert 0.0 <= first.dominant_perspective_score <= 1.0
    assert 0.0 <= first.opposite_edge_disagreement <= 1.0


def test_nonselected_audit_outlier_stays_private_without_blocking_pack(
    tmp_path: Path,
) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    _replace_candidate_with_edge_outlier(receipt, selected=False)

    prepared = prepare_h3_source_pack(receipt, tmp_path / "published")

    first_record = prepared.manifest.records[0]
    assert first_record.selection.selected_candidate_sha256 != json.loads(
        receipt.read_text(encoding="utf-8"),
    )["records"][0]["candidates"][0]["sha256"]


def test_selected_audit_outlier_fails_closed(tmp_path: Path) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    _replace_candidate_with_edge_outlier(receipt, selected=True)

    with pytest.raises(
        H3MaterialSourceError,
        match="selected candidate exceeds the frozen opposite-edge threshold",
    ):
        prepare_h3_source_pack(receipt, tmp_path / "published")


def _mutate_receipt(path: Path, mutation) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_bytes(
        (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )


def test_prepare_source_pack_publishes_only_exact_selected_bytes(
    tmp_path: Path,
) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    prepared = prepare_h3_source_pack(receipt, tmp_path / "published")

    assert prepared.manifest.schema_version == H3_SOURCE_PACK_SCHEMA
    assert tuple(
        record.slot_id for record in prepared.manifest.records
    ) == H3_HERO_SLOTS
    assert prepared.root == tmp_path / "published" / prepared.manifest.source_pack_id
    assert all(
        record.selection.candidate_count == 3
        for record in prepared.manifest.records
    )
    assert all(
        record.rights_review.public_release_authorized is False
        for record in prepared.manifest.records
    )
    assert tuple(
        sorted(path.name for path in (prepared.root / "sources").iterdir())
    ) == tuple(
        sorted(
            record.native_source.object_path.removeprefix("sources/")
            for record in prepared.manifest.records
        ),
    )

    canonical = canonical_h3_source_pack_bytes(prepared.manifest)
    assert str(tmp_path).encode() not in canonical
    assert b"-1.png" not in canonical
    assert b"-3.png" not in canonical

    loaded = load_h3_source_pack(prepared.root)
    assert loaded == prepared.manifest
    for record in loaded.records:
        payload = read_verified_h3_source(
            prepared.root,
            pack=loaded,
            slot_id=record.slot_id,
        )
        assert _sha256(payload) == record.native_source.sha256


def test_prepare_source_pack_is_content_idempotent(tmp_path: Path) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    first = prepare_h3_source_pack(receipt, tmp_path / "published")
    second = prepare_h3_source_pack(receipt, tmp_path / "published")

    assert second == first
    assert canonical_h3_source_pack_bytes(first.manifest) == (
        (first.root / "manifest.json").read_bytes()
    )


@pytest.mark.parametrize(
    "mutation, match",
    (
        (
            lambda payload: payload["records"][0]["candidates"].pop(),
            "exactly 3",
        ),
        (
            lambda payload: payload["records"][0]["selection"].update(
                selected_candidate_sha256="f" * 64,
            ),
            "selected candidate",
        ),
        (
            lambda payload: payload["records"][0].update(
                prompt_sha256="0" * 64,
            ),
            "prompt SHA-256",
        ),
        (
            lambda payload: payload["records"][0].update(
                generator_version_evidence="inferred-from-filename",
            ),
            "generator version evidence",
        ),
        (
            lambda payload: payload["records"][0]["rights_review"].update(
                public_release_authorized=True,
            ),
            "public release",
        ),
        (
            lambda payload: payload["records"][0]["candidates"][0].update(
                source_path="../escape.png",
            ),
            "portable relative",
        ),
    ),
)
def test_prepare_source_pack_rejects_untrusted_receipt(
    tmp_path: Path,
    mutation,
    match: str,
) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    _mutate_receipt(receipt, mutation)

    with pytest.raises(H3MaterialSourceError, match=match):
        prepare_h3_source_pack(receipt, tmp_path / "published")
    assert not (tmp_path / "published").exists()


def test_prepare_source_pack_rejects_nonopaque_candidate(tmp_path: Path) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    candidate = payload["records"][0]["candidates"][0]
    candidate_path = receipt.parent / candidate["source_path"]
    descriptor = _write_candidate(candidate_path, colour=(20, 30, 40, 128))
    descriptor["source_path"] = candidate["source_path"]
    payload["records"][0]["candidates"][0] = descriptor
    receipt.write_bytes(
        (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )

    with pytest.raises(H3MaterialSourceError, match="opaque"):
        prepare_h3_source_pack(receipt, tmp_path / "published")


def test_prepare_source_pack_retains_opaque_rgba_mode_and_bytes(
    tmp_path: Path,
) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    candidate = payload["records"][0]["candidates"][0]
    candidate_path = receipt.parent / candidate["source_path"]
    descriptor = _write_candidate(candidate_path, colour=(20, 30, 40, 255))
    descriptor["source_path"] = candidate["source_path"]
    payload["records"][0]["candidates"][0] = descriptor
    payload["records"][0]["selection"]["selected_candidate_sha256"] = (
        descriptor["sha256"]
    )
    receipt.write_bytes(
        (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )

    prepared = prepare_h3_source_pack(receipt, tmp_path / "published")
    record = prepared.manifest.records[0]
    assert record.native_source.mode == "RGBA"
    assert (
        prepared.root / record.native_source.object_path
    ).read_bytes() == candidate_path.read_bytes()


def test_load_source_pack_rejects_tampered_selected_bytes(tmp_path: Path) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    prepared = prepare_h3_source_pack(receipt, tmp_path / "published")
    selected = prepared.manifest.records[0].native_source
    (prepared.root / selected.object_path).write_bytes(b"tampered")

    with pytest.raises(H3MaterialSourceError, match="SHA-256"):
        load_h3_source_pack(prepared.root)


def test_load_source_pack_rejects_extra_empty_directory(tmp_path: Path) -> None:
    receipt = _write_selection_receipt(tmp_path / "input")
    prepared = prepare_h3_source_pack(receipt, tmp_path / "published")
    (prepared.root / "untracked").mkdir()

    with pytest.raises(H3MaterialSourceError, match="directory closure"):
        load_h3_source_pack(prepared.root)
