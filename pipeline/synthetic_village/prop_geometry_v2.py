"""Pure-Python canonical prop-geometry v2 plan (HANDOFF-IMAGE2-040 Batch35).

This module defines a frozen, fail-closed part graph for the eight prop
slots that already exist in
``assets/default-resources/synthetic-mountain-village-visual-slots-v1.json``
and are currently consumed by
``scripts/blender/build_synthetic_village.py::_build_prop`` as coarse
primitive proxies (jar = two cylinders, firewood = twelve cylinders, bench
has a back-like slab despite its backless slot description, etc.).

FEEDBACK-IMAGE2-040 supplied one six-view design board per slot. This
module freezes **one** canonical part graph per slot. Where panels
disagree (e.g. tool counts, firewood log counts, slat counts), the
canonical graph picks a single value rather than averaging or switching
by camera.

Trust boundary (Literal-locked, must never be promoted):

```text
synthetic               = true
stage                   = design-only
camera_calibration      = unknown
geometry_consistency    = not-verified
metric_scale            = unknown
real_photo_texture      = false
training_use            = forbidden-as-multiview
coverage_use            = forbidden
clearance_use           = forbidden-as-evidence
trust_effect            = none
```

Source image SHAs are locked to the FEEDBACK-IMAGE2-040 `Accepted
modeling inputs` table. A wrong/stale handoff cannot silently consume a
different image.

This module is the pure-model half of the producer. It does NOT touch
Blender, does NOT write ``web/data/``, does NOT register defaults and
does NOT claim acceptance. Blender emission, exact-build probes and
post-render v2 come after Codex signs off on this schema.
"""
from __future__ import annotations

import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROP_GEOMETRY_V2_SCHEMA = "nantai.synthetic-village.prop-geometry-v2.v1"
SCHEMA_VERSION = 1

STABLE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
OPTIONAL_SHA256_PATTERN = r"^(?:[0-9a-f]{64})?$"

#: Slot ids that already exist in
#: ``assets/default-resources/synthetic-mountain-village-visual-slots-v1.json``
#: and are consumed by ``_build_prop``. Locked to the eight ids in
#: FEEDBACK-IMAGE2-040's `Accepted modeling inputs` table.
SUPPORTED_SLOT_IDS: frozenset[str] = frozenset({
    "prop-water-jar-01",
    "prop-firewood-stack-01",
    "prop-bamboo-basket-01",
    "prop-wooden-bench-01",
    "prop-farming-tools-01",
    "prop-grain-rack-01",
    "prop-stone-trough-01",
    "prop-handcart-01",
})

#: Source image SHA-256 per slot, locked to FEEDBACK-IMAGE2-040
#: `Accepted modeling inputs`. Changing one of these is a contract break
#: and must produce a new schema version.
BATCH35_SOURCE_SHAS: dict[str, str] = {
    "prop-water-jar-01":
        "30caa127934742e64889ea2dc5055b4c34a72174736ea999cc26e057d60149c6",
    "prop-firewood-stack-01":
        "1106dd3682b944e8806174a7b277eccf598daccf7b562c8cb3ec3e468ec98b71",
    "prop-bamboo-basket-01":
        "92a09118cb5d33e703979fa998940b2dc4a23f9fea0140a86dc7b98a96c2dd8b",
    "prop-wooden-bench-01":
        "328748cf38d12abc6aedab557162233e8e0e006e5f8ec2907be71ba634153a3a",
    "prop-farming-tools-01":
        "075cf4d252e39d374c012e13b24d8e365a2bedfbc16abaca18f0ba0a62d1e6ad",
    "prop-grain-rack-01":
        "e5bebc0502bacdc74ef2b1941bce6a829342554479c9a0db895867f6c0345745",
    "prop-stone-trough-01":
        "0634f7d9ae8287dfecb00f61064f250c4ff6585e09a4f0eea56c1581577422b1",
    "prop-handcart-01":
        "3f5e83b734cb707e2011804ff33733752f07210e6c05c421dacfb0e232af0e39",
}

#: Semantic kinds required per slot per FEEDBACK-IMAGE2-040 §4. The plan
#: builder rejects a slot plan that does not include at least these kinds.
REQUIRED_SEMANTIC_KINDS: dict[str, frozenset[str]] = {
    "prop-water-jar-01": frozenset({"body", "rim", "opening", "foot"}),
    "prop-firewood-stack-01": frozenset({"frame", "log"}),
    "prop-bamboo-basket-01": frozenset({
        "body", "rim", "handle", "opening", "base",
    }),
    "prop-wooden-bench-01": frozenset({
        "seat", "legs", "braces", "pegs",
    }),
    "prop-farming-tools-01": frozenset({"tool-head", "handle", "rest"}),
    "prop-grain-rack-01": frozenset({"frame", "rail", "brace", "shelf"}),
    "prop-stone-trough-01": frozenset({
        "basin", "wall", "notch", "feet",
    }),
    "prop-handcart-01": frozenset({
        "bed", "wheel", "spoke", "axle", "handle", "brace", "rest",
    }),
}

#: Slots that require an exact wheel count (FEEDBACK-IMAGE2-040 §4:
#: "cart bed/two spoked wheels/axle/handles/braces/rests"). Wrong count
#: must fail closed.
EXACT_COUNT_KINDS: dict[str, dict[str, int]] = {
    "prop-handcart-01": {"wheel": 2},
}

#: Slots that require a minimum count per semantic kind (e.g. at least
#: four distinct tool heads/handles for ``prop-farming-tools-01``).
MIN_COUNT_KINDS: dict[str, dict[str, int]] = {
    "prop-farming-tools-01": {"tool-head": 4, "handle": 4},
    "prop-wooden-bench-01": {"pegs": 4},
    "prop-grain-rack-01": {"brace": 4},
    "prop-handcart-01": {"spoke": 8, "brace": 2, "rest": 2},
}


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


