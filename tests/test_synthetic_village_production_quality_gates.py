"""Post-render quality evidence is raw, explicit, and byte-bound."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.production_journal import ProductionArtifactRecord
from pipeline.synthetic_village.production_quality_gates import (
    ProductionFrameEvidenceBinding,
    ProductionFrameLayerStatistics,
    ProductionFrameQualityError,
    ProductionFrameQualityPolicyV2,
    build_production_frame_quality_report_v2,
    build_production_frame_quality_request_v2,
    candidate_synthetic_village_frame_quality_policy_v2,
    canonical_production_frame_quality_policy_v2_bytes,
    canonical_production_frame_quality_request_v2_bytes,
    evaluate_production_frame_quality_v2,
    production_frame_quality_policy_v2_sha256,
)
from tests.test_synthetic_village_production_render import _request

CAMERA_ID = "camera-ground-route-034"
TOTAL_PIXELS = 1024 * 576
UPPER_PIXELS = 1024 * 288


def _statistics(**updates: object) -> ProductionFrameLayerStatistics:
    payload = {
        "camera_id": CAMERA_ID,
        "total_pixel_count": TOTAL_PIXELS,
        "upper_pixel_count": UPPER_PIXELS,
        "valid_depth_pixel_count": 500_000,
        "valid_normal_pixel_count": 500_000,
        "registered_instance_pixel_count": 120_000,
        "valid_semantic_pixel_count": 500_000,
        "sky_pixel_count": 89_824,
        "upper_ground_pixel_count": 20_000,
        "near_depth_pixel_count": 40_000,
        "dominant_near_instance_id": 42,
        "dominant_near_instance_pixel_count": 25_000,
        "dominant_upper_instance_id": 42,
        "dominant_upper_instance_pixel_count": 180_000,
    }
    payload.update(updates)
    return ProductionFrameLayerStatistics(**payload)


def _artifacts(camera_id: str = CAMERA_ID) -> tuple[ProductionArtifactRecord, ...]:
    return tuple(
        ProductionArtifactRecord(
            kind=kind,
            path=f"{directory}/{camera_id}{suffix}",
            sha256=hashlib.sha256(f"{kind}:{camera_id}".encode()).hexdigest(),
            size_bytes=100 + index,
        )
        for index, (kind, directory, suffix) in enumerate(
            (
                ("rgb", "rgb", ".png"),
                ("depth", "depth", ".exr"),
                ("normal", "normal", ".exr"),
                ("instance-mask", "instance", ".png"),
                ("semantic-mask", "semantic", ".png"),
                ("camera-metadata", "cameras", ".json"),
            ),
        )
    )


def _binding(
    *,
    camera_id: str = CAMERA_ID,
    artifacts: tuple[ProductionArtifactRecord, ...] | None = None,
) -> ProductionFrameEvidenceBinding:
    return ProductionFrameEvidenceBinding(
        camera_id=camera_id,
        runtime_report_sha256="7" * 64,
        artifacts=artifacts or _artifacts(camera_id),
    )


def _policy() -> ProductionFrameQualityPolicyV2:
    return candidate_synthetic_village_frame_quality_policy_v2(
        minimum_valid_depth_pixel_ratio=0.30,
        minimum_valid_normal_pixel_ratio=0.30,
        minimum_valid_semantic_pixel_ratio=0.30,
        maximum_sky_pixel_ratio=0.55,
        maximum_upper_ground_pixel_ratio=0.30,
        maximum_near_depth_pixel_ratio=0.35,
        maximum_near_instance_dominance_ratio=0.70,
        maximum_upper_instance_dominance_ratio=0.70,
        near_depth_m=2.0,
        upper_region_end_row_exclusive=288,
        ground_semantic_ids=(1,),
    )


def _quality_request(
    *,
    frames: tuple[ProductionFrameEvidenceBinding, ...] | None = None,
):
    render_request = _request(camera_id=CAMERA_ID)
    policy = _policy()
    return build_production_frame_quality_request_v2(
        plan=render_request.production_plan,
        selected_camera_ids=(CAMERA_ID,),
        build_id=render_request.build_id,
        render_id=render_request.render_id,
        blender_executable_sha256=render_request.blender_executable_sha256,
        renderer_script_sha256=render_request.renderer_script_sha256,
        blend_sha256=render_request.blend_sha256,
        build_report_sha256=render_request.build_report_sha256,
        object_registry=render_request.object_registry,
        semantic_registry=render_request.semantic_registry,
        journal_sha256="8" * 64,
        frames=frames or (_binding(),),
        policy=policy,
    )


def test_statistics_derive_ratios_from_raw_integer_counts() -> None:
    statistics = _statistics()

    assert statistics.valid_depth_pixel_ratio == round(500_000 / TOTAL_PIXELS, 6)
    assert statistics.valid_normal_pixel_ratio == round(500_000 / TOTAL_PIXELS, 6)
    assert statistics.registered_instance_pixel_ratio == round(
        120_000 / TOTAL_PIXELS,
        6,
    )
    assert statistics.valid_semantic_pixel_ratio == round(
        500_000 / TOTAL_PIXELS,
        6,
    )
    assert statistics.sky_pixel_ratio == round(89_824 / TOTAL_PIXELS, 6)
    assert statistics.upper_ground_pixel_ratio == round(
        20_000 / UPPER_PIXELS,
        6,
    )
    assert statistics.near_depth_pixel_ratio == round(40_000 / 500_000, 6)
    assert statistics.near_instance_dominance_ratio == round(
        25_000 / 40_000,
        6,
    )
    assert statistics.upper_instance_dominance_ratio == round(
        180_000 / UPPER_PIXELS,
        6,
    )


def test_statistics_reject_ratio_injection_and_invalid_counts() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _statistics(valid_depth_pixel_ratio=0.99)
    with pytest.raises(ValidationError):
        _statistics(valid_depth_pixel_count=TOTAL_PIXELS + 1)
    with pytest.raises(ValidationError):
        _statistics(dominant_upper_instance_pixel_count=UPPER_PIXELS + 1)
    with pytest.raises(ValidationError):
        _statistics(
            dominant_near_instance_id=None,
            dominant_near_instance_pixel_count=1,
        )


def test_policy_binds_measurement_semantics_and_has_no_instance_minimum() -> None:
    policy = _policy()
    rule_ids = tuple(row.rule_id for row in policy.rules)

    assert policy.near_depth_m == 2.0
    assert policy.upper_region_end_row_exclusive == 288
    assert policy.ground_semantic_ids == (1,)
    assert policy.sky_semantic_id == 0
    assert policy.ratio_round_digits == 6
    assert policy.near_depth_denominator == "valid-depth-pixels"
    assert policy.upper_dominance_denominator == "upper-region-pixels"
    assert policy.near_instance_dominance_denominator == "near-depth-pixels"
    assert "valid-instance-pixel-ratio" not in rule_ids


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("near_depth_m", 2.1),
        ("upper_region_end_row_exclusive", 287),
        ("ground_semantic_ids", (1, 2)),
        ("near_depth_denominator", "all-pixels"),
    ),
)
def test_policy_measurement_mutation_changes_policy_sha(
    field: str,
    value: object,
) -> None:
    policy = _policy()
    altered = ProductionFrameQualityPolicyV2.model_validate(
        {**policy.model_dump(mode="python"), field: value},
    )

    assert production_frame_quality_policy_v2_sha256(altered) != (
        production_frame_quality_policy_v2_sha256(policy)
    )
    assert canonical_production_frame_quality_policy_v2_bytes(policy).endswith(
        b"\n",
    )


def test_quality_request_binds_exact_render_journal_and_six_artifacts() -> None:
    request = _quality_request()
    render_request = _request(camera_id=CAMERA_ID)

    assert request.render_id == render_request.render_id
    assert (
        request.renderer_script_sha256
        == render_request.renderer_script_sha256
    )
    assert request.journal_sha256 == "8" * 64
    assert len(request.frames) == 1
    assert len(request.frames[0].artifacts) == 6
    assert hashlib.sha256(
        canonical_production_frame_quality_request_v2_bytes(
            request,
            exclude_request_id=True,
        ),
    ).hexdigest() == request.request_id


def test_changing_artifact_sha_changes_request_id() -> None:
    original = _quality_request()
    artifacts = list(_artifacts())
    artifacts[0] = artifacts[0].model_copy(update={"sha256": "f" * 64})
    altered = _quality_request(
        frames=(_binding(artifacts=tuple(artifacts)),),
    )

    assert altered.request_id != original.request_id


def test_frame_binding_requires_exact_six_file_contract() -> None:
    with pytest.raises(ValidationError):
        _binding(artifacts=_artifacts()[:-1])
    duplicate = (*_artifacts()[:-1], _artifacts()[0])
    with pytest.raises(ValidationError):
        _binding(artifacts=duplicate)
    redirected = list(_artifacts())
    redirected[0] = redirected[0].model_copy(
        update={"path": f"rgb/../rgb/{CAMERA_ID}.png"},
    )
    with pytest.raises(ValidationError):
        _binding(artifacts=tuple(redirected))


def test_request_rejects_unknown_instance_and_semantic_policy_ids() -> None:
    render_request = _request(camera_id=CAMERA_ID)
    policy = _policy()
    with pytest.raises(ValidationError, match="semantic"):
        build_production_frame_quality_request_v2(
            plan=render_request.production_plan,
            selected_camera_ids=(CAMERA_ID,),
            build_id=render_request.build_id,
            render_id=render_request.render_id,
            blender_executable_sha256=render_request.blender_executable_sha256,
            renderer_script_sha256=render_request.renderer_script_sha256,
            blend_sha256=render_request.blend_sha256,
            build_report_sha256=render_request.build_report_sha256,
            object_registry=render_request.object_registry,
            semantic_registry=render_request.semantic_registry,
            journal_sha256="8" * 64,
            frames=(_binding(),),
            policy=policy.model_copy(update={"ground_semantic_ids": (99,)}),
        )


def test_report_rejects_dominant_instance_absent_from_bound_registry() -> None:
    request = _quality_request()
    unknown = _statistics(
        dominant_near_instance_id=999,
        dominant_upper_instance_id=999,
    )

    with pytest.raises(
        ProductionFrameQualityError,
        match="instance absent from the bound registry",
    ):
        build_production_frame_quality_report_v2(
            request,
            statistics=(unknown,),
        )


def test_evaluation_uses_derived_counts_and_operator_policy() -> None:
    decision = evaluate_production_frame_quality_v2(
        _statistics(),
        policy=_policy(),
    )

    assert decision.camera_id == CAMERA_ID
    assert "upper-instance-dominance" not in decision.failed_rule_ids
    assert json.loads(
        canonical_production_frame_quality_request_v2_bytes(
            _quality_request(),
        ),
    )["synthetic"] is True
