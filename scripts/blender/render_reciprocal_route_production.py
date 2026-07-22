"""Render six production layers from one verified exact-218 Blender build."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import bpy

REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-request.v8"
)
REPORT_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-report.v4"
)
CAMERA_SCHEMA = (
    "nantai.synthetic-village.local-production-camera-metadata.v4"
)
RECIPROCAL_BUILD_ADAPTER = "windows-reciprocal-route-v1"
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
ROLE_INSTANCE_IDS = {
    "central-courtyard-downhill": list(range(176, 183)),
    "bridge-deck-crossing": list(range(183, 189)),
    "watermill-tailrace": list(range(189, 196)),
    "covered-gallery-underpass": list(range(196, 205)),
    "forest-orchard-boundary": list(range(205, 212)),
    "lower-valley-uphill": list(range(212, 219)),
}


class RuntimeRenderError(RuntimeError):
    """Stable failure raised before reciprocal frame publication."""


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


def _validate_reciprocal_boundary(request, *, scene, script_path):
    """Validate identities unique to the additive exact-218 renderer."""

    if request.get("schema_version") != REQUEST_SCHEMA:
        raise RuntimeRenderError("reciprocal-route render schema is invalid")
    if request.get("build_adapter") != RECIPROCAL_BUILD_ADAPTER:
        raise RuntimeRenderError(
            "reciprocal-route build adapter is invalid",
        )
    for key in (
        "renderer_script_sha256",
        "engine_script_sha256",
        "build_id",
        "reciprocal_route_module_plan_sha256",
        "environment_module_build_report_sha256",
        "object_registry_sha256",
        "role_camera_candidate_sha256",
        "source_camera_registry_sha256",
        "source_production_plan_sha256",
    ):
        if not _is_sha256(request.get(key)):
            raise RuntimeRenderError(f"request {key} is not a SHA-256")
    if _sha256_file(script_path) != request["renderer_script_sha256"]:
        raise RuntimeRenderError(
            "renderer script digest does not match executing script",
        )
    registry = request.get("object_registry")
    if (
        not isinstance(registry, list)
        or [row.get("instance_id") for row in registry]
        != EXPECTED_INSTANCE_IDS
    ):
        raise RuntimeRenderError("object registry is not exact 1..218")
    if hashlib.sha256(_canonical_bytes(registry)).hexdigest() != request[
        "object_registry_sha256"
    ]:
        raise RuntimeRenderError("object registry digest is invalid")
    role_module_id = request.get("role_module_id")
    expected_visible_ids = ROLE_INSTANCE_IDS.get(role_module_id)
    if expected_visible_ids is None:
        raise RuntimeRenderError("reciprocal role module is invalid")
    if request.get("required_visible_instance_ids") != expected_visible_ids:
        raise RuntimeRenderError(
            "required visible instance IDs do not match the complete role segment",
        )
    source_plan = request.get("source_production_plan")
    if not isinstance(source_plan, dict) or hashlib.sha256(
        _canonical_bytes(source_plan),
    ).hexdigest() != request["source_production_plan_sha256"]:
        raise RuntimeRenderError("source production plan digest is invalid")
    role_candidate = request.get("role_camera_candidate")
    if not isinstance(role_candidate, dict) or hashlib.sha256(
        _canonical_bytes(role_candidate),
    ).hexdigest() != request["role_camera_candidate_sha256"]:
        raise RuntimeRenderError("role camera candidate digest is invalid")
    if (
        role_candidate.get("role_module_id") != role_module_id
        or role_candidate.get("bound_production_plan_sha256")
        != request["source_production_plan_sha256"]
        or role_candidate.get("bound_camera_registry_sha256")
        != request["source_camera_registry_sha256"]
    ):
        raise RuntimeRenderError(
            "role camera candidate source bindings are invalid",
        )
    lineage = _parse_scene_lineage(scene)
    if lineage["build_id"] != request["build_id"]:
        raise RuntimeRenderError(
            "reciprocal-route scene build ID does not match request",
        )
    if lineage["reciprocal_route_module_plan_sha256"] != request[
        "reciprocal_route_module_plan_sha256"
    ]:
        raise RuntimeRenderError(
            "reciprocal-route scene plan digest does not match request",
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
        raise RuntimeRenderError("engine script cannot be read") from exc
    if (
        not _is_sha256(expected_sha256)
        or hashlib.sha256(source).hexdigest() != expected_sha256
    ):
        raise RuntimeRenderError(
            "engine script digest does not match imported script",
        )
    spec = importlib.util.spec_from_file_location(
        "nantai_frozen_production_renderer_v4",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeRenderError("frozen render engine cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _prepare_engine(engine):
    """Adapt only the loaded module object; the frozen script bytes stay intact."""

    engine.LOCAL_PRODUCTION_REQUEST_SCHEMA = REQUEST_SCHEMA
    engine.LOCAL_PRODUCTION_REPORT_SCHEMA = REPORT_SCHEMA
    engine.LOCAL_PRODUCTION_CAMERA_SCHEMA = CAMERA_SCHEMA
    engine.__file__ = __file__

    def validate_registry(object_registry):
        engine._expect_list(object_registry, 218, "object_registry")
        actual_instances = [
            row.get("instance_id")
            for row in object_registry
            if isinstance(row, dict)
        ]
        if actual_instances != EXPECTED_INSTANCE_IDS:
            raise engine.RuntimeRenderError(
                "object registry instance IDs are not stable 1 through 218",
            )
        stable_ids = []
        for row in object_registry:
            engine._expect_keys(
                row,
                (
                    "object_id",
                    "instance_id",
                    "semantic_id",
                    "material_id",
                    "variant_id",
                ),
                "object registry row",
            )
            if (
                not isinstance(row["object_id"], str)
                or re.fullmatch(
                    r"[a-z0-9]+(?:-[a-z0-9]+)*",
                    row["object_id"],
                )
                is None
                or isinstance(row["semantic_id"], bool)
                or not isinstance(row["semantic_id"], int)
                or not 3 <= row["semantic_id"] < len(engine.SEMANTIC_CLASSES)
                or isinstance(row["material_id"], bool)
                or not isinstance(row["material_id"], int)
                or not 1 <= row["material_id"] <= 255
            ):
                raise engine.RuntimeRenderError(
                    "object registry row is invalid",
                )
            stable_ids.append(row["object_id"])
        if len(set(stable_ids)) != 218:
            raise engine.RuntimeRenderError(
                "object registry stable IDs are not unique",
            )

    engine._validate_object_registry_contract = validate_registry
    return engine


def _validate_request(request, engine):
    _validate_reciprocal_boundary(
        request,
        scene=bpy.context.scene,
        script_path=Path(__file__),
    )
    _prepare_topology_proxies_for_production(bpy.data.objects)
    internal = copy.deepcopy(request)
    internal.pop("environment_module_build_report_sha256")
    internal.pop("reciprocal_route_module_plan_sha256")
    internal.pop("engine_script_sha256")
    internal.pop("role_module_id")
    internal.pop("required_visible_instance_ids")
    internal.pop("role_camera_candidate")
    internal.pop("role_camera_candidate_sha256")
    internal.pop("source_camera_registry_sha256")
    internal.pop("source_production_plan")
    internal.pop("source_production_plan_sha256")
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
            raise RuntimeRenderError(
                "engine identity cannot be read before import",
            )
        engine_sha256 = request_hint.get("engine_script_sha256")
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeRenderError(
            "engine identity cannot be read before import",
        ) from exc
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
        print(f"NANTAI_RECIPROCAL_RENDER_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
