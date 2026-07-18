"""Local-production batch evidence stays L0 and rejects low-value frames."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.production_journal import ProductionArtifactRecord
from pipeline.synthetic_village.production_render import (
    LocalProductionQualityPolicy,
    evaluate_local_production_frame_quality,
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

    journal = new_local_production_render_journal(request, quality_policy=policy)

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


def test_local_journal_separates_verified_and_quality_rejected_frames() -> None:
    request = _request()
    policy = LocalProductionQualityPolicy(minimum_valid_pixel_ratio=0.75)
    journal = new_local_production_render_journal(request, quality_policy=policy)
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
    assert "required" not in completed.stderr
