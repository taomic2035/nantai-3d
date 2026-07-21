"""Reciprocal route module plan for Batch 8/9 (HANDOFF-OPUS-009).

This plan is the canonical, content-addressed counterpart of
``EnvironmentModulePlan`` v1: it binds ``ScenePlan`` SHA-256,
``ElevatedTopologyPlan`` SHA-256, ``EnvironmentModulePlan`` v1 SHA-256,
and the Batch 8 + Batch 9 Release manifest SHA-256s plus 12 selected
image SHA-256s.  It does not replace any field in the immutable
``EnvironmentModulePlan`` v1; it is additive.

The plan carries six reciprocal-route modules:

  1. ``central-courtyard-downhill`` -- downhill reciprocal route out of
     the central courtyard, bound to ``courtyard-public-002`` and
     ``path-network-002/003``.  Seven new parts: downhill gate, covered
     side passage, cross-slope alley, two route attachments, gallery post
     run, gallery guard.

  2. ``bridge-deck-crossing`` -- standing-eye bridge deck with two-end
     connectivity.  Six new parts: upstream/downstream route attachments,
     access ramp, side maintenance path, drainage scuppers, deck edge
     transition.  Bound to ``bridge-lower-001`` and ``path-network-001/005``.

  3. ``watermill-tailrace`` -- watermill tailwater maintenance route.
     Seven new parts: building shell, maintenance platform, service stair,
     access panel, creek-bank path, platform guard, tailrace retaining
     wall.  Bound to ``waterwheel-*`` instances 155-160 (in v1 bridge
     module) and ``creek-main-001``.

  4. ``covered-gallery-underpass`` -- cross-level covered gallery
     underpass.  Nine new parts: underpass lower lane, post/beam/
     foundation/guard runs, side door, three branch attachments (upper/
     lower/side).  Bound to ``covered-timber-gallery-v1`` and
     ``cross-level-covered-passage-v1``.

  5. ``forest-orchard-boundary`` -- forest/orchard boundary return route.
     Seven new parts: path fork, orchard transition, retaining drain,
     trail shelter, inbound/outbound route attachments, edge vegetation
     band.  Bound to ``orchard-slope-001/002`` and ``path-network-002``.

  6. ``lower-valley-uphill`` -- lower valley uphill reciprocal route.
     Seven new parts: entry path, field-edge path, creek-maintenance
     trail, drainage outlet, building back entry, route reconnection,
     retaining step.  Bound to ``path-network-001`` and ``creek-main-001``.

Instance ID segment: ``EnvironmentModulePlan`` v1 owns 131-175.
``ReciprocalRouteModulePlan`` v1 owns 176-218, partitioned across the six
modules.  The partition is hard-locked so a later module cannot steal
another module's instance IDs, and v1's 1-175 segment is left untouched.

Provenance contract:
  * ``synthetic=true``, ``geometry_usability=preview-only``,
    ``simplified-pbr-not-render-parity``, ``verification_level=L0``,
    ``real_photo_textures=false``, ``metric_alignment=false``,
    ``trust_effect=none``.
  * Reference images are ``design-only`` -- they never enter this plan
    as multi-view training evidence; their SHA-256 is bound for
    provenance only, not for coverage or orientation inference.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .elevated_topology import (
    ElevatedTopologyPlan,
    canonical_elevated_topology_bytes,
)
from .environment_module import (
    EnvironmentModulePlan,
    canonical_environment_module_plan_bytes,
)
from .scene_plan import SEMANTIC_ORDER, ScenePlan, canonical_scene_plan_bytes

RECIPROCAL_ROUTE_SCHEMA = "nantai.synthetic-village.reciprocal-route-module.v1"
RECIPROCAL_ROUTE_RECIPE_VERSION = "v1"

#: Batch 8 Release manifest + archive SHA-256s (HANDOFF-OPUS-009).
BATCH8_RELEASE_MANIFEST_SHA256 = (
    "be933fa37b56eee53e8acc78b7e2ff577c0bc4d6407fea91bfeb1da8d0637dbc"
)
BATCH8_ARCHIVE_SHA256 = (
    "6bdafc92b9eb2df3a943c4e5df3466e9609c22db89844dc940db3dab6ca921eb"
)

#: Batch 9 Release manifest + archive SHA-256s (HANDOFF-OPUS-009).
BATCH9_RELEASE_MANIFEST_SHA256 = (
    "bf5e2a5c6907baf5acefa5c6cf7d85bf9cfe611b47013f5bb1b564eca3064339"
)
BATCH9_ARCHIVE_SHA256 = (
    "6f7cc48e40e3d323a98e5ca91633cb6a6a7f623d7544efe44317102b3e5648f8"
)

#: Selected image SHA-256s from Batch 8 (design-only provenance binding).
BATCH8_CENTRAL_COURTYARD_DOWNHILL_SHA256 = (
    "05a49b4e085d555488e2ff1cc54ef7f643dc99fdbe184c3e09efe295af3c7408"
)
BATCH8_BRIDGE_DECK_CROSSING_SHA256 = (
    "ba6f3838b5a07b1f18c07e67c61f1ef31ff5862cf79c4c0fa60a248c0105cada"
)
BATCH8_WATERMILL_TAILRACE_SHA256 = (
    "77feef027408c2087dcb88f0d459eeab51e3a5f52b4af399eb9963ce3214a958"
)
BATCH8_COVERED_GALLERY_UNDERPASS_SHA256 = (
    "6d124e3269418558f3d5c187b9919d93d8e6e35e7b1ee71dc83591e5a0338b35"
)
BATCH8_FOREST_ORCHARD_BOUNDARY_SHA256 = (
    "339dbd218c09733d80460580d60b4e4bbd4854d3cde13aa5744a0f2a2aba466c"
)
BATCH8_LOWER_VALLEY_UPHILL_SHA256 = (
    "0641e54144a11d52411e08905a556c698f8e8d19fb78ff2c01cb4c5104ab76a7"
)

#: Selected image SHA-256s from Batch 9 (design-only provenance binding).
BATCH9_CENTRAL_COURTYARD_LATERAL_SHA256 = (
    "cd11d944f457c5dfb3415657eb85e38c0033c7c6e0d284771ede9f51d5d11cd8"
)
BATCH9_BRIDGE_DOWNSTREAM_BANK_SHA256 = (
    "f0e9c029b06dfa9832d44ca0ff4fbde186d84e1ef6a3adfcc6a994a09d1e97be"
)
BATCH9_WATERMILL_OPPOSITE_BANK_SHA256 = (
    "77137860a0b2f98d35747bde61a3852bcf10882343235a5c0faeb5d85f619f83"
)
BATCH9_COVERED_GALLERY_LOWER_LANE_SHA256 = (
    "a5f935bbdd2b6609aef40b92c0c8e57e746257274e2c21c81990835715df2ec0"
)
BATCH9_FOREST_ORCHARD_LATERAL_FORK_SHA256 = (
    "afd44bbdb965be7a3f6a478cd9c2509aead86c1204389c992a5d7fbdcb9ed80e"
)
BATCH9_LOWER_VALLEY_FIELD_EDGE_SHA256 = (
    "788eb01187c13ca02807a20cee42720b1970100d9c714d1bd647c82dc353dd7b"
)

#: Instance ID segments.  v1 owns 131-175 (locked in environment_module.py).
#: Reciprocal route modules own 176-218, partitioned as follows.  Changing
#: these numbers changes the plan digest.
CENTRAL_DOWNHILL_INSTANCE_RANGE = range(176, 183)   # 176..182 (7 parts)
BRIDGE_CROSSING_INSTANCE_RANGE = range(183, 189)    # 183..188 (6 parts)
WATERMILL_TAILRACE_INSTANCE_RANGE = range(189, 196) # 189..195 (7 parts)
GALLERY_UNDERPASS_INSTANCE_RANGE = range(196, 205)  # 196..204 (9 parts)
FOREST_BOUNDARY_INSTANCE_RANGE = range(205, 212)     # 205..211 (7 parts)
LOWER_VALLEY_UPHILL_INSTANCE_RANGE = range(212, 219) # 212..218 (7 parts)

#: Hard geometric thresholds (HANDOFF-OPUS-009 §1-6).
MIN_DOWNHILL_GATE_WIDTH_M = 1.8
MIN_CROSS_SLOPE_ALLEY_WIDTH_M = 1.6
MIN_COVERED_PASSAGE_CLEAR_HEIGHT_M = 2.4
MIN_BRIDGE_ACCESS_RAMP_WIDTH_M = 2.4
MAX_BRIDGE_RAMP_SLOPE_PCT = 8.3
MIN_MAINTENANCE_PLATFORM_WIDTH_M = 1.2
MAX_SERVICE_STAIR_SLOPE_PCT = 38.0
MIN_GALLERY_UNDERPASS_CLEAR_HEIGHT_M = 2.4
MIN_GALLERY_UNDERPASS_CLEAR_WIDTH_M = 1.8
MIN_TRAIL_SHELTER_CLEAR_WIDTH_M = 1.5
MIN_LOWER_VALLEY_TRAIL_WIDTH_M = 1.2
MAX_RETAINING_STEP_RISE_M = 0.18

#: Default spatial layout for the simplified v1 build (Phase 4.1).
#: Used only by ``_default_part_layout`` to populate ``PartLayoutSpec``;
#: the Blender runtime script never reads these constants.  Any change
#: here changes ``reciprocal_route_module_plan_sha256`` and therefore
#: ``build_id`` and the downstream render identity.
_DEFAULT_MODULE_BASE_POSITION: dict[ModuleId, tuple[float, float, float]] = {
    "central-courtyard-downhill": (40.0, 30.0, 70.0),
    "bridge-deck-crossing": (-150.0, -100.0, 50.0),
    "watermill-tailrace": (-180.0, -130.0, 45.0),
    "covered-gallery-underpass": (60.0, -25.0, 78.0),
    "forest-orchard-boundary": (120.0, 80.0, 75.0),
    "lower-valley-uphill": (-90.0, 60.0, 55.0),
}
_DEFAULT_PART_SPACING_Y_M = 2.5
_DEFAULT_PART_EXTENT_M: tuple[float, float, float] = (1.6, 1.6, 0.6)
_DEFAULT_PART_ORIENTATION_DEG = 0.0

# BuildReport v1 reserves 0/1/2 for sky, terrain, and terrain-support,
# then assigns ScenePlan semantic classes from 3 in SEMANTIC_ORDER order.
SEMANTIC_ID_BY_CLASS = {
    semantic_class: semantic_id
    for semantic_id, semantic_class in enumerate(SEMANTIC_ORDER, start=3)
}

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ModuleId = Literal[
    "central-courtyard-downhill",
    "bridge-deck-crossing",
    "watermill-tailrace",
    "covered-gallery-underpass",
    "forest-orchard-boundary",
    "lower-valley-uphill",
]
PartId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class ReciprocalRouteError(ValueError):
    """The reciprocal route module plan cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