_GRAIN_FRAMES = ("grain-rack-frame-left", "grain-rack-frame-right")
_GRAIN_RAILS = (
    "grain-rack-rail-top",
    "grain-rack-rail-mid",
    "grain-rack-rail-bottom",
)
_GRAIN_BRACES = (
    "grain-rack-brace-left",
    "grain-rack-brace-right",
    "grain-rack-brace-left-rear",
    "grain-rack-brace-right-rear",
)
_HANDCART_SPOKES = {
    side: tuple(f"cart-spoke-{side}-{angle:03d}" for angle in (0, 45, 90, 135))
    for side in ("left", "right")
}

# Strictly declared volumetric AABB intersections for canonical structural
# joints. A new or removed overlap changes the observed set and is rejected;
# generated image panels never authorize an undeclared intersection.
CANONICAL_ALLOWED_INTERSECTIONS: dict[
    str,
    frozenset[tuple[str, str]],
] = {
    "prop-water-jar-01": frozenset({
        _pair("water-jar-body", "water-jar-foot"),
    }),
    "prop-firewood-stack-01": frozenset(
        _pair("firewood-frame", f"firewood-log-0-{col}")
        for col in range(4)
    ),
    "prop-bamboo-basket-01": frozenset({
        _pair("bamboo-basket-body", "bamboo-basket-rim"),
        _pair("bamboo-basket-body", "bamboo-basket-base"),
        _pair("bamboo-basket-rim", "bamboo-basket-loop-handle-a"),
        _pair("bamboo-basket-rim", "bamboo-basket-loop-handle-b"),
    }),
    "prop-wooden-bench-01": frozenset({
        *(
            _pair("bench-seat", f"bench-leg-{y}-{x}")
            for x in ("left", "right")
            for y in ("front", "rear")
        ),
        *(
            _pair("bench-seat", f"bench-peg-{x}-{y}")
            for x in ("left", "right")
            for y in ("front", "rear")
        ),
        *(
            _pair(f"bench-leg-{y}-{x}", f"bench-brace-{brace}")
            for x in ("left", "right")
            for y, brace in (("front", "long"), ("rear", "short"))
        ),
        *(
            _pair(f"bench-leg-{y}-{x}", f"bench-peg-{x}-{y}")
            for x in ("left", "right")
            for y in ("front", "rear")
        ),
    }),
    "prop-farming-tools-01": frozenset(
        _pair(f"tool-{name}-head", f"tool-{name}-handle")
        for name in ("hoe", "rake", "sickle", "spade")
    ),
    "prop-grain-rack-01": frozenset({
        *(_pair(frame, rail) for frame in _GRAIN_FRAMES for rail in _GRAIN_RAILS),
        _pair("grain-rack-frame-left", "grain-rack-brace-left"),
        _pair("grain-rack-frame-left", "grain-rack-brace-left-rear"),
        _pair("grain-rack-frame-right", "grain-rack-brace-right"),
        _pair("grain-rack-frame-right", "grain-rack-brace-right-rear"),
        *(_pair(frame, "grain-rack-shelf") for frame in _GRAIN_FRAMES),
        *(_pair(rail, brace) for rail in _GRAIN_RAILS for brace in _GRAIN_BRACES),
        _pair("grain-rack-rail-bottom", "grain-rack-shelf"),
        _pair("grain-rack-brace-left", "grain-rack-brace-left-rear"),
        _pair("grain-rack-brace-right", "grain-rack-brace-right-rear"),
        *(_pair(brace, "grain-rack-shelf") for brace in _GRAIN_BRACES),
    }),
    "prop-stone-trough-01": frozenset({
        *(
            _pair("stone-trough-basin", f"stone-trough-wall-{side}")
            for side in ("front", "rear", "left", "right")
        ),
        *(
            _pair(
                f"stone-trough-wall-{front_back}",
                f"stone-trough-wall-{left_right}",
            )
            for front_back in ("front", "rear")
            for left_right in ("left", "right")
        ),
    }),
    "prop-handcart-01": frozenset({
        *(_pair("cart-bed", f"cart-wheel-{side}") for side in ("left", "right")),
        *(_pair("cart-bed", f"cart-handle-{side}") for side in ("left", "right")),
        *(_pair("cart-bed", f"cart-brace-{side}") for side in ("left", "right")),
        *(
            _pair(f"cart-wheel-{side}", spoke)
            for side, spokes in _HANDCART_SPOKES.items()
            for spoke in spokes
        ),
        *(_pair(f"cart-wheel-{side}", "cart-axle") for side in ("left", "right")),
        *(
            _pair(spokes[i], spokes[j])
            for spokes in _HANDCART_SPOKES.values()
            for i in range(len(spokes))
            for j in range(i + 1, len(spokes))
        ),
        *(
            _pair(spoke, "cart-axle")
            for spokes in _HANDCART_SPOKES.values()
            for spoke in spokes
        ),
        *(_pair("cart-axle", f"cart-handle-{side}") for side in ("left", "right")),
        *(_pair("cart-axle", f"cart-brace-{side}") for side in ("left", "right")),
        *(
            _pair(f"cart-handle-{side}", f"cart-brace-{side}")
            for side in ("left", "right")
        ),
    }),
}

# Maximum absolute coordinate (m) for any local transform component or
# render bound. Props are small (under 2 m); anything beyond 10 m is a
# schema bug or a unit confusion.
MAX_ABS_COORDINATE_M = 10.0


class PropGeometryV2Error(RuntimeError):
    """Raised when a prop-geometry v2 plan violates the fail-closed
    contract. Never returned as a status field; always raised."""


