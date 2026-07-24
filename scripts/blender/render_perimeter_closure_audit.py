"""Fresh clearance and six-layer rendering for one exact-266 closure build."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path

import bpy

CLEARANCE_REQUEST_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-clearance-request.v1"
)
CLEARANCE_REPORT_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-clearance-report.v1"
)
RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-render-frame-request.v1"
)
RENDER_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-report.v4"
)
CAMERA_METADATA_SCHEMA = (
    "nantai.synthetic-village.local-production-camera-metadata.v4"
)
PREFLIGHT_ENGINE_SHA256 = (
    "aa9b1dab8ebb2f42b421ffd6420c6ca82cc096ade9cc7fe50d56b630079908e6"
)
PROFILE_ID = "synthetic-village-perimeter-closure-audit-v1"
BUILD_ADAPTER = "windows-perimeter-closure-audit-v1"
EXPECTED_INSTANCE_IDS = list(range(1, 267))
EXPECTED_CAMERA_IDS = [
    f"camera-audit-overview-{index:03d}" for index in range(1, 17)
]
EXPECTED_AUDIT_CAMERA_IDS = [
    f"audit-closure-{sector}-{direction}"
    for sector in (
        "upstream",
        "northeast",
        "east",
        "southeast",
        "downstream",
        "southwest",
        "west",
        "northwest",
    )
    for direction in ("inward", "outward")
]
LINEAGE_KEYS = {
    "build_id",
    "canonical_roots",
    "geometry_usability",
    "overlay_roots",
    "stage",
    "trust_effect",
}
RENDER_CAPABILITY = {
    "schema_version": (
        "nantai.synthetic-village.perimeter-closure-render-capability.v1"
    ),
    "renderer_id": "blender-4.5.11-six-layer-exact266-v1",
    "instance_id_min": 1,
    "instance_id_max": 266,
    "clearance_probe": "fixed-5x5-first-hit-distance-v1",
    "artifacts": [
        "rgb",
        "depth",
        "normal",
        "instance-mask",
        "semantic-mask",
        "camera-metadata",
    ],
    "target_visibility": "uint16-instance-mask-positive-pixels-v1",
    "seam_visibility": "uint16-instance-mask-positive-pixels-v1",
    "trust_effect": "none-quality-filter-only",
}


class RuntimeAuditError(RuntimeError):
    """Stable failure raised before exact-266 audit evidence publication."""


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


def _is_sha256(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeAuditError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise RuntimeAuditError(
        f"audit request contains non-finite JSON number: {value}"
    )


def _renderer_capability_sha256():
    return hashlib.sha256(_canonical_bytes(RENDER_CAPABILITY)).hexdigest()


def _runtime_mode_args(argv):
    try:
        marker = argv.index("--")
    except ValueError as exc:
        raise RuntimeAuditError("missing Blender argument separator") from exc
    values = argv[marker + 1 :]
    if (
        len(values) != 6
        or values[0] != "--mode"
        or values[1] not in {"preflight", "render"}
        or values[2] != "--request"
        or values[4] != "--output"
    ):
        raise RuntimeAuditError(
            "expected --mode <preflight|render> --request <file> "
            "--output <path>"
        )
    return values[1], Path(values[3]), Path(values[5])


def _parse_scene_lineage(scene):
    raw = scene.get("nv_perimeter_closure_build")
    try:
        lineage = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeAuditError(
            "perimeter-closure scene lineage is absent or invalid"
        ) from exc
    if not isinstance(lineage, dict) or set(lineage) != LINEAGE_KEYS:
        raise RuntimeAuditError(
            "perimeter-closure scene lineage has unknown or missing fields"
        )
    if raw != json.dumps(lineage, separators=(",", ":"), sort_keys=True):
        raise RuntimeAuditError(
            "perimeter-closure scene lineage is not canonical JSON"
        )
    if (
        lineage["canonical_roots"] != 266
        or lineage["overlay_roots"] != 48
        or lineage["geometry_usability"] != "preview-only"
        or lineage["stage"] != "modeled-unverified"
        or lineage["trust_effect"] != "none-quality-filter-only"
        or not _is_sha256(lineage["build_id"])
    ):
        raise RuntimeAuditError(
            "perimeter-closure scene lineage trust contract is invalid"
        )
    return lineage


def _validate_registry(request):
    registry = request.get("object_registry")
    if (
        not isinstance(registry, list)
        or [row.get("instance_id") for row in registry]
        != EXPECTED_INSTANCE_IDS
    ):
        raise RuntimeAuditError("object registry is not exact 1..266")
    if hashlib.sha256(_canonical_bytes(registry)).hexdigest() != request.get(
        "object_registry_sha256"
    ):
        raise RuntimeAuditError("object registry digest is invalid")
    object_ids = [row.get("object_id") for row in registry]
    if (
        len(set(object_ids)) != 266
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) is None
            for value in object_ids
        )
    ):
        raise RuntimeAuditError("object registry stable IDs are invalid")
    return registry


def _validate_audit_plan(request):
    plan = request.get("audit_plan")
    if (
        not isinstance(plan, dict)
        or hashlib.sha256(_canonical_bytes(plan)).hexdigest()
        != request.get("audit_plan_sha256")
        or plan.get("plan_id")
        != "synthetic-village-perimeter-closure-audit-v1"
        or plan.get("camera_count") != 16
        or plan.get("synthetic") is not True
        or plan.get("verification_level") != "L0"
        or plan.get("geometry_usability") != "preview-only"
        or plan.get("stage") != "modeled-unverified"
        or plan.get("metric_alignment") is not False
        or plan.get("real_photo_textures") is not False
        or plan.get("training_use") != "forbidden-as-multiview"
        or plan.get("trust_effect") != "none-quality-filter-only"
    ):
        raise RuntimeAuditError("audit plan identity or trust is invalid")
    cameras = plan.get("cameras")
    if (
        not isinstance(cameras, list)
        or len(cameras) != 16
        or [row.get("camera_id") for row in cameras] != EXPECTED_CAMERA_IDS
        or [row.get("audit_camera_id") for row in cameras]
        != EXPECTED_AUDIT_CAMERA_IDS
    ):
        raise RuntimeAuditError("audit camera tuple is not exact")
    identity_pairs = (
        (request.get("build_id"), plan.get("exact_build_id")),
        (
            request.get("build_report_sha256"),
            plan.get("exact_build_report_sha256"),
        ),
        (request.get("blend_sha256"), plan.get("exact_blend_sha256")),
        (
            request.get("object_registry_sha256"),
            plan.get("object_registry_sha256"),
        ),
        (
            request.get("perimeter_closure_plan_sha256"),
            plan.get("perimeter_closure_plan_sha256"),
        ),
    )
    if any(
        not _is_sha256(left)
        or not _is_sha256(right)
        or left != right
        for left, right in identity_pairs
    ):
        raise RuntimeAuditError("audit exact build identity disagrees")
    return plan


def _validate_common_boundary(request, *, scene, script_path):
    if request.get("profile_id") != PROFILE_ID:
        raise RuntimeAuditError("audit profile ID is invalid")
    if request.get("audit_script_sha256") != _sha256_file(script_path):
        raise RuntimeAuditError(
            "audit script digest does not match executing script"
        )
    if (
        request.get("synthetic") is not True
        or request.get("verification_level") != "L0"
        or request.get("geometry_usability") != "preview-only"
        or request.get("stage") != "modeled-unverified"
        or request.get("trust_effect") != "none-quality-filter-only"
    ):
        raise RuntimeAuditError("audit request provenance is invalid")
    plan = _validate_audit_plan(request)
    _validate_registry(request)
    lineage = _parse_scene_lineage(scene)
    if lineage["build_id"] != request["build_id"]:
        raise RuntimeAuditError(
            "perimeter-closure scene build ID does not match request"
        )
    return plan


def _validate_clearance_boundary(request, *, scene, script_path):
    if request.get("schema_version") != CLEARANCE_REQUEST_SCHEMA:
        raise RuntimeAuditError("clearance request schema is invalid")
    plan = _validate_common_boundary(
        request,
        scene=scene,
        script_path=script_path,
    )
    cameras = plan["cameras"]
    if request.get("selected_camera_ids") != EXPECTED_CAMERA_IDS:
        raise RuntimeAuditError(
            "clearance selected camera set is not exact"
        )
    if not _is_sha256(request.get("preflight_id")):
        raise RuntimeAuditError("clearance preflight ID is invalid")
    unsigned = dict(request)
    unsigned.pop("preflight_id")
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != request[
        "preflight_id"
    ]:
        raise RuntimeAuditError("clearance preflight ID is not canonical")
    if [row["camera_id"] for row in cameras] != request[
        "selected_camera_ids"
    ]:
        raise RuntimeAuditError("clearance plan camera order disagrees")
    return request


def _validate_render_boundary(request, *, scene, script_path):
    if request.get("schema_version") != RENDER_REQUEST_SCHEMA:
        raise RuntimeAuditError("render request schema is invalid")
    if request.get("build_adapter") != BUILD_ADAPTER:
        raise RuntimeAuditError("render build adapter is invalid")
    plan = _validate_common_boundary(
        request,
        scene=scene,
        script_path=script_path,
    )
    if request.get("renderer_capability_sha256") != (
        _renderer_capability_sha256()
    ):
        raise RuntimeAuditError("render capability digest is invalid")
    cameras = plan["cameras"]
    camera = request.get("camera")
    selected = next(
        (
            row
            for row in cameras
            if row.get("audit_camera_id")
            == request.get("audit_camera_id")
        ),
        None,
    )
    if (
        not isinstance(camera, dict)
        or selected != camera
        or request.get("required_target_instance_ids")
        != camera.get("required_target_instance_ids")
        or request.get("required_seam_instance_ids")
        != camera.get("required_seam_instance_ids")
    ):
        raise RuntimeAuditError(
            "render camera or visibility target contract is invalid"
        )
    decision = request.get("clearance_decision")
    if (
        not isinstance(decision, dict)
        or decision.get("passes") is not True
        or decision.get("camera_id") != camera.get("camera_id")
        or decision.get("policy_sha256")
        != request.get("clearance_policy_sha256")
    ):
        raise RuntimeAuditError(
            "render request lacks passing clearance evidence"
        )
    if not all(
        _is_sha256(request.get(key))
        for key in (
            "render_id",
            "engine_script_sha256",
            "preflight_id",
            "clearance_report_sha256",
            "local_quality_policy_sha256",
            "post_render_policy_sha256",
        )
    ):
        raise RuntimeAuditError("render request contains an invalid digest")
    unsigned = dict(request)
    unsigned.pop("render_id")
    if hashlib.sha256(_canonical_bytes(unsigned)).hexdigest() != request[
        "render_id"
    ]:
        raise RuntimeAuditError("render ID is not canonical")
    return request


def _build_clearance_report_payload(request, raw_request, measured):
    evidence = [row[0] for row in measured]
    decisions = [row[1] for row in measured]
    return {
        "schema_version": CLEARANCE_REPORT_SCHEMA,
        "profile_id": PROFILE_ID,
        "preflight_id": request["preflight_id"],
        "request_sha256": hashlib.sha256(raw_request).hexdigest(),
        "audit_plan_sha256": request["audit_plan_sha256"],
        "camera_registry_sha256": request["camera_registry_sha256"],
        "build_id": request["build_id"],
        "build_report_sha256": request["build_report_sha256"],
        "blend_sha256": request["blend_sha256"],
        "perimeter_closure_plan_sha256": request[
            "perimeter_closure_plan_sha256"
        ],
        "blender_executable_sha256": request[
            "blender_executable_sha256"
        ],
        "audit_script_sha256": request["audit_script_sha256"],
        "object_registry_sha256": request["object_registry_sha256"],
        "policy": request["policy"],
        "policy_sha256": request["policy_sha256"],
        "evidence": evidence,
        "decisions": decisions,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_usability": "preview-only",
        "stage": "modeled-unverified",
        "trust_effect": "none-quality-filter-only",
    }


def _to_engine_render_request(request):
    internal = deepcopy(request)
    plan = internal["audit_plan"]
    internal["profile_id"] = "synthetic-village-coverage-180-v1"
    internal["production_plan"] = plan
    internal["production_plan_sha256"] = internal["audit_plan_sha256"]
    internal["elevated_topology_sha256"] = plan[
        "perimeter_closure_plan"
    ]["topology_plan_sha256"]
    internal["renderer_script_sha256"] = internal[
        "audit_script_sha256"
    ]
    internal["quality_policy_sha256"] = internal[
        "local_quality_policy_sha256"
    ]
    internal["build_adapter"] = "windows-textured-v2"
    for key in (
        "audit_plan",
        "audit_plan_sha256",
        "audit_camera_id",
        "audit_script_sha256",
        "engine_script_sha256",
        "perimeter_closure_plan_sha256",
        "clearance_report_sha256",
        "clearance_policy_sha256",
        "clearance_decision",
        "renderer_capability_sha256",
        "local_quality_policy",
        "local_quality_policy_sha256",
        "required_target_instance_ids",
        "required_seam_instance_ids",
        "geometry_usability",
        "stage",
        "trust_effect",
    ):
        internal.pop(key)
    return internal


def _measure_clearance(request, engine, context):
    engine._validate_policy(request["policy"])
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    cameras = {
        row["camera_id"]: row for row in request["audit_plan"]["cameras"]
    }
    return tuple(
        engine._measure_camera(cameras[camera_id], request, depsgraph)
        for camera_id in request["selected_camera_ids"]
    )


def _audit_camera_registry_digest(plan):
    payload = [
        {
            "audit_camera_id": camera["audit_camera_id"],
            "camera_id": camera["camera_id"],
            "module_id": camera["module_id"],
            "direction": camera["direction"],
            "c2w_opencv": camera["c2w_opencv"],
            "required_target_instance_ids": camera[
                "required_target_instance_ids"
            ],
            "required_seam_instance_ids": camera[
                "required_seam_instance_ids"
            ],
        }
        for camera in plan["cameras"]
    ]
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _prepare_render_engine(engine):
    engine.LOCAL_PRODUCTION_REQUEST_SCHEMA = RENDER_REQUEST_SCHEMA
    engine.LOCAL_PRODUCTION_REPORT_SCHEMA = RENDER_REPORT_SCHEMA
    engine.LOCAL_PRODUCTION_CAMERA_SCHEMA = CAMERA_METADATA_SCHEMA
    engine.__file__ = __file__

    def validate_registry(object_registry):
        if (
            not isinstance(object_registry, list)
            or [row.get("instance_id") for row in object_registry]
            != EXPECTED_INSTANCE_IDS
        ):
            raise engine.RuntimeRenderError(
                "object registry instance IDs are not stable 1 through 266"
            )
        object_ids = [row.get("object_id") for row in object_registry]
        if (
            len(set(object_ids)) != 266
            or any(
                not isinstance(object_id, str)
                or re.fullmatch(
                    r"[a-z0-9]+(?:-[a-z0-9]+)*",
                    object_id,
                )
                is None
                for object_id in object_ids
            )
        ):
            raise engine.RuntimeRenderError(
                "object registry stable IDs are not unique"
            )

    def validate_camera_request(request):
        plan = request.get("production_plan")
        cameras = plan.get("cameras") if isinstance(plan, dict) else None
        camera = request.get("camera")
        selected = (
            next(
                (
                    row
                    for row in cameras
                    if isinstance(row, dict)
                    and isinstance(camera, dict)
                    and row.get("camera_id") == camera.get("camera_id")
                ),
                None,
            )
            if isinstance(cameras, list)
            else None
        )
        if (
            not isinstance(plan, dict)
            or plan.get("plan_id")
            != "synthetic-village-perimeter-closure-audit-v1"
            or plan.get("camera_count") != 16
            or plan.get("synthetic") is not True
            or plan.get("verification_level") != "L0"
            or plan.get("geometry_usability") != "preview-only"
            or plan.get("stage") != "modeled-unverified"
            or plan.get("training_use") != "forbidden-as-multiview"
            or plan.get("trust_effect") != "none-quality-filter-only"
            or not isinstance(cameras, list)
            or len(cameras) != 16
            or [row.get("camera_id") for row in cameras]
            != EXPECTED_CAMERA_IDS
            or [row.get("audit_camera_id") for row in cameras]
            != EXPECTED_AUDIT_CAMERA_IDS
            or hashlib.sha256(_canonical_bytes(plan)).hexdigest()
            != request.get("production_plan_sha256")
            or _audit_camera_registry_digest(plan)
            != request.get("camera_registry_sha256")
            or plan.get("perimeter_closure_plan", {}).get(
                "topology_plan_sha256"
            )
            != request.get("elevated_topology_sha256")
        ):
            raise engine.RuntimeRenderError(
                "exact-266 audit production plan is invalid"
            )
        if selected != camera:
            raise engine.RuntimeRenderError(
                "production camera does not match the immutable plan"
            )
        if (
            camera.get("group_id") != "audit-overview"
            or camera.get("audit_only") is not True
            or camera.get("disclosure")
            != "audit-only-modeled-scene-perimeter-closure"
            or camera.get("arc_length_m") is not None
        ):
            raise engine.RuntimeRenderError(
                "audit camera provenance is invalid"
            )
        expected_blender = [
            [
                float(camera["c2w_opencv"][row][column])
                * (-1.0 if column in {1, 2} else 1.0)
                for column in range(4)
            ]
            for row in range(4)
        ]
        requested = request.get("requested_c2w_blender")
        if (
            not isinstance(requested, list)
            or len(requested) != 4
            or any(
                not isinstance(row, list) or len(row) != 4
                for row in requested
            )
            or max(
                abs(float(requested[row][column]) - expected_blender[row][column])
                for row in range(4)
                for column in range(4)
            )
            > 1e-6
        ):
            raise engine.RuntimeRenderError(
                "audit Blender matrix disagrees with OpenCV pose"
            )

    engine._validate_object_registry_contract = validate_registry
    engine._validate_production_camera_request = validate_camera_request
    return engine


def _load_frozen_module(path, expected_sha256, module_name):
    path = Path(path)
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise RuntimeAuditError("frozen engine cannot be read") from exc
    if (
        not _is_sha256(expected_sha256)
        or hashlib.sha256(source).hexdigest() != expected_sha256
    ):
        raise RuntimeAuditError("frozen engine digest does not match request")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeAuditError("frozen engine cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    return module


def _validate_runtime_identity(request, bpy_module):
    executable_path = Path(bpy_module.app.binary_path).absolute()
    if (
        not executable_path.is_file()
        or _sha256_file(executable_path)
        != request.get("blender_executable_sha256")
    ):
        raise RuntimeAuditError(
            "executing Blender binary does not match immutable digest"
        )
    blend_path = Path(bpy_module.data.filepath).absolute()
    if (
        not blend_path.is_file()
        or _sha256_file(blend_path) != request.get("blend_sha256")
    ):
        raise RuntimeAuditError(
            "loaded Blender file does not match immutable digest"
        )
    if (
        bpy_module.app.version_string != "4.5.11 LTS"
        or bpy_module.app.build_hash.decode("ascii") != "4db51e9d1e1e"
    ):
        raise RuntimeAuditError(
            "executing Blender identity is not pinned 4.5.11 LTS"
        )
    scene = bpy_module.context.scene
    if (
        scene.get("nv_synthetic") is not True
        or scene.get("nv_fidelity")
        != "simplified-pbr-not-render-parity"
    ):
        raise RuntimeAuditError("loaded Blender scene provenance is invalid")


def _write_clearance_report(output_path, payload):
    output_path = Path(output_path)
    if output_path.exists():
        raise RuntimeAuditError("clearance report path already exists")
    if not output_path.parent.is_dir():
        raise RuntimeAuditError("clearance report parent is unavailable")
    raw = _canonical_bytes(payload)
    temporary = output_path.with_name(
        f".{output_path.name}.tmp-{hashlib.sha256(raw).hexdigest()[:12]}"
    )
    if temporary.exists():
        raise RuntimeAuditError("clearance temporary path already exists")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _prepare_topology_proxies(objects):
    proxies = [
        obj
        for obj in objects
        if obj.type == "MESH" and obj.get("nv_proxy_topology", False)
    ]
    proxy_ids = [obj.get("nv_stable_id") for obj in proxies]
    if (
        len(proxies) != 6
        or len(set(proxy_ids)) != 6
        or any(
            not isinstance(proxy_id, str) or not proxy_id
            for proxy_id in proxy_ids
        )
        or any(
            obj.get("nv_root") is True
            or obj.get("nv_stage") != "modeled-unverified"
            or obj.get("nv_trust_effect") != "none"
            or obj.get("nv_geometry_usability") != "preview-only"
            or not obj.hide_render
            or obj.pass_index != 0
            for obj in proxies
        )
    ):
        raise RuntimeAuditError(
            "topology proxy mesh identity or visibility is invalid"
        )
    for obj in proxies:
        obj.hide_viewport = True


def main():
    mode, request_path, output_path = _runtime_mode_args(sys.argv)
    try:
        raw = request_path.read_bytes()
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeAuditError("audit request cannot be read") from exc
    if not isinstance(request, dict) or raw != _canonical_bytes(request):
        raise RuntimeAuditError("audit request is not canonical JSON")
    _validate_runtime_identity(request, bpy)
    if mode == "preflight":
        _validate_clearance_boundary(
            request,
            scene=bpy.context.scene,
            script_path=Path(__file__),
        )
        preflight_engine = _load_frozen_module(
            Path(__file__).with_name("preflight_production_cameras.py"),
            PREFLIGHT_ENGINE_SHA256,
            "nantai_exact266_clearance_engine_v1",
        )
        measured = _measure_clearance(
            request,
            preflight_engine,
            bpy.context,
        )
        report = _build_clearance_report_payload(
            request,
            raw,
            measured,
        )
        _write_clearance_report(output_path, report)
        print(
            "NANTAI_PERIMETER_CLOSURE_PREFLIGHT_OK "
            f"preflight_id={request['preflight_id']} cameras={len(measured)}",
            flush=True,
        )
        return
    _validate_render_boundary(
        request,
        scene=bpy.context.scene,
        script_path=Path(__file__),
    )
    engine = _prepare_render_engine(
        _load_frozen_module(
            Path(__file__).with_name("render_synthetic_village.py"),
            request["engine_script_sha256"],
            "nantai_exact266_six_layer_engine_v1",
        )
    )
    internal = _to_engine_render_request(request)
    scene = bpy.context.scene
    previous_build_id = scene.get("nv_build_id")
    scene["nv_build_id"] = request["build_id"]
    try:
        _prepare_topology_proxies(bpy.data.objects)
        engine._validate_request(internal)
        engine._execute_render(internal, output_path)
    except engine.RuntimeRenderError as exc:
        raise RuntimeAuditError(str(exc)) from exc
    finally:
        if previous_build_id is None:
            del scene["nv_build_id"]
        else:
            scene["nv_build_id"] = previous_build_id


if __name__ == "__main__":
    try:
        main()
    except RuntimeAuditError as exc:
        print(f"NANTAI_PERIMETER_CLOSURE_AUDIT_ERROR {exc}", flush=True)
        raise SystemExit(17) from None