# --------------------------------------------------------------------------- #
# 1. Central courtyard downhill reciprocal route.
# --------------------------------------------------------------------------- #


class CourtyardDownhillGateSpec(FrozenModel):
    """Downhill gate at the courtyard edge leading to the reciprocal route."""

    clear_width_m: float = Field(
        ge=MIN_DOWNHILL_GATE_WIDTH_M,
        allow_inf_nan=False,
    )
    connects_to_topology: Literal["path-network-002", "path-network-003"]
    threshold_coplanar_with_paving: Literal[True] = True


class CourtyardCoveredSidePassageSpec(FrozenModel):
    """Covered side passage branching off the downhill route."""

    clear_height_m: float = Field(
        ge=MIN_COVERED_PASSAGE_CLEAR_HEIGHT_M,
        allow_inf_nan=False,
    )
    clear_width_m: float = Field(
        ge=MIN_CROSS_SLOPE_ALLEY_WIDTH_M,
        allow_inf_nan=False,
    )
    connects_to_topology: Literal["path-network-002", "path-network-003"]
    drainage_channel_not_blocking: Literal[True] = True


class CourtyardCrossSlopeAlleySpec(FrozenModel):
    """Cross-slope alley reconnecting to the registered topology."""

    clear_width_m: float = Field(
        ge=MIN_CROSS_SLOPE_ALLEY_WIDTH_M,
        allow_inf_nan=False,
    )
    slope_pct: float = Field(gt=0.0, le=8.3, allow_inf_nan=False)
    connects_to_topology: Literal["path-network-002", "path-network-003"]


