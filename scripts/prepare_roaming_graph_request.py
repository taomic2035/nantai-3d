"""Prepare a manifest request for the Blender roaming-graph emitter
(HANDOFF-GLM-009 step 3).

This script reads the exact build's ``reciprocal-route-build-request.json``
and emits a ``roaming-graph-manifest-request.json`` that the Blender
emitter ``scripts/blender/emit_roaming_graph_manifest.py`` consumes.

The room/portal selection is the **first bounded milestone** from
HANDOFF-GLM-009: two real modeled rooms and one declared portal with
two reciprocal directed edges.  The selection is hard-coded (not
inferred from image pixels or filenames):

- Room 1: ``central-courtyard-downhill`` bound to ``courtyard-public-002``.
- Room 2: ``covered-gallery-underpass`` bound to ``covered-timber-gallery-v1``.
- Portal: ``portal-courtyard-gallery-side-passage`` bound to
  ``courtyard-covered-side-passage-001`` (part of the downhill module).

Room centers, portal endpoints and clearance values are declared by the
plan's ``part_layout.center_m`` and module ``recipe`` — they are not
re-measured from the .blend. The Blender emitter measures only the real
``collision_proxy_sha256`` from mesh bytes, so trust remains graph-only.

Usage::

    python -m scripts.prepare_roaming_graph_request \\
        --build-request <exact-build>/reciprocal-route-build-request.json \\
        --blend-path <exact-build>/village-reciprocal-route.blend \\
        --blend-sha b13b4353... \\
        --build-report-sha 3421d3f1... \\
        --emitter-script scripts/blender/emit_roaming_graph_manifest.py \\
        --output <staging>/roaming-graph-manifest-request.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

MANIFEST_SCHEMA = "nantai.synthetic-village.roaming-graph-manifest.v1"

# Hard-coded first-milestone selection (HANDOFF-GLM-009).
# These IDs are stable lowercase-hyphenated and match the plan's
# module_id / bound_object_id / part_id fields exactly.
ROOM_1_MODULE_ID = "central-courtyard-downhill"
ROOM_1_BOUND_OBJECT_ID = "courtyard-public-002"  # recipe.bound_object_id

ROOM_2_MODULE_ID = "covered-gallery-underpass"
ROOM_2_BOUND_OBJECT_ID = "covered-timber-gallery-v1"  # recipe.bound_gallery_object_id

PORTAL_ID = "portal-courtyard-gallery-side-passage"
PORTAL_BOUND_OBJECT_ID = "courtyard-covered-side-passage-001"  # part_id
PORTAL_ROOM_IDS = (ROOM_1_MODULE_ID, ROOM_2_MODULE_ID)


class PrepareRequestError(RuntimeError):
    """Raised when the manifest request cannot be prepared."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrepareRequestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_build_request(path: Path) -> dict:
    if not path.is_file():
        raise PrepareRequestError(f"build request not found: {path}")
    raw = path.read_bytes()
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise PrepareRequestError("build request bytes are absent or unbounded")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrepareRequestError(f"build request is not valid JSON: {exc}") from exc


def _find_module(plan: dict, module_id: str) -> dict:
    for module in plan["modules"]:
        if module["module_id"] == module_id:
            return module
    raise PrepareRequestError(
        f"module {module_id!r} not found in plan")


def _first_part_center(module: dict) -> list[float]:
    parts = sorted(module["parts"], key=lambda p: p["instance_id"])
    first = parts[0]
    return list(first["part_layout"]["center_m"])


def _module_batch8_source_sha(module: dict) -> str:
    sha = module.get("batch8_design_source_sha256")
    if not sha or not isinstance(sha, str):
        raise PrepareRequestError(
            f"module {module['module_id']} has no batch8_design_source_sha256")
    return sha


def _read_recipe_field(module: dict, recipe_key: str, field: str):
    recipe = module["recipe"]
    if recipe_key not in recipe:
        raise PrepareRequestError(
            f"module {module['module_id']} recipe has no {recipe_key!r}")
    sub = recipe[recipe_key]
    if not isinstance(sub, dict) or field not in sub:
        raise PrepareRequestError(
            f"module {module['module_id']} recipe.{recipe_key} has no {field!r}")
    return sub[field]


