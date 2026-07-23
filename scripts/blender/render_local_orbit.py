"""Render one local-orbit frame from a verified exact-218 Blender build."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import bpy

LOCAL_ORBIT_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-orbit-render-frame-request.v1"
)
REPORT_SCHEMA = "nantai.synthetic-village.local-production-render-frame-report.v4"
CAMERA_SCHEMA = "nantai.synthetic-village.local-production-camera-metadata.v4"
LOCAL_ORBIT_BUILD_ADAPTER = "windows-reciprocal-route-local-orbit-v1"
EXPECTED_INSTANCE_IDS = list(range(1, 219))
WATERWHEEL_ASSEMBLY_INSTANCE_IDS = list(range(155, 161))
PRIMARY_AZIMUTHS = list(range(0, 360, 45))
PRIMARY_ORBIT_IDS = [f"audit-waterwheel-az{azimuth:03d}" for azimuth in PRIMARY_AZIMUTHS]
PRIMARY_CAMERA_IDS = [f"camera-audit-overview-{index:03d}" for index in range(1, 9)]
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


class RuntimeRenderError(RuntimeError):
    """Stable failure raised before local-orbit frame publication."""


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
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _parse_scene_lineage(scene):
    raw = scene.get("nv_reciprocal_route_module_build")
    try:
        lineage = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeRenderError(
            "reciprocal-route scene lineage is absent or invalid",
        ) from exc
    if not isinstance(lineage, dict) or set(lineage) != LINEAGE_KEYS:
        raise RuntimeRenderError(
            "reciprocal-route scene lineage has unknown or missing fields",
        )
    if raw != json.dumps(lineage, separators=(",", ":"), sort_keys=True):
        raise RuntimeRenderError(
            "reciprocal-route scene lineage is not canonical JSON",
        )
    if (
        lineage["geometry_usability"] != "preview-only"
        or lineage["module_root_count"] != 43
        or lineage["topology_proxy_count"] != 6
        or lineage["stage"] != "modeled-unverified"
        or lineage["trust_effect"] != "none"
    ):
        raise RuntimeRenderError(
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
        raise RuntimeRenderError(
            "topology proxy mesh identity or visibility is invalid",
        )
    for obj in proxies:
        obj.hide_viewport = True


def _validate_local_orbit_boundary(request, *, scene, script_path):
    if request.get("schema_version") != LOCAL_ORBIT_RENDER_REQUEST_SCHEMA:
        raise RuntimeRenderError("local orbit render schema is invalid")
    if request.get("build_adapter") != LOCAL_ORBIT_BUILD_ADAPTER:
        raise RuntimeRenderError("local orbit build adapter is invalid")
    for key in (
        "renderer_script_sha256",
        "engine_script_sha256",
        "build_id",
        "blend_sha256",
        "reciprocal_route_module_plan_sha256",
        "environment_module_build_report_sha256",
        "object_registry_sha256",
        "source_camera_registry_sha256",
        "source_production_plan_sha256",
        "local_orbit_plan_sha256",
    ):
        if not _is_sha256(request.get(key)):
            raise RuntimeRenderError(f"request {key} is not a SHA-256")
    if _sha256_file(script_path) != request["renderer_script_sha256"]:
        raise RuntimeRenderError(
            "local orbit renderer digest does not match executing script",
        )
    registry = request.get("object_registry")
    if (
        not isinstance(registry, list)
        or [row.get("instance_id") for row in registry] != EXPECTED_INSTANCE_IDS
        or hashlib.sha256(_canonical_bytes(registry)).hexdigest()
        != request["object_registry_sha256"]
    ):
        raise RuntimeRenderError("local orbit object registry is not exact 1..218")
    if request["required_visible_instance_ids"] != WATERWHEEL_ASSEMBLY_INSTANCE_IDS:
        raise RuntimeRenderError(
            "local orbit required visible instance IDs are not exact",
        )
    source_plan = request.get("source_production_plan")
    if (
        not isinstance(source_plan, dict)
        or hashlib.sha256(_canonical_bytes(source_plan)).hexdigest()
        != request["source_production_plan_sha256"]
    ):
        raise RuntimeRenderError("local orbit source production plan digest is invalid")
    local_plan = request.get("local_orbit_plan")
    if (
        not isinstance(local_plan, dict)
        or hashlib.sha256(_canonical_bytes(local_plan)).hexdigest()
        != request["local_orbit_plan_sha256"]
    ):
        raise RuntimeRenderError("local orbit plan digest is invalid")
    if (
        local_plan.get("source_production_plan_sha256")
        != request["source_production_plan_sha256"]
        or local_plan.get("exact_build_id") != request["build_id"]
        or local_plan.get("exact_blend_sha256") != request["blend_sha256"]
        or local_plan.get("synthetic") is not True
        or local_plan.get("verification_level") != "L0"
        or local_plan.get("geometry_usability") != "preview-only"
        or local_plan.get("training_use") != "forbidden-as-multiview"
        or local_plan.get("trust_effect") != "none-quality-filter-only"
    ):
        raise RuntimeRenderError("local orbit plan trust or build binding is invalid")
    cameras = local_plan.get("cameras")
    if (
        not isinstance(cameras, list)
        or len(cameras) != 8
        or [row.get("orbit_camera_id") for row in cameras] != PRIMARY_ORBIT_IDS
        or [row.get("materialized_camera_id") for row in cameras]
        != PRIMARY_CAMERA_IDS
        or [row.get("azimuth_deg") for row in cameras] != PRIMARY_AZIMUTHS
    ):
        raise RuntimeRenderError("local orbit camera tuple is not exact")
    orbit_camera_id = request["orbit_camera_id"]
    camera_id = request.get("camera", {}).get("camera_id")
    selected = next(
        (row for row in cameras if row.get("orbit_camera_id") == orbit_camera_id),
        None,
    )
    if selected is None or selected.get("materialized_camera_id") != camera_id:
        raise RuntimeRenderError("local orbit camera mapping is invalid")
    lineage = _parse_scene_lineage(scene)
    if lineage["build_id"] != request["build_id"]:
        raise RuntimeRenderError("local orbit scene build ID does not match request")
    if lineage["reciprocal_route_module_plan_sha256"] != request[
        "reciprocal_route_module_plan_sha256"
    ]:
        raise RuntimeRenderError(
            "local orbit scene module plan digest does not match request",
        )


def _load_engine(expected_sha256, *, engine_path=None):
    path = (
        Path(engine_path)
        if engine_path is not None
        else Path(__file__).with_name("render_synthetic_village.py")
    )
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise RuntimeRenderError("frozen render engine cannot be read") from exc
    if (
        not _is_sha256(expected_sha256)
        or hashlib.sha256(source).hexdigest() != expected_sha256
    ):
        raise RuntimeRenderError(
            "frozen render engine digest does not match request",
        )
    spec = importlib.util.spec_from_file_location(
        "nantai_frozen_local_orbit_renderer_v1",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeRenderError("frozen render engine cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _prepare_engine(engine):
    engine.LOCAL_PRODUCTION_REQUEST_SCHEMA = LOCAL_ORBIT_RENDER_REQUEST_SCHEMA
    engine.LOCAL_PRODUCTION_REPORT_SCHEMA = REPORT_SCHEMA
    engine.LOCAL_PRODUCTION_CAMERA_SCHEMA = CAMERA_SCHEMA
    engine.__file__ = __file__

    def validate_registry(object_registry):
        engine._expect_list(object_registry, 218, "object_registry")
        if [row.get("instance_id") for row in object_registry] != EXPECTED_INSTANCE_IDS:
            raise engine.RuntimeRenderError(
                "object registry instance IDs are not stable 1 through 218",
            )
        stable_ids = []
        for row in object_registry:
            engine._expect_keys(
                row,
                ("object_id", "instance_id", "semantic_id", "material_id", "variant_id"),
                "object registry row",
            )
            if (
                not isinstance(row["object_id"], str)
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["object_id"])
                is None
                or isinstance(row["semantic_id"], bool)
                or not isinstance(row["semantic_id"], int)
                or not 3 <= row["semantic_id"] < len(engine.SEMANTIC_CLASSES)
                or isinstance(row["material_id"], bool)
                or not isinstance(row["material_id"], int)
                or not 1 <= row["material_id"] <= 255
            ):
                raise engine.RuntimeRenderError("object registry row is invalid")
            stable_ids.append(row["object_id"])
        if len(set(stable_ids)) != 218:
            raise engine.RuntimeRenderError(
                "object registry stable IDs are not unique",
            )

    engine._validate_object_registry_contract = validate_registry
    return engine


def _validate_request(request, engine):
    _validate_local_orbit_boundary(
        request,
        scene=bpy.context.scene,
        script_path=Path(__file__),
    )
    _prepare_topology_proxies_for_production(bpy.data.objects)
    internal = copy.deepcopy(request)
    internal.pop("environment_module_build_report_sha256")
    internal.pop("reciprocal_route_module_plan_sha256")
    internal.pop("engine_script_sha256")
    internal.pop("required_visible_instance_ids")
    internal.pop("source_camera_registry_sha256")
    internal.pop("source_production_plan")
    internal.pop("source_production_plan_sha256")
    internal.pop("local_orbit_plan")
    internal.pop("local_orbit_plan_sha256")
    internal.pop("orbit_camera_id")
    internal["build_adapter"] = "windows-textured-v2"
    internal["build_id"] = bpy.context.scene.get("nv_build_id")
    try:
        engine._validate_request(internal)
    except engine.RuntimeRenderError as exc:
        raise RuntimeRenderError(str(exc)) from exc
    return request


def main():
    try:
        marker = sys.argv.index("--")
        values = sys.argv[marker + 1 :]
        if (
            len(values) != 4
            or values[0] != "--request"
            or values[2] != "--staging"
        ):
            raise RuntimeRenderError(
                "expected exactly --request <file> --staging <directory>",
            )
        request_hint = json.loads(Path(values[1]).read_text(encoding="utf-8"))
        if not isinstance(request_hint, dict):
            raise RuntimeRenderError("engine identity cannot be read before import")
        engine_sha256 = request_hint.get("engine_script_sha256")
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeRenderError("engine identity cannot be read before import") from exc
    engine = _prepare_engine(_load_engine(engine_sha256))
    try:
        request_path, staging_path = engine._runtime_argv(sys.argv)
        request = engine._load_request(request_path)
    except engine.RuntimeRenderError as exc:
        raise RuntimeRenderError(str(exc)) from exc
    request = _validate_request(request, engine)
    try:
        engine._execute_render(request, staging_path)
    except engine.RuntimeRenderError as exc:
        raise RuntimeRenderError(str(exc)) from exc


if __name__ == "__main__":
    try:
        main()
    except RuntimeRenderError as exc:
        print(f"NANTAI_LOCAL_ORBIT_RENDER_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
