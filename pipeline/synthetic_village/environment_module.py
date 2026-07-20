"""Environment module plan for Batch 6 production modules (HANDOFF-OPUS-007).

This plan is the canonical, content-addressed counterpart of
``ElevatedTopologyPlan``: it binds the exact ``ScenePlan`` and
``ElevatedTopologyPlan`` digests, the three private ``design-only`` image
source SHA-256 values, and a recipe version.  It does not replace any
field in the immutable ``ScenePlan`` v1; it is additive.

The plan carries three modules and their parts:

  1. ``central-courtyard``  -- bound to ``courtyard-public-002`` and the
     central ground attachments + elevated edges.  Hard geometric
     validators enforce gallery clear width / headroom, stair clear width,
     and ramp clear width.  Entry attachments must keep west/east
     entrances connected to ``path-network-002/003``.

  2. ``lower-bridge-waterwheel`` -- bound to ``bridge-lower-001`` and
     ``creek-main-001``.  Cross-section validators enforce
     ``water_z <= bank_z <= terrain_z`` and ``arch_soffit_z >= deck_z`` so
     the water surface cannot pass through terrain or the arch soffit
     cannot dip below the deck.  Waterwheel parts (wheel / axle / bracket /
     millrace / spill / tailwater) carry independent object identity.

  3. ``rear-service-courtyard`` -- bound to ``building-central-008``.
     The prototype is only a layout reference; door / window / eaves /
     gutter / drainage geometry is declared explicitly in the recipe, not
     reverse-engineered from the private ``.blend``.

Instance ID segment: elevated components occupy 127-130; environment
module parts start at 131 and are partitioned across the three modules.
The partition is hard-locked so a later module cannot steal another
module's instance IDs.

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
from .scene_plan import ScenePlan, canonical_scene_plan_bytes

ENVIRONMENT_MODULE_SCHEMA = "nantai.synthetic-village.environment-module.v1"
ENVIRONMENT_MODULE_RECIPE_VERSION = "v1"

#: Stable SHA-256 of the three private ``design-only`` image sources.
#: These are bound for provenance only; they are NOT multi-view training
#: evidence and never contribute to coverage or orientation.
CENTRAL_COURTYARD_SOURCE_SHA256 = (
    "19b40a84322ab7d343716bd684fc83a3207ae42ad94993d28446707f7a5537df"
)
BRIDGE_UNDERCROFT_SOURCE_SHA256 = (
    "16b9f390f4550b2ec64bd98e4ccd799e05c4f44cd924a5da1503eec73ae8b4be"
)
REAR_SERVICE_COURTYARD_SOURCE_SHA256 = (
    "2c3900ab686cb45252538c8bdb6e507396ec9084ca7809a44fa3524810ab8b51"
)
DESIGN_SOURCE_SHA256S = (
    CENTRAL_COURTYARD_SOURCE_SHA256,
    BRIDGE_UNDERCROFT_SOURCE_SHA256,
    REAR_SERVICE_COURTYARD_SOURCE_SHA256,
)

#: Instance ID segments.  Elevated components own 127-130 (locked in
#: elevated_topology.py).  Environment modules own 131-175, partitioned
#: as follows.  Changing these numbers changes the plan digest.
CENTRAL_COURTYARD_INSTANCE_RANGE = range(131, 146)   # 131..145 (15 parts)
LOWER_BRIDGE_INSTANCE_RANGE = range(146, 161)        # 146..160 (15 parts)
REAR_SERVICE_INSTANCE_RANGE = range(161, 176)        # 161..175 (15 parts)

#: Hard geometric thresholds (HANDOFF-OPUS-007 §1).
MIN_GALLERY_CLEAR_WIDTH_M = 2.6
MIN_GALLERY_CLEAR_HEIGHT_M = 2.4
MIN_STAIR_CLEAR_WIDTH_M = 2.4
MIN_RAMP_CLEAR_WIDTH_M = 3.0

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ModuleId = Literal[
    "central-courtyard",
    "lower-bridge-waterwheel",
    "rear-service-courtyard",
]
PartId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class EnvironmentModuleError(ValueError):
    """The environment module plan cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Central courtyard.
# --------------------------------------------------------------------------- #


