"""Local-production batch evidence stays L0 and rejects low-value frames."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.production_journal import ProductionArtifactRecord
from pipeline.synthetic_village.production_preflight import (
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
    build_production_clearance_report,
    build_production_clearance_request,
    canonical_production_clearance_report_bytes,
)
from pipeline.synthetic_village.production_render import (
    LocalProductionQualityPolicy,
    build_local_production_frame_request,
    evaluate_local_production_frame_quality,
    local_production_quality_policy_sha256,
    new_local_production_render_journal,
    transition_local_production_frame,
)
from tests.test_synthetic_village_production_render import _request

ROOT = Path(__file__).resolve().parents[1]


def _statistics(*, background_pixels: int) -> canary.RenderStatistics:
    return canary.RenderStatistics(
        depth_min_m=1.0,
        depth_max_m=100.0,
        depth_background_pixels=background_pixels,
        depth_max_range_error_m=0.001,
        normal_max_unit_error=0.0001,
        instance_ids=(0, 1),
        semantic_ids=(0, 3),
    )


def _artifacts(camera_id: str) -> tuple[ProductionArtifactRecord, ...]:
    return tuple(
        ProductionArtifactRecord(
            kind=kind,
            path=path,
            sha256="a" * 64,
            size_bytes=10,
        )
        for kind, path in (
            ("rgb", f"rgb/{camera_id}.png"),
            ("depth", f"depth/{camera_id}.exr"),
            ("normal", f"normal/{camera_id}.exr"),
            ("instance-mask", f"instance/{camera_id}.png"),
            ("semantic-mask", f"semantic/{camera_id}.png"),
            ("camera-metadata", f"cameras/{camera_id}.json"),
        )
    )


def _preflight_context(request, *, obstructed: bool):
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )
    preflight_request = build_production_clearance_request(
        plan=request.production_plan,
        selected_camera_ids=(request.camera.camera_id,),
        build_id=request.build_id,
        blender_executable_sha256=request.blender_executable_sha256,
        preflight_script_sha256="6" * 64,
        blend_sha256=request.blend_sha256,
        build_report_sha256=request.build_report_sha256,
        object_registry=request.object_registry,
        auxiliary_registry=request.auxiliary_registry,
        semantic_registry=request.semantic_registry,
        policy=policy,
    )
    evidence = ProductionCameraClearanceEvidence(
        camera_id=request.camera.camera_id,
        rays=tuple(
            ProductionClearanceRayEvidence(
                sample_x=sample_x,
                sample_y=sample_y,
                hit=obstructed and sample_y >= 0.0,
                distance_m=0.5 if obstructed and sample_y >= 0.0 else None,
                object_name=(
                    "nv__lower-bridge__deck"
                    if obstructed and sample_y >= 0.0
                    else None
                ),
                stable_id=(
                    "lower-bridge"
                    if obstructed and sample_y >= 0.0
                    else None
                ),
                part_id=(
                    "deck" if obstructed and sample_y >= 0.0 else None
                ),
                semantic_id=(
                    4 if obstructed and sample_y >= 0.0 else None
                ),
            )
            for sample_y in policy.sample_grid
            for sample_x in policy.sample_grid
        ),
    )
    report = build_production_clearance_report(
        preflight_request,
        evidence=(evidence,),
    )
    report_sha256 = hashlib.sha256(
        canonical_production_clearance_report_bytes(report),
    ).hexdigest()
    return preflight_request, report, report_sha256


def _new_journal(request, *, policy: LocalProductionQualityPolicy):
    preflight_request, preflight_report, report_sha256 = _preflight_context(
        request,
        obstructed=False,
    )
    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=policy,
    )
    return new_local_production_render_journal(
        bound_request,
        quality_policy=policy,
        preflight_request=preflight_request,
        preflight_report=preflight_report,
        preflight_report_sha256=report_sha256,
        preflight_wall_clock_seconds=1.0,
    )


def _bound_render_request(
    request,
    *,
    preflight_request,
    quality_policy: LocalProductionQualityPolicy,
):
    return build_local_production_frame_request(
        plan=request.production_plan,
        camera_id=request.camera.camera_id,
        build_adapter=request.build_adapter,
        build_id=request.build_id,
        blender_executable_sha256=request.blender_executable_sha256,
        renderer_script_sha256=request.renderer_script_sha256,
        blend_sha256=request.blend_sha256,
        build_report_sha256=request.build_report_sha256,
        object_registry=request.object_registry,
        auxiliary_registry=request.auxiliary_registry,
        semantic_registry=request.semantic_registry,
        preflight_id=preflight_request.preflight_id,
        quality_policy_sha256=local_production_quality_policy_sha256(
            quality_policy,
        ),
    )


def test_valid_pixel_quality_gate_is_explicit_and_measured() -> None:
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)

    passing = evaluate_local_production_frame_quality(
        _statistics(background_pixels=116_775),
        policy=policy,
    )
    rejected = evaluate_local_production_frame_quality(
        _statistics(background_pixels=200_000),
        policy=policy,
    )

    assert passing.total_pixel_count == 1024 * 576
    assert passing.valid_pixel_count == (1024 * 576) - 116_775
    assert passing.valid_pixel_ratio == 0.802017
    assert passing.passes is True
    assert rejected.valid_pixel_ratio == 0.660916
    assert rejected.passes is False
    assert passing.trust_effect == "none-quality-filter-only"


def test_local_production_journal_is_l0_and_covers_the_immutable_plan() -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)

    journal = _new_journal(request, policy=policy)

    assert journal.verification_level == "L0"
    assert journal.synthetic is True
    assert journal.geometry_trust == "simplified-pbr-not-render-parity"
    assert journal.production_plan_sha256 == request.production_plan_sha256
    assert journal.camera_registry_sha256 == request.camera_registry_sha256
    assert journal.quality_policy == policy
    assert len(journal.frames) == 180
    assert tuple(row.camera_id for row in journal.frames) == tuple(
        row.camera_id for row in request.production_plan.cameras
    )
    assert {row.state for row in journal.frames} == {"planned"}


def test_preflight_rejection_is_bound_without_six_layer_artifacts() -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    preflight_request, preflight_report, report_sha256 = _preflight_context(
        request,
        obstructed=True,
    )

    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=policy,
    )
    journal = new_local_production_render_journal(
        bound_request,
        quality_policy=policy,
        preflight_request=preflight_request,
        preflight_report=preflight_report,
        preflight_report_sha256=report_sha256,
        preflight_wall_clock_seconds=1.25,
    )

    frame = next(
        row
        for row in journal.frames
        if row.camera_id == request.camera.camera_id
    )
    assert journal.preflight_id == preflight_request.preflight_id
    assert journal.preflight_report_sha256 == report_sha256
    assert journal.preflight_wall_clock_seconds == 1.25
    assert frame.state == "preflight-rejected"
    assert frame.artifacts == ()
    assert frame.runtime_report_sha256 is None
    assert frame.statistics is None
    assert frame.quality is None
    assert frame.clearance_decision is not None
    assert frame.clearance_decision.passes is False
    assert frame.preflight_report_sha256 == report_sha256


def test_geometry_preflight_pass_stays_planned_not_verified() -> None:
    request = _request()
    preflight_request, preflight_report, report_sha256 = _preflight_context(
        request,
        obstructed=False,
    )

    quality_policy = LocalProductionQualityPolicy(
        minimum_valid_pixel_ratio=0.75,
    )
    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=quality_policy,
    )
    journal = new_local_production_render_journal(
        bound_request,
        quality_policy=quality_policy,
        preflight_request=preflight_request,
        preflight_report=preflight_report,
        preflight_report_sha256=report_sha256,
        preflight_wall_clock_seconds=1.0,
    )

    frame = next(
        row
        for row in journal.frames
        if row.camera_id == request.camera.camera_id
    )
    assert frame.state == "planned"
    assert frame.clearance_decision is not None
    assert frame.clearance_decision.passes is True
    assert frame.artifacts == ()


def test_local_journal_separates_verified_and_quality_rejected_frames() -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    journal = _new_journal(request, policy=policy)
    camera_id = request.camera.camera_id

    passing_statistics = _statistics(background_pixels=116_775)
    passing_quality = evaluate_local_production_frame_quality(
        passing_statistics,
        policy=policy,
    )
    verified = transition_local_production_frame(
        journal,
        camera_id,
        state="verified",
        artifacts=_artifacts(camera_id),
        runtime_report_sha256="b" * 64,
        statistics=passing_statistics,
        quality=passing_quality,
        wall_clock_seconds=11.19,
    )
    assert next(row for row in verified.frames if row.camera_id == camera_id).state == (
        "verified"
    )
    assert verified.journal_sha256 != journal.journal_sha256

    rejected_statistics = _statistics(background_pixels=200_000)
    rejected_quality = evaluate_local_production_frame_quality(
        rejected_statistics,
        policy=policy,
    )
    rejected = transition_local_production_frame(
        journal,
        camera_id,
        state="rejected",
        artifacts=_artifacts(camera_id),
        runtime_report_sha256="c" * 64,
        statistics=rejected_statistics,
        quality=rejected_quality,
        wall_clock_seconds=12.0,
    )
    frame = next(row for row in rejected.frames if row.camera_id == camera_id)
    assert frame.state == "rejected"
    assert frame.quality is not None
    assert frame.quality.passes is False


def test_failed_frame_can_retry_without_stale_failure_evidence() -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    journal = _new_journal(request, policy=policy)
    camera_id = request.camera.camera_id

    rendering = transition_local_production_frame(
        journal,
        camera_id,
        state="rendering",
    )
    failed = transition_local_production_frame(
        rendering,
        camera_id,
        state="failed",
        error="Blender exited with code 17",
        wall_clock_seconds=4.25,
    )

    retried = transition_local_production_frame(
        failed,
        camera_id,
        state="rendering",
    )

    frame = next(row for row in retried.frames if row.camera_id == camera_id)
    assert frame.state == "rendering"
    assert frame.error is None
    assert frame.wall_clock_seconds is None


def test_cli_requires_an_explicit_quality_threshold() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/synthetic_village.py",
            "render-production-local",
            "--help",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "--min-valid-pixel-ratio" in completed.stdout
    assert "--clearance-near-distance-m" in completed.stdout
    assert "--min-upper-middle-near-hits" in completed.stdout
    assert "--preflight-only" in completed.stdout
    assert "--visual-pack-root" in completed.stdout
    assert "required" not in completed.stderr
