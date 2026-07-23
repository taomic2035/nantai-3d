"""Batch 22 content-addressed local waterwheel orbit plan."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.elevated_topology import build_elevated_topology_plan
from pipeline.synthetic_village.environment_module import (
    build_default_environment_module_plan,
    environment_module_plan_sha256,
)
from pipeline.synthetic_village.local_orbit_audit import (
    LocalOrbitAuditPlan,
    LocalOrbitPlanError,
    build_waterwheel_local_orbit_plan,
    canonical_local_orbit_plan_bytes,
    load_local_orbit_plan,
    local_orbit_plan_sha256,
    materialize_local_orbit_render_plan,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
    canonical_production_plan_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _inputs():
    scene = build_scene_plan()
    topology = build_elevated_topology_plan(scene)
    source_plan = build_production_camera_plan(scene, topology)
    environment_plan = build_default_environment_module_plan(
        scene=scene,
        elevated_topology=topology,
    )
    return source_plan, environment_module_plan_sha256(environment_plan)


def _plan() -> LocalOrbitAuditPlan:
    source_plan, environment_sha = _inputs()
    return build_waterwheel_local_orbit_plan(
        source_plan=source_plan,
        environment_module_plan_sha256=environment_sha,
        exact_build_id="b" * 64,
        exact_blend_sha256="c" * 64,
        anchor_m=(-185.2, -115.0, 43.15),
    )


def test_builds_exact_eight_direction_content_addressed_orbit() -> None:
    plan = _plan()

    assert tuple(row.orbit_camera_id for row in plan.cameras) == (
        "audit-waterwheel-az000",
        "audit-waterwheel-az045",
        "audit-waterwheel-az090",
        "audit-waterwheel-az135",
        "audit-waterwheel-az180",
        "audit-waterwheel-az225",
        "audit-waterwheel-az270",
        "audit-waterwheel-az315",
    )
    assert tuple(row.azimuth_deg for row in plan.cameras) == tuple(range(0, 360, 45))
    assert all(row.radius_m == 20.0 for row in plan.cameras)
    assert all(row.position_m[2] == pytest.approx(44.75) for row in plan.cameras)
    assert canonical_local_orbit_plan_bytes(plan).endswith(b"\n")
    assert local_orbit_plan_sha256(plan) == hashlib.sha256(
        canonical_local_orbit_plan_bytes(plan),
    ).hexdigest()
    assert plan.synthetic is True
    assert plan.verification_level == "L0"
    assert plan.geometry_usability == "preview-only"
    assert plan.training_use == "forbidden-as-multiview"
    assert plan.trust_effect == "none-quality-filter-only"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_production_plan_sha256", "not-a-sha"),
        ("environment_module_plan_sha256", "z" * 64),
        ("exact_build_id", "1" * 63),
        ("exact_blend_sha256", "A" * 64),
        ("anchor_m", (float("nan"), -115.0, 43.15)),
        ("synthetic", False),
        ("verification_level", "L2"),
        ("geometry_usability", "metric-aligned"),
        ("training_use", "allowed"),
        ("trust_effect", "measured"),
    ],
)
def test_plan_rejects_invalid_identity_or_trust_claim(field: str, value: object) -> None:
    payload = _plan().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError):
        LocalOrbitAuditPlan.model_validate_json(json.dumps(payload))


def test_plan_rejects_duplicate_or_reordered_azimuth() -> None:
    payload = _plan().model_dump(mode="json")
    payload["cameras"][1]["azimuth_deg"] = 0

    with pytest.raises(ValidationError, match="ordered azimuth"):
        LocalOrbitAuditPlan.model_validate_json(json.dumps(payload))


def test_plan_rejects_position_not_derived_from_anchor() -> None:
    payload = _plan().model_dump(mode="json")
    payload["cameras"][3]["position_m"][0] += 0.25

    with pytest.raises(ValidationError, match="derived from anchor"):
        LocalOrbitAuditPlan.model_validate_json(json.dumps(payload))


def test_materialized_plan_replaces_only_audit_group_and_preserves_source() -> None:
    source, _ = _inputs()
    source_bytes = canonical_production_plan_bytes(source)
    orbit = _plan()

    derived = materialize_local_orbit_render_plan(source, orbit)

    assert canonical_production_plan_bytes(source) == source_bytes
    assert derived.camera_count == 180
    assert derived.complete is True
    source_non_audit = tuple(
        camera for camera in source.cameras if camera.group_id != "audit-overview"
    )
    derived_non_audit = tuple(
        camera for camera in derived.cameras if camera.group_id != "audit-overview"
    )
    assert derived_non_audit == source_non_audit
    audit = tuple(camera for camera in derived.cameras if camera.group_id == "audit-overview")
    assert len(audit) == 12
    assert tuple(camera.camera_id for camera in audit[:8]) == tuple(
        row.materialized_camera_id for row in orbit.cameras
    )
    for camera, row in zip(audit[:8], orbit.cameras, strict=True):
        assert camera.position_m == pytest.approx(row.position_m, abs=1e-3)
    expectation = {
        row.group_id: row.expected_dominant_semantic
        for row in derived.post_render_quality_expectation.group_expectations
    }
    assert expectation["audit-overview"] == "mixed"
    assert all(camera.audit_only for camera in audit)
    assert all("modeled-scene" in camera.disclosure for camera in audit)


def test_builder_binds_exact_source_plan_digest() -> None:
    source, environment_sha = _inputs()
    plan = build_waterwheel_local_orbit_plan(
        source_plan=source,
        environment_module_plan_sha256=environment_sha,
        exact_build_id="b" * 64,
        exact_blend_sha256="c" * 64,
        anchor_m=(-185.2, -115.0, 43.15),
    )

    assert plan.source_production_plan_sha256 == hashlib.sha256(
        canonical_production_plan_bytes(source),
    ).hexdigest()


def test_loader_requires_exact_canonical_plan_bytes(tmp_path: Path) -> None:
    plan = _plan()
    path = tmp_path / "local-orbit-plan.json"
    path.write_bytes(canonical_local_orbit_plan_bytes(plan))

    assert load_local_orbit_plan(path) == plan
    path.write_text(
        json.dumps(plan.model_dump(mode="json")),
        encoding="utf-8",
    )
    with pytest.raises(LocalOrbitPlanError, match="canonical"):
        load_local_orbit_plan(path)