class CentralCourtyardDownhillRecipe(FrozenModel):
    module_id: Literal["central-courtyard-downhill"] = "central-courtyard-downhill"
    bound_object_id: Literal["courtyard-public-002"]
    bound_path_networks: tuple[
        Literal["path-network-002", "path-network-003"],
        ...,
    ] = Field(min_length=2, max_length=2)
    downhill_gate: CourtyardDownhillGateSpec
    covered_side_passage: CourtyardCoveredSidePassageSpec
    cross_slope_alley: CourtyardCrossSlopeAlleySpec
    gallery_post_run_material_slot_id: PartId
    gallery_guard_material_slot_id: PartId

    @model_validator(mode="after")
    def _attachments_unique(self) -> CentralCourtyardDownhillRecipe:
        if len(set(self.bound_path_networks)) != len(self.bound_path_networks):
            raise ValueError("bound path networks must be unique")
        return self


# --------------------------------------------------------------------------- #
# 2. Bridge deck standing-eye crossing.
# --------------------------------------------------------------------------- #


class BridgeAccessRampSpec(FrozenModel):
    """At least one ramp alternative to stairs at the bridge end."""

    clear_width_m: float = Field(
        ge=MIN_BRIDGE_ACCESS_RAMP_WIDTH_M,
        allow_inf_nan=False,
    )
    slope_pct: float = Field(gt=0.0, le=MAX_BRIDGE_RAMP_SLOPE_PCT, allow_inf_nan=False)
    continuous_collision: Literal[True] = True


class BridgeRouteAttachmentSpec(FrozenModel):
    """One end of the bridge route attachment to the registered path network."""

    upstream_or_downstream: Literal["upstream", "downstream"]
    connects_to_topology: Literal["path-network-001", "path-network-005"]
    height_continuous: Literal[True] = True
    width_continuous: Literal[True] = True
    normal_continuous: Literal[True] = True


class BridgeDeckCrossingRecipe(FrozenModel):
    module_id: Literal["bridge-deck-crossing"] = "bridge-deck-crossing"
    bound_bridge_object_id: Literal["bridge-lower-001"]
    bound_path_networks: tuple[
        Literal["path-network-001", "path-network-005"],
        ...,
    ] = Field(min_length=2, max_length=2)
    upstream_attachment: BridgeRouteAttachmentSpec
    downstream_attachment: BridgeRouteAttachmentSpec
    access_ramp: BridgeAccessRampSpec
    side_maintenance_path_present: Literal[True] = True
    drainage_scuppers_present: Literal[True] = True
    deck_edge_transition_present: Literal[True] = True

    @model_validator(mode="after")
    def _attachments_unique(self) -> BridgeDeckCrossingRecipe:
        if len(set(self.bound_path_networks)) != len(self.bound_path_networks):
            raise ValueError("bound path networks must be unique")
        if self.upstream_attachment.upstream_or_downstream != "upstream":
            raise ValueError("upstream attachment must declare upstream")
        if self.downstream_attachment.upstream_or_downstream != "downstream":
            raise ValueError("downstream attachment must declare downstream")
        return self


# --------------------------------------------------------------------------- #
# 3. Watermill tailrace maintenance route.
# --------------------------------------------------------------------------- #


class WatermillMaintenancePlatformSpec(FrozenModel):
    """Maintenance platform with independent walkable topology."""

    clear_width_m: float = Field(
        ge=MIN_MAINTENANCE_PLATFORM_WIDTH_M,
        allow_inf_nan=False,
    )
    connects_to_creek_bank_path: Literal[True] = True
    reaches_wheel_axle_access_panel: Literal[True] = True
    wheel_clearance_not_penetrating: Literal[True] = True


class WatermillServiceStairSpec(FrozenModel):
    """Service stair from bank to platform."""

    tread_count: int = Field(ge=3)
    tread_depth_m: float = Field(gt=0.25, allow_inf_nan=False)
    slope_pct: float = Field(
        gt=0.0,
        le=MAX_SERVICE_STAIR_SLOPE_PCT,
        allow_inf_nan=False,
    )
    continuous_collision: Literal[True] = True


class WatermillTailraceRecipe(FrozenModel):
    module_id: Literal["watermill-tailrace"] = "watermill-tailrace"
    bound_waterwheel_part_ids: tuple[
        Literal[
            "waterwheel-wheel-001",
            "waterwheel-axle-001",
            "waterwheel-bracket-001",
            "waterwheel-millrace-001",
            "waterwheel-spill-001",
            "waterwheel-tailwater-001",
        ],
        ...,
    ] = Field(min_length=6, max_length=6)
    bound_creek_object_id: Literal["creek-main-001"]
    bound_path_network: Literal["path-network-001"]
    building_shell_present: Literal[True] = True
    maintenance_platform: WatermillMaintenancePlatformSpec
    service_stair: WatermillServiceStairSpec
    access_panel_independent_identity: Literal[True] = True
    tailrace_retaining_wall_present: Literal[True] = True

    @model_validator(mode="after")
    def _waterwheel_ids_unique(self) -> WatermillTailraceRecipe:
        if len(set(self.bound_waterwheel_part_ids)) != len(self.bound_waterwheel_part_ids):
            raise ValueError("bound waterwheel part IDs must be unique")
        return self


# --------------------------------------------------------------------------- #
# 4. Covered gallery cross-level underpass.
# --------------------------------------------------------------------------- #


class GalleryUnderpassLowerLaneSpec(FrozenModel):
    """Lower-lane walkable topology edge, separate from upper gallery."""

    clear_height_m: float = Field(
        ge=MIN_GALLERY_UNDERPASS_CLEAR_HEIGHT_M,
        allow_inf_nan=False,
    )
    clear_width_m: float = Field(
        ge=MIN_GALLERY_UNDERPASS_CLEAR_WIDTH_M,
        allow_inf_nan=False,
    )
    column_collision_probed: Literal[True] = True