class CourtyardGallerySpec(FrozenModel):
    """Continuous covered gallery along the courtyard perimeter."""

    clear_width_m: float = Field(
        ge=MIN_GALLERY_CLEAR_WIDTH_M, allow_inf_nan=False,
    )
    clear_height_m: float = Field(
        ge=MIN_GALLERY_CLEAR_HEIGHT_M, allow_inf_nan=False,
    )
    deck_segment_count: int = Field(ge=2)
    drainage_channel_present: bool
    east_entry_connects_to: Literal["path-network-003"]
    west_entry_connects_to: Literal["path-network-002"]


class CourtyardStairSpec(FrozenModel):
    clear_width_m: float = Field(
        ge=MIN_STAIR_CLEAR_WIDTH_M, allow_inf_nan=False,
    )
    tread_count: int = Field(ge=3)
    tread_depth_m: float = Field(gt=0.25, allow_inf_nan=False)
    continuous_collision: bool


class CourtyardRampSpec(FrozenModel):
    clear_width_m: float = Field(
        ge=MIN_RAMP_CLEAR_WIDTH_M, allow_inf_nan=False,
    )
    continuous_collision: bool
    slope_pct: float = Field(gt=0.0, le=8.3, allow_inf_nan=False)


class CourtyardPropSpec(FrozenModel):
    workshed_count: int = Field(ge=1)
    workbench_count: int = Field(ge=1)
    replaceable_prop_slot_count: int = Field(ge=1)
    planter_tree_non_collision: bool


class CentralCourtyardRecipe(FrozenModel):
    module_id: Literal["central-courtyard"] = "central-courtyard"
    bound_object_id: Literal["courtyard-public-002"]
    bound_ground_attachments: tuple[
        Literal["central-ground-west", "central-ground-east"],
        ...,
    ] = Field(min_length=2, max_length=2)
    bound_elevated_edges: tuple[
        Literal[
            "edge-central-stair-001",
            "edge-central-gallery-001",
            "edge-central-ramp-001",
        ],
        ...,
    ] = Field(min_length=3, max_length=3)
    gallery: CourtyardGallerySpec
    stair: CourtyardStairSpec
    ramp: CourtyardRampSpec
    props: CourtyardPropSpec
    paving_material_slot_id: PartId
    drainage_material_slot_id: PartId

    @model_validator(mode="after")
    def _attachments_unique(self) -> CentralCourtyardRecipe:
        if len(set(self.bound_ground_attachments)) != len(
            self.bound_ground_attachments,
        ):
            raise ValueError("ground attachments must be unique")
        if len(set(self.bound_elevated_edges)) != len(self.bound_elevated_edges):
            raise ValueError("elevated edges must be unique")
        return self


# --------------------------------------------------------------------------- #
# Lower bridge / waterwheel.
# --------------------------------------------------------------------------- #


class CreekCrossSectionSpec(FrozenModel):
    """One cross-section of the creek at a given arc length.

    The ordering invariant is ``water_z <= bank_z <= terrain_z``; the
    arch soffit must sit at or above the deck.  Violations are fail-closed.
    """

    arc_length_m: float = Field(ge=0.0, allow_inf_nan=False)
    terrain_z_m: float = Field(allow_inf_nan=False)
    bank_z_m: float = Field(allow_inf_nan=False)
    water_z_m: float = Field(allow_inf_nan=False)
    arch_soffit_z_m: float = Field(allow_inf_nan=False)
    deck_z_m: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def _section_is_non_penetrating(self) -> CreekCrossSectionSpec:
        if not (self.water_z_m <= self.bank_z_m + 1e-6):
            raise ValueError(
                f"creek section at {self.arc_length_m}m: water z "
                f"({self.water_z_m}) above bank z ({self.bank_z_m})",
            )
        if not (self.bank_z_m <= self.terrain_z_m + 1e-6):
            raise ValueError(
                f"creek section at {self.arc_length_m}m: bank z "
                f"({self.bank_z_m}) above terrain z ({self.terrain_z_m})",
            )
        if not (self.arch_soffit_z_m >= self.deck_z_m - 1e-6):
            raise ValueError(
                f"creek section at {self.arc_length_m}m: arch soffit z "
                f"({self.arch_soffit_z_m}) below deck z ({self.deck_z_m})",
            )
        return self


