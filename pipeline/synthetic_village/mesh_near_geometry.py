"""Deterministic semantic geometry plans for high-detail synthetic LOD2."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from pipeline.synthetic_village.mesh_asset_build import (
    ASSET_RECIPE_CONTRACTS,
    AssetKind,
)
from pipeline.synthetic_village.mesh_asset_bundle import Bounds3
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    LOD2_TRIANGLE_BANDS,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Primitive = Literal[
    "box",
    "bevelled-box",
    "cylinder",
    "roof-tile",
    "thatch-strip",
    "branch",
    "leaf-card",
    "stone-block",
    "frame",
]
Elevation = Literal["east", "north", "south", "west"]

NEAR_GEOMETRY_PLAN_SCHEMA = "nantai.synthetic-village.near-geometry-plan.v1"
NEAR_GEOMETRY_ALGORITHM_ID = "deterministic-semantic-near-geometry-v1"
REGISTERED_FOOTPRINTS = {
    "fence_wood_01": (3.0, 0.2, 1.1),
    "house_barn_01": (12.0, 8.0, 8.0),
    "house_stone_01": (9.0, 7.0, 6.5),
    "house_thatch_01": (7.0, 6.0, 6.0),
    "house_wood_01": (8.0, 6.0, 6.5),
    "house_wood_02": (10.0, 7.0, 7.0),
    "stone_lamp_01": (0.8, 0.8, 2.0),
    "stone_wall_01": (4.0, 0.5, 1.2),
    "tree_bamboo_01": (3.0, 3.0, 10.0),
    "tree_broadleaf_01": (7.0, 7.0, 8.0),
    "tree_pine_01": (4.0, 4.0, 9.0),
}
BUILDING_DETAIL = {
    "roof_tile_columns": 24,
    "roof_tile_rows": 12,
    "window_count_min": 6,
    "door_count_min": 2,
    "frame_members_per_opening": 4,
}
VEGETATION_DETAIL = {
    "tree_bamboo_01": {
        "trunk-or-culm": 12,
        "branch": 96,
        "leaf-card": 3_000,
    },
    "tree_broadleaf_01": {
        "trunk-or-culm": 1,
        "branch": 180,
        "leaf-card": 3_000,
    },
    "tree_pine_01": {
        "trunk-or-culm": 1,
        "branch": 240,
        "leaf-card": 3_000,
    },
}
VEGETATION_MATERIALS = {
    "tree_bamboo_01": (
        "material-bamboo-leaf-01",
        "material-bamboo-stem-01",
    ),
    "tree_broadleaf_01": (
        "material-broadleaf-canopy-01",
        "material-broadleaf-bark-01",
    ),
    "tree_pine_01": (
        "material-orchard-leaf-01",
        "material-orchard-bark-01",
    ),
}
PROP_DETAIL = {
    "fence_wood_01": {"post": 12, "rail": 22, "brace": 10},
    "stone_lamp_01": {"bevelled-part": 48, "cage-member": 12},
    "stone_wall_01": {"stone-block": 96, "cap-stone": 18},
}
BUILDING_SPECIFIC_CLASS = {
    "house_barn_01": "barn-door",
    "house_stone_01": "quoin",
    "house_thatch_01": "thatch-fringe",
    "house_wood_01": "board-seam",
    "house_wood_02": "brace",
}
BUILDING_WALL_MATERIAL = {
    "house_barn_01": "material-dark-timber-01",
    "house_stone_01": "material-fieldstone-01",
    "house_thatch_01": "material-rammed-earth-01",
    "house_wood_01": "material-weathered-timber-01",
    "house_wood_02": "material-pale-plaster-01",
}


class NearGeometryPlanError(ValueError):
    """Near geometry inputs or semantic output cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
    )


