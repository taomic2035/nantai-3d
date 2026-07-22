"""Probe a content-addressed reciprocal-route ``.blend`` for real mesh /
collision measurements (HANDOFF-CODEX-011 P0-1, HANDOFF-OPUS-009 Phase 4
item 2).

This script runs only inside the pinned Blender 4.5.11 Windows runtime.
The host supplies an absolute canonical probe-request path and an empty
private staging directory after ``--``.  The probe:

1. Validates its own script SHA-256 against the request.
2. Opens the bound ``.blend`` and validates its SHA-256.
3. Reads the build request (sibling of the ``.blend``) to recover the
   full ``reciprocal_route_module_plan`` and re-validates every input
   SHA (plan, build_id, build_report, object_registry).
4. Builds BVH trees for the 43 module meshes and the 175 v1 environment
   meshes.
5. Measures real geometric properties from the Blender mesh via
   ``bpy`` / ``bmesh`` / ``mathutils.bvhtree.BVHTree`` /
   ``Object.closest_point_on_mesh``:
   * Per-module route clear width, slope, clearance, route length;
   * Per-pair module-module BVH overlap counts;
   * Per-module module-environment intersection object IDs;
   * Per-module attachment distance to the declared canonical
     ``topology_ref`` object.
6. Emits a content-addressed ``ReciprocalRouteProbeReport`` to the
   staging directory.  The report's SHA is the canonical-JSON SHA-256.

The probe is fail-closed: every measurement is real.  No measurement is
inferred from the plan, the build report, or the file name.  The probe
does NOT promote ``modeled-unverified`` trust; all trust fields remain
Literal-locked to ``preview-only`` / ``L0`` / ``none``.

If a measurement cannot be taken (e.g., a ray missed every obstacle), it
is recorded as ``passed=False`` with a ``failure_reason``; the report is
still emitted, but its ``summary.overall_passed`` is ``False``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

REQUEST_SCHEMA = "nantai.synthetic-village.reciprocal-route-probe-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.reciprocal-route-probe.v1"
PROBE_ID = "synthetic-village-reciprocal-route-probe-v1"
REPORT_NAME = "reciprocal-route-probe-report.json"

# Geometric thresholds (must match pipeline.synthetic_village.reciprocal_route_probe).
MIN_ROUTE_CLEAR_WIDTH_M = 1.2
MAX_ROUTE_SLOPE_PCT = 12.0
MIN_ROUTE_CLEARANCE_M = 2.4
MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M = 2.0
ROUTE_SAMPLES_PER_MODULE = 5

# Maximum ray-cast distance for perpendicular / upward samples (m).
# Rays that exceed this distance without a hit are recorded as ``None``
# (open), not as ``inf``.
RAY_MAX_DISTANCE_M = 100.0

EXPECTED_MODULE_COUNT = 6
EXPECTED_MODULE_MODULE_PAIR_COUNT = 15
EXPECTED_MODULE_ROOTS = 43
EXPECTED_BASE_ROOTS = 175
EXPECTED_TOTAL_ROOTS = 218

MODULE_IDS = (
    "central-courtyard-downhill",
    "bridge-deck-crossing",
    "watermill-tailrace",
    "covered-gallery-underpass",
    "forest-orchard-boundary",
    "lower-valley-uphill",
)

#: Module attachment topology (REVIEW-CODEX-021).
#:
#: This is the **module attachment topology** — the path object the
#: module mesh attaches to in Blender.  It is used by the Phase 4.3
#: attachment probe to measure the distance from each module's first
#: part mesh to the declared ``topology_ref`` scene object.
#:
#: This is DISTINCT from the **camera placement topology** (which path
#: network has a ground ``WalkableNode`` within 30 m of the candidate's
#: position).  The two may differ for modules that cross path-network
#: boundaries.  The probe does NOT assert that the camera's
#: ``topology_ref`` equals the module's attachment ref.
#:
#: ``MODULE_TOPOLOGY_REFS`` stays at the original values because it
#: measures module mesh attachment, not camera placement.
MODULE_TOPOLOGY_REFS = {
    "central-courtyard-downhill": "path-network-003",
    "bridge-deck-crossing": "path-network-001",
    "watermill-tailrace": "path-network-001",
    "covered-gallery-underpass": "path-network-005",
    "forest-orchard-boundary": "path-network-002",
    "lower-valley-uphill": "path-network-001",
}

#: Camera placement topology (REVIEW-CODEX-021).
#:
#: This is the **camera placement topology** — the path network whose
#: ground ``WalkableNode`` is within 30 m of the candidate position.
#: Mirrors ``_DEFAULT_ROLE_CAMERA_PLACEMENT`` in
#: ``reciprocal_route_module.py``.  When ``role_camera_candidates`` is
#: present in the plan, the probe validates each candidate's
#: ``topology_ref`` matches this mapping AND that
#: ``bound_walkable_node.ground_route_ref`` matches the candidate's
#: ``topology_ref``.
CAMERA_PLACEMENT_TOPOLOGY = {
    "central-courtyard-downhill": "path-network-003",
    "bridge-deck-crossing": "path-network-001",
    "watermill-tailrace": "path-network-001",
    "covered-gallery-underpass": "path-network-003",
    "forest-orchard-boundary": "path-network-003",
    "lower-valley-uphill": "path-network-002",
}

# Trust disclosure (must match the schema's Literal-locked disclosure).
DISCLOSURE = (
    "modeled-unverified mesh probe; measurements are real but trust "
    "remains preview-only"
)

# Batch 8/9 manifest SHAs (must match reciprocal_route_module.py).
BATCH8_RELEASE_MANIFEST_SHA256 = (
    "be933fa37b56eee53e8acc78b7e2ff577c0bc4d6407fea91bfeb1da8d0637dbc"
)
BATCH8_ARCHIVE_SHA256 = (
    "6bdafc92b9eb2df3a943c4e5df3466e9609c22db89844dc940db3dab6ca921eb"
)
BATCH9_RELEASE_MANIFEST_SHA256 = (
    "bf5e2a5c6907baf5acefa5c6cf7d85bf9cfe611b47013f5bb1b564eca3064339"
)
BATCH9_ARCHIVE_SHA256 = (
    "6f7cc48e40e3d323a98e5ca91633cb6a6a7f623d7544efe44317102b3e5648f8"
)


class ProbeBuildError(RuntimeError):
    """The probe request, build request, or loaded scene is invalid."""


# --------------------------------------------------------------------------- #
# JSON / SHA helpers.
# --------------------------------------------------------------------------- #


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeBuildError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_bytes(payload) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


# --------------------------------------------------------------------------- #
# Argv / request loading.
# --------------------------------------------------------------------------- #


def _runtime_paths(argv):
    if "--" not in argv:
        raise ProbeBuildError("missing -- separator in argv")
    sep = argv.index("--")
    args = argv[sep + 1:]
    if len(args) != 2:
        raise ProbeBuildError("expected exactly two arguments after --")
    request_path = Path(args[0]).resolve()
    staging_path = Path(args[1]).resolve()
    if not request_path.is_file():
        raise ProbeBuildError(f"request path is not a file: {request_path}")
    if not staging_path.is_dir():
        raise ProbeBuildError(
            f"staging path is not a directory: {staging_path}",
        )
    return request_path, staging_path


def _load_request(path: Path) -> dict:
    raw = path.read_bytes()
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise ProbeBuildError("request bytes are absent or unbounded")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeBuildError(f"request is not valid JSON: {exc}") from exc


def _expect_exact_keys(payload, keys, label):
    if set(payload.keys()) != set(keys):
        raise ProbeBuildError(
            f"{label} keys are not exact: "
            f"extra={set(payload.keys()) - set(keys)} "
            f"missing={set(keys) - set(payload.keys())}",
        )


def _validate_request(request: dict) -> dict:
    top_keys = {
        "schema_version",
        "probe_script_sha256",
        "input_blend_path",
        "input_blend_sha256",
        "input_plan_sha256",
        "input_build_id",
        "input_build_report_sha256",
        "input_object_registry_sha256",
        "build_request_path",
    }
    _expect_exact_keys(request, top_keys, "probe request")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise ProbeBuildError("probe request schema_version is invalid")
    digest_fields = (
        "probe_script_sha256",
        "input_blend_sha256",
        "input_plan_sha256",
        "input_build_id",
        "input_build_report_sha256",
        "input_object_registry_sha256",
    )
    if not all(_is_sha256(request[key]) for key in digest_fields):
        raise ProbeBuildError("probe request contains an invalid SHA-256")
    if not request["input_blend_path"]:
        raise ProbeBuildError("probe request input_blend_path is empty")
    if not request["build_request_path"]:
        raise ProbeBuildError(
            "probe request build_request_path is empty; production callers "
            "must supply the reciprocal-route-build-request.json path so "
            "the probe can read the plan and re-validate every input SHA",
        )
    # Fail-closed: the probe script's own bytes must hash to the
    # declared probe_script_sha256.  This prevents a report from claiming
    # to come from a script whose bytes have been tampered with.
    if request["probe_script_sha256"] != _sha256_file(Path(__file__)):
        raise ProbeBuildError(
            "probe script bytes disagree with request probe_script_sha256",
        )
    return request


# --------------------------------------------------------------------------- #
# Build request (plan source) loading + SHA re-validation.
# --------------------------------------------------------------------------- #


def _load_build_request(build_request_path: Path) -> dict:
    raw = build_request_path.read_bytes()
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise ProbeBuildError("build request bytes are absent or unbounded")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeBuildError(f"build request is not valid JSON: {exc}") from exc


def _validate_build_request(
    build_request: dict,
    probe_request: dict,
) -> dict:
    """Re-validate every input SHA against the build request.

    The probe must not trust the plan_sha256 the caller supplied in the
    probe request alone; it re-derives the plan SHA from the build
    request's ``reciprocal_route_module_plan`` and confirms it matches.
    The reciprocal build report SHA is validated separately in
    ``_load_and_validate_build_report`` because it is the SHA of the
    build report *file* (an output), not a field in the build request.
    """
    top_keys = {
        "schema_version",
        "build_id",
        "synthetic",
        "verification_level",
        "geometry_usability",
        "stage",
        "trust_effect",
        "base_build_id",
        "base_build_report_sha256",
        "base_blend_sha256",
        "base_blender_executable_sha256",
        "base_object_registry_sha256",
        "base_environment_module_plan_sha256",
        "runtime_script_sha256",
        "reciprocal_route_module_plan_sha256",
        "reciprocal_route_module_plan",
        "material_bindings",
        "object_registry",
        "requested_artifact",
    }
    _expect_exact_keys(build_request, top_keys, "build request")
    if build_request["schema_version"] != (
        "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
    ):
        raise ProbeBuildError("build request schema_version is invalid")
    plan = build_request["reciprocal_route_module_plan"]
    if not isinstance(plan, dict):
        raise ProbeBuildError("build request plan is not a dict")
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    if plan_sha != build_request["reciprocal_route_module_plan_sha256"]:
        raise ProbeBuildError("build request plan SHA disagrees with plan bytes")
    if plan_sha != probe_request["input_plan_sha256"]:
        raise ProbeBuildError(
            "probe request input_plan_sha256 disagrees with build request plan",
        )
    if build_request["build_id"] != probe_request["input_build_id"]:
        raise ProbeBuildError(
            "probe request input_build_id disagrees with build request",
        )
    base_registry_sha = _sha256_bytes(
        _canonical_bytes(build_request["object_registry"][:EXPECTED_BASE_ROOTS]),
    )
    if base_registry_sha != probe_request["input_object_registry_sha256"]:
        raise ProbeBuildError(
            "probe request input_object_registry_sha256 disagrees with build request",
        )
    return build_request


def _load_and_validate_build_report(probe_request: dict) -> dict:
    """Load the reciprocal-route-build-report.json (sibling of the .blend)
    and validate its file SHA + internal fields against the probe request.

    This closes the chain: the .blend SHA is validated by ``_load_blend``,
    the build request's plan/build_id/registry SHAs are validated by
    ``_validate_build_request``, and the build report's file SHA +
    internal fields are validated here.  Any mismatch is fail-closed.
    """

    blend_path = Path(probe_request["input_blend_path"]).resolve()
    build_report_path = blend_path.parent / "reciprocal-route-build-report.json"
    if not build_report_path.is_file():
        raise ProbeBuildError(
            f"reciprocal-route-build-report.json not found at {build_report_path}",
        )
    file_sha = _sha256_file(build_report_path)
    if file_sha != probe_request["input_build_report_sha256"]:
        raise ProbeBuildError(
            "probe request input_build_report_sha256 disagrees with "
            "reciprocal-route-build-report.json file SHA",
        )
    raw = build_report_path.read_bytes()
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise ProbeBuildError("build report bytes are absent or unbounded")
    try:
        build_report = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeBuildError(f"build report is not valid JSON: {exc}") from exc
    # Validate the build report's internal identity fields against the
    # probe request.  This proves the build report was produced from the
    # same build request the probe just validated.
    if build_report.get("build_id") != probe_request["input_build_id"]:
        raise ProbeBuildError(
            "build report build_id disagrees with probe request input_build_id",
        )
    artifact = build_report.get("artifact") or {}
    if artifact.get("sha256") != probe_request["input_blend_sha256"]:
        raise ProbeBuildError(
            "build report artifact.sha256 disagrees with probe request "
            "input_blend_sha256",
        )
    if (
        build_report.get("reciprocal_route_module_plan_sha256")
        != probe_request["input_plan_sha256"]
    ):
        raise ProbeBuildError(
            "build report reciprocal_route_module_plan_sha256 disagrees with "
            "probe request input_plan_sha256",
        )
    return build_report


# --------------------------------------------------------------------------- #
# Scene loading + .blend SHA validation.
# --------------------------------------------------------------------------- #


def _load_blend(probe_request: dict) -> None:
    blend_path = Path(probe_request["input_blend_path"]).resolve()
    if (
        not blend_path.is_absolute()
        or not blend_path.is_file()
        or blend_path.is_symlink()
        or _sha256_file(blend_path) != probe_request["input_blend_sha256"]
    ):
        raise ProbeBuildError(
            "input_blend_path is not the bound blend artifact",
        )
    bpy.ops.wm.open_mainfile(filepath=str(blend_path))


def _module_id_for_part(part_id: str, plan: dict) -> str | None:
    for module in plan["modules"]:
        for part in module["parts"]:
            if part["part_id"] == part_id:
                return module["module_id"]
    return None


def _topology_ref_for_module(module_id: str, plan: dict) -> str:
    """Return the module attachment ``topology_ref`` for a module.

    REVIEW-CODEX-021: this returns the **module attachment topology**
    (from ``MODULE_TOPOLOGY_REFS``), NOT the camera placement topology.
    The probe uses this to measure the distance from the module's mesh
    to the declared path object in Blender.

    When ``role_camera_candidates`` is present, the probe validates the
    candidate's ``topology_ref`` matches ``CAMERA_PLACEMENT_TOPOLOGY``
    (not ``MODULE_TOPOLOGY_REFS``), and that
    ``bound_walkable_node.ground_route_ref`` matches the candidate's
    ``topology_ref``.  The probe does NOT assert that the camera's
    ref equals the module's attachment ref.
    """

    attachment_ref = MODULE_TOPOLOGY_REFS[module_id]
    candidates = plan.get("role_camera_candidates")
    if candidates is None:
        return attachment_ref
    if not isinstance(candidates, list) or len(candidates) != EXPECTED_MODULE_COUNT:
        raise ProbeBuildError(
            "plan role_camera_candidates is present but not six entries",
        )
    matches = [
        c for c in candidates
        if isinstance(c, dict) and c.get("role_module_id") == module_id
    ]
    if len(matches) != 1:
        raise ProbeBuildError(
            f"plan role_camera_candidates for {module_id} "
            f"is not unique (matches={len(matches)})",
        )
    camera_ref = matches[0].get("topology_ref")
    if not isinstance(camera_ref, str) or not camera_ref:
        raise ProbeBuildError(
            f"plan role_camera_candidates for {module_id} has no topology_ref",
        )
    # Validate camera placement topology (REVIEW-CODEX-021)
    expected_camera_ref = CAMERA_PLACEMENT_TOPOLOGY[module_id]
    if camera_ref != expected_camera_ref:
        raise ProbeBuildError(
            f"plan role_camera_candidates topology_ref={camera_ref} for "
            f"{module_id} disagrees with canonical camera placement "
            f"{expected_camera_ref}",
        )
    # Validate bound_walkable_node consistency (REVIEW-CODEX-021)
    bound_node = matches[0].get("bound_walkable_node")
    if bound_node is not None:
        node_route = bound_node.get("ground_route_ref")
        if not isinstance(node_route, str) or node_route != camera_ref:
            raise ProbeBuildError(
                f"plan role_camera_candidates for {module_id} has "
                f"bound_walkable_node.ground_route_ref={node_route!r} "
                f"that does not match topology_ref={camera_ref!r}",
            )
    return attachment_ref


# --------------------------------------------------------------------------- #
# Scene indexing: module meshes, env meshes, topology objects.
# --------------------------------------------------------------------------- #


def _index_scene(plan: dict):
    """Categorise Blender objects into module meshes, env meshes, and
    a stable_id → object lookup for topology attachment probes."""

    module_parts_by_id = {}  # module_id → list[part_dict] (sorted by instance_id)
    for module in plan["modules"]:
        parts = sorted(module["parts"], key=lambda p: p["instance_id"])
        module_parts_by_id[module["module_id"]] = parts

    module_meshes = {}  # part_id → bpy.types.Object (the mesh__{part_id} object)
    env_meshes = []  # list[bpy.types.Object] (base scene mesh objects, nv_instance_id <= 175)
    stable_id_to_obj = {}  # nv_stable_id → bpy.types.Object (for topology_ref lookup)

    for obj in bpy.data.objects:
        stable_id = obj.get("nv_stable_id") or ""
        if obj.name.startswith("mesh__") and stable_id:
            module_id = _module_id_for_part(stable_id, plan)
            if module_id is None:
                raise ProbeBuildError(
                    f"module mesh {obj.name} has no matching plan part",
                )
            if stable_id in module_meshes:
                raise ProbeBuildError(
                    f"duplicate module mesh for part_id={stable_id}",
                )
            module_meshes[stable_id] = obj
        elif (
            obj.type == "MESH"
            and stable_id
            # Phase 4.3: topology proxies (nv_proxy_topology=True) are
            # auxiliary attachment targets emitted by
            # ``apply_reciprocal_route_modules.py``.  They must NOT enter
            # env_meshes -- otherwise module-environment intersection
            # probes would falsely report each module intersecting its
            # own proxy.  Proxies still enter stable_id_to_obj below so
            # ``_topology_attachment_probes`` can find them.
            and not obj.get("nv_proxy_topology")
        ):
            env_meshes.append(obj)
        if stable_id:
            if stable_id in stable_id_to_obj:
                # Non-unique stable_id is allowed for child mesh objects
                # (mesh__{part_id} shares nv_stable_id with its parent
                # root nv__{part_id}).  Only the root empty is the
                # canonical topology target, so keep the first seen.
                pass
            else:
                stable_id_to_obj[stable_id] = obj

    if len(module_meshes) != EXPECTED_MODULE_ROOTS:
        raise ProbeBuildError(
            f"module mesh count is {len(module_meshes)}, "
            f"expected {EXPECTED_MODULE_ROOTS}",
        )
    # Env meshes include the 175 base roots' meshes (if they are mesh
    # objects themselves) plus any standalone mesh objects in the base
    # scene.  We do not require an exact count here; the probe iterates
    # whatever env meshes exist and reports the intersections it finds.
    if not env_meshes:
        raise ProbeBuildError("scene has no environment mesh objects to probe")

    return module_parts_by_id, module_meshes, env_meshes, stable_id_to_obj


# --------------------------------------------------------------------------- #
# BVH construction.
# --------------------------------------------------------------------------- #


def _bvh_for_object(obj, depsgraph) -> BVHTree | None:
    """Build a BVH tree from the object's evaluated mesh in world space."""
    try:
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        if mesh is None or len(mesh.vertices) == 0 or len(mesh.polygons) == 0:
            eval_obj.to_mesh_clear()
            return None
        bvh = BVHTree.FromObject(obj, depsgraph)
        eval_obj.to_mesh_clear()
        return bvh
    except (RuntimeError, ValueError):
        return None


