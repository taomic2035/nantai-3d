from __future__ import annotations

import pytest

from pipeline.human_review_inputs import (
    PRODUCTION_MAXIMUM_SCREENSHOT_BYTES,
    HumanReviewInputError,
    materialize_human_review_policy,
)
from pipeline.real_scene_acceptance import (
    REQUIRED_VISUAL_CATEGORIES,
    HumanReviewPolicy,
    canonical_human_review_policy_bytes,
)
from pipeline.viewer_acceptance import (
    canonical_viewer_performance_policy_bytes,
    canonical_viewer_performance_report_bytes,
)
from tests.test_viewer_acceptance import (
    _policy as _viewer_policy,
)
from tests.test_viewer_acceptance import (
    _report_v2 as _viewer_report_v2,
)


def _viewer_evidence(tmp_path):
    report = _viewer_report_v2(tmp_path)
    report_path = tmp_path / "viewer/report.json"
    report_path.write_bytes(canonical_viewer_performance_report_bytes(report))
    return report, report_path, tmp_path / report.viewer_policy.path


def test_materializer_derives_canonical_policy_from_verified_v2_capture(
    tmp_path,
):
    report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)
    output = tmp_path / "review/human-review-policy.json"

    result = materialize_human_review_policy(
        evidence_root=tmp_path,
        viewer_policy_path=viewer_policy_path,
        viewer_report_path=report_path,
        output_path=output,
    )

    payload = output.read_bytes()
    policy = HumanReviewPolicy.model_validate_json(payload)
    assert result.output_path == output
    assert result.viewer_report_sha256 == report.content_sha256
    assert policy.source_role == "production-acceptance"
    assert policy.required_categories == REQUIRED_VISUAL_CATEGORIES
    assert policy.required_pose_ids == tuple(row.pose_id for row in report.poses)
    assert policy.maximum_screenshot_bytes == PRODUCTION_MAXIMUM_SCREENSHOT_BYTES
    assert payload == canonical_human_review_policy_bytes(policy)

    with pytest.raises(HumanReviewInputError, match="output.*absent"):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=output,
        )


def test_materializer_rejects_capture_artifact_tamper(
    tmp_path,
):
    _report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)
    viewer_policy_path.write_bytes(b"tampered")

    with pytest.raises(
        HumanReviewInputError,
        match="Viewer capture|policy|changed",
    ):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=tmp_path / "review/policy.json",
        )


def test_materializer_rejects_output_outside_evidence_root(
    tmp_path,
):
    _report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)

    with pytest.raises(
        HumanReviewInputError,
        match="evidence root",
    ):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=tmp_path.parent / "escaped-policy.json",
        )


def test_materializer_rejects_viewer_policy_pose_drift(
    tmp_path,
):
    report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)
    drifted = _viewer_policy().model_copy(
        update={"required_pose_ids": tuple(reversed(_viewer_policy().required_pose_ids))}
    )
    viewer_policy_path.write_bytes(canonical_viewer_performance_policy_bytes(drifted))
    assert tuple(row.pose_id for row in report.poses) != (drifted.required_pose_ids)

    with pytest.raises(
        HumanReviewInputError,
        match="policy|pose",
    ):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=tmp_path / "review/policy.json",
        )