def _rotation_matrix(
    rotation_degrees: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    rx, ry, rz = (math.radians(value) for value in rotation_degrees)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return (
        (
            cz * cy,
            cz * sy * sx - sz * cx,
            cz * sy * cx + sz * sx,
        ),
        (
            sz * cy,
            sz * sy * sx + cz * cx,
            sz * sy * cx - cz * sx,
        ),
        (-sy, cy * sx, cy * cx),
    )


def _rotated_box_half_extents(
    scale: tuple[float, float, float],
    rotation_degrees: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Return conservative world-axis extents for a rotated local unit box."""

    matrix = _rotation_matrix(rotation_degrees)
    return tuple(
        sum(
            abs(matrix[axis][source_axis])
            * scale[source_axis]
            / 2
            for source_axis in range(3)
        )
        for axis in range(3)
    )


def _rotated_prism_relative_bounds(
    scale: tuple[float, float, float],
    rotation_degrees: tuple[float, float, float],
    triangles: int,
    *,
    axis: Literal["x", "z"],
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    segments = (triangles + 4) // 4
    if segments < 3 or 4 * segments - 4 != triangles:
        raise ValueError("near prism triangle contract is unsupported")
    vertices = tuple(
        (
            (end, 0.5 * math.cos(angle), 0.5 * math.sin(angle))
            if axis == "x"
            else (0.5 * math.cos(angle), 0.5 * math.sin(angle), end)
        )
        for end in (-0.5, 0.5)
        for angle in (
            2 * math.pi * index / segments
            for index in range(segments)
        )
    )
    matrix = _rotation_matrix(rotation_degrees)
    transformed = tuple(
        tuple(
            sum(
                matrix[world_axis][source_axis]
                * vertex[source_axis]
                * scale[source_axis]
                for source_axis in range(3)
            )
            for world_axis in range(3)
        )
        for vertex in vertices
    )
    return (
        tuple(
            min(vertex[world_axis] for vertex in transformed)
            for world_axis in range(3)
        ),
        tuple(
            max(vertex[world_axis] for vertex in transformed)
            for world_axis in range(3)
        ),
    )


def _component_relative_bounds(
    component: NearComponent,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    if component.primitive in {"cylinder", "branch"}:
        return _rotated_prism_relative_bounds(
            component.scale,
            component.rotation_degrees,
            component.planned_triangles,
            axis="x" if component.primitive == "branch" else "z",
        )
    extents = _rotated_box_half_extents(
        component.scale,
        component.rotation_degrees,
    )
    return (
        tuple(-value for value in extents),
        extents,
    )


def _component_inside_footprint(
    component: NearComponent,
    footprint_m: tuple[float, float, float],
) -> bool:
    relative_min, relative_max = _component_relative_bounds(component)
    minimum = tuple(
        component.position[axis] + relative_min[axis]
        for axis in range(3)
    )
    maximum = tuple(
        component.position[axis] + relative_max[axis]
        for axis in range(3)
    )
    return (
        minimum[0] >= -footprint_m[0] / 2 - 1e-9
        and maximum[0] <= footprint_m[0] / 2 + 1e-9
        and minimum[1] >= -footprint_m[1] / 2 - 1e-9
        and maximum[1] <= footprint_m[1] / 2 + 1e-9
        and minimum[2] >= -1e-9
        and maximum[2] <= footprint_m[2] + 1e-9
    )


class NearComponent(FrozenModel):
    component_id: str = Field(
        pattern=r"^[a-z0-9_]+:[a-z0-9]+(?:-[a-z0-9]+)*:[0-9]{4}$",
    )
    part_class: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    primitive: Primitive
    material_slot_id: str = Field(
        pattern=r"^material-[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation_degrees: tuple[float, float, float]
    planned_triangles: int = Field(ge=2)
    parent_id: str | None = None
    elevation: Elevation | None = None

    @model_validator(mode="after")
    def _finite_visible_component(self) -> NearComponent:
        if (
            not all(
                math.isfinite(value)
                for value in (
                    *self.position,
                    *self.scale,
                    *self.rotation_degrees,
                )
            )
            or not all(value > 0 for value in self.scale)
        ):
            raise ValueError(
                "near component transform must be finite and visible",
            )
        return self


class NearGeometryPlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.near-geometry-plan.v1"
    ] = NEAR_GEOMETRY_PLAN_SCHEMA
    plan_id: Sha256
    algorithm_id: Literal[
        "deterministic-semantic-near-geometry-v1"
    ] = NEAR_GEOMETRY_ALGORITHM_ID
    asset_id: str = Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
    kind: AssetKind
    footprint_m: tuple[float, float, float]
    recipe_id: str = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    material_slot_ids: tuple[str, ...] = Field(min_length=1)
    aabb: Bounds3
    covered_elevations: tuple[Elevation, ...] = ()
    detail_counts: dict[str, int]
    planned_triangles: int = Field(ge=1)
    components: tuple[NearComponent, ...] = Field(min_length=1)
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"

    @model_validator(mode="after")
    def _complete_semantic_plan(self) -> NearGeometryPlan:
        contract = ASSET_RECIPE_CONTRACTS.get(self.asset_id)
        expected_footprint = REGISTERED_FOOTPRINTS.get(self.asset_id)
        if contract is None or expected_footprint is None:
            raise ValueError("near geometry asset is not registered")
        expected_recipe = (
            contract[1].removesuffix("-v1") + "-near-v2"
        )
        if (
            self.kind != contract[0]
            or self.footprint_m != expected_footprint
            or self.recipe_id != expected_recipe
            or self.material_slot_ids != contract[2]
        ):
            raise ValueError(
                "near geometry plan differs from its registered contract",
            )
        expected_bounds = (
            (
                -self.footprint_m[0] / 2,
                -self.footprint_m[1] / 2,
                0.0,
            ),
            (
                self.footprint_m[0] / 2,
                self.footprint_m[1] / 2,
                self.footprint_m[2],
            ),
        )
        if (self.aabb.min, self.aabb.max) != expected_bounds:
            raise ValueError(
                "near geometry bounds differ from the registered footprint",
            )
        component_ids = tuple(
            row.component_id for row in self.components
        )
        if (
            component_ids != tuple(sorted(component_ids))
            or len(set(component_ids)) != len(component_ids)
        ):
            raise ValueError(
                "near geometry component IDs must be sorted and unique",
            )
        component_set = set(component_ids)
        if any(
            row.parent_id is not None
            and row.parent_id not in component_set
            for row in self.components
        ):
            raise ValueError(
                "near geometry parent component does not exist",
            )
        if any(
            row.material_slot_id not in self.material_slot_ids
            for row in self.components
        ):
            raise ValueError(
                "near geometry component uses an undeclared material",
            )
        if any(
            not _component_inside_footprint(row, self.footprint_m)
            for row in self.components
        ):
            raise ValueError(
                "near geometry component exceeds the registered footprint",
            )
        if self.planned_triangles != sum(
            row.planned_triangles for row in self.components
        ):
            raise ValueError(
                "near geometry triangle total differs from its components",
            )
        lower, upper = LOD2_TRIANGLE_BANDS[self.kind]
        if not lower <= self.planned_triangles <= upper:
            raise ValueError(
                "near geometry triangle total is outside its exact band",
            )
        expected_elevations = (
            ("east", "north", "south", "west")
            if self.kind == "building"
            else ()
        )
        if self.covered_elevations != expected_elevations:
            raise ValueError(
                "near geometry elevation coverage is incomplete",
            )
        digest = hashlib.sha256(
            canonical_near_geometry_plan_bytes(
                self,
                exclude_plan_id=True,
            ),
        ).hexdigest()
        if digest != self.plan_id:
            raise ValueError(
                "near geometry plan ID does not match canonical content",
            )
        return self


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_near_geometry_plan_bytes(
    plan: NearGeometryPlan,
    *,
    exclude_plan_id: bool = False,
) -> bytes:
    payload = plan.model_dump(mode="json")
    if exclude_plan_id:
        payload.pop("plan_id")
    return _canonical_json_bytes(payload)


def stable_unit(asset_id: str, component_id: str, channel: str) -> float:
    digest = hashlib.sha256(
        f"{asset_id}:{component_id}:{channel}".encode(),
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def _clean(value: float) -> float:
    result = round(float(value), 9)
    return 0.0 if abs(result) < 1e-12 else result


class _ComponentBuilder:
    def __init__(self, asset_id: str) -> None:
        self.asset_id = asset_id
        self.components: list[NearComponent] = []
        self.counts: defaultdict[str, int] = defaultdict(int)

    def add(
        self,
        part_class: str,
        primitive: Primitive,
        material_slot_id: str,
        position: tuple[float, float, float],
        scale: tuple[float, float, float],
        *,
        rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
        triangles: int = 12,
        parent_id: str | None = None,
        elevation: Elevation | None = None,
    ) -> str:
        index = self.counts[part_class]
        self.counts[part_class] += 1
        component_id = f"{self.asset_id}:{part_class}:{index:04d}"
        self.components.append(
            NearComponent(
                component_id=component_id,
                part_class=part_class,
                primitive=primitive,
                material_slot_id=material_slot_id,
                position=tuple(_clean(value) for value in position),
                scale=tuple(_clean(value) for value in scale),
                rotation_degrees=tuple(
                    _clean(value) for value in rotation
                ),
                planned_triangles=triangles,
                parent_id=parent_id,
                elevation=elevation,
            ),
        )
        return component_id


def _material_like(
    slots: tuple[str, ...],
    *needles: str,
) -> str:
    return next(
        (
            slot
            for needle in needles
            for slot in slots
            if needle in slot
        ),
        slots[0],
    )


def _opening_transform(
    elevation: Elevation,
    ordinal: int,
    footprint: tuple[float, float, float],
    *,
    door: bool,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]:
    width, depth, height = footprint
    z_scale = height * (0.28 if door else 0.18)
    z = z_scale / 2 if door else height * (0.28 + 0.08 * (ordinal % 2))
    offset = (-0.22 + 0.22 * (ordinal % 3))
    if elevation in {"east", "west"}:
        return (
            (
                width * (0.485 if elevation == "east" else -0.485),
                depth * offset,
                z,
            ),
            (width * 0.015, depth * 0.16, z_scale),
        )
    return (
        (
            width * offset,
            depth * (0.485 if elevation == "north" else -0.485),
            z,
        ),
        (width * 0.16, depth * 0.015, z_scale),
    )


def _build_building(
    asset_id: str,
    footprint: tuple[float, float, float],
    slots: tuple[str, ...],
) -> tuple[tuple[NearComponent, ...], dict[str, int]]:
    width, depth, height = footprint
    wall_height = height * 0.60
    builder = _ComponentBuilder(asset_id)
    frame_material = _material_like(
        slots,
        "timber",
        "bamboo",
    )
    roof_material = _material_like(
        slots,
        "roof",
        "woven",
    )
    wall_material = BUILDING_WALL_MATERIAL[asset_id]
    builder.add(
        "foundation",
        "bevelled-box",
        wall_material,
        (0.0, 0.0, height * 0.025),
        (width * 0.96, depth * 0.96, height * 0.05),
        triangles=28,
    )
    walls = (
        ("east", (width * 0.49, 0.0, wall_height / 2), (width * 0.02, depth, wall_height)),
        ("north", (0.0, depth * 0.49, wall_height / 2), (width, depth * 0.02, wall_height)),
        ("south", (0.0, -depth * 0.49, wall_height / 2), (width, depth * 0.02, wall_height)),
        ("west", (-width * 0.49, 0.0, wall_height / 2), (width * 0.02, depth, wall_height)),
    )
    for elevation, position, scale in walls:
        builder.add(
            "wall",
            "bevelled-box",
            wall_material,
            position,
            scale,
            triangles=28,
            elevation=elevation,
        )
    for side in (-1, 1):
        builder.add(
            "roof-shell",
            "box",
            roof_material,
            (0.0, side * depth * 0.24, wall_height + (height - wall_height) * 0.42),
            (width, depth * 0.50, height * 0.06),
            rotation=(side * 32.0, 0.0, 0.0),
        )
    roof_primitive: Primitive = (
        "thatch-strip"
        if asset_id == "house_thatch_01"
        else "roof-tile"
    )
    for side in (-1, 1):
        for row in range(BUILDING_DETAIL["roof_tile_rows"]):
            row_fraction = (row + 0.5) / BUILDING_DETAIL["roof_tile_rows"]
            for column in range(BUILDING_DETAIL["roof_tile_columns"]):
                column_fraction = (
                    column + 0.5
                ) / BUILDING_DETAIL["roof_tile_columns"]
                builder.add(
                    "roof-detail",
                    roof_primitive,
                    roof_material,
                    (
                        -width / 2 + width * column_fraction,
                        side * depth * (0.48 - 0.44 * row_fraction),
                        wall_height
                        + (height - wall_height)
                        * (0.12 + 0.78 * row_fraction),
                    ),
                    (
                        width / BUILDING_DETAIL["roof_tile_columns"] * 0.98,
                        depth / BUILDING_DETAIL["roof_tile_rows"] * 0.58,
                        height * 0.018,
                    ),
                    rotation=(side * 32.0, 0.0, 0.0),
                    triangles=12,
                )
    for elevation, position, _scale in walls:
        if elevation in {"east", "west"}:
            eave_position = (
                position[0],
                0.0,
                wall_height + height * 0.01,
            )
            eave_scale = (width * 0.02, depth, height * 0.035)
        else:
            eave_position = (
                0.0,
                position[1],
                wall_height + height * 0.01,
            )
            eave_scale = (width, depth * 0.02, height * 0.035)
        builder.add(
            "eave",
            "bevelled-box",
            frame_material,
            eave_position,
            eave_scale,
            triangles=28,
            elevation=elevation,
        )
    opening_rows = (
        *(("window-opening", elevation, index) for index, elevation in enumerate(
            ("east", "west", "north", "south", "east", "west"),
        )),
        ("door-opening", "north", 0),
        ("door-opening", "south", 1),
    )
    for part_class, elevation, ordinal in opening_rows:
        position, scale = _opening_transform(
            elevation,
            ordinal,
            footprint,
            door=part_class == "door-opening",
        )
        opening_id = builder.add(
            part_class,
            "box",
            frame_material,
            position,
            scale,
            triangles=2,
            elevation=elevation,
        )
        for member in range(
            BUILDING_DETAIL["frame_members_per_opening"],
        ):
            horizontal = member >= 2
            member_scale = (
                (
                    scale[0],
                    scale[1] * 1.12,
                    height * 0.025,
                )
                if horizontal
                else (
                    max(scale[0], width * 0.02),
                    max(scale[1], depth * 0.02),
                    scale[2],
                )
            )
            builder.add(
                "frame",
                "frame",
                frame_material,
                position,
                member_scale,
                triangles=12,
                parent_id=opening_id,
                elevation=elevation,
            )
    for index in range(8):
        builder.add(
            "roof-ridge",
            "bevelled-box",
            roof_material,
            (
                -width * 0.42 + index * width * 0.12,
                0.0,
                height * 0.965,
            ),
            (width * 0.11, depth * 0.045, height * 0.035),
            triangles=28,
        )
    for index in range(24):
        elevation: Elevation = (
            "east",
            "north",
            "south",
            "west",
        )[index % 4]
        position, scale = _opening_transform(
            elevation,
            index,
            footprint,
            door=False,
        )
        builder.add(
            "surface-break",
            "bevelled-box",
            wall_material,
            (
                position[0] * 1.005,
                position[1] * 1.005,
                height * (0.16 + 0.025 * (index % 8)),
            ),
            (
                max(scale[0] * 0.35, width * 0.012),
                max(scale[1] * 0.35, depth * 0.012),
                height * 0.035,
            ),
            triangles=28,
            elevation=elevation,
        )
    specific_class = BUILDING_SPECIFIC_CLASS[asset_id]
    specific_primitive: Primitive = {
        "quoin": "stone-block",
        "thatch-fringe": "thatch-strip",
    }.get(specific_class, "frame")
    specific_material = (
        wall_material
        if specific_class == "quoin"
        else roof_material
        if specific_class == "thatch-fringe"
        else _material_like(slots, "weathered")
        if specific_class == "barn-door"
        else frame_material
    )
    for index in range(64):
        elevation = (
            "east",
            "north",
            "south",
            "west",
        )[index % 4]
        position, _opening_scale = _opening_transform(
            elevation,
            index,
            footprint,
            door=False,
        )
        builder.add(
            specific_class,
            specific_primitive,
            specific_material,
            (
                position[0],
                position[1],
                height * (0.08 + 0.05 * (index % 10)),
            ),
            (
                width * 0.025,
                depth * 0.025,
                height * 0.08,
            ),
            rotation=(0.0, 0.0, (index % 3 - 1) * 8.0),
            triangles=28 if specific_primitive == "stone-block" else 12,
            elevation=elevation,
        )
    detail_counts = {
        **BUILDING_DETAIL,
        "roof_detail_count": (
            2
            * BUILDING_DETAIL["roof_tile_columns"]
            * BUILDING_DETAIL["roof_tile_rows"]
        ),
        "window_count": 6,
        "door_count": 2,
    }
    return tuple(sorted(
        builder.components,
        key=lambda row: row.component_id,
    )), detail_counts


def _build_vegetation(
    asset_id: str,
    footprint: tuple[float, float, float],
    slots: tuple[str, ...],
) -> tuple[tuple[NearComponent, ...], dict[str, int]]:
    width, depth, height = footprint
    builder = _ComponentBuilder(asset_id)
    foliage_material, structure_material = VEGETATION_MATERIALS[asset_id]
    if {foliage_material, structure_material} != set(slots):
        raise NearGeometryPlanError(
            "vegetation materials differ from the registered recipe",
        )
    counts = VEGETATION_DETAIL[asset_id]
    trunk_ids = []
    for index in range(counts["trunk-or-culm"]):
        token = f"trunk-{index:04d}"
        x = (
            (stable_unit(asset_id, token, "x") - 0.5) * width * 0.38
            if counts["trunk-or-culm"] > 1
            else 0.0
        )
        y = (
            (stable_unit(asset_id, token, "y") - 0.5) * depth * 0.38
            if counts["trunk-or-culm"] > 1
            else 0.0
        )
        radius = width * (
            0.018
            if asset_id == "tree_bamboo_01"
            else 0.065
        )
        trunk_scale = (radius, radius, height * 0.92)
        trunk_rotation = (
            (stable_unit(asset_id, token, "lean-x") - 0.5) * 4.0,
            (stable_unit(asset_id, token, "lean-y") - 0.5) * 4.0,
            0.0,
        )
        trunk_relative_min, _ = _rotated_prism_relative_bounds(
            trunk_scale,
            trunk_rotation,
            48 if asset_id == "tree_bamboo_01" else 64,
            axis="z",
        )
        trunk_z = -trunk_relative_min[2]
        trunk_ids.append(
            builder.add(
                "trunk-or-culm",
                "cylinder",
                structure_material,
                (x, y, trunk_z),
                trunk_scale,
                rotation=trunk_rotation,
                triangles=(
                    48 if asset_id == "tree_bamboo_01" else 64
                ),
            ),
        )
    if asset_id == "tree_bamboo_01":
        for trunk_index, trunk_id in enumerate(trunk_ids):
            token = f"trunk-{trunk_index:04d}"
            x = (stable_unit(asset_id, token, "x") - 0.5) * width * 0.38
            y = (stable_unit(asset_id, token, "y") - 0.5) * depth * 0.38
            for node in range(8):
                builder.add(
                    "culm-node",
                    "cylinder",
                    structure_material,
                    (x, y, height * (0.10 + node * 0.105)),
                    (width * 0.026, width * 0.026, height * 0.008),
                    triangles=16,
                    parent_id=trunk_id,
                )
    branch_ids = []
    for index in range(counts["branch"]):
        token = f"branch-{index:04d}"
        parent = trunk_ids[index % len(trunk_ids)]
        yaw = 360.0 * stable_unit(asset_id, token, "yaw")
        radial = width * 0.20 * stable_unit(asset_id, token, "radius")
        radians = math.radians(yaw)
        branch_ids.append(
            builder.add(
                "branch",
                "branch",
                structure_material,
                (
                    math.cos(radians) * radial,
                    math.sin(radians) * radial,
                    height
                    * (
                        0.28
                        + 0.58
                        * stable_unit(asset_id, token, "height")
                    ),
                ),
                (
                    width
                    * (
                        0.16
                        + 0.12
                        * stable_unit(asset_id, token, "length")
                    ),
                    width * 0.012,
                    width * 0.012,
                ),
                rotation=(
                    0.0,
                    -38.0
                    + 24.0
                    * stable_unit(asset_id, token, "pitch"),
                    yaw,
                ),
                triangles=16,
                parent_id=parent,
            ),
        )
    for index in range(counts["leaf-card"]):
        token = f"leaf-{index:04d}"
        parent = branch_ids[index % len(branch_ids)]
        builder.add(
            "leaf-card",
            "leaf-card",
            foliage_material,
            (
                (stable_unit(asset_id, token, "x") - 0.5)
                * width
                * 0.78,
                (stable_unit(asset_id, token, "y") - 0.5)
                * depth
                * 0.78,
                height
                * (
                    0.22
                    + 0.72 * stable_unit(asset_id, token, "z")
                ),
            ),
            (
                width
                * (
                    0.035
                    + 0.035 * stable_unit(asset_id, token, "width")
                ),
                height
                * (
                    0.025
                    + 0.025 * stable_unit(asset_id, token, "length")
                ),
                0.01,
            ),
            rotation=(
                -24.0
                + 48.0 * stable_unit(asset_id, token, "roll"),
                -30.0
                + 60.0 * stable_unit(asset_id, token, "bend"),
                360.0 * stable_unit(asset_id, token, "yaw"),
            ),
            triangles=2,
            parent_id=parent,
        )
    detail_counts = dict(counts)
    if asset_id == "tree_bamboo_01":
        detail_counts["culm-node"] = 96
    return tuple(sorted(
        builder.components,
        key=lambda row: row.component_id,
    )), detail_counts


def _build_prop(
    asset_id: str,
    footprint: tuple[float, float, float],
    slots: tuple[str, ...],
) -> tuple[tuple[NearComponent, ...], dict[str, int]]:
    width, depth, height = footprint
    builder = _ComponentBuilder(asset_id)
    counts = PROP_DETAIL[asset_id]
    if asset_id == "fence_wood_01":
        material = slots[0]
        for part_class, count in counts.items():
            for index in range(count):
                fraction = (index + 0.5) / count
                z = (
                    height * 0.45
                    if part_class == "post"
                    else height * (0.24 + 0.5 * (index % 2))
                )
                builder.add(
                    part_class,
                    "bevelled-box" if part_class != "brace" else "frame",
                    material,
                    (-width / 2 + width * fraction, 0.0, z),
                    (
                        (
                            width * 0.035
                            if part_class == "post"
                            else width / count * 0.90
                        ),
                        depth * 0.92,
                        height * (0.90 if part_class == "post" else 0.07),
                    ),
                    rotation=(
                        0.0,
                        (
                            -24.0
                            if part_class == "brace" and index % 2
                            else 24.0 if part_class == "brace" else 0.0
                        ),
                        (
                            stable_unit(
                                asset_id,
                                f"{part_class}-{index}",
                                "yaw",
                            )
                            - 0.5
                        )
                        * 2.0,
                    ),
                    triangles=28 if part_class != "brace" else 24,
                )
    elif asset_id == "stone_lamp_01":
        stone = _material_like(slots, "stone")
        metal = _material_like(slots, "metal")
        for index in range(counts["bevelled-part"]):
            layer = index // 8
            angle = 2 * math.pi * (index % 8) / 8
            radius = width * (0.18 + 0.025 * (layer % 2))
            builder.add(
                "bevelled-part",
                "bevelled-box",
                stone,
                (
                    math.cos(angle) * radius,
                    math.sin(angle) * radius,
                    height * (0.05 + layer * 0.11),
                ),
                (width * 0.16, depth * 0.16, height * 0.10),
                rotation=(0.0, 0.0, math.degrees(angle)),
                triangles=28,
            )
        for index in range(counts["cage-member"]):
            angle = 2 * math.pi * index / counts["cage-member"]
            builder.add(
                "cage-member",
                "frame",
                metal,
                (
                    math.cos(angle) * width * 0.22,
                    math.sin(angle) * depth * 0.22,
                    height * 0.80,
                ),
                (width * 0.035, depth * 0.035, height * 0.30),
                rotation=(0.0, 0.0, math.degrees(angle)),
                triangles=24,
            )
    else:
        material = slots[0]
        for index in range(counts["stone-block"]):
            course = index // 24
            column = index % 24
            token = f"block-{index:04d}"
            scale = (
                width / 24 * 0.92,
                depth
                * (
                    0.62
                    + 0.20
                    * stable_unit(asset_id, token, "thickness")
                ),
                height * 0.18,
            )
            rotation = (
                0.0,
                0.0,
                (
                    stable_unit(asset_id, token, "yaw") - 0.5
                )
                * 8.0,
            )
            nominal_x = -width / 2 + width * (column + 0.5) / 24
            half_x = _rotated_box_half_extents(scale, rotation)[0]
            x = max(
                -width / 2 + half_x + 1e-6,
                min(width / 2 - half_x - 1e-6, nominal_x),
            )
            builder.add(
                "stone-block",
                "stone-block",
                material,
                (
                    x,
                    (
                        stable_unit(asset_id, token, "depth") - 0.5
                    )
                    * depth
                    * 0.08,
                    height * (0.09 + course * 0.20),
                ),
                scale,
                rotation=rotation,
                triangles=28,
            )
        for index in range(counts["cap-stone"]):
            scale = (
                width / counts["cap-stone"] * 0.96,
                depth * 0.92,
                height * 0.16,
            )
            rotation = (0.0, 0.0, (-1) ** index * 2.0)
            nominal_x = (
                -width / 2
                + width * (index + 0.5) / counts["cap-stone"]
            )
            half_x = _rotated_box_half_extents(scale, rotation)[0]
            x = max(
                -width / 2 + half_x + 1e-6,
                min(width / 2 - half_x - 1e-6, nominal_x),
            )
            builder.add(
                "cap-stone",
                "stone-block",
                material,
                (
                    x,
                    0.0,
                    height * 0.91,
                ),
                scale,
                rotation=rotation,
                triangles=28,
            )
    return tuple(sorted(
        builder.components,
        key=lambda row: row.component_id,
    )), dict(counts)


def build_near_geometry_plan(
    asset_id: str,
    footprint_m: tuple[float, float, float],
) -> NearGeometryPlan:
    """Build one path-free exact semantic plan for a registered LOD2 asset."""

    contract = ASSET_RECIPE_CONTRACTS.get(asset_id)
    expected_footprint = REGISTERED_FOOTPRINTS.get(asset_id)
    if contract is None or expected_footprint is None:
        raise NearGeometryPlanError(
            "near geometry asset is not registered",
        )
    try:
        footprint = tuple(footprint_m)
    except TypeError as exc:
        raise NearGeometryPlanError(
            "near geometry footprint is invalid",
        ) from exc
    if footprint != expected_footprint:
        raise NearGeometryPlanError(
            "near geometry footprint differs from the registry",
        )
    kind, v1_recipe, slots = contract
    if kind == "building":
        components, detail_counts = _build_building(
            asset_id,
            footprint,
            slots,
        )
        covered_elevations = ("east", "north", "south", "west")
    elif kind == "vegetation":
        components, detail_counts = _build_vegetation(
            asset_id,
            footprint,
            slots,
        )
        covered_elevations = ()
    else:
        components, detail_counts = _build_prop(
            asset_id,
            footprint,
            slots,
        )
        covered_elevations = ()
    unsigned = {
        "schema_version": NEAR_GEOMETRY_PLAN_SCHEMA,
        "algorithm_id": NEAR_GEOMETRY_ALGORITHM_ID,
        "asset_id": asset_id,
        "kind": kind,
        "footprint_m": footprint,
        "recipe_id": v1_recipe.removesuffix("-v1") + "-near-v2",
        "material_slot_ids": slots,
        "aabb": Bounds3(
            min=(-footprint[0] / 2, -footprint[1] / 2, 0.0),
            max=(footprint[0] / 2, footprint[1] / 2, footprint[2]),
        ),
        "covered_elevations": covered_elevations,
        "detail_counts": detail_counts,
        "planned_triangles": sum(
            row.planned_triangles for row in components
        ),
        "components": components,
        "synthetic": True,
        "geometry_usability": "preview-only",
    }
    plan_id = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
    try:
        return NearGeometryPlan(plan_id=plan_id, **unsigned)
    except ValidationError as exc:
        raise NearGeometryPlanError(
            f"near geometry plan is invalid: {exc}",
        ) from exc
