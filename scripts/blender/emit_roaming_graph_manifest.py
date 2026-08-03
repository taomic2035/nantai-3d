"""Blender-side emitter for the roaming-graph collision manifest
(HANDOFF-GLM-009 step 3).

This script runs only inside the pinned Blender 4.5.11 Windows runtime.
The host supplies an absolute canonical manifest-request path and an
empty private staging directory after ``--``.

The emitter:

1. Validates its own script SHA-256 against the request.
2. Opens the bound ``.blend`` and validates its SHA-256.
3. Reads the build request (sibling of the ``.blend``) to recover the
   full ``reciprocal_route_module_plan`` and re-validates every input
   SHA (plan, build_id, build_report, object_registry).
4. For each declared room/portal ``bound_object_id``, finds the
   matching Blender object and computes a content-addressed SHA-256
   over its evaluated mesh in **world space** (vertices + loop
   polygons).  The SHA is the ``collision_proxy_sha256``.
5. Emits a content-addressed ``RoamingGraphManifest`` to the staging
   directory.  The manifest's SHA is the canonical-JSON SHA-256.

The emitter is fail-closed: every SHA is measured from real geometry.
No SHA is inferred from the plan, the build report, the object name, or
the engine string.  The emitter does NOT promote ``modeled-unverified``
trust; all trust fields remain Literal-locked downstream.

If a declared ``bound_object_id`` cannot be found in the scene, or its
mesh has zero vertices/polygons, the emitter stops and emits no manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import bpy

MANIFEST_SCHEMA = "nantai.synthetic-village.roaming-graph-manifest.v1"
MANIFEST_ID = "synthetic-village-roaming-graph-manifest-v1"
MANIFEST_NAME = "roaming-graph-manifest.json"

EXPECTED_MODULE_ROOTS = 43
EXPECTED_BASE_ROOTS = 175

DISCLOSURE = (
    "modeled-unverified mesh SHA manifest; SHAs are measured from real "
    "geometry but trust remains preview-only"
)


class ManifestBuildError(RuntimeError):
    """The manifest request, build request, or loaded scene is invalid."""


# --------------------------------------------------------------------------- #
# JSON / SHA helpers.
# --------------------------------------------------------------------------- #


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestBuildError(f"duplicate JSON key: {key}")
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


def _stable_read_and_sha256(
    path: Path, *, max_bytes: int, label: str,
) -> tuple[bytes, str]:
    """Read ``path`` through a single ``O_NOFOLLOW`` descriptor and compute
    its SHA-256 from the SAME bytes.

    This closes the false-binding TOCTOU where the file was opened once for
    parsing (``read_bytes``) and again for hashing (``_sha256_file``):
    between the two opens an attacker could swap the file so the hash matched
    the original while the parsed content came from the swapped file.

    Returns ``(raw_bytes, sha256_hex)``.  Raises ``ManifestBuildError`` on any
    identity mismatch, symlink, non-regular file, or over-size read.
    """
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise ManifestBuildError(f"{label} not found") from None
    except OSError as exc:
        raise ManifestBuildError(f"{label} cannot be inspected") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ManifestBuildError(f"{label} is not a bounded regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestBuildError(f"{label} cannot be opened") from exc
    digest = hashlib.sha256()
    payload = bytearray()
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IFMT(opened.st_mode) != stat.S_IFMT(before.st_mode)
            or opened.st_ino != before.st_ino
            or opened.st_dev != before.st_dev
        ):
            raise ManifestBuildError(f"{label} changed before read")
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    try:
        with stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise ManifestBuildError(f"{label} exceeds bounded read limit")
                digest.update(chunk)
            read_fstat = os.fstat(stream.fileno())
    except OSError as exc:
        raise ManifestBuildError(f"{label} cannot be read") from exc
    try:
        after = path.lstat()
    except OSError as exc:
        raise ManifestBuildError(f"{label} cannot be reinspected") from exc
    if (
        stat.S_IFMT(opened.st_mode) != stat.S_IFMT(read_fstat.st_mode)
        or opened.st_ino != read_fstat.st_ino
        or opened.st_dev != read_fstat.st_dev
        or stat.S_IFMT(before.st_mode) != stat.S_IFMT(after.st_mode)
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
        or len(payload) != before.st_size
    ):
        raise ManifestBuildError(f"{label} changed during read")
    return bytes(payload), digest.hexdigest()


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
        raise ManifestBuildError("missing -- separator in argv")
    sep = argv.index("--")
    args = argv[sep + 1:]
    if len(args) != 2:
        raise ManifestBuildError("expected exactly two arguments after --")
    request_path = Path(args[0]).resolve()
    staging_path = Path(args[1]).resolve()
    if not request_path.is_file():
        raise ManifestBuildError(f"request path is not a file: {request_path}")
    if not staging_path.is_dir():
        raise ManifestBuildError(
            f"staging path is not a directory: {staging_path}",
        )
    return request_path, staging_path


def _load_request(path: Path) -> dict:
    raw, _sha = _stable_read_and_sha256(
        path, max_bytes=16 * 1024 * 1024, label="manifest request",
    )
    if not raw:
        raise ManifestBuildError("request bytes are absent")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestBuildError(f"request is not valid JSON: {exc}") from exc


def _expect_exact_keys(payload, keys, label):
    if set(payload.keys()) != set(keys):
        raise ManifestBuildError(
            f"{label} keys are not exact: "
            f"extra={set(payload.keys()) - set(keys)} "
            f"missing={set(keys) - set(payload.keys())}",
        )


def _validate_request(request: dict) -> dict:
    top_keys = {
        "schema_version",
        "manifest_script_sha256",
        "input_blend_path",
        "input_blend_sha256",
        "input_plan_sha256",
        "input_build_id",
        "input_build_report_sha256",
        "input_object_registry_sha256",
        "build_request_path",
        "rooms",
        "portals",
    }
    _expect_exact_keys(request, top_keys, "manifest request")
    if request["schema_version"] != MANIFEST_SCHEMA:
        raise ManifestBuildError("manifest request schema_version is invalid")
    digest_fields = (
        "manifest_script_sha256",
        "input_blend_sha256",
        "input_plan_sha256",
        "input_build_id",
        "input_build_report_sha256",
        "input_object_registry_sha256",
    )
    if not all(_is_sha256(request[key]) for key in digest_fields):
        raise ManifestBuildError("manifest request contains an invalid SHA-256")
    if not request["input_blend_path"]:
        raise ManifestBuildError("manifest request input_blend_path is empty")
    if not request["build_request_path"]:
        raise ManifestBuildError(
            "manifest request build_request_path is empty; production callers "
            "must supply the reciprocal-route-build-request.json path so "
            "the emitter can read the plan and re-validate every input SHA",
        )
    # Fail-closed: the emitter script's own bytes must hash to the
    # declared manifest_script_sha256.
    if request["manifest_script_sha256"] != _sha256_file(Path(__file__)):
        raise ManifestBuildError(
            "emitter script bytes disagree with request manifest_script_sha256",
        )
    if not isinstance(request["rooms"], list) or not request["rooms"]:
        raise ManifestBuildError("manifest request rooms must be a non-empty list")
    if not isinstance(request["portals"], list) or not request["portals"]:
        raise ManifestBuildError("manifest request portals must be a non-empty list")
    for room in request["rooms"]:
        _validate_room_spec(room)
    for portal in request["portals"]:
        _validate_portal_spec(portal)
    return request


def _validate_room_spec(room: dict) -> None:
    keys = {
        "room_id", "label", "kind", "center_enu_m", "bound_object_id",
    }
    _expect_exact_keys(room, keys, "room spec")
    if not isinstance(room["room_id"], str) or not room["room_id"]:
        raise ManifestBuildError("room spec room_id is empty")
    if not isinstance(room["label"], str) or not room["label"].strip():
        raise ManifestBuildError(f"room {room['room_id']} label is empty")
    if room["kind"] not in ("exterior", "interior", "transition"):
        raise ManifestBuildError(
            f"room {room['room_id']} kind is invalid: {room['kind']!r}")
    if not isinstance(room["center_enu_m"], list) or len(room["center_enu_m"]) != 3:
        raise ManifestBuildError(
            f"room {room['room_id']} center_enu_m must be a 3-list")
    for c in room["center_enu_m"]:
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            raise ManifestBuildError(
                f"room {room['room_id']} center_enu_m has non-numeric component")
    if not isinstance(room["bound_object_id"], str) or not room["bound_object_id"]:
        raise ManifestBuildError(
            f"room {room['room_id']} bound_object_id is empty")


def _validate_portal_spec(portal: dict) -> None:
    keys = {
        "portal_id", "room_ids", "endpoints_enu_m", "clear_width_m",
        "clear_height_m", "bound_object_id", "source_input_sha256",
    }
    _expect_exact_keys(portal, keys, "portal spec")
    if not isinstance(portal["portal_id"], str) or not portal["portal_id"]:
        raise ManifestBuildError("portal spec portal_id is empty")
    if (not isinstance(portal["room_ids"], list)
            or len(portal["room_ids"]) != 2
            or portal["room_ids"][0] == portal["room_ids"][1]):
        raise ManifestBuildError(
            f"portal {portal['portal_id']} room_ids must be two distinct strings")
    if (not isinstance(portal["endpoints_enu_m"], list)
            or len(portal["endpoints_enu_m"]) != 2
            or not all(len(ep) == 3 for ep in portal["endpoints_enu_m"])):
        raise ManifestBuildError(
            f"portal {portal['portal_id']} endpoints_enu_m must be two 3-lists")
    cw = portal["clear_width_m"]
    if not isinstance(cw, (int, float)) or isinstance(cw, bool):
        raise ManifestBuildError(
            f"portal {portal['portal_id']} clear_width_m must be numeric")
    if cw <= 0 or cw > 100:
        raise ManifestBuildError(
            f"portal {portal['portal_id']} clear_width_m out of bounds")
    ch = portal["clear_height_m"]
    if not isinstance(ch, (int, float)) or isinstance(ch, bool):
        raise ManifestBuildError(
            f"portal {portal['portal_id']} clear_height_m must be numeric")
    if ch <= 0 or ch > 100:
        raise ManifestBuildError(
            f"portal {portal['portal_id']} clear_height_m out of bounds")
    if not isinstance(portal["bound_object_id"], str) or not portal["bound_object_id"]:
        raise ManifestBuildError(
            f"portal {portal['portal_id']} bound_object_id is empty")
    if not _is_sha256(portal["source_input_sha256"]):
        raise ManifestBuildError(
            f"portal {portal['portal_id']} source_input_sha256 is not 64-hex")


# --------------------------------------------------------------------------- #
# Build request (plan source) loading + SHA re-validation.
# --------------------------------------------------------------------------- #


def _load_build_request(build_request_path: Path) -> dict:
    raw, _sha = _stable_read_and_sha256(
        build_request_path,
        max_bytes=64 * 1024 * 1024,
        label="reciprocal-route-build-request",
    )
    if not raw:
        raise ManifestBuildError("build request bytes are absent")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestBuildError(f"build request is not valid JSON: {exc}") from exc


def _validate_build_request(
    build_request: dict,
    manifest_request: dict,
) -> dict:
    top_keys = {
        "schema_version", "build_id", "synthetic", "verification_level",
        "geometry_usability", "stage", "trust_effect", "base_build_id",
        "base_build_report_sha256", "base_blend_sha256",
        "base_blender_executable_sha256", "base_object_registry_sha256",
        "base_environment_module_plan_sha256", "runtime_script_sha256",
        "reciprocal_route_module_plan_sha256", "reciprocal_route_module_plan",
        "material_bindings", "object_registry", "requested_artifact",
    }
    _expect_exact_keys(build_request, top_keys, "build request")
    if build_request["schema_version"] != (
        "nantai.synthetic-village.reciprocal-route-runtime-request.v1"
    ):
        raise ManifestBuildError("build request schema_version is invalid")
    plan = build_request["reciprocal_route_module_plan"]
    if not isinstance(plan, dict):
        raise ManifestBuildError("build request plan is not a dict")
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    if plan_sha != build_request["reciprocal_route_module_plan_sha256"]:
        raise ManifestBuildError("build request plan SHA disagrees with plan bytes")
    if plan_sha != manifest_request["input_plan_sha256"]:
        raise ManifestBuildError(
            "manifest request input_plan_sha256 disagrees with build request plan",
        )
    if build_request["build_id"] != manifest_request["input_build_id"]:
        raise ManifestBuildError(
            "manifest request input_build_id disagrees with build request",
        )
    base_registry_sha = _sha256_bytes(
        _canonical_bytes(build_request["object_registry"][:EXPECTED_BASE_ROOTS]),
    )
    if base_registry_sha != manifest_request["input_object_registry_sha256"]:
        raise ManifestBuildError(
            "manifest request input_object_registry_sha256 disagrees with build request",
        )
    return build_request


def _load_and_validate_build_report(manifest_request: dict) -> dict:
    build_report_path = (
        Path(manifest_request["input_blend_path"]).resolve().parent
        / "reciprocal-route-build-report.json"
    )
    # Read the build report through a single O_NOFOLLOW descriptor so the SHA
    # and the parsed JSON come from the SAME bytes.  The previous pattern
    # (read_bytes + _sha256_file) opened the file twice, allowing a swap
    # between the parse and the hash -- a false cryptographic binding.
    raw, file_sha = _stable_read_and_sha256(
        build_report_path,
        max_bytes=64 * 1024 * 1024,
        label="reciprocal-route-build-report.json",
    )
    if not raw:
        raise ManifestBuildError("build report bytes are absent")
    try:
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestBuildError(f"build report is not valid JSON: {exc}") from exc
    if file_sha != manifest_request["input_build_report_sha256"]:
        raise ManifestBuildError(
            "build report file SHA disagrees with manifest request",
        )
    if report.get("build_id") != manifest_request["input_build_id"]:
        raise ManifestBuildError(
            "build report build_id disagrees with manifest request",
        )
    if report.get("artifact", {}).get("sha256") != manifest_request["input_blend_sha256"]:
        raise ManifestBuildError(
            "build report artifact.sha256 disagrees with manifest request",
        )
    return report


# --------------------------------------------------------------------------- #
# .blend loading.
# --------------------------------------------------------------------------- #


def _load_blend(manifest_request: dict) -> None:
    blend_path = Path(manifest_request["input_blend_path"]).resolve()
    if not blend_path.is_file():
        raise ManifestBuildError(f"blend file not found: {blend_path}")
    file_sha = _sha256_file(blend_path)
    if file_sha != manifest_request["input_blend_sha256"]:
        raise ManifestBuildError(
            "blend file SHA disagrees with manifest request input_blend_sha256",
        )
    try:
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
    except RuntimeError as exc:
        raise ManifestBuildError(f"failed to open blend file: {exc}") from exc


# --------------------------------------------------------------------------- #
# Mesh SHA computation.
# --------------------------------------------------------------------------- #


def _find_object_by_id(object_id: str):
    """Find a Blender object by its ``nv_stable_id`` custom property or
    its name. Duplicate stable-id matches are ambiguous and fail closed."""
    stable_matches = [
        obj for obj in bpy.data.objects
        if obj.get("nv_stable_id") == object_id
    ]
    if len(stable_matches) > 1:
        raise ManifestBuildError(
            f"bound_object_id {object_id!r} matches multiple nv_stable_id objects"
        )
    if stable_matches:
        return stable_matches[0]
    # Second pass: match by name (covers mesh__{part_id} and root objects).
    name_matches = [obj for obj in bpy.data.objects if obj.name == object_id]
    if len(name_matches) > 1:
        raise ManifestBuildError(
            f"bound_object_id {object_id!r} matches multiple object names"
        )
    if name_matches:
        return name_matches[0]
    return None


def _mesh_world_space_sha256(obj, depsgraph) -> str:
    """Compute a content-addressed SHA-256 over the object's evaluated
    mesh in world space.

    The SHA covers:
      - world-space vertex coordinates (float64, little-endian);
      - loop polygons as vertex-index tuples;
      - the object's name (so two objects with the same mesh but
        different stable identities produce different SHAs).

    This is the ``collision_proxy_sha256``: object identity, mesh geometry
    and transform are all bound; any of them changing changes the SHA.

    Objects with zero vertices or polygons are fail-closed: the emitter
    raises ``ManifestBuildError`` instead of emitting a placeholder SHA.
    """
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    try:
        if mesh is None or len(mesh.vertices) == 0 or len(mesh.polygons) == 0:
            raise ManifestBuildError(
                f"object {obj.name!r} has no evaluable geometry "
                f"(vertices={0 if mesh is None else len(mesh.vertices)}, "
                f"polygons={0 if mesh is None else len(mesh.polygons)})",
            )
        import struct
        world_matrix = obj.matrix_world
        digest = hashlib.sha256()
        # Object name (stable identity).
        digest.update(obj.name.encode("utf-8"))
        digest.update(b"\x00")
        # Vertex count + world-space coords (float64 little-endian).
        digest.update(struct.pack("<Q", len(mesh.vertices)))
        for vert in mesh.vertices:
            co = world_matrix @ vert.co
            digest.update(struct.pack("<ddd", float(co.x), float(co.y), float(co.z)))
        # Polygon count + loop vertex indices.
        digest.update(struct.pack("<Q", len(mesh.polygons)))
        for poly in mesh.polygons:
            digest.update(struct.pack("<Q", poly.loop_start))
            digest.update(struct.pack("<Q", poly.loop_total))
        for loop in mesh.loops:
            digest.update(struct.pack("<Q", loop.vertex_index))
        return digest.hexdigest()
    finally:
        if mesh is not None:
            eval_obj.to_mesh_clear()


def _collect_child_meshes(obj) -> list:
    """Collect all MESH descendants of ``obj`` recursively.  When a room
    or portal is bound to an EMPTY parent (common for module roots),
    the collision proxy SHA must cover every child mesh's geometry."""
    meshes = []
    for child in obj.children_recursive:
        if child.type == "MESH":
            meshes.append(child)
    return meshes


