"""Local-production batch evidence stays L0 and rejects low-value frames."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.local_production_runner import (
    persist_local_production_state,
    publish_production_frame_quality_evidence,
)
from pipeline.synthetic_village.production_journal import ProductionArtifactRecord
from pipeline.synthetic_village.production_preflight import (
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
    build_production_clearance_report,
    build_production_clearance_request,
    canonical_production_clearance_report_bytes,
)
from pipeline.synthetic_village.production_quality_gates import (
    ProductionFrameLayerStatistics,
    ProductionFrameQualityReportV2,
    ProductionFrameQualityRequestV2,
    canonical_production_frame_quality_policy_v2_bytes,
    canonical_production_frame_quality_report_v2_bytes,
    canonical_production_frame_quality_request_v2_bytes,
)
from pipeline.synthetic_village.production_render import (
    LocalProductionQualityPolicy,
    build_local_production_frame_request,
    canonical_local_production_render_journal_bytes,
    evaluate_local_production_frame_quality,
    local_production_quality_policy_sha256,
    new_local_production_render_journal,
    transition_local_production_frame,
)
from scripts import synthetic_village as synthetic_village_cli
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


def _layer_statistics(camera_id: str) -> ProductionFrameLayerStatistics:
    return ProductionFrameLayerStatistics(
        camera_id=camera_id,
        upper_pixel_count=1024 * 288,
        valid_depth_pixel_count=500_000,
        valid_normal_pixel_count=500_000,
        registered_instance_pixel_count=120_000,
        valid_semantic_pixel_count=500_000,
        sky_pixel_count=(1024 * 576) - 500_000,
        upper_ground_pixel_count=20_000,
        near_depth_pixel_count=40_000,
        dominant_near_instance_id=1,
        dominant_near_instance_pixel_count=10_000,
        dominant_upper_instance_id=1,
        dominant_upper_instance_pixel_count=30_000,
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
        post_render_policy=request.post_render_policy,
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
    assert frame.layer_statistics is None
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
        layer_statistics=_layer_statistics(camera_id),
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
        layer_statistics=_layer_statistics(camera_id),
        quality=rejected_quality,
        wall_clock_seconds=12.0,
    )
    frame = next(row for row in rejected.frames if row.camera_id == camera_id)
    assert frame.state == "rejected"
    assert frame.quality is not None
    assert frame.quality.passes is False


def test_completed_frames_publish_canonical_v2_quality_sidecars(tmp_path) -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    preflight_request, _, _ = _preflight_context(request, obstructed=False)
    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=policy,
    )
    journal = _new_journal(request, policy=policy)
    camera_id = request.camera.camera_id
    statistics = _statistics(background_pixels=116_775)
    quality = evaluate_local_production_frame_quality(
        statistics,
        policy=policy,
    )
    journal = transition_local_production_frame(
        journal,
        camera_id,
        state="verified",
        artifacts=_artifacts(camera_id),
        runtime_report_sha256="b" * 64,
        statistics=statistics,
        layer_statistics=_layer_statistics(camera_id),
        quality=quality,
        wall_clock_seconds=11.19,
    )

    publication = publish_production_frame_quality_evidence(
        render_root=tmp_path,
        frame_request=bound_request,
        journal=journal,
    )

    assert publication is not None
    assert publication.selected_camera_ids == (camera_id,)
    quality_request = ProductionFrameQualityRequestV2.model_validate_json(
        publication.request_path.read_bytes(),
    )
    quality_report = ProductionFrameQualityReportV2.model_validate_json(
        publication.report_path.read_bytes(),
    )
    assert quality_request.journal_sha256 == journal.journal_sha256
    assert quality_request.frames[0].runtime_report_sha256 == "b" * 64
    assert publication.request_path.read_bytes() == (
        canonical_production_frame_quality_request_v2_bytes(quality_request)
    )
    assert publication.report_path.read_bytes() == (
        canonical_production_frame_quality_report_v2_bytes(quality_report)
    )
    assert quality_report.request_id == quality_request.request_id
    assert quality_report.statistics == (_layer_statistics(camera_id),)


def test_quality_sidecars_refresh_when_the_bound_journal_changes(tmp_path) -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    preflight_request, _, _ = _preflight_context(request, obstructed=False)
    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=policy,
    )
    journal = _new_journal(request, policy=policy)
    camera_id = request.camera.camera_id
    statistics = _statistics(background_pixels=116_775)
    quality = evaluate_local_production_frame_quality(statistics, policy=policy)
    journal = transition_local_production_frame(
        journal,
        camera_id,
        state="verified",
        artifacts=_artifacts(camera_id),
        runtime_report_sha256="b" * 64,
        statistics=statistics,
        layer_statistics=_layer_statistics(camera_id),
        quality=quality,
        wall_clock_seconds=11.19,
    )
    first = publish_production_frame_quality_evidence(
        render_root=tmp_path,
        frame_request=bound_request,
        journal=journal,
    )
    assert first is not None
    first_request = ProductionFrameQualityRequestV2.model_validate_json(
        first.request_path.read_bytes(),
    )

    changed = transition_local_production_frame(
        journal,
        camera_id,
        state="verified",
        artifacts=tuple(
            artifact.model_copy(update={"sha256": "c" * 64})
            for artifact in _artifacts(camera_id)
        ),
        runtime_report_sha256="d" * 64,
        statistics=statistics,
        layer_statistics=_layer_statistics(camera_id),
        quality=quality,
        wall_clock_seconds=12.0,
    )
    second = publish_production_frame_quality_evidence(
        render_root=tmp_path,
        frame_request=bound_request,
        journal=changed,
    )

    assert second is not None
    second_request = ProductionFrameQualityRequestV2.model_validate_json(
        second.request_path.read_bytes(),
    )
    assert second_request.journal_sha256 == changed.journal_sha256
    assert second_request.request_id != first_request.request_id
    assert second_request.frames[0].runtime_report_sha256 == "d" * 64


def test_quality_sidecars_are_removed_when_no_completed_frame_remains(tmp_path) -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    preflight_request, _, _ = _preflight_context(request, obstructed=False)
    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=policy,
    )
    journal = _new_journal(request, policy=policy)
    camera_id = request.camera.camera_id
    statistics = _statistics(background_pixels=116_775)
    journal = transition_local_production_frame(
        journal,
        camera_id,
        state="verified",
        artifacts=_artifacts(camera_id),
        runtime_report_sha256="b" * 64,
        statistics=statistics,
        layer_statistics=_layer_statistics(camera_id),
        quality=evaluate_local_production_frame_quality(
            statistics,
            policy=policy,
        ),
        wall_clock_seconds=11.19,
    )
    assert publish_production_frame_quality_evidence(
        render_root=tmp_path,
        frame_request=bound_request,
        journal=journal,
    ) is not None

    rendering = transition_local_production_frame(
        journal,
        camera_id,
        state="rendering",
    )
    publication = publish_production_frame_quality_evidence(
        render_root=tmp_path,
        frame_request=bound_request,
        journal=rendering,
    )

    assert publication is None
    assert not tmp_path.joinpath("quality-request.json").exists()
    assert not tmp_path.joinpath("quality-report.json").exists()


def test_persisted_runner_state_binds_sidecars_to_written_journal(tmp_path) -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    preflight_request, _, _ = _preflight_context(request, obstructed=False)
    bound_request = _bound_render_request(
        request,
        preflight_request=preflight_request,
        quality_policy=policy,
    )
    journal = _new_journal(request, policy=policy)
    camera_id = request.camera.camera_id
    statistics = _statistics(background_pixels=116_775)
    journal = transition_local_production_frame(
        journal,
        camera_id,
        state="verified",
        artifacts=_artifacts(camera_id),
        runtime_report_sha256="b" * 64,
        statistics=statistics,
        layer_statistics=_layer_statistics(camera_id),
        quality=evaluate_local_production_frame_quality(
            statistics,
            policy=policy,
        ),
        wall_clock_seconds=11.19,
    )

    publication = persist_local_production_state(
        journal_path=tmp_path / "render-journal.json",
        frame_request=bound_request,
        journal=journal,
    )

    assert publication is not None
    written_journal = publication.request_path.parent.joinpath(
        "render-journal.json",
    ).read_bytes()
    assert written_journal == canonical_local_production_render_journal_bytes(
        journal,
    )
    quality_request = ProductionFrameQualityRequestV2.model_validate_json(
        publication.request_path.read_bytes(),
    )
    assert quality_request.journal_sha256 == journal.journal_sha256


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
    assert "--post-render-policy" in completed.stdout
    assert "--clearance-near-distance-m" in completed.stdout
    assert "--min-upper-middle-near-hits" in completed.stdout
    assert "--preflight-only" in completed.stdout
    assert "--visual-pack-root" in completed.stdout
    assert "required" not in completed.stderr


def test_cli_loads_a_canonical_explicit_post_render_policy(tmp_path) -> None:
    policy = _request().post_render_policy
    path = tmp_path / "policy.json"
    path.write_bytes(
        canonical_production_frame_quality_policy_v2_bytes(policy),
    )

    assert synthetic_village_cli._load_post_render_policy(path) == policy