class WaterwheelPartSpec(FrozenModel):
    """Independent part identity for one waterwheel component.

    The waterwheel must NOT merge with the bridge abutment or any building
    (HANDOFF-OPUS-007 §2).  Each part here declares its own stable
    instance id, part id, and material slot, so the build report can
    distinguish them in the instance layer.
    """

    part_id: PartId
    instance_id: int
    material_slot_id: PartId
    semantic_id: int = Field(ge=0, le=14)


class LowerBridgeRecipe(FrozenModel):
    module_id: Literal["lower-bridge-waterwheel"] = "lower-bridge-waterwheel"
    bound_bridge_object_id: Literal["bridge-lower-001"]
    bound_creek_object_id: Literal["creek-main-001"]
    bound_path_networks: tuple[
        Literal["path-network-001", "path-network-005"],
        ...,
    ] = Field(min_length=2, max_length=2)
    arch_thickness_m: float = Field(gt=0.2, allow_inf_nan=False)
    abutment_support_count: int = Field(ge=2)
    creek_sections: tuple[CreekCrossSectionSpec, ...] = Field(min_length=3)
    waterwheel_parts: tuple[WaterwheelPartSpec, ...] = Field(min_length=6)
    maintenance_platform_is_main_route: Literal[False] = False
    main_route_connectivity_preserved: bool

    @model_validator(mode="after")
    def _waterwheel_parts_independent(self) -> LowerBridgeRecipe:
        ids = tuple(part.instance_id for part in self.waterwheel_parts)
        if len(set(ids)) != len(ids):
            raise ValueError("waterwheel part instance IDs must be unique")
        part_ids = tuple(part.part_id for part in self.waterwheel_parts)
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("waterwheel part IDs must be unique")
        # Waterwheel parts MUST stay inside the bridge module's instance
        # segment (146-160).  An instance ID outside this segment means
        # the waterwheel is stealing another module's identity.
        for part in self.waterwheel_parts:
            if part.instance_id not in LOWER_BRIDGE_INSTANCE_RANGE:
                raise ValueError(
                    f"waterwheel part {part.part_id} instance "
                    f"{part.instance_id} is outside the lower-bridge "
                    f"segment [146, 160]",
                )
        if not self.main_route_connectivity_preserved:
            raise ValueError(
                "main route connectivity over bridge-lower-001 must be preserved",
            )
        return self


# --------------------------------------------------------------------------- #
# Rear service courtyard.
# --------------------------------------------------------------------------- #


class ServiceCourtyardVariantSpec(FrozenModel):
    variant_id: PartId
    replaceable_prop_count: int = Field(ge=3)


class RearServiceCourtyardRecipe(FrozenModel):
    module_id: Literal["rear-service-courtyard"] = "rear-service-courtyard"
    bound_building_object_id: Literal["building-central-008"]
    paving_conform_to_terrain: bool
    door_window_eaves_gutter_declared: bool
    elevated_access_deck_present: bool
    service_shed_count: int = Field(ge=1)
    storage_rack_count: int = Field(ge=1)
    wood_pile_count: int = Field(ge=1)
    wash_basin_count: int = Field(ge=1)
    variants: tuple[ServiceCourtyardVariantSpec, ...] = Field(min_length=3)
    props_do_not_carry_topology: Literal[True] = True
    props_do_not_block_paths: Literal[True] = True

    @model_validator(mode="after")
    def _variants_unique(self) -> RearServiceCourtyardRecipe:
        ids = tuple(variant.variant_id for variant in self.variants)
        if len(set(ids)) != len(ids):
            raise ValueError("service courtyard variant IDs must be unique")
        return self


# --------------------------------------------------------------------------- #
# Module wrapper.
# --------------------------------------------------------------------------- #


class EnvironmentModulePart(FrozenModel):
    """One stable part declared by a module.

    Every part carries an instance id in the module's locked segment, a
    part id, a semantic id, and a material slot id.  Unknown fields stay
    unknown; no inference is made from names.
    """

    module_id: ModuleId
    part_id: PartId
    instance_id: int
    semantic_id: int = Field(ge=0, le=14)
    material_slot_id: PartId

    @model_validator(mode="after")
    def _instance_in_module_segment(self) -> EnvironmentModulePart:
        expected_range = _module_instance_range(self.module_id)
        if self.instance_id not in expected_range:
            raise ValueError(
                f"part {self.part_id} instance {self.instance_id} is outside "
                f"module {self.module_id} segment "
                f"[{expected_range.start}, {expected_range.stop - 1}]",
            )
        return self


