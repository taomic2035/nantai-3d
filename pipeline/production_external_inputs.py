#!/usr/bin/env python3
"""Canonical blocked report for external production inputs (NOW-7).

This module produces a machine-readable declaration of which external
inputs are required for Production V1 but not yet available.  The
report is canonical, duplicate-key-safe, content-addressed, and
secret-free.

Each requirement entry uses a closed ``requirement_id`` and a closed
``reason_code`` — never free text.  When the state is ``missing`` or
``unknown``, identity fields are ``None`` and the canonical JSON
contains no placeholder host, digest, or SHA.  ``present-unverified``
may only bind operator-input content SHAs; it must not claim
``rights-cleared``, ``ready``, ``metric-aligned`` or release-allowed
status.  Whether a dataset may be published is derived elsewhere by
``pipeline.real_dataset.validate_capture_rights``; this module has no
authority to derive it.

Default CLI invocation emits a blocked report listing all six
requirements as ``missing`` without any operator input — usable when
no endpoint, GPU, or credentials exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pipeline.durable_io import (
    DurableIOError,
    flush_file,
    publish_file_noreplace,
)


class ProductionExternalInputsError(ValueError):
    """The blocked external inputs report is malformed."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class RequirementId(StrEnum):
    """Closed set of Production V1 external-input requirement IDs.

    The ``PRODUCTION_DATASET`` requirement is the slot for the
    rights-cleared production dataset identity required by NOW-7; the
    actual rights-clearance derivation lives in
    ``pipeline.real_dataset.validate_capture_rights`` and is NOT
    claimed by this report.  The enum value intentionally avoids the
    substring ``"rights-cleared"`` so canonical JSON never carries a
    self-claimed clearance statement.
    """

    SSH_ENDPOINT = "ssh-endpoint"
    IMMUTABLE_CUDA_IMAGE = "immutable-cuda-image"
    PRODUCTION_DATASET = "production-dataset"
    NERFSTUDIO_SPLATFACTO = "nerfstudio-1.1.5-splatfacto"
    NON_COPLANAR_CONTROL_POINTS = "non-coplanar-control-points"
    VIEWER_HUMAN_ACCEPTANCE = "viewer-human-acceptance"


class RequirementState(StrEnum):
    """Closed set of per-requirement states.

    ``missing``: operator did not provide input.
    ``unknown``: probe could not determine availability.
    ``present-unverified``: operator input is bound by content SHA, but
        no metric, rights clearance, or acceptance is implied.
    """

    MISSING = "missing"
    UNKNOWN = "unknown"
    PRESENT_UNVERIFIED = "present-unverified"


class ReasonCode(StrEnum):
    """Closed set of reason codes — no free text."""

    NO_OPERATOR_INPUT_BOUND = "no-operator-input-bound"
    NO_CONTAINER_DIGEST_BOUND = "no-container-digest-bound"
    NO_DATASET_CONTENT_BOUND = "no-dataset-content-bound"
    NO_RIGHTS_RECEIPT_BOUND = "no-rights-receipt-bound"
    NERFSTUDIO_REQUIREMENT_UNVERIFIED = "nerfstudio-requirement-unverified"
    NO_CONTROL_POINTS_MEASURED = "no-control-points-measured"
    NO_VIEWER_ACCEPTANCE_EVIDENCE = "no-viewer-acceptance-evidence"
    OPERATOR_INPUT_BOUND_BUT_UNVERIFIED = "operator-input-bound-but-unverified"
    OPERATOR_INPUT_BOUND_BUT_RIGHTS_RECEIPT_MISSING = (
        "operator-input-bound-but-rights-receipt-missing"
    )
    OPERATOR_INPUT_BOUND_BUT_VIEWER_ACCEPTANCE_PENDING = (
        "operator-input-bound-but-viewer-acceptance-pending"
    )
    UNKNOWN_PROBE_OUTCOME = "unknown-probe-outcome"


_SHA256_PATTERN = r"^[0-9a-f]{64}$"

