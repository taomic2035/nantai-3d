"""Measure scene-bound clearance for selected production cameras in one run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

REQUEST_SCHEMA = "nantai.synthetic-village.production-clearance-request.v1"
REPORT_SCHEMA = "nantai.synthetic-village.production-clearance-report.v1"
EVIDENCE_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-evidence.v1"
)
DECISION_SCHEMA = (
    "nantai.synthetic-village.production-camera-clearance-decision.v1"
)
POLICY_SCHEMA = "nantai.synthetic-village.production-clearance-policy.v1"
PROFILE_ID = "synthetic-village-coverage-180-v1"
GEOMETRY_TRUST = "simplified-pbr-not-render-parity"
TRUST_EFFECT = "none-quality-filter-only"
SAMPLE_GRID = (-0.9, -0.45, 0.0, 0.45, 0.9)
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RAY_DISTANCE_M = 2000.0
OPENCV_TO_BLENDER = Matrix.Diagonal((1.0, -1.0, -1.0, 1.0))


class RuntimePreflightError(RuntimeError):
    """Stable failure raised before report publication."""


def _canonical_bytes(payload):
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimePreflightError(
                f"request contains duplicate JSON key: {key}",
            )
        result[key] = value
    return result


def _reject_constant(value):
    raise RuntimePreflightError(
        f"request contains non-finite JSON number: {value}",
    )


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _is_reparse_point(path):
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _assert_direct_path(path, label, leaf_may_be_absent=False):
    if _is_reparse_point(path) or _is_reparse_point(path.parent):
        raise RuntimePreflightError(f"{label} path is redirected")
    try:
        resolved_parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimePreflightError(f"{label} parent is unavailable") from exc
    if os.path.normcase(str(resolved_parent)) != os.path.normcase(
        str(path.parent),
    ):
        raise RuntimePreflightError(f"{label} path is redirected")
    if not leaf_may_be_absent:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RuntimePreflightError(f"{label} path is unavailable") from exc
        if os.path.normcase(str(resolved)) != os.path.normcase(str(path)):
            raise RuntimePreflightError(f"{label} path is redirected")


def _signature(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _runtime_argv(argv):
    try:
        marker = argv.index("--")
    except ValueError as exc:
        raise RuntimePreflightError(
            "runtime arguments must follow --",
        ) from exc
    values = argv[marker + 1 :]
    if (
        len(values) != 4
        or values[0] != "--request"
        or values[2] != "--report"
    ):
        raise RuntimePreflightError(
            "expected exactly --request <file> --report <file>",
        )
    request_path = Path(values[1])
    report_path = Path(values[3])
    if not request_path.is_absolute() or not report_path.is_absolute():
        raise RuntimePreflightError(
            "request and report paths must be absolute",
        )
    request_path = request_path.absolute()
    report_path = report_path.absolute()
    if not request_path.is_file():
        raise RuntimePreflightError("request file does not exist")
    if report_path.exists():
        raise RuntimePreflightError("report path already exists")
    _assert_direct_path(request_path, "request")
    _assert_direct_path(report_path, "report", leaf_may_be_absent=True)
    return request_path, report_path


def _load_request(path):
    try:
        before = path.stat()
        if before.st_size <= 0 or before.st_size > MAX_REQUEST_BYTES:
            raise RuntimePreflightError("request size is invalid")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _signature(before) != _signature(opened):
                raise RuntimePreflightError(
                    "request changed before bounded read",
                )
            raw = stream.read(MAX_REQUEST_BYTES + 1)
            after_open = os.fstat(stream.fileno())
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_REQUEST_BYTES
            or _signature(opened) != _signature(after_open)
            or _signature(before) != _signature(after)
        ):
            raise RuntimePreflightError(
                "request changed during bounded read",
            )
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        if raw != _canonical_bytes(parsed):
            raise RuntimePreflightError("request must be canonical JSON")
        return parsed, raw
    except RuntimePreflightError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePreflightError(
            "request is not valid bounded UTF-8 JSON",
        ) from exc


def _expect_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RuntimePreflightError(
            f"{label} has unknown or missing fields",
        )


def _production_registry_digest(plan):
    payload = {
        "profile_id": plan["profile_id"],
        "plan_schema": plan["plan_schema"],
        "scene_plan_sha256": plan["scene_plan_sha256"],
        "elevated_topology_sha256": plan["elevated_topology_sha256"],
        "cameras": [
            {
                "camera_id": camera["camera_id"],
                "group_id": camera["group_id"],
                "topology_ref": camera["topology_ref"],
                "c2w_opencv": camera["c2w_opencv"],
            }
            for camera in plan["cameras"]
        ],
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_policy(policy):
    _expect_keys(
        policy,
        (
            "schema_version",
            "policy_id",
            "sample_grid",
            "upper_middle_min_sample_y",
            "near_distance_m",
            "minimum_upper_middle_near_hit_count",
            "trust_effect",
        ),
        "clearance policy",
    )
    if (
        policy["schema_version"] != POLICY_SCHEMA
        or policy["policy_id"] != "synthetic-village-clearance-v1"
        or policy["sample_grid"] != list(SAMPLE_GRID)
        or policy["upper_middle_min_sample_y"] != 0.0
        or isinstance(policy["near_distance_m"], bool)
        or not isinstance(policy["near_distance_m"], (int, float))
        or not math.isfinite(policy["near_distance_m"])
        or not 0.0 < policy["near_distance_m"] <= 100.0
        or isinstance(
            policy["minimum_upper_middle_near_hit_count"],
            bool,
        )
        or not isinstance(
            policy["minimum_upper_middle_near_hit_count"],
            int,
        )
        or not 1
        <= policy["minimum_upper_middle_near_hit_count"]
        <= 15
        or policy["trust_effect"] != TRUST_EFFECT
    ):
        raise RuntimePreflightError("clearance policy is invalid")


def _validate_request(request, raw):
    _expect_keys(
        request,
        (
            "schema_version",
            "profile_id",
            "production_plan",
            "production_plan_sha256",
            "camera_registry_sha256",
            "selected_camera_ids",
            "build_id",
            "blender_executable_sha256",
            "preflight_script_sha256",
            "blend_sha256",
            "build_report_sha256",
            "object_registry_sha256",
            "object_registry",
            "auxiliary_registry",
            "semantic_registry",
            "policy",
            "policy_sha256",
            "preflight_id",
            "synthetic",
            "geometry_trust",
            "trust_effect",
        ),
        "request",
    )
    if (
        request["schema_version"] != REQUEST_SCHEMA
        or request["profile_id"] != PROFILE_ID
        or request["synthetic"] is not True
        or request["geometry_trust"] != GEOMETRY_TRUST
        or request["trust_effect"] != TRUST_EFFECT
    ):
        raise RuntimePreflightError("request provenance contract is invalid")
    for key in (
        "production_plan_sha256",
        "camera_registry_sha256",
        "build_id",
        "blender_executable_sha256",
        "preflight_script_sha256",
        "blend_sha256",
        "build_report_sha256",
        "object_registry_sha256",
        "policy_sha256",
        "preflight_id",
    ):
        if not _is_sha256(request[key]):
            raise RuntimePreflightError(f"request {key} is not a SHA-256")
    if _sha256_file(Path(__file__)) != request["preflight_script_sha256"]:
        raise RuntimePreflightError(
            "preflight script digest does not match executing script",
        )
    executable_path = Path(bpy.app.binary_path).absolute()
    if (
        not executable_path.is_file()
        or _sha256_file(executable_path)
        != request["blender_executable_sha256"]
    ):
        raise RuntimePreflightError(
            "executing Blender binary does not match immutable digest",
        )
    blend_path = Path(bpy.data.filepath).absolute()
    if (
        not blend_path.is_file()
        or _sha256_file(blend_path) != request["blend_sha256"]
    ):
        raise RuntimePreflightError(
            "loaded Blender file does not match immutable digest",
        )
    if (
        bpy.app.version_string != "4.5.11 LTS"
        or bpy.app.build_hash.decode("ascii") != "4db51e9d1e1e"
    ):
        raise RuntimePreflightError(
            "executing Blender identity is not pinned 4.5.11 LTS",
        )
    scene = bpy.context.scene
    if request["build_id"] not in {
        scene.get("nv_build_id"),
        scene.get("nv_preview_id"),
    }:
        raise RuntimePreflightError(
            "loaded Blender scene build ID does not match request",
        )
    if (
        scene.get("nv_fidelity") != GEOMETRY_TRUST
        or scene.get("nv_synthetic") is not True
    ):
        raise RuntimePreflightError(
            "loaded Blender scene provenance is invalid",
        )

    plan = request["production_plan"]
    if (
        not isinstance(plan, dict)
        or plan.get("profile_id") != PROFILE_ID
        or plan.get("camera_count") != 180
        or plan.get("declared_target_count") != 180
        or plan.get("complete") is not True
        or plan.get("unplaced_groups") != []
        or plan.get("geometry_trust") != GEOMETRY_TRUST
        or plan.get("verification_level") != "L2"
        or hashlib.sha256(_canonical_bytes(plan)).hexdigest()
        != request["production_plan_sha256"]
        or _production_registry_digest(plan)
        != request["camera_registry_sha256"]
    ):
        raise RuntimePreflightError(
            "production plan or camera registry identity is invalid",
        )
    cameras = plan.get("cameras")
    if (
        not isinstance(cameras, list)
        or len(cameras) != 180
        or len(
            {
                row.get("camera_id")
                for row in cameras
                if isinstance(row, dict)
            },
        )
        != 180
    ):
        raise RuntimePreflightError(
            "production camera registry is incomplete",
        )
    selected = request["selected_camera_ids"]
    if (
        not isinstance(selected, list)
        or not selected
        or len(selected) != len(set(selected))
        or selected
        != [
            row["camera_id"]
            for row in cameras
            if row["camera_id"] in set(selected)
        ]
    ):
        raise RuntimePreflightError(
            "selected camera IDs are not a unique plan-ordered subset",
        )

    object_registry = request["object_registry"]
    if (
        not isinstance(object_registry, list)
        or len(object_registry) != 130
        or [row.get("instance_id") for row in object_registry]
        != list(range(1, 131))
        or hashlib.sha256(_canonical_bytes(object_registry)).hexdigest()
        != request["object_registry_sha256"]
    ):
        raise RuntimePreflightError("object registry identity is invalid")
    _validate_policy(request["policy"])
    if (
        hashlib.sha256(_canonical_bytes(request["policy"])).hexdigest()
        != request["policy_sha256"]
    ):
        raise RuntimePreflightError("clearance policy digest is invalid")
    unsigned = dict(request)
    unsigned.pop("preflight_id")
    if (
        hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
        != request["preflight_id"]
    ):
        raise RuntimePreflightError(
            "preflight ID does not bind request inputs",
        )
    if hashlib.sha256(raw).hexdigest() == "0" * 64:
        raise RuntimePreflightError("request digest is impossible")
    return request


def _registry_value(obj, key):
    value = obj.get(key)
    if key == "nv_semantic_id":
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return value if isinstance(value, str) and value else None


def _measure_camera(camera, request, depsgraph):
    matrix_blender = Matrix(camera["c2w_opencv"]) @ OPENCV_TO_BLENDER
    origin = matrix_blender.translation
    rotation = matrix_blender.to_3x3()
    intrinsics = camera["intrinsics"]
    width = float(intrinsics["width_px"])
    height = float(intrinsics["height_px"])
    fx = float(intrinsics["fx"])
    fy = float(intrinsics["fy"])
    cx = float(intrinsics["cx"])
    cy = float(intrinsics["cy"])
    if (
        width != 1024.0
        or height != 576.0
        or cx != 512.0
        or cy != 288.0
        or not all(
            math.isfinite(value) and value > 0.0 for value in (fx, fy)
        )
    ):
        raise RuntimePreflightError(
            "production camera intrinsics are invalid",
        )
    rays = []
    for sample_y in SAMPLE_GRID:
        for sample_x in SAMPLE_GRID:
            pixel_x = (sample_x + 1.0) * 0.5 * (width - 1.0)
            pixel_y = (1.0 - sample_y) * 0.5 * (height - 1.0)
            local_direction = Vector(
                (
                    (pixel_x - cx) / fx,
                    (cy - pixel_y) / fy,
                    -1.0,
                ),
            ).normalized()
            direction = (rotation @ local_direction).normalized()
            hit, location, _normal, _face, obj, _matrix = (
                bpy.context.scene.ray_cast(
                    depsgraph,
                    origin,
                    direction,
                    distance=MAX_RAY_DISTANCE_M,
                )
            )
            if hit:
                stable_id = _registry_value(obj, "nv_stable_id")
                if stable_id is None:
                    stable_id = _registry_value(obj, "nv_root_id")
                ray = {
                    "sample_x": sample_x,
                    "sample_y": sample_y,
                    "hit": True,
                    "distance_m": round((location - origin).length, 9),
                    "object_name": obj.name,
                    "stable_id": stable_id,
                    "part_id": _registry_value(obj, "nv_part_id"),
                    "semantic_id": _registry_value(
                        obj,
                        "nv_semantic_id",
                    ),
                }
            else:
                ray = {
                    "sample_x": sample_x,
                    "sample_y": sample_y,
                    "hit": False,
                    "distance_m": None,
                    "object_name": None,
                    "stable_id": None,
                    "part_id": None,
                    "semantic_id": None,
                }
            rays.append(ray)
    evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "camera_id": camera["camera_id"],
        "rays": rays,
    }
    evidence_sha256 = hashlib.sha256(
        _canonical_bytes(evidence),
    ).hexdigest()
    policy = request["policy"]
    near_count = sum(
        1
        for row in rays
        if (
            row["hit"]
            and row["sample_y"] >= policy["upper_middle_min_sample_y"]
            and row["distance_m"] < policy["near_distance_m"]
        )
    )
    passes = (
        near_count
        < policy["minimum_upper_middle_near_hit_count"]
    )
    decision = {
        "schema_version": DECISION_SCHEMA,
        "camera_id": camera["camera_id"],
        "policy_sha256": request["policy_sha256"],
        "evidence_sha256": evidence_sha256,
        "measured_upper_middle_near_hit_count": near_count,
        "passes": passes,
        "failed_rule_ids": (
            [] if passes else ["upper-middle-near-hit-count"]
        ),
        "trust_effect": TRUST_EFFECT,
    }
    return evidence, decision


def _execute(request, raw, report_path):
    cameras = {
        row["camera_id"]: row
        for row in request["production_plan"]["cameras"]
    }
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    measured = [
        _measure_camera(cameras[camera_id], request, depsgraph)
        for camera_id in request["selected_camera_ids"]
    ]
    report = {
        "schema_version": REPORT_SCHEMA,
        "profile_id": PROFILE_ID,
        "preflight_id": request["preflight_id"],
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "production_plan_sha256": request["production_plan_sha256"],
        "camera_registry_sha256": request["camera_registry_sha256"],
        "build_id": request["build_id"],
        "blender_executable_sha256": request[
            "blender_executable_sha256"
        ],
        "preflight_script_sha256": request[
            "preflight_script_sha256"
        ],
        "blend_sha256": request["blend_sha256"],
        "build_report_sha256": request["build_report_sha256"],
        "object_registry_sha256": request["object_registry_sha256"],
        "policy_sha256": request["policy_sha256"],
        "evidence": [row[0] for row in measured],
        "decisions": [row[1] for row in measured],
        "synthetic": True,
        "geometry_trust": GEOMETRY_TRUST,
        "trust_effect": TRUST_EFFECT,
    }
    raw_report = _canonical_bytes(report)
    temporary = report_path.with_name(
        f".{report_path.name}.tmp-{request['preflight_id'][:12]}",
    )
    if temporary.exists() or _is_reparse_point(temporary):
        raise RuntimePreflightError(
            "temporary report path already exists",
        )
    try:
        with temporary.open("xb") as stream:
            stream.write(raw_report)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, report_path)
    except Exception:
        if temporary.exists() and not _is_reparse_point(temporary):
            temporary.unlink()
        raise
    print(
        "NANTAI_PREFLIGHT_OK "
        f"preflight_id={request['preflight_id']} "
        f"cameras={len(measured)}",
        flush=True,
    )


def main():
    request_path, report_path = _runtime_argv(sys.argv)
    request, raw = _load_request(request_path)
    _execute(_validate_request(request, raw), raw, report_path)


if __name__ == "__main__":
    try:
        main()
    except RuntimePreflightError as exc:
        print(f"NANTAI_PREFLIGHT_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