def _build_request_payload(
    build_request: dict,
    blend_path: Path,
    blend_sha: str,
    build_report_sha: str,
    emitter_script_sha: str,
) -> dict:
    plan = build_request["reciprocal_route_module_plan"]
    room1_module = _find_module(plan, ROOM_1_MODULE_ID)
    room2_module = _find_module(plan, ROOM_2_MODULE_ID)

    room1_center = _first_part_center(room1_module)
    room2_center = _first_part_center(room2_module)

    portal_clear_width = float(_read_recipe_field(
        room1_module, "covered_side_passage", "clear_width_m"))
    portal_clear_height = float(_read_recipe_field(
        room1_module, "covered_side_passage", "clear_height_m"))
    portal_source_sha = _module_batch8_source_sha(room1_module)

    rooms = [
        {
            "room_id": ROOM_1_MODULE_ID,
            "label": "Central Courtyard Downhill",
            "kind": "exterior",
            "center_enu_m": room1_center,
            "bound_object_id": ROOM_1_BOUND_OBJECT_ID,
        },
        {
            "room_id": ROOM_2_MODULE_ID,
            "label": "Covered Gallery Underpass",
            "kind": "transition",
            "center_enu_m": room2_center,
            "bound_object_id": ROOM_2_BOUND_OBJECT_ID,
        },
    ]
    portals = [
        {
            "portal_id": PORTAL_ID,
            "room_ids": list(PORTAL_ROOM_IDS),
            "endpoints_enu_m": [room1_center, room2_center],
            "clear_width_m": portal_clear_width,
            "clear_height_m": portal_clear_height,
            "bound_object_id": PORTAL_BOUND_OBJECT_ID,
            "source_input_sha256": portal_source_sha,
        },
    ]
    build_request_path = build_request.get("_build_request_path") or ""
    return {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_script_sha256": emitter_script_sha,
        "input_blend_path": str(blend_path.resolve()),
        "input_blend_sha256": blend_sha,
        "input_plan_sha256": build_request["reciprocal_route_module_plan_sha256"],
        "input_build_id": build_request["build_id"],
        "input_build_report_sha256": build_report_sha,
        "input_object_registry_sha256": build_request["base_object_registry_sha256"],
        "build_request_path": str(Path(build_request_path).resolve()) if build_request_path else "",
        "rooms": rooms,
        "portals": portals,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a manifest request for the Blender emitter.",
    )
    parser.add_argument(
        "--build-request", required=True, type=Path,
        help="Path to reciprocal-route-build-request.json.",
    )
    parser.add_argument(
        "--blend-path", required=True, type=Path,
        help="Path to village-reciprocal-route.blend.",
    )
    parser.add_argument(
        "--blend-sha", required=True,
        help="SHA-256 of the .blend file.",
    )
    parser.add_argument(
        "--build-report-sha", required=True,
        help="SHA-256 of the reciprocal-route-build-report.json file.",
    )
    parser.add_argument(
        "--emitter-script", required=True, type=Path,
        help="Path to scripts/blender/emit_roaming_graph_manifest.py.",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Output manifest request JSON path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        build_request = _load_build_request(args.build_request)
        build_request["_build_request_path"] = str(args.build_request.resolve())
        if not args.emitter_script.is_file():
            raise PrepareRequestError(
                f"emitter script not found: {args.emitter_script}")
        emitter_sha = _sha256_file(args.emitter_script)
        payload = _build_request_payload(
            build_request,
            args.blend_path,
            args.blend_sha,
            args.build_report_sha,
            emitter_sha,
        )
        # Validate the build_request_path is set correctly.
        if not payload["build_request_path"]:
            payload["build_request_path"] = str(args.build_request.resolve())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"manifest_request_path={args.output}")
        print(f"manifest_script_sha256={emitter_sha}")
        print(f"room_count={len(payload['rooms'])}")
        print(f"portal_count={len(payload['portals'])}")
        return 0
    except PrepareRequestError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
