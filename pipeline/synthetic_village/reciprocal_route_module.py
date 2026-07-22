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
from .production_profile import (
    CameraGroupId,
    ProductionCameraPlan,
    ProductionCameraPose,
    _pose,
    canonical_production_plan_bytes,
    production_camera_registry_digest,
)
from .scene_plan import (
    SEMANTIC_ORDER,
    ScenePlan,
    canonical_scene_plan_bytes,
    terrain_height_m,
)

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

#: Default XY anchors plus legacy Z seeds for the simplified v1 build.
#: Used only by ``_default_part_layout`` to populate ``PartLayoutSpec``;
#: the Blender runtime script never reads these constants.  Any change
#: here changes ``reciprocal_route_module_plan_sha256`` and therefore
#: ``build_id`` and the downstream render identity.
#: Non-central Z is no longer read from the third tuple item. A flat module
#: floor is derived from the maximum analytic terrain height across its exact
#: part run plus ``_NONCENTRAL_FLOOR_CLEARANCE_M``. This preserves the <=12%
#: flat-route slope contract without leaving forest/lower-valley geometry
#: buried or bridge/watermill/gallery geometry at arbitrary elevations.
#:   * part extent z 0.6 -> 2.5: the previous 0.6 m solid box gave only
#:     0.3 m upward clearance (probe measured clearance_min_m ~0.3).  The
#:     new 2.5 m extent, combined with the 5-panel passage geometry in
#:     ``_module_geometry``, gives an upward clearance of ~2.475 m which
#:     is >= ``MIN_ROUTE_CLEARANCE_M = 2.4``.  The part_layout.center_m.z
#:     remains the passage floor; the passage rises from center_m.z to
#:     center_m.z + extent_m.z.
_DEFAULT_MODULE_BASE_POSITION: dict[ModuleId, tuple[float, float, float]] = {
    "central-courtyard-downhill": (40.0, 30.0, 70.0),
    "bridge-deck-crossing": (-155.0, -100.0, 55.0),  # GLM-P0 Step 2: -150 -> -155
    "watermill-tailrace": (-180.0, -130.0, 52.0),
    "covered-gallery-underpass": (57.0, -25.0, 78.0),
    "forest-orchard-boundary": (120.0, 30.0, 75.0),
    "lower-valley-uphill": (-90.0, -127.5, 55.0),
}
_DEFAULT_PART_SPACING_Y_M = 2.5
#: Passage extent: x/y are the outer bounding box of the passage
#: (walls included).  z is the passage height (floor to ceiling).  The
#: runtime's ``_module_geometry`` (Phase 4.3) decomposes this extent into
#: 4 panels: floor, ceiling, left wall, right wall (no front/back wall --
#: the route passes through along y).  Inner width = x - 2 *
#: _PASSAGE_WALL_THICKNESS_M (0.1 m on each side) = 1.4 m >=
#: MIN_ROUTE_CLEAR_WIDTH_M = 1.2.  Inner height = z + 2 *
#: _PASSAGE_RAY_SAFE_GAP_M (0.001 m gap above and below so the upward
#: ray origin is not on the floor surface) = 2.502 m >=
#: MIN_ROUTE_CLEARANCE_M = 2.4.  Upward ray from part center hits the
#: ceiling underside at distance z + gap = 2.501 m >= 2.4.
#:
#: Phase 4.3 amendment (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md
#: §"待处理" item: "perpendicular ray missed"): the original extent_y
#: 1.6 m < spacing_y 2.5 m left a 0.9 m gap between adjacent parts'
#: walls, so 3 of the probe's 5 polyline-interpolated samples fell in
#: empty space and the perpendicular ray cast along x hit nothing.
#: Raising extent_y to 2.6 m makes each wall span +-1.3 m around its
#: part center; adjacent walls then overlap by 0.1 m and every sample
#: position along the polyline is inside some wall's y range.  Inner
#: width is unaffected because it depends on x, not y.
_DEFAULT_PART_EXTENT_M: tuple[float, float, float] = (1.6, 2.6, 2.5)
_DEFAULT_PART_ORIENTATION_DEG = 0.0
_CENTRAL_CONTOUR_DIRECTION = (1.0, 0.0, 0.0)
_CENTRAL_CONTOUR_Y_M = 40.0
_CENTRAL_CONTOUR_ORIENTATION_DEG = 270.0
_CENTRAL_FLOOR_CLEARANCE_M = 0.5
_NONCENTRAL_FLOOR_CLEARANCE_M = 0.5

# BuildReport v1 reserves 0/1/2 for sky, terrain, and terrain-support,
# then assigns ScenePlan semantic classes from 3 in SEMANTIC_ORDER order.
SEMANTIC_ID_BY_CLASS = {
    semantic_class: semantic_id
    for semantic_id, semantic_class in enumerate(SEMANTIC_ORDER, start=3)
}

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
#: Finite float for tuple element types — Pydantic strict mode already
#: rejects ``bool`` for ``float`` fields, but ``allow_inf_nan=False``
#: adds schema-level defense so ``inf``/``nan`` are rejected before the
#: model_validator's ``math.isfinite`` check runs (GLM-P2 defense in depth
#: per FEEDBACK-HANDOFF-CODEX-012 §"GLM-P2").
_FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
ModuleId = Literal[
    "central-courtyard-downhill",
    "bridge-deck-crossing",
    "watermill-tailrace",
    "covered-gallery-underpass",
    "forest-orchard-boundary",
    "lower-valley-uphill",
]
PartId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
GeometryFamily = Literal[
    "open-path",
    "covered-passage",
    "bridge-deck",
    "building-shell",
    "structural-frame",
    "drainage-channel",
    "retaining-structure",
    "guard-rail",
    "service-prop",
    "vegetation-band",
]