# ---------------------------------------------------------------------------
# Frozen base
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_assignment=True,
        str_strip_whitespace=False,
    )


# ---------------------------------------------------------------------------
# PropPart
# ---------------------------------------------------------------------------

#: Collision roles Literal-locked to the safe set. ``phantom`` is
#: intentionally not in the set — a part without a real collision role
#: must not be added.
CollisionRole = Literal["solid", "hollow", "opening", "rest", "decoration"]

#: Support roles. ``grounded`` parts must touch the envelope floor;
#: ``rest`` parts rest on another part; ``suspended`` parts are anchored
#: to a higher part; ``floating`` is forbidden for required supports.
SupportRole = Literal["grounded", "rest", "suspended"]


def _validate_finite_tuple(
    value: tuple[float, ...],
    label: str,
    max_abs: float = MAX_ABS_COORDINATE_M,
) -> None:
    for i, c in enumerate(value):
        if math.isnan(c) or math.isinf(c):
            raise ValueError(
                f"{label}[{i}] is not finite: {c}")
        if abs(c) > max_abs:
            raise ValueError(
                f"{label}[{i}]={c} out of bounds (max abs {max_abs}m)")


class PropPart(_Frozen):
    """One stable part of a prop's canonical part graph.

    Local coordinates are meters in the prop's local frame, with +Z up
    and origin at the prop's ground-plane center. ``local_transform_m``
    is the part center relative to the prop origin. ``render_bounds_m``
    is the conservative prop-frame axis-aligned bounding box after the
    declared rotation (width, depth, height).
    """

    part_id: str = Field(pattern=STABLE_ID_PATTERN)
    local_transform_m: tuple[float, float, float]
    local_rotation_euler_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    material_slot_id: str = Field(pattern=STABLE_ID_PATTERN)
    collision_role: CollisionRole
    render_bounds_m: tuple[float, float, float]
    support_role: SupportRole = "grounded"
    #: Semantic kind (body, rim, opening, foot, wheel, axle, etc.). Used
    #: to enforce the per-slot required-kind coverage. Defaults to
    #: ``part`` so parts without a more specific kind still validate.
    semantic_kind: str = Field(default="part", pattern=STABLE_ID_PATTERN)
    @model_validator(mode="after")
    def _validate_part(self) -> PropPart:
        _validate_finite_tuple(self.local_transform_m, "local_transform_m")
        _validate_finite_tuple(
            self.local_rotation_euler_deg,
            "local_rotation_euler_deg",
            max_abs=360.0,
        )
        _validate_finite_tuple(self.render_bounds_m, "render_bounds_m")
        # Zero-volume parts are forbidden — they are almost always a
        # missing-data or unit bug.
        if any(b == 0 for b in self.render_bounds_m):
            raise ValueError(
                f"part {self.part_id}: render_bounds_m has a zero "
                f"dimension ({self.render_bounds_m}) → zero-volume part")
        if any(b < 0 for b in self.render_bounds_m):
            raise ValueError(
                f"part {self.part_id}: render_bounds_m has a negative "
                f"dimension ({self.render_bounds_m})")
        return self


# ---------------------------------------------------------------------------
# PropSlotPlan
# ---------------------------------------------------------------------------