def _module_instance_range(module_id: ModuleId) -> range:
    if module_id == "central-courtyard":
        return CENTRAL_COURTYARD_INSTANCE_RANGE
    if module_id == "lower-bridge-waterwheel":
        return LOWER_BRIDGE_INSTANCE_RANGE
    return REAR_SERVICE_INSTANCE_RANGE


class EnvironmentModule(FrozenModel):
    module_id: ModuleId
    recipe_version: Literal["v1"] = ENVIRONMENT_MODULE_RECIPE_VERSION
    design_source_sha256: Sha256
    parts: tuple[EnvironmentModulePart, ...] = Field(min_length=1)
    recipe: (
        CentralCourtyardRecipe
        | LowerBridgeRecipe
        | RearServiceCourtyardRecipe
    )

    @model_validator(mode="after")
    def _recipe_matches_module(self) -> EnvironmentModule:
        if self.recipe.module_id != self.module_id:
            raise ValueError("recipe module id disagrees with module wrapper")
        if self.design_source_sha256 not in DESIGN_SOURCE_SHA256S:
            raise ValueError("design source SHA-256 is not one of the bound sources")
        expected_source = _module_design_source(self.module_id)
        if self.design_source_sha256 != expected_source:
            raise ValueError(
                f"design source SHA-256 disagrees with module {self.module_id}",
            )
        instance_ids = tuple(part.instance_id for part in self.parts)
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("module part instance IDs must be unique")
        part_ids = tuple(part.part_id for part in self.parts)
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("module part IDs must be unique")
        # For the lower-bridge module, every waterwheel part declared in
        # the recipe MUST also appear in the parts list with the same
        # instance id.  This catches the bug class where the recipe and
        # the parts list disagree about waterwheel identity.
        if isinstance(self.recipe, LowerBridgeRecipe):
            parts_by_id = {part.part_id: part for part in self.parts}
            for wheel_part in self.recipe.waterwheel_parts:
                if wheel_part.part_id not in parts_by_id:
                    raise ValueError(
                        f"waterwheel part {wheel_part.part_id} declared in "
                        f"recipe but missing from module parts list",
                    )
                actual = parts_by_id[wheel_part.part_id]
                if actual.instance_id != wheel_part.instance_id:
                    raise ValueError(
                        f"waterwheel part {wheel_part.part_id} instance "
                        f"id disagrees: recipe={wheel_part.instance_id} "
                        f"parts={actual.instance_id}",
                    )
        return self


def _module_design_source(module_id: ModuleId) -> str:
    return {
        "central-courtyard": CENTRAL_COURTYARD_SOURCE_SHA256,
        "lower-bridge-waterwheel": BRIDGE_UNDERCROFT_SOURCE_SHA256,
        "rear-service-courtyard": REAR_SERVICE_COURTYARD_SOURCE_SHA256,
    }[module_id]


# --------------------------------------------------------------------------- #
# Top-level plan.
# --------------------------------------------------------------------------- #


class EnvironmentModuleSummary(FrozenModel):
    module_count: Literal[3] = 3
    part_count: int = Field(ge=1)
    instance_id_segment_start: Literal[131] = 131
    instance_id_segment_end: Literal[175] = 175