def _measure_collision_proxy_sha256(
    object_id: str,
    depsgraph,
) -> str:
    obj = _find_object_by_id(object_id)
    if obj is None:
        raise ManifestBuildError(
            f"bound_object_id {object_id!r} not found in scene",
        )
    if obj.type == "MESH":
        return _mesh_world_space_sha256(obj, depsgraph)
    if obj.type == "EMPTY":
        children = _collect_child_meshes(obj)
        if not children:
            raise ManifestBuildError(
                f"bound_object_id {object_id!r} is an EMPTY with no child "
                f"MESH objects; cannot measure collision proxy SHA",
            )
        # Combine every child mesh's SHA into one digest.  Sort by name
        # for deterministic ordering across runs.
        children.sort(key=lambda c: c.name)
        digest = hashlib.sha256()
        digest.update(obj.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(len(children)).encode("ascii"))
        digest.update(b"\x00")
        for child in children:
            child_sha = _mesh_world_space_sha256(child, depsgraph)
            digest.update(child.name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(child_sha.encode("ascii"))
            digest.update(b"\x00")
        return digest.hexdigest()
    raise ManifestBuildError(
        f"bound_object_id {object_id!r} is not a MESH or EMPTY "
        f"(type={obj.type})",
    )


# --------------------------------------------------------------------------- #
# Manifest assembly.
# --------------------------------------------------------------------------- #


def _build_manifest(
    manifest_request: dict,
    build_request: dict,
    build_report: dict,
    depsgraph,
) -> dict:
    rooms_out = []
    for room_spec in manifest_request["rooms"]:
        sha = _measure_collision_proxy_sha256(
            room_spec["bound_object_id"], depsgraph)
        rooms_out.append({
            "room_id": room_spec["room_id"],
            "label": room_spec["label"],
            "kind": room_spec["kind"],
            "center_enu_m": list(room_spec["center_enu_m"]),
            "bound_object_id": room_spec["bound_object_id"],
            "collision_proxy_sha256": sha,
        })
    portals_out = []
    for portal_spec in manifest_request["portals"]:
        sha = _measure_collision_proxy_sha256(
            portal_spec["bound_object_id"], depsgraph)
        portals_out.append({
            "portal_id": portal_spec["portal_id"],
            "room_ids": list(portal_spec["room_ids"]),
            "endpoints_enu_m": [list(ep) for ep in portal_spec["endpoints_enu_m"]],
            "clear_width_m": float(portal_spec["clear_width_m"]),
            "clear_height_m": float(portal_spec["clear_height_m"]),
            "bound_object_id": portal_spec["bound_object_id"],
            "collision_proxy_sha256": sha,
            "source_input_sha256": portal_spec["source_input_sha256"],
        })
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_id": MANIFEST_ID,
        "manifest_script_sha256": manifest_request["manifest_script_sha256"],
        "disclosure": DISCLOSURE,
        "input_blend_sha256": manifest_request["input_blend_sha256"],
        "input_build_id": manifest_request["input_build_id"],
        "input_build_report_sha256": manifest_request["input_build_report_sha256"],
        "input_plan_sha256": manifest_request["input_plan_sha256"],
        "input_object_registry_sha256": manifest_request["input_object_registry_sha256"],
        "base_blend_sha256": build_request["base_blend_sha256"],
        "base_build_id": build_request["base_build_id"],
        "base_build_report_sha256": build_request["base_build_report_sha256"],
        "base_environment_module_plan_sha256":
            build_request["base_environment_module_plan_sha256"],
        "reciprocal_route_module_plan_sha256":
            build_request["reciprocal_route_module_plan_sha256"],
        "runtime_script_sha256": build_request["runtime_script_sha256"],
        "build_report_artifact_sha256":
            build_report["artifact"]["sha256"],
        "build_report_artifact_size_bytes":
            build_report["artifact"]["size_bytes"],
        "rooms": rooms_out,
        "portals": portals_out,
    }
    return manifest