class GalleryBranchAttachmentSpec(FrozenModel):
    """One of three gallery exit attachments (upper/lower/side)."""

    branch: Literal["upper", "lower", "side"]
    connects_to_topology: Literal[
        "path-network-002",
        "path-network-003",
        "path-network-005",
    ]
    topology_node_explicit: Literal[True] = True


class CoveredGalleryUnderpassRecipe(FrozenModel):
    module_id: Literal["covered-gallery-underpass"] = "covered-gallery-underpass"
    bound_gallery_object_id: Literal["covered-timber-gallery-v1"]
    bound_passage_object_id: Literal["cross-level-covered-passage-v1"]
    lower_lane: GalleryUnderpassLowerLaneSpec
    upper_branch: GalleryBranchAttachmentSpec
    lower_branch: GalleryBranchAttachmentSpec
    side_branch: GalleryBranchAttachmentSpec
    post_beam_foundation_declared: Literal[True] = True
    guard_run_declared: Literal[True] = True
    side_door_present: Literal[True] = True

    @model_validator(mode="after")
    def _branches_unique(self) -> CoveredGalleryUnderpassRecipe:
        branches = (
            self.upper_branch.branch,
            self.lower_branch.branch,
            self.side_branch.branch,
        )
        if len(set(branches)) != len(branches):
            raise ValueError("gallery branch attachments must be unique")
        if self.upper_branch.branch != "upper":
            raise ValueError("upper_branch must declare branch=upper")
        if self.lower_branch.branch != "lower":
            raise ValueError("lower_branch must declare branch=lower")
        if self.side_branch.branch != "side":
            raise ValueError("side_branch must declare branch=side")
        return self


# --------------------------------------------------------------------------- #
# 5. Forest/orchard boundary return route.
# --------------------------------------------------------------------------- #


class ForestPathForkSpec(FrozenModel):
    """Path fork where the boundary route splits into two branches."""

    branch_count: Literal[2] = 2
    both_branches_close_in_baked_topology: Literal[True] = True


class ForestRetainingDrainSpec(FrozenModel):
    """Retaining drain that must not conflict with walkable surface."""

    not_crossing_walkable_surface: Literal[True] = True
    continuous_collision: Literal[True] = True


class ForestOrchardBoundaryRecipe(FrozenModel):
    module_id: Literal["forest-orchard-boundary"] = "forest-orchard-boundary"
    bound_orchard_object_ids: tuple[
        Literal["orchard-slope-001", "orchard-slope-002"],
        ...,
    ] = Field(min_length=2, max_length=2)
    bound_path_network: Literal["path-network-002"]
    path_fork: ForestPathForkSpec
    orchard_transition_present: Literal[True] = True
    retaining_drain: ForestRetainingDrainSpec
    trail_shelter_clear_width_m: float = Field(
        ge=MIN_TRAIL_SHELTER_CLEAR_WIDTH_M,
        allow_inf_nan=False,
    )
    inbound_route_attachment_present: Literal[True] = True
    outbound_route_attachment_present: Literal[True] = True
    edge_vegetation_band_is_replaceable_instance: Literal[True] = True
    vegetation_band_not_in_geometry_trust: Literal[True] = True

    @model_validator(mode="after")
    def _orchard_ids_unique(self) -> ForestOrchardBoundaryRecipe:
        if len(set(self.bound_orchard_object_ids)) != len(self.bound_orchard_object_ids):
            raise ValueError("bound orchard object IDs must be unique")
        return self


# --------------------------------------------------------------------------- #
# 6. Lower valley uphill reciprocal route.
# --------------------------------------------------------------------------- #


class LowerValleyTrailSpec(FrozenModel):
    """Lower valley trail segment with minimum width."""

    clear_width_m: float = Field(
        ge=MIN_LOWER_VALLEY_TRAIL_WIDTH_M,
        allow_inf_nan=False,
    )
    continuous_collision: Literal[True] = True


class LowerValleyRetainingStepSpec(FrozenModel):
    """Retaining step that mediates elevation change on the uphill route."""

    max_rise_m: float = Field(
        gt=0.0,
        le=MAX_RETAINING_STEP_RISE_M,
        allow_inf_nan=False,
    )
    continuous_collision: Literal[True] = True


class LowerValleyUphillRecipe(FrozenModel):
    module_id: Literal["lower-valley-uphill"] = "lower-valley-uphill"
    bound_path_network: Literal["path-network-001"]
    bound_creek_object_id: Literal["creek-main-001"]
    entry_path: LowerValleyTrailSpec
    field_edge_path: LowerValleyTrailSpec
    creek_maintenance_trail: LowerValleyTrailSpec
    drainage_outlet_present: Literal[True] = True
    building_back_entry_present: Literal[True] = True
    route_reconnection_to_village_body: Literal[True] = True
    route_reconnection_to_bridge_or_watermill: Literal[True] = True
    retaining_step: LowerValleyRetainingStepSpec


# --------------------------------------------------------------------------- #
# Module wrapper.
# --------------------------------------------------------------------------- #


class PartLayoutSpec(FrozenModel):
    """Canonical spatial placement of one reciprocal-route part.

    Coordinates are scene-local meters relative to the scene origin.
    The Blender runtime script must consume these fields verbatim from
    the canonical request; it may NOT invent its own layout, spacing,
    extent, or orientation (HANDOFF-OPUS-009 Phase 4.1, responding to
    REVIEW-CODEX-018 §"Phase 4 必须处理的边界" item 1).

    The layout is ``modeled-unverified``: it carries honest placement
    for the simplified box geometry, not measured survey coordinates.
    """

    center_m: tuple[float, float, float]
    extent_m: tuple[float, float, float]
    orientation_deg: float = Field(ge=0.0, lt=360.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _layout_is_finite_and_positive(self) -> PartLayoutSpec:
        if len(self.center_m) != 3:
            raise ValueError("center_m must be a 3-tuple")
        if len(self.extent_m) != 3:
            raise ValueError("extent_m must be a 3-tuple")
        for axis, value in zip(("x", "y", "z"), self.center_m, strict=True):
            if not math.isfinite(value):
                raise ValueError(f"center_m {axis} must be finite")
        for axis, value in zip(("x", "y", "z"), self.extent_m, strict=True):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"extent_m {axis} must be positive and finite")
        return self