class PropSlotPlan(_Frozen):
    """One slot's canonical part graph + envelope + source SHA."""

    slot_id: str = Field(pattern=STABLE_ID_PATTERN)
    source_image_sha256: str = Field(pattern=SHA256_PATTERN)
    parts: tuple[PropPart, ...]
    envelope_m: tuple[float, float, float]
    allowed_intersections: tuple[tuple[str, str], ...] = ()
    #: Optional collision proxy SHA, set when the plan is finalized for
    #: build. Empty during pure-model authoring.
    collision_proxy_sha256: str = Field(
        default="",
        pattern=OPTIONAL_SHA256_PATTERN,
    )

    @model_validator(mode="after")
    def _validate_slot_plan(self) -> PropSlotPlan:
        if self.slot_id not in SUPPORTED_SLOT_IDS:
            raise PropGeometryV2Error(
                f"unknown slot_id={self.slot_id!r}; supported ids are "
                f"{sorted(SUPPORTED_SLOT_IDS)}")
        if not self.parts:
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: parts must be non-empty")
        # Duplicate part ids are forbidden.
        part_ids = [p.part_id for p in self.parts]
        if len(set(part_ids)) != len(part_ids):
            seen: set[str] = set()
            dups = []
            for pid in part_ids:
                if pid in seen:
                    dups.append(pid)
                seen.add(pid)
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: duplicate part ids {sorted(set(dups))}")
        normalized_declared = tuple(
            _pair(left, right)
            for left, right in self.allowed_intersections
        )
        if any(left == right for left, right in normalized_declared):
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: an allowed intersection cannot "
                "reference the same part twice"
            )
        if len(set(normalized_declared)) != len(normalized_declared):
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: duplicate allowed intersections"
            )
        unknown_intersection_ids = {
            part_id
            for pair in normalized_declared
            for part_id in pair
            if part_id not in part_ids
        }
        if unknown_intersection_ids:
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: allowed intersections reference "
                f"unknown parts {sorted(unknown_intersection_ids)}"
            )
        # Source SHA must match the slot's locked SHA.
        expected_sha = BATCH35_SOURCE_SHAS.get(self.slot_id)
        if expected_sha is None or self.source_image_sha256 != expected_sha:
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: source_image_sha256 disagrees with "
                f"FEEDBACK-IMAGE2-040 (expected {expected_sha!r})")
        # Envelope must be finite and positive.
        _validate_finite_tuple(self.envelope_m, "envelope_m")
        if any(b <= 0 for b in self.envelope_m):
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: envelope_m must be positive "
                f"(got {self.envelope_m})")
        # Each part's transform + half-bounds must fit inside the envelope.
        ew, ed, eh = self.envelope_m
        for part in self.parts:
            px, py, pz = part.local_transform_m
            bw, bd, bh = part.render_bounds_m
            # Grounded parts must touch the floor (their lowest Z is <= 0).
            if part.support_role == "grounded":
                if pz - bh / 2 > 0.001:
                    raise PropGeometryV2Error(
                        f"slot {self.slot_id}: part {part.part_id} is "
                        f"marked grounded but floats above the floor "
                        f"(center_z={pz}, half_h={bh/2})")
            # Render bounds must fit inside the envelope (with a small
            # tolerance for floating-point).
            tol = 1e-3
            if abs(px) + bw / 2 > ew / 2 + tol:
                raise PropGeometryV2Error(
                    f"slot {self.slot_id}: part {part.part_id} render "
                    f"bounds overflow envelope on X "
                    f"(center_x={px}, half_w={bw/2}, envelope_w={ew})")
            if abs(py) + bd / 2 > ed / 2 + tol:
                raise PropGeometryV2Error(
                    f"slot {self.slot_id}: part {part.part_id} render "
                    f"bounds overflow envelope on Y "
                    f"(center_y={py}, half_d={bd/2}, envelope_d={ed})")
            if pz + bh / 2 > eh + tol or pz - bh / 2 < -tol:
                raise PropGeometryV2Error(
                    f"slot {self.slot_id}: part {part.part_id} render "
                    f"bounds overflow envelope on Z "
                    f"(center_z={pz}, half_h={bh/2}, envelope_h={eh})")
        collidable = [
            part
            for part in self.parts
            if part.collision_role in {"solid", "hollow"}
        ]
        observed_intersections: set[tuple[str, str]] = set()
        for index, left in enumerate(collidable):
            for right in collidable[index + 1:]:
                overlaps = (
                    min(
                        left.local_transform_m[axis]
                        + left.render_bounds_m[axis] / 2,
                        right.local_transform_m[axis]
                        + right.render_bounds_m[axis] / 2,
                    )
                    - max(
                        left.local_transform_m[axis]
                        - left.render_bounds_m[axis] / 2,
                        right.local_transform_m[axis]
                        - right.render_bounds_m[axis] / 2,
                    )
                    for axis in range(3)
                )
                if all(overlap > 1e-3 for overlap in overlaps):
                    observed_intersections.add(
                        _pair(left.part_id, right.part_id)
                    )
        declared_intersections = set(normalized_declared)
        if observed_intersections != declared_intersections:
            unexpected = observed_intersections - declared_intersections
            stale = declared_intersections - observed_intersections
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: intersection declarations disagree "
                f"with geometry; unexpected={sorted(unexpected)}, "
                f"stale={sorted(stale)}"
            )
        # Required semantic kinds must be present.
        required = REQUIRED_SEMANTIC_KINDS.get(self.slot_id, frozenset())
        present = {p.semantic_kind for p in self.parts}
        missing = required - present
        if missing:
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: missing required semantic kinds "
                f"{sorted(missing)}; present={sorted(present)}")
        # Exact-count kinds (e.g. handcart exactly two wheels).
        exact_counts = EXACT_COUNT_KINDS.get(self.slot_id, {})
        for kind, expected in exact_counts.items():
            actual = sum(1 for p in self.parts if p.semantic_kind == kind)
            if actual != expected:
                raise PropGeometryV2Error(
                    f"slot {self.slot_id}: semantic_kind={kind!r} requires "
                    f"exactly {expected} parts, got {actual}")
        # Min-count kinds (e.g. farming-tools at least four tool-heads).
        min_counts = MIN_COUNT_KINDS.get(self.slot_id, {})
        for kind, minimum in min_counts.items():
            actual = sum(1 for p in self.parts if p.semantic_kind == kind)
            if actual < minimum:
                raise PropGeometryV2Error(
                    f"slot {self.slot_id}: semantic_kind={kind!r} requires "
                    f"at least {minimum} parts, got {actual}")
        return self

    def finalize_for_build(self, collision_proxy_sha256: str) -> PropSlotPlan:
        """Return a new plan with the collision proxy SHA bound.

        A solid collision role requires a non-empty SHA at finalize time.
        Empty SHA is rejected.
        """
        if re.fullmatch(r"[0-9a-f]{64}", collision_proxy_sha256) is None:
            raise PropGeometryV2Error(
                f"slot {self.slot_id}: collision_proxy_sha256 must be a "
                f"64-hex SHA-256 at finalize time")
        # Re-validate via the constructor (frozen model → new instance).
        return self.model_copy(update={"collision_proxy_sha256": collision_proxy_sha256})


# ---------------------------------------------------------------------------
# PropGeometryV2Plan (top-level plan)
# ---------------------------------------------------------------------------


class PropGeometryV2Plan(_Frozen):
    schema_version: Literal[
        "nantai.synthetic-village.prop-geometry-v2.v1"
    ] = PROP_GEOMETRY_V2_SCHEMA
    synthetic: Literal[True] = True
    stage: Literal["design-only"] = "design-only"
    camera_calibration: Literal["unknown"] = "unknown"
    geometry_consistency: Literal["not-verified"] = "not-verified"
    metric_scale: Literal["unknown"] = "unknown"
    real_photo_texture: Literal[False] = False
    training_use: Literal["forbidden-as-multiview"] = "forbidden-as-multiview"
    coverage_use: Literal["forbidden"] = "forbidden"
    clearance_use: Literal["forbidden-as-evidence"] = "forbidden-as-evidence"
    trust_effect: Literal["none"] = "none"
    slot_plans: dict[str, PropSlotPlan]

    @model_validator(mode="after")
    def _validate_plan(self) -> PropGeometryV2Plan:
        if set(self.slot_plans.keys()) != SUPPORTED_SLOT_IDS:
            raise PropGeometryV2Error(
                f"plan must cover exactly the eight supported slots; "
                f"got {sorted(self.slot_plans.keys())}")
        mismatched_keys = {
            key: slot_plan.slot_id
            for key, slot_plan in self.slot_plans.items()
            if key != slot_plan.slot_id
        }
        if mismatched_keys:
            raise PropGeometryV2Error(
                "slot_plans keys must match their embedded slot_id values; "
                f"mismatches={mismatched_keys}"
            )
        return self