def _build_module_bvhs(module_meshes, depsgraph):
    """Build a BVH per module mesh object.  Returns part_id → BVHTree."""
    bvhs = {}
    for part_id, obj in module_meshes.items():
        bvh = _bvh_for_object(obj, depsgraph)
        if bvh is None:
            raise ProbeBuildError(
                f"module mesh {part_id} has no evaluable geometry",
            )
        bvhs[part_id] = bvh
    return bvhs


def _build_env_bvhs(env_meshes, depsgraph):
    """Build a BVH per env mesh object.  Returns list[(object_id, BVHTree)]."""
    out = []
    for obj in env_meshes:
        bvh = _bvh_for_object(obj, depsgraph)
        if bvh is None:
            continue
        stable_id = obj.get("nv_stable_id") or obj.name
        out.append((stable_id, bvh))
    return out


# --------------------------------------------------------------------------- #
# Route probe (per module).
# --------------------------------------------------------------------------- #


def _part_center_world(part: dict) -> Vector:
    """World-space center of a part (read from the plan's part_layout)."""
    center = part["part_layout"]["center_m"]
    return Vector((float(center[0]), float(center[1]), float(center[2])))


def _polyline_points(parts: list[dict]) -> list[Vector]:
    return [_part_center_world(part) for part in parts]