class ReciprocalRouteModulePart(FrozenModel):
    """One stable part declared by a reciprocal-route module.

    Every part carries an instance id in the module's locked segment, a
    part id, a semantic id, a material slot id, and a canonical spatial
    layout.  Unknown fields stay unknown; no inference is made from names.
    """

    module_id: ModuleId
    part_id: PartId
    instance_id: int
    semantic_id: int = Field(ge=0, le=14)
    material_slot_id: PartId
    part_layout: PartLayoutSpec

    @model_validator(mode="after")
    def _instance_in_module_segment(self) -> ReciprocalRouteModulePart:
        expected_range = _module_instance_range(self.module_id)
        if self.instance_id not in expected_range:
            raise ValueError(
                f"part {self.part_id} instance {self.instance_id} is outside "
                f"module {self.module_id} segment "
                f"[{expected_range.start}, {expected_range.stop - 1}]",
            )
        return self


def _module_instance_range(module_id: ModuleId) -> range:
    if module_id == "central-courtyard-downhill":
        return CENTRAL_DOWNHILL_INSTANCE_RANGE
    if module_id == "bridge-deck-crossing":
        return BRIDGE_CROSSING_INSTANCE_RANGE
    if module_id == "watermill-tailrace":
        return WATERMILL_TAILRACE_INSTANCE_RANGE
    if module_id == "covered-gallery-underpass":
        return GALLERY_UNDERPASS_INSTANCE_RANGE
    if module_id == "forest-orchard-boundary":
        return FOREST_BOUNDARY_INSTANCE_RANGE
    return LOWER_VALLEY_UPHILL_INSTANCE_RANGE


def _module_batch8_source(module_id: ModuleId) -> str:
    return {
        "central-courtyard-downhill": BATCH8_CENTRAL_COURTYARD_DOWNHILL_SHA256,
        "bridge-deck-crossing": BATCH8_BRIDGE_DECK_CROSSING_SHA256,
        "watermill-tailrace": BATCH8_WATERMILL_TAILRACE_SHA256,
        "covered-gallery-underpass": BATCH8_COVERED_GALLERY_UNDERPASS_SHA256,
        "forest-orchard-boundary": BATCH8_FOREST_ORCHARD_BOUNDARY_SHA256,
        "lower-valley-uphill": BATCH8_LOWER_VALLEY_UPHILL_SHA256,
    }[module_id]


def _module_batch9_source(module_id: ModuleId) -> str:
    return {
        "central-courtyard-downhill": BATCH9_CENTRAL_COURTYARD_LATERAL_SHA256,
        "bridge-deck-crossing": BATCH9_BRIDGE_DOWNSTREAM_BANK_SHA256,
        "watermill-tailrace": BATCH9_WATERMILL_OPPOSITE_BANK_SHA256,
        "covered-gallery-underpass": BATCH9_COVERED_GALLERY_LOWER_LANE_SHA256,
        "forest-orchard-boundary": BATCH9_FOREST_ORCHARD_LATERAL_FORK_SHA256,
        "lower-valley-uphill": BATCH9_LOWER_VALLEY_FIELD_EDGE_SHA256,
    }[module_id]


def _default_part_layout(
    module_id: ModuleId,
    instance_id: int,
) -> PartLayoutSpec:
    """Build the canonical default layout for one part.

    Preserves the exact spatial layout that Phase 3 hardcoded inside
    ``apply_reciprocal_route_modules.MODULE_BASE_POSITION`` so the AABB
    reported by REVIEW-CODEX-018 stays identical.  The runtime script
    now reads these values verbatim from the plan instead of inventing
    them.
    """

    base_x, base_y, base_z = _DEFAULT_MODULE_BASE_POSITION[module_id]
    offset_y = (instance_id - 176) * _DEFAULT_PART_SPACING_Y_M
    return PartLayoutSpec(
        center_m=(base_x, base_y + offset_y, base_z),
        extent_m=_DEFAULT_PART_EXTENT_M,
        orientation_deg=_DEFAULT_PART_ORIENTATION_DEG,
    )


Recipe = (
    CentralCourtyardDownhillRecipe
    | BridgeDeckCrossingRecipe
    | WatermillTailraceRecipe
    | CoveredGalleryUnderpassRecipe
    | ForestOrchardBoundaryRecipe
    | LowerValleyUphillRecipe
)


class ReciprocalRouteModule(FrozenModel):
    module_id: ModuleId
    recipe_version: Literal["v1"] = RECIPROCAL_ROUTE_RECIPE_VERSION
    batch8_design_source_sha256: Sha256
    batch9_design_source_sha256: Sha256
    parts: tuple[ReciprocalRouteModulePart, ...] = Field(min_length=1)
    recipe: Annotated[Recipe, Field(discriminator="module_id")]

    @model_validator(mode="after")
    def _recipe_matches_module(self) -> ReciprocalRouteModule:
        if self.recipe.module_id != self.module_id:
            raise ValueError("recipe module id disagrees with module wrapper")
        if self.batch8_design_source_sha256 != _module_batch8_source(self.module_id):
            raise ValueError(
                f"batch8 design source SHA-256 disagrees with module {self.module_id}",
            )
        if self.batch9_design_source_sha256 != _module_batch9_source(self.module_id):
            raise ValueError(
                f"batch9 design source SHA-256 disagrees with module {self.module_id}",
            )
        instance_ids = tuple(part.instance_id for part in self.parts)
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("module part instance IDs must be unique")
        part_ids = tuple(part.part_id for part in self.parts)
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("module part IDs must be unique")
        return self


# --------------------------------------------------------------------------- #
# Top-level plan.
# --------------------------------------------------------------------------- #


class ReciprocalRouteModuleSummary(FrozenModel):
    module_count: Literal[6] = 6
    part_count: int = Field(ge=1)
    instance_id_segment_start: Literal[176] = 176
    instance_id_segment_end: Literal[218] = 218