# ---------------------------------------------------------------------------
# Canonical part graphs (frozen design decisions)
# ---------------------------------------------------------------------------


def _water_jar_plan() -> PropSlotPlan:
    """Jar body/rim/opening/foot per FEEDBACK-IMAGE2-040 §4.

    Canonical decisions (frozen, not averaged):
      - Ovoid body, widest near the upper third (water jar silhouette).
      - Narrow neck/rim with a circular opening.
      - Flat foot ring for stability.
      - Material: clay-brick (glazed ceramic).
    """
    parts = (
        PropPart(
            part_id="water-jar-body",
            local_transform_m=(0.0, 0.0, 0.50),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(0.96, 0.96, 1.00),
            support_role="grounded",
            semantic_kind="body",
        ),
        PropPart(
            part_id="water-jar-rim",
            local_transform_m=(0.0, 0.0, 1.05),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(0.46, 0.46, 0.10),
            support_role="rest",
            semantic_kind="rim",
        ),
        PropPart(
            part_id="water-jar-opening",
            local_transform_m=(0.0, 0.0, 1.08),
            material_slot_id="material-clay-brick-01",
            collision_role="opening",
            render_bounds_m=(0.30, 0.30, 0.04),
            support_role="rest",
            semantic_kind="opening",
        ),
        PropPart(
            part_id="water-jar-foot",
            local_transform_m=(0.0, 0.0, 0.02),
            material_slot_id="material-clay-brick-01",
            collision_role="solid",
            render_bounds_m=(0.70, 0.70, 0.04),
            support_role="grounded",
            semantic_kind="foot",
        ),
    )
    return PropSlotPlan(
        slot_id="prop-water-jar-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-water-jar-01"],
        parts=parts,
        envelope_m=(1.00, 1.00, 1.20),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-water-jar-01"]
        )),
    )


def _firewood_stack_plan() -> PropSlotPlan:
    """Stack frame plus varied logs (3 rows × 4 logs = 12)."""
    logs = []
    for row in range(3):
        for col in range(4):
            # Alternate log lengths slightly (visual variation, frozen).
            length = 1.45 - (col % 2) * 0.12
            logs.append(PropPart(
                part_id=f"firewood-log-{row}-{col}",
                local_transform_m=(
                    0.0,
                    -0.45 + col * 0.30,
                    0.18 + row * 0.30,
                ),
                material_slot_id="material-weathered-timber-01",
                collision_role="solid",
                render_bounds_m=(length, 0.26, 0.26),
                support_role="rest",
                semantic_kind="log",
            ))
    frame = PropPart(
        part_id="firewood-frame",
        local_transform_m=(0.0, 0.0, 0.05),
        material_slot_id="material-weathered-timber-01",
        collision_role="solid",
        render_bounds_m=(1.50, 1.10, 0.10),
        support_role="grounded",
        semantic_kind="frame",
    )
    return PropSlotPlan(
        slot_id="prop-firewood-stack-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-firewood-stack-01"],
        parts=(frame, *logs),
        envelope_m=(1.60, 1.20, 1.00),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-firewood-stack-01"]
        )),
    )


def _bamboo_basket_plan() -> PropSlotPlan:
    """Basket body/rim/loops/open interior/base.

    Canonical decisions:
      - Tapered body, widest at the rim.
      - Two loop handles on opposite sides.
      - Open interior (collision_role='opening') for visual depth.
      - Flat woven base.
    """
    parts = (
        PropPart(
            part_id="bamboo-basket-body",
            local_transform_m=(0.0, 0.0, 0.45),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.16, 1.16, 0.90),
            support_role="grounded",
            semantic_kind="body",
        ),
        PropPart(
            part_id="bamboo-basket-rim",
            local_transform_m=(0.0, 0.0, 0.90),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.20, 1.20, 0.06),
            support_role="rest",
            semantic_kind="rim",
        ),
        PropPart(
            part_id="bamboo-basket-loop-handle-a",
            local_transform_m=(-0.55, 0.0, 1.05),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.10, 0.10, 0.30),
            support_role="rest",
            semantic_kind="handle",
        ),
        PropPart(
            part_id="bamboo-basket-loop-handle-b",
            local_transform_m=(0.55, 0.0, 1.05),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.10, 0.10, 0.30),
            support_role="rest",
            semantic_kind="handle",
        ),
        PropPart(
            part_id="bamboo-basket-interior",
            local_transform_m=(0.0, 0.0, 0.70),
            material_slot_id="material-weathered-timber-01",
            collision_role="opening",
            render_bounds_m=(1.00, 1.00, 0.40),
            support_role="rest",
            semantic_kind="opening",
        ),
        PropPart(
            part_id="bamboo-basket-base",
            local_transform_m=(0.0, 0.0, 0.03),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.90, 0.90, 0.06),
            support_role="grounded",
            semantic_kind="base",
        ),
    )
    return PropSlotPlan(
        slot_id="prop-bamboo-basket-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-bamboo-basket-01"],
        parts=parts,
        envelope_m=(1.30, 1.30, 1.20),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-bamboo-basket-01"]
        )),
    )