_GEOMETRY_FAMILY_SEMANTIC_CLASSES: dict[GeometryFamily, frozenset[str]] = {
    "open-path": frozenset({"path"}),
    "covered-passage": frozenset({"building"}),
    "bridge-deck": frozenset({"bridge"}),
    "building-shell": frozenset({"building"}),
    "structural-frame": frozenset({"building"}),
    "drainage-channel": frozenset({"creek"}),
    "retaining-structure": frozenset({"retaining-wall"}),
    "guard-rail": frozenset({"prop"}),
    "service-prop": frozenset({"prop"}),
    "vegetation-band": frozenset({"prop"}),
}


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

    center_m: tuple[_FiniteFloat, _FiniteFloat, _FiniteFloat]
    extent_m: tuple[_FiniteFloat, _FiniteFloat, _FiniteFloat]
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


# --------------------------------------------------------------------------- #
# Standing-eye camera candidate per reciprocal role (Phase 4.2, responding to
# HANDOFF-CODEX-010 §"Opus camera 输出清单" + HANDOFF-OPUS-009 requirement
# "六个角色各自存在 standing-eye ground-route camera").
#
# The candidate is a *proposal* for the §3 caller chain, not a canonical
# ProductionCameraPose.  The caller is responsible for:
#   - materialising the candidate into a full ProductionCameraPose
#     (computing intrinsics + c2w_opencv via _look_at_c2w / _intrinsics);
#   - running fresh exact-218 preflight + six-layer render + post-render
#     policy + before/after RGB verification.
# The candidate only declares the standing-eye geometry, the topology ref,
# and the content-addressed binding to the production camera plan so the
# downstream chain can verify "this render really came from this candidate".
# --------------------------------------------------------------------------- #


#: Camera IDs for the six reciprocal roles.  ``reciprocal-role`` namespace
#: is intentionally separate from the production ``ground-route`` group to
#: keep candidate lineage traceable in the journal.  The caller may later
#: promote an accepted candidate into the ``ground-route`` group via the
#: existing repose/search contract.
RoleCameraId = Literal[
    "camera-reciprocal-role-001",
    "camera-reciprocal-role-002",
    "camera-reciprocal-role-003",
    "camera-reciprocal-role-004",
    "camera-reciprocal-role-005",
    "camera-reciprocal-role-006",
]

#: Standing-eye height shared with production_profile.EYE_HEIGHT_M.
#: Literal-locked to fail-closed any drift away from standing-eye.
ROLE_CAMERA_EYE_HEIGHT_M = 1.6

#: Default horizontal field of view for reciprocal-role candidates.
#: Matches production_profile ground-route FOV (65 deg) so the candidate
#: lines up with the existing ``_FOV_BY_CATEGORY["ground"]`` baseline.
ROLE_CAMERA_FOV_X_DEG = 65.0

#: Lookahead distance for the standing-eye target point.  Matches
#: production_profile.ROUTE_LOOKAHEAD_M so the candidate's look_at_m is
#: consistent with how the 180-camera plan places ground-route cameras.
ROLE_CAMERA_LOOKAHEAD_M = 25.0
ROLE_CAMERA_APPROACH_OFFSET_M = 5.0

#: Phase 4.4 (P0-2 item 1): maximum 3D distance from a candidate's
#: ``position_m`` to its ``bound_walkable_node_position_m`` when the
#: optional canonical walkable-node binding is populated.  A standing-eye
#: candidate placed at the role camera lookahead (25 m) may legitimately
#: sit up to ~30 m from the closest walkable node along the same route,
#: so the threshold is ``ROLE_CAMERA_LOOKAHEAD_M + 5.0 m`` to allow a
#: small over-scan without admitting candidates that are clearly off the
#: bound topology.  This is a geometry gate, not a trust gate: it does
#: not promote ``modeled-unverified`` geometry to ``measured``.
ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M = ROLE_CAMERA_LOOKAHEAD_M + 5.0


class WalkableNodeBinding(FrozenModel):
    """Phase 4.4 (P0-2 item 1): optional canonical topology binding.

    When populated, this binds a ``ReciprocalRoleCameraCandidate`` to a
    specific ``WalkableNode`` in ``ElevatedTopologyPlan``.  The binding
    is content-addressed: it stores the node's ``node_id`` (pattern-
    validated), ``position_m`` (3-tuple of finite scene-local metres),
    ``level`` (Literal-locked to ``"ground"`` or ``"elevated"``), and
    ``ground_route_ref`` (the path network the node belongs to).

    REVIEW-CODEX-021: ``ground_route_ref`` is now a required field so
    the binding is self-contained — callers and probes can verify the
    candidate's ``topology_ref`` matches the bound node's route without
    consulting an external topology plan.  The candidate's
    ``topology_ref`` must equal ``ground_route_ref`` (validated on the
    parent ``ReciprocalRoleCameraCandidate``).

    The binding does NOT replace ``topology_ref`` (which still names the
    path network the candidate is placed along for backward compat with
    Phase 4.2 callers).  It augments the candidate with a canonical
    node reference so downstream §3 callers can verify the candidate's
    placement against the surveyed topology graph rather than only the
    path-network string.

    The candidate's ``position_m`` to ``node_position_m`` 3D distance
    must be within ``ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M``; this
    is checked by ``ReciprocalRoleCameraCandidate``'s validator, not
    here, because the distance depends on the candidate's position
    which lives on the parent model.
    """

    node_id: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        min_length=1,
        max_length=64,
    )
    node_position_m: tuple[_FiniteFloat, _FiniteFloat, _FiniteFloat]
    level: Literal["ground", "elevated"]
    ground_route_ref: str = Field(
        pattern=r"^path-[a-z0-9]+(?:-[a-z0-9]+)*$",
        min_length=1,
    )

    @model_validator(mode="after")
    def _node_position_is_finite(self) -> WalkableNodeBinding:
        for axis, value in zip(("x", "y", "z"), self.node_position_m, strict=True):
            if not math.isfinite(value):
                raise ValueError(f"node_position_m {axis} must be finite")
        if len(self.node_position_m) != 3:
            raise ValueError("node_position_m must be a 3-tuple")
        return self