def _sample_polyline(points: list[Vector], sample_count: int):
    """Sample ``sample_count`` points evenly by arc length along the polyline.

    Returns a list of ``(position, forward_unit_vector)`` tuples.
    ``forward`` is the unit tangent of the segment the sample falls on.
    For a degenerate polyline (single point), ``forward`` defaults to
    +X so perpendicular rays can still be cast.
    """
    if len(points) == 1:
        return [(points[0], Vector((1.0, 0.0, 0.0)))] * sample_count

    # Compute cumulative arc lengths.  ``points`` and ``points[1:]`` are
    # intentionally different lengths (one shorter), so ``strict=False``
    # is correct; ``strict=True`` would always raise.
    seg_lengths = []
    cum = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        seg = (b - a).length
        seg_lengths.append(seg)
        cum.append(cum[-1] + seg)
    total = cum[-1]
    if total <= 0.0:
        return [(points[0], Vector((1.0, 0.0, 0.0)))] * sample_count

    samples = []
    for i in range(sample_count):
        if sample_count == 1:
            t = 0.0
        else:
            t = (total * i) / (sample_count - 1)
        # Find the segment containing arc length t.
        seg_index = 0
        for k in range(len(seg_lengths)):
            if cum[k + 1] >= t:
                seg_index = k
                break
        else:
            seg_index = len(seg_lengths) - 1
        seg_start = cum[seg_index]
        seg_len = seg_lengths[seg_index]
        if seg_len <= 0.0:
            pos = points[seg_index]
            forward = Vector((1.0, 0.0, 0.0))
        else:
            frac = (t - seg_start) / seg_len
            a = points[seg_index]
            b = points[seg_index + 1]
            pos = a + (b - a) * frac
            forward = (b - a).normalized()
        samples.append((pos, forward))
    return samples