def _wooden_bench_plan() -> PropSlotPlan:
    """Backless bench seat/legs/braces/pegs.

    FEEDBACK-IMAGE2-040 §3: freeze one explicit part graph where panels
    disagree. The current ``_build_prop`` has a back-like slab despite
    the backless slot description — this plan is backless.
    """
    parts = (
        PropPart(
            part_id="bench-seat",
            local_transform_m=(0.0, 0.0, 0.45),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.40, 0.40, 0.08),
            support_role="rest",
            semantic_kind="seat",
        ),
        PropPart(
            part_id="bench-leg-front-left",
            local_transform_m=(-0.60, -0.13, 0.22),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.10, 0.10, 0.44),
            support_role="grounded",
            semantic_kind="legs",
        ),
        PropPart(
            part_id="bench-leg-front-right",
            local_transform_m=(0.60, -0.13, 0.22),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.10, 0.10, 0.44),
            support_role="grounded",
            semantic_kind="legs",
        ),
        PropPart(
            part_id="bench-leg-rear-left",
            local_transform_m=(-0.60, 0.13, 0.22),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.10, 0.10, 0.44),
            support_role="grounded",
            semantic_kind="legs",
        ),
        PropPart(
            part_id="bench-leg-rear-right",
            local_transform_m=(0.60, 0.13, 0.22),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.10, 0.10, 0.44),
            support_role="grounded",
            semantic_kind="legs",
        ),
        PropPart(
            part_id="bench-brace-long",
            local_transform_m=(0.0, -0.13, 0.22),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.20, 0.06, 0.06),
            support_role="rest",
            semantic_kind="braces",
        ),
        PropPart(
            part_id="bench-brace-short",
            local_transform_m=(0.0, 0.13, 0.22),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.20, 0.06, 0.06),
            support_role="rest",
            semantic_kind="braces",
        ),
        *(
            PropPart(
                part_id=f"bench-peg-{x_name}-{y_name}",
                local_transform_m=(x, y, 0.44),
                material_slot_id="material-weathered-timber-01",
                collision_role="solid",
                render_bounds_m=(0.04, 0.04, 0.08),
                support_role="rest",
                semantic_kind="pegs",
            )
            for x_name, x in (("left", -0.60), ("right", 0.60))
            for y_name, y in (("front", -0.13), ("rear", 0.13))
        ),
    )
    return PropSlotPlan(
        slot_id="prop-wooden-bench-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-wooden-bench-01"],
        parts=parts,
        envelope_m=(1.60, 0.60, 0.50),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-wooden-bench-01"]
        )),
    )


def _farming_tools_plan() -> PropSlotPlan:
    """Four distinct tool heads/handles plus rest.

    Canonical tools (frozen):
      1. Hoe — flat blade + handle.
      2. Rake — fan head + handle.
      3. Sickle — curved blade + handle.
      4. Spade — square head + handle.
    All rest on a low timber stand.
    """
    handle_kind = "handle"
    head_kind = "tool-head"
    parts = [
        PropPart(
            part_id="tool-rest",
            local_transform_m=(0.0, 0.0, 0.05),
            material_slot_id="material-weathered-timber-01",
            collision_role="rest",
            render_bounds_m=(1.00, 0.30, 0.10),
            support_role="grounded",
            semantic_kind="rest",
        ),
    ]
    # Four tools at x = -0.45, -0.15, 0.15, 0.45.
    tool_specs = (
        ("hoe", "hoe"),
        ("rake", "rake"),
        ("sickle", "sickle"),
        ("spade", "spade"),
    )
    for i, (tool_name, _) in enumerate(tool_specs):
        x = -0.45 + i * 0.30
        parts.append(PropPart(
            part_id=f"tool-{tool_name}-head",
            local_transform_m=(x, 0.0, 1.30),
            material_slot_id="material-aged-metal-01",
            collision_role="solid",
            render_bounds_m=(0.18, 0.06, 0.20),
            support_role="rest",
            semantic_kind=head_kind,
        ))
        parts.append(PropPart(
            part_id=f"tool-{tool_name}-handle",
            local_transform_m=(x, 0.0, 0.70),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.05, 0.05, 1.20),
            support_role="rest",
            semantic_kind=handle_kind,
        ))
    return PropSlotPlan(
        slot_id="prop-farming-tools-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-farming-tools-01"],
        parts=tuple(parts),
        envelope_m=(1.20, 0.40, 1.40),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-farming-tools-01"]
        )),
    )