class EnvironmentModulePlan(FrozenModel):
    """Additive, content-addressed environment module plan.

    Binds ``ScenePlan`` SHA-256, ``ElevatedTopologyPlan`` SHA-256, the
    three design source SHA-256s, and the recipe version.  Replacing any
    module changes ``module_plan_sha256`` and therefore the build request,
    object registry, and downstream render identity.
    """

    schema_version: Literal[
        "nantai.synthetic-village.environment-module.v1"
    ] = ENVIRONMENT_MODULE_SCHEMA
    plan_id: Literal["synthetic-village-environment-module-v1"] = (
        "synthetic-village-environment-module-v1"
    )
    recipe_version: Literal["v1"] = ENVIRONMENT_MODULE_RECIPE_VERSION
    scene_plan_sha256: Sha256
    elevated_topology_sha256: Sha256
    central_courtyard_source_sha256: Literal[CENTRAL_COURTYARD_SOURCE_SHA256] = (
        CENTRAL_COURTYARD_SOURCE_SHA256
    )
    bridge_undercroft_source_sha256: Literal[BRIDGE_UNDERCROFT_SOURCE_SHA256] = (
        BRIDGE_UNDERCROFT_SOURCE_SHA256
    )
    rear_service_courtyard_source_sha256: Literal[
        REAR_SERVICE_COURTYARD_SOURCE_SHA256
    ] = REAR_SERVICE_COURTYARD_SOURCE_SHA256
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"
    verification_level: Literal["L0"] = "L0"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none"] = "none"
    modules: tuple[EnvironmentModule, ...] = Field(min_length=3, max_length=3)
    summary: EnvironmentModuleSummary

    @model_validator(mode="after")
    def _modules_are_exact_and_ordered(self) -> EnvironmentModulePlan:
        module_ids = tuple(module.module_id for module in self.modules)
        expected = (
            "central-courtyard",
            "lower-bridge-waterwheel",
            "rear-service-courtyard",
        )
        if module_ids != expected:
            raise ValueError(
                "environment modules must be exactly the ordered three",
            )
        # Instance IDs are partitioned across modules -- no overlaps.
        all_instances: list[int] = []
        for module in self.modules:
            for part in module.parts:
                all_instances.append(part.instance_id)
        if len(set(all_instances)) != len(all_instances):
            raise ValueError(
                "environment module part instance IDs must not overlap",
            )
        # The full instance segment is exactly 131..175.
        expected_segment = set(range(131, 176))
        if set(all_instances) != expected_segment:
            raise ValueError(
                "environment module parts must collectively occupy exactly "
                "the 131..175 instance segment",
            )
        return self


def canonical_environment_module_plan_bytes(plan: EnvironmentModulePlan) -> bytes:
    return _canonical(plan.model_dump(mode="json"))


def environment_module_plan_sha256(plan: EnvironmentModulePlan) -> str:
    return hashlib.sha256(
        canonical_environment_module_plan_bytes(plan),
    ).hexdigest()


def verify_environment_module_plan(
    plan: EnvironmentModulePlan,
    *,
    scene: ScenePlan,
    elevated_topology: ElevatedTopologyPlan,
) -> None:
    """Re-bind every identity; raise on any mismatch."""

    expected_scene_sha = hashlib.sha256(
        canonical_scene_plan_bytes(scene),
    ).hexdigest()
    if plan.scene_plan_sha256 != expected_scene_sha:
        raise EnvironmentModuleError(
            "environment module plan scene_plan_sha256 disagrees with scene",
        )
    expected_topology_sha = hashlib.sha256(
        canonical_elevated_topology_bytes(elevated_topology),
    ).hexdigest()
    if plan.elevated_topology_sha256 != expected_topology_sha:
        raise EnvironmentModuleError(
            "environment module plan elevated_topology_sha256 disagrees "
            "with topology",
        )
    # Re-validate canonical bytes (re-runs every model_validator).
    revalidated = EnvironmentModulePlan.model_validate_json(
        canonical_environment_module_plan_bytes(plan),
    )
    if revalidated != plan:
        raise EnvironmentModuleError(
            "environment module plan is not canonical JSON",
        )


def _default_central_courtyard_recipe() -> CentralCourtyardRecipe:
    return CentralCourtyardRecipe(
        bound_object_id="courtyard-public-002",
        bound_ground_attachments=("central-ground-west", "central-ground-east"),
        bound_elevated_edges=(
            "edge-central-stair-001",
            "edge-central-gallery-001",
            "edge-central-ramp-001",
        ),
        gallery=CourtyardGallerySpec(
            clear_width_m=3.0,
            clear_height_m=2.7,
            deck_segment_count=4,
            drainage_channel_present=True,
            east_entry_connects_to="path-network-003",
            west_entry_connects_to="path-network-002",
        ),
        stair=CourtyardStairSpec(
            clear_width_m=2.6,
            tread_count=6,
            tread_depth_m=0.30,
            continuous_collision=True,
        ),
        ramp=CourtyardRampSpec(
            clear_width_m=3.2,
            continuous_collision=True,
            slope_pct=6.0,
        ),
        props=CourtyardPropSpec(
            workshed_count=2,
            workbench_count=4,
            replaceable_prop_slot_count=6,
            planter_tree_non_collision=True,
        ),
        paving_material_slot_id="material-courtyard-flagstone-01",
        drainage_material_slot_id="material-courtyard-drain-01",
    )


