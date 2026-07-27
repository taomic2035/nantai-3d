"""Content-addressed measurement, policy, and decision for metric alignment.

The legacy ``sim3.alignment.v1`` record mixes measured residuals with the
thresholds that judged them.  This module separates those trust domains:

* measurement: immutable observed geometry and input identities;
* policy: independently content-addressed thresholds;
* decision: the result of applying one policy to one measurement.

Changing a threshold therefore cannot rewrite measured residuals or their SHA.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.real_dataset import canonical_model_bytes
from pipeline.recon_schema import (
    AlignmentStatus,
    ControlPoint,
    GeoAlignment,
    MetricStatus,
    RegistrationResult,
    Sim3AlignmentEvidence,
)


class MetricAlignmentEvidenceError(ValueError):
    """Alignment evidence identities or trust claims are inconsistent."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class _ControlPointSet(FrozenModel):
    schema_id: Literal["nantai.metric-alignment-control-points.v1"] = Field(
        default="nantai.metric-alignment-control-points.v1",
        alias="schema",
        serialization_alias="schema",
    )
    control_points: tuple[ControlPoint, ...]

    @model_validator(mode="after")
    def _labels_are_unique(self) -> _ControlPointSet:
        labels = tuple(point.label for point in self.control_points)
        if len(set(labels)) != len(labels):
            raise ValueError("metric alignment control points have duplicate labels")
        return self


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MEASUREMENT_ID_PATTERN = r"^metric-alignment-measurement-[0-9a-f]{64}$"
_POLICY_ID_PATTERN = r"^metric-alignment-policy-[0-9a-f]{64}$"
_DECISION_ID_PATTERN = r"^metric-alignment-decision-[0-9a-f]{64}$"

MetricAlignmentFailureCode = Literal[
    "insufficient-control-points",
    "degenerate-span",
    "rms-exceeded",
    "max-residual-exceeded",
    "derived-effective-count-missing",
    "derived-effective-count-too-low",
    "derived-holdout-missing",
    "derived-holdout-exceeded",
    "derived-compound-rms-exceeded",
    "aligned-output-missing",
    "aligned-output-identity-mismatch",
    "aligned-output-trust-mismatch",
]


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _content_sha(model: BaseModel) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            model.model_dump(
                mode="json",
                by_alias=True,
                exclude={
                    "measurement_id",
                    "policy_id",
                    "decision_id",
                    "content_sha256",
                },
            )
        )
    ).hexdigest()


def canonical_control_points_bytes(
    control_points: list[ControlPoint],
) -> bytes:
    model = _ControlPointSet(
        control_points=tuple(control_points),
    )
    return _canonical_json_bytes(
        model.model_dump(mode="json", by_alias=True)
    )


def _canonical_transform_history_bytes(
    registration: RegistrationResult,
) -> bytes:
    return _canonical_json_bytes(
        {
            "transforms": [
                transform.model_dump(mode="json")
                for transform in registration.transform_chain
            ]
        }
    )


