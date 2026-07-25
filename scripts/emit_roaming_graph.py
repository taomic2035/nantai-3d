"""Host-side driver for the roaming-graph v1 producer (HANDOFF-GLM-009).

This script is the orchestrator that runs **after** the Blender-side
emitter ``scripts/blender/emit_roaming_graph_manifest.py`` has produced
a content-addressed ``roaming-graph-manifest.json``.  It:

1. Reads the manifest and re-validates its file SHA against the sidecar
   ``roaming-graph-manifest.sha256``.
2. Re-validates every input SHA in the manifest against the declared
   build request / build report / plan bytes on disk.
3. Builds a ``RoamingGraph`` via
   ``pipeline.synthetic_village.roaming_graph.build_roaming_graph``,
   binding every room and portal to the real ``collision_proxy_sha256``
   measured by the Blender emitter.
4. Serializes the graph to canonical LF JSON and writes it to a
   content-addressed private directory
   ``.nantai-studio/synthetic-village/hybrid-v4-candidates/roaming-graphs/<graph_sha256>/``.
5. Re-opens and revalidates the persisted bytes, recomputes their
   SHA-256, and prints the graph SHA + path for Codex review.

This script does NOT write ``web/data/roaming-graph.json``, Git, or a
Release artifact.  It does NOT write ``accepted:true`` or
``Reviewer: Codex``.

Usage::

    python -m scripts.emit_roaming_graph \\
        --manifest <staging>/roaming-graph-manifest.json \\
        --build-request <exact-build>/reciprocal-route-build-request.json \\
        --graph-id courtyard-gallery-side-passage-v1 \\
        --entry-room-id central-courtyard-downhill
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from pipeline.synthetic_village.roaming_graph import (
    GraphBindings,
    Portal,
    RoamingGraphError,
    Room,
    build_roaming_graph,
    serialize_roaming_graph,
)

DEFAULT_OUTPUT_ROOT = Path(
    ".nantai-studio/synthetic-village/hybrid-v4-candidates/roaming-graphs"
)


class EmitGraphError(RuntimeError):
    """Raised when the host driver cannot produce a graph artifact."""


# --------------------------------------------------------------------------- #
# SHA / JSON helpers.
# --------------------------------------------------------------------------- #


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(payload) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return (text + "\n").encode("utf-8")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EmitGraphError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _is_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


# --------------------------------------------------------------------------- #
# Manifest loading + re-validation.
# --------------------------------------------------------------------------- #


def _load_manifest(manifest_path: Path) -> dict:
    if not manifest_path.is_file():
        raise EmitGraphError(f"manifest not found: {manifest_path}")
    raw = manifest_path.read_bytes()
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise EmitGraphError("manifest bytes are absent or unbounded")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmitGraphError(f"manifest is not valid JSON: {exc}") from exc
    sha_path = manifest_path.parent / "roaming-graph-manifest.sha256"
    if not sha_path.is_file():
        raise EmitGraphError(
            "manifest sidecar .sha256 not found; the Blender emitter must "
            "write it beside the manifest",
        )
    declared_sha = sha_path.read_text(encoding="utf-8").strip()
    if not _is_sha256(declared_sha):
        raise EmitGraphError(f"manifest sidecar SHA is not 64-hex: {declared_sha!r}")
    actual_sha = _sha256_file(manifest_path)
    if actual_sha != declared_sha:
        raise EmitGraphError(
            f"manifest file SHA disagrees with sidecar: "
            f"actual={actual_sha} declared={declared_sha}",
        )
    return manifest


def _revalidate_manifest_inputs(
    manifest: dict,
    build_request_path: Path,
) -> dict:
    """Re-derive every input SHA from the bytes on disk and confirm
    they match the manifest's declared SHAs.  This closes the chain:
    the manifest is only trustworthy if its declared input SHAs match
    the actual build request / build report / plan bytes."""
    if not build_request_path.is_file():
        raise EmitGraphError(f"build request not found: {build_request_path}")
    raw = build_request_path.read_bytes()
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise EmitGraphError("build request bytes are absent or unbounded")
    try:
        build_request = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmitGraphError(f"build request is not valid JSON: {exc}") from exc
    # Re-derive plan SHA from the build request's plan bytes.
    plan = build_request.get("reciprocal_route_module_plan")
    if not isinstance(plan, dict):
        raise EmitGraphError("build request has no plan dict")
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    if plan_sha != manifest["input_plan_sha256"]:
        raise EmitGraphError(
            f"plan SHA disagrees: actual={plan_sha} "
            f"manifest={manifest['input_plan_sha256']}")
    if plan_sha != build_request.get("reciprocal_route_module_plan_sha256"):
        raise EmitGraphError(
            "build request plan SHA disagrees with plan bytes",
        )
    if build_request.get("build_id") != manifest["input_build_id"]:
        raise EmitGraphError(
            "build request build_id disagrees with manifest",
        )
    # Re-validate build report file SHA.
    build_report_path = build_request_path.parent / "reciprocal-route-build-report.json"
    if not build_report_path.is_file():
        raise EmitGraphError(f"build report not found: {build_report_path}")
    report_sha = _sha256_file(build_report_path)
    if report_sha != manifest["input_build_report_sha256"]:
        raise EmitGraphError(
            f"build report file SHA disagrees: actual={report_sha} "
            f"manifest={manifest['input_build_report_sha256']}")
    return build_request


# --------------------------------------------------------------------------- #
# Graph assembly.
# --------------------------------------------------------------------------- #


def _build_rooms(manifest: dict) -> list[Room]:
    rows = manifest.get("rooms")
    if not isinstance(rows, list):
        raise EmitGraphError("manifest rooms must be a list")
    rooms = []
    for index, row in enumerate(rows):
        try:
            rooms.append(Room(
                room_id=row["room_id"],
                label=row["label"],
                kind=row["kind"],
                center_enu_m=tuple(row["center_enu_m"]),
                collision_proxy_sha256=row["collision_proxy_sha256"],
            ))
        except KeyError as exc:
            raise EmitGraphError(
                f"rooms[{index}] missing required field {exc.args[0]!r}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise EmitGraphError(f"rooms[{index}] is invalid: {exc}") from exc
    return rooms


def _build_portals(manifest: dict) -> list[Portal]:
    rows = manifest.get("portals")
    if not isinstance(rows, list):
        raise EmitGraphError("manifest portals must be a list")
    portals = []
    for index, row in enumerate(rows):
        try:
            portals.append(Portal(
                portal_id=row["portal_id"],
                room_ids=tuple(row["room_ids"]),
                endpoints_enu_m=(
                    tuple(row["endpoints_enu_m"][0]),
                    tuple(row["endpoints_enu_m"][1]),
                ),
                clear_width_m=float(row["clear_width_m"]),
                clear_height_m=float(row["clear_height_m"]),
                collision_proxy_sha256=row["collision_proxy_sha256"],
                source_input_sha256=row["source_input_sha256"],
            ))
        except KeyError as exc:
            raise EmitGraphError(
                f"portals[{index}] missing required field {exc.args[0]!r}"
            ) from exc
        except (IndexError, TypeError, ValueError) as exc:
            raise EmitGraphError(f"portals[{index}] is invalid: {exc}") from exc
    return portals


def _build_bindings(manifest: dict) -> GraphBindings:
    return GraphBindings(
        scene_artifact_sha256=manifest["input_blend_sha256"],
        build_report_sha256=manifest["input_build_report_sha256"],
        source_plan_sha256=manifest["reciprocal_route_module_plan_sha256"],
        collision_manifest_sha256=_sha256_file(
            Path(manifest["_manifest_path"])),
    )


def _emit_graph(
    manifest: dict,
    graph_id: str,
    entry_room_id: str,
) -> tuple[str, bytes]:
    rooms = _build_rooms(manifest)
    portals = _build_portals(manifest)
    bindings = _build_bindings(manifest)
    graph = build_roaming_graph(
        graph_id=graph_id,
        entry_room_id=entry_room_id,
        bindings=bindings,
        rooms=rooms,
        portals=portals,
        route_loops=(),
    )
    blob = serialize_roaming_graph(graph).encode("utf-8")
    sha = _sha256_bytes(blob)
    return sha, blob


# --------------------------------------------------------------------------- #
# Persistence + revalidation.
# --------------------------------------------------------------------------- #


def _persist_graph(
    blob: bytes,
    graph_sha: str,
    output_root: Path,
) -> Path:
    out_dir = output_root / graph_sha
    if out_dir.exists():
        # Idempotent re-run: existing dir is fine if its content matches.
        existing = out_dir / "roaming-graph.json"
        if existing.is_file():
            existing_sha = _sha256_file(existing)
            if existing_sha != graph_sha:
                raise EmitGraphError(
                    f"existing graph dir {out_dir} has stale bytes: "
                    f"actual={existing_sha} expected={graph_sha}",
                )
            return existing
        raise EmitGraphError(
            f"graph dir {out_dir} exists but has no roaming-graph.json",
        )
    out_dir.mkdir(parents=True, exist_ok=False)
    out_path = out_dir / "roaming-graph.json"
    out_path.write_bytes(blob)
    # Sidecar SHA for independent verification.
    (out_dir / "roaming-graph.json.sha256").write_text(
        graph_sha + "\n", encoding="utf-8")
    return out_path


def _revalidate_graph(out_path: Path, expected_sha: str) -> None:
    actual_sha = _sha256_file(out_path)
    if actual_sha != expected_sha:
        raise EmitGraphError(
            f"persisted graph SHA disagrees: actual={actual_sha} "
            f"expected={expected_sha}",
        )
    # Re-parse the JSON to confirm it's still valid.
    raw = out_path.read_bytes()
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmitGraphError(f"persisted graph is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a roaming-graph v1 artifact from a Blender manifest.",
    )
    parser.add_argument(
        "--manifest", required=True, type=Path,
        help="Path to roaming-graph-manifest.json produced by the Blender emitter.",
    )
    parser.add_argument(
        "--build-request", required=True, type=Path,
        help="Path to reciprocal-route-build-request.json (the exact build's plan source).",
    )
    parser.add_argument(
        "--graph-id", required=True,
        help="Stable lowercase-hyphenated graph_id (e.g. courtyard-gallery-side-passage-v1).",
    )
    parser.add_argument(
        "--entry-room-id", required=True,
        help="Stable lowercase-hyphenated entry_room_id.",
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT,
        help=f"Private output root (default: {DEFAULT_OUTPUT_ROOT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        manifest = _load_manifest(args.manifest)
        manifest["_manifest_path"] = str(args.manifest.resolve())
        _revalidate_manifest_inputs(manifest, args.build_request)
        graph_sha, graph_blob = _emit_graph(
            manifest, args.graph_id, args.entry_room_id)
        out_path = _persist_graph(graph_blob, graph_sha, args.output_root)
        _revalidate_graph(out_path, graph_sha)
        print(f"graph_sha256={graph_sha}")
        print(f"graph_path={out_path}")
        print(f"graph_size_bytes={len(graph_blob)}")
        print(f"build_id={manifest['input_build_id']}")
        print(f"plan_sha256={manifest['input_plan_sha256']}")
        print(f"build_report_sha256={manifest['input_build_report_sha256']}")
        print(f"blend_sha256={manifest['input_blend_sha256']}")
        print(f"collision_manifest_sha256={_sha256_file(args.manifest)}")
        print(f"room_count={len(manifest['rooms'])}")
        print(f"portal_count={len(manifest['portals'])}")
        return 0
    except (EmitGraphError, RoamingGraphError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