def _default_lower_bridge_recipe() -> LowerBridgeRecipe:
    sections = tuple(
        CreekCrossSectionSpec(
            arc_length_m=float(arc),
            terrain_z_m=0.0,
            bank_z_m=-0.4,
            water_z_m=-0.8,
            arch_soffit_z_m=2.6,
            deck_z_m=2.4,
        )
        for arc in (10.0, 25.0, 40.0)
    )
    # Instance IDs MUST match the bridge module's parts list (155-160).
    # Waterwheel parts are never merged with bridge abutment/arch/deck
    # (HANDOFF-OPUS-007 §2): they take the upper end of the bridge segment,
    # leaving 146-154 for bridge structure and creek bed.
    wheel_parts = (
        WaterwheelPartSpec(
            part_id="waterwheel-wheel-001",
            instance_id=155,
            material_slot_id="material-waterwheel-wood-01",
            semantic_id=4,
        ),
        WaterwheelPartSpec(
            part_id="waterwheel-axle-001",
            instance_id=156,
            material_slot_id="material-waterwheel-iron-01",
            semantic_id=4,
        ),
        WaterwheelPartSpec(
            part_id="waterwheel-bracket-001",
            instance_id=157,
            material_slot_id="material-waterwheel-iron-01",
            semantic_id=4,
        ),
        WaterwheelPartSpec(
            part_id="waterwheel-millrace-001",
            instance_id=158,
            material_slot_id="material-waterwheel-wood-01",
            semantic_id=4,
        ),
        WaterwheelPartSpec(
            part_id="waterwheel-spill-001",
            instance_id=159,
            material_slot_id="material-stone-block-01",
            semantic_id=4,
        ),
        WaterwheelPartSpec(
            part_id="waterwheel-tailwater-001",
            instance_id=160,
            material_slot_id="material-stone-block-01",
            semantic_id=4,
        ),
    )
    return LowerBridgeRecipe(
        bound_bridge_object_id="bridge-lower-001",
        bound_creek_object_id="creek-main-001",
        bound_path_networks=("path-network-001", "path-network-005"),
        arch_thickness_m=0.45,
        abutment_support_count=2,
        creek_sections=sections,
        waterwheel_parts=wheel_parts,
        maintenance_platform_is_main_route=False,
        main_route_connectivity_preserved=True,
    )


def _default_rear_service_recipe() -> RearServiceCourtyardRecipe:
    return RearServiceCourtyardRecipe(
        bound_building_object_id="building-central-008",
        paving_conform_to_terrain=True,
        door_window_eaves_gutter_declared=True,
        elevated_access_deck_present=True,
        service_shed_count=2,
        storage_rack_count=4,
        wood_pile_count=3,
        wash_basin_count=2,
        variants=(
            ServiceCourtyardVariantSpec(
                variant_id="service-variant-wood-store",
                replaceable_prop_count=4,
            ),
            ServiceCourtyardVariantSpec(
                variant_id="service-variant-tool-rack",
                replaceable_prop_count=5,
            ),
            ServiceCourtyardVariantSpec(
                variant_id="service-variant-wash-station",
                replaceable_prop_count=3,
            ),
        ),
    )


