"""Fresh exact-218 clearance preflight for reciprocal-route Blender builds."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import bpy

REQUEST_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-clearance-request.v1"
)
REPORT_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-clearance-report.v1"
)
PROFILE_ID = "synthetic-village-coverage-180-v1"
GEOMETRY_TRUST = "simplified-pbr-not-render-parity"
TRUST_EFFECT = "none-quality-filter-only"
EXPECTED_INSTANCE_IDS = list(range(1, 219))
LINEAGE_KEYS = {
    "build_id",
    "reciprocal_route_module_plan_sha256",
    "geometry_usability",
    "module_root_count",
    "topology_proxy_count",
    "stage",
    "trust_effect",
}
EXPECTED_TOPOLOGY_PROXY_COUNT = 6
REQUEST_KEYS = {
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
    "environment_module_build_report_sha256",
    "reciprocal_route_module_plan_sha256",
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
}


class RuntimePreflightError(RuntimeError):
    """Stable failure raised before reciprocal report publication."""


def _canonical_bytes(payload):
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _parse_scene_lineage(scene):
    raw = scene.get("nv_reciprocal_route_module_build")
    try:
        lineage = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimePreflightError(
            "reciprocal-route scene lineage is absent or invalid",
        ) from exc
    if not isinstance(lineage, dict) or set(lineage) != LINEAGE_KEYS:
        raise RuntimePreflightError(
            "reciprocal-route scene lineage has unknown or missing fields",
        )
    if raw != json.dumps(lineage, separators=(",", ":"), sort_keys=True):
        raise RuntimePreflightError(
            "reciprocal-route scene lineage is not canonical JSON",
        )
    if (
        lineage["geometry_usability"] != "preview-only"
        or lineage["module_root_count"] != 43
        or lineage["topology_proxy_count"] != 6
        or lineage["stage"] != "modeled-unverified"
        or lineage["trust_effect"] != "none"
    ):
        raise RuntimePreflightError(
            "reciprocal-route scene lineage trust contract is invalid",
        )
    return lineage


def _prepare_topology_proxies_for_production(objects):
    proxies = [
        obj
        for obj in objects
        if obj.type == "MESH" and obj.get("nv_proxy_topology", False)
    ]
    proxy_ids = [obj.get("nv_stable_id") for obj in proxies]
    if (
        len(proxies) != EXPECTED_TOPOLOGY_PROXY_COUNT
        or len(set(proxy_ids)) != EXPECTED_TOPOLOGY_PROXY_COUNT
        or any(not isinstance(proxy_id, str) or not proxy_id for proxy_id in proxy_ids)
        or any(
            obj.get("nv_root") is True
            or obj.get("nv_stage") != "modeled-unverified"
            or obj.get("nv_trust_effect") != "none"
            or obj.get("nv_geometry_usability") != "preview-only"
            or not obj.hide_render
            for obj in proxies
        )
    ):
        raise RuntimePreflightError(
            "topology proxy mesh identity or visibility is invalid",
        )
    for obj in proxies:
        obj.hide_viewport = True


def _validate_reciprocal_boundary(request, *, scene, script_path):
    """Validate identities unique to the additive exact-218 caller."""

    if request.get("schema_version") != REQUEST_SCHEMA:
        raise RuntimePreflightError(
            "reciprocal-route preflight schema is invalid",
        )
    for key in (
        "preflight_script_sha256",
        "build_id",
        "reciprocal_route_module_plan_sha256",
        "object_registry_sha256",
    ):
        if not _is_sha256(request.get(key)):
            raise RuntimePreflightError(f"request {key} is not a SHA-256")
    if _sha256_file(script_path) != request["preflight_script_sha256"]:
        raise RuntimePreflightError(
            "preflight script digest does not match executing script",
        )
    registry = request.get("object_registry")
    if (
        not isinstance(registry, list)
        or [row.get("instance_id") for row in registry]
        != EXPECTED_INSTANCE_IDS
    ):
        raise RuntimePreflightError(
            "object registry is not exact 1..218",
        )
    if hashlib.sha256(_canonical_bytes(registry)).hexdigest() != request[
        "object_registry_sha256"
    ]:
        raise RuntimePreflightError("object registry digest is invalid")
    lineage = _parse_scene_lineage(scene)
    if lineage["build_id"] != request["build_id"]:
        raise RuntimePreflightError(
            "reciprocal-route scene build ID does not match request",
        )
    if lineage["reciprocal_route_module_plan_sha256"] != request[
        "reciprocal_route_module_plan_sha256"
    ]:
        raise RuntimePreflightError(
            "reciprocal-route scene plan digest does not match request",
        )


def _load_engine():
    path = Path(__file__).with_name("preflight_production_cameras.py")
    spec = importlib.util.spec_from_file_location(
        "nantai_frozen_production_preflight_v1",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimePreflightError("frozen preflight engine cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_request(request, raw, engine):
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise RuntimePreflightError("request has unknown or missing fields")
    _validate_reciprocal_boundary(
        request,
        scene=bpy.context.scene,
        script_path=Path(__file__),
    )
    _prepare_topology_proxies_for_production(bpy.data.objects)
    if (
        request["profile_id"] != PROFILE_ID
        or request["synthetic"] is not True
        or request["geometry_trust"] != GEOMETRY_TRUST
        or request["trust_effect"] != TRUST_EFFECT
    ):
        raise RuntimePreflightError("request provenance contract is invalid")
    for key in (
        "production_plan_sha256",
        "camera_registry_sha256",
        "blender_executable_sha256",
        "blend_sha256",
        "build_report_sha256",
        "environment_module_build_report_sha256",
        "policy_sha256",
        "preflight_id",
    ):
        if not _is_sha256(request[key]):
            raise RuntimePreflightError(f"request {key} is not a SHA-256")
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
        or engine._production_registry_digest(plan)
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
    try:
        engine._validate_policy(request["policy"])
    except engine.RuntimePreflightError as exc:
        raise RuntimePreflightError(str(exc)) from exc
    if hashlib.sha256(_canonical_bytes(request["policy"])).hexdigest() != request[
        "policy_sha256"
    ]:
        raise RuntimePreflightError("clearance policy digest is invalid")
    unsigned = dict(request)
    unsigned.pop("preflight_id")
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != request[
        "preflight_id"
    ]:
        raise RuntimePreflightError(
            "preflight ID does not bind request inputs",
        )
    if hashlib.sha256(raw).hexdigest() == "0" * 64:
        raise RuntimePreflightError("request digest is impossible")
    return request


def _write_report(request, raw, report_path, engine):
    cameras = {
        row["camera_id"]: row
        for row in request["production_plan"]["cameras"]
    }
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    measured = [
        engine._measure_camera(cameras[camera_id], request, depsgraph)
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
        "preflight_script_sha256": request["preflight_script_sha256"],
        "blend_sha256": request["blend_sha256"],
        "build_report_sha256": request["build_report_sha256"],
        "environment_module_build_report_sha256": request[
            "environment_module_build_report_sha256"
        ],
        "reciprocal_route_module_plan_sha256": request[
            "reciprocal_route_module_plan_sha256"
        ],
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
    if temporary.exists() or engine._is_reparse_point(temporary):
        raise RuntimePreflightError("temporary report path already exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw_report)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, report_path)
    except Exception:
        if temporary.exists() and not engine._is_reparse_point(temporary):
            temporary.unlink()
        raise
    print(
        "NANTAI_RECIPROCAL_PREFLIGHT_OK "
        f"preflight_id={request['preflight_id']} "
        f"cameras={len(measured)}",
        flush=True,
    )


def main():
    engine = _load_engine()
    try:
        request_path, report_path = engine._runtime_argv(sys.argv)
        request, raw = engine._load_request(request_path)
    except engine.RuntimePreflightError as exc:
        raise RuntimePreflightError(str(exc)) from exc
    _write_report(
        _validate_request(request, raw, engine),
        raw,
        report_path,
        engine,
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimePreflightError as exc:
        print(f"NANTAI_RECIPROCAL_PREFLIGHT_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