class ReciprocalRouteModulePlan(FrozenModel):
    """Additive, content-addressed reciprocal-route module plan.

    Binds ``ScenePlan`` SHA-256, ``ElevatedTopologyPlan`` SHA-256,
    ``EnvironmentModulePlan`` v1 SHA-256, Batch 8 + Batch 9 Release
    manifest SHA-256s, archive SHA-256s, and the recipe version.
    Replacing any module changes ``module_plan_sha256`` and therefore
    the build request, object registry, and downstream render identity.

    This plan does NOT promote ``modeled-unverified`` trust: every module
    keeps ``synthetic=true``, ``geometry_usability=preview-only``,
    ``verification_level=L0``, ``trust_effect=none``.
    """

    schema_version: Literal["nantai.synthetic-village.reciprocal-route-module.v1"] = (
        RECIPROCAL_ROUTE_SCHEMA
    )
    plan_id: Literal["synthetic-village-reciprocal-route-module-v1"] = (
        "synthetic-village-reciprocal-route-module-v1"
    )
    recipe_version: Literal["v1"] = RECIPROCAL_ROUTE_RECIPE_VERSION
    scene_plan_sha256: Sha256
    elevated_topology_sha256: Sha256
    environment_module_plan_sha256: Sha256
    batch8_release_manifest_sha256: Literal[BATCH8_RELEASE_MANIFEST_SHA256] = (
        BATCH8_RELEASE_MANIFEST_SHA256
    )
    batch8_archive_sha256: Literal[BATCH8_ARCHIVE_SHA256] = BATCH8_ARCHIVE_SHA256
    batch9_release_manifest_sha256: Literal[BATCH9_RELEASE_MANIFEST_SHA256] = (
        BATCH9_RELEASE_MANIFEST_SHA256
    )
    batch9_archive_sha256: Literal[BATCH9_ARCHIVE_SHA256] = BATCH9_ARCHIVE_SHA256
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"
    verification_level: Literal["L0"] = "L0"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = "simplified-pbr-not-render-parity"
    trust_effect: Literal["none"] = "none"
    modules: tuple[ReciprocalRouteModule, ...] = Field(min_length=6, max_length=6)
    summary: ReciprocalRouteModuleSummary

    @model_validator(mode="after")
    def _modules_are_exact_and_ordered(self) -> ReciprocalRouteModulePlan:
        module_ids = tuple(module.module_id for module in self.modules)
        expected = (
            "central-courtyard-downhill",
            "bridge-deck-crossing",
            "watermill-tailrace",
            "covered-gallery-underpass",
            "forest-orchard-boundary",
            "lower-valley-uphill",
        )
        if module_ids != expected:
            raise ValueError(
                "reciprocal-route modules must be exactly the ordered six",
            )
        # Instance IDs are partitioned across modules -- no overlaps.
        all_instances: list[int] = []
        all_part_ids: list[str] = []
        for module in self.modules:
            for part in module.parts:
                all_instances.append(part.instance_id)
                all_part_ids.append(part.part_id)
        if len(set(all_instances)) != len(all_instances):
            raise ValueError(
                "reciprocal-route module part instance IDs must not overlap",
            )
        if len(set(all_part_ids)) != len(all_part_ids):
            raise ValueError(
                "reciprocal-route module part IDs must be unique across the plan",
            )
        # The full instance segment is exactly 176..218.
        expected_segment = set(range(176, 219))
        if set(all_instances) != expected_segment:
            raise ValueError(
                "reciprocal-route module parts must collectively occupy exactly "
                "the 176..218 instance segment",
            )
        if self.summary.part_count != len(all_instances):
            raise ValueError(
                "reciprocal-route module summary part_count disagrees with modules",
            )
        return self


def canonical_reciprocal_route_module_plan_bytes(
    plan: ReciprocalRouteModulePlan,
) -> bytes:
    return _canonical(plan.model_dump(mode="json"))


def reciprocal_route_module_plan_sha256(plan: ReciprocalRouteModulePlan) -> str:
    return hashlib.sha256(
        canonical_reciprocal_route_module_plan_bytes(plan),
    ).hexdigest()


def verify_reciprocal_route_module_plan(
    plan: ReciprocalRouteModulePlan,
    *,
    scene: ScenePlan,
    elevated_topology: ElevatedTopologyPlan,
    environment_module_plan: EnvironmentModulePlan,
) -> None:
    """Re-bind every identity; raise on any mismatch."""

    expected_scene_sha = hashlib.sha256(
        canonical_scene_plan_bytes(scene),
    ).hexdigest()
    if plan.scene_plan_sha256 != expected_scene_sha:
        raise ReciprocalRouteError(
            "reciprocal-route module plan scene_plan_sha256 disagrees with scene",
        )
    expected_topology_sha = hashlib.sha256(
        canonical_elevated_topology_bytes(elevated_topology),
    ).hexdigest()
    if plan.elevated_topology_sha256 != expected_topology_sha:
        raise ReciprocalRouteError(
            "reciprocal-route module plan elevated_topology_sha256 disagrees with topology",
        )
    expected_env_module_sha = hashlib.sha256(
        canonical_environment_module_plan_bytes(environment_module_plan),
    ).hexdigest()
    if plan.environment_module_plan_sha256 != expected_env_module_sha:
        raise ReciprocalRouteError(
            "reciprocal-route module plan environment_module_plan_sha256 disagrees with v1 plan",
        )
    # Re-validate canonical bytes (re-runs every model_validator).
    revalidated = ReciprocalRouteModulePlan.model_validate_json(
        canonical_reciprocal_route_module_plan_bytes(plan),
    )
    if revalidated != plan:
        raise ReciprocalRouteError(
            "reciprocal-route module plan is not canonical JSON",
        )


# --------------------------------------------------------------------------- #
# Default recipe builders.
# --------------------------------------------------------------------------- #


def _default_central_courtyard_downhill_recipe() -> CentralCourtyardDownhillRecipe:
    return CentralCourtyardDownhillRecipe(
        bound_object_id="courtyard-public-002",
        bound_path_networks=("path-network-002", "path-network-003"),
        downhill_gate=CourtyardDownhillGateSpec(
            clear_width_m=2.0,
            connects_to_topology="path-network-003",
            threshold_coplanar_with_paving=True,
        ),
        covered_side_passage=CourtyardCoveredSidePassageSpec(
            clear_height_m=2.5,
            clear_width_m=1.8,
            connects_to_topology="path-network-002",
            drainage_channel_not_blocking=True,
        ),
        cross_slope_alley=CourtyardCrossSlopeAlleySpec(
            clear_width_m=1.8,
            slope_pct=6.0,
            connects_to_topology="path-network-003",
        ),
        gallery_post_run_material_slot_id="material-courtyard-timber-01",
        gallery_guard_material_slot_id="material-service-iron-01",
    )


