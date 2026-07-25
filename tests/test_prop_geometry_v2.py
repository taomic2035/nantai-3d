"""RED tests for the Batch35 canonical prop-geometry v2 plan (HANDOFF-IMAGE2-040).

These tests fix the schema before any Blender geometry is added. The pure
model defines one canonical part graph per prop slot, fail-closed against
duplicate/unknown part ids, non-finite transforms, zero-volume parts,
dimension-envelope overflow, floating required supports, forbidden
interpenetration, missing collision proxies, wrong tool/wheel counts and
source-SHA mismatch.

Trust boundary (must be Literal-locked):
  synthetic          = true
  stage              = design-only
  geometry_consistency = not-verified
  real_photo_texture = false
  training_use       = forbidden-as-multiview
  coverage_use       = forbidden
  clearance_use      = forbidden-as-evidence
  trust_effect       = none

Source SHAs come from FEEDBACK-IMAGE2-040 `Accepted modeling inputs`.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.prop_geometry_v2 import (
    BATCH35_SOURCE_SHAS,
    PROP_SLOTS_V2,
    PropGeometryV2Error,
    PropGeometryV2Plan,
    PropPart,
    PropSlotPlan,
    build_prop_geometry_v2_plan,
    serialize_prop_geometry_v2_plan,
)

# Eight slot ids exactly as listed in FEEDBACK-IMAGE2-040.
EXPECTED_SLOT_IDS = (
    "prop-water-jar-01",
    "prop-firewood-stack-01",
    "prop-bamboo-basket-01",
    "prop-wooden-bench-01",
    "prop-farming-tools-01",
    "prop-grain-rack-01",
    "prop-stone-trough-01",
    "prop-handcart-01",
)

# One source SHA per slot, locked at import time so a wrong/stale handoff
# cannot silently consume a different image.
EXPECTED_SOURCE_SHAS = {
    "prop-water-jar-01": "30caa127934742e64889ea2dc5055b4c34a72174736ea999cc26e057d60149c6",
    "prop-firewood-stack-01": "1106dd3682b944e8806174a7b277eccf598daccf7b562c8cb3ec3e468ec98b71",
    "prop-bamboo-basket-01": "92a09118cb5d33e703979fa998940b2dc4a23f9fea0140a86dc7b98a96c2dd8b",
    "prop-wooden-bench-01": "328748cf38d12abc6aedab557162233e8e0e006e5f8ec2907be71ba634153a3a",
    "prop-farming-tools-01": "075cf4d252e39d374c012e13b24d8e365a2bedfbc16abaca18f0ba0a62d1e6ad",
    "prop-grain-rack-01": "e5bebc0502bacdc74ef2b1941bce6a829342554479c9a0db895867f6c0345745",
    "prop-stone-trough-01": "0634f7d9ae8287dfecb00f61064f250c4ff6585e09a4f0eea56c1581577422b1",
    "prop-handcart-01": "3f5e83b734cb707e2011804ff33733752f07210e6c05c421dacfb0e232af0e39",
}


# --------------------------------------------------------------------------- #
# Schema-level RED cases (must fail before any Blender work).
# --------------------------------------------------------------------------- #


def test_batch35_source_shas_match_feedback_image2_040():
    """BATCH35_SOURCE_SHAS must be exactly the eight SHAs locked in the
    FEEDBACK-IMAGE2-040 table, in the same slot order."""
    assert set(BATCH35_SOURCE_SHAS.keys()) == set(EXPECTED_SLOT_IDS)
    for slot_id, sha in EXPECTED_SOURCE_SHAS.items():
        assert BATCH35_SOURCE_SHAS[slot_id] == sha, (
            f"source SHA for {slot_id} disagrees with FEEDBACK-IMAGE2-040"
        )


def test_prop_slots_v2_covers_exactly_eight_slots():
    assert set(PROP_SLOTS_V2.keys()) == set(EXPECTED_SLOT_IDS)


def test_prop_part_rejects_non_finite_transforms():
    with pytest.raises((ValidationError, PropGeometryV2Error)):
        PropPart(
            part_id="water-jar-body",
            local_transform_m=(float("nan"), 0.0, 0.0),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(0.96, 0.96, 1.05),
            support_role="grounded",
        )


def test_prop_part_rejects_non_finite_rotations():
    with pytest.raises((ValidationError, PropGeometryV2Error)):
        PropPart(
            part_id="water-jar-body",
            local_transform_m=(0.0, 0.0, 0.5),
            local_rotation_euler_deg=(0.0, float("inf"), 0.0),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(0.96, 0.96, 1.0),
            support_role="grounded",
        )


def test_prop_part_rejects_zero_volume():
    with pytest.raises((ValidationError, PropGeometryV2Error)):
        PropPart(
            part_id="water-jar-body",
            local_transform_m=(0.0, 0.0, 0.0),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(0.0, 0.0, 0.0),
            support_role="grounded",
        )


def test_prop_part_rejects_unknown_collision_role():
    with pytest.raises((ValidationError, PropGeometryV2Error)):
        PropPart(
            part_id="x",
            local_transform_m=(0.0, 0.0, 0.0),
            material_slot_id="material-clay-brick-01",
            collision_role="phantom",
            render_bounds_m=(1.0, 1.0, 1.0),
            support_role="grounded",
        )


def test_prop_part_rejects_unknown_support_role():
    with pytest.raises((ValidationError, PropGeometryV2Error)):
        PropPart(
            part_id="x",
            local_transform_m=(0.0, 0.0, 0.0),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(1.0, 1.0, 1.0),
            support_role="floating-no-anchor",
        )


# --------------------------------------------------------------------------- #
# Plan-level RED cases.
# --------------------------------------------------------------------------- #


def test_plan_rejects_duplicate_part_ids():
    part_a = PropPart(
        part_id="dup",
        local_transform_m=(0.0, 0.0, 0.0),
        material_slot_id="material-clay-brick-01",
        collision_role="solid",
        render_bounds_m=(0.5, 0.5, 0.5),
        support_role="grounded",
    )
    part_b = PropPart(
        part_id="dup",
        local_transform_m=(1.0, 0.0, 0.0),
        material_slot_id="material-clay-brick-01",
        collision_role="solid",
        render_bounds_m=(0.5, 0.5, 0.5),
        support_role="grounded",
    )
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-water-jar-01",
            source_image_sha256=BATCH35_SOURCE_SHAS["prop-water-jar-01"],
            parts=(part_a, part_b),
            envelope_m=(1.5, 1.5, 1.5),
        )


def test_plan_rejects_floating_required_supports():
    """A part marked as `support_role='grounded'` must sit on or below the
    envelope floor; a floating grounded part is a contradiction."""
    floating = PropPart(
        part_id="floating-leg",
        local_transform_m=(0.0, 0.0, 5.0),
        material_slot_id="material-weathered-timber-01",
        collision_role="solid",
        render_bounds_m=(0.1, 0.1, 0.1),
        support_role="grounded",
    )
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-wooden-bench-01",
            source_image_sha256=BATCH35_SOURCE_SHAS["prop-wooden-bench-01"],
            parts=(floating,),
            envelope_m=(1.5, 0.5, 0.5),
        )


def test_plan_rejects_part_envelope_overflow():
    """Any part whose render bounds exceed the slot envelope must fail."""
    too_big = PropPart(
        part_id="oversize",
        local_transform_m=(0.0, 0.0, 0.0),
        material_slot_id="material-weathered-timber-01",
        collision_role="solid",
        render_bounds_m=(5.0, 5.0, 5.0),
        support_role="grounded",
    )
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-wooden-bench-01",
            source_image_sha256=BATCH35_SOURCE_SHAS["prop-wooden-bench-01"],
            parts=(too_big,),
            envelope_m=(1.5, 0.5, 0.5),
        )


def test_plan_rejects_undeclared_interpenetration():
    canonical = PROP_SLOTS_V2["prop-water-jar-01"]
    intruder = PropPart(
        part_id="water-jar-intruder",
        local_transform_m=(0.0, 0.0, 0.5),
        material_slot_id="material-clay-brick-01",
        collision_role="solid",
        render_bounds_m=(0.20, 0.20, 0.20),
        support_role="rest",
    )
    with pytest.raises(PropGeometryV2Error, match="unexpected"):
        PropSlotPlan(
            slot_id=canonical.slot_id,
            source_image_sha256=canonical.source_image_sha256,
            parts=(*canonical.parts, intruder),
            envelope_m=canonical.envelope_m,
            allowed_intersections=canonical.allowed_intersections,
        )


def test_plan_rejects_stale_intersection_declaration():
    canonical = PROP_SLOTS_V2["prop-water-jar-01"]
    with pytest.raises(PropGeometryV2Error, match="unknown parts"):
        PropSlotPlan(
            slot_id=canonical.slot_id,
            source_image_sha256=canonical.source_image_sha256,
            parts=canonical.parts,
            envelope_m=canonical.envelope_m,
            allowed_intersections=(
                *canonical.allowed_intersections,
                ("water-jar-body", "removed-part"),
            ),
        )


def test_plan_rejects_unknown_slot_id():
    part = PropPart(
        part_id="x",
        local_transform_m=(0.0, 0.0, 0.0),
        material_slot_id="material-clay-brick-01",
        collision_role="solid",
        render_bounds_m=(0.5, 0.5, 0.5),
        support_role="grounded",
    )
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-not-in-batch35-01",
            source_image_sha256="0" * 64,
            parts=(part,),
            envelope_m=(1.0, 1.0, 1.0),
        )


def test_plan_rejects_wrong_source_sha():
    """The plan's source_image_sha256 must match the slot's locked SHA."""
    part = PropPart(
        part_id="water-jar-body",
        local_transform_m=(0.0, 0.0, 0.0),
        material_slot_id="material-clay-brick-01",
        collision_role="solid",
        render_bounds_m=(0.5, 0.5, 0.5),
        support_role="grounded",
    )
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-water-jar-01",
            source_image_sha256="a" * 64,
            parts=(part,),
            envelope_m=(1.0, 1.0, 1.0),
        )


def test_plan_rejects_missing_collision_proxy_when_required():
    """A solid collision role requires a non-empty collision_proxy_sha256
    when the plan is finalized for build."""
    plan = PROP_SLOTS_V2["prop-water-jar-01"]
    with pytest.raises(PropGeometryV2Error):
        plan.finalize_for_build(collision_proxy_sha256="")


def test_plan_rejects_non_hex_collision_proxy_sha():
    plan = PROP_SLOTS_V2["prop-water-jar-01"]
    with pytest.raises(PropGeometryV2Error):
        plan.finalize_for_build(collision_proxy_sha256="g" * 64)


def test_farming_tools_requires_at_least_four_distinct_tool_heads():
    """FEEDBACK-IMAGE2-040 §4: cover at minimum four distinct tool
    heads/handles plus rest. The plan must reject fewer than four."""
    parts = tuple(
        PropPart(
            part_id=f"tool-{i}",
            local_transform_m=(float(i), 0.0, 0.0),
            material_slot_id="material-aged-metal-01",
            collision_role="solid",
            render_bounds_m=(0.1, 0.1, 0.5),
            support_role="rest",
        )
        for i in range(3)
    )
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-farming-tools-01",
            source_image_sha256=BATCH35_SOURCE_SHAS["prop-farming-tools-01"],
            parts=parts,
            envelope_m=(1.0, 0.5, 0.5),
        )


def test_handcart_requires_exactly_two_wheels():
    """FEEDBACK-IMAGE2-040 §4: cart bed/two spoked wheels/axle/handles/
    braces/rests. Wrong wheel count must fail closed."""

    def wheel(part_id: str, x: float) -> PropPart:
        return PropPart(
            part_id=part_id,
            local_transform_m=(x, 0.0, 0.0),
            material_slot_id="material-aged-metal-01",
            collision_role="solid",
            render_bounds_m=(0.4, 0.1, 0.4),
            support_role="grounded",
            semantic_kind="wheel",
        )

    bed = PropPart(
        part_id="cart-bed",
        local_transform_m=(0.0, 0.0, 0.5),
        material_slot_id="material-weathered-timber-01",
        collision_role="solid",
        render_bounds_m=(1.2, 0.6, 0.15),
        support_role="rest",
        semantic_kind="bed",
    )
    # One wheel only — must fail.
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-handcart-01",
            source_image_sha256=BATCH35_SOURCE_SHAS["prop-handcart-01"],
            parts=(bed, wheel("wheel-1", -0.5)),
            envelope_m=(1.5, 0.7, 0.8),
        )
    # Three wheels — must fail.
    with pytest.raises(PropGeometryV2Error):
        PropSlotPlan(
            slot_id="prop-handcart-01",
            source_image_sha256=BATCH35_SOURCE_SHAS["prop-handcart-01"],
            parts=(
                bed,
                wheel("wheel-1", -0.5),
                wheel("wheel-2", 0.5),
                wheel("wheel-3", 0.0),
            ),
            envelope_m=(1.5, 0.7, 0.8),
        )


def test_handcart_accepts_exactly_two_wheels():
    """A canonical two-wheel handcart must be accepted."""
    plan = PROP_SLOTS_V2["prop-handcart-01"]
    assert plan.slot_id == "prop-handcart-01"
    assert sum(part.semantic_kind == "wheel" for part in plan.parts) == 2


def test_water_jar_minimum_part_coverage():
    """FEEDBACK-IMAGE2-040 §4: jar body/rim/opening/foot.
    The canonical plan must include at least those four semantic kinds."""
    plan = PROP_SLOTS_V2["prop-water-jar-01"]
    kinds = {p.semantic_kind for p in plan.parts}
    for required in ("body", "rim", "opening", "foot"):
        assert required in kinds, (
            f"water-jar plan missing semantic_kind={required}; "
            f"got {sorted(kinds)}"
        )


def test_bench_is_backless():
    """FEEDBACK-IMAGE2-040 §3: freeze one explicit part graph where panels
    disagree. The bench must be backless per its slot description, not the
    current `_build_prop` back-like slab."""
    plan = PROP_SLOTS_V2["prop-wooden-bench-01"]
    kinds = {p.semantic_kind for p in plan.parts}
    assert "back" not in kinds, "bench must be backless; found a 'back' part"
    for required in ("seat", "legs", "braces"):
        assert required in kinds, (
            f"bench plan missing semantic_kind={required}; "
            f"got {sorted(kinds)}"
        )


def test_canonical_plans_cover_handoff_minimum_semantics():
    required = {
        "prop-water-jar-01": {"body", "rim", "opening", "foot"},
        "prop-firewood-stack-01": {"frame", "log"},
        "prop-bamboo-basket-01": {
            "body", "rim", "handle", "opening", "base",
        },
        "prop-wooden-bench-01": {"seat", "legs", "braces", "pegs"},
        "prop-farming-tools-01": {"tool-head", "handle", "rest"},
        "prop-grain-rack-01": {"frame", "rail", "brace", "shelf"},
        "prop-stone-trough-01": {"basin", "wall", "notch", "feet"},
        "prop-handcart-01": {
            "bed", "wheel", "spoke", "axle", "handle", "brace", "rest",
        },
    }
    for slot_id, required_kinds in required.items():
        present = {
            part.semantic_kind
            for part in PROP_SLOTS_V2[slot_id].parts
        }
        assert required_kinds <= present

    assert sum(
        part.semantic_kind == "brace"
        for part in PROP_SLOTS_V2["prop-grain-rack-01"].parts
    ) >= 4
    assert sum(
        part.semantic_kind == "spoke"
        for part in PROP_SLOTS_V2["prop-handcart-01"].parts
    ) >= 8


# --------------------------------------------------------------------------- #
# Canonical plan + serialization.
# --------------------------------------------------------------------------- #


def test_build_prop_geometry_v2_plan_returns_all_eight_slots():
    plan = build_prop_geometry_v2_plan()
    assert set(plan.slot_plans.keys()) == set(EXPECTED_SLOT_IDS)


def test_top_level_plan_rejects_key_slot_id_mismatch():
    swapped = dict(PROP_SLOTS_V2)
    left, right = EXPECTED_SLOT_IDS[:2]
    swapped[left], swapped[right] = swapped[right], swapped[left]
    with pytest.raises(PropGeometryV2Error, match="keys must match"):
        PropGeometryV2Plan(slot_plans=swapped)


def test_top_level_schema_version_is_literal_locked():
    with pytest.raises(ValidationError):
        PropGeometryV2Plan(
            schema_version="nantai.synthetic-village.prop-geometry-v2.v999",
            slot_plans=PROP_SLOTS_V2,
        )


def test_serialize_prop_geometry_v2_plan_round_trips():
    plan = build_prop_geometry_v2_plan()
    blob = serialize_prop_geometry_v2_plan(plan)
    assert isinstance(blob, bytes)
    # Canonical LF JSON must end with a newline.
    assert blob.endswith(b"\n")
    # Round-trip: the schema_version + slot count must survive.
    import json
    parsed = json.loads(blob.decode("utf-8"))
    assert parsed["schema_version"].startswith(
        "nantai.synthetic-village.prop-geometry-v2."
    )
    assert len(parsed["slot_plans"]) == 8


def test_trust_fields_are_literal_locked():
    """Trust fields must be Literal-locked to the design-only family and
    must not be settable to promoted values."""
    plan = build_prop_geometry_v2_plan()
    assert plan.synthetic is True
    assert plan.stage == "design-only"
    assert plan.camera_calibration == "unknown"
    assert plan.geometry_consistency == "not-verified"
    assert plan.metric_scale == "unknown"
    assert plan.real_photo_texture is False
    assert plan.training_use == "forbidden-as-multiview"
    assert plan.coverage_use == "forbidden"
    assert plan.clearance_use == "forbidden-as-evidence"
    assert plan.trust_effect == "none"