def _grain_rack_plan() -> PropSlotPlan:
    """Rack frames/rails/braces/slatted shelf.

    Canonical decisions:
      - Two A-frame end assemblies.
      - Three horizontal rails.
      - Two diagonal braces per end.
      - One slatted shelf across the lower rails.
    """
    parts = (
        # Frames (two A-frame ends).
        PropPart(
            part_id="grain-rack-frame-left",
            local_transform_m=(-0.55, 0.0, 0.70),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.50, 1.40),
            support_role="grounded",
            semantic_kind="frame",
        ),
        PropPart(
            part_id="grain-rack-frame-right",
            local_transform_m=(0.55, 0.0, 0.70),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.50, 1.40),
            support_role="grounded",
            semantic_kind="frame",
        ),
        # Rails (three horizontal).
        PropPart(
            part_id="grain-rack-rail-top",
            local_transform_m=(0.0, 0.0, 1.35),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.20, 0.06, 0.06),
            support_role="suspended",
            semantic_kind="rail",
        ),
        PropPart(
            part_id="grain-rack-rail-mid",
            local_transform_m=(0.0, 0.0, 0.95),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.20, 0.06, 0.06),
            support_role="suspended",
            semantic_kind="rail",
        ),
        PropPart(
            part_id="grain-rack-rail-bottom",
            local_transform_m=(0.0, 0.0, 0.40),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.20, 0.06, 0.06),
            support_role="rest",
            semantic_kind="rail",
        ),
        # Braces (two diagonal per end → 4 total).
        PropPart(
            part_id="grain-rack-brace-left",
            local_transform_m=(-0.55, 0.0, 0.85),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.40, 1.10),
            support_role="rest",
            semantic_kind="brace",
        ),
        PropPart(
            part_id="grain-rack-brace-right",
            local_transform_m=(0.55, 0.0, 0.85),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.40, 1.10),
            support_role="rest",
            semantic_kind="brace",
        ),
        PropPart(
            part_id="grain-rack-brace-left-rear",
            local_transform_m=(-0.55, 0.10, 0.85),
            local_rotation_euler_deg=(12.0, 0.0, 0.0),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.40, 1.10),
            support_role="rest",
            semantic_kind="brace",
        ),
        PropPart(
            part_id="grain-rack-brace-right-rear",
            local_transform_m=(0.55, 0.10, 0.85),
            local_rotation_euler_deg=(-12.0, 0.0, 0.0),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.40, 1.10),
            support_role="rest",
            semantic_kind="brace",
        ),
        # Slatted shelf.
        PropPart(
            part_id="grain-rack-shelf",
            local_transform_m=(0.0, 0.0, 0.42),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.10, 0.50, 0.04),
            support_role="suspended",
            semantic_kind="shelf",
        ),
    )
    return PropSlotPlan(
        slot_id="prop-grain-rack-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-grain-rack-01"],
        parts=parts,
        envelope_m=(1.30, 0.60, 1.40),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-grain-rack-01"]
        )),
    )


def _stone_trough_plan() -> PropSlotPlan:
    """Open trough basin/walls/notch/feet."""
    parts = (
        PropPart(
            part_id="stone-trough-basin",
            local_transform_m=(0.0, 0.0, 0.30),
            material_slot_id="material-fieldstone-01",
            collision_role="hollow",
            render_bounds_m=(1.00, 0.50, 0.30),
            support_role="rest",
            semantic_kind="basin",
        ),
        PropPart(
            part_id="stone-trough-wall-front",
            local_transform_m=(0.0, -0.22, 0.30),
            material_slot_id="material-fieldstone-01",
            collision_role="solid",
            render_bounds_m=(1.00, 0.06, 0.30),
            support_role="rest",
            semantic_kind="wall",
        ),
        PropPart(
            part_id="stone-trough-wall-rear",
            local_transform_m=(0.0, 0.22, 0.30),
            material_slot_id="material-fieldstone-01",
            collision_role="solid",
            render_bounds_m=(1.00, 0.06, 0.30),
            support_role="rest",
            semantic_kind="wall",
        ),
        PropPart(
            part_id="stone-trough-wall-left",
            local_transform_m=(-0.47, 0.0, 0.30),
            material_slot_id="material-fieldstone-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.50, 0.30),
            support_role="rest",
            semantic_kind="wall",
        ),
        PropPart(
            part_id="stone-trough-wall-right",
            local_transform_m=(0.47, 0.0, 0.30),
            material_slot_id="material-fieldstone-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.50, 0.30),
            support_role="rest",
            semantic_kind="wall",
        ),
        PropPart(
            part_id="stone-trough-drain-notch",
            local_transform_m=(0.0, -0.25, 0.38),
            material_slot_id="material-fieldstone-01",
            collision_role="opening",
            render_bounds_m=(0.18, 0.08, 0.16),
            support_role="rest",
            semantic_kind="notch",
        ),
        PropPart(
            part_id="stone-trough-foot-left",
            local_transform_m=(-0.40, 0.0, 0.05),
            material_slot_id="material-fieldstone-01",
            collision_role="solid",
            render_bounds_m=(0.20, 0.40, 0.10),
            support_role="grounded",
            semantic_kind="feet",
        ),
        PropPart(
            part_id="stone-trough-foot-right",
            local_transform_m=(0.40, 0.0, 0.05),
            material_slot_id="material-fieldstone-01",
            collision_role="solid",
            render_bounds_m=(0.20, 0.40, 0.10),
            support_role="grounded",
            semantic_kind="feet",
        ),
    )
    return PropSlotPlan(
        slot_id="prop-stone-trough-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-stone-trough-01"],
        parts=parts,
        envelope_m=(1.20, 0.60, 0.50),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-stone-trough-01"]
        )),
    )


