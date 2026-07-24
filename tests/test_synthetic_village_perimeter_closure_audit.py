"""TDD contract for the exact-266 sixteen-camera closure audit."""

from __future__ import annotations

import copy
import hashlib
import math
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import perimeter_closure_module as closure_module
from pipeline.synthetic_village.perimeter_closure_audit import (
    PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER,
    PerimeterClosureAuditPlan,
    build_perimeter_closure_audit_plan,
    canonical_perimeter_closure_audit_plan_bytes,
    perimeter_closure_audit_plan_sha256,
    verify_perimeter_closure_audit_plan,
)
from pipeline.synthetic_village.perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    PerimeterClosurePlan,
    build_default_perimeter_closure_plan,
    perimeter_closure_plan_sha256,
)


def _batch24_manifest() -> dict[str, Any]:
    assets = []
    for sector, sources in closure_module._BATCH24_SOURCES.items():
        for kind, (file_name, sha256) in sources.items():
            assets.append(
                {
                    "file": file_name,
                    "kind": kind,
                    "sector": sector,
                    "sha256": sha256,
                }
            )
    return {
        "schema_version": 1,
        "batch_id": closure_module.BATCH24_BATCH_ID,
        "asset_count": 16,
        "prompt_count": 16,
        "trust": {
            "synthetic": True,
            "stage": "design-only",
            "camera_calibration": "unknown",
            "geometry_consistency": "not-verified",
            "metric_scale": "unknown",
            "real_photo_texture": False,
            "training_use": "forbidden-as-multiview",
            "coverage_use": "forbidden",
            "trust_effect": "none",
        },
        "assets": assets,
    }


@pytest.fixture
def closure_plan() -> PerimeterClosurePlan:
    return build_default_perimeter_closure_plan(
        batch24_manifest=_batch24_manifest(),
        batch24_manifest_sha256="a" * 64,
        production_plan_sha256="b" * 64,
        topology_plan_sha256="c" * 64,
        terrain_height_at=lambda x, y: round(0.01 * x - 0.005 * y, 3),
    )


def _audit_terrain(x_m: float, y_m: float) -> float:
    return round(7.0 + 0.02 * x_m + 0.005 * y_m, 3)


def _build(closure_plan: PerimeterClosurePlan) -> PerimeterClosureAuditPlan:
    return build_perimeter_closure_audit_plan(
        perimeter_closure_plan=closure_plan,
        exact_build_id="d" * 64,
        exact_build_report_sha256="e" * 64,
        exact_blend_sha256="f" * 64,
        object_registry_sha256="1" * 64,
        terrain_height_at=_audit_terrain,
    )


def _payload(plan: PerimeterClosureAuditPlan) -> dict[str, Any]:
    return copy.deepcopy(plan.model_dump(mode="python"))


def test_plan_materializes_exact_sixteen_bidirectional_cameras(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)

    assert len(plan.cameras) == 16
    assert tuple(camera.audit_camera_id for camera in plan.cameras) == (
        PERIMETER_CLOSURE_AUDIT_CAMERA_ORDER
    )
    assert {camera.direction for camera in plan.cameras} == {
        "inward",
        "outward",
    }
    assert all(camera.eye_height_m == 1.6 for camera in plan.cameras)
    assert all(
        camera.source_plan_sha256
        == perimeter_closure_plan_sha256(closure_plan)
        for camera in plan.cameras
    )

    for module_index, module in enumerate(closure_plan.modules):
        inward, outward = plan.cameras[module_index * 2 : module_index * 2 + 2]
        inner_ground = _audit_terrain(
            module.inner_anchor_m[0],
            module.inner_anchor_m[1],
        )
        outer_ground = _audit_terrain(
            module.outer_anchor_m[0],
            module.outer_anchor_m[1],
        )
        assert inward.position_m == (
            module.outer_anchor_m[0],
            module.outer_anchor_m[1],
            round(outer_ground + 1.6, 3),
        )
        assert inward.look_at_m == (
            module.inner_anchor_m[0],
            module.inner_anchor_m[1],
            round(inner_ground + 1.6, 3),
        )
        assert outward.position_m == inward.look_at_m
        assert outward.look_at_m == inward.position_m
        assert inward.position_terrain_z_m == outer_ground
        assert outward.position_terrain_z_m == inner_ground


