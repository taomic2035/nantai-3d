#!/usr/bin/env python3
"""Canonical blocked report for external production inputs.

This module produces a machine-readable declaration of which external
inputs are required for Production V1 but not yet available.  The report
is canonical, duplicate-key-safe, and content-addressed.

It must NOT contain tokens, private keys, full environment variables, or
private absolute paths.  It must NOT declare ``ready``,
``verified-production``, ``metric-aligned``, or release-allowed status.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductionExternalInputsError(ValueError):
    """The blocked external inputs report is malformed."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CONTAINER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
_HOST_KEY_FINGERPRINT_PATTERN = re.compile(r"^SHA256:[A-Za-z0-9+/=]+$")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATASET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_FORBIDDEN_TERMS = frozenset(
    {
        "ready",
        "verified-production",
        "metric-aligned",
        "release-allowed",
    }
)
_SECRET_PATTERN = re.compile(
    r"(?i)(password|token|secret|private[_-]?key|credential)\s*[=:]"
)


def _no_forbidden_content(value: str, *, label: str) -> str:
    lower = value.lower()
    for term in _FORBIDDEN_TERMS:
        if term in lower:
            raise ProductionExternalInputsError(
                f"{label} must not declare '{term}'"
            )
    if _SECRET_PATTERN.search(value):
        raise ProductionExternalInputsError(
            f"{label} must not contain secret-like content"
        )
    if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
        raise ProductionExternalInputsError(
            f"{label} must not contain absolute paths"
        )
    if "\\" in value:
        raise ProductionExternalInputsError(
            f"{label} must not contain backslash paths"
        )
    return value


class SshEndpointBlock(FrozenModel):
    state: Literal["blocked"]
    host: str
    port: int = Field(ge=1, le=65535)
    pinned_host_key_fingerprint: str
    blocked_reason: str

    @field_validator("host")
    @classmethod
    def _validate_host(cls, v: str) -> str:
        if not _HOST_PATTERN.fullmatch(v):
            raise ValueError("ssh host must be a hostname")
        return v

    @field_validator("pinned_host_key_fingerprint")
    @classmethod
    def _validate_fingerprint(cls, v: str) -> str:
        if not _HOST_KEY_FINGERPRINT_PATTERN.fullmatch(v):
            raise ValueError(
                "host key fingerprint must be SHA256:base64"
            )
        return v

    @field_validator("blocked_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _no_forbidden_content(v, label="ssh_endpoint blocked_reason")


class CudaImageBlock(FrozenModel):
    state: Literal["blocked"]
    identity: str
    blocked_reason: str

    @field_validator("identity")
    @classmethod
    def _validate_identity(cls, v: str) -> str:
        if not _CONTAINER_PATTERN.fullmatch(v):
            raise ValueError("cuda image must be an immutable digest")
        return v

    @field_validator("blocked_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _no_forbidden_content(v, label="cuda_image blocked_reason")


class DatasetBlock(FrozenModel):
    state: Literal["blocked"]
    name: str
    content_sha256: str
    rights_clearance: Literal["rights-cleared"]
    blocked_reason: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _DATASET_NAME_PATTERN.fullmatch(v):
            raise ValueError("dataset name must be alphanumeric")
        return v

    @field_validator("content_sha256")
    @classmethod
    def _validate_sha(cls, v: str) -> str:
        if not re.fullmatch(_SHA256_PATTERN, v):
            raise ValueError("dataset content_sha256 must be 64 hex")
        return v

    @field_validator("blocked_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _no_forbidden_content(v, label="dataset blocked_reason")


class NerfstudioRequirementBlock(FrozenModel):
    state: Literal["blocked"]
    required_version: Literal["1.1.5"]
    required_method: Literal["splatfacto"]
    blocked_reason: str

    @field_validator("blocked_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _no_forbidden_content(
            v, label="nerfstudio blocked_reason"
        )


class ControlPointBlock(FrozenModel):
    state: Literal["blocked"]
    minimum_count: int = Field(ge=4)
    non_coplanar: Literal[True]
    blocked_reason: str

    @field_validator("blocked_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _no_forbidden_content(
            v, label="control_points blocked_reason"
        )


class ViewerAcceptanceBlock(FrozenModel):
    state: Literal["blocked"]
    blocked_reason: str

    @field_validator("blocked_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _no_forbidden_content(
            v, label="viewer_acceptance blocked_reason"
        )


class BlockedExternalInputsReport(FrozenModel):
    """Canonical, content-addressed blocked report."""

    schema_id: Literal["nantai.blocked-external-inputs.v1"] = Field(
        default="nantai.blocked-external-inputs.v1",
        alias="schema",
        serialization_alias="schema",
    )
    ssh_endpoint: SshEndpointBlock
    cuda_image: CudaImageBlock
    dataset: DatasetBlock
    nerfstudio: NerfstudioRequirementBlock
    control_points: ControlPointBlock
    viewer_acceptance: ViewerAcceptanceBlock
    report_sha256: str = Field(pattern=_SHA256_PATTERN)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
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


def _duplicate_keys(pairs):
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
    """Canonical bytes used to compute ``report_sha256`` (excludes the
    sha field itself)."""
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


def build_blocked_report(
    *,
    ssh_endpoint: SshEndpointBlock,
    cuda_image: CudaImageBlock,
    dataset: DatasetBlock,
    nerfstudio: NerfstudioRequirementBlock,
    control_points: ControlPointBlock,
    viewer_acceptance: ViewerAcceptanceBlock,
) -> BlockedExternalInputsReport:
    """Build a canonical blocked report with content-addressed SHA."""
    signing_payload: dict[str, Any] = {
        "schema": "nantai.blocked-external-inputs.v1",
        "ssh_endpoint": ssh_endpoint.model_dump(
            mode="json", by_alias=True
        ),
        "cuda_image": cuda_image.model_dump(
            mode="json", by_alias=True
        ),
        "dataset": dataset.model_dump(mode="json", by_alias=True),
        "nerfstudio": nerfstudio.model_dump(
            mode="json", by_alias=True
        ),
        "control_points": control_points.model_dump(
            mode="json", by_alias=True
        ),
        "viewer_acceptance": viewer_acceptance.model_dump(
            mode="json", by_alias=True
        ),
    }
    report_sha = hashlib.sha256(
        _canonical_json_bytes(signing_payload)
    ).hexdigest()
    report = BlockedExternalInputsReport(
        ssh_endpoint=ssh_endpoint,
        cuda_image=cuda_image,
        dataset=dataset,
        nerfstudio=nerfstudio,
        control_points=control_points,
        viewer_acceptance=viewer_acceptance,
        report_sha256=report_sha,
    )
    if compute_blocked_report_sha256(report) != report_sha:
        raise ProductionExternalInputsError(
            "blocked report sha256 round-trip failed"
        )
    return report


def load_blocked_report(raw: bytes) -> BlockedExternalInputsReport:
    """Load and validate a blocked report from raw canonical bytes."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ProductionExternalInputsError(
            "blocked report must be raw bytes"
        )
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
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
    if raw != blocked_report_full_canonical_bytes(report):
        raise ProductionExternalInputsError(
            "blocked report is not canonical JSON"
        )
    return report