def _measure_route(
    parts: list[dict],
    module_bvhs: dict,
    env_bvhs: list,
) -> dict:
    """Measure route geometry for one module.  Returns a dict of
    measurement values suitable for ``ModuleRouteProbe``."""

    points = _polyline_points(parts)
    samples_data = _sample_polyline(points, ROUTE_SAMPLES_PER_MODULE)

    # Flatten all BVHs (module + env) for perpendicular ray-casting.
    probe_bvhs = list(module_bvhs.values()) + [b for _id, b in env_bvhs]

    sample_measurements = []
    clear_widths = []
    clearances = []
    any_ray_missed = False

    for position, forward in samples_data:
        # Perpendicular directions in the XY plane.
        left_dir = Vector((-forward.y, forward.x, 0.0)).normalized()
        right_dir = Vector((forward.y, -forward.x, 0.0)).normalized()
        up_dir = Vector((0.0, 0.0, 1.0))

        left_clear = _min_ray_hit(probe_bvhs, position, left_dir, RAY_MAX_DISTANCE_M)
        right_clear = _min_ray_hit(probe_bvhs, position, right_dir, RAY_MAX_DISTANCE_M)
        upward_clear = _min_ray_hit(probe_bvhs, position, up_dir, RAY_MAX_DISTANCE_M)

        if left_clear is None or right_clear is None:
            any_ray_missed = True

        clear_width = (
            (left_clear + right_clear)
            if (left_clear is not None and right_clear is not None)
            else None
        )
        if clear_width is not None:
            clear_widths.append(clear_width)
        if upward_clear is not None:
            clearances.append(upward_clear)

        sample_measurements.append({
            "arc_length_m": 0.0,  # filled below
            "left_clear_m": left_clear,
            "right_clear_m": right_clear,
            "upward_clear_m": upward_clear,
            "sample_position_m": (
                float(position.x),
                float(position.y),
                float(position.z),
            ),
            "route_forward": (
                float(forward.x),
                float(forward.y),
                float(forward.z),
            ),
        })

    # Fill arc_length_m per sample (cumulative).  Same ``strict=False``
    # rationale as ``_sample_polyline``: ``points[1:]`` is one shorter.
    if len(points) > 1:
        cum = [0.0]
        for a, b in zip(points, points[1:], strict=False):
            cum.append(cum[-1] + (b - a).length)
        total = cum[-1]
    else:
        total = 0.0
    for i, sm in enumerate(sample_measurements):
        if total > 0.0:
            sm["arc_length_m"] = (total * i) / (len(sample_measurements) - 1)
        else:
            sm["arc_length_m"] = 0.0

    # Aggregate measurements.
    clear_width_min = min(clear_widths) if clear_widths else None
    clearance_min = min(clearances) if clearances else None

    # Slope: (z_last - z_first) / horizontal_distance * 100.
    if len(points) >= 2:
        first = points[0]
        last = points[-1]
        dz = last.z - first.z
        dx = last.x - first.x
        dy = last.y - first.y
        horiz = math.sqrt(dx * dx + dy * dy)
        slope_pct = (dz / horiz * 100.0) if horiz > 0.0 else 0.0
        route_length_m = horiz
    else:
        slope_pct = 0.0
        route_length_m = 0.0

    # Pass logic.
    failures = []
    if any_ray_missed or clear_width_min is None:
        failures.append("perpendicular ray missed (clear_width unavailable)")
    elif clear_width_min < MIN_ROUTE_CLEAR_WIDTH_M:
        failures.append(
            f"clear_width_min_m={clear_width_min:.3f} < "
            f"{MIN_ROUTE_CLEAR_WIDTH_M:.3f}",
        )
    if abs(slope_pct) > MAX_ROUTE_SLOPE_PCT:
        failures.append(
            f"|slope_pct|={abs(slope_pct):.3f} > {MAX_ROUTE_SLOPE_PCT:.3f}",
        )
    if clearance_min is not None and clearance_min < MIN_ROUTE_CLEARANCE_M:
        failures.append(
            f"clearance_min_m={clearance_min:.3f} < "
            f"{MIN_ROUTE_CLEARANCE_M:.3f}",
        )

    passed = not failures
    failure_reason = "; ".join(failures) if failures else None
    return {
        "samples": sample_measurements,
        "clear_width_min_m": clear_width_min,
        "slope_pct": slope_pct,
        "clearance_min_m": clearance_min,
        "route_length_m": route_length_m,
        "passed": passed,
        "failure_reason": failure_reason,
    }