def _default_module(module_id: ModuleId) -> EnvironmentModule:
    if module_id == "central-courtyard":
        recipe = _default_central_courtyard_recipe()
        part_specs = (
            ("courtyard-paving-001", 131, 2, "material-courtyard-flagstone-01"),
            ("courtyard-gallery-deck-001", 132, 4, "material-courtyard-timber-01"),
            ("courtyard-gallery-roof-001", 133, 4, "material-courtyard-tile-01"),
            ("courtyard-stair-run-001", 134, 4, "material-courtyard-stone-01"),
            ("courtyard-ramp-run-001", 135, 4, "material-courtyard-stone-01"),
            ("courtyard-drainage-channel-001", 136, 6, "material-courtyard-drain-01"),
            ("courtyard-segment-wall-001", 137, 5, "material-courtyard-stone-01"),
            ("courtyard-segment-wall-002", 138, 5, "material-courtyard-stone-01"),
            ("courtyard-workshed-001", 139, 4, "material-courtyard-timber-01"),
            ("courtyard-workshed-002", 140, 4, "material-courtyard-timber-01"),
            ("courtyard-workbench-001", 141, 4, "material-courtyard-timber-01"),
            ("courtyard-workbench-002", 142, 4, "material-courtyard-timber-01"),
            ("courtyard-replaceable-prop-001", 143, 10, "material-courtyard-timber-01"),
            ("courtyard-replaceable-prop-002", 144, 10, "material-courtyard-timber-01"),
            ("courtyard-curb-edge-001", 145, 6, "material-courtyard-stone-01"),
        )
    elif module_id == "lower-bridge-waterwheel":
        recipe = _default_lower_bridge_recipe()
        part_specs = (
            ("bridge-arch-001", 146, 4, "material-stone-block-01"),
            ("bridge-abutment-001", 147, 4, "material-stone-block-01"),
            ("bridge-abutment-002", 148, 4, "material-stone-block-01"),
            ("bridge-deck-slabs-001", 149, 4, "material-stone-block-01"),
            ("bridge-parapet-001", 150, 4, "material-stone-block-01"),
            ("bridge-parapet-002", 151, 4, "material-stone-block-01"),
            ("creek-bed-cut-001", 152, 6, "material-creek-stone-01"),
            ("creek-bank-stone-001", 153, 6, "material-creek-stone-01"),
            ("creek-water-surface-001", 154, 6, "material-water-01"),
            ("waterwheel-wheel-001", 155, 4, "material-waterwheel-wood-01"),
            ("waterwheel-axle-001", 156, 4, "material-waterwheel-iron-01"),
            ("waterwheel-bracket-001", 157, 4, "material-waterwheel-iron-01"),
            ("waterwheel-millrace-001", 158, 4, "material-waterwheel-wood-01"),
            ("waterwheel-spill-001", 159, 4, "material-stone-block-01"),
            ("waterwheel-tailwater-001", 160, 4, "material-stone-block-01"),
        )
    else:
        recipe = _default_rear_service_recipe()
        part_specs = (
            ("service-paving-001", 161, 6, "material-service-stone-01"),
            ("service-back-wall-001", 162, 5, "material-stone-block-01"),
            ("service-side-wall-001", 163, 5, "material-stone-block-01"),
            ("service-side-wall-002", 164, 5, "material-stone-block-01"),
            ("service-door-assembly-001", 165, 5, "material-service-timber-01"),
            ("service-window-assembly-001", 166, 5, "material-service-timber-01"),
            ("service-eaves-001", 167, 5, "material-service-tile-01"),
            ("service-gutter-001", 168, 6, "material-service-iron-01"),
            ("service-drain-outlet-001", 169, 6, "material-service-stone-01"),
            ("service-access-deck-001", 170, 4, "material-service-timber-01"),
            ("service-shed-001", 171, 4, "material-service-timber-01"),
            ("service-shed-002", 172, 4, "material-service-timber-01"),
            ("service-storage-rack-001", 173, 10, "material-service-timber-01"),
            ("service-wood-pile-001", 174, 10, "material-service-timber-01"),
            ("service-wash-basin-001", 175, 10, "material-service-stone-01"),
        )
    parts = tuple(
        EnvironmentModulePart(
            module_id=module_id,
            part_id=part_id,
            instance_id=instance_id,
            semantic_id=semantic_id,
            material_slot_id=material_slot_id,
        )
        for part_id, instance_id, semantic_id, material_slot_id in part_specs
    )
    return EnvironmentModule(
        module_id=module_id,
        design_source_sha256=_module_design_source(module_id),
        parts=parts,
        recipe=recipe,
    )


def build_default_environment_module_plan(
    *,
    scene: ScenePlan,
    elevated_topology: ElevatedTopologyPlan,
) -> EnvironmentModulePlan:
    """Build the canonical default plan bound to the given scene + topology."""

    scene_sha = hashlib.sha256(
        canonical_scene_plan_bytes(scene),
    ).hexdigest()
    topology_sha = hashlib.sha256(
        canonical_elevated_topology_bytes(elevated_topology),
    ).hexdigest()
    modules = tuple(
        _default_module(module_id)
        for module_id in (
            "central-courtyard",
            "lower-bridge-waterwheel",
            "rear-service-courtyard",
        )
    )
    plan = EnvironmentModulePlan(
        scene_plan_sha256=scene_sha,
        elevated_topology_sha256=topology_sha,
        modules=modules,
        summary=EnvironmentModuleSummary(
            part_count=sum(len(module.parts) for module in modules),
        ),
    )
    return plan.model_copy(
        update={},  # trigger re-validation
    )
