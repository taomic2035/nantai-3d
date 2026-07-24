"""Measure UV repeat density in a bound Blender build.

The measured quantity is UV-coordinate area per square metre of evaluated
mesh surface. It is not texels per metre because this probe does not bind
texture pixel dimensions. The historical filename is retained for caller
compatibility.

Usage::

    blender --background scene.blend --python probe_uv_texel_density.py -- \
      --output uv-repeat-report.json \
      --build-report perimeter-closure-build-report.json
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

import bpy

REPORT_SCHEMA = "nantai.synthetic-village.uv-repeat-density-probe.v1"
REQUIRED_CATEGORIES = ("terrain", "creek", "long-wall")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class UvProbeError(RuntimeError):
    """Fail-closed UV measurement or identity error."""


def _canonical_bytes(payload):
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_args(argv):
    try:
        marker = argv.index("--")
    except ValueError as exc:
        raise UvProbeError("missing Blender argument separator") from exc
    values = argv[marker + 1 :]
    if (
        len(values) != 4
        or values[0] != "--output"
        or values[2] != "--build-report"
    ):
        raise UvProbeError(
            "expected --output <json> --build-report <json>"
        )
    return Path(values[1]), Path(values[3])


def _triangle_area_uv(a, b, c):
    return 0.5 * abs(
        (b[0] - a[0]) * (c[1] - a[1])
        - (c[0] - a[0]) * (b[1] - a[1])
    )


def _triangle_area_world(a, b, c):
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return 0.5 * math.sqrt(sum(value * value for value in cross))


def _measure_triangles(triangles):
    """Measure already-triangulated world/UV coordinate tuples."""

    uv_area_total = 0.0
    mesh_area_total = 0.0
    triangle_count = 0
    for world_vertices, uv_vertices in triangles:
        if len(world_vertices) != 3 or len(uv_vertices) != 3:
            raise UvProbeError("evaluated UV measurement is not triangular")
        values = tuple(
            float(value)
            for vertex in (*world_vertices, *uv_vertices)
            for value in vertex
        )
        if not all(math.isfinite(value) for value in values):
            raise UvProbeError("UV measurement coordinates must be finite")
        uv_area = _triangle_area_uv(*uv_vertices)
        mesh_area = _triangle_area_world(*world_vertices)
        if uv_area <= 1e-12 or mesh_area <= 1e-12:
            continue
        uv_area_total += uv_area
        mesh_area_total += mesh_area
        triangle_count += 1
    if triangle_count == 0 or mesh_area_total <= 0.0:
        raise UvProbeError("audited object has zero measurable UV surface")
    return {
        "uv_area_total": uv_area_total,
        "mesh_area_total_m2": mesh_area_total,
        "uv_area_per_m2": uv_area_total / mesh_area_total,
        "triangle_count": triangle_count,
    }


def _category_for_object(obj):
    explicit = obj.get("nv_uv_audit_category")
    if explicit is not None:
        if explicit not in {*REQUIRED_CATEGORIES, "other"}:
            raise UvProbeError(
                f"unsupported UV audit category: {explicit}"
            )
        return explicit
    name = obj.name.lower()
    if name.startswith("nv__aux-terrain") or "terrain" in name:
        return "terrain"
    if "creek" in name or "water" in name:
        return "creek"
    if any(token in name for token in ("wall", "building", "bridge")):
        return "long-wall"
    return "other"


def _object_material_ids(obj):
    material_ids = []
    for slot in obj.material_slots:
        material = slot.material
        if material is None:
            continue
        material_id = material.get("nv_material_id", material.name)
        if not isinstance(material_id, str) or not material_id:
            raise UvProbeError(
                f"object material identity is invalid: {obj.name}"
            )
        material_ids.append(material_id)
    return sorted(set(material_ids))


def _measurement_object_identity(obj):
    object_id = obj.name
    stable_root_id = obj.get("nv_stable_id", obj.name)
    if (
        not isinstance(object_id, str)
        or not object_id
        or not isinstance(stable_root_id, str)
        or not stable_root_id
    ):
        raise UvProbeError(
            f"object or stable-root identity is invalid: {obj.name}"
        )
    return {
        "object_id": object_id,
        "stable_root_id": stable_root_id,
    }


def measure_object_uv_density(obj, depsgraph):
    """Measure one evaluated mesh, including quads/ngons via loop triangles."""

    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(
        preserve_all_data_layers=True,
        depsgraph=depsgraph,
    )
    try:
        if mesh is None or not mesh.polygons:
            return None
        uv_layer = mesh.uv_layers.get(obj.get("nv_uv_layer", "nv_uv0"))
        if uv_layer is None:
            uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            return None

        mesh.calc_loop_triangles()
        triangles = []
        for triangle in mesh.loop_triangles:
            world_vertices = tuple(
                tuple(
                    float(value)
                    for value in (
                        evaluated.matrix_world
                        @ mesh.vertices[vertex_index].co
                    )
                )
                for vertex_index in triangle.vertices
            )
            uv_vertices = tuple(
                tuple(float(value) for value in uv_layer.data[loop_index].uv)
                for loop_index in triangle.loops
            )
            triangles.append((world_vertices, uv_vertices))
        measured = _measure_triangles(tuple(triangles))
        tile_scale = float(obj.get("nv_uv_tile_scale", 1.0))
        if not math.isfinite(tile_scale) or tile_scale <= 0.0:
            raise UvProbeError(
                f"object tile scale must be finite and positive: {obj.name}"
            )
        return {
            **_measurement_object_identity(obj),
            "object_name": obj.name,
            "category": _category_for_object(obj),
            "material_ids": _object_material_ids(obj),
            **measured,
            "tile_scale": tile_scale,
        }
    finally:
        evaluated.to_mesh_clear()


def _summarize_measurements(
    measurements,
    required_categories=REQUIRED_CATEGORIES,
):
    if not measurements:
        raise UvProbeError("UV probe produced no object measurements")
    if (
        not required_categories
        or len(set(required_categories)) != len(required_categories)
    ):
        raise UvProbeError(
            "required categories must be non-empty and unique"
        )

    object_ids = set()
    by_category = {}
    for row in measurements:
        object_id = row.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise UvProbeError("audited object identity is missing")
        if object_id in object_ids:
            raise UvProbeError(f"duplicate object identity: {object_id}")
        object_ids.add(object_id)
        material_ids = row.get("material_ids")
        if (
            not isinstance(material_ids, list)
            or material_ids != sorted(set(material_ids))
            or not material_ids
        ):
            raise UvProbeError(
                f"audited material identities are invalid: {object_id}"
            )
        numeric = (
            row.get("uv_area_total"),
            row.get("mesh_area_total_m2"),
            row.get("uv_area_per_m2"),
            row.get("tile_scale"),
        )
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0.0
            for value in numeric
        ):
            raise UvProbeError(
                f"UV measurements must be finite and positive: {object_id}"
            )
        triangle_count = row.get("triangle_count")
        if (
            not isinstance(triangle_count, int)
            or isinstance(triangle_count, bool)
            or triangle_count <= 0
        ):
            raise UvProbeError(
                f"triangle count must be positive: {object_id}"
            )
        category = row.get("category")
        if not isinstance(category, str) or not category:
            raise UvProbeError(
                f"audited object category is invalid: {object_id}"
            )
        by_category.setdefault(category, []).append(row)

    missing = tuple(
        category
        for category in required_categories
        if category not in by_category
    )
    if missing:
        raise UvProbeError(
            "required categories have no measured UV surface: "
            + ", ".join(missing)
        )

    summaries = {}
    all_ratios = []
    for category, rows in sorted(by_category.items()):
        ratios = sorted(float(row["uv_area_per_m2"]) for row in rows)
        all_ratios.extend(ratios)
        summaries[category] = {
            "object_count": len(rows),
            "triangle_count": sum(
                int(row["triangle_count"]) for row in rows
            ),
            "uv_area_per_m2_min": ratios[0],
            "uv_area_per_m2_max": ratios[-1],
            "uv_area_per_m2_median": ratios[len(ratios) // 2],
            "variation_ratio": ratios[-1] / ratios[0],
        }
    return {
        "by_category": summaries,
        "overall_uv_area_per_m2_min": min(all_ratios),
        "overall_uv_area_per_m2_max": max(all_ratios),
        "overall_variation_ratio": max(all_ratios) / min(all_ratios),
    }


def _build_report(*, identities, measurements):
    expected_keys = {
        "source_blend_sha256",
        "build_report_sha256",
        "probe_script_sha256",
        "blender_executable_sha256",
    }
    if (
        set(identities) != expected_keys
        or any(
            not isinstance(value, str)
            or SHA256_PATTERN.fullmatch(value) is None
            for value in identities.values()
        )
    ):
        raise UvProbeError("UV probe runtime identity set is invalid")
    summary = _summarize_measurements(measurements)
    payload = {
        "schema_version": REPORT_SCHEMA,
        **identities,
        "measurement_unit": "uv-area-per-m2",
        "object_count": len(measurements),
        "per_object": sorted(
            measurements,
            key=lambda row: row["object_id"],
        ),
        **summary,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "real_photo_texture": False,
        "trust_effect": "none-quality-filter-only",
    }
    payload["content_sha256"] = hashlib.sha256(
        _canonical_bytes(payload)
    ).hexdigest()
    return payload


def _write_report(path, report):
    path = Path(path).absolute()
    if path.exists():
        raise UvProbeError("UV probe output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp")
    if staging.exists():
        raise UvProbeError("UV probe staging output already exists")
    staging.write_bytes(_canonical_bytes(report))
    staging.replace(path)


def main():
    output_path, build_report_path = _runtime_args(sys.argv)
    blend_path = Path(bpy.data.filepath)
    executable_path = Path(bpy.app.binary_path)
    script_path = Path(__file__)
    for path, label in (
        (blend_path, "source blend"),
        (build_report_path, "build report"),
        (executable_path, "Blender executable"),
        (script_path, "probe script"),
    ):
        if not path.is_file():
            raise UvProbeError(f"{label} is absent: {path}")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    measurements = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        measured = measure_object_uv_density(obj, depsgraph)
        if measured is not None:
            measurements.append(measured)
    report = _build_report(
        identities={
            "source_blend_sha256": _sha256_file(blend_path),
            "build_report_sha256": _sha256_file(build_report_path),
            "probe_script_sha256": _sha256_file(script_path),
            "blender_executable_sha256": _sha256_file(executable_path),
        },
        measurements=measurements,
    )
    _write_report(output_path, report)
    print(
        "NANTAI_UV_REPEAT_PROBE_OK "
        f"objects={report['object_count']} "
        f"report_sha256={report['content_sha256']}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"NANTAI_UV_REPEAT_PROBE_ERROR {exc}", file=sys.stderr)
        raise SystemExit(19) from exc
