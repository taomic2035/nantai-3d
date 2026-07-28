from __future__ import annotations

import copy

import pytest

from pipeline.production_release_contract import (
    ProductionReleaseContractError,
    build_production_receipt,
    load_production_receipt_bytes,
    load_public_evidence_bytes,
    validate_public_evidence,
)
from pipeline.release_archive import canonical_json_bytes
from tests.production_release_fixtures import (
    MODELED_ACCEPTANCE_SHA,
    MODELED_SCENE_ID,
    modeled_artifact_records,
    modeled_entrypoints,
    modeled_public_evidence,
)


def _build_receipt(**updates):
    arguments = {
        "version": "v0.0.0",
        "source_commit": "a" * 40,
        "artifacts": modeled_artifact_records(),
        "protected_roots": ("web", "scripts", "pipeline", "evidence"),
        "entrypoints": modeled_entrypoints(),
        "public_evidence": modeled_public_evidence(),
    }
    arguments.update(updates)
    return build_production_receipt(**arguments)


def test_public_evidence_round_trips_as_one_exact_canonical_contract() -> None:
    evidence = modeled_public_evidence()

    validated = validate_public_evidence(evidence)
    payload = canonical_json_bytes(validated)

    assert validated == evidence
    assert load_public_evidence_bytes(payload) == evidence
    assert validated["acceptance"]["report_sha256"] == MODELED_ACCEPTANCE_SHA
    assert validated["scene"]["scene_identity"] == MODELED_SCENE_ID


def test_production_receipt_is_content_addressed_and_artifacts_are_sorted() -> None:
    receipt = _build_receipt()
    payload = canonical_json_bytes(receipt)

    assert load_production_receipt_bytes(payload) == receipt
    assert receipt["schema"] == "nantai.production-runtime-release.v1"
    assert receipt["package"]["layout"] == "nantai.production-runtime.v1"
    assert receipt["package"]["immutable"] is True
    assert len(receipt["package"]["content_id"]) == 64
    assert [row["path"] for row in receipt["artifacts"]] == sorted(
        row["path"] for row in receipt["artifacts"]
    )
    assert receipt["scene"] == {
        "scene_identity": MODELED_SCENE_ID,
        "source_role": "production-acceptance",
        "quality_role": "production",
        "geometry_usability": "metric-aligned",
        "units": "meters",
        "alignment_status": "aligned",
        "trust_effect": "none",
    }
    assert receipt["acceptance"]["production_release_allowed"] is True


def test_production_receipt_rejects_preview_version() -> None:
    with pytest.raises(ProductionReleaseContractError, match="version"):
        _build_receipt(version="v1.0.0-preview.2")


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("acceptance", "source_role"), "internal-canary", "source role"),
        (
            ("acceptance", "production_release_allowed"),
            False,
            "release",
        ),
        (("scene", "quality_role"), "preview-only", "quality"),
        (
            ("scene", "geometry_usability"),
            "preview-only",
            "geometry",
        ),
        (("scene", "units"), "arbitrary", "units"),
        (("render", "accepted"), False, "render"),
        (("viewer", "schema"), "nantai.viewer-performance-report.v1", "Viewer"),
        (("human_review", "accepted"), False, "human"),
    ),
)
def test_public_evidence_rejects_trust_promotion_inputs(
    path: tuple[str, str],
    value: object,
    message: str,
) -> None:
    evidence = copy.deepcopy(modeled_public_evidence())
    evidence[path[0]][path[1]] = value

    with pytest.raises(ProductionReleaseContractError, match=message):
        validate_public_evidence(evidence)


def test_receipt_rejects_casefold_artifact_collision() -> None:
    artifacts = modeled_artifact_records()
    duplicate = dict(artifacts[0])
    duplicate["path"] = str(duplicate["path"]).upper()

    with pytest.raises(ProductionReleaseContractError, match="case-fold"):
        _build_receipt(artifacts=(*artifacts, duplicate))


def test_receipt_rejects_combined_casefold_normalization_collision() -> None:
    artifacts = modeled_artifact_records()
    upper_nfc = dict(artifacts[0])
    upper_nfc["path"] = "evidence/\xc9.txt"
    lower_nfd = dict(artifacts[0])
    lower_nfd["path"] = "evidence/e\u0301.txt"

    with pytest.raises(
        ProductionReleaseContractError,
        match="case-fold/normalization",
    ):
        _build_receipt(artifacts=(*artifacts, upper_nfc, lower_nfd))


def test_public_evidence_loader_rejects_noncanonical_and_duplicate_json() -> None:
    payload = canonical_json_bytes(modeled_public_evidence())

    with pytest.raises(ProductionReleaseContractError, match="canonical"):
        load_public_evidence_bytes(payload.replace(b":", b": ", 1))

    with pytest.raises(ProductionReleaseContractError, match="duplicate"):
        load_public_evidence_bytes(
            payload.replace(
                b'{"acceptance":',
                b'{"schema":"nantai.production-public-evidence.v1",'
                b'"acceptance":',
                1,
            )
        )


def test_receipt_loader_rejects_content_id_tampering() -> None:
    receipt = _build_receipt()
    receipt["package"]["content_id"] = "f" * 64

    with pytest.raises(ProductionReleaseContractError, match="content ID"):
        load_production_receipt_bytes(canonical_json_bytes(receipt))


def test_public_evidence_rejects_unknown_private_field() -> None:
    evidence = modeled_public_evidence()
    evidence["operator_path"] = "C:/private/operator"

    with pytest.raises(ProductionReleaseContractError, match="fields"):
        validate_public_evidence(evidence)