class ReciprocalRoleCameraCandidate(FrozenModel):
    """One standing-eye camera candidate for one reciprocal role.

    The candidate is ``modeled-unverified`` geometry, not a surveyed
    viewpoint.  It carries honest placement (scene-local meters, finite,
    standing-eye height) and content-addressed binding to the production
    camera plan + camera registry, so the §3 caller chain can verify the
    render's lineage back to this exact candidate.

    The candidate is NOT a ``ProductionCameraPose``: it omits intrinsics
    and the c2w_opencv matrix because those are caller-computed from
    ``position_m`` + ``look_at_m`` via ``_look_at_c2w``.  Adding them here
    would duplicate caller responsibility and let the plan drift away
    from the canonical pose computation.  The candidate also binds to
    the reciprocal-route module plan via ``role_module_id`` so a render
    cannot claim a role camera without the matching module plan.

    Phase 4.4 (P0-2 item 1): ``bound_walkable_node`` is the optional
    canonical topology upgrade.  When ``None`` (default), the candidate
    is bound only to its ``topology_ref`` path-network string (Phase 4.2
    behaviour).  When populated, the candidate additionally binds to a
    specific ``WalkableNode`` in ``ElevatedTopologyPlan`` via the
    node's ``node_id`` + ``position_m`` + ``level`` triple, and the
    candidate's ``position_m`` to ``node_position_m`` 3D distance must
    be within ``ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M``.  The
    binding is content-addressed: any field change alters the plan SHA.
    """

    role_module_id: ModuleId
    camera_id: RoleCameraId
    topology_ref: str = Field(min_length=1)
    arc_length_m: float | None = Field(default=None, allow_inf_nan=False)
    position_m: tuple[_FiniteFloat, _FiniteFloat, _FiniteFloat]
    look_at_m: tuple[_FiniteFloat, _FiniteFloat, _FiniteFloat]
    eye_height_m: Literal[ROLE_CAMERA_EYE_HEIGHT_M] = ROLE_CAMERA_EYE_HEIGHT_M
    fov_x_deg: float = Field(
        gt=0.0,
        lt=180.0,
        allow_inf_nan=False,
    )
    audit_only: Literal[False] = False
    disclosure: str = Field(min_length=10)
    bound_production_plan_sha256: Sha256
    bound_camera_registry_sha256: Sha256
    bound_walkable_node: WalkableNodeBinding | None = None

    @model_validator(mode="after")
    def _candidate_is_finite_and_standing_eye(self) -> ReciprocalRoleCameraCandidate:
        for axis, value in zip(("x", "y", "z"), self.position_m, strict=True):
            if not math.isfinite(value):
                raise ValueError(f"position_m {axis} must be finite")
        for axis, value in zip(("x", "y", "z"), self.look_at_m, strict=True):
            if not math.isfinite(value):
                raise ValueError(f"look_at_m {axis} must be finite")
        if len(self.position_m) != 3:
            raise ValueError("position_m must be a 3-tuple")
        if len(self.look_at_m) != 3:
            raise ValueError("look_at_m must be a 3-tuple")
        # A standing-eye candidate must not sit on the same point as its
        # target: that produces a degenerate c2w with no forward axis.
        if math.dist(self.position_m, self.look_at_m) < 1.0:
            raise ValueError(
                "position_m and look_at_m must differ by at least 1.0 m "
                "to form a valid standing-eye view direction",
            )
        # Phase 4.4 (P0-2 item 1): if the optional canonical walkable
        # node binding is populated, the candidate's position must be
        # within the standing-eye lookahead envelope of the bound node.
        # This is a geometry gate that catches a candidate claiming to
        # bind a node it is clearly not near -- it does NOT promote the
        # candidate to measured/metric/aligned.
        #
        # REVIEW-CODEX-021: the candidate's ``topology_ref`` must equal
        # the bound node's ``ground_route_ref``.  Without this check, a
        # candidate could bind to a node on a different path network
        # (false-green: spatially near but topologically wrong).
        if self.bound_walkable_node is not None:
            if self.topology_ref != self.bound_walkable_node.ground_route_ref:
                raise ValueError(
                    f"candidate topology_ref={self.topology_ref!r} does not "
                    f"match bound_walkable_node.ground_route_ref="
                    f"{self.bound_walkable_node.ground_route_ref!r}; "
                    f"the candidate must bind a node on the same path "
                    f"network it claims to be placed along",
                )
            distance = math.dist(
                self.position_m,
                self.bound_walkable_node.node_position_m,
            )
            if distance > ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M:
                raise ValueError(
                    f"candidate position_m to bound_walkable_node "
                    f"distance {distance:.3f} m exceeds "
                    f"ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M "
                    f"{ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M:.3f} m",
                )
        return self


#: Phase 4.4 (P0-2 item 2): the set of production ``CameraGroupId`` values a
#: reciprocal-role candidate may legally materialize into.  ``audit-overview``
#: is excluded because audit-overview poses are aerial (altitude ~190 m), not
#: standing-eye (1.6 m); materializing a 1.6 m candidate as an aerial overview
#: would silently lie about the camera's true viewpoint.  This is a trust gate:
#: it does not promote ``modeled-unverified`` geometry to ``measured``, but it
#: does fail-closed a clearly-wrong group assignment.
RECIPROCAL_ROLE_TARGET_GROUP_IDS: frozenset[CameraGroupId] = frozenset(
    {"ground-route", "elevated-pedestrian", "perimeter-inward", "environment-corridor"}
)