def _min_ray_hit(bvhs: list, origin: Vector, direction: Vector, max_distance: float):
    """Ray-cast against every BVH and return the minimum finite hit distance,
    or ``None`` if no BVH recorded a hit."""
    best = None
    direction = direction.normalized()
    for bvh in bvhs:
        hit = bvh.ray_cast(origin, direction, max_distance)
        if hit is None:
            continue
        location, _normal, _index, distance = hit
        if location is None or distance is None:
            continue
        if not math.isfinite(distance) or distance < 0.0:
            continue
        if best is None or distance < best:
            best = float(distance)
    return best


# --------------------------------------------------------------------------- #
# Module-module intersection probe.
# --------------------------------------------------------------------------- #


def _module_module_intersections(module_bvhs: dict, module_parts_by_id: dict) -> list:
    """For each of the 15 unordered module pairs, count overlapping
    triangle pairs via BVH overlap.  Returns a list of probe dicts."""
    results = []
    for i in range(EXPECTED_MODULE_COUNT):
        for j in range(i + 1, EXPECTED_MODULE_COUNT):
            a_id = MODULE_IDS[i]
            b_id = MODULE_IDS[j]
            a_parts = module_parts_by_id[a_id]
            b_parts = module_parts_by_id[b_id]
            intersection_count = 0
            for a_part in a_parts:
                a_bvh = module_bvhs.get(a_part["part_id"])
                if a_bvh is None:
                    continue
                for b_part in b_parts:
                    b_bvh = module_bvhs.get(b_part["part_id"])
                    if b_bvh is None:
                        continue
                    try:
                        overlap = a_bvh.overlap(b_bvh)
                    except (RuntimeError, ValueError):
                        overlap = []
                    if overlap:
                        intersection_count += len(overlap)
            passed = intersection_count == 0
            results.append({
                "pair_key": f"{a_id}--{b_id}",
                "module_a": a_id,
                "module_b": b_id,
                "intersection_count": intersection_count,
                "passed": passed,
                "failure_reason": (
                    None if passed
                    else f"intersection_count={intersection_count} > 0"
                ),
            })
    return results