def _handcart_plan() -> PropSlotPlan:
    """Cart bed/two spoked wheels/axle/handles/braces/rests.

    Exactly two wheels (EXACT_COUNT_KINDS enforces this).
    """
    parts = (
        PropPart(
            part_id="cart-bed",
            local_transform_m=(0.0, 0.0, 0.65),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(1.40, 0.70, 0.20),
            support_role="rest",
            semantic_kind="bed",
        ),
        PropPart(
            part_id="cart-wheel-left",
            local_transform_m=(-0.55, 0.0, 0.30),
            material_slot_id="material-aged-metal-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.60, 0.60),
            support_role="grounded",
            semantic_kind="wheel",
        ),
        PropPart(
            part_id="cart-wheel-right",
            local_transform_m=(0.55, 0.0, 0.30),
            material_slot_id="material-aged-metal-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.60, 0.60),
            support_role="grounded",
            semantic_kind="wheel",
        ),
        *(
            PropPart(
                part_id=f"cart-spoke-{side_name}-{angle:03d}",
                local_transform_m=(x, 0.0, 0.30),
                local_rotation_euler_deg=(float(angle), 0.0, 0.0),
                material_slot_id="material-aged-metal-01",
                collision_role="solid",
                render_bounds_m=(0.03, 0.50, 0.04),
                support_role="suspended",
                semantic_kind="spoke",
            )
            for side_name, x in (("left", -0.55), ("right", 0.55))
            for angle in (0, 45, 90, 135)
        ),
        PropPart(
            part_id="cart-axle",
            local_transform_m=(0.0, 0.0, 0.30),
            material_slot_id="material-aged-metal-01",
            collision_role="solid",
            render_bounds_m=(1.20, 0.06, 0.06),
            support_role="rest",
            semantic_kind="axle",
        ),
        PropPart(
            part_id="cart-handle-left",
            local_transform_m=(-0.30, 0.0, 0.80),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.06, 1.20),
            support_role="suspended",
            semantic_kind="handle",
        ),
        PropPart(
            part_id="cart-handle-right",
            local_transform_m=(0.30, 0.0, 0.80),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.06, 0.06, 1.20),
            support_role="suspended",
            semantic_kind="handle",
        ),
        PropPart(
            part_id="cart-brace-left",
            local_transform_m=(-0.30, 0.0, 0.50),
            local_rotation_euler_deg=(0.0, -12.0, 0.0),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.08, 0.08, 0.80),
            support_role="rest",
            semantic_kind="brace",
        ),
        PropPart(
            part_id="cart-brace-right",
            local_transform_m=(0.30, 0.0, 0.50),
            local_rotation_euler_deg=(0.0, 12.0, 0.0),
            material_slot_id="material-weathered-timber-01",
            collision_role="solid",
            render_bounds_m=(0.08, 0.08, 0.80),
            support_role="rest",
            semantic_kind="brace",
        ),
        PropPart(
            part_id="cart-rest-left",
            local_transform_m=(-0.30, 0.25, 0.05),
            material_slot_id="material-weathered-timber-01",
            collision_role="rest",
            render_bounds_m=(0.12, 0.12, 0.10),
            support_role="grounded",
            semantic_kind="rest",
        ),
        PropPart(
            part_id="cart-rest-right",
            local_transform_m=(0.30, 0.25, 0.05),
            material_slot_id="material-weathered-timber-01",
            collision_role="rest",
            render_bounds_m=(0.12, 0.12, 0.10),
            support_role="grounded",
            semantic_kind="rest",
        ),
    )
    return PropSlotPlan(
        slot_id="prop-handcart-01",
        source_image_sha256=BATCH35_SOURCE_SHAS["prop-handcart-01"],
        parts=parts,
        envelope_m=(1.80, 0.80, 1.40),
        allowed_intersections=tuple(sorted(
            CANONICAL_ALLOWED_INTERSECTIONS["prop-handcart-01"]
        )),
    )


#: The frozen canonical plans for all eight slots.
PROP_SLOTS_V2: dict[str, PropSlotPlan] = {
    "prop-water-jar-01": _water_jar_plan(),
    "prop-firewood-stack-01": _firewood_stack_plan(),
    "prop-bamboo-basket-01": _bamboo_basket_plan(),
    "prop-wooden-bench-01": _wooden_bench_plan(),
    "prop-farming-tools-01": _farming_tools_plan(),
    "prop-grain-rack-01": _grain_rack_plan(),
    "prop-stone-trough-01": _stone_trough_plan(),
    "prop-handcart-01": _handcart_plan(),
}


def build_prop_geometry_v2_plan() -> PropGeometryV2Plan:
    """Build the canonical prop-geometry v2 plan for all eight slots.

    The plan is frozen at module import time; this function returns a
    re-validated view of it. Source SHAs, semantic-kind coverage,
    exact/min counts and envelope fits are all validated on construction.
    """
    return PropGeometryV2Plan(slot_plans=PROP_SLOTS_V2)


# ---------------------------------------------------------------------------
# Canonical LF JSON serialization
# ---------------------------------------------------------------------------


def _part_to_dict(part: PropPart) -> dict:
    return {
        "part_id": part.part_id,
        "local_transform_m": list(part.local_transform_m),
        "local_rotation_euler_deg": list(part.local_rotation_euler_deg),
        "material_slot_id": part.material_slot_id,
        "collision_role": part.collision_role,
        "render_bounds_m": list(part.render_bounds_m),
        "support_role": part.support_role,
        "semantic_kind": part.semantic_kind,
    }


def _slot_plan_to_dict(plan: PropSlotPlan) -> dict:
    return {
        "slot_id": plan.slot_id,
        "source_image_sha256": plan.source_image_sha256,
        "envelope_m": list(plan.envelope_m),
        "allowed_intersections": [
            list(pair)
            for pair in plan.allowed_intersections
        ],
        "collision_proxy_sha256": plan.collision_proxy_sha256,
        "parts": [_part_to_dict(p) for p in plan.parts],
    }


def serialize_prop_geometry_v2_plan(plan: PropGeometryV2Plan) -> bytes:
    """Serialize the plan to canonical LF JSON bytes.

    The bytes end with a trailing newline and are sorted by key, so the
    SHA-256 of the bytes is deterministic and can be used as a content
    address. The schema_version + trust fields are included so a future
    schema change produces a different SHA.
    """
    payload = {
        "schema_version": plan.schema_version,
        "synthetic": plan.synthetic,
        "stage": plan.stage,
        "camera_calibration": plan.camera_calibration,
        "geometry_consistency": plan.geometry_consistency,
        "metric_scale": plan.metric_scale,
        "real_photo_texture": plan.real_photo_texture,
        "training_use": plan.training_use,
        "coverage_use": plan.coverage_use,
        "clearance_use": plan.clearance_use,
        "trust_effect": plan.trust_effect,
        "slot_plans": {
            sid: _slot_plan_to_dict(p)
            for sid, p in plan.slot_plans.items()
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")