_FORBIDDEN_STATEMENTS = frozenset(
    {
        "ready",
        "verified-production",
        "metric-aligned",
        "release-allowed",
        "rights-cleared",
        "gpu-available",
        "viewer-accepted",
    }
)

_SECRET_PATTERN = re.compile(
    r"(?i)(password|token|secret|private[_-]?key|credential)\s*[=:]"
)


class RequirementEntry(FrozenModel):
    """A single requirement entry in a blocked report."""

    requirement_id: RequirementId
    state: RequirementState
    reason_code: ReasonCode
    operator_input_content_sha256: str | None = Field(default=None)
    rights_receipt_content_sha256: str | None = Field(default=None)

    @field_validator("operator_input_content_sha256")
    @classmethod
    def _validate_operator_sha(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(_SHA256_PATTERN, v):
            raise ValueError("operator_input_content_sha256 must be 64 hex")
        return v

    @field_validator("rights_receipt_content_sha256")
    @classmethod
    def _validate_receipt_sha(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not re.fullmatch(_SHA256_PATTERN, v):
            raise ValueError("rights_receipt_content_sha256 must be 64 hex")
        return v

    @model_validator(mode="after")
    def _validate_consistency(self) -> RequirementEntry:
        missing_like = self.state in (
            RequirementState.MISSING,
            RequirementState.UNKNOWN,
        )
        if missing_like:
            if self.operator_input_content_sha256 is not None:
                raise ValueError(
                    f"{self.state.value} state cannot bind operator input SHA"
                )
            if self.rights_receipt_content_sha256 is not None:
                raise ValueError(
                    f"{self.state.value} state cannot bind rights receipt SHA"
                )
        if self.state == RequirementState.PRESENT_UNVERIFIED:
            if self.operator_input_content_sha256 is None:
                raise ValueError(
                    "present-unverified requires operator_input_content_sha256"
                )
            if (
                self.rights_receipt_content_sha256 is not None
                and self.requirement_id != RequirementId.PRODUCTION_DATASET
            ):
                raise ValueError(
                    "rights_receipt_content_sha256 only valid for "
                    "production-dataset requirement"
                )
        return self


class BlockedExternalInputsReport(FrozenModel):
    """Canonical, content-addressed blocked report."""

    schema_id: Literal["nantai.blocked-external-inputs.v1"] = Field(
        default="nantai.blocked-external-inputs.v1",
        alias="schema",
        serialization_alias="schema",
    )
    state: Literal["blocked-external-input"] = "blocked-external-input"
    requirements: list[RequirementEntry]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("requirements")
    @classmethod
    def _validate_requirements(
        cls, v: list[RequirementEntry]
    ) -> list[RequirementEntry]:
        if not v:
            raise ValueError("requirements must not be empty")
        ids = [entry.requirement_id for entry in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate requirement_id")
        expected = sorted(RequirementId, key=lambda r: r.value)
        if ids != [e.requirement_id for e in sorted(v, key=lambda e: e.requirement_id.value)]:
            raise ValueError("requirements must be sorted by requirement_id")
        if sorted({e.requirement_id for e in v}, key=lambda r: r.value) != expected:
            raise ValueError("requirements must cover all six RequirementId values")
        return v


_DEFAULT_REASON_FOR: dict[RequirementId, ReasonCode] = {
    RequirementId.SSH_ENDPOINT: ReasonCode.NO_OPERATOR_INPUT_BOUND,
    RequirementId.IMMUTABLE_CUDA_IMAGE: ReasonCode.NO_CONTAINER_DIGEST_BOUND,
    RequirementId.PRODUCTION_DATASET: ReasonCode.NO_DATASET_CONTENT_BOUND,
    RequirementId.NERFSTUDIO_SPLATFACTO: ReasonCode.NERFSTUDIO_REQUIREMENT_UNVERIFIED,
    RequirementId.NON_COPLANAR_CONTROL_POINTS: ReasonCode.NO_CONTROL_POINTS_MEASURED,
    RequirementId.VIEWER_HUMAN_ACCEPTANCE: ReasonCode.NO_VIEWER_ACCEPTANCE_EVIDENCE,
}


def _canonical_json_bytes(payload: Any) -> bytes:
    if not isinstance(payload, (dict, list)):
        raise ProductionExternalInputsError(
            "canonical JSON payload must be dict or list"
        )
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionExternalInputsError(
                "blocked report has duplicate keys"
            )
        result[key] = value
    return result


def blocked_report_signing_bytes(
    report: BlockedExternalInputsReport,
) -> bytes:
    """Canonical bytes used to compute ``report_sha256`` (excludes the sha)."""
    payload = report.model_dump(mode="json", by_alias=True)
    payload.pop("report_sha256", None)
    return _canonical_json_bytes(payload)


def compute_blocked_report_sha256(
    report: BlockedExternalInputsReport,
) -> str:
    """Content-addressed SHA-256 of the report's signing bytes."""
    return hashlib.sha256(blocked_report_signing_bytes(report)).hexdigest()


def blocked_report_full_canonical_bytes(
    report: BlockedExternalInputsReport,
) -> bytes:
    """Full canonical bytes including ``report_sha256``."""
    return _canonical_json_bytes(
        report.model_dump(mode="json", by_alias=True)
    )


def _assert_no_forbidden_content(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            _assert_no_forbidden_content(key)
            _assert_no_forbidden_content(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_forbidden_content(item)
    elif isinstance(payload, str):
        lower = payload.lower()
        for term in _FORBIDDEN_STATEMENTS:
            if term in lower:
                raise ProductionExternalInputsError(
                    f"blocked report contains forbidden statement: {term}"
                )
        if _SECRET_PATTERN.search(payload):
            raise ProductionExternalInputsError(
                "blocked report contains secret-like content"
            )
        if payload.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", payload):
            raise ProductionExternalInputsError(
                "blocked report contains absolute path"
            )
        if "\\" in payload:
            raise ProductionExternalInputsError(
                "blocked report contains backslash path"
            )


def _build_blocked_report(
    entries: list[RequirementEntry],
) -> BlockedExternalInputsReport:
    sorted_entries = sorted(entries, key=lambda e: e.requirement_id.value)
    signing_payload: dict[str, Any] = {
        "schema": "nantai.blocked-external-inputs.v1",
        "state": "blocked-external-input",
        "requirements": [
            e.model_dump(mode="json", by_alias=True) for e in sorted_entries
        ],
    }
    report_sha = hashlib.sha256(
        _canonical_json_bytes(signing_payload)
    ).hexdigest()
    report = BlockedExternalInputsReport(
        state="blocked-external-input",
        requirements=sorted_entries,
        report_sha256=report_sha,
    )
    if compute_blocked_report_sha256(report) != report_sha:
        raise ProductionExternalInputsError(
            "blocked report sha256 round-trip failed"
        )
    full_payload = report.model_dump(mode="json", by_alias=True)
    _assert_no_forbidden_content(full_payload)
    return report


def build_default_blocked_report() -> BlockedExternalInputsReport:
    """Build a blocked report with all six requirements in ``missing`` state.

    No operator input, no placeholder identity, no host, no digest, no
    SHA.  This is the canonical report emitted when no external values
    are available; the CLI uses it as the default.
    """
    entries = [
        RequirementEntry(
            requirement_id=rid,
            state=RequirementState.MISSING,
            reason_code=_DEFAULT_REASON_FOR[rid],
        )
        for rid in sorted(RequirementId, key=lambda r: r.value)
    ]
    return _build_blocked_report(entries)


def build_blocked_report(
    *,
    entries: list[RequirementEntry],
) -> BlockedExternalInputsReport:
    """Build a canonical blocked report from a list of requirement entries.

    Each ``RequirementId`` must appear exactly once.  Entries are sorted
    by ``requirement_id`` to ensure canonical output.
    """
    if not entries:
        raise ProductionExternalInputsError("entries must not be empty")
    ids = [e.requirement_id for e in entries]
    if len(ids) != len(set(ids)):
        raise ProductionExternalInputsError(
            "entries contain duplicate requirement_id"
        )
    expected = sorted(RequirementId, key=lambda r: r.value)
    if sorted(ids, key=lambda r: r.value) != expected:
        raise ProductionExternalInputsError(
            "entries must cover all six RequirementId values exactly once"
        )
    return _build_blocked_report(entries)


def load_blocked_report(
    raw: bytes | bytearray | str,
) -> BlockedExternalInputsReport:
    """Load and validate a blocked report from raw canonical bytes."""
    if isinstance(raw, str):
        try:
            raw_bytes = raw.encode("ascii")
        except UnicodeError as exc:
            raise ProductionExternalInputsError(
                "blocked report must be ASCII"
            ) from exc
    elif isinstance(raw, (bytes, bytearray)):
        raw_bytes = bytes(raw)
    else:
        raise ProductionExternalInputsError(
            "blocked report must be bytes or str"
        )
    try:
        text = raw_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProductionExternalInputsError(
            "blocked report must be ASCII"
        ) from exc
    try:
        parsed = json.loads(text, object_pairs_hook=_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ProductionExternalInputsError(
            "blocked report is not valid JSON"
        ) from exc
    except ProductionExternalInputsError:
        raise
    try:
        report = BlockedExternalInputsReport.model_validate(parsed)
    except ValueError as exc:
        raise ProductionExternalInputsError(
            "blocked report validation failed"
        ) from exc
    expected_sha = compute_blocked_report_sha256(report)
    if report.report_sha256 != expected_sha:
        raise ProductionExternalInputsError(
            "blocked report sha256 does not match signing bytes"
        )
    if raw_bytes != blocked_report_full_canonical_bytes(report):
        raise ProductionExternalInputsError(
            "blocked report is not canonical JSON"
        )
    _assert_no_forbidden_content(report.model_dump(mode="json", by_alias=True))
    return report


def publish_blocked_report(
    report: BlockedExternalInputsReport,
    *,
    output: Path,
) -> None:
    """Publish a canonical blocked report with no-replace durable semantics.

    The output parent must exist.  A pre-existing destination is treated
    as a replay or collision and is rejected; the caller must not
    overwrite audit-trail files.
    """
    output = Path(output).resolve()
    if not output.parent.exists():
        raise ProductionExternalInputsError(
            "output parent directory must exist"
        )
    raw = blocked_report_full_canonical_bytes(report)
    temporary = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        flush_file(temporary)
        publish_file_noreplace(temporary, output)
    except DurableIOError as exc:
        temporary.unlink(missing_ok=True)
        raise ProductionExternalInputsError(
            "blocked report durable publication failed"
        ) from exc
    except FileExistsError as exc:
        temporary.unlink(missing_ok=True)
        raise ProductionExternalInputsError(
            "blocked report destination already exists; replay blocked"
        ) from exc
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ProductionExternalInputsError(
            "blocked report publication cannot be opened"
        ) from exc


def _parse_operator_input_spec(
    spec: str,
) -> tuple[RequirementId, str]:
    try:
        rid_str, sha = spec.split(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --operator-input-sha256 format: {spec!r}"
        ) from exc
    try:
        rid = RequirementId(rid_str)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"unknown requirement_id: {rid_str!r}"
        ) from exc
    if not re.fullmatch(_SHA256_PATTERN, sha):
        raise argparse.ArgumentTypeError(
            f"operator input SHA-256 must be 64 hex: {sha!r}"
        )
    return rid, sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.production_external_inputs",
        description=(
            "Emit a canonical blocked-external-input report. Without any "
            "operator input flags, all six requirements are listed as "
            "missing. Output uses no-replace durable publication."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the canonical blocked report (no-replace).",
    )
    parser.add_argument(
        "--operator-input-sha256",
        action="append",
        default=[],
        metavar="REQUIREMENT_ID:SHA256",
        help=(
            "Bind an operator input content SHA for a requirement. May be "
            "repeated. Requirements not listed remain in missing state."
        ),
    )
    parser.add_argument(
        "--rights-receipt-sha256",
        type=str,
        default=None,
        metavar="SHA256",
        help=(
            "Bind rights receipt content SHA for the production-dataset "
            "requirement. Only valid together with "
            "--operator-input-sha256 production-dataset:SHA."
        ),
    )
    parser.add_argument(
        "--state-unknown",
        action="append",
        default=[],
        metavar="REQUIREMENT_ID",
        help=(
            "Mark a requirement as unknown (probe could not determine "
            "availability). May be repeated. Cannot be combined with "
            "--operator-input-sha256 for the same requirement."
        ),
    )
    args = parser.parse_args(argv)

    operator_inputs: dict[RequirementId, str] = {}
    for spec in args.operator_input_sha256:
        try:
            rid, sha = _parse_operator_input_spec(spec)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
        if rid in operator_inputs:
            parser.error(
                f"duplicate --operator-input-sha256 for {rid.value}"
            )
        operator_inputs[rid] = sha

    unknown_ids: set[RequirementId] = set()
    for raw_id in args.state_unknown:
        try:
            rid = RequirementId(raw_id)
        except ValueError:
            parser.error(f"unknown requirement_id for --state-unknown: {raw_id!r}")
        if rid in unknown_ids:
            parser.error(f"duplicate --state-unknown for {rid.value}")
        if rid in operator_inputs:
            parser.error(
                f"--state-unknown and --operator-input-sha256 both "
                f"reference {rid.value}"
            )
        unknown_ids.add(rid)

    if args.rights_receipt_sha256 is not None:
        if not re.fullmatch(_SHA256_PATTERN, args.rights_receipt_sha256):
            parser.error(
                "--rights-receipt-sha256 must be 64 hex characters"
            )
        if RequirementId.PRODUCTION_DATASET not in operator_inputs:
            parser.error(
                "--rights-receipt-sha256 requires "
                "--operator-input-sha256 production-dataset:SHA"
            )

    entries: list[RequirementEntry] = []
    for rid in sorted(RequirementId, key=lambda r: r.value):
        if rid in operator_inputs:
            kwargs: dict[str, Any] = {
                "requirement_id": rid,
                "state": RequirementState.PRESENT_UNVERIFIED,
                "reason_code": _present_unverified_reason_for(
                    rid,
                    rights_receipt_provided=(
                        rid == RequirementId.PRODUCTION_DATASET
                        and args.rights_receipt_sha256 is not None
                    ),
                ),
                "operator_input_content_sha256": operator_inputs[rid],
            }
            if (
                rid == RequirementId.PRODUCTION_DATASET
                and args.rights_receipt_sha256 is not None
            ):
                kwargs["rights_receipt_content_sha256"] = (
                    args.rights_receipt_sha256
                )
            entries.append(RequirementEntry(**kwargs))
        elif rid in unknown_ids:
            entries.append(
                RequirementEntry(
                    requirement_id=rid,
                    state=RequirementState.UNKNOWN,
                    reason_code=ReasonCode.UNKNOWN_PROBE_OUTCOME,
                )
            )
        else:
            entries.append(
                RequirementEntry(
                    requirement_id=rid,
                    state=RequirementState.MISSING,
                    reason_code=_DEFAULT_REASON_FOR[rid],
                )
            )

    try:
        report = build_blocked_report(entries=entries)
    except (ProductionExternalInputsError, ValueError) as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 2

    try:
        publish_blocked_report(report, output=args.output)
    except ProductionExternalInputsError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    return 0


def _present_unverified_reason_for(
    rid: RequirementId,
    *,
    rights_receipt_provided: bool,
) -> ReasonCode:
    if rid == RequirementId.PRODUCTION_DATASET:
        return (
            ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED
            if rights_receipt_provided
            else ReasonCode.OPERATOR_INPUT_BOUND_BUT_RIGHTS_RECEIPT_MISSING
        )
    if rid == RequirementId.VIEWER_HUMAN_ACCEPTANCE:
        return ReasonCode.OPERATOR_INPUT_BOUND_BUT_VIEWER_ACCEPTANCE_PENDING
    return ReasonCode.OPERATOR_INPUT_BOUND_BUT_UNVERIFIED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