# --------------------------------------------------------------------------- #
# Module-environment intersection probe.
# --------------------------------------------------------------------------- #


def _module_environment_intersections(
    module_bvhs: dict,
    module_parts_by_id: dict,
    env_bvhs: list,
) -> list:
    """For each of the 6 modules, find which env objects intersect with
    any of the module's part meshes.  Returns a list of probe dicts."""
    results = []
    for module_id in MODULE_IDS:
        parts = module_parts_by_id[module_id]
        intersecting_ids = []
        for env_id, env_bvh in env_bvhs:
            hit = False
            for part in parts:
                part_bvh = module_bvhs.get(part["part_id"])
                if part_bvh is None:
                    continue
                try:
                    overlap = part_bvh.overlap(env_bvh)
                except (RuntimeError, ValueError):
                    overlap = []
                if overlap:
                    hit = True
                    break
            if hit:
                intersecting_ids.append(env_id)
        intersection_count = len(intersecting_ids)
        passed = intersection_count == 0
        results.append({
            "role_module_id": module_id,
            "intersecting_object_ids": tuple(intersecting_ids),
            "intersection_count": intersection_count,
            "passed": passed,
            "failure_reason": (
                None if passed
                else f"intersection_count={intersection_count} > 0"
            ),
        })
    return results


# --------------------------------------------------------------------------- #
# Topology attachment probe.
# --------------------------------------------------------------------------- #