def _default_bridge_deck_crossing_recipe() -> BridgeDeckCrossingRecipe:
    return BridgeDeckCrossingRecipe(
        bound_bridge_object_id="bridge-lower-001",
        bound_path_networks=("path-network-001", "path-network-005"),
        upstream_attachment=BridgeRouteAttachmentSpec(
            upstream_or_downstream="upstream",
            connects_to_topology="path-network-001",
            height_continuous=True,
            width_continuous=True,
            normal_continuous=True,
        ),
        downstream_attachment=BridgeRouteAttachmentSpec(
            upstream_or_downstream="downstream",
            connects_to_topology="path-network-005",
            height_continuous=True,
            width_continuous=True,
            normal_continuous=True,
        ),
        access_ramp=BridgeAccessRampSpec(
            clear_width_m=2.6,
            slope_pct=7.0,
            continuous_collision=True,
        ),
        side_maintenance_path_present=True,
        drainage_scuppers_present=True,
        deck_edge_transition_present=True,
    )


def _default_watermill_tailrace_recipe() -> WatermillTailraceRecipe:
    return WatermillTailraceRecipe(
        bound_waterwheel_part_ids=(
            "waterwheel-wheel-001",
            "waterwheel-axle-001",
            "waterwheel-bracket-001",
            "waterwheel-millrace-001",
            "waterwheel-spill-001",
            "waterwheel-tailwater-001",
        ),
        bound_creek_object_id="creek-main-001",
        bound_path_network="path-network-001",
        building_shell_present=True,
        maintenance_platform=WatermillMaintenancePlatformSpec(
            clear_width_m=1.5,
            connects_to_creek_bank_path=True,
            reaches_wheel_axle_access_panel=True,
            wheel_clearance_not_penetrating=True,
        ),
        service_stair=WatermillServiceStairSpec(
            tread_count=5,
            tread_depth_m=0.30,
            slope_pct=32.0,
            continuous_collision=True,
        ),
        access_panel_independent_identity=True,
        tailrace_retaining_wall_present=True,
    )


def _default_covered_gallery_underpass_recipe() -> CoveredGalleryUnderpassRecipe:
    return CoveredGalleryUnderpassRecipe(
        bound_gallery_object_id="covered-timber-gallery-v1",
        bound_passage_object_id="cross-level-covered-passage-v1",
        lower_lane=GalleryUnderpassLowerLaneSpec(
            clear_height_m=2.5,
            clear_width_m=2.0,
            column_collision_probed=True,
        ),
        upper_branch=GalleryBranchAttachmentSpec(
            branch="upper",
            connects_to_topology="path-network-002",
            topology_node_explicit=True,
        ),
        lower_branch=GalleryBranchAttachmentSpec(
            branch="lower",
            connects_to_topology="path-network-005",
            topology_node_explicit=True,
        ),
        side_branch=GalleryBranchAttachmentSpec(
            branch="side",
            connects_to_topology="path-network-003",
            topology_node_explicit=True,
        ),
        post_beam_foundation_declared=True,
        guard_run_declared=True,
        side_door_present=True,
    )


def _default_forest_orchard_boundary_recipe() -> ForestOrchardBoundaryRecipe:
    return ForestOrchardBoundaryRecipe(
        bound_orchard_object_ids=("orchard-slope-001", "orchard-slope-002"),
        bound_path_network="path-network-002",
        path_fork=ForestPathForkSpec(
            branch_count=2,
            both_branches_close_in_baked_topology=True,
        ),
        orchard_transition_present=True,
        retaining_drain=ForestRetainingDrainSpec(
            not_crossing_walkable_surface=True,
            continuous_collision=True,
        ),
        trail_shelter_clear_width_m=1.8,
        inbound_route_attachment_present=True,
        outbound_route_attachment_present=True,
        edge_vegetation_band_is_replaceable_instance=True,
        vegetation_band_not_in_geometry_trust=True,
    )


def _default_lower_valley_uphill_recipe() -> LowerValleyUphillRecipe:
    return LowerValleyUphillRecipe(
        bound_path_network="path-network-001",
        bound_creek_object_id="creek-main-001",
        entry_path=LowerValleyTrailSpec(
            clear_width_m=1.5,
            continuous_collision=True,
        ),
        field_edge_path=LowerValleyTrailSpec(
            clear_width_m=1.5,
            continuous_collision=True,
        ),
        creek_maintenance_trail=LowerValleyTrailSpec(
            clear_width_m=1.3,
            continuous_collision=True,
        ),
        drainage_outlet_present=True,
        building_back_entry_present=True,
        route_reconnection_to_village_body=True,
        route_reconnection_to_bridge_or_watermill=True,
        retaining_step=LowerValleyRetainingStepSpec(
            max_rise_m=0.15,
            continuous_collision=True,
        ),
    )


