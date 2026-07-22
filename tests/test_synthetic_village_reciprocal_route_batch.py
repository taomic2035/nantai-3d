"""Durable six-role batch ledger tests for the reciprocal production caller."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.elevated_topology import build_elevated_topology_plan
from pipeline.synthetic_village.environment_module import (
    build_default_environment_module_plan,
)
from pipeline.synthetic_village.production_preflight import ProductionClearancePolicy
from pipeline.synthetic_village.production_profile import build_production_camera_plan
from pipeline.synthetic_village.production_quality_gates import (
    candidate_synthetic_village_frame_quality_policy_v2,
)
from pipeline.synthetic_village.production_render import LocalProductionQualityPolicy
from pipeline.synthetic_village.reciprocal_route_batch import (
    RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS,
    ReciprocalProductionBatchTarget,
    load_reciprocal_production_batch_journal,
    run_reciprocal_production_batch,
)
from pipeline.synthetic_village.reciprocal_route_module import (
    build_default_reciprocal_route_module_plan,
)
from pipeline.synthetic_village.reciprocal_route_production import (
    ReciprocalProductionCameraResult,
    ReciprocalProductionError,
    VerifiedReciprocalProductionBuild,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _context(tmp_path: Path):
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    source_plan = build_production_camera_plan(scene, topology)
    environment = build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )
    reciprocal = build_default_reciprocal_route_module_plan(
        scene=scene,
        elevated_topology=topology,
        environment_module_plan=environment,
        production_camera_plan=source_plan,
    )
    report_path = tmp_path / "reciprocal-route-build-report.json"
    report_path.write_bytes(b"verified-report\n")
    blend_path = tmp_path / "village-reciprocal-route.blend"
    blend_path.write_bytes(b"verified-blend")
    executable = tmp_path / "blender.exe"
    executable.write_bytes(b"verified-blender")
    registry = tuple(
        canary.ObjectRegistryEntry(
            object_id=f"test-object-{instance_id:03d}",
            instance_id=instance_id,
            semantic_id=3,
            material_id=1,
            variant_id=None,
        )
        for instance_id in range(1, 219)
    )
    build = VerifiedReciprocalProductionBuild(
        build_id="1" * 64,
        report_path=report_path,
        report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        blend_path=blend_path,
        blend_sha256=hashlib.sha256(blend_path.read_bytes()).hexdigest(),
        environment_module_build_report_sha256="6" * 64,
        reciprocal_route_module_plan_sha256="7" * 64,
        object_registry=registry,
        role_camera_candidates=reciprocal.role_camera_candidates,
    )
    targets = tuple(
        ReciprocalProductionBatchTarget(
            role_module_id=role_id,
            target_camera_id=(
                "camera-ground-route-010"
                if index % 2 == 0
                else "camera-ground-route-039"
            ),
        )
        for index, role_id in enumerate(RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS)
    )
    return source_plan, build, executable, targets


def _post_render_policy():
    return candidate_synthetic_village_frame_quality_policy_v2(
        minimum_valid_depth_pixel_ratio=0.3,
        minimum_valid_normal_pixel_ratio=0.3,
        minimum_valid_semantic_pixel_ratio=0.3,
        maximum_sky_pixel_ratio=0.55,
        maximum_upper_ground_pixel_ratio=0.3,
        maximum_near_depth_pixel_ratio=0.35,
        maximum_near_instance_dominance_ratio=0.7,
        maximum_upper_instance_dominance_ratio=0.7,
        near_depth_m=2.0,
        upper_region_end_row_exclusive=288,
        ground_semantic_ids=(1,),
    )


def _accepted_result(output_root: Path, role_id: str, camera_id: str):
    render_id = hashlib.sha256(role_id.encode()).hexdigest()
    frame_root = output_root / "frames" / render_id / camera_id
    frame_root.mkdir(parents=True)
    return ReciprocalProductionCameraResult(
        render_id=render_id,
        camera_id=camera_id,
        frame_root=frame_root,
        preflight_request_sha256="a" * 64,
        preflight_report_sha256="b" * 64,
        render_request_sha256="c" * 64,
        render_report_sha256="d" * 64,
        journal_sha256="e" * 64,
        quality_request_sha256="f" * 64,
        quality_report_sha256="0" * 64,
    )


def test_batch_persists_six_role_outcomes_and_retries_only_failure(
    tmp_path: Path,
) -> None:
    source_plan, build, executable, targets = _context(tmp_path)
    output_root = tmp_path / "batch"
    calls: list[str] = []

    def first_runner(**kwargs):
        role_id = kwargs["role_camera_candidate"].role_module_id
        calls.append(role_id)
        if role_id == "lower-valley-uphill":
            raise ReciprocalProductionError(
                "post-render quality rejected camera: camera-ground-route-039",
            )
        return _accepted_result(
            output_root,
            role_id,
            kwargs["target_camera_id"],
        )

    first = run_reciprocal_production_batch(
        verified_build=build,
        source_plan=source_plan,
        targets=targets,
        blender_executable=executable,
        output_root=output_root,
        clearance_policy=ProductionClearancePolicy(
            near_distance_m=2.0,
            minimum_upper_middle_near_hit_count=5,
        ),
        quality_policy=LocalProductionQualityPolicy(
            minimum_valid_pixel_ratio=0.05,
        ),
        post_render_policy=_post_render_policy(),
        camera_runner=first_runner,
    )

    assert calls == list(RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS)
    assert first.accepted_count == 5
    assert first.failed_count == 1
    assert first.reused_count == 0
    journal = load_reciprocal_production_batch_journal(first.journal_path)
    assert journal.batch_id == first.batch_id
    assert tuple(row.role_module_id for row in journal.entries) == (
        RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS
    )
    assert journal.entries[-1].state == "failed"
    assert journal.entries[-1].error_code == "post-render-quality-rejected"
    assert journal.entries[-1].quality_report_sha256 is None

    calls.clear()

    def retry_runner(**kwargs):
        role_id = kwargs["role_camera_candidate"].role_module_id
        calls.append(role_id)
        return _accepted_result(
            output_root,
            role_id,
            kwargs["target_camera_id"],
        )

    second = run_reciprocal_production_batch(
        verified_build=build,
        source_plan=source_plan,
        targets=targets,
        blender_executable=executable,
        output_root=output_root,
        clearance_policy=ProductionClearancePolicy(
            near_distance_m=2.0,
            minimum_upper_middle_near_hit_count=5,
        ),
        quality_policy=LocalProductionQualityPolicy(
            minimum_valid_pixel_ratio=0.05,
        ),
        post_render_policy=_post_render_policy(),
        camera_runner=retry_runner,
    )

    assert calls == ["lower-valley-uphill"]
    assert second.accepted_count == 6
    assert second.failed_count == 0
    assert second.reused_count == 5
    assert all(
        row.state == "accepted"
        for row in load_reciprocal_production_batch_journal(
            second.journal_path,
        ).entries
    )