class MetricAlignmentMeasurement(FrozenModel):
    schema_id: Literal["nantai.metric-alignment-measurement.v1"] = Field(
        default="nantai.metric-alignment-measurement.v1",
        alias="schema",
        serialization_alias="schema",
    )
    measurement_id: str = Field(pattern=_MEASUREMENT_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    registration_sha256: str = Field(pattern=_SHA256_PATTERN)
    control_points_sha256: str = Field(pattern=_SHA256_PATTERN)
    transform_history_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_aligned_registration_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    observed_transform_history_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    source_frame_id: str = Field(min_length=1)
    source_axes: str = Field(min_length=1)
    source_handedness: str = Field(min_length=1)
    source_units: str = Field(min_length=1)
    target_frame_id: str = Field(min_length=1)
    geo_origin: dict[str, float]
    scale: float = Field(gt=0)
    quat_wxyz: tuple[float, float, float, float]
    translation_xyz: tuple[float, float, float]
    n_control_points: int = Field(ge=0)
    control_point_labels: tuple[str, ...]
    per_point_residual_m: tuple[float, ...]
    rms_residual_m: float = Field(ge=0)
    max_residual_m: float = Field(ge=0)
    source_singular_values: tuple[float, float, float]
    n_effective_control_points: int | None = Field(default=None, ge=0)
    holdout_rms_m: float | None = Field(default=None, ge=0)
    holdout_max_m: float | None = Field(default=None, ge=0)
    holdout_folds: int | None = Field(default=None, ge=0)
    upstream_alignment_rms_m: float | None = Field(default=None, ge=0)
    control_target_provenance: str | None = None

    @model_validator(mode="after")
    def _measurement_is_self_consistent(
        self,
    ) -> MetricAlignmentMeasurement:
        if (
            len(self.control_point_labels) != self.n_control_points
            or len(self.per_point_residual_m) != self.n_control_points
        ):
            raise ValueError(
                "measurement point arrays disagree with n_control_points"
            )
        if len(set(self.control_point_labels)) != self.n_control_points:
            raise ValueError("measurement control-point labels must be unique")
        measured_max = max(self.per_point_residual_m, default=0.0)
        measured_rms = math.sqrt(
            sum(value * value for value in self.per_point_residual_m)
            / max(1, self.n_control_points)
        )
        if not math.isclose(
            self.max_residual_m,
            measured_max,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "measurement max residual disagrees with per-point residuals"
            )
        if not math.isclose(
            self.rms_residual_m,
            measured_rms,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "measurement RMS disagrees with per-point residuals"
            )
        if (
            any(value < 0 for value in self.source_singular_values)
            or tuple(sorted(self.source_singular_values, reverse=True))
            != self.source_singular_values
        ):
            raise ValueError(
                "measurement singular values must be nonnegative descending"
            )
        expected = _content_sha(self)
        if self.content_sha256 != expected:
            raise ValueError("measurement content_sha256 disagrees")
        if self.measurement_id != f"metric-alignment-measurement-{expected}":
            raise ValueError("measurement_id disagrees")
        return self


class MetricAlignmentPolicy(FrozenModel):
    schema_id: Literal["nantai.metric-alignment-policy.v1"] = Field(
        default="nantai.metric-alignment-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    policy_id: str = Field(pattern=_POLICY_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    min_control_points: Literal[4] = 4
    min_effective_control_points_derived: int = Field(default=8, ge=4)
    min_span_ratio: float = Field(gt=0)
    absolute_span_floor_source_units: float = Field(default=1e-6, gt=0)
    max_rms_m: float = Field(gt=0)
    max_residual_m: float = Field(gt=0)
    require_holdout_for_derived: Literal[True] = True

    @model_validator(mode="after")
    def _policy_is_content_addressed(self) -> MetricAlignmentPolicy:
        expected = _content_sha(self)
        if self.content_sha256 != expected:
            raise ValueError("policy content_sha256 disagrees")
        if self.policy_id != f"metric-alignment-policy-{expected}":
            raise ValueError("policy_id disagrees")
        return self

    @classmethod
    def create(
        cls,
        *,
        max_rms_m: float,
        max_residual_m: float,
        min_span_ratio: float,
        min_effective_control_points_derived: int = 8,
    ) -> MetricAlignmentPolicy:
        zero = "0" * 64
        fields = {
            "max_rms_m": max_rms_m,
            "max_residual_m": max_residual_m,
            "min_span_ratio": min_span_ratio,
            "min_effective_control_points_derived": (
                min_effective_control_points_derived
            ),
        }
        provisional = cls.model_construct(
            policy_id=f"metric-alignment-policy-{zero}",
            content_sha256=zero,
            **fields,
        )
        digest = _content_sha(provisional)
        return cls(
            policy_id=f"metric-alignment-policy-{digest}",
            content_sha256=digest,
            **fields,
        )


class MetricAlignmentDecision(FrozenModel):
    schema_id: Literal["nantai.metric-alignment-decision.v1"] = Field(
        default="nantai.metric-alignment-decision.v1",
        alias="schema",
        serialization_alias="schema",
    )
    decision_id: str = Field(pattern=_DECISION_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    measurement_sha256: str = Field(pattern=_SHA256_PATTERN)
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["accepted", "rejected"]
    failure_codes: tuple[MetricAlignmentFailureCode, ...]
    aligned_registration_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    transform_history_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    output_frame_id: str | None = None
    output_metric_status: Literal["metric"] | None = None
    output_geo_alignment: Literal["aligned"] | None = None

    @model_validator(mode="after")
    def _decision_is_self_consistent(self) -> MetricAlignmentDecision:
        output_fields = (
            self.aligned_registration_sha256,
            self.transform_history_sha256,
            self.output_frame_id,
            self.output_metric_status,
            self.output_geo_alignment,
        )
        if self.status == "accepted":
            if self.failure_codes or any(value is None for value in output_fields):
                raise ValueError(
                    "accepted decision requires complete metric output bindings"
                )
        elif not self.failure_codes or any(
            value is not None for value in output_fields
        ):
            raise ValueError(
                "rejected decision cannot carry metric output claims"
            )
        expected = _content_sha(self)
        if self.content_sha256 != expected:
            raise ValueError("decision content_sha256 disagrees")
        if self.decision_id != f"metric-alignment-decision-{expected}":
            raise ValueError("decision_id disagrees")
        return self


def canonical_metric_alignment_measurement_bytes(
    measurement: MetricAlignmentMeasurement,
) -> bytes:
    return _canonical_json_bytes(
        measurement.model_dump(mode="json", by_alias=True)
    )


def canonical_metric_alignment_policy_bytes(
    policy: MetricAlignmentPolicy,
) -> bytes:
    return _canonical_json_bytes(
        policy.model_dump(mode="json", by_alias=True)
    )


def canonical_metric_alignment_decision_bytes(
    decision: MetricAlignmentDecision,
) -> bytes:
    return _canonical_json_bytes(
        decision.model_dump(mode="json", by_alias=True)
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MetricAlignmentEvidenceError(
                "metric alignment evidence has duplicate keys"
            )
        result[key] = value
    return result


def _parse_canonical_model(
    payload: bytes,
    model_type,
    canonicalizer,
):
    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        model = model_type.model_validate_json(payload)
    except MetricAlignmentEvidenceError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise MetricAlignmentEvidenceError(
            "metric alignment evidence is invalid"
        ) from exc
    if payload != canonicalizer(model):
        raise MetricAlignmentEvidenceError(
            "metric alignment evidence is not canonical"
        )
    return model


def load_canonical_control_points_bytes(
    payload: bytes,
) -> list[ControlPoint]:
    model = _parse_canonical_model(
        payload,
        _ControlPointSet,
        lambda value: _canonical_json_bytes(
            value.model_dump(mode="json", by_alias=True)
        ),
    )
    return list(model.control_points)


def load_metric_alignment_measurement_bytes(
    payload: bytes,
) -> MetricAlignmentMeasurement:
    return _parse_canonical_model(
        payload,
        MetricAlignmentMeasurement,
        canonical_metric_alignment_measurement_bytes,
    )


def load_metric_alignment_policy_bytes(
    payload: bytes,
) -> MetricAlignmentPolicy:
    return _parse_canonical_model(
        payload,
        MetricAlignmentPolicy,
        canonical_metric_alignment_policy_bytes,
    )


def load_metric_alignment_decision_bytes(
    payload: bytes,
) -> MetricAlignmentDecision:
    return _parse_canonical_model(
        payload,
        MetricAlignmentDecision,
        canonical_metric_alignment_decision_bytes,
    )


def _alignment_evidence(
    aligned: RegistrationResult,
) -> Sim3AlignmentEvidence:
    if aligned.pose_to_world is None:
        raise MetricAlignmentEvidenceError(
            "aligned registration has no pose_to_world transform"
        )
    matches: list[Sim3AlignmentEvidence] = []
    for value in aligned.pose_to_world.evidence:
        try:
            matches.append(Sim3AlignmentEvidence.parse(value))
        except ValueError:
            continue
    if len(matches) != 1 or not matches[0].passed:
        raise MetricAlignmentEvidenceError(
            "aligned registration requires one passed Sim3 measurement"
        )
    return matches[0]


def _source_projection(
    aligned: RegistrationResult,
    source: RegistrationResult,
) -> RegistrationResult:
    return aligned.model_copy(
        update={
            "world_frame": source.world_frame,
            "pose_to_world": source.pose_to_world,
            "alignment_status": source.alignment_status,
        }
    )


def measure_metric_alignment(
    source_registration: RegistrationResult,
    aligned_registration: RegistrationResult,
    control_points: list[ControlPoint],
) -> MetricAlignmentMeasurement:
    """Extract policy-free observations and bind every trust-root input."""

    if (
        source_registration.alignment_status is AlignmentStatus.ALIGNED
        or source_registration.pose_to_world is not None
    ):
        raise MetricAlignmentEvidenceError(
            "source registration must remain unaligned before measurement"
        )
    if (
        aligned_registration.alignment_status is not AlignmentStatus.ALIGNED
        or aligned_registration.pose_to_world is None
        or aligned_registration.world_frame is None
    ):
        raise MetricAlignmentEvidenceError(
            "aligned registration is missing metric output"
        )
    if canonical_model_bytes(
        _source_projection(aligned_registration, source_registration)
    ) != canonical_model_bytes(source_registration):
        raise MetricAlignmentEvidenceError(
            "aligned registration changes source registration content"
        )
    evidence = _alignment_evidence(aligned_registration)
    labels = tuple(point.label for point in control_points)
    if labels != evidence.control_point_labels:
        raise MetricAlignmentEvidenceError(
            "control points disagree with measured labels"
        )
    sim3 = aligned_registration.pose_to_world.sim3
    if abs(sim3.scale - evidence.scale) > 1e-12:
        raise MetricAlignmentEvidenceError(
            "alignment Sim3 scale disagrees with measurement"
        )
    fields = {
        "registration_sha256": hashlib.sha256(
            canonical_model_bytes(source_registration)
        ).hexdigest(),
        "control_points_sha256": hashlib.sha256(
            canonical_control_points_bytes(control_points)
        ).hexdigest(),
        "transform_history_sha256": hashlib.sha256(
            _canonical_transform_history_bytes(source_registration)
        ).hexdigest(),
        "observed_aligned_registration_sha256": hashlib.sha256(
            canonical_model_bytes(aligned_registration)
        ).hexdigest(),
        "observed_transform_history_sha256": hashlib.sha256(
            _canonical_transform_history_bytes(aligned_registration)
        ).hexdigest(),
        "source_frame_id": source_registration.pose_frame.frame_id,
        "source_axes": source_registration.pose_frame.axes.value,
        "source_handedness": source_registration.pose_frame.handedness.value,
        "source_units": source_registration.pose_frame.units.value,
        "target_frame_id": aligned_registration.world_frame.frame_id,
        "geo_origin": evidence.geo_origin,
        "scale": sim3.scale,
        "quat_wxyz": sim3.quat_wxyz,
        "translation_xyz": sim3.t_xyz,
        "n_control_points": evidence.n_control_points,
        "control_point_labels": evidence.control_point_labels,
        "per_point_residual_m": evidence.per_point_residual_m,
        "rms_residual_m": evidence.rms_residual_m,
        "max_residual_m": evidence.max_residual_m,
        "source_singular_values": evidence.source_singular_values,
        "n_effective_control_points": (
            evidence.n_effective_control_points
        ),
        "holdout_rms_m": evidence.holdout_rms_m,
        "holdout_max_m": evidence.holdout_max_m,
        "holdout_folds": evidence.holdout_folds,
        "upstream_alignment_rms_m": evidence.upstream_alignment_rms_m,
        "control_target_provenance": evidence.control_target_provenance,
    }
    zero = "0" * 64
    provisional = MetricAlignmentMeasurement.model_construct(
        measurement_id=f"metric-alignment-measurement-{zero}",
        content_sha256=zero,
        **fields,
    )
    digest = _content_sha(provisional)
    return MetricAlignmentMeasurement(
        measurement_id=f"metric-alignment-measurement-{digest}",
        content_sha256=digest,
        **fields,
    )


def _policy_failures(
    measurement: MetricAlignmentMeasurement,
    policy: MetricAlignmentPolicy,
) -> tuple[MetricAlignmentFailureCode, ...]:
    failures: list[MetricAlignmentFailureCode] = []
    if measurement.n_control_points < policy.min_control_points:
        failures.append("insufficient-control-points")
    singular = measurement.source_singular_values
    span_floor = max(
        policy.min_span_ratio * singular[0],
        policy.absolute_span_floor_source_units,
    )
    if singular[0] <= 0 or singular[2] < span_floor:
        failures.append("degenerate-span")
    if measurement.rms_residual_m > policy.max_rms_m:
        failures.append("rms-exceeded")
    if measurement.max_residual_m > policy.max_residual_m:
        failures.append("max-residual-exceeded")
    if measurement.control_target_provenance is not None:
        if measurement.n_effective_control_points is None:
            failures.append("derived-effective-count-missing")
        elif (
            measurement.n_effective_control_points
            < policy.min_effective_control_points_derived
        ):
            failures.append("derived-effective-count-too-low")
        if measurement.holdout_rms_m is None:
            failures.append("derived-holdout-missing")
        elif measurement.holdout_rms_m > policy.max_rms_m:
            failures.append("derived-holdout-exceeded")
        if (
            measurement.holdout_rms_m is not None
            and (
                measurement.holdout_rms_m
                + (measurement.upstream_alignment_rms_m or 0.0)
            )
            > policy.max_rms_m
        ):
            failures.append("derived-compound-rms-exceeded")
    return tuple(failures)


def _output_failures(
    measurement: MetricAlignmentMeasurement,
    aligned: RegistrationResult | None,
) -> tuple[MetricAlignmentFailureCode, ...]:
    if aligned is None:
        return ("aligned-output-missing",)
    if hashlib.sha256(canonical_model_bytes(aligned)).hexdigest() != (
        measurement.observed_aligned_registration_sha256
    ):
        return ("aligned-output-identity-mismatch",)
    if (
        aligned.alignment_status is not AlignmentStatus.ALIGNED
        or aligned.world_frame is None
        or aligned.pose_to_world is None
        or aligned.world_frame.metric_status is not MetricStatus.METRIC
        or aligned.world_frame.geo_aligned is not GeoAlignment.ALIGNED
        or aligned.world_frame.frame_id != measurement.target_frame_id
        or hashlib.sha256(
            _canonical_transform_history_bytes(aligned)
        ).hexdigest()
        != measurement.observed_transform_history_sha256
    ):
        return ("aligned-output-trust-mismatch",)
    return ()


def decide_metric_alignment(
    measurement: MetricAlignmentMeasurement,
    policy: MetricAlignmentPolicy,
    *,
    aligned_registration: RegistrationResult | None,
) -> MetricAlignmentDecision:
    failures = list(_policy_failures(measurement, policy))
    if not failures:
        failures.extend(
            _output_failures(measurement, aligned_registration)
        )
    status: Literal["accepted", "rejected"] = (
        "accepted" if not failures else "rejected"
    )
    accepted = status == "accepted"
    fields = {
        "measurement_sha256": measurement.content_sha256,
        "policy_sha256": policy.content_sha256,
        "status": status,
        "failure_codes": tuple(failures),
        "aligned_registration_sha256": (
            measurement.observed_aligned_registration_sha256
            if accepted
            else None
        ),
        "transform_history_sha256": (
            measurement.observed_transform_history_sha256
            if accepted
            else None
        ),
        "output_frame_id": (
            measurement.target_frame_id if accepted else None
        ),
        "output_metric_status": "metric" if accepted else None,
        "output_geo_alignment": "aligned" if accepted else None,
    }
    zero = "0" * 64
    provisional = MetricAlignmentDecision.model_construct(
        decision_id=f"metric-alignment-decision-{zero}",
        content_sha256=zero,
        **fields,
    )
    digest = _content_sha(provisional)
    return MetricAlignmentDecision(
        decision_id=f"metric-alignment-decision-{digest}",
        content_sha256=digest,
        **fields,
    )


def verify_metric_alignment_decision(
    *,
    source_registration: RegistrationResult,
    control_points: list[ControlPoint],
    aligned_registration: RegistrationResult,
    measurement: MetricAlignmentMeasurement,
    policy: MetricAlignmentPolicy,
    decision: MetricAlignmentDecision,
) -> None:
    measured = measure_metric_alignment(
        source_registration,
        aligned_registration,
        control_points,
    )
    if measured != measurement:
        if measured.control_points_sha256 != measurement.control_points_sha256:
            raise MetricAlignmentEvidenceError(
                "metric alignment control points identity changed"
            )
        raise MetricAlignmentEvidenceError(
            "metric alignment measurement identity changed"
        )
    expected = decide_metric_alignment(
        measured,
        policy,
        aligned_registration=aligned_registration,
    )
    if expected != decision:
        raise MetricAlignmentEvidenceError(
            "metric alignment decision disagrees with current evidence"
        )