def _default_module(module_id: ModuleId) -> ReciprocalRouteModule:
    if module_id == "central-courtyard-downhill":
        recipe = _default_central_courtyard_downhill_recipe()
        part_specs = (
            (
                "courtyard-downhill-gate-001",
                176,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
            (
                "courtyard-covered-side-passage-001",
                177,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-courtyard-timber-01",
            ),
            (
                "courtyard-cross-slope-alley-001",
                178,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
            (
                "courtyard-route-attachment-upper-001",
                179,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
            (
                "courtyard-route-attachment-lower-001",
                180,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
            (
                "courtyard-gallery-post-run-001",
                181,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-courtyard-timber-01",
            ),
            (
                "courtyard-gallery-guard-001",
                182,
                SEMANTIC_ID_BY_CLASS["prop"],
                "material-service-iron-01",
            ),
        )
    elif module_id == "bridge-deck-crossing":
        recipe = _default_bridge_deck_crossing_recipe()
        part_specs = (
            (
                "bridge-route-attachment-upstream-001",
                183,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "bridge-route-attachment-downstream-001",
                184,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "bridge-access-ramp-001",
                185,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "bridge-side-maintenance-path-001",
                186,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "bridge-drainage-scuppers-001",
                187,
                SEMANTIC_ID_BY_CLASS["creek"],
                "material-stone-block-01",
            ),
            (
                "bridge-deck-edge-transition-001",
                188,
                SEMANTIC_ID_BY_CLASS["bridge"],
                "material-stone-block-01",
            ),
        )
    elif module_id == "watermill-tailrace":
        recipe = _default_watermill_tailrace_recipe()
        part_specs = (
            (
                "watermill-building-shell-001",
                189,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-waterwheel-wood-01",
            ),
            (
                "watermill-maintenance-platform-001",
                190,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-waterwheel-wood-01",
            ),
            (
                "watermill-service-stair-001",
                191,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-waterwheel-wood-01",
            ),
            (
                "watermill-access-panel-001",
                192,
                SEMANTIC_ID_BY_CLASS["prop"],
                "material-waterwheel-iron-01",
            ),
            (
                "watermill-creek-bank-path-001",
                193,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-creek-stone-01",
            ),
            (
                "watermill-platform-guard-001",
                194,
                SEMANTIC_ID_BY_CLASS["prop"],
                "material-waterwheel-iron-01",
            ),
            (
                "watermill-tailrace-retaining-wall-001",
                195,
                SEMANTIC_ID_BY_CLASS["retaining-wall"],
                "material-stone-block-01",
            ),
        )
    elif module_id == "covered-gallery-underpass":
        recipe = _default_covered_gallery_underpass_recipe()
        part_specs = (
            (
                "gallery-underpass-lower-lane-001",
                196,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-timber-01",
            ),
            (
                "gallery-post-run-001",
                197,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-courtyard-timber-01",
            ),
            (
                "gallery-beam-run-001",
                198,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-courtyard-timber-01",
            ),
            (
                "gallery-foundation-run-001",
                199,
                SEMANTIC_ID_BY_CLASS["retaining-wall"],
                "material-stone-block-01",
            ),
            (
                "gallery-guard-run-001",
                200,
                SEMANTIC_ID_BY_CLASS["prop"],
                "material-service-iron-01",
            ),
            (
                "gallery-side-door-001",
                201,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-courtyard-timber-01",
            ),
            (
                "gallery-branch-attachment-upper-001",
                202,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
            (
                "gallery-branch-attachment-lower-001",
                203,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
            (
                "gallery-branch-attachment-side-001",
                204,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-courtyard-stone-01",
            ),
        )
    elif module_id == "forest-orchard-boundary":
        recipe = _default_forest_orchard_boundary_recipe()
        part_specs = (
            (
                "forest-boundary-path-fork-001",
                205,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "forest-orchard-transition-001",
                206,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "forest-retaining-drain-001",
                207,
                SEMANTIC_ID_BY_CLASS["retaining-wall"],
                "material-stone-block-01",
            ),
            (
                "forest-trail-shelter-001",
                208,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-courtyard-timber-01",
            ),
            (
                "forest-route-attachment-inbound-001",
                209,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "forest-route-attachment-outbound-001",
                210,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-stone-block-01",
            ),
            (
                "forest-edge-vegetation-band-001",
                211,
                SEMANTIC_ID_BY_CLASS["prop"],
                "material-water-01",
            ),
        )
    else:  # lower-valley-uphill
        recipe = _default_lower_valley_uphill_recipe()
        part_specs = (
            (
                "lower-valley-entry-path-001",
                212,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-creek-stone-01",
            ),
            (
                "lower-valley-field-edge-path-001",
                213,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-creek-stone-01",
            ),
            (
                "lower-valley-creek-maintenance-trail-001",
                214,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-creek-stone-01",
            ),
            (
                "lower-valley-drainage-outlet-001",
                215,
                SEMANTIC_ID_BY_CLASS["creek"],
                "material-stone-block-01",
            ),
            (
                "lower-valley-building-back-entry-001",
                216,
                SEMANTIC_ID_BY_CLASS["building"],
                "material-waterwheel-wood-01",
            ),
            (
                "lower-valley-route-reconnection-001",
                217,
                SEMANTIC_ID_BY_CLASS["path"],
                "material-creek-stone-01",
            ),
            (
                "lower-valley-retaining-step-001",
                218,
                SEMANTIC_ID_BY_CLASS["retaining-wall"],
                "material-stone-block-01",
            ),
        )
    parts = tuple(
        ReciprocalRouteModulePart(
            module_id=module_id,
            part_id=part_id,
            instance_id=instance_id,
            semantic_id=semantic_id,
            material_slot_id=material_slot_id,
            part_layout=_default_part_layout(module_id, instance_id),
        )
        for part_id, instance_id, semantic_id, material_slot_id in part_specs
    )
    return ReciprocalRouteModule(
        module_id=module_id,
        batch8_design_source_sha256=_module_batch8_source(module_id),
        batch9_design_source_sha256=_module_batch9_source(module_id),
        parts=parts,
        recipe=recipe,
    )


def build_default_reciprocal_route_module_plan(
    *,
    scene: ScenePlan,
    elevated_topology: ElevatedTopologyPlan,
    environment_module_plan: EnvironmentModulePlan,
) -> ReciprocalRouteModulePlan:
    """Build the canonical default plan bound to the given scene + topology + v1 plan."""

    scene_sha = hashlib.sha256(
        canonical_scene_plan_bytes(scene),
    ).hexdigest()
    topology_sha = hashlib.sha256(
        canonical_elevated_topology_bytes(elevated_topology),
    ).hexdigest()
    env_module_sha = hashlib.sha256(
        canonical_environment_module_plan_bytes(environment_module_plan),
    ).hexdigest()
    modules = tuple(
        _default_module(module_id)
        for module_id in (
            "central-courtyard-downhill",
            "bridge-deck-crossing",
            "watermill-tailrace",
            "covered-gallery-underpass",
            "forest-orchard-boundary",
            "lower-valley-uphill",
        )
    )
    plan = ReciprocalRouteModulePlan(
        scene_plan_sha256=scene_sha,
        elevated_topology_sha256=topology_sha,
        environment_module_plan_sha256=env_module_sha,
        modules=modules,
        summary=ReciprocalRouteModuleSummary(
            part_count=sum(len(module.parts) for module in modules),
        ),
    )
    return plan.model_copy(
        update={},  # trigger re-validation
    )