def _topology_attachment_probes(
    module_parts_by_id: dict,
    stable_id_to_obj: dict,
    plan: dict,
    depsgraph,
) -> list:
    """For each of the 6 modules, measure the distance from the module's
    first part center to the nearest surface of its declared
    ``topology_ref`` object.  Returns a list of probe dicts.

    When the measurement cannot be taken (topology object missing, no
    parts, or ``BVHTree.find_nearest`` returned no hit), the report
    carries ``attachment_distance_m=None`` -- honest absence rather than
    ``inf``.  The schema's validator rejects ``passed=True`` when the
    distance is ``None``.

    Phase 4.3 amendment (FEEDBACK-HANDOFF-OPUS-009-phase4-probe.md
    §"待处理" item: "topology attachment distance wrong"): Blender 4.5.11
    ``Object.closest_point_on_mesh`` and ``BVHTree.find_nearest`` both
    return a ``distance`` field that does NOT match the geometric
    distance from origin to the returned location (verified empirically:
    multiple origins all returned ``distance=3`` while the real
    Euclidean distance ranged from 0.0 to 24.25 m).  To stay fail-closed
    the probe now builds a BVH per topology object, calls
    ``find_nearest`` for the nearest surface location only, and computes
    the distance itself as ``(center - location).length``.  This is
    independent of any Blender version's tuple ordering.
    """
    results = []
    for module_id in MODULE_IDS:
        topology_ref = _topology_ref_for_module(module_id, plan)
        # Phase 4.3: prefer the module-specific proxy mesh emitted by
        # ``apply_reciprocal_route_modules.py::_build_topology_proxies``.
        # The v1 base scene's ``path-network-001/002/003/005`` are EMPTY
        # roots; ``BVHTree.FromObject`` on an EMPTY returns None.  The
        # reciprocal-route runtime emits one proxy mesh per module with
        # stable_id ``"{topology_ref}::{module_id}"`` so the probe can
        # measure a real attachment distance.  Fall back to the original
        # topology_ref if the proxy is absent (e.g. older build).
        proxy_id = f"{topology_ref}::{module_id}"
        topology_obj = stable_id_to_obj.get(proxy_id)
        if topology_obj is None:
            topology_obj = stable_id_to_obj.get(topology_ref)
        used_target = (
            proxy_id if topology_obj is not None and topology_obj.get(
                "nv_stable_id",
            ) == proxy_id
            else topology_ref
        )
        if topology_obj is None:
            results.append({
                "role_module_id": module_id,
                "topology_ref": topology_ref,
                "attachment_distance_m": None,
                "passed": False,
                "failure_reason": (
                    f"topology_ref object {topology_ref} not found in scene "
                    f"(also no proxy {proxy_id})"
                ),
            })
            continue
        parts = module_parts_by_id[module_id]
        if not parts:
            results.append({
                "role_module_id": module_id,
                "topology_ref": topology_ref,
                "attachment_distance_m": None,
                "passed": False,
                "failure_reason": (
                    f"module has no parts (target={used_target})"
                ),
            })
            continue
        first_part = parts[0]
        center = _part_center_world(first_part)
        # Build a world-space BVH for the topology object.  We avoid
        # ``Object.closest_point_on_mesh`` here because in Blender 4.5.11
        # its returned ``distance`` field does not match the geometric
        # distance (see function docstring).
        bvh = _bvh_for_object(topology_obj, depsgraph)
        if bvh is None:
            results.append({
                "role_module_id": module_id,
                "topology_ref": topology_ref,
                "attachment_distance_m": None,
                "passed": False,
                "failure_reason": (
                    f"BVH could not be built for {used_target} "
                    f"(no evaluable mesh)"
                ),
            })
            continue
        try:
            nearest = bvh.find_nearest(center, RAY_MAX_DISTANCE_M)
        except (RuntimeError, ValueError):
            nearest = None
        # find_nearest returns (location, normal, distance, index) on
        # hit, (None, None, None, None) on miss.  We only need the
        # location because we compute distance ourselves.
        if nearest is None or nearest[0] is None:
            results.append({
                "role_module_id": module_id,
                "topology_ref": topology_ref,
                "attachment_distance_m": None,
                "passed": False,
                "failure_reason": (
                    f"BVH.find_nearest returned no hit for "
                    f"{used_target}"
                ),
            })
            continue
        location = nearest[0]
        # Compute the real geometric distance -- Blender 4.5's
        # find_nearest distance field is not reliable (see docstring).
        distance_m = float((center - location).length)
        if not math.isfinite(distance_m):
            results.append({
                "role_module_id": module_id,
                "topology_ref": topology_ref,
                "attachment_distance_m": None,
                "passed": False,
                "failure_reason": (
                    f"computed distance is not finite for "
                    f"{used_target}"
                ),
            })
            continue
        passed = distance_m <= MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M
        results.append({
            "role_module_id": module_id,
            "topology_ref": topology_ref,
            "attachment_distance_m": distance_m,
            "passed": passed,
            "failure_reason": (
                None if passed
                else f"distance={distance_m:.3f} > "
                     f"{MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M:.3f} "
                     f"(target={used_target})"
            ),
        })
    return results


# --------------------------------------------------------------------------- #
# Summary + report assembly.
# --------------------------------------------------------------------------- #


def _build_summary(
    route_probes: list,
    pair_probes: list,
    env_probes: list,
    topology_probes: list,
) -> dict:
    route_pass = sum(1 for p in route_probes if p["passed"])
    route_fail = EXPECTED_MODULE_COUNT - route_pass
    pair_pass = sum(1 for p in pair_probes if p["passed"])
    pair_fail = EXPECTED_MODULE_MODULE_PAIR_COUNT - pair_pass
    env_pass = sum(1 for p in env_probes if p["passed"])
    env_fail = EXPECTED_MODULE_COUNT - env_pass
    topo_pass = sum(1 for p in topology_probes if p["passed"])
    topo_fail = EXPECTED_MODULE_COUNT - topo_pass
    overall_passed = (
        route_fail == 0 and pair_fail == 0 and env_fail == 0 and topo_fail == 0
    )
    return {
        "module_route_passed_count": route_pass,
        "module_route_failed_count": route_fail,
        "module_module_intersection_passed_count": pair_pass,
        "module_module_intersection_failed_count": pair_fail,
        "module_environment_intersection_passed_count": env_pass,
        "module_environment_intersection_failed_count": env_fail,
        "topology_attachment_passed_count": topo_pass,
        "topology_attachment_failed_count": topo_fail,
        "overall_passed": overall_passed,
    }


