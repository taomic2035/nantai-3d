"""Material UV texel density audit (HANDOFF-GLM-007 §5.6 P2b).

Pure-function module that computes the effective tile size (texture repeat
distance in metres) for each material/object-category combination and reports
the variation ratio.  Does NOT promote geometry/trust level; it only measures
UV projection parameters so that texture stretching can be reported objectively.

The effective tile size for a given object is::

    effective_tile_m = nominal_tile_m * tile_scale

where ``nominal_tile_m`` comes from the material registry and ``tile_scale``
comes from the object's ``nv_uv_tile_scale`` property (default 1.0).

A higher effective tile_m means the texture is more stretched (fewer repeats
per metre).  The variation ratio = max(effective_tile_m) / min(effective_tile_m)
across all audited objects; a ratio > 3.0 indicates extreme stretching variation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MaterialTileRecord(BaseModel):
    """A single material's UV tile parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    material_id: str
    nominal_tile_m: float
    uv_policy: str


class ObjectTileRecord(BaseModel):
    """A single object's UV tile scale."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    object_category: str
    material_id: str
    tile_scale: float


class TexelDensityReport(BaseModel):
    """Aggregated texel density variation report."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    per_object: tuple[dict, ...]
    by_category: dict[str, dict]
    overall_min_tile_m: float
    overall_max_tile_m: float
    variation_ratio: float
    extreme_variation: bool


def audit_texel_density(
    materials: list[MaterialTileRecord],
    objects: list[ObjectTileRecord],
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

    material_map: dict[str, MaterialTileRecord] = {}
    for m in materials:
        if m.nominal_tile_m <= 0:
            raise ValueError(
                f"nominal_tile_m must be positive: {m.material_id}"
            )
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
    for obj in objects:
        if obj.tile_scale <= 0:
            raise ValueError(
                f"tile_scale must be positive: {obj.object_category}/{obj.material_id}"
            )
        mat = material_map.get(obj.material_id)
        if mat is None:
            raise ValueError(
                f"material_id not found in registry: {obj.material_id}"
            )
        effective_tile_m = mat.nominal_tile_m * obj.tile_scale
        per_object.append(
            {
                "object_category": obj.object_category,
                "material_id": obj.material_id,
                "nominal_tile_m": mat.nominal_tile_m,
                "tile_scale": obj.tile_scale,
                "effective_tile_m": effective_tile_m,
                "uv_policy": mat.uv_policy,
            }
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
    variation_ratio = overall_max / overall_min if overall_min > 0 else float("inf")

    return TexelDensityReport(
        per_object=tuple(per_object),
        by_category=by_category,
        overall_min_tile_m=overall_min,
        overall_max_tile_m=overall_max,
        variation_ratio=variation_ratio,
        extreme_variation=variation_ratio > 3.0,
    )
