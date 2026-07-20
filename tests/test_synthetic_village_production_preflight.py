"""Production-camera clearance evidence is raw, canonical, and policy-scoped."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.production_preflight import (
    ProductionCameraClearanceEvidence,
    ProductionClearancePolicy,
    ProductionClearanceRayEvidence,
    ProductionClearanceReport,
    ProductionClearanceRequest,
    ProductionPreflightError,
    build_production_clearance_report,
    build_production_clearance_request,
    canonical_production_camera_clearance_evidence_bytes,
    canonical_production_clearance_report_bytes,
    canonical_production_clearance_request_bytes,
    evaluate_production_camera_clearance,
    parse_production_clearance_report_bytes,
    production_camera_clearance_evidence_sha256,
    production_clearance_policy_sha256,
    production_clearance_preflight_id,
    production_clearance_request_sha256,
    verify_production_clearance_report,
)
from tests.test_synthetic_village_production_render import _request


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
                stable_id=(
                    "lower-bridge"
                    if (sample_x, sample_y) in hits
                    else None
                ),
                part_id="deck" if (sample_x, sample_y) in hits else None,
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


def test_hit_registry_identity_uses_scene_string_ids() -> None:
    ray = ProductionClearanceRayEvidence(
        sample_x=0.0,
        sample_y=0.0,
        hit=True,
        distance_m=1.25,
        object_name="nv__lower-bridge__deck",
        stable_id="lower-bridge",
        part_id="deck",
        semantic_id=4,
    )

    assert ray.stable_id == "lower-bridge"
    assert ray.part_id == "deck"
    with pytest.raises(ValidationError):
        ProductionClearanceRayEvidence(
            sample_x=0.0,
            sample_y=0.0,
            hit=True,
            distance_m=1.25,
            stable_id=10,
            part_id=2,
        )


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


def _clearance_request() -> ProductionClearanceRequest:
    render_request = _request()
    return build_production_clearance_request(
        plan=render_request.production_plan,
        selected_camera_ids=(
            "camera-ground-route-010",
            "camera-ground-route-034",
            "camera-ground-route-039",
        ),
        build_id=render_request.build_id,
        blender_executable_sha256=render_request.blender_executable_sha256,
        preflight_script_sha256="6" * 64,
        blend_sha256=render_request.blend_sha256,
        build_report_sha256=render_request.build_report_sha256,
        object_registry=render_request.object_registry,
        auxiliary_registry=render_request.auxiliary_registry,
        semantic_registry=render_request.semantic_registry,
        policy=ProductionClearancePolicy(
            near_distance_m=2.0,
            minimum_upper_middle_near_hit_count=5,
        ),
    )


def test_clearance_request_binds_every_scene_and_policy_identity() -> None:
    request = _clearance_request()

    assert request.production_plan_sha256 == (
        _request().production_plan_sha256
    )
    assert request.camera_registry_sha256 == (
        _request().camera_registry_sha256
    )
    assert request.object_registry_sha256 == _request().object_registry_sha256
    assert request.policy_sha256 == production_clearance_policy_sha256(
        request.policy,
    )
    assert request.preflight_id == production_clearance_preflight_id(request)
    assert production_clearance_request_sha256(request) == (
        production_clearance_request_sha256(request)
    )
    assert request.synthetic is True
    assert request.geometry_trust == "simplified-pbr-not-render-parity"
    assert request.trust_effect == "none-quality-filter-only"


@pytest.mark.parametrize(
    "field",
    [
        "production_plan_sha256",
        "camera_registry_sha256",
        "object_registry_sha256",
        "policy_sha256",
        "blend_sha256",
    ],
)
def test_clearance_request_rejects_readdressed_inputs(field: str) -> None:
    request = _clearance_request()
    payload = json.loads(canonical_production_clearance_request_bytes(request))
    payload[field] = "f" * 64

    with pytest.raises(ValidationError):
        ProductionClearanceRequest.model_validate_json(json.dumps(payload))


def test_clearance_request_requires_unique_plan_ordered_camera_subset() -> None:
    request = _clearance_request()
    payload = json.loads(canonical_production_clearance_request_bytes(request))
    payload["selected_camera_ids"] = [
        "camera-ground-route-039",
        "camera-ground-route-010",
        "camera-ground-route-010",
    ]

    with pytest.raises(ValidationError, match="unique plan-ordered subset"):
        ProductionClearanceRequest.model_validate_json(json.dumps(payload))


def test_report_recomputes_every_decision_from_bound_raw_evidence() -> None:
    request = _clearance_request()
    evidence = tuple(
        _evidence_with_hits(
            camera_id=camera_id,
            hits=(
                {
                    (sample_x, sample_y): 0.5
                    for sample_x in request.policy.sample_grid
                    for sample_y in (0.0, 0.45, 0.9)
                }
                if camera_id == "camera-ground-route-010"
                else {}
            ),
        )
        for camera_id in request.selected_camera_ids
    )
    report = build_production_clearance_report(request, evidence=evidence)

    verify_production_clearance_report(report, request=request)

    assert report.request_sha256 == production_clearance_request_sha256(request)
    assert tuple(row.camera_id for row in report.decisions) == (
        request.selected_camera_ids
    )
    assert report.decisions[0].passes is False
    assert report.decisions[1].passes is True
    assert report.decisions[2].passes is True


def test_report_fails_closed_on_missing_camera_or_fabricated_decision() -> None:
    request = _clearance_request()
    evidence = tuple(
        _evidence_with_hits(camera_id=camera_id, hits={})
        for camera_id in request.selected_camera_ids
    )
    report = build_production_clearance_report(request, evidence=evidence)
    missing = report.model_copy(
        update={
            "evidence": report.evidence[:-1],
            "decisions": report.decisions[:-1],
        },
    )
    fabricated = report.model_copy(
        update={
            "decisions": (
                report.decisions[0].model_copy(
                    update={
                        "passes": False,
                        "failed_rule_ids": ("upper-middle-near-hit-count",),
                    },
                ),
                *report.decisions[1:],
            ),
        },
    )

    with pytest.raises(ProductionPreflightError, match="camera set"):
        verify_production_clearance_report(missing, request=request)
    with pytest.raises(ProductionPreflightError, match="decision"):
        verify_production_clearance_report(fabricated, request=request)


def test_report_rejects_request_or_runtime_identity_mismatch() -> None:
    request = _clearance_request()
    evidence = tuple(
        _evidence_with_hits(camera_id=camera_id, hits={})
        for camera_id in request.selected_camera_ids
    )
    report = build_production_clearance_report(request, evidence=evidence)

    for field in (
        "request_sha256",
        "build_id",
        "blender_executable_sha256",
        "preflight_script_sha256",
        "blend_sha256",
        "build_report_sha256",
        "object_registry_sha256",
    ):
        altered = report.model_copy(update={field: "f" * 64})
        with pytest.raises(ProductionPreflightError, match="identity"):
            verify_production_clearance_report(altered, request=request)


def test_report_contract_rejects_unknown_fields() -> None:
    request = _clearance_request()
    report = build_production_clearance_report(
        request,
        evidence=tuple(
            _evidence_with_hits(camera_id=camera_id, hits={})
            for camera_id in request.selected_camera_ids
        ),
    )
    payload = report.model_dump(mode="json")
    payload["untrusted_note"] = "looks good"

    with pytest.raises(ValidationError):
        ProductionClearanceReport.model_validate(payload)


def test_report_parser_rejects_duplicate_keys_and_noncanonical_bytes() -> None:
    request = _clearance_request()
    report = build_production_clearance_report(
        request,
        evidence=tuple(
            _evidence_with_hits(camera_id=camera_id, hits={})
            for camera_id in request.selected_camera_ids
        ),
    )
    canonical = canonical_production_clearance_report_bytes(report)

    assert parse_production_clearance_report_bytes(canonical) == report
    with pytest.raises(ProductionPreflightError, match="duplicate JSON key"):
        parse_production_clearance_report_bytes(
            b'{"schema_version":"first","schema_version":"second"}\n',
        )
    with pytest.raises(ProductionPreflightError, match="canonical JSON"):
        parse_production_clearance_report_bytes(
            json.dumps(report.model_dump(mode="json")).encode("utf-8"),
        )
