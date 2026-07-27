from __future__ import annotations

import hashlib

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.alignment import align_registration
from pipeline.metric_alignment_evidence import (
    MetricAlignmentEvidenceError,
    MetricAlignmentPolicy,
    canonical_control_points_bytes,
    canonical_metric_alignment_decision_bytes,
    canonical_metric_alignment_measurement_bytes,
    canonical_metric_alignment_policy_bytes,
    decide_metric_alignment,
    measure_metric_alignment,
    verify_metric_alignment_decision,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
    ControlPoint,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    GeoAlignment,
    GeoAnchor,
    Handedness,
    MetricStatus,
    RegistrationResult,
)

_ORIGIN = GeoAnchor(lat=26.0, lon=119.0, alt=50.0)


def _source_registration() -> RegistrationResult:
    source = {
        "cp0": (0.0, 0.0, 0.0),
        "cp1": (10.0, 0.0, 0.0),
        "cp2": (0.0, 10.0, 0.0),
        "cp3": (0.0, 0.0, 10.0),
        "cp4": (4.0, 3.0, 6.0),
    }
    poses = [
        CameraPose(
            image=image,
            session_id="s0",
            quat_wxyz=[1.0, 0.0, 0.0, 0.0],
            t_xyz=list(xyz),
            intrinsics=CameraIntrinsics.from_fov(640, 480),
        )
        for image, xyz in source.items()
    ]
    return RegistrationResult(
        engine="colmap",
        pose_frame=CoordinateFrame(
            frame_id="sfm-local",
            handedness=Handedness.RIGHT,
            axes=AxisConvention.SFM_ARBITRARY,
            units=CoordinateUnits.ARBITRARY,
            metric_status=MetricStatus.ARBITRARY,
            geo_aligned=GeoAlignment.UNALIGNED,
            provenance=FrameProvenance.SFM,
            evidence=("fresh-colmap",),
        ),
        alignment_status=AlignmentStatus.UNALIGNED,
        geo_origin=_ORIGIN,
        sessions=[
            CaptureSession(
                session_id="s0",
                kind="photo_batch",
                source="photos",
                images=list(source),
            )
        ],
        poses=poses,
    )


def _control_points(*, noisy: bool = True) -> list[ControlPoint]:
    source = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 10.0],
            [4.0, 3.0, 6.0],
        ]
    )
    target = 1.5 * source + np.array([3.0, -2.0, 5.0])
    if noisy:
        target[-1] += np.array([0.12, -0.04, 0.03])
    return [
        ControlPoint(
            label=f"cp{index}",
            image=f"cp{index}",
            enu_xyz=tuple(point.tolist()),
        )
        for index, point in enumerate(target)
    ]


def _aligned_fixture():
    source = _source_registration()
    control_points = _control_points()
    aligned = align_registration(
        source,
        control_points,
        geo_origin=_ORIGIN,
        max_rms_m=1.0,
    )
    measurement = measure_metric_alignment(
        source,
        aligned,
        control_points,
    )
    return source, control_points, aligned, measurement


def _policy(*, max_rms_m: float, max_residual_m: float) -> MetricAlignmentPolicy:
    return MetricAlignmentPolicy.create(
        max_rms_m=max_rms_m,
        max_residual_m=max_residual_m,
        min_span_ratio=1e-3,
    )


def test_measurement_is_policy_independent_and_content_bound():
    source, control_points, _aligned, measurement = _aligned_fixture()

    assert measurement.registration_sha256 == hashlib.sha256(
        canonical_model_bytes(source)
    ).hexdigest()
    assert measurement.control_points_sha256 == hashlib.sha256(
        canonical_control_points_bytes(control_points)
    ).hexdigest()
    assert measurement.transform_history_sha256 == hashlib.sha256(
        b'{"transforms":[]}\n'
    ).hexdigest()
    assert measurement.rms_residual_m > 0
    assert measurement.max_residual_m >= measurement.rms_residual_m

    strict = _policy(max_rms_m=0.001, max_residual_m=0.001)
    relaxed = _policy(max_rms_m=1.0, max_residual_m=1.0)

    assert strict.content_sha256 != relaxed.content_sha256
    assert canonical_metric_alignment_measurement_bytes(measurement) == (
        canonical_metric_alignment_measurement_bytes(measurement)
    )


def test_policy_change_changes_decision_not_measurement():
    _source, _control_points, aligned, measurement = _aligned_fixture()
    measurement_sha = measurement.content_sha256

    rejected = decide_metric_alignment(
        measurement,
        _policy(max_rms_m=0.001, max_residual_m=0.001),
        aligned_registration=aligned,
    )
    accepted_policy = _policy(max_rms_m=1.0, max_residual_m=1.0)
    accepted = decide_metric_alignment(
        measurement,
        accepted_policy,
        aligned_registration=aligned,
    )

    assert measurement.content_sha256 == measurement_sha
    assert rejected.status == "rejected"
    assert "rms-exceeded" in rejected.failure_codes
    assert rejected.aligned_registration_sha256 is None
    assert rejected.output_metric_status is None
    assert accepted.status == "accepted"
    assert accepted.measurement_sha256 == measurement_sha
    assert accepted.policy_sha256 == accepted_policy.content_sha256
    assert accepted.aligned_registration_sha256 == hashlib.sha256(
        canonical_model_bytes(aligned)
    ).hexdigest()
    assert accepted.output_metric_status == "metric"
    assert accepted.output_geo_alignment == "aligned"
    assert accepted.output_frame_id == "world-enu"


def test_decision_verifier_rejects_identity_drift():
    source, control_points, aligned, measurement = _aligned_fixture()
    policy = _policy(max_rms_m=1.0, max_residual_m=1.0)
    decision = decide_metric_alignment(
        measurement,
        policy,
        aligned_registration=aligned,
    )

    verify_metric_alignment_decision(
        source_registration=source,
        control_points=control_points,
        aligned_registration=aligned,
        measurement=measurement,
        policy=policy,
        decision=decision,
    )

    drifted_points = list(control_points)
    drifted_points[-1] = drifted_points[-1].model_copy(
        update={"enu_xyz": (99.0, 98.0, 97.0)}
    )
    with pytest.raises(
        MetricAlignmentEvidenceError,
        match="control points",
    ):
        verify_metric_alignment_decision(
            source_registration=source,
            control_points=drifted_points,
            aligned_registration=aligned,
            measurement=measurement,
            policy=policy,
            decision=decision,
        )


def test_rejected_decision_cannot_carry_metric_output_claims():
    _source, _control_points, aligned, measurement = _aligned_fixture()
    decision = decide_metric_alignment(
        measurement,
        _policy(max_rms_m=0.001, max_residual_m=0.001),
        aligned_registration=aligned,
    )
    payload = decision.model_dump(mode="python")
    payload["output_metric_status"] = "metric"
    with pytest.raises(ValidationError, match="rejected"):
        type(decision).model_validate(payload)


def test_canonical_layers_are_distinct_and_round_trip():
    _source, _control_points, aligned, measurement = _aligned_fixture()
    policy = _policy(max_rms_m=1.0, max_residual_m=1.0)
    decision = decide_metric_alignment(
        measurement,
        policy,
        aligned_registration=aligned,
    )

    assert canonical_metric_alignment_measurement_bytes(measurement).endswith(
        b"\n"
    )
    assert canonical_metric_alignment_policy_bytes(policy).endswith(b"\n")
    assert canonical_metric_alignment_decision_bytes(decision).endswith(b"\n")
    assert len(
        {
            measurement.content_sha256,
            policy.content_sha256,
            decision.content_sha256,
        }
    ) == 3