def materialize_reciprocal_role_candidate(
    candidate: ReciprocalRoleCameraCandidate,
    *,
    target_group_id: CameraGroupId,
    target_sequence_index: int,
    target_camera_id: str,
) -> ProductionCameraPose:
    """Phase 4.4 (P0-2 item 2): materialize a candidate to a ``ProductionCameraPose``.

    The candidate carries standing-eye placement (``position_m``, ``look_at_m``,
    ``eye_height_m``, ``fov_x_deg``, ``disclosure``) and content-addressed
    binding to the production camera plan + camera registry, but it is NOT a
    ``ProductionCameraPose``: it omits ``intrinsics`` and ``c2w_opencv`` because
    those are caller-computed.  This helper computes them via the same private
    ``_pose`` used by the 180-camera plan (``_look_at_c2w`` + ``_intrinsics``
    from ``camera_plan``, re-exported via ``production_profile``), so the
    materialized pose is byte-identical to what the 180-camera plan would have
    produced for the same placement.

    The candidate's ``camera_id`` (``RoleCameraId``, Literal-locked to
    ``camera-reciprocal-role-001``..``006``) is NOT carried to the production
    pose because ``ProductionCameraPose.camera_id``'s regex rejects the
    ``reciprocal-role`` prefix.  The caller must supply a ``target_camera_id``
    matching ``^camera-(?:ground-route|elevated-pedestrian|perimeter-inward|
    environment-corridor|audit-overview)-[0-9]{3}$`` and a
    ``target_sequence_index`` in ``[1, 180]``; the resulting pose's
    ``camera_id``/``sequence_index``/``group_id`` come from these arguments,
    NOT from the candidate.

    ``target_group_id == "audit-overview"`` is rejected: a 1.6 m standing-eye
    candidate cannot honestly materialize as a ~190 m aerial overview.  The
    candidate's ``audit_only: Literal[False] = False`` is honoured verbatim --
    the materialized pose carries ``audit_only=False`` regardless of group
    (this is what ``_pose`` already does for any non-audit-overview group).

    The resulting pose carries the candidate's ``disclosure`` verbatim -- no
    trust is added or implied.  The pose's content addressing via
    ``canonical_production_plan_bytes`` is the caller's responsibility, not
    this helper's; the helper only constructs a single pose, not a full plan.
    """
    if target_group_id == "audit-overview":
        raise ReciprocalRouteError(
            "refusing to materialize a standing-eye candidate as "
            "audit-overview: audit-overview is an aerial overview group "
            "(altitude ~190 m), not a pedestrian viewpoint (1.6 m)",
        )
    if target_group_id not in RECIPROCAL_ROLE_TARGET_GROUP_IDS:
        # Defensive: CameraGroupId is a Literal so this branch should be
        # unreachable for valid type-checked callers, but a runtime caller
        # passing a raw string would otherwise reach ``_pose`` and produce
        # a ProductionCameraPose with an unexpected group_id.
        raise ReciprocalRouteError(
            f"target_group_id {target_group_id!r} is not one of the "
            f"reciprocal-role target groups "
            f"{sorted(RECIPROCAL_ROLE_TARGET_GROUP_IDS)}",
        )
    return _pose(
        camera_id=target_camera_id,
        group_id=target_group_id,
        sequence_index=target_sequence_index,
        topology_ref=candidate.topology_ref,
        arc_length_m=candidate.arc_length_m,
        position=candidate.position_m,
        look_at=candidate.look_at_m,
        eye_height_m=candidate.eye_height_m,
        fov_x_deg=candidate.fov_x_deg,
        disclosure=candidate.disclosure,
    )


#: Phase 4.4 (P0-2 item 3): minimum route clearance (metres) that a
#: reciprocal-route module passage must provide before a replacement
#: candidate may be placed on it.  Matches the probe's threshold in
#: ``scripts/blender/probe_reciprocal_route_modules.py`` (MIN_ROUTE_CLEARANCE_M).
#: This is a geometry gate, not a trust gate: it verifies the passage has
#: standing-eye clearance but does NOT promote the geometry to measured/metric.
MIN_ROUTE_CLEARANCE_M = 2.4

#: Phase 4.4 (P0-2 item 3): the set of obstructed production camera ids
#: that may be replaced by a reciprocal-role candidate.  These are the
#: two cameras that the 180-camera clearance audit (REVIEW-CODEX-011)
#: rejected due to near-surface occlusion (bridge-lower-001 / stone-deck-
#: parapets-piers at 0.433-0.574 m).  A replacement candidate sits on a
#: reciprocal-route module passage whose clearance has been verified by
#: the Phase 4.3 probe (``probe_clearance_min_m >= MIN_ROUTE_CLEARANCE_M``).
REPLACEMENT_OBSTRUCTED_CAMERA_IDS: frozenset[str] = frozenset(
    {"camera-ground-route-010", "camera-ground-route-039"}
)

#: Phase 4.4 (P0-2 item 3): the canonical module order, used to map a
#: ``role_module_id`` to its 1-based role index for ``RoleCameraId``.
#: This must match the order validated by ``ReciprocalRouteModulePlan``'s
#: ``_modules_are_exact_and_ordered`` validator (see ``expected`` tuple
#: there).  Changing this order would change the role index assignment
#: and break the candidate's ``camera_id`` mapping.  TDD
#: ``test_reciprocal_route_module_order_matches_plan_module_order`` locks
#: the two tuples to the same value so they cannot drift apart.
RECIPROCAL_ROUTE_MODULE_ORDER: tuple[ModuleId, ...] = (
    "central-courtyard-downhill",
    "bridge-deck-crossing",
    "watermill-tailrace",
    "covered-gallery-underpass",
    "forest-orchard-boundary",
    "lower-valley-uphill",
)


