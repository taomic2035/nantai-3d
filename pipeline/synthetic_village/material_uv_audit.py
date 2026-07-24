"""Material UV repeat-distance audit (HANDOFF-GLM-007 §5.6 P2b).

Pure-function module that computes the effective tile size (texture repeat
distance in metres) for each material/object-category combination and reports
the variation ratio. It does not know texture pixel dimensions and therefore
must never call this value "texels per metre". It does not promote geometry or
trust level; it only measures declared UV repeat-distance parameters.

The effective tile size for a given object is::

    effective_tile_m = nominal_tile_m * tile_scale

where ``nominal_tile_m`` comes from the material registry and ``tile_scale``
comes from the object's ``nv_uv_tile_scale`` property (default 1.0).

A higher effective tile_m means the texture is more stretched (fewer repeats
per metre).  The variation ratio = max(effective_tile_m) / min(effective_tile_m)
across all audited objects; a ratio > 3.0 indicates extreme stretching variation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

REQUIRED_AUDIT_CATEGORIES = ("terrain", "creek", "long-wall")


class MaterialTileRecord(BaseModel):
    """A single material's UV tile parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    material_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    nominal_tile_m: float = Field(gt=0.0, allow_inf_nan=False)
    uv_policy: str = Field(min_length=1)


class ObjectTileRecord(BaseModel):
    """A single object's UV tile scale."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    object_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    object_category: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    material_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    tile_scale: float = Field(gt=0.0, allow_inf_nan=False)


class TexelDensityReport(BaseModel):
    """Aggregated UV repeat-distance variation report.

    The historical class name is retained for import compatibility. The
    machine-readable unit prevents consumers from interpreting the result as a
    pixel density measurement.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    per_object: tuple[dict, ...]
    by_category: dict[str, dict]
    overall_min_tile_m: float
    overall_max_tile_m: float
    variation_ratio: float
    extreme_variation: bool
    measurement_unit: Literal["repeat-distance-m"] = "repeat-distance-m"
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )


def audit_texel_density(
    materials: list[MaterialTileRecord],
    objects: list[ObjectTileRecord],
    *,
    required_categories: tuple[str, ...] = REQUIRED_AUDIT_CATEGORIES,
) -> TexelDensityReport:
    """Compute effective tile sizes and report variation.

    Args:
        materials: List of material tile records with nominal_tile_m.
        objects: List of object tile records with tile_scale and category.

    Returns:
        TexelDensityReport with per-object effective tile sizes and
        aggregated variation statistics.

    Raises:
        ValueError: If inputs are empty or contain invalid values.
    """
    if not materials:
        raise ValueError("materials list is empty")
    if not objects:
        raise ValueError("objects list is empty")

    if (
        not required_categories
        or len(set(required_categories)) != len(required_categories)
    ):
        raise ValueError("required categories must be non-empty and unique")

    material_map: dict[str, MaterialTileRecord] = {}
    for m in materials:
        if m.material_id in material_map:
            raise ValueError(f"duplicate material ID: {m.material_id}")
        if m.uv_policy not in {
            "world-xy",
            "dominant-axis-box",
            "roof-slope",
            "object-long-axis",
            "leaf-card",
        }:
            raise ValueError(
                f"unsupported uv_policy: {m.material_id}={m.uv_policy}"
            )
        material_map[m.material_id] = m

    per_object: list[dict] = []
    object_ids: set[str] = set()
    for obj in objects:
        if obj.object_id in object_ids:
            raise ValueError(f"duplicate object ID: {obj.object_id}")
        object_ids.add(obj.object_id)
        mat = material_map.get(obj.material_id)
        if mat is None:
            raise ValueError(
                f"material_id not found in registry: {obj.material_id}"
            )
        effective_tile_m = mat.nominal_tile_m * obj.tile_scale
        per_object.append(
            {
                "object_id": obj.object_id,
                "object_category": obj.object_category,
                "material_id": obj.material_id,
                "nominal_tile_m": mat.nominal_tile_m,
                "tile_scale": obj.tile_scale,
                "effective_tile_m": effective_tile_m,
                "uv_policy": mat.uv_policy,
            }
        )

    present_categories = {row["object_category"] for row in per_object}
    missing_categories = tuple(
        category
        for category in required_categories
        if category not in present_categories
    )
    if missing_categories:
        raise ValueError(
            "required categories have no audited objects: "
            + ", ".join(missing_categories)
        )

    by_category: dict[str, dict] = {}
    for rec in per_object:
        cat = rec["object_category"]
        if cat not in by_category:
            by_category[cat] = {
                "effective_tile_m_values": [],
                "materials": set(),
            }
        by_category[cat]["effective_tile_m_values"].append(
            rec["effective_tile_m"]
        )
        by_category[cat]["materials"].add(rec["material_id"])

    for _cat, data in by_category.items():
        vals = sorted(data["effective_tile_m_values"])
        n = len(vals)
        data["min"] = vals[0]
        data["max"] = vals[-1]
        data["median"] = vals[n // 2]
        data["material_count"] = len(data["materials"])
        data["materials"] = sorted(data["materials"])
        del data["effective_tile_m_values"]

    all_effective = [rec["effective_tile_m"] for rec in per_object]
    overall_min = min(all_effective)
    overall_max = max(all_effective)
    variation_ratio = overall_max / overall_min

    return TexelDensityReport(
        per_object=tuple(per_object),
        by_category=by_category,
        overall_min_tile_m=overall_min,
        overall_max_tile_m=overall_max,
        variation_ratio=variation_ratio,
        extreme_variation=variation_ratio > 3.0,
    )