def test_plan_binds_current_targets_and_neighbor_seams(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    seam_ids = tuple(module.parts[4].instance_id for module in closure_plan.modules)

    for module_index, module in enumerate(closure_plan.modules):
        expected_targets = tuple(part.instance_id for part in module.parts)
        expected_seams = (
            seam_ids[module_index],
            seam_ids[(module_index + 1) % len(seam_ids)],
        )
        for camera in plan.cameras[module_index * 2 : module_index * 2 + 2]:
            assert camera.required_target_instance_ids == expected_targets
            assert camera.required_seam_instance_ids == expected_seams


def test_plan_is_canonical_and_content_addressed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    plan = _build(closure_plan)
    payload = canonical_perimeter_closure_audit_plan_bytes(plan)

    assert payload.endswith(b"\n")
    assert perimeter_closure_audit_plan_sha256(plan) == hashlib.sha256(
        payload
    ).hexdigest()
    verify_perimeter_closure_audit_plan(
        plan,
        perimeter_closure_plan=closure_plan,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("exact_build_id", "2" * 64, "build"),
        ("exact_build_report_sha256", "3" * 64, "report"),
        ("exact_blend_sha256", "4" * 64, "blend"),
        ("object_registry_sha256", "5" * 64, "registry"),
    ),
)
def test_external_expected_identity_drift_fails_closed(
    closure_plan: PerimeterClosurePlan,
    field: str,
    value: str,
    message: str,
) -> None:
    plan = _build(closure_plan)
    expected = {
        "exact_build_id": plan.exact_build_id,
        "exact_build_report_sha256": plan.exact_build_report_sha256,
        "exact_blend_sha256": plan.exact_blend_sha256,
        "object_registry_sha256": plan.object_registry_sha256,
    }
    expected[field] = value

    with pytest.raises(ValueError, match=message):
        verify_perimeter_closure_audit_plan(
            plan,
            perimeter_closure_plan=closure_plan,
            **expected,
        )


def test_duplicate_or_reordered_camera_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    cameras = list(payload["cameras"])
    cameras[1] = copy.deepcopy(cameras[0])
    payload["cameras"] = tuple(cameras)

    with pytest.raises(ValidationError, match="camera"):
        PerimeterClosureAuditPlan.model_validate(payload)


def test_same_position_reversal_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    payload["cameras"][1]["position_m"] = copy.deepcopy(
        payload["cameras"][0]["position_m"]
    )
    payload["cameras"][1]["position_terrain_z_m"] = payload["cameras"][0][
        "position_terrain_z_m"
    ]

    with pytest.raises(ValidationError, match="anchor|position|pair"):
        PerimeterClosureAuditPlan.model_validate(payload)


def test_floating_camera_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    position = payload["cameras"][0]["position_m"]
    payload["cameras"][0]["position_m"] = (
        position[0],
        position[1],
        position[2] + 0.5,
    )

    with pytest.raises(ValidationError, match="eye height|terrain"):
        PerimeterClosureAuditPlan.model_validate(payload)


@pytest.mark.parametrize("bad_height", (math.nan, math.inf, -math.inf))
def test_non_finite_terrain_sample_fails_closed(
    closure_plan: PerimeterClosurePlan,
    bad_height: float,
) -> None:
    with pytest.raises(ValueError, match="terrain"):
        build_perimeter_closure_audit_plan(
            perimeter_closure_plan=closure_plan,
            exact_build_id="d" * 64,
            exact_build_report_sha256="e" * 64,
            exact_blend_sha256="f" * 64,
            object_registry_sha256="1" * 64,
            terrain_height_at=lambda _x, _y: bad_height,
        )


def test_promoted_trust_fails_closed(
    closure_plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(_build(closure_plan))
    payload["geometry_usability"] = "metric-aligned"

    with pytest.raises(ValidationError):
        PerimeterClosureAuditPlan.model_validate(payload)


def test_module_order_remains_the_canonical_eight() -> None:
    assert PERIMETER_CLOSURE_MODULE_ORDER == (
        "closure-upstream",
        "closure-northeast",
        "closure-east",
        "closure-southeast",
        "closure-downstream",
        "closure-southwest",
        "closure-west",
        "closure-northwest",
    )