def _write_manifest(manifest: dict, staging_path: Path) -> Path:
    blob = _canonical_bytes(manifest)
    manifest_sha = _sha256_bytes(blob)
    out_path = staging_path / MANIFEST_NAME
    out_path.write_bytes(blob)
    # Sidecar SHA file for host-side verification.
    (staging_path / "roaming-graph-manifest.sha256").write_text(
        manifest_sha + "\n", encoding="utf-8")
    return out_path


def main(argv) -> int:
    try:
        request_path, staging_path = _runtime_paths(argv)
        request = _load_request(request_path)
        request = _validate_request(request)
        build_request = _load_build_request(Path(request["build_request_path"]))
        build_request = _validate_build_request(build_request, request)
        build_report = _load_and_validate_build_report(request)
        _load_blend(request)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        manifest = _build_manifest(request, build_request, build_report, depsgraph)
        out_path = _write_manifest(manifest, staging_path)
        manifest_sha = _sha256_file(out_path)
        print(f"manifest_sha256={manifest_sha}")
        print(f"manifest_path={out_path}")
        print(f"room_count={len(manifest['rooms'])}")
        print(f"portal_count={len(manifest['portals'])}")
        return 0
    except ManifestBuildError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 1
    except Exception as exc:  # pragma: no cover - Blender runtime errors
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