def _assemble_report(
    probe_request: dict,
    route_probes: list,
    pair_probes: list,
    env_probes: list,
    topology_probes: list,
    summary: dict,
) -> dict:
    return {
        "schema_version": REPORT_SCHEMA,
        "probe_id": PROBE_ID,
        "probe_script_sha256": probe_request["probe_script_sha256"],
        "input_blend_sha256": probe_request["input_blend_sha256"],
        "input_build_id": probe_request["input_build_id"],
        "input_plan_sha256": probe_request["input_plan_sha256"],
        "input_build_report_sha256": probe_request["input_build_report_sha256"],
        "input_object_registry_sha256": probe_request["input_object_registry_sha256"],
        "batch8_release_manifest_sha256": BATCH8_RELEASE_MANIFEST_SHA256,
        "batch8_archive_sha256": BATCH8_ARCHIVE_SHA256,
        "batch9_release_manifest_sha256": BATCH9_RELEASE_MANIFEST_SHA256,
        "batch9_archive_sha256": BATCH9_ARCHIVE_SHA256,
        "module_route_probes": route_probes,
        "module_module_intersections": pair_probes,
        "module_environment_intersections": env_probes,
        "topology_attachment_probes": topology_probes,
        "summary": summary,
        "synthetic": True,
        "geometry_usability": "preview-only",
        "verification_level": "L0",
        "metric_alignment": False,
        "real_photo_textures": False,
        "geometry_trust": "simplified-pbr-not-render-parity",
        "trust_effect": "none",
        "disclosure": DISCLOSURE,
    }


def _write_report(report: dict, staging_path: Path) -> Path:
    report_path = staging_path / REPORT_NAME
    if report_path.exists():
        raise ProbeBuildError("probe report already exists in staging dir")
    with report_path.open("xb") as stream:
        stream.write(_canonical_bytes(report))
        stream.flush()
        os.fsync(stream.fileno())
    return report_path


# --------------------------------------------------------------------------- #
# Main.
# --------------------------------------------------------------------------- #


def main():
    request_path, staging_path = _runtime_paths(sys.argv)
    probe_request = _validate_request(_load_request(request_path))
    _load_blend(probe_request)
    # Validate the reciprocal build report file SHA + internal fields
    # against the probe request.  This must happen before reading the
    # build request so the chain is: .blend → build report → build request.
    _load_and_validate_build_report(probe_request)

    build_request_path = Path(probe_request["build_request_path"]).resolve()
    if not build_request_path.is_file():
        raise ProbeBuildError(
            f"build_request_path is not a file: {build_request_path}",
        )
    build_request = _load_build_request(build_request_path)
    _validate_build_request(build_request, probe_request)
    plan = build_request["reciprocal_route_module_plan"]

    # Index the scene.
    module_parts_by_id, module_meshes, env_meshes, stable_id_to_obj = _index_scene(plan)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    module_bvhs = _build_module_bvhs(module_meshes, depsgraph)
    env_bvhs = _build_env_bvhs(env_meshes, depsgraph)

    # Run all four probe categories.
    route_probes = []
    for module_id in MODULE_IDS:
        parts = module_parts_by_id[module_id]
        measured = _measure_route(parts, module_bvhs, env_bvhs)
        route_probes.append({
            "role_module_id": module_id,
            "sample_count": len(measured["samples"]),
            "samples": measured["samples"],
            "clear_width_min_m": measured["clear_width_min_m"],
            "slope_pct": measured["slope_pct"],
            "clearance_min_m": measured["clearance_min_m"],
            "route_length_m": measured["route_length_m"],
            "passed": measured["passed"],
            "failure_reason": measured["failure_reason"],
        })

    pair_probes = _module_module_intersections(module_bvhs, module_parts_by_id)
    env_probes = _module_environment_intersections(
        module_bvhs, module_parts_by_id, env_bvhs,
    )
    topology_probes = _topology_attachment_probes(
        module_parts_by_id, stable_id_to_obj, plan, depsgraph,
    )

    summary = _build_summary(route_probes, pair_probes, env_probes, topology_probes)
    report = _assemble_report(
        probe_request,
        route_probes,
        pair_probes,
        env_probes,
        topology_probes,
        summary,
    )
    _write_report(report, staging_path)
    print(
        "NANTAI_RECIPROCAL_ROUTE_PROBE="
        + json.dumps(
            {
                "probe_id": PROBE_ID,
                "overall_passed": summary["overall_passed"],
                "route_passed": summary["module_route_passed_count"],
                "pair_passed": summary["module_module_intersection_passed_count"],
                "env_passed": summary["module_environment_intersection_passed_count"],
                "topo_passed": summary["topology_attachment_passed_count"],
                "trust_effect": "none",
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except ProbeBuildError as exc:
        print(f"NANTAI_RECIPROCAL_ROUTE_PROBE_ERROR {exc}", flush=True)
        sys.exit(1)
