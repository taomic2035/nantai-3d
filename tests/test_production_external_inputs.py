"""Tests for the canonical blocked external inputs report (NOW-7 D1).

The previous draft used placeholder host ``gpu-host``, repeated
character SHA values and a self-claimed ``rights_clearance="rights-cleared"``
literal.  That violated provenance fail closed: missing values must not
be replaced by format-correct placeholders, and the blocked report has
no authority to derive rights clearance.  These tests pin the new
closed-enum contract:

- Top-level ``state="blocked-external-input"`` plus a sorted, unique
  ``requirements`` list covering all six ``RequirementId`` values.
- Each entry uses closed ``requirement_id``, ``state`` and
  ``reason_code`` enums — never free text.
- ``missing`` and ``unknown`` states bind no identity fields; canonical
  JSON contains no host, digest or SHA placeholder.
- ``present-unverified`` only binds operator input content SHA(s); it
  must not claim ``rights-cleared``, ``ready``, ``metric-aligned`` or
  release-allowed status.
- Rights clearance is recorded as "needs source/rights receipt" or two
  content SHAs; the actual clearance derivation lives in
  ``pipeline.real_dataset.validate_capture_rights``.
- Default CLI invocation emits a canonical blocked report listing all
  six requirements as ``missing`` without any external values.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.production_external_inputs import (
    ProductionExternalInputsError,
    ReasonCode,
    RequirementEntry,
    RequirementId,
    RequirementState,
    blocked_report_full_canonical_bytes,
    blocked_report_signing_bytes,
    build_blocked_report,
    build_default_blocked_report,
    compute_blocked_report_sha256,
    load_blocked_report,
    publish_blocked_report,
)
from pipeline.production_external_inputs import (
    main as cli_main,
)

_ROOT = Path(__file__).resolve().parents[1]
_SHA_A = "a" * 64
_SHA_B = "b" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_missing_entries() -> list[RequirementEntry]:
    return [
        RequirementEntry(
            requirement_id=rid,
            state=RequirementState.MISSING,
            reason_code=_default_reason_for(rid),
        )
        for rid in sorted(RequirementId, key=lambda r: r.value)
    ]


def _default_reason_for(rid: RequirementId) -> ReasonCode:
    return {
        RequirementId.SSH_ENDPOINT: ReasonCode.NO_OPERATOR_INPUT_BOUND,
        RequirementId.IMMUTABLE_CUDA_IMAGE: ReasonCode.NO_CONTAINER_DIGEST_BOUND,
        RequirementId.PRODUCTION_DATASET: ReasonCode.NO_DATASET_CONTENT_BOUND,
        RequirementId.NERFSTUDIO_SPLATFACTO: ReasonCode.NERFSTUDIO_REQUIREMENT_UNVERIFIED,
        RequirementId.NON_COPLANAR_CONTROL_POINTS: ReasonCode.NO_CONTROL_POINTS_MEASURED,
        RequirementId.VIEWER_HUMAN_ACCEPTANCE: ReasonCode.NO_VIEWER_ACCEPTANCE_EVIDENCE,
    }[rid]


def _present_unverified(
    rid: RequirementId,
    *,
    sha: str = _SHA_A,
    rights_receipt_sha: str | None = None,
) -> RequirementEntry:
    kwargs: dict[str, object] = {
        "requirement_id": rid,
        "state": RequirementState.PRESENT_UNVERIFIED,
        "reason_code": ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
        "operator_input_content_sha256": sha,
    }
    if rights_receipt_sha is not None:
        kwargs["rights_receipt_content_sha256"] = rights_receipt_sha
    return RequirementEntry(**kwargs)


def _entries_with(
    overrides: dict[RequirementId, RequirementEntry],
) -> list[RequirementEntry]:
    entries: dict[RequirementId, RequirementEntry] = {
        rid: RequirementEntry(
            requirement_id=rid,
            state=RequirementState.MISSING,
            reason_code=_default_reason_for(rid),
        )
        for rid in RequirementId
    }
    entries.update(overrides)
    return [entries[rid] for rid in sorted(RequirementId, key=lambda r: r.value)]


# ---------------------------------------------------------------------------
# NOW-7 RED: missing endpoint never requires or emits placeholder host
# ---------------------------------------------------------------------------


def test_missing_endpoint_never_requires_or_emits_placeholder_host():
    """Default blocked report must not require or emit any host string.

    The old draft required a ``host`` parameter for every blocked report
    and tests invented ``gpu-host``.  The new contract has no host
    field; ``missing`` state binds no identity.
    """
    report = build_default_blocked_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    # No placeholder host strings may appear in canonical JSON
    assert "gpu-host" not in text
    assert "host" not in text.lower().split('"')
    # No SSH-specific identity fields
    ssh_entry = next(
        e for e in report.requirements if e.requirement_id == RequirementId.SSH_ENDPOINT
    )
    assert ssh_entry.state == RequirementState.MISSING
    assert ssh_entry.operator_input_content_sha256 is None
    assert ssh_entry.rights_receipt_content_sha256 is None
    # No 'host' field anywhere in the parsed payload
    payload = json.loads(text)
    _assert_no_field_named(payload, "host")


def _assert_no_field_named(payload: object, field: str) -> None:
    if isinstance(payload, dict):
        assert field not in payload, f"field {field!r} present in report"
        for value in payload.values():
            _assert_no_field_named(value, field)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_field_named(item, field)


# ---------------------------------------------------------------------------
# NOW-7 RED: missing image never requires or emits placeholder digest
# ---------------------------------------------------------------------------


def test_missing_image_never_requires_or_emits_placeholder_digest():
    """Default blocked report must not require or emit any container digest."""
    report = build_default_blocked_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    assert "sha256:" not in text
    assert "registry.example" not in text
    assert "@sha256:" not in text
    cuda_entry = next(
        e for e in report.requirements
        if e.requirement_id == RequirementId.IMMUTABLE_CUDA_IMAGE
    )
    assert cuda_entry.state == RequirementState.MISSING
    assert cuda_entry.operator_input_content_sha256 is None


# ---------------------------------------------------------------------------
# NOW-7 RED: missing dataset never requires or emits placeholder SHA
# ---------------------------------------------------------------------------


def test_missing_dataset_never_requires_or_emits_placeholder_sha():
    """Default blocked report must not require or emit any dataset SHA."""
    report = build_default_blocked_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    # The only 64-char hex string allowed in canonical bytes is
    # ``report_sha256`` (the content-address of the whole report).
    # No operator_input_content_sha256 or rights_receipt_content_sha256
    # placeholder may appear as a non-null value.
    matches = re.findall(r'"([0-9a-f]{64})"', text)
    assert matches == [report.report_sha256]
    dataset_entry = next(
        e for e in report.requirements
        if e.requirement_id == RequirementId.PRODUCTION_DATASET
    )
    assert dataset_entry.state == RequirementState.MISSING
    assert dataset_entry.operator_input_content_sha256 is None
    assert dataset_entry.rights_receipt_content_sha256 is None


# ---------------------------------------------------------------------------
# NOW-7 RED: rights cannot be claimed without bound source and receipt SHA
# ---------------------------------------------------------------------------


def test_rights_cannot_be_claimed_without_bound_source_and_receipt_sha():
    """``production-dataset`` present-unverified records the need for source/rights.

    The blocked report only records the need for source/rights receipt
    or both content SHAs; whether publishing is allowed is derived by
    ``pipeline.real_dataset.validate_capture_rights``.  The report must
    not self-claim ``rights-cleared``.
    """
    # Source SHA alone (no receipt) is acceptable as present-unverified
    # with a "rights receipt missing" reason code; it must NOT claim clearance.
    entry = RequirementEntry(
        requirement_id=RequirementId.PRODUCTION_DATASET,
        state=RequirementState.PRESENT_UNVERIFIED,
        reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_RIGHTS_RECEIPT_MISSING,
        operator_input_content_sha256=_SHA_A,
    )
    assert entry.rights_receipt_content_sha256 is None

    # Source SHA + receipt SHA is the bound form; still no clearance claim
    entry_bound = RequirementEntry(
        requirement_id=RequirementId.PRODUCTION_DATASET,
        state=RequirementState.PRESENT_UNVERIFIED,
        reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
        operator_input_content_sha256=_SHA_A,
        rights_receipt_content_sha256=_SHA_B,
    )
    report = build_blocked_report(
        entries=_entries_with(
            {RequirementId.PRODUCTION_DATASET: entry_bound}
        )
    )
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    assert "rights-cleared" not in text.lower()

    # Receipt SHA without source SHA is rejected (present-unverified requires source)
    with pytest.raises(ValueError):
        RequirementEntry(
            requirement_id=RequirementId.PRODUCTION_DATASET,
            state=RequirementState.PRESENT_UNVERIFIED,
            reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
            operator_input_content_sha256=None,
            rights_receipt_content_sha256=_SHA_B,
        )


# ---------------------------------------------------------------------------
# NOW-7 RED: partial inputs report only exact unresolved requirement IDs
# ---------------------------------------------------------------------------


def test_partial_inputs_report_only_exact_unresolved_requirement_ids():
    """When only some inputs are bound, others remain ``missing``.

    The report must list all six RequirementId values exactly once; the
    bound ones become ``present-unverified`` and the others stay
    ``missing`` with their original reason codes.  No partial state is
    inferred for unbound requirements.
    """
    entries = _entries_with(
        {
            RequirementId.SSH_ENDPOINT: _present_unverified(
                RequirementId.SSH_ENDPOINT, sha=_SHA_A
            ),
            RequirementId.IMMUTABLE_CUDA_IMAGE: _present_unverified(
                RequirementId.IMMUTABLE_CUDA_IMAGE, sha=_SHA_B
            ),
        }
    )
    report = build_blocked_report(entries=entries)
    by_id = {e.requirement_id: e for e in report.requirements}
    assert len(by_id) == 6
    assert by_id[RequirementId.SSH_ENDPOINT].state == RequirementState.PRESENT_UNVERIFIED
    assert by_id[RequirementId.IMMUTABLE_CUDA_IMAGE].state == RequirementState.PRESENT_UNVERIFIED
    assert by_id[RequirementId.PRODUCTION_DATASET].state == RequirementState.MISSING
    assert by_id[RequirementId.NERFSTUDIO_SPLATFACTO].state == RequirementState.MISSING
    assert by_id[RequirementId.NON_COPLANAR_CONTROL_POINTS].state == RequirementState.MISSING
    assert by_id[RequirementId.VIEWER_HUMAN_ACCEPTANCE].state == RequirementState.MISSING
    assert by_id[RequirementId.PRODUCTION_DATASET].reason_code == (
        ReasonCode.NO_DATASET_CONTENT_BOUND
    )


# ---------------------------------------------------------------------------
# NOW-7 RED: no free text or secret-bearing value fields
# ---------------------------------------------------------------------------


def test_report_has_no_free_text_or_secret_bearing_value_fields():
    """The schema uses closed enums only; no free-text fields exist.

    ``reason_code`` and ``requirement_id`` are closed enums.  Reason
    strings like "system is ready" or "password=xyz" must be rejected
    both at schema level and at canonical-bytes scan level.
    """
    # Free-text reason strings are impossible: reason_code is a closed Enum
    with pytest.raises(ValueError):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.MISSING,
            reason_code="custom free text",
        )
    # Unknown requirement_id strings are rejected
    with pytest.raises(ValueError):
        RequirementEntry(
            requirement_id="custom-requirement",
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
        )

    # Injecting free text via canonical JSON is rejected on load
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["requirements"][0]["note"] = "password=secret"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises(ProductionExternalInputsError):
        load_blocked_report(raw)


# ---------------------------------------------------------------------------
# NOW-7 RED: CLI emits blocked report without any external values
# ---------------------------------------------------------------------------


def test_cli_emits_blocked_report_without_any_external_values(tmp_path):
    """Default CLI invocation must succeed with no operator input flags.

    Output must be canonical, content-addressed, and contain all six
    requirements in ``missing`` state with no placeholder identity.
    """
    output = tmp_path / "blocked.json"
    rc = cli_main(["--output", str(output)])
    assert rc == 0
    assert output.is_file()
    raw = output.read_bytes()
    report = load_blocked_report(raw)
    assert report.state == "blocked-external-input"
    assert len(report.requirements) == 6
    for entry in report.requirements:
        assert entry.state == RequirementState.MISSING
        assert entry.operator_input_content_sha256 is None
        assert entry.rights_receipt_content_sha256 is None
    # No placeholder identity in the canonical bytes; the only 64-char
    # hex allowed is report_sha256 (the report's own content-address).
    text = raw.decode("ascii")
    assert "gpu-host" not in text
    assert "@sha256:" not in text
    matches = re.findall(r'"([0-9a-f]{64})"', text)
    assert matches == [report.report_sha256]


# ---------------------------------------------------------------------------
# Build & round-trip
# ---------------------------------------------------------------------------


def test_build_default_report_round_trips():
    report = build_default_blocked_report()
    raw = blocked_report_full_canonical_bytes(report)
    loaded = load_blocked_report(raw)
    assert loaded == report
    assert loaded.report_sha256 == report.report_sha256


def test_default_report_lists_all_six_requirements_in_order():
    report = build_default_blocked_report()
    ids = [e.requirement_id for e in report.requirements]
    expected = sorted(RequirementId, key=lambda r: r.value)
    assert ids == expected
    assert [e.state for e in report.requirements] == [RequirementState.MISSING] * 6


def test_default_report_canonical_json_is_sorted():
    report = build_default_blocked_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    assert text.endswith("\n")
    payload = json.loads(text)
    assert list(payload.keys()) == sorted(payload.keys())
    assert payload["schema"] == "nantai.blocked-external-inputs.v1"
    assert payload["state"] == "blocked-external-input"


def test_report_sha_is_content_addressed():
    entries_a = _entries_with(
        {RequirementId.SSH_ENDPOINT: _present_unverified(
            RequirementId.SSH_ENDPOINT, sha=_SHA_A
        )}
    )
    entries_b = _entries_with(
        {RequirementId.SSH_ENDPOINT: _present_unverified(
            RequirementId.SSH_ENDPOINT, sha=_SHA_B
        )}
    )
    report_a = build_blocked_report(entries=entries_a)
    report_b = build_blocked_report(entries=entries_b)
    assert report_a.report_sha256 != report_b.report_sha256
    assert compute_blocked_report_sha256(report_a) == report_a.report_sha256


def test_signing_bytes_exclude_sha():
    report = build_default_blocked_report()
    signing = blocked_report_signing_bytes(report)
    assert b"report_sha256" not in signing
    full = blocked_report_full_canonical_bytes(report)
    assert b"report_sha256" in full


# ---------------------------------------------------------------------------
# RequirementId / State / ReasonCode closed enums
# ---------------------------------------------------------------------------


def test_requirement_id_enum_is_closed():
    with pytest.raises(ValueError):
        RequirementId("invalid-requirement")


def test_requirement_state_enum_is_closed():
    with pytest.raises(ValueError):
        RequirementState("ready")


def test_reason_code_enum_is_closed():
    with pytest.raises(ValueError):
        ReasonCode("custom reason")


def test_state_cannot_be_non_blocked():
    with pytest.raises(ValueError):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state="ready",
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
        )


# ---------------------------------------------------------------------------
# RequirementEntry consistency rules
# ---------------------------------------------------------------------------


def test_missing_state_cannot_bind_operator_sha():
    with pytest.raises(ValueError, match="cannot bind operator input SHA"):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
            operator_input_content_sha256=_SHA_A,
        )


def test_missing_state_cannot_bind_rights_receipt_sha():
    with pytest.raises(ValueError, match="cannot bind rights receipt SHA"):
        RequirementEntry(
            requirement_id=RequirementId.PRODUCTION_DATASET,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
            rights_receipt_content_sha256=_SHA_B,
        )


def test_unknown_state_cannot_bind_any_sha():
    with pytest.raises(ValueError, match="cannot bind operator input SHA"):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.UNKNOWN,
            reason_code=ReasonCode.UNKNOWN_PROBE_OUTCOME,
            operator_input_content_sha256=_SHA_A,
        )


def test_present_unverified_requires_operator_sha():
    with pytest.raises(ValueError, match="requires operator_input_content_sha256"):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.PRESENT_UNVERIFIED,
            reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
        )


def test_present_unverified_rejects_non_hex_sha():
    with pytest.raises(ValueError, match="must be 64 hex"):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.PRESENT_UNVERIFIED,
            reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
            operator_input_content_sha256="not-hex",
        )


def test_present_unverified_rejects_placeholder_short_sha():
    """Repeated short placeholder SHAs are not accepted as identity."""
    with pytest.raises(ValueError, match="must be 64 hex"):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.PRESENT_UNVERIFIED,
            reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
            operator_input_content_sha256="aaaa",
        )


def test_rights_receipt_only_valid_for_dataset_requirement():
    with pytest.raises(ValueError, match="only valid for production-dataset"):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.PRESENT_UNVERIFIED,
            reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED,
            operator_input_content_sha256=_SHA_A,
            rights_receipt_content_sha256=_SHA_B,
        )


def test_dataset_present_unverified_allows_source_only():
    """``production-dataset`` present-unverified may bind source SHA only.

    The blocked report records the need for a rights receipt; the
    reason_code distinguishes "receipt missing" from "both SHAs bound".
    The blocked report has no authority to claim rights-cleared status.
    """
    entry = RequirementEntry(
        requirement_id=RequirementId.PRODUCTION_DATASET,
        state=RequirementState.PRESENT_UNVERIFIED,
        reason_code=ReasonCode.OPERATOR_INPUT_BOUND_BUT_RIGHTS_RECEIPT_MISSING,
        operator_input_content_sha256=_SHA_A,
    )
    assert entry.rights_receipt_content_sha256 is None


# ---------------------------------------------------------------------------
# Top-level report validators
# ---------------------------------------------------------------------------


def test_requirements_must_cover_all_six_ids():
    entries = [
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
        ),
    ]
    with pytest.raises(ProductionExternalInputsError, match="must cover all six"):
        build_blocked_report(entries=entries)


def test_requirements_must_not_have_duplicates():
    entries = [
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
        ),
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
        ),
        RequirementEntry(
            requirement_id=RequirementId.IMMUTABLE_CUDA_IMAGE,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_CONTAINER_DIGEST_BOUND,
        ),
        RequirementEntry(
            requirement_id=RequirementId.PRODUCTION_DATASET,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_DATASET_CONTENT_BOUND,
        ),
        RequirementEntry(
            requirement_id=RequirementId.NERFSTUDIO_SPLATFACTO,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NERFSTUDIO_REQUIREMENT_UNVERIFIED,
        ),
        RequirementEntry(
            requirement_id=RequirementId.NON_COPLANAR_CONTROL_POINTS,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_CONTROL_POINTS_MEASURED,
        ),
    ]
    with pytest.raises(ProductionExternalInputsError, match="duplicate"):
        build_blocked_report(entries=entries)


def test_requirements_must_be_sorted():
    """Canonical JSON requires sorted requirement_id order."""
    entries = list(reversed(_all_missing_entries()))
    # Build still sorts internally; load must reject unsorted input
    report = build_blocked_report(entries=entries)
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    payload = json.loads(text)
    # Reverse the requirements in raw JSON
    payload["requirements"] = list(reversed(payload["requirements"]))
    unsorted_raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises(ProductionExternalInputsError):
        load_blocked_report(unsorted_raw)


def test_extra_fields_rejected_at_entry_level():
    with pytest.raises(ValueError):
        RequirementEntry(
            requirement_id=RequirementId.SSH_ENDPOINT,
            state=RequirementState.MISSING,
            reason_code=ReasonCode.NO_OPERATOR_INPUT_BOUND,
            extra_field="evil",
        )


def test_extra_fields_rejected_in_loaded_report():
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["extra"] = "evil"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises(ProductionExternalInputsError):
        load_blocked_report(raw)


# ---------------------------------------------------------------------------
# Duplicate keys & canonical JSON
# ---------------------------------------------------------------------------


def test_duplicate_keys_rejected():
    report = build_default_blocked_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    duplicated = text.replace(
        '"schema"',
        '"schema":"nantai.blocked-external-inputs.v1","schema"',
        1,
    )
    with pytest.raises(ProductionExternalInputsError, match="duplicate keys"):
        load_blocked_report(duplicated.encode("ascii"))


def test_non_canonical_json_rejected():
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    non_canonical = json.dumps(payload, indent=2) + "\n"
    with pytest.raises(ProductionExternalInputsError, match="not canonical JSON"):
        load_blocked_report(non_canonical.encode("ascii"))


def test_wrong_sha_rejected():
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["report_sha256"] = "e" * 64
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises(ProductionExternalInputsError, match="sha256 does not match"):
        load_blocked_report(raw)


def test_non_ascii_rejected():
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["requirements"][0]["reason_code"] = "café"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises((ProductionExternalInputsError, ValueError)):
        load_blocked_report(raw)


def test_load_rejects_non_bytes_input():
    with pytest.raises(ProductionExternalInputsError):
        load_blocked_report(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Forbidden statements and secret scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "ready",
        "verified-production",
        "metric-aligned",
        "release-allowed",
        "rights-cleared",
        "gpu-available",
        "viewer-accepted",
    ],
)
def test_forbidden_statement_injected_via_payload_rejected(forbidden: str):
    """Forbidden statements must not appear in the canonical report.

    We inject the forbidden string into a reason_code field via raw
    JSON manipulation; load_blocked_report must reject it.
    """
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["requirements"][0]["reason_code"] = forbidden
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises((ProductionExternalInputsError, ValueError)):
        load_blocked_report(raw)


def test_secret_like_content_injected_via_payload_rejected():
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["requirements"][0]["reason_code"] = "password=secret123"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises((ProductionExternalInputsError, ValueError)):
        load_blocked_report(raw)


def test_absolute_path_injected_via_payload_rejected():
    report = build_default_blocked_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["requirements"][0]["reason_code"] = "/home/user/secret/path"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises((ProductionExternalInputsError, ValueError)):
        load_blocked_report(raw)


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def test_cli_binds_operator_input_sha(tmp_path):
    output = tmp_path / "blocked.json"
    rc = cli_main(
        [
            "--output",
            str(output),
            "--operator-input-sha256",
            f"ssh-endpoint:{_SHA_A}",
        ]
    )
    assert rc == 0
    report = load_blocked_report(output.read_bytes())
    by_id = {e.requirement_id: e for e in report.requirements}
    assert by_id[RequirementId.SSH_ENDPOINT].state == RequirementState.PRESENT_UNVERIFIED
    assert (
        by_id[RequirementId.SSH_ENDPOINT].operator_input_content_sha256
        == _SHA_A
    )
    assert by_id[RequirementId.IMMUTABLE_CUDA_IMAGE].state == RequirementState.MISSING


def test_cli_binds_dataset_and_receipt(tmp_path):
    output = tmp_path / "blocked.json"
    rc = cli_main(
        [
            "--output",
            str(output),
            "--operator-input-sha256",
            f"production-dataset:{_SHA_A}",
            "--rights-receipt-sha256",
            _SHA_B,
        ]
    )
    assert rc == 0
    report = load_blocked_report(output.read_bytes())
    by_id = {e.requirement_id: e for e in report.requirements}
    entry = by_id[RequirementId.PRODUCTION_DATASET]
    assert entry.state == RequirementState.PRESENT_UNVERIFIED
    assert entry.operator_input_content_sha256 == _SHA_A
    assert entry.rights_receipt_content_sha256 == _SHA_B


def test_cli_rejects_receipt_without_dataset_input(tmp_path):
    output = tmp_path / "blocked.json"
    with pytest.raises(SystemExit):
        cli_main(
            [
                "--output",
                str(output),
                "--rights-receipt-sha256",
                _SHA_B,
            ]
        )


def test_cli_rejects_invalid_operator_sha(tmp_path):
    output = tmp_path / "blocked.json"
    with pytest.raises(SystemExit):
        cli_main(
            [
                "--output",
                str(output),
                "--operator-input-sha256",
                "ssh-endpoint:not-hex",
            ]
        )


def test_cli_rejects_unknown_requirement_id(tmp_path):
    output = tmp_path / "blocked.json"
    with pytest.raises(SystemExit):
        cli_main(
            [
                "--output",
                str(output),
                "--operator-input-sha256",
                f"unknown-requirement:{_SHA_A}",
            ]
        )


def test_cli_state_unknown_marks_requirement_unknown(tmp_path):
    output = tmp_path / "blocked.json"
    rc = cli_main(
        [
            "--output",
            str(output),
            "--state-unknown",
            RequirementId.NERFSTUDIO_SPLATFACTO.value,
        ]
    )
    assert rc == 0
    report = load_blocked_report(output.read_bytes())
    by_id = {e.requirement_id: e for e in report.requirements}
    assert by_id[RequirementId.NERFSTUDIO_SPLATFACTO].state == RequirementState.UNKNOWN
    assert by_id[RequirementId.NERFSTUDIO_SPLATFACTO].reason_code == (
        ReasonCode.UNKNOWN_PROBE_OUTCOME
    )


def test_cli_rejects_state_unknown_and_operator_input_for_same_requirement(tmp_path):
    output = tmp_path / "blocked.json"
    with pytest.raises(SystemExit):
        cli_main(
            [
                "--output",
                str(output),
                "--state-unknown",
                RequirementId.SSH_ENDPOINT.value,
                "--operator-input-sha256",
                f"ssh-endpoint:{_SHA_A}",
            ]
        )


def test_cli_no_replace_rejects_existing_destination(tmp_path):
    output = tmp_path / "blocked.json"
    output.write_bytes(b'{"existing":"file"}')
    rc = cli_main(["--output", str(output)])
    assert rc == 1
    # Existing file must be preserved (audit-trail protection)
    assert json.loads(output.read_bytes()) == {"existing": "file"}


def test_cli_invocable_as_python_module(tmp_path):
    """``python -m pipeline.production_external_inputs`` must work."""
    output = tmp_path / "blocked.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline.production_external_inputs",
            "--output",
            str(output),
        ],
        cwd=_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert output.is_file()


# ---------------------------------------------------------------------------
# No-replace durable publication
# ---------------------------------------------------------------------------


def test_publish_blocked_report_no_replace(tmp_path):
    report = build_default_blocked_report()
    output = tmp_path / "blocked.json"
    publish_blocked_report(report, output=output)
    assert output.is_file()
    # Second publication to same destination must fail
    with pytest.raises(ProductionExternalInputsError, match="already exists"):
        publish_blocked_report(report, output=output)


def test_publish_blocked_report_rejects_missing_parent(tmp_path):
    report = build_default_blocked_report()
    output = tmp_path / "missing-dir" / "blocked.json"
    with pytest.raises(ProductionExternalInputsError, match="parent directory"):
        publish_blocked_report(report, output=output)


def test_publish_blocked_report_atomic_under_fault(tmp_path, monkeypatch):
    """If durable publication fails after namespace change, destination
    must not be left as an empty/partial file and must remain replayable.
    """
    from pipeline import production_external_inputs as pei

    report = build_default_blocked_report()
    output = tmp_path / "blocked.json"

    def fail_replace(source, destination):
        raise pei.DurableIOError(
            "sync failed after publish", published=True
        )

    monkeypatch.setattr(pei, "publish_file_noreplace", fail_replace)
    with pytest.raises(ProductionExternalInputsError, match="durable publication"):
        publish_blocked_report(report, output=output)
    # Destination must not exist; caller can retry safely
    assert not output.exists()
    # Temp files must be cleaned up
    leftovers = [
        p for p in tmp_path.iterdir()
        if p.name.startswith(f".{output.name}.")
    ]
    assert not leftovers