def build_ground_route_replacement_candidate(
    *,
    obstructed_camera_id: str,
    role_module_id: ModuleId,
    topology_ref: str,
    bound_walkable_node: WalkableNodeBinding,
    look_at_m: tuple[float, float, float],
    bound_production_plan_sha256: Sha256,
    bound_camera_registry_sha256: Sha256,
    probe_clearance_min_m: float,
    disclosure: str,
) -> ReciprocalRoleCameraCandidate:
    """Phase 4.4 (P0-2 item 3): build a replacement candidate for an obstructed ground-route camera.

    The 180-camera clearance audit (REVIEW-CODEX-011) rejected
    ``camera-ground-route-010`` and ``camera-ground-route-039`` due to
    near-surface occlusion.  This helper builds a replacement
    ``ReciprocalRoleCameraCandidate`` that sits on a reciprocal-route
    module passage whose clearance has been verified by the Phase 4.3
    probe (``probe_clearance_min_m >= MIN_ROUTE_CLEARANCE_M``).

    The candidate's ``position_m`` is derived from the bound walkable
    node's ground position + standing-eye height (1.6 m).  The candidate's
    ``look_at_m`` is supplied by the caller (computed from the module's
    route direction).  The candidate's ``camera_id`` reuses the role's
    ``RoleCameraId`` (e.g., ``camera-reciprocal-role-001`` for
    ``central-courtyard-downhill``); this is safe because the replacement
    candidate is standalone, NOT part of the plan's
    ``role_camera_candidates`` tuple.  When materialized via
    ``materialize_reciprocal_role_candidate``, the resulting
    ``ProductionCameraPose`` carries the obstructed camera's id (e.g.,
    ``camera-ground-route-010``) as ``target_camera_id``.

    Fail-closed contract:

      * ``obstructed_camera_id`` must be in
        ``REPLACEMENT_OBSTRUCTED_CAMERA_IDS``.  An unknown id is rejected
        -- we will not search a replacement for a camera that was not
        actually rejected by the clearance audit.
      * ``probe_clearance_min_m`` must be ``>= MIN_ROUTE_CLEARANCE_M``
        (2.4 m).  A passage with unverified or insufficient clearance
        cannot host a standing-eye candidate.  The caller MUST supply the
        real measured value from the Phase 4.3 probe report; inferring
        clearance from file names or module names is forbidden.
      * The candidate's ``position_m`` to ``bound_walkable_node.node_position_m``
        3D distance is exactly ``ROLE_CAMERA_EYE_HEIGHT_M`` (1.6 m), well
        within ``ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M`` (30.0 m).  The
        ``ReciprocalRoleCameraCandidate`` validator enforces this.

    The resulting candidate is ``modeled-unverified``: it does NOT promote
    the geometry to measured/metric/aligned.  Acceptance requires fresh
    preflight + six-layer render + post-render policy, which is the §3
    caller's responsibility.
    """
    if obstructed_camera_id not in REPLACEMENT_OBSTRUCTED_CAMERA_IDS:
        raise ReciprocalRouteError(
            f"obstructed_camera_id {obstructed_camera_id!r} is not one of "
            f"the reposeable obstructed cameras "
            f"{sorted(REPLACEMENT_OBSTRUCTED_CAMERA_IDS)}",
        )
    # Phase 4.4 fail-closed audit (REVIEW-OPUS-006): probe_clearance_min_m
    # must be a finite real number.  NaN and Inf silently bypass the
    # ``< MIN_ROUTE_CLEARANCE_M`` comparison (``nan < 2.4`` is False,
    # ``inf < 2.4`` is False), so a caller passing NaN or Inf would
    # construct a replacement candidate without verified clearance --
    # a fail-open hole.  Reject explicitly before the comparison.
    # GLM-P2 (FEEDBACK-HANDOFF-CODEX-012): ``bool`` is a subclass of
    # ``int`` in Python, so ``isinstance(True, (int, float))`` is True
    # and ``math.isfinite(True)`` is True.  An explicit ``bool`` check
    # is required to reject ``True``/``False`` as a clearance value.
    if isinstance(probe_clearance_min_m, bool) or not isinstance(
        probe_clearance_min_m, (int, float),
    ) or not math.isfinite(probe_clearance_min_m):
        raise ReciprocalRouteError(
            f"probe_clearance_min_m={probe_clearance_min_m!r} must be a "
            f"finite real number; NaN/Inf/bool silently bypass the "
            f"clearance gate and are rejected"
        )
    if probe_clearance_min_m < MIN_ROUTE_CLEARANCE_M:
        raise ReciprocalRouteError(
            f"probe_clearance_min_m={probe_clearance_min_m:.3f} < "
            f"MIN_ROUTE_CLEARANCE_M={MIN_ROUTE_CLEARANCE_M:.3f}; cannot "
            f"place a standing-eye replacement candidate on a passage "
            f"with insufficient clearance",
        )
    if role_module_id not in RECIPROCAL_ROUTE_MODULE_ORDER:
        raise ReciprocalRouteError(
            f"role_module_id {role_module_id!r} is not one of the "
            f"reciprocal-route modules {RECIPROCAL_ROUTE_MODULE_ORDER}",
        )
    if bound_walkable_node.level != "ground":
        raise ReciprocalRouteError(
            f"build_ground_route_replacement_candidate requires a ground-level "
            f"walkable node; got level={bound_walkable_node.level!r}. "
            f"Elevated replacements are not yet supported.",
        )
    role_index = RECIPROCAL_ROUTE_MODULE_ORDER.index(role_module_id) + 1
    camera_id: RoleCameraId = f"camera-reciprocal-role-{role_index:03d}"  # type: ignore[assignment]
    node_pos = bound_walkable_node.node_position_m
    position_m = (
        node_pos[0],
        node_pos[1],
        node_pos[2] + ROLE_CAMERA_EYE_HEIGHT_M,
    )
    return ReciprocalRoleCameraCandidate(
        role_module_id=role_module_id,
        camera_id=camera_id,
        topology_ref=topology_ref,
        arc_length_m=None,
        position_m=position_m,
        look_at_m=look_at_m,
        eye_height_m=ROLE_CAMERA_EYE_HEIGHT_M,
        fov_x_deg=ROLE_CAMERA_FOV_X_DEG,
        audit_only=False,
        disclosure=disclosure,
        bound_production_plan_sha256=bound_production_plan_sha256,
        bound_camera_registry_sha256=bound_camera_registry_sha256,
        bound_walkable_node=bound_walkable_node,
    )


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
    geometry_family: GeometryFamily
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
        allowed_semantic_ids = {
            SEMANTIC_ID_BY_CLASS[semantic_class]
            for semantic_class in _GEOMETRY_FAMILY_SEMANTIC_CLASSES[
                self.geometry_family
            ]
        }
        if self.semantic_id not in allowed_semantic_ids:
            raise ValueError(
                f"geometry family {self.geometry_family} is incompatible with "
                f"semantic_id {self.semantic_id}",
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


def _flat_module_floor_z(module_id: ModuleId) -> float:
    """Return a flat floor clearing the highest terrain point in the run."""

    base_x, base_y, _legacy_z = _DEFAULT_MODULE_BASE_POSITION[module_id]
    peak = max(
        terrain_height_m(
            base_x,
            base_y + (instance_id - 176) * _DEFAULT_PART_SPACING_Y_M,
        )
        for instance_id in _module_instance_range(module_id)
    )
    return round(peak + _NONCENTRAL_FLOOR_CLEARANCE_M, 3)


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
    *,
    elevated_topology: ElevatedTopologyPlan,
) -> PartLayoutSpec:
    """Build the canonical default layout for one part.

    Non-canary modules preserve the Phase 3 hardcoded layout.  The central
    canary follows a free contour near the canonical ``central-ground-east``
    node so it does not duplicate the already-built courtyard environment
    module.  The runtime reads every value verbatim from this plan.
    """

    if module_id == "central-courtyard-downhill":
        node = next(
            (
                row
                for row in elevated_topology.nodes
                if row.node_id == "central-ground-east"
            ),
            None,
        )
        if (
            node is None
            or node.level != "ground"
            or node.ground_route_ref != "path-network-003"
        ):
            raise ReciprocalRouteError(
                "central courtyard route requires canonical central-ground-east "
                "on path-network-003",
            )
        part_index = instance_id - CENTRAL_DOWNHILL_INSTANCE_RANGE.start
        distance_m = part_index * _DEFAULT_PART_SPACING_Y_M
        x = node.position_m[0] + _CENTRAL_CONTOUR_DIRECTION[0] * distance_m
        y = _CENTRAL_CONTOUR_Y_M
        floor_z = (
            terrain_height_m(node.position_m[0], y)
            + _CENTRAL_FLOOR_CLEARANCE_M
        )
        center_m = (
            round(x, 3),
            round(y, 3),
            round(floor_z, 3),
        )
        return PartLayoutSpec(
            center_m=center_m,
            extent_m=_DEFAULT_PART_EXTENT_M,
            orientation_deg=_CENTRAL_CONTOUR_ORIENTATION_DEG,
        )

    base_x, base_y, _legacy_z = _DEFAULT_MODULE_BASE_POSITION[module_id]
    base_z = _flat_module_floor_z(module_id)
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
    #: Six standing-eye camera candidates, one per reciprocal role
    #: (HANDOFF-OPUS-009 Phase 4.2 / HANDOFF-CODEX-010 §"Opus camera
    #: 输出清单").  Candidates are additive: they bind to the production
    #: camera plan + camera registry SHAs but do NOT promote
    #: ``modeled-unverified`` trust or replace the canonical 180-camera
    #: plan.  The §3 caller chain materialises them into
    #: ``ProductionCameraPose`` instances and runs fresh preflight +
    #: six-layer render + post-render policy before any acceptance.
    role_camera_candidates: tuple[ReciprocalRoleCameraCandidate, ...] = (
        Field(min_length=6, max_length=6)
    )

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
        # Role camera candidates: exactly six, one per module, unique IDs,
        # in module order.  This is the Phase 4.2 fail-closed gate:
        # a plan with a missing / duplicate / mis-ordered candidate is
        # rejected at schema level, not at caller level.
        if len(self.role_camera_candidates) != 6:
            raise ValueError(
                "reciprocal-route plan must carry exactly six role camera candidates",
            )
        candidate_role_ids = tuple(
            candidate.role_module_id for candidate in self.role_camera_candidates
        )
        if candidate_role_ids != expected:
            raise ValueError(
                "reciprocal-route role camera candidates must be ordered "
                "one-per-module matching the six module IDs",
            )
        candidate_camera_ids = tuple(
            candidate.camera_id for candidate in self.role_camera_candidates
        )
        if len(set(candidate_camera_ids)) != len(candidate_camera_ids):
            raise ValueError(
                "reciprocal-route role camera candidate IDs must be unique",
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


_DEFAULT_GEOMETRY_FAMILY_BY_PART_ID: dict[str, GeometryFamily] = {
    # Central courtyard: one authored covered passage, four open route
    # surfaces, then semantic-compatible gallery structure and guard.
    "courtyard-downhill-gate-001": "open-path",
    "courtyard-covered-side-passage-001": "covered-passage",
    "courtyard-cross-slope-alley-001": "open-path",
    "courtyard-route-attachment-upper-001": "open-path",
    "courtyard-route-attachment-lower-001": "open-path",
    "courtyard-gallery-post-run-001": "structural-frame",
    "courtyard-gallery-guard-001": "guard-rail",
    # Bridge route: attachments and ramps stay open overhead.  The bridge
    # transition uses bridge semantics, not a path-labelled fake roof.
    "bridge-route-attachment-upstream-001": "open-path",
    "bridge-route-attachment-downstream-001": "open-path",
    "bridge-access-ramp-001": "open-path",
    "bridge-side-maintenance-path-001": "open-path",
    "bridge-drainage-scuppers-001": "drainage-channel",
    "bridge-deck-edge-transition-001": "bridge-deck",
    # Watermill service route and independent structures.
    "watermill-building-shell-001": "building-shell",
    "watermill-maintenance-platform-001": "open-path",
    "watermill-service-stair-001": "open-path",
    "watermill-access-panel-001": "service-prop",
    "watermill-creek-bank-path-001": "open-path",
    "watermill-platform-guard-001": "guard-rail",
    "watermill-tailrace-retaining-wall-001": "retaining-structure",
    # The lower lane is a path surface under separately declared gallery
    # structure; its own path-semantic mesh deliberately has no fake roof.
    "gallery-underpass-lower-lane-001": "open-path",
    # The recipe declares a finite lower-lane clear height. The building-
    # semantic post run owns the measured covered cell; the path-semantic
    # lower lane stays an open surface and therefore never mislabels a roof.
    "gallery-post-run-001": "covered-passage",
    "gallery-beam-run-001": "structural-frame",
    "gallery-foundation-run-001": "retaining-structure",
    "gallery-guard-run-001": "guard-rail",
    "gallery-side-door-001": "building-shell",
    "gallery-branch-attachment-upper-001": "open-path",
    "gallery-branch-attachment-lower-001": "open-path",
    "gallery-branch-attachment-side-001": "open-path",
    # Forest/orchard boundary.
    "forest-boundary-path-fork-001": "open-path",
    "forest-orchard-transition-001": "open-path",
    "forest-retaining-drain-001": "retaining-structure",
    "forest-trail-shelter-001": "covered-passage",
    "forest-route-attachment-inbound-001": "open-path",
    "forest-route-attachment-outbound-001": "open-path",
    "forest-edge-vegetation-band-001": "vegetation-band",
    # Lower valley return route.
    "lower-valley-entry-path-001": "open-path",
    "lower-valley-field-edge-path-001": "open-path",
    "lower-valley-creek-maintenance-trail-001": "open-path",
    "lower-valley-drainage-outlet-001": "drainage-channel",
    "lower-valley-building-back-entry-001": "building-shell",
    "lower-valley-route-reconnection-001": "open-path",
    "lower-valley-retaining-step-001": "retaining-structure",
}


def _default_module(
    module_id: ModuleId,
    *,
    elevated_topology: ElevatedTopologyPlan,
) -> ReciprocalRouteModule:
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
            geometry_family=_DEFAULT_GEOMETRY_FAMILY_BY_PART_ID[part_id],
            part_layout=_default_part_layout(
                module_id,
                instance_id,
                elevated_topology=elevated_topology,
            ),
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


#: Camera placement topology per reciprocal role (REVIEW-CODEX-021).
#:
#: Each entry maps (role_module_id, camera_topology_ref) — the path
#: network whose ground ``WalkableNode`` is within 30 m of the
#: candidate's position.  This is the **camera placement topology**,
#: distinct from the module's **attachment topology** (which path object
#: the module mesh attaches to in Blender).  The two may differ for
#: modules that cross path-network boundaries.
#:
#: REVIEW-CODEX-021 fixed three false-green bindings where the
#: candidate's ``topology_ref`` named a path network with no ground
#: node within 30 m:
#:
#:   covered-gallery-underpass: path-network-005 → path-network-003
#:     (central-ground-east at 28.4 m; path-network-005 has no node)
#:   forest-orchard-boundary:  path-network-002 → path-network-003
#:     (upper-ground-west at 28.1 m; same-ref nearest was 202 m)
#:   lower-valley-uphill:      path-network-001 → path-network-002
#:     (valley-ground-north at 9.6 m; same-ref nearest was 102 m)
#:
#: Any change here changes ``reciprocal_route_module_plan_sha256`` and
#: therefore ``build_id`` and the downstream render identity.
_DEFAULT_ROLE_CAMERA_PLACEMENT: tuple[
    tuple[ModuleId, str],
    ...,
] = (
    ("central-courtyard-downhill", "path-network-003"),
    ("bridge-deck-crossing", "path-network-001"),
    ("watermill-tailrace", "path-network-001"),
    ("covered-gallery-underpass", "path-network-003"),
    ("forest-orchard-boundary", "path-network-003"),
    ("lower-valley-uphill", "path-network-002"),
)

#: Default disclosure strings per role (Phase 4.2).  Each disclosure is
#: honest about the candidate being modeled-unverified geometry, not a
#: surveyed viewpoint.  The §3 caller chain must not accept a candidate
#: without running fresh preflight + six-layer render + post-render policy.
_ROLE_CAMERA_DISCLOSURE: dict[ModuleId, str] = {
    "central-courtyard-downhill": (
        "modeled-unverified standing-eye at the courtyard downhill gate; "
        "fresh preflight + six-layer render required before acceptance"
    ),
    "bridge-deck-crossing": (
        "modeled-unverified standing-eye on the bridge deck mid-span; "
        "fresh preflight + six-layer render required before acceptance"
    ),
    "watermill-tailrace": (
        "modeled-unverified standing-eye at the watermill tailrace "
        "service stair; fresh preflight + six-layer render required before acceptance"
    ),
    "covered-gallery-underpass": (
        "modeled-unverified standing-eye at the covered gallery "
        "underpass lower lane; fresh preflight + six-layer render required before acceptance"
    ),
    "forest-orchard-boundary": (
        "modeled-unverified standing-eye at the forest orchard boundary "
        "edge; fresh preflight + six-layer render required before acceptance"
    ),
    "lower-valley-uphill": (
        "modeled-unverified standing-eye at the lower valley retaining "
        "step; fresh preflight + six-layer render required before acceptance"
    ),
}


def _default_role_camera_candidates(
    *,
    modules: tuple[ReciprocalRouteModule, ...],
    elevated_topology: ElevatedTopologyPlan,
    plan_sha: str,
    registry_sha: str,
) -> tuple[ReciprocalRoleCameraCandidate, ...]:
    """Build the canonical six standing-eye camera candidates.

    Each candidate sits at standing-eye height (1.6 m) above its module's
    first passage floor, with the look_at point 25 m along the ordered-part
    route direction.  The candidate's ``bound_production_plan_sha256``
    + ``bound_camera_registry_sha256`` are content-addressed bindings to
    the canonical 180-camera plan, so the §3 caller chain can verify the
    render's lineage back to this exact candidate.

    REVIEW-CODEX-021: each candidate's ``bound_walkable_node`` is now
    populated deterministically from ``elevated_topology``.  The nearest
    ground node whose ``ground_route_ref`` matches the candidate's
    ``topology_ref`` is selected; ties, absence, and > 30 m all fail-closed.

    ``plan_sha`` / ``registry_sha`` may be ``"0" * 64`` placeholder when
    the plan is constructed without a production camera plan (unit tests);
    the caller is expected to supply real SHAs before publishing.  This
    keeps the plan constructible for tests that do not exercise the §3
    caller chain.
    """

    module_by_id = {module.module_id: module for module in modules}
    candidates: list[ReciprocalRoleCameraCandidate] = []
    for index, (module_id, topology_ref) in enumerate(
        _DEFAULT_ROLE_CAMERA_PLACEMENT,
        start=1,
    ):
        parts = tuple(
            sorted(
                module_by_id[module_id].parts,
                key=lambda part: part.instance_id,
            ),
        )
        first = parts[0].part_layout.center_m
        last = parts[-1].part_layout.center_m
        route_length = math.dist(first, last)
        if route_length <= 0.0:
            raise ReciprocalRouteError(
                f"role module {module_id} has no non-degenerate route direction",
            )
        direction = tuple(
            (last[axis] - first[axis]) / route_length
            for axis in range(3)
        )
        position_m = (
            first[0] - direction[0] * ROLE_CAMERA_APPROACH_OFFSET_M,
            first[1] - direction[1] * ROLE_CAMERA_APPROACH_OFFSET_M,
            first[2] + ROLE_CAMERA_EYE_HEIGHT_M,
        )
        look_at_m = tuple(
            position_m[axis] + direction[axis] * ROLE_CAMERA_LOOKAHEAD_M
            for axis in range(3)
        )

        # REVIEW-CODEX-021: deterministically bind the nearest ground node
        # whose ground_route_ref matches topology_ref.  No same-ref node,
        # distance > 30 m, or distance ambiguity all fail-closed.
        matching_nodes = [
            node
            for node in elevated_topology.nodes
            if node.level == "ground"
            and node.ground_route_ref == topology_ref
        ]
        if not matching_nodes:
            raise ReciprocalRouteError(
                f"role module {module_id} topology_ref={topology_ref!r} "
                f"has no ground node with matching ground_route_ref in "
                f"the elevated topology plan",
            )
        scored = sorted(
            (
                (math.dist(position_m, node.position_m), node)
                for node in matching_nodes
            ),
            key=lambda pair: pair[0],
        )
        nearest_distance, nearest_node = scored[0]
        if nearest_distance > ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M:
            raise ReciprocalRouteError(
                f"role module {module_id} nearest same-ref ground node "
                f"{nearest_node.node_id} is {nearest_distance:.3f} m away, "
                f"exceeds ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M "
                f"{ROLE_CAMERA_WALKABLE_NODE_MAX_DISTANCE_M:.3f} m",
            )
        if len(scored) > 1:
            second_distance = scored[1][0]
            if abs(nearest_distance - second_distance) < 1e-6:
                raise ReciprocalRouteError(
                    f"role module {module_id} has ambiguous nearest "
                    f"ground nodes: {scored[0][1].node_id} at "
                    f"{nearest_distance:.3f} m vs {scored[1][1].node_id} "
                    f"at {second_distance:.3f} m",
                )
        bound_walkable_node = WalkableNodeBinding(
            node_id=nearest_node.node_id,
            node_position_m=nearest_node.position_m,
            level="ground",
            ground_route_ref=nearest_node.ground_route_ref,
        )

        candidates.append(
            ReciprocalRoleCameraCandidate(
                role_module_id=module_id,
                camera_id=f"camera-reciprocal-role-{index:03d}",
                topology_ref=topology_ref,
                arc_length_m=None,
                position_m=position_m,
                look_at_m=look_at_m,
                eye_height_m=ROLE_CAMERA_EYE_HEIGHT_M,
                fov_x_deg=ROLE_CAMERA_FOV_X_DEG,
                audit_only=False,
                disclosure=_ROLE_CAMERA_DISCLOSURE[module_id],
                bound_production_plan_sha256=plan_sha,
                bound_camera_registry_sha256=registry_sha,
                bound_walkable_node=bound_walkable_node,
            ),
        )
    return tuple(candidates)


def build_default_reciprocal_route_module_plan(
    *,
    scene: ScenePlan,
    elevated_topology: ElevatedTopologyPlan,
    environment_module_plan: EnvironmentModulePlan,
    production_camera_plan: ProductionCameraPlan | None = None,
) -> ReciprocalRouteModulePlan:
    """Build the canonical default plan bound to the given scene + topology + v1 plan.

    ``production_camera_plan`` binds the six standing-eye role camera
    candidates (Phase 4.2) to the canonical 180-camera plan + camera
    registry.  When omitted, the candidates are constructed with
    placeholder all-zero SHA-256 bindings; the caller is expected to
    supply a real plan before publishing.  This keeps the plan
    constructible for unit tests that do not exercise the §3 caller
    chain, while still binding content identity in the published path.
    """

    scene_sha = hashlib.sha256(
        canonical_scene_plan_bytes(scene),
    ).hexdigest()
    topology_sha = hashlib.sha256(
        canonical_elevated_topology_bytes(elevated_topology),
    ).hexdigest()
    env_module_sha = hashlib.sha256(
        canonical_environment_module_plan_bytes(environment_module_plan),
    ).hexdigest()
    if production_camera_plan is not None:
        plan_sha = hashlib.sha256(
            canonical_production_plan_bytes(production_camera_plan),
        ).hexdigest()
        registry_sha = production_camera_registry_digest(production_camera_plan)
    else:
        plan_sha = "0" * 64
        registry_sha = "0" * 64
    modules = tuple(
        _default_module(module_id, elevated_topology=elevated_topology)
        for module_id in (
            "central-courtyard-downhill",
            "bridge-deck-crossing",
            "watermill-tailrace",
            "covered-gallery-underpass",
            "forest-orchard-boundary",
            "lower-valley-uphill",
        )
    )
    role_camera_candidates = _default_role_camera_candidates(
        modules=modules,
        elevated_topology=elevated_topology,
        plan_sha=plan_sha,
        registry_sha=registry_sha,
    )
    plan = ReciprocalRouteModulePlan(
        scene_plan_sha256=scene_sha,
        elevated_topology_sha256=topology_sha,
        environment_module_plan_sha256=env_module_sha,
        modules=modules,
        summary=ReciprocalRouteModuleSummary(
            part_count=sum(len(module.parts) for module in modules),
        ),
        role_camera_candidates=role_camera_candidates,
    )
    return plan.model_copy(
        update={},  # trigger re-validation
    )
