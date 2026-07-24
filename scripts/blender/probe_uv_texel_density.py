"""Probe UV texel density for terrain, creek, walls (HANDOFF-GLM-007 §5.6 P2b).

Opens a .blend file, measures UV area vs mesh face area for each object,
and reports per-object texel density ratios.  Pure measurement: does not
modify the scene.

Usage (inside Blender --background):
    blender --background <blend> --python probe_uv_texel_density.py
"""
import json
import sys

import bpy


def _triangle_area(a, b, c):
    return 0.5 * ((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))


def _mesh_triangle_area_world(a, b, c):
    return 0.5 * ((b - a).cross(c - a)).length


def measure_object_uv_density(obj):
    """Return (uv_area_total, mesh_area_total, ratio, face_count, tile_scale)."""
    mesh = obj.data
    if mesh is None or len(mesh.polygons) == 0:
        return None

    uv_layer = mesh.uv_layers.get(obj.get("nv_uv_layer", "nv_uv0"))
    if uv_layer is None:
        uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None

    tile_scale = obj.get("nv_uv_tile_scale", 1.0)

    uv_area_total = 0.0
    mesh_area_total = 0.0
    face_count = 0

    world_matrix = obj.matrix_world

    for polygon in mesh.polygons:
        if len(polygon.vertices) != 3:
            continue

        verts = [mesh.vertices[polygon.vertices[i]].co for i in range(3)]
        world_verts = [world_matrix @ v for v in verts]

        loops = polygon.loop_indices
        uvs = [uv_layer.data[loops[i]].uv for i in range(3)]

        uv_area = abs(_triangle_area(uvs[0], uvs[1], uvs[2]))
        mesh_area = _mesh_triangle_area_world(world_verts[0], world_verts[1], world_verts[2])

        if uv_area > 1e-12 and mesh_area > 1e-12:
            uv_area_total += uv_area
            mesh_area_total += mesh_area
            face_count += 1

    if face_count == 0 or mesh_area_total <= 0:
        return None

    ratio = uv_area_total / mesh_area_total
    return {
        "uv_area_total": uv_area_total,
        "mesh_area_total": mesh_area_total,
        "ratio": ratio,
        "face_count": face_count,
        "tile_scale": float(tile_scale),
        "texels_per_meter_sq": ratio,
    }


def main():
    blend_path = sys.argv[-1] if "--" not in sys.argv else sys.argv[sys.argv.index("--") + 1]
    if blend_path.endswith("--"):
        blend_path = sys.argv[-1]

    # Use the currently loaded blend (opened via --background <blend>)
    results = {}

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        name = obj.name
        # Categorize
        if name.startswith("nv__aux-terrain") or "terrain" in name.lower():
            category = "terrain"
        elif "creek" in name.lower() or "water" in name.lower():
            category = "creek"
        elif "wall" in name.lower() or "building" in name.lower() or "bridge" in name.lower():
            category = "wall-building"
        else:
            category = "other"

        m = measure_object_uv_density(obj)
        if m is None:
            continue

        results[name] = {
            "category": category,
            **m,
        }

    # Aggregate by category
    by_category = {}
    for name, m in results.items():
        cat = m["category"]
        if cat not in by_category:
            by_category[cat] = {
                "objects": [],
                "ratios": [],
                "tile_scales": [],
            }
        by_category[cat]["objects"].append(name)
        by_category[cat]["ratios"].append(m["ratio"])
        by_category[cat]["tile_scales"].append(m["tile_scale"])

    summary = {}
    for cat, data in by_category.items():
        ratios = sorted(data["ratios"])
        n = len(ratios)
        summary[cat] = {
            "object_count": n,
            "ratio_min": ratios[0],
            "ratio_max": ratios[-1],
            "ratio_median": ratios[n // 2],
            "ratio_min_max": ratios[-1] / ratios[0] if ratios[0] > 0 else None,
            "tile_scales": sorted(set(data["tile_scales"])),
        }

    output = {
        "blend": bpy.data.filepath,
        "per_object": dict(sorted(results.items())),
        "by_category": dict(sorted(summary.items())),
    }

    print("UV_TEXEL_DENSITY_JSON_START")
    print(json.dumps(output, indent=2, sort_keys=True))
    print("UV_TEXEL_DENSITY_JSON_END")


if __name__ == "__main__":
    main()
