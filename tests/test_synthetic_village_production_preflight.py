"""Production-camera clearance evidence is raw, canonical, and policy-scoped."""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.production_preflight import (
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
    canonical_production_camera_clearance_evidence_bytes,
    evaluate_production_camera_clearance,
    production_camera_clearance_evidence_sha256,
    production_clearance_policy_sha256,
)


def _evidence_with_hits(
    *,
    camera_id: str,
    hits: dict[tuple[float, float], float],
) -> ProductionCameraClearanceEvidence:
    return ProductionCameraClearanceEvidence(
        camera_id=camera_id,
        rays=tuple(
            ProductionClearanceRayEvidence(
                sample_x=sample_x,
                sample_y=sample_y,
                hit=(sample_x, sample_y) in hits,
                distance_m=hits.get((sample_x, sample_y)),
                object_name=(
                    "SV_Lower_Bridge"
                    if (sample_x, sample_y) in hits
                    else None
                ),
                stable_id=10 if (sample_x, sample_y) in hits else None,
                part_id=2 if (sample_x, sample_y) in hits else None,
                semantic_id=3 if (sample_x, sample_y) in hits else None,
            )
            for sample_y in (-0.9, -0.45, 0.0, 0.45, 0.9)
            for sample_x in (-0.9, -0.45, 0.0, 0.45, 0.9)
        ),
    )


def test_clearance_policy_is_content_addressed_and_never_upgrades_trust() -> None:
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )

    assert policy.policy_id == "synthetic-village-clearance-v1"
    assert policy.sample_grid == (-0.9, -0.45, 0.0, 0.45, 0.9)
    assert policy.trust_effect == "none-quality-filter-only"
    assert len(production_clearance_policy_sha256(policy)) == 64


def test_evaluator_rejects_five_upper_middle_near_hits_but_not_lower_ground() -> None:
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=5,
    )
    obstructed = _evidence_with_hits(
        camera_id="camera-ground-route-010",
        hits={
            (sample_x, sample_y): 0.5
            for sample_x in policy.sample_grid
            for sample_y in (0.0, 0.45, 0.9)
        },
    )
    ground_only = _evidence_with_hits(
        camera_id="camera-ground-route-001",
        hits={(sample_x, -0.9): 0.5 for sample_x in policy.sample_grid},
    )

    rejected = evaluate_production_camera_clearance(obstructed, policy=policy)
    passing = evaluate_production_camera_clearance(ground_only, policy=policy)

    assert rejected.passes is False
    assert rejected.failed_rule_ids == ("upper-middle-near-hit-count",)
    assert rejected.measured_upper_middle_near_hit_count == 15
    assert passing.passes is True
    assert passing.measured_upper_middle_near_hit_count == 0
    assert passing.trust_effect == "none-quality-filter-only"


def test_near_distance_uses_a_strict_less_than_boundary() -> None:
    policy = ProductionClearancePolicy(
        near_distance_m=2.0,
        minimum_upper_middle_near_hit_count=1,
    )
    evidence = _evidence_with_hits(
        camera_id="camera-ground-route-001",
        hits={(0.0, 0.0): 2.0},
    )

    decision = evaluate_production_camera_clearance(evidence, policy=policy)

    assert decision.measured_upper_middle_near_hit_count == 0
    assert decision.passes is True


def test_evidence_requires_the_exact_unique_fixed_grid() -> None:
    evidence = _evidence_with_hits(
        camera_id="camera-ground-route-001",
        hits={},
    )
    duplicated = (*evidence.rays[:-1], evidence.rays[0])

    with pytest.raises(ValidationError, match="exact fixed 5x5 sample grid"):
        ProductionCameraClearanceEvidence(
            camera_id=evidence.camera_id,
            rays=duplicated,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "sample_x": 0.0,
                "sample_y": 0.0,
                "hit": False,
                "distance_m": 1.0,
            },
            "miss cannot carry hit evidence",
        ),
        (
            {
                "sample_x": 0.0,
                "sample_y": 0.0,
                "hit": True,
            },
            "hit requires a measured distance",
        ),
    ],
)
def test_ray_hit_and_miss_fields_are_consistent(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProductionClearanceRayEvidence(**kwargs)


def test_hit_may_preserve_unknown_registry_fields_without_inference() -> None:
    ray = ProductionClearanceRayEvidence(
        sample_x=0.0,
        sample_y=0.0,
        hit=True,
        distance_m=1.25,
        object_name="UnregisteredMesh",
    )

    assert ray.stable_id is None
    assert ray.part_id is None
    assert ray.semantic_id is None


def test_evidence_canonical_bytes_and_sha_are_cross_process_stable() -> None:
    evidence = _evidence_with_hits(
        camera_id="camera-ground-route-001",
        hits={(0.0, 0.0): 1.25},
    )
    payload = canonical_production_camera_clearance_evidence_bytes(evidence)
    script = "\n".join(
        (
            "import sys",
            "from pipeline.synthetic_village.production_preflight import "
            "ProductionCameraClearanceEvidence, "
            "production_camera_clearance_evidence_sha256",
            "row = ProductionCameraClearanceEvidence.model_validate_json("
            "sys.stdin.buffer.read())",
            "print(production_camera_clearance_evidence_sha256(row))",
        ),
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=payload,
        check=True,
        capture_output=True,
    )

    assert completed.stdout.decode("utf-8").strip() == (
        production_camera_clearance_evidence_sha256(evidence)
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "near_distance_m": 0.0,
            "minimum_upper_middle_near_hit_count": 5,
        },
        {
            "near_distance_m": 2.0,
            "minimum_upper_middle_near_hit_count": 0,
        },
        {
            "near_distance_m": 2.0,
            "minimum_upper_middle_near_hit_count": 16,
        },
    ],
)
def test_malformed_operator_policy_fails_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProductionClearancePolicy(**kwargs)
