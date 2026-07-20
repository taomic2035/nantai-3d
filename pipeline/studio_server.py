"""Read-only local adapter and static server for Nantai 3D Studio.

This module reports evidence already present below a project root.  It never
starts ingest, registration, reconstruction, or asset mutation.  Its optional
world-chunk endpoint derives deterministic synthetic PLY bytes in memory and
does not write a project artifact or trust root.  Legacy and incomplete files
remain visible as untrusted proxy evidence instead of being upgraded from an
engine name or a human-readable convention string.

API contract:

* ``GET /api/project`` -> Studio ``ProjectSnapshot`` schema version 2.
* ``GET /api/runs`` -> ``{"items": [...], "cursor": "..."}``.
* ``GET /api/capabilities`` -> explicit fail-closed operation capabilities.
* ``GET /api/world/chunk/{x}/{y}.ply`` -> opt-in, side-effect-free world chunk.
* ``GET /api/world/mesh-chunk/{x}/{y}.json`` -> verified mesh chunk evidence.
* ``GET /api/world/mesh-assets/...`` -> immutable audited GLB/texture bytes.
* ``GET /api/world/material-maps/...`` -> immutable verified PBR map bytes.
* ``GET /web/data/production-camera-plan.json`` -> deterministic synthetic plan.
* ``GET``/``HEAD`` below approved static roots -> project-relative files.
* every mutating method -> structured HTTP 405; no job is started.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import re
import secrets
import time
from datetime import UTC, datetime
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from plyfile import PlyData, PlyParseError
from pydantic import ValidationError

from pipeline.assets import AssetRegistry
from pipeline.gaussian_scene import GaussianScene
from pipeline.mock_layout import DEFAULT_ASSETS
from pipeline.render_chunk_to_ply import render_single_chunk
from pipeline.studio_jobs import JobContractError, JobService, WriterBusyError
from pipeline.studio_ledger import (
    ActiveRunConflictError,
    RequestConflictError,
    RunRecord,
)
from pipeline.synthetic_village.infinite_terrain import TERRAIN_ALGORITHM_ID
from pipeline.synthetic_village.local_textured_preview import (
    LocalTexturedPreviewError,
    canonical_local_textured_preview_manifest_bytes,
    load_local_textured_preview_manifest,
    read_verified_local_textured_preview_glb,
)
from pipeline.synthetic_village.material_bundle import (
    DerivedMaterialBundle,
    MaterialBundleError,
    canonical_material_bundle_bytes,
    load_material_bundle,
    read_verified_material_map,
)
from pipeline.synthetic_village.material_bundle_v2 import (
    H2_PROFILE_ID,
    H3_PROFILE_ID,
    MaterialBundleV2,
    MaterialBundleV2Error,
    load_material_bundle_v2,
    read_verified_material_texture_v2,
)
from pipeline.synthetic_village.mesh_asset_bundle import (
    MeshAssetBundleAny,
    MeshAssetBundleError,
    load_mesh_asset_bundle,
    read_verified_mesh_template_glb,
)
from pipeline.synthetic_village.mesh_asset_bundle_v2 import (
    MeshAssetBundleV2,
    read_verified_mesh_texture,
)
from pipeline.synthetic_village.mesh_asset_bundle_v3 import (
    MeshAssetBundleV3,
    MeshAssetBundleV3Error,
    load_mesh_asset_bundle_v3,
    read_verified_mesh_texture_v3,
    read_verified_mesh_variant_glb,
)
from pipeline.synthetic_village.mesh_chunk import (
    MAX_SAFE_INTEGER,
    MeshChunkError,
    build_mesh_chunk_manifest,
    canonical_mesh_chunk_runtime_bytes,
    project_mesh_chunk_runtime,
    project_mesh_chunk_runtime_v3,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
    canonical_production_plan_bytes,
)

SNAPSHOT_SCHEMA_VERSION = 2
RUN_LEDGER_SCHEMA_VERSION = 1
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_JOB_BODY_BYTES = 64 * 1024
MAX_REJECTED_BODY_DRAIN_BYTES = 2 * 1024 * 1024
REJECTED_BODY_DRAIN_TIMEOUT_S = 0.5
MAX_PLY_HEADER_BYTES = 1024 * 1024

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
STATIC_ROOTS = {"assets", "handoff", "recon", "web"}
EVIDENCE_ROOTS = {"recon", "web"}
ALLOWED_RUN_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}
STUDIO_COMMAND_IDS = ("ingest", "reconstruct", "world", "validate-assets")
READ_ONLY_REASON = "Job execution is not enabled in this Studio milestone."
XYZ_PROPERTIES = frozenset({"x", "y", "z"})
CORE_3DGS_PROPERTIES = frozenset({
    "x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
    "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
})

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' "
    "https://cdn.jsdelivr.net https://sparkjs.dev; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; "
    "connect-src 'self' data: blob: https://cdn.jsdelivr.net https://sparkjs.dev; "
    "worker-src 'self' blob:; frame-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
)


def read_only_capabilities(reason: str = READ_ONLY_REASON) -> dict[str, Any]:
    """Return a fresh capability document; method presence never implies writes."""
    return {
        "schema_version": 1,
        "mode": "read-only",
        "reason": reason,
        "request_token": None,
        "single_writer": True,
        "commands": {
            command: {
                "enabled": False,
                "cancel": False,
                "retry": False,
                "reason": reason,
            }
            for command in STUDIO_COMMAND_IDS
        },
    }


def read_write_capabilities(token: str) -> dict[str, Any]:
    disabled = "This command is outside Studio Milestone B1."
    ready = "Verified local ingest write path is ready."
    return {
        "schema_version": 1,
        "mode": "read-write",
        "reason": ready,
        "request_token": token,
        "single_writer": True,
        "commands": {
            command: {
                "enabled": command == "ingest",
                "cancel": False,
                "retry": False,
                "reason": None if command == "ingest" else disabled,
            }
            for command in STUDIO_COMMAND_IDS
        },
    }


def _run_payload(run: RunRecord) -> dict[str, Any]:
    return {
        "id": run.id,
        "command": run.command,
        "status": run.status,
        "phase": run.phase,
        "parameters": run.parameters,
        "created_at": run.created_utc,
        "updated_at": run.updated_utc,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "artifact_ids": list(run.artifact_ids),
        "cancel_available": False,
        "retry_available": False,
        "adapter_kind": "local",
    }


def _event_payload(event) -> dict[str, Any]:
    return {
        "cursor": event.cursor,
        "run_id": event.run_id,
        "seq": event.seq,
        "phase": event.phase,
        "progress": event.progress,
        "level": event.level,
        "code": event.code,
        "message": event.message,
        "created_at": event.created_utc,
    }


class PathAccessError(ValueError):
    """Raised when a URL cannot safely resolve below an approved static root."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return None, "too-large"
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "invalid-json"
    if not isinstance(value, dict):
        return None, "invalid-shape"
    return value, None


def _is_below(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _is_real_project_subtree(root: Path, subtree: Path) -> bool:
    """Accept a project evidence root only when its root entry is not a symlink."""
    root = root.resolve()
    if subtree.is_symlink():
        return False
    try:
        resolved = subtree.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return resolved == subtree and _is_below(root, resolved) and resolved.is_dir()


def _resolve_local_textured_preview_directory(
    root: Path,
    preview_id: str,
) -> Path | None:
    root = root.resolve()
    boundary = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/local-previews"
    )
    cursor = root
    for component in boundary.relative_to(root).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            return None
        try:
            resolved = cursor.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved != cursor or not resolved.is_dir():
            return None
    directory = boundary / preview_id
    if directory.is_symlink():
        return None
    try:
        resolved_directory = directory.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if (
        resolved_directory != directory
        or not _is_below(boundary, resolved_directory)
        or not resolved_directory.is_dir()
    ):
        return None
    return resolved_directory


def _resolve_mesh_asset_bundle_directory(
    root: Path,
    bundle_id: str,
) -> Path | None:
    root = root.resolve()
    boundary = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles"
    )
    cursor = root
    for component in boundary.relative_to(root).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            return None
        try:
            resolved = cursor.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved != cursor or not resolved.is_dir():
            return None
    directory = boundary / bundle_id
    if directory.is_symlink():
        return None
    try:
        resolved_directory = directory.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if (
        resolved_directory != directory
        or not _is_below(boundary, resolved_directory)
        or not resolved_directory.is_dir()
    ):
        return None
    return resolved_directory


def _resolve_material_bundle_directory(
    root: Path,
    bundle_id: str,
) -> Path | None:
    root = root.resolve()
    boundary = (
        root
        / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles"
    )
    cursor = root
    for component in boundary.relative_to(root).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            return None
        try:
            resolved = cursor.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved != cursor or not resolved.is_dir():
            return None
    directory = boundary / bundle_id
    if directory.is_symlink():
        return None
    try:
        resolved_directory = directory.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if (
        resolved_directory != directory
        or not _is_below(boundary, resolved_directory)
        or not resolved_directory.is_dir()
    ):
        return None
    return resolved_directory


def _resolve_h3_bundle_directory(
    root: Path,
    *,
    kind: str,
    bundle_id: str,
) -> Path | None:
    if kind not in {"material-bundles", "mesh-bundles"}:
        return None
    root = root.resolve()
    boundary = root / ".nantai-studio/h3" / kind
    cursor = root
    for component in boundary.relative_to(root).parts:
        cursor = cursor / component
        if cursor.is_symlink():
            return None
        try:
            resolved = cursor.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved != cursor or not resolved.is_dir():
            return None
    directory = boundary / bundle_id
    if directory.is_symlink():
        return None
    try:
        resolved_directory = directory.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if (
        resolved_directory != directory
        or not _is_below(boundary, resolved_directory)
        or not resolved_directory.is_dir()
    ):
        return None
    return resolved_directory


def _resolve_real_evidence_file(
    root: Path,
    path: Path,
    *,
    approved_root: str,
) -> Path | None:
    """Resolve one evidence file without following its own or parent symlinks."""
    root = root.resolve()
    boundary = root / approved_root
    if not _is_real_project_subtree(root, boundary) or path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if resolved != path or not _is_below(boundary, resolved) or not resolved.is_file():
        return None
    return resolved


def _resolve_evidence_path(root: Path, raw_path: Any, *, relative_to: Path) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        return None
    declared = Path(raw_path)
    candidates: list[Path]
    if declared.is_absolute():
        candidates = [declared]
    else:
        # Reconstruct currently writes full_3dgs relative to project root while
        # LOD entries are relative to the manifest.  Accept either only when it
        # resolves inside the same project.
        candidates = [root / declared, relative_to / declared]
    root = root.resolve()
    for candidate in candidates:
        lexical = candidate.absolute()
        try:
            relative = lexical.relative_to(root)
        except ValueError:
            continue
        if not relative.parts or relative.parts[0] not in EVIDENCE_ROOTS:
            continue
        approved_root = root / relative.parts[0]
        if not _is_real_project_subtree(root, approved_root):
            continue
        try:
            resolved = lexical.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if _is_below(approved_root, resolved) and resolved.is_file():
            return resolved
    return None


def _ply_header(path: Path) -> tuple[list[str], int | None]:
    properties: list[str] = []
    vertex_count: int | None = None
    current_element: str | None = None
    consumed = 0
    try:
        with path.open("rb") as stream:
            first = stream.readline()
            consumed += len(first)
            if first.strip() != b"ply":
                return [], None
            while consumed <= MAX_PLY_HEADER_BYTES:
                raw = stream.readline()
                consumed += len(raw)
                if not raw:
                    return [], None
                line = raw.decode("ascii", errors="replace").strip()
                if line == "end_header":
                    return properties, vertex_count
                parts = line.split()
                if len(parts) == 3 and parts[0] == "element":
                    current_element = parts[1]
                    if current_element == "vertex":
                        try:
                            vertex_count = max(0, int(parts[2]))
                        except ValueError:
                            return [], None
                elif current_element == "vertex" and len(parts) >= 3 and parts[0] == "property":
                    properties.append(parts[-1])
    except OSError:
        return [], None
    return [], None


def _valid_ply_payload(
    path: Path,
    *,
    required_properties: frozenset[str] = XYZ_PROPERTIES,
    gaussian_semantics: bool = False,
    require_3dgs: bool = False,
) -> tuple[bool, list[str], int | None]:
    properties, vertex_count = _ply_header(path)
    valid = False
    if (
        isinstance(vertex_count, int)
        and vertex_count > 0
        and required_properties.issubset(properties)
    ):
        try:
            if gaussian_semantics:
                scene = GaussianScene.load_ply(path, require_3dgs=require_3dgs)
                valid = len(scene) == vertex_count
            else:
                ply = PlyData.read(str(path), mmap="c")
                vertex = ply["vertex"].data
                parsed_properties = set(vertex.dtype.names or ())
                valid = (
                    len(vertex) == vertex_count
                    and required_properties.issubset(parsed_properties)
                )
        except (OSError, KeyError, TypeError, ValueError, PlyParseError):
            valid = False
    return valid, properties, vertex_count


def _sh_degree(properties: list[str]) -> int:
    rest_count = sum(name.startswith("f_rest_") for name in properties)
    if rest_count == 0:
        return 0
    for degree in range(1, 8):
        if rest_count == 3 * ((degree + 1) ** 2 - 1):
            return degree
    return 0


def _artifact(root: Path, path: Path, *, kind: str) -> dict[str, Any]:
    sha256 = _sha256_file(path)
    return {
        "id": f"recon-scene-full-{sha256[:12]}",
        "kind": kind,
        "uri": f"/{path.relative_to(root.resolve()).as_posix()}",
        "sha256": sha256,
        "bytes": path.stat().st_size,
        "created_at": _iso_mtime(path),
        # The working artifact can be rewritten by another pipeline run.  A
        # future freeze endpoint may create immutable snapshots; this server
        # must not claim that property from a normal file.
        "immutable": False,
    }


def _v2_full_artifact_descriptor(manifest: dict[str, Any]) -> dict[str, Any] | None:
    artifacts = manifest.get("artifacts")
    descriptor = artifacts.get("full_3dgs") if isinstance(artifacts, dict) else None
    declared_path = manifest.get("full_3dgs")
    if not isinstance(descriptor, dict) or not isinstance(declared_path, str):
        return None
    sha256 = descriptor.get("sha256")
    byte_count = descriptor.get("bytes")
    if descriptor.get("path") != declared_path:
        return None
    if not isinstance(sha256, str) or len(sha256) != 64:
        return None
    if type(byte_count) is not int or byte_count < 0:
        return None
    return descriptor


def _scan_sources(root: Path) -> dict[str, Any]:
    input_dir = root / "input"
    files: list[dict[str, Any]] = []
    images = videos = 0
    if input_dir.is_dir():
        for path in sorted(candidate for candidate in input_dir.rglob("*") if candidate.is_file()):
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                kind = "image"
                images += 1
            elif suffix in VIDEO_SUFFIXES:
                kind = "video"
                videos += 1
            else:
                continue
            files.append(
                {
                    "name": path.relative_to(input_dir).as_posix(),
                    "kind": kind,
                    "status": "discovered",
                    "bytes": path.stat().st_size,
                }
            )

    photos_dir = root / "photos"
    frames = 0
    if photos_dir.is_dir():
        frames = sum(
            1
            for path in photos_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
    return {
        "images": images,
        "videos": videos,
        "frames": frames,
        # Without an ingest run ledger, zero rejected files would be a claim
        # without evidence.  ``null`` makes that uncertainty explicit.
        "rejected": None,
        "files": files,
        "duplicate_detection": "unknown",
    }


def _unknown_coordinate() -> dict[str, Any]:
    return {
        "source_frame": "unknown",
        "world_frame": "unknown",
        "source_provenance": "unknown",
        "world_provenance": "unknown",
        "contributor_provenance": ["unknown"],
        "units": "unknown",
        "handedness": "unknown",
        "up_axis": "unknown",
        "transform_chain": [],
        "metric_evidence": [],
        "registered_images": 0,
        "total_images": 0,
    }


def _registration_counts(root: Path, sessions: Any) -> tuple[int, int]:
    registration_path = _resolve_real_evidence_file(
        root, root / "recon/registration.json", approved_root="recon"
    )
    registration, error = (
        _read_json(registration_path)
        if registration_path is not None
        else (None, "unsafe-or-missing")
    )
    if error is None and registration and registration.get("schema_version") == 2:
        poses = registration.get("poses")
        registered = len(poses) if isinstance(poses, list) else 0
        registration_sessions = registration.get("sessions")
        if isinstance(registration_sessions, list):
            sessions = registration_sessions
    else:
        registered = 0
    total = 0
    if isinstance(sessions, list):
        for session in sessions:
            if not isinstance(session, dict):
                continue
            images = session.get("images")
            if isinstance(images, list):
                total += len(images)
            else:
                count = session.get("n_images")
                if isinstance(count, int) and count >= 0:
                    total += count
    return registered, total


def _coordinate_snapshot(root: Path, manifest: dict[str, Any] | None) -> dict[str, Any]:
    coordinate = _unknown_coordinate()
    if not manifest or manifest.get("schema_version") != 2:
        return coordinate
    contract = manifest.get("coordinate_contract")
    if not isinstance(contract, dict):
        return coordinate
    pose_frame = contract.get("pose_frame")
    target_frame = contract.get("target_frame")
    if not isinstance(pose_frame, dict) or not isinstance(target_frame, dict):
        return coordinate
    if not _frame_claim_is_coherent(pose_frame) or not _frame_claim_is_coherent(target_frame):
        return coordinate

    axes = target_frame.get("axes")
    source_id = pose_frame.get("frame_id")
    target_id = target_frame.get("frame_id")
    source_provenance = pose_frame.get("provenance")
    world_provenance = target_frame.get("provenance")
    units = target_frame.get("units")
    handedness = target_frame.get("handedness")
    transform_chain = contract.get("transform_chain")
    metric_evidence = contract.get("metric_evidence")
    ancestry = contract.get("ancestry")
    contributor_provenance: list[str] = []
    if isinstance(ancestry, list):
        for ancestor in ancestry:
            source_frame = (
                ancestor.get("source_frame") if isinstance(ancestor, dict) else None
            )
            provenance = (
                source_frame.get("provenance")
                if isinstance(source_frame, dict)
                and _frame_claim_is_coherent(source_frame)
                else "unknown"
            )
            if provenance not in contributor_provenance:
                contributor_provenance.append(provenance)
    if not contributor_provenance:
        contributor_provenance = ["unknown"]
    registered, total = _registration_counts(root, manifest.get("sessions"))
    coordinate.update(
        {
            "source_frame": source_id if isinstance(source_id, str) and source_id else "unknown",
            "world_frame": target_id if isinstance(target_id, str) and target_id else "unknown",
            "source_provenance": source_provenance,
            "world_provenance": world_provenance,
            "contributor_provenance": contributor_provenance,
            "units": units if units in {"meters", "arbitrary", "unknown"} else "unknown",
            "handedness": (
                handedness if handedness in {"right", "left", "unknown"} else "unknown"
            ),
            "up_axis": "z" if axes in {"enu-z-up", "local-z-up"} else "unknown",
            "transform_chain": transform_chain if isinstance(transform_chain, list) else [],
            "metric_evidence": (
                [item for item in metric_evidence if isinstance(item, str) and item]
                if isinstance(metric_evidence, list)
                else []
            ),
            "registered_images": registered,
            "total_images": total,
        }
    )
    return coordinate


def _frame_claim_is_coherent(frame: dict[str, Any]) -> bool:
    """Validate the subset of CoordinateFrame that the Studio reducer consumes."""

    frame_id = frame.get("frame_id")
    handedness = frame.get("handedness")
    axes = frame.get("axes")
    units = frame.get("units")
    metric_status = frame.get("metric_status")
    geo_aligned = frame.get("geo_aligned")
    provenance = frame.get("provenance")
    if not isinstance(frame_id, str) or not frame_id:
        return False
    if handedness != "right":
        return False
    if axes not in {"enu-z-up", "local-z-up", "sfm-arbitrary", "unknown"}:
        return False
    expected_metric_status = {
        "meters": "metric",
        "arbitrary": "arbitrary",
        "unknown": "unknown",
    }.get(units)
    if expected_metric_status is None or metric_status != expected_metric_status:
        return False
    if geo_aligned not in {"aligned", "unaligned", "unknown"}:
        return False
    if provenance not in {"measured", "synthetic", "sfm", "unknown"}:
        return False
    if geo_aligned == "aligned" and not (
        axes == "enu-z-up" and units == "meters" and metric_status == "metric"
    ):
        return False
    if frame_id == "world-enu" and geo_aligned != "aligned":
        return False
    return True


def _reconstruction_snapshot(
    root: Path,
    manifest: dict[str, Any] | None,
    manifest_path: Path,
    *,
    allow_orphan: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    is_v2 = bool(manifest and manifest.get("schema_version") == 2)
    provenance = manifest.get("provenance") if is_v2 and manifest else None
    if not isinstance(provenance, dict):
        provenance = {}

    requested = provenance.get("requested_reconstruction_engine")
    actual = provenance.get("actual_reconstruction_engine")
    declared_synthetic = provenance.get("synthetic")
    declared_geometry_usability = provenance.get("geometry_usability")
    allowed_geometry_usability = {
        "preview-proxy",
        "preview-only",
        "metric-aligned",
        "metric-unaligned",
    }
    reconstruction: dict[str, Any] = {
        "requested_engine": requested if isinstance(requested, str) else "unknown",
        "actual_engine": actual if isinstance(actual, str) else "unknown",
        "synthetic": declared_synthetic if isinstance(declared_synthetic, bool) else True,
        "geometry_usability": (
            declared_geometry_usability
            if declared_geometry_usability in allowed_geometry_usability
            else "preview-only"
        ),
        "attributes": [],
        "sh_degree": 0,
        "renderer_capabilities": [],
        "gaussian_count": 0,
        "lod": [],
        "evidence_status": "missing-manifest",
    }
    stitch: dict[str, Any] = {
        "sessions": 0,
        "overlap_ratio": None,
        "dedup_voxel_m": None,
        "replacement_regions": 0,
        "lod_counts": [],
    }

    full_path: Path | None = None
    if manifest:
        full_path = _resolve_evidence_path(
            root,
            manifest.get("full_3dgs"),
            relative_to=manifest_path.parent,
        )
    if full_path is None and not is_v2 and allow_orphan:
        orphan = root / "recon/scene_full.ply"
        try:
            resolved_orphan = orphan.resolve(strict=True)
        except (OSError, RuntimeError):
            resolved_orphan = None
        if (
            resolved_orphan
            and _is_below(root.resolve(), resolved_orphan)
            and resolved_orphan.is_file()
        ):
            full_path = resolved_orphan

    if is_v2 and manifest:
        count = manifest.get("gaussian_count")
        reconstruction["gaussian_count"] = count if isinstance(count, int) and count >= 0 else 0
        sessions = manifest.get("sessions")
        stitch["sessions"] = len(sessions) if isinstance(sessions, list) else 0
        stitch["overlap_ratio"] = (
            manifest.get("overlap_ratio")
            if isinstance(manifest.get("overlap_ratio"), (int, float))
            else None
        )
        stitch["dedup_voxel_m"] = (
            manifest.get("dedup_voxel_m")
            if isinstance(manifest.get("dedup_voxel_m"), (int, float))
            else None
        )
        replacements = manifest.get("replacement_regions", 0)
        stitch["replacement_regions"] = (
            replacements if isinstance(replacements, int) and replacements >= 0 else 0
        )
    elif manifest:
        reconstruction["evidence_status"] = "legacy-manifest"

    lod_counts: list[int] = []
    valid_lods: list[int] = []
    if is_v2 and manifest and isinstance(manifest.get("lod"), dict):
        sortable: list[tuple[int, Any]] = []
        for key, value in manifest["lod"].items():
            try:
                level = int(key)
            except (TypeError, ValueError):
                continue
            sortable.append((level, value))
        for level, raw_path in sorted(sortable):
            lod_path = _resolve_evidence_path(
                root, raw_path, relative_to=manifest_path.parent
            )
            if lod_path is None:
                continue
            valid_lod, _, vertex_count = _valid_ply_payload(lod_path)
            if not valid_lod:
                continue
            valid_lods.append(level)
            lod_counts.append(vertex_count)
    reconstruction["lod"] = valid_lods
    stitch["lod_counts"] = lod_counts

    descriptor = _v2_full_artifact_descriptor(manifest) if is_v2 and manifest else None
    descriptor_invalid = is_v2 and descriptor is None
    measured_artifact: dict[str, Any] | None = None
    if full_path is not None and not descriptor_invalid:
        measured_artifact = _artifact(
            root, full_path, kind="3dgs-ply" if is_v2 else "legacy-ply"
        )
        if is_v2 and descriptor is not None:
            descriptor_invalid = (
                descriptor["sha256"] != measured_artifact["sha256"]
                or descriptor["bytes"] != measured_artifact["bytes"]
            )

    if descriptor_invalid:
        reconstruction["declared_synthetic"] = declared_synthetic
        reconstruction["synthetic"] = True
        reconstruction["geometry_usability"] = "preview-only"
        reconstruction["evidence_status"] = "invalid-artifact-descriptor"
        reconstruction["integrity_error"] = "invalid-descriptor"
    elif full_path is not None and measured_artifact is not None:
        required = CORE_3DGS_PROPERTIES if is_v2 else XYZ_PROPERTIES
        valid_payload, properties, header_count = _valid_ply_payload(
            full_path,
            required_properties=required,
            gaussian_semantics=is_v2,
            require_3dgs=is_v2,
        )
        if not valid_payload:
            reconstruction["declared_synthetic"] = declared_synthetic
            reconstruction["synthetic"] = True
            reconstruction["geometry_usability"] = "preview-only"
            reconstruction["evidence_status"] = "invalid-artifact-payload"
            reconstruction["integrity_error"] = "invalid-ply"
        else:
            reconstruction["artifact"] = measured_artifact
            reconstruction["attributes"] = properties
            reconstruction["sh_degree"] = _sh_degree(properties)
            reconstruction["renderer_capabilities"] = ["dc-color"]
            if reconstruction["gaussian_count"] == 0:
                reconstruction["gaussian_count"] = header_count
            if is_v2:
                reconstruction["evidence_status"] = "v2-artifact-present"
            elif manifest:
                reconstruction["evidence_status"] = "legacy-manifest"
            else:
                reconstruction["evidence_status"] = "orphan-artifact"
    else:
        # Missing bytes invalidate a non-synthetic declaration for Studio's
        # fail-closed reducer.  Preserve the declaration separately for audit.
        reconstruction["declared_synthetic"] = declared_synthetic
        reconstruction["synthetic"] = True
        reconstruction["geometry_usability"] = "preview-only"
        reconstruction["evidence_status"] = "missing-artifact"

    bounds = manifest.get("bounds") if is_v2 and manifest else None
    if isinstance(bounds, dict):
        reconstruction["bounds"] = bounds
    return reconstruction, stitch


def _asset_snapshot(root: Path) -> dict[str, Any]:
    empty_snapshot = {
        "registered": 0,
        "consumed": 0,
        "blocked": 0,
        "registry_revision": "missing-or-invalid",
        "current_handoff": None,
        "items": [],
    }
    assets_root = root / "assets"
    if not _is_real_project_subtree(root, assets_root):
        return empty_snapshot
    registry_path = _resolve_real_evidence_file(
        root, assets_root / "registry.json", approved_root="assets"
    )
    if registry_path is None:
        return empty_snapshot
    registry, error = _read_json(registry_path)
    if error is not None or not registry or registry.get("schema_version") != 2:
        return empty_snapshot
    assets = registry.get("assets")
    if not isinstance(assets, dict):
        assets = {}
    revision = f"sha256:{_sha256_file(registry_path)[:16]}"

    world_path = root / "web/data/manifest.json"
    resolved_world_path = _resolve_real_evidence_file(
        root, world_path, approved_root="web"
    )
    world, _ = (
        _read_json(resolved_world_path)
        if resolved_world_path is not None
        else (None, "unsafe-or-missing")
    )
    rows = world.get("asset_consumption") if isinstance(world, dict) else []
    if not isinstance(rows, list):
        rows = []
    valid_chunk_point_counts: dict[str, int] = {}
    seen_chunk_ids: set[str] = set()
    duplicate_chunk_ids: set[str] = set()
    chunks = world.get("chunks") if isinstance(world, dict) else []
    if isinstance(chunks, list):
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = chunk.get("id")
            if isinstance(chunk_id, str) and chunk_id:
                if chunk_id in seen_chunk_ids:
                    duplicate_chunk_ids.add(chunk_id)
                seen_chunk_ids.add(chunk_id)
            chunk_path = _resolve_evidence_path(
                root, chunk.get("ply_file"), relative_to=world_path.parent
            )
            valid_payload, _, live_point_count = (
                _valid_ply_payload(chunk_path, gaussian_semantics=True)
                if chunk_path is not None
                else (False, [], None)
            )
            declared_point_count = chunk.get("point_count")
            valid_chunk = (
                valid_payload
                and type(declared_point_count) is int
                and declared_point_count > 0
                and declared_point_count == live_point_count
            )
            if isinstance(chunk_id, str) and chunk_id and valid_chunk:
                valid_chunk_point_counts[chunk_id] = live_point_count
    for chunk_id in duplicate_chunk_ids:
        valid_chunk_point_counts.pop(chunk_id, None)

    reported_points_by_chunk: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        chunk_id = row.get("chunk_id")
        point_count = row.get("point_count")
        if (
            isinstance(chunk_id, str)
            and chunk_id in valid_chunk_point_counts
            and type(point_count) is int
            and point_count > 0
        ):
            reported_points_by_chunk[chunk_id] = (
                reported_points_by_chunk.get(chunk_id, 0) + point_count
            )
    valid_consumption_chunk_ids = {
        chunk_id
        for chunk_id, live_point_count in valid_chunk_point_counts.items()
        if reported_points_by_chunk.get(chunk_id, 0) <= live_point_count
    }

    items: list[dict[str, Any]] = []
    for asset_id in sorted(assets):
        entry = assets[asset_id]
        if not isinstance(entry, dict):
            continue
        reason: str | None = None
        raw_ply = entry.get("ply")
        try:
            payload = _resolve_asset_payload(assets_root, raw_ply)
        except PathAccessError:
            payload = None
            reason = "payload-path-invalid"
        expected_sha = entry.get("sha256")
        actual_sha: str | None = None
        if reason is not None:
            pass
        elif payload is None:
            reason = "payload-missing"
        elif not isinstance(expected_sha, str) or len(expected_sha) != 64:
            reason = "registry-sha256-invalid"
        else:
            actual_sha = _sha256_file(payload)
            if actual_sha != expected_sha:
                reason = "payload-sha256-mismatch"
            elif not _valid_ply_payload(payload, gaussian_semantics=True)[0]:
                reason = "payload-ply-invalid"
        validated = reason is None
        version = entry.get("version")
        matching_consumption = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("asset_id") == asset_id
            and row.get("version") == version
            and row.get("sha256") == expected_sha
            and row.get("renderer") == entry.get("kind")
            and row.get("chunk_id") in valid_consumption_chunk_ids
            and type(row.get("instances")) is int
            and row["instances"] > 0
            and type(row.get("point_count")) is int
            and row["point_count"] > 0
        ]
        consumed = validated and bool(matching_consumption)
        item: dict[str, Any] = {
            "id": asset_id,
            "kind": entry.get("kind", "unknown"),
            "version": version if isinstance(version, int) else 0,
            "origin": entry.get("origin", "unknown"),
            "sha256": expected_sha if isinstance(expected_sha, str) else "unknown",
            "validated": validated,
            "consumed": consumed,
            "consumption": matching_consumption if consumed else [],
        }
        if reason:
            item["reason"] = reason
        items.append(item)
    consumed_count = sum(bool(item["consumed"]) for item in items)
    return {
        "registered": len(items),
        "consumed": consumed_count,
        "blocked": len(items) - consumed_count,
        "registry_revision": revision,
        "current_handoff": _current_asset_handoff(root, items),
        "items": items,
    }


def _current_asset_handoff(
    root: Path, registry_items: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Identify one handoff only from an exact, validated registry identity match."""
    if not registry_items or any(not item.get("validated") for item in registry_items):
        return None
    registry_identity = {
        item["id"]: (item["sha256"], item["kind"]) for item in registry_items
    }
    deliverables_root = root / "handoff/deliverables"
    if not _is_real_project_subtree(root, deliverables_root):
        return None

    matches: list[dict[str, Any]] = []
    for deliverable in sorted(deliverables_root.iterdir(), key=lambda path: path.name):
        if not _is_real_project_subtree(root, deliverable):
            continue
        manifest_path = _resolve_real_evidence_file(
            root, deliverable / "manifest.json", approved_root="handoff"
        )
        if manifest_path is None:
            continue
        manifest, error = _read_json(manifest_path)
        if (
            error is not None
            or not manifest
            or manifest.get("schema_version") != 2
            or manifest.get("handoff_id") != deliverable.name
        ):
            continue
        manifest_items = manifest.get("items")
        if not isinstance(manifest_items, list):
            continue
        manifest_identity: dict[str, tuple[str, str]] = {}
        valid_manifest = True
        for row in manifest_items:
            if not isinstance(row, dict):
                valid_manifest = False
                break
            asset_id = row.get("asset_id")
            sha256 = row.get("sha256")
            kind = row.get("kind")
            if (
                not isinstance(asset_id, str)
                or not asset_id
                or asset_id in manifest_identity
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or not isinstance(kind, str)
                or not kind
            ):
                valid_manifest = False
                break
            manifest_identity[asset_id] = (sha256, kind)
        if not valid_manifest or manifest_identity != registry_identity:
            continue
        match: dict[str, Any] = {
            "id": deliverable.name,
            "item_count": len(manifest_identity),
            "manifest_sha256": _sha256_file(manifest_path),
        }
        generator = manifest.get("generator")
        source_handoff = (
            generator.get("source_handoff") if isinstance(generator, dict) else None
        )
        if isinstance(source_handoff, str) and source_handoff:
            match["source_handoff"] = source_handoff
        preview_path = _resolve_real_evidence_file(
            root,
            deliverable / "previews/contact-sheet.png",
            approved_root="handoff",
        )
        if preview_path is not None:
            match["preview_uri"] = (
                f"/handoff/deliverables/{deliverable.name}/previews/contact-sheet.png"
            )
        matches.append(match)
    return matches[0] if len(matches) == 1 else None


def _world_composition_available(root: Path) -> bool:
    """Accept Compose evidence only when every declared world chunk is live and valid."""

    world_path = root / "web/data/manifest.json"
    resolved_world_path = _resolve_real_evidence_file(
        root, world_path, approved_root="web"
    )
    if resolved_world_path is None:
        return False
    world, error = _read_json(resolved_world_path)
    chunks = world.get("chunks") if error is None and isinstance(world, dict) else None
    if not isinstance(chunks, list) or not chunks:
        return False
    seen_ids: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            return False
        chunk_id = chunk.get("id")
        point_count = chunk.get("point_count")
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen_ids:
            return False
        if type(point_count) is not int or point_count <= 0:
            return False
        seen_ids.add(chunk_id)
        chunk_path = _resolve_evidence_path(
            root, chunk.get("ply_file"), relative_to=world_path.parent
        )
        valid_payload, _, live_point_count = (
            _valid_ply_payload(chunk_path, gaussian_semantics=True)
            if chunk_path is not None
            else (False, [], None)
        )
        if not valid_payload or live_point_count != point_count:
            return False
    return True


def _step(
    *,
    available: bool,
    execution: str = "succeeded",
    preview: str = "ready",
    trust: str = "proxy",
) -> dict[str, str]:
    if not available:
        return {
            "availability": "missing",
            "execution": "idle",
            "freshness": "stale",
            "preview": "unloaded",
            "trust": "untrusted",
        }
    return {
        "availability": "ready",
        "execution": execution,
        "freshness": "current",
        "preview": preview,
        "trust": trust,
    }


def _load_runs(root: Path) -> dict[str, Any]:
    ledger_path = root / ".nantai-studio/runs.json"
    ledger, error = _read_json(ledger_path)
    if error is not None or not ledger or ledger.get("schema_version") != RUN_LEDGER_SCHEMA_VERSION:
        return {
            "items": [],
            "cursor": "empty" if error == "missing" else "invalid",
        }
    raw_items = ledger.get("items")
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("id"), str) or not item["id"]:
                continue
            if item.get("status") not in ALLOWED_RUN_STATUSES:
                continue
            if item.get("adapter_kind") != "local":
                continue
            if item.get("command") not in STUDIO_COMMAND_IDS:
                continue
            items.append(item)
    return {
        "items": items,
        "cursor": f"sha256:{_sha256_file(ledger_path)[:16]}",
    }


def _resolve_asset_payload(assets_root: Path, raw_path: Any) -> Path | None:
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or "\x00" in raw_path
        or "\\" in raw_path
    ):
        raise PathAccessError("invalid asset payload path")
    if any(part in {"", ".", ".."} for part in raw_path.split("/")):
        raise PathAccessError("invalid asset payload path")
    declared = Path(raw_path)
    if declared.is_absolute():
        raise PathAccessError("invalid asset payload path")

    if assets_root.is_symlink():
        raise PathAccessError("asset root must not be a symlink")
    resolved_root = assets_root.resolve(strict=True)
    if resolved_root != assets_root:
        raise PathAccessError("asset root must be a real project subtree")
    try:
        candidate = (resolved_root / declared).resolve(strict=True)
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError) as exc:
        raise PathAccessError("invalid asset payload path") from exc
    if candidate == resolved_root or not _is_below(resolved_root, candidate):
        raise PathAccessError("asset payload escapes assets root")
    if not candidate.is_file():
        return None
    return candidate


def build_project_snapshot(project_root: str | Path) -> dict[str, Any]:
    """Build a Studio schema-v2 snapshot exclusively from on-disk evidence."""

    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    web_root = root / "web"
    manifest_path = web_root / "data/recon/recon_manifest.json"
    resolved_manifest_path = _resolve_real_evidence_file(
        root, manifest_path, approved_root="web"
    )
    if resolved_manifest_path is not None:
        manifest, manifest_error = _read_json(resolved_manifest_path)
    else:
        manifest, manifest_error = (
            None,
            "unsafe-path" if manifest_path.exists() or manifest_path.is_symlink()
            else "missing",
        )
    sources = _scan_sources(root)
    coordinate = _coordinate_snapshot(root, manifest)
    reconstruction, stitch = _reconstruction_snapshot(
        root,
        manifest,
        manifest_path,
        allow_orphan=manifest_error == "missing",
    )
    coordinate_provenance_trusted = (
        coordinate["source_provenance"] in {"measured", "sfm"}
        and coordinate["world_provenance"] == "measured"
        and all(
            provenance in {"measured", "sfm"}
            for provenance in coordinate["contributor_provenance"]
        )
    )
    if (
        not coordinate_provenance_trusted
        and reconstruction["geometry_usability"]
        in {"metric-aligned", "metric-unaligned"}
    ):
        reconstruction["declared_geometry_usability"] = reconstruction[
            "geometry_usability"
        ]
        reconstruction["geometry_usability"] = "preview-only"
    assets = _asset_snapshot(root)
    world_composition_available = _world_composition_available(root)
    runs = _load_runs(root)

    artifact_present = isinstance(reconstruction.get("artifact"), dict)
    metric_geometry = reconstruction["geometry_usability"] in {
        "metric-aligned", "metric-unaligned"
    }
    v2_coordinate = bool(
        manifest
        and manifest.get("schema_version") == 2
        and coordinate["world_frame"] != "unknown"
    )
    all_assets_valid = bool(assets["registered"]) and all(
        item["validated"] for item in assets["items"]
    )
    all_assets_consumed = all_assets_valid and assets["blocked"] == 0
    pipeline = {
        "sources": _step(available=bool(sources["images"] + sources["videos"]), trust="untrusted"),
        "align": _step(
            available=v2_coordinate,
            trust=(
                "untrusted" if not coordinate_provenance_trusted
                else "proxy" if reconstruction["synthetic"]
                else "verified" if metric_geometry
                else "untrusted"
            ),
        ),
        "reconstruct": _step(available=artifact_present, trust="proxy"),
        "stitch": _step(
            available=world_composition_available,
            trust="proxy",
        ),
        "assets": _step(
            available=bool(assets["registered"]),
            trust=(
                "verified" if all_assets_consumed
                else "proxy" if all_assets_valid
                else "untrusted"
            ),
        ),
        "review": _step(
            available=artifact_present,
            execution="idle",
            trust="proxy" if artifact_present else "untrusted",
        ),
    }

    evidence_paths = [
        root / "input",
        root / "photos",
        root / "recon/registration.json",
        manifest_path,
        root / "assets/registry.json",
        root / "web/data/manifest.json",
    ]
    existing = [path for path in evidence_paths if path.exists()]
    newest_evidence = max(existing, key=lambda path: path.stat().st_mtime) if existing else root
    updated_at = _iso_mtime(newest_evidence)
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "project": {
            "id": root.name,
            "name": root.name,
            "updated_at": updated_at,
            "storage": str(root),
        },
        "adapter": {"kind": "local", "connected": True},
        "sources": sources,
        "coordinate": coordinate,
        "reconstruction": reconstruction,
        "stitch": stitch,
        "assets": assets,
        "pipeline": pipeline,
        "active_run": (
            {
                "id": runs["items"][-1]["id"],
                "command": runs["items"][-1]["command"],
                "status": runs["items"][-1]["status"],
            }
            if runs["items"]
            else None
        ),
        "diagnostics": [],
    }
    if manifest_error:
        snapshot["diagnostics"].append(f"reconstruction-manifest:{manifest_error}")
    elif manifest and manifest.get("schema_version") != 2:
        snapshot["diagnostics"].append("reconstruction-manifest:legacy-schema")
    if not coordinate_provenance_trusted:
        snapshot["diagnostics"].append("coordinate-provenance:untrusted")
    if reconstruction["evidence_status"] == "missing-artifact":
        snapshot["diagnostics"].append("reconstruction-artifact:missing")
    elif reconstruction["evidence_status"] == "invalid-artifact-descriptor":
        snapshot["diagnostics"].append("reconstruction-artifact:invalid-descriptor")
    elif reconstruction["evidence_status"] == "invalid-artifact-payload":
        snapshot["diagnostics"].append("reconstruction-artifact:invalid-ply")
    return snapshot


def resolve_static_path(project_root: str | Path, url_path: str) -> Path:
    """Resolve a percent-encoded URL path within approved project subtrees."""

    root = Path(project_root).expanduser().resolve(strict=True)
    try:
        decoded = unquote(url_path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise PathAccessError("invalid URL encoding") from exc
    if "\x00" in decoded or "\\" in decoded:
        raise PathAccessError("invalid path characters")
    pure = PurePosixPath(decoded)
    parts = tuple(part for part in pure.parts if part != "/")
    if not parts or any(part in {".", ".."} or part.startswith(".") for part in parts):
        raise PathAccessError("path is outside approved static roots")
    if parts[0] not in STATIC_ROOTS:
        raise PathAccessError("path is outside approved static roots")
    approved_path = root / parts[0]
    if approved_path.is_symlink():
        raise PathAccessError("approved static root must not be a symlink")
    try:
        approved_root = approved_path.resolve(strict=True)
    except FileNotFoundError:
        return root.joinpath(*parts)
    except (OSError, RuntimeError) as exc:
        raise PathAccessError("unsafe static root") from exc
    if approved_root != approved_path or not _is_below(root, approved_root):
        raise PathAccessError("static root escapes project root")
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        # A non-existent path can still be classified as a normal 404 provided
        # its closest existing parent does not escape through a symlink.
        parent = candidate.parent
        while not parent.exists() and parent != root:
            parent = parent.parent
        try:
            resolved_parent = parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathAccessError("unsafe static path") from exc
        if not _is_below(approved_root, resolved_parent):
            raise PathAccessError("symlink escapes approved static root") from None
        return candidate
    except (OSError, RuntimeError) as exc:
        raise PathAccessError("unsafe static path") from exc
    if not _is_below(approved_root, resolved):
        raise PathAccessError("symlink escapes approved static root")
    return resolved


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    explicit = {
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".ply": "application/octet-stream",
        ".svg": "image/svg+xml",
    }
    if suffix in explicit:
        return explicit[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _read_world_manifest(root: Path) -> dict[str, Any] | None:
    manifest_path = root / "web/data/manifest.json"
    resolved = _resolve_real_evidence_file(
        root,
        manifest_path,
        approved_root="web",
    )
    if resolved is None:
        return None
    manifest, error = _read_json(resolved)
    if error is not None or not isinstance(manifest, dict):
        return None
    return manifest


@lru_cache(maxsize=1)
def _canonical_production_camera_plan_payload() -> bytes:
    """Build the deterministic synthetic production plan without persisting it."""

    return canonical_production_plan_bytes(build_production_camera_plan())


def _valid_on_demand_world_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    """Accept only a complete recipe that the runtime can reproduce faithfully."""

    grid = manifest.get("grid")
    if not isinstance(grid, dict) or type(grid.get("on_demand")) is not bool:
        return None
    if grid.get("url_template") != "/api/world/chunk/{x}/{y}.ply":
        return None
    if type(grid.get("world_seed")) is not int:
        return None
    if grid.get("layout_engine") != "mock":
        return None
    if type(grid.get("uses_assets")) is not bool:
        return None
    if grid.get("terrain_algorithm_id") != TERRAIN_ALGORITHM_ID:
        return None
    return manifest


def _on_demand_world_manifest(root: Path) -> dict[str, Any] | None:
    """Read a valid grid contract that this runtime can safely activate."""

    manifest = _read_world_manifest(root)
    if manifest is None:
        return None
    return _valid_on_demand_world_manifest(manifest)


def _valid_on_demand_mesh_manifest(
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    """Accept only the exact synthetic mesh recipe implemented by this runtime."""

    grid = manifest.get("mesh_grid")
    if (
        isinstance(grid, dict)
        and grid.get("runtime_schema")
        == "nantai.synthetic-village.mesh-chunk-runtime.v3"
    ):
        required_keys = {
            "runtime_schema",
            "on_demand",
            "url_template",
            "asset_url_template",
            "texture_url_template",
            "world_seed",
            "layout_engine",
            "terrain_algorithm_id",
            "source_mesh_asset_bundle_id",
            "mesh_asset_bundle_id",
            "fallback_material_bundle_id",
            "material_bundle_id",
        }
        if set(grid) != required_keys:
            return None
        if type(grid.get("on_demand")) is not bool:
            return None
        if (
            grid.get("url_template")
            != "/api/world/mesh-chunk/{x}/{y}.json"
            or grid.get("asset_url_template")
            != (
                "/api/world/mesh-assets/{bundle_id}/{profile_id}/"
                "{asset_id}/lod{lod}.glb"
            )
            or grid.get("texture_url_template")
            != (
                "/api/world/mesh-textures/{bundle_id}/{profile_id}/"
                "{sha256}.{extension}"
            )
        ):
            return None
        world_seed = grid.get("world_seed")
        if (
            type(world_seed) is not int
            or abs(world_seed) > MAX_SAFE_INTEGER
            or grid.get("layout_engine") != "mock"
            or grid.get("terrain_algorithm_id")
            != TERRAIN_ALGORITHM_ID
        ):
            return None
        for field in (
            "source_mesh_asset_bundle_id",
            "mesh_asset_bundle_id",
            "fallback_material_bundle_id",
            "material_bundle_id",
        ):
            value = grid.get(field)
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                return None
        return manifest
    required_keys = {
        "on_demand",
        "url_template",
        "asset_url_template",
        "world_seed",
        "layout_engine",
        "terrain_algorithm_id",
        "mesh_asset_bundle_id",
        "material_bundle_id",
    }
    if not isinstance(grid, dict) or set(grid) != required_keys:
        return None
    if type(grid.get("on_demand")) is not bool:
        return None
    if grid.get("url_template") != "/api/world/mesh-chunk/{x}/{y}.json":
        return None
    if grid.get("asset_url_template") != (
        "/api/world/mesh-assets/{bundle_id}/{asset_id}/lod{lod}.glb"
    ):
        return None
    world_seed = grid.get("world_seed")
    if (
        type(world_seed) is not int
        or abs(world_seed) > MAX_SAFE_INTEGER
    ):
        return None
    if grid.get("layout_engine") != "mock":
        return None
    if grid.get("terrain_algorithm_id") != TERRAIN_ALGORITHM_ID:
        return None
    for field in ("mesh_asset_bundle_id", "material_bundle_id"):
        value = grid.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            return None
    return manifest


def _on_demand_mesh_manifest(root: Path) -> dict[str, Any] | None:
    manifest = _read_world_manifest(root)
    if manifest is None:
        return None
    return _valid_on_demand_mesh_manifest(manifest)


def _load_active_mesh_asset_bundle(
    root: Path,
    manifest: dict[str, Any],
) -> tuple[Path, MeshAssetBundleAny]:
    grid = manifest["mesh_grid"]
    directory = _resolve_mesh_asset_bundle_directory(
        root,
        grid["mesh_asset_bundle_id"],
    )
    if directory is None:
        raise MeshAssetBundleError("declared mesh asset bundle is unavailable")
    bundle = load_mesh_asset_bundle(directory)
    if (
        bundle.bundle_id != grid["mesh_asset_bundle_id"]
        or bundle.material_bundle_id != grid["material_bundle_id"]
    ):
        raise MeshAssetBundleError(
            "declared mesh asset or material bundle identity disagrees",
        )
    expected_asset_ids = tuple(sorted({
        asset_id
        for group in DEFAULT_ASSETS.values()
        for asset_id in group
    }))
    if bundle.asset_ids != expected_asset_ids:
        raise MeshAssetBundleError(
            "declared mesh asset bundle does not cover the layout asset closure",
        )
    return directory, bundle


def _load_active_material_bundle(
    root: Path,
    manifest: dict[str, Any],
    *,
    mesh_bundle: MeshAssetBundleAny | None = None,
) -> tuple[Path, DerivedMaterialBundle]:
    grid = manifest["mesh_grid"]
    directory = _resolve_material_bundle_directory(
        root,
        grid["material_bundle_id"],
    )
    if directory is None:
        raise MaterialBundleError("declared material bundle is unavailable")
    bundle = load_material_bundle(directory)
    if bundle.bundle_id != grid["material_bundle_id"]:
        raise MaterialBundleError(
            "declared material bundle identity disagrees",
        )
    if mesh_bundle is not None:
        if (
            bundle.bundle_id != mesh_bundle.material_bundle_id
            or hashlib.sha256(
                canonical_material_bundle_bytes(bundle),
            ).hexdigest() != mesh_bundle.material_bundle_manifest_sha256
        ):
            raise MaterialBundleError(
                "declared material bundle manifest digest disagrees",
            )
        records = {record.slot_id: record for record in bundle.records}
        if any(
            records.get(expected.slot_id) is None
            or records[expected.slot_id].source_sha256 != expected.source_sha256
            for expected in mesh_bundle.material_registry
        ):
            raise MaterialBundleError(
                "mesh and material bundle source identities disagree",
            )
    return directory, bundle


def _load_active_mesh_v3_bundles(
    root: Path,
    manifest: dict[str, Any],
) -> tuple[
    Path,
    MeshAssetBundleV2,
    Path,
    MeshAssetBundleV3,
]:
    grid = manifest["mesh_grid"]
    source_directory = _resolve_mesh_asset_bundle_directory(
        root,
        grid["source_mesh_asset_bundle_id"],
    )
    mesh_directory = _resolve_h3_bundle_directory(
        root,
        kind="mesh-bundles",
        bundle_id=grid["mesh_asset_bundle_id"],
    )
    if source_directory is None or mesh_directory is None:
        raise MeshAssetBundleV3Error(
            "declared mesh v3 bundle closure is unavailable",
        )
    source = load_mesh_asset_bundle(source_directory)
    mesh = load_mesh_asset_bundle_v3(mesh_directory)
    if type(source) is not MeshAssetBundleV2:
        raise MeshAssetBundleV3Error(
            "declared mesh v3 source is not v2",
        )
    if (
        source.bundle_id != grid["source_mesh_asset_bundle_id"]
        or mesh.bundle_id != grid["mesh_asset_bundle_id"]
        or mesh.source_v2_bundle_id != source.bundle_id
        or source.material_bundle_id
        != grid["fallback_material_bundle_id"]
        or mesh.fallback_material_bundle_id
        != grid["fallback_material_bundle_id"]
        or mesh.material_bundle_v2_id
        != grid["material_bundle_id"]
    ):
        raise MeshAssetBundleV3Error(
            "declared mesh v3 identities disagree",
        )
    expected_asset_ids = tuple(sorted({
        asset_id
        for group in DEFAULT_ASSETS.values()
        for asset_id in group
    }))
    if (
        source.asset_ids != expected_asset_ids
        or tuple(record.asset_id for record in mesh.records)
        != expected_asset_ids
    ):
        raise MeshAssetBundleV3Error(
            "declared mesh v3 asset closure is incomplete",
        )
    return source_directory, source, mesh_directory, mesh


def _load_active_material_bundle_v2(
    root: Path,
    manifest: dict[str, Any],
    *,
    mesh_bundle: MeshAssetBundleV3,
) -> tuple[Path, MaterialBundleV2]:
    grid = manifest["mesh_grid"]
    directory = _resolve_h3_bundle_directory(
        root,
        kind="material-bundles",
        bundle_id=grid["material_bundle_id"],
    )
    if directory is None:
        raise MaterialBundleV2Error(
            "declared material bundle v2 is unavailable",
        )
    bundle = load_material_bundle_v2(directory)
    if (
        bundle.bundle_id != grid["material_bundle_id"]
        or bundle.fallback_bundle_id
        != grid["fallback_material_bundle_id"]
        or mesh_bundle.material_bundle_v2_id != bundle.bundle_id
        or mesh_bundle.fallback_material_bundle_id
        != bundle.fallback_bundle_id
        or set(bundle.profiles) != {H3_PROFILE_ID, H2_PROFILE_ID}
    ):
        raise MaterialBundleV2Error(
            "declared material bundle v2 identities disagree",
        )
    return directory, bundle


def _is_world_bounds_validation_error(error: ValidationError) -> bool:
    """Identify the mock world's finite WGS84 envelope without hiding other bugs."""

    issues = error.errors(include_url=False)
    return bool(issues) and all(
        tuple(issue.get("loc", ())) in {
            ("geo_origin", "lat"),
            ("geo_origin", "lon"),
        }
        and issue.get("type") in {"greater_than_equal", "less_than_equal"}
        for issue in issues
    )


class StudioRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler configured with a project root by :func:`make_server`."""

    server_version = "NantaiStudio/0.1"
    sys_version = ""
    project_root: Path

    def _canonical_request(self, *, discard_bounded_body: bool = False) -> bool:
        if not getattr(self.server, "write_enabled", False):
            return True
        if self.headers.get("Host") != self.server.canonical_host:
            if discard_bounded_body:
                self._discard_bounded_request_body()
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_host",
                "The request Host does not match the bound loopback service.",
            )
            return False
        return True

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)

    def _send_bytes(
        self,
        status: int,
        payload: bytes,
        *,
        content_type: str,
        cache_control: str,
        head_only: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache_control)
        self._security_headers()
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(payload)

    def _send_json(self, status: int, value: Any, *, head_only: bool = False) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            status,
            payload,
            content_type="application/json; charset=utf-8",
            cache_control="no-store",
            head_only=head_only,
        )

    def _send_not_modified(self, etag: str, *, cache_control: str) -> None:
        self.send_response(HTTPStatus.NOT_MODIFIED)
        self.send_header("Cache-Control", cache_control)
        self.send_header("ETag", etag)
        self._security_headers()
        self.end_headers()

    def _send_immutable_verified(
        self,
        payload: bytes,
        *,
        sha256: str,
        content_type: str,
        head_only: bool,
    ) -> None:
        if hashlib.sha256(payload).hexdigest() != sha256:
            raise ValueError("verified payload digest changed before response")
        etag = f'"sha256:{sha256}"'
        cache_control = "public, max-age=31536000, immutable"
        request_etags = {
            candidate.strip()
            for candidate in self.headers.get(
                "If-None-Match",
                "",
            ).split(",")
            if candidate.strip()
        }
        if "*" in request_etags or etag in request_etags:
            self._send_not_modified(
                etag,
                cache_control=cache_control,
            )
            return
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            content_type=content_type,
            cache_control=cache_control,
            head_only=head_only,
            extra_headers={"ETag": etag},
        )

    def _error(self, status: int, code: str, message: str, *, head_only: bool = False) -> None:
        self._send_json(
            status,
            {
                "schema_version": 1,
                "error": {"code": code, "message": message, "status": status},
            },
            head_only=head_only,
        )

    def _discard_request_body(self, content_length: int, *, byte_budget: int) -> bool:
        """Consume a rejected body within strict byte and wall-clock budgets."""

        remaining = min(content_length, byte_budget)
        complete = content_length <= byte_budget
        deadline = time.monotonic() + REJECTED_BODY_DRAIN_TIMEOUT_S
        previous_timeout = self.connection.gettimeout()
        try:
            while remaining:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    complete = False
                    break
                self.connection.settimeout(timeout)
                try:
                    chunk = self.rfile.read1(min(remaining, 64 * 1024))
                except (TimeoutError, OSError):
                    complete = False
                    break
                if not chunk:
                    complete = False
                    break
                remaining -= len(chunk)
        finally:
            self.connection.settimeout(previous_timeout)
        if remaining or not complete:
            self.close_connection = True
            return False
        return True

    def _discard_bounded_request_body(self) -> None:
        """Drain a declared in-contract body before an early POST rejection."""

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return
        if 0 <= content_length <= MAX_JOB_BODY_BYTES:
            self._discard_request_body(
                content_length,
                byte_budget=MAX_JOB_BODY_BYTES,
            )

    def _reject_post(self, status: int, code: str, message: str) -> None:
        self._discard_bounded_request_body()
        self._error(status, code, message)

    def _serve(self, *, head_only: bool) -> None:
        if not self._canonical_request():
            return
        request_path = urlsplit(self.path).path
        if request_path == "/api/capabilities":
            self._send_json(
                HTTPStatus.OK,
                self.server.capabilities,
                head_only=head_only,
            )
            return
        if request_path == "/api/project":
            try:
                snapshot = build_project_snapshot(self.project_root)
                if getattr(self.server, "write_enabled", False):
                    active = next((
                        run for run in self.server.job_service.ledger.list_runs()
                        if run.status in {"queued", "running"}
                    ), None)
                    snapshot["active_run"] = None if active is None else {
                        "id": active.id,
                        "command": active.command,
                        "status": active.status,
                    }
            except (OSError, ValueError):
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "project_snapshot_failed",
                    "The project snapshot could not be read safely.",
                    head_only=head_only,
                )
                return
            self._send_json(HTTPStatus.OK, snapshot, head_only=head_only)
            return
        if request_path.startswith("/api/runs/") and getattr(
            self.server, "write_enabled", False,
        ):
            run_id = unquote(request_path.removeprefix("/api/runs/"))
            if not run_id or "/" in run_id:
                self._error(HTTPStatus.NOT_FOUND, "run_not_found", "Run not found.")
                return
            try:
                run = self.server.job_service.ledger.get_run(run_id)
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "run_not_found", "Run not found.")
                return
            events = [
                _event_payload(event)
                for event in self.server.job_service.ledger.list_events()
                if event.run_id == run_id
            ]
            self._send_json(
                HTTPStatus.OK,
                {"schema_version": 1, "run": _run_payload(run), "events": events},
                head_only=head_only,
            )
            return
        if request_path == "/api/runs":
            if getattr(self.server, "write_enabled", False):
                try:
                    query = urlsplit(self.path).query
                    if not query:
                        cursor = 0
                    elif re.fullmatch(r"cursor=\d+", query):
                        cursor = int(query.split("=", 1)[1])
                    else:
                        raise ValueError
                    events = self.server.job_service.ledger.list_events(cursor=cursor)
                except ValueError:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_cursor",
                        "The event cursor is invalid.",
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "schema_version": 1,
                        "items": [
                            _run_payload(run)
                            for run in self.server.job_service.ledger.list_runs()
                        ],
                        "events": [_event_payload(event) for event in events],
                        "cursor": events[-1].cursor if events else cursor,
                    },
                    head_only=head_only,
                )
            else:
                self._send_json(
                    HTTPStatus.OK,
                    _load_runs(self.project_root),
                    head_only=head_only,
                )
            return
        if request_path.startswith("/api/local-textured-preview/"):
            match = re.fullmatch(
                r"/api/local-textured-preview/([0-9a-f]{64})/"
                r"(manifest\.json|village-canary\.glb)",
                request_path,
            )
            if match is None or urlsplit(self.path).query:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "local_textured_preview_not_found",
                    "Local textured preview not found.",
                    head_only=head_only,
                )
                return
            preview_id, filename = match.groups()
            directory = _resolve_local_textured_preview_directory(
                self.project_root,
                preview_id,
            )
            if directory is None:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "local_textured_preview_not_found",
                    "Local textured preview not found.",
                    head_only=head_only,
                )
                return
            try:
                manifest = load_local_textured_preview_manifest(
                    directory / "manifest.json",
                )
                if manifest.preview_id != preview_id:
                    raise LocalTexturedPreviewError(
                        "local preview identity does not match its route",
                    )
                glb_payload = read_verified_local_textured_preview_glb(
                    directory / "village-canary.glb",
                    manifest=manifest,
                )
            except LocalTexturedPreviewError:
                self._error(
                    HTTPStatus.CONFLICT,
                    "local_textured_preview_invalid",
                    "Local textured preview evidence is invalid.",
                    head_only=head_only,
                )
                return
            if filename == "manifest.json":
                payload = canonical_local_textured_preview_manifest_bytes(manifest)
                content_type = "application/json; charset=utf-8"
                cache_control = "no-store"
                digest = hashlib.sha256(payload).hexdigest()
            else:
                payload = glb_payload
                content_type = "model/gltf-binary"
                cache_control = "public, max-age=0, must-revalidate"
                digest = manifest.glb_sha256
            etag = f'"sha256:{digest}"'
            request_etags = {
                candidate.strip()
                for candidate in self.headers.get("If-None-Match", "").split(",")
                if candidate.strip()
            }
            if "*" in request_etags or etag in request_etags:
                self._send_not_modified(etag, cache_control=cache_control)
                return
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                content_type=content_type,
                cache_control=cache_control,
                head_only=head_only,
                extra_headers={"ETag": etag},
            )
            return
        if request_path.startswith("/api/world/mesh-chunk/"):
            match = re.fullmatch(
                r"/api/world/mesh-chunk/([^/]+)/([^/]+)\.json",
                request_path,
            )
            query = urlsplit(self.path).query
            if match is None or re.fullmatch(r"lod=[012]", query) is None:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_mesh_chunk_request",
                    "Mesh chunk coordinates must be integers and lod must be 0, 1, or 2.",
                    head_only=head_only,
                )
                return
            segments = tuple(unquote(segment) for segment in match.groups())
            if any(
                re.fullmatch(r"-?(?:0|[1-9]\d*)", segment) is None
                or segment == "-0"
                for segment in segments
            ):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_mesh_chunk_request",
                    "Mesh chunk coordinates must be canonical integers.",
                    head_only=head_only,
                )
                return
            chunk_x, chunk_y = (int(segment) for segment in segments)
            if any(abs(value) > MAX_SAFE_INTEGER for value in (chunk_x, chunk_y)):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_mesh_chunk_request",
                    "Mesh chunk coordinates exceed the safe integer range.",
                    head_only=head_only,
                )
                return
            mesh_manifest = _on_demand_mesh_manifest(self.project_root)
            if mesh_manifest is None:
                self._error(
                    HTTPStatus.CONFLICT,
                    "mesh_on_demand_unavailable",
                    "The world manifest does not opt in to verified mesh chunks.",
                    head_only=head_only,
                )
                return
            is_runtime_v3 = (
                mesh_manifest["mesh_grid"].get("runtime_schema")
                == "nantai.synthetic-village.mesh-chunk-runtime.v3"
            )
            if is_runtime_v3:
                try:
                    (
                        _source_directory,
                        source_bundle,
                        _mesh_directory,
                        mesh_v3_bundle,
                    ) = _load_active_mesh_v3_bundles(
                        self.project_root,
                        mesh_manifest,
                    )
                except MeshAssetBundleError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_v3_invalid",
                        "The declared mesh v3 bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                    return
                try:
                    (
                        _material_directory,
                        material_v2_bundle,
                    ) = _load_active_material_bundle_v2(
                        self.project_root,
                        mesh_manifest,
                        mesh_bundle=mesh_v3_bundle,
                    )
                except MaterialBundleError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "material_bundle_v2_invalid",
                        "The declared material v2 bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                    return
            else:
                try:
                    _directory, bundle = _load_active_mesh_asset_bundle(
                        self.project_root,
                        mesh_manifest,
                    )
                except MeshAssetBundleError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_invalid",
                        "The declared mesh asset bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                    return
                try:
                    (
                        _material_directory,
                        material_bundle,
                    ) = _load_active_material_bundle(
                        self.project_root,
                        mesh_manifest,
                        mesh_bundle=bundle,
                    )
                except MaterialBundleError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "material_bundle_invalid",
                        "The declared material bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                    return
            try:
                chunk = build_mesh_chunk_manifest(
                    chunk_x,
                    chunk_y,
                    world_seed=mesh_manifest["mesh_grid"]["world_seed"],
                    bundle=(
                        source_bundle
                        if is_runtime_v3
                        else bundle
                    ),
                    lod=int(query.removeprefix("lod=")),
                )
                if is_runtime_v3:
                    runtime = project_mesh_chunk_runtime_v3(
                        chunk,
                        bundle=mesh_v3_bundle,
                        material_bundle=material_v2_bundle,
                    )
                else:
                    runtime = project_mesh_chunk_runtime(
                        chunk,
                        bundle=bundle,
                        material_bundle=material_bundle,
                    )
                payload = canonical_mesh_chunk_runtime_bytes(runtime)
            except ValidationError as exc:
                if _is_world_bounds_validation_error(exc):
                    self._error(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        "mesh_world_bounds_exceeded",
                        "The requested mesh chunk is outside the renderable geographic envelope.",
                        head_only=head_only,
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_chunk_render_failed",
                        "The mesh chunk failed internal layout validation.",
                        head_only=head_only,
                    )
                return
            except MeshChunkError:
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "mesh_chunk_render_failed",
                    "The mesh chunk could not be derived from verified evidence.",
                    head_only=head_only,
                )
                return
            digest = hashlib.sha256(payload).hexdigest()
            etag = f'"sha256:{digest}"'
            cache_control = "no-store"
            request_etags = {
                candidate.strip()
                for candidate in self.headers.get("If-None-Match", "").split(",")
                if candidate.strip()
            }
            if "*" in request_etags or etag in request_etags:
                self._send_not_modified(etag, cache_control=cache_control)
                return
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                content_type="application/json; charset=utf-8",
                cache_control=cache_control,
                head_only=head_only,
                extra_headers={"ETag": etag},
            )
            return
        if request_path.startswith("/api/world/mesh-textures/"):
            match = re.fullmatch(
                r"/api/world/mesh-textures/([0-9a-f]{64})/"
                r"(h3-ai-ktx2-4k|h2-png-1k-fallback)/"
                r"([0-9a-f]{64})\.(ktx2|png)",
                request_path,
            )
            if match is None or urlsplit(self.path).query:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "mesh_profile_texture_not_found",
                    "Mesh profile texture not found.",
                    head_only=head_only,
                )
                return
            (
                route_bundle_id,
                profile_id,
                texture_sha256,
                extension,
            ) = match.groups()
            mesh_manifest = _on_demand_mesh_manifest(
                self.project_root,
            )
            if (
                mesh_manifest is None
                or mesh_manifest["mesh_grid"].get("runtime_schema")
                != "nantai.synthetic-village.mesh-chunk-runtime.v3"
            ):
                self._error(
                    HTTPStatus.CONFLICT,
                    "mesh_v3_on_demand_unavailable",
                    "The world manifest does not opt in to mesh runtime v3.",
                    head_only=head_only,
                )
                return
            if (
                route_bundle_id
                != mesh_manifest["mesh_grid"]["mesh_asset_bundle_id"]
            ):
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "mesh_profile_texture_not_found",
                    "Mesh profile texture not found.",
                    head_only=head_only,
                )
                return
            try:
                (
                    _source_directory,
                    _source_bundle,
                    mesh_directory,
                    mesh_v3_bundle,
                ) = _load_active_mesh_v3_bundles(
                    self.project_root,
                    mesh_manifest,
                )
            except MeshAssetBundleError:
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "mesh_asset_bundle_v3_invalid",
                    "The declared mesh v3 bundle is unavailable or invalid.",
                    head_only=head_only,
                )
                return
            try:
                (
                    material_directory,
                    material_v2_bundle,
                ) = _load_active_material_bundle_v2(
                    self.project_root,
                    mesh_manifest,
                    mesh_bundle=mesh_v3_bundle,
                )
                media_type = (
                    "image/ktx2"
                    if extension == "ktx2"
                    else "image/png"
                )
                is_material_texture = any(
                    descriptor.sha256 == texture_sha256
                    and descriptor.media_type == media_type
                    for descriptor in material_v2_bundle.profiles[
                        profile_id
                    ].textures
                )
                if is_material_texture:
                    payload = read_verified_material_texture_v2(
                        material_directory,
                        bundle=material_v2_bundle,
                        profile_id=profile_id,
                        sha256=texture_sha256,
                        media_type=media_type,
                    )
                else:
                    is_mesh_texture = any(
                        binding.sha256 == texture_sha256
                        and binding.media_type == media_type
                        for record in mesh_v3_bundle.records
                        for binding in record.lod["2"].variants[
                            profile_id
                        ].texture_bindings
                    )
                    if not is_mesh_texture:
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "mesh_profile_texture_not_found",
                            "Mesh profile texture not found.",
                            head_only=head_only,
                        )
                        return
                    payload = read_verified_mesh_texture_v3(
                        mesh_directory,
                        bundle=mesh_v3_bundle,
                        sha256=texture_sha256,
                        media_type=media_type,
                    )
                self._send_immutable_verified(
                    payload,
                    sha256=texture_sha256,
                    content_type=media_type,
                    head_only=head_only,
                )
            except MaterialBundleV2Error as exc:
                if "absent" in str(exc) or "profile" in str(exc):
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_profile_texture_not_found",
                        "Mesh profile texture not found.",
                        head_only=head_only,
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "material_bundle_v2_invalid",
                        "The declared material v2 bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
            except MeshAssetBundleV3Error as exc:
                if "absent" in str(exc):
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_profile_texture_not_found",
                        "Mesh profile texture not found.",
                        head_only=head_only,
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_v3_invalid",
                        "The declared mesh v3 bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
            except ValueError:
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "material_bundle_v2_invalid",
                    "The declared material v2 bundle is unavailable or invalid.",
                    head_only=head_only,
                )
            return
        if request_path.startswith("/api/world/material-maps/"):
            match = re.fullmatch(
                r"/api/world/material-maps/([0-9a-f]{64})/"
                r"(material-[a-z0-9]+(?:-[a-z0-9]+)*)/"
                r"(base_color|normal|orm)\.png",
                request_path,
            )
            if match is None or urlsplit(self.path).query:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "material_map_not_found",
                    "Material map not found.",
                    head_only=head_only,
                )
                return
            route_bundle_id, slot_id, role = match.groups()
            mesh_manifest = _on_demand_mesh_manifest(self.project_root)
            if mesh_manifest is None:
                self._error(
                    HTTPStatus.CONFLICT,
                    "mesh_on_demand_unavailable",
                    "The world manifest does not opt in to verified mesh chunks.",
                    head_only=head_only,
                )
                return
            if route_bundle_id != mesh_manifest["mesh_grid"]["material_bundle_id"]:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "material_map_not_found",
                    "Material map not found.",
                    head_only=head_only,
                )
                return
            try:
                directory, material_bundle = _load_active_material_bundle(
                    self.project_root,
                    mesh_manifest,
                )
                payload = read_verified_material_map(
                    directory,
                    bundle=material_bundle,
                    slot_id=slot_id,
                    role=role,
                )
            except MaterialBundleError as exc:
                if "not present" in str(exc) or "role" in str(exc):
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "material_map_not_found",
                        "Material map not found.",
                        head_only=head_only,
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "material_bundle_invalid",
                        "The declared material bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                return
            digest = hashlib.sha256(payload).hexdigest()
            etag = f'"sha256:{digest}"'
            cache_control = "public, max-age=31536000, immutable"
            request_etags = {
                candidate.strip()
                for candidate in self.headers.get("If-None-Match", "").split(",")
                if candidate.strip()
            }
            if "*" in request_etags or etag in request_etags:
                self._send_not_modified(etag, cache_control=cache_control)
                return
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                content_type="image/png",
                cache_control=cache_control,
                head_only=head_only,
                extra_headers={"ETag": etag},
            )
            return
        if request_path.startswith("/api/world/mesh-assets/"):
            profile_match = re.fullmatch(
                r"/api/world/mesh-assets/([0-9a-f]{64})/"
                r"(h3-ai-ktx2-4k|h2-png-1k-fallback)/"
                r"([a-z0-9]+(?:_[a-z0-9]+)*)/lod([012])\.glb",
                request_path,
            )
            if profile_match is not None and not urlsplit(self.path).query:
                (
                    route_bundle_id,
                    profile_id,
                    asset_id,
                    lod_text,
                ) = profile_match.groups()
                mesh_manifest = _on_demand_mesh_manifest(
                    self.project_root,
                )
                if (
                    mesh_manifest is None
                    or mesh_manifest["mesh_grid"].get(
                        "runtime_schema",
                    )
                    != "nantai.synthetic-village.mesh-chunk-runtime.v3"
                ):
                    self._error(
                        HTTPStatus.CONFLICT,
                        "mesh_v3_on_demand_unavailable",
                        "The world manifest does not opt in to mesh runtime v3.",
                        head_only=head_only,
                    )
                    return
                if (
                    route_bundle_id
                    != mesh_manifest["mesh_grid"][
                        "mesh_asset_bundle_id"
                    ]
                ):
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_profile_asset_not_found",
                        "Mesh profile asset not found.",
                        head_only=head_only,
                    )
                    return
                try:
                    (
                        _source_directory,
                        _source_bundle,
                        mesh_directory,
                        mesh_v3_bundle,
                    ) = _load_active_mesh_v3_bundles(
                        self.project_root,
                        mesh_manifest,
                    )
                    (
                        _material_directory,
                        _material_v2_bundle,
                    ) = _load_active_material_bundle_v2(
                        self.project_root,
                        mesh_manifest,
                        mesh_bundle=mesh_v3_bundle,
                    )
                    lod = int(lod_text)
                    if lod == 2:
                        payload = read_verified_mesh_variant_glb(
                            mesh_directory,
                            bundle=mesh_v3_bundle,
                            asset_id=asset_id,
                            profile_id=profile_id,
                        )
                    else:
                        payload = read_verified_mesh_template_glb(
                            mesh_directory,
                            bundle=mesh_v3_bundle,
                            asset_id=asset_id,
                            lod=lod,
                        )
                    record = next(
                        (
                            row
                            for row in mesh_v3_bundle.records
                            if row.asset_id == asset_id
                        ),
                        None,
                    )
                    if record is None:
                        raise MeshAssetBundleV3Error(
                            "mesh v3 asset is absent",
                        )
                    descriptor = record.lod[lod_text]
                    expected_sha256 = (
                        descriptor.variants[
                            profile_id
                        ].glb_sha256
                        if lod == 2
                        else descriptor.glb_sha256
                    )
                    self._send_immutable_verified(
                        payload,
                        sha256=expected_sha256,
                        content_type="model/gltf-binary",
                        head_only=head_only,
                    )
                except MeshAssetBundleError as exc:
                    if (
                        "absent" in str(exc)
                        or "not present" in str(exc)
                        or "LOD" in str(exc)
                    ):
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "mesh_profile_asset_not_found",
                            "Mesh profile asset not found.",
                            head_only=head_only,
                        )
                    else:
                        self._error(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            "mesh_asset_bundle_v3_invalid",
                            "The declared mesh v3 bundle is unavailable or invalid.",
                            head_only=head_only,
                        )
                except MaterialBundleError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "material_bundle_v2_invalid",
                        "The declared material v2 bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                except ValueError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_v3_invalid",
                        "The declared mesh v3 bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                return
            if (
                re.match(
                    r"^/api/world/mesh-assets/[^/]+/"
                    r"(?:h3-ai-ktx2-4k|h2-png-1k-fallback)(?:/|$)",
                    request_path,
                )
                is not None
            ):
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "mesh_profile_asset_not_found",
                    "Mesh profile asset not found.",
                    head_only=head_only,
                )
                return
            is_texture_route = (
                re.match(
                    r"^/api/world/mesh-assets/[^/]*/textures(?:/|$)",
                    request_path,
                )
                is not None
            )
            if is_texture_route:
                texture_match = re.fullmatch(
                    r"/api/world/mesh-assets/([0-9a-f]{64})/"
                    r"textures/([0-9a-f]{64})\.png",
                    request_path,
                )
                if texture_match is None or urlsplit(self.path).query:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_texture_not_found",
                        "Mesh texture not found.",
                        head_only=head_only,
                    )
                    return
                route_bundle_id, texture_sha256 = texture_match.groups()
                mesh_manifest = _on_demand_mesh_manifest(self.project_root)
                if mesh_manifest is None:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "mesh_on_demand_unavailable",
                        "The world manifest does not opt in to verified mesh chunks.",
                        head_only=head_only,
                    )
                    return
                if route_bundle_id != mesh_manifest["mesh_grid"]["mesh_asset_bundle_id"]:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_texture_not_found",
                        "Mesh texture not found.",
                        head_only=head_only,
                    )
                    return
                try:
                    directory, bundle = _load_active_mesh_asset_bundle(
                        self.project_root,
                        mesh_manifest,
                    )
                except MeshAssetBundleError:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_invalid",
                        "The declared mesh asset bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                    return
                if type(bundle) is not MeshAssetBundleV2:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_texture_not_found",
                        "Mesh texture not found.",
                        head_only=head_only,
                    )
                    return
                try:
                    payload = read_verified_mesh_texture(
                        directory,
                        bundle=bundle,
                        sha256=texture_sha256,
                    )
                except MeshAssetBundleError as exc:
                    if "not present" in str(exc):
                        self._error(
                            HTTPStatus.NOT_FOUND,
                            "mesh_texture_not_found",
                            "Mesh texture not found.",
                            head_only=head_only,
                        )
                    else:
                        self._error(
                            HTTPStatus.INTERNAL_SERVER_ERROR,
                            "mesh_asset_bundle_invalid",
                            "The declared mesh asset bundle is unavailable or invalid.",
                            head_only=head_only,
                        )
                    return
                digest = hashlib.sha256(payload).hexdigest()
                if digest != texture_sha256:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_invalid",
                        "The declared mesh asset bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                    return
                etag = f'"sha256:{digest}"'
                cache_control = "public, max-age=31536000, immutable"
                request_etags = {
                    candidate.strip()
                    for candidate in self.headers.get(
                        "If-None-Match",
                        "",
                    ).split(",")
                    if candidate.strip()
                }
                if "*" in request_etags or etag in request_etags:
                    self._send_not_modified(
                        etag,
                        cache_control=cache_control,
                    )
                    return
                self._send_bytes(
                    HTTPStatus.OK,
                    payload,
                    content_type="image/png",
                    cache_control=cache_control,
                    head_only=head_only,
                    extra_headers={"ETag": etag},
                )
                return
            match = re.fullmatch(
                r"/api/world/mesh-assets/([0-9a-f]{64})/"
                r"([a-z0-9]+(?:_[a-z0-9]+)*)/lod([012])\.glb",
                request_path,
            )
            if match is None or urlsplit(self.path).query:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "mesh_asset_not_found",
                    "Mesh asset not found.",
                    head_only=head_only,
                )
                return
            route_bundle_id, asset_id, lod_text = match.groups()
            mesh_manifest = _on_demand_mesh_manifest(self.project_root)
            if mesh_manifest is None:
                self._error(
                    HTTPStatus.CONFLICT,
                    "mesh_on_demand_unavailable",
                    "The world manifest does not opt in to verified mesh chunks.",
                    head_only=head_only,
                )
                return
            if route_bundle_id != mesh_manifest["mesh_grid"]["mesh_asset_bundle_id"]:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "mesh_asset_not_found",
                    "Mesh asset not found.",
                    head_only=head_only,
                )
                return
            try:
                directory, bundle = _load_active_mesh_asset_bundle(
                    self.project_root,
                    mesh_manifest,
                )
                payload = read_verified_mesh_template_glb(
                    directory,
                    bundle=bundle,
                    asset_id=asset_id,
                    lod=int(lod_text),
                )
            except MeshAssetBundleError as exc:
                message = str(exc)
                if "not present" in message or "LOD" in message:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "mesh_asset_not_found",
                        "Mesh asset not found.",
                        head_only=head_only,
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "mesh_asset_bundle_invalid",
                        "The declared mesh asset bundle is unavailable or invalid.",
                        head_only=head_only,
                    )
                return
            digest = hashlib.sha256(payload).hexdigest()
            etag = f'"sha256:{digest}"'
            cache_control = "public, max-age=31536000, immutable"
            request_etags = {
                candidate.strip()
                for candidate in self.headers.get("If-None-Match", "").split(",")
                if candidate.strip()
            }
            if "*" in request_etags or etag in request_etags:
                self._send_not_modified(etag, cache_control=cache_control)
                return
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                content_type="model/gltf-binary",
                cache_control=cache_control,
                head_only=head_only,
                extra_headers={"ETag": etag},
            )
            return
        if request_path.startswith("/api/world/chunk/"):
            match = re.fullmatch(
                r"/api/world/chunk/([^/]+)/([^/]+)\.ply",
                request_path,
            )
            query = urlsplit(self.path).query
            if match is None or (query and re.fullmatch(r"lod=[012]", query) is None):
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_world_chunk_request",
                    "World chunk coordinates must be integers and lod must be 0, 1, or 2.",
                    head_only=head_only,
                )
                return
            try:
                chunk_x, chunk_y = (int(unquote(segment)) for segment in match.groups())
            except ValueError:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_world_chunk_request",
                    "World chunk coordinates must be integers and lod must be 0, 1, or 2.",
                    head_only=head_only,
                )
                return
            world_manifest = _on_demand_world_manifest(self.project_root)
            if world_manifest is None:
                self._error(
                    HTTPStatus.CONFLICT,
                    "world_on_demand_unavailable",
                    "The world manifest does not opt in to deterministic on-demand chunks.",
                    head_only=head_only,
                )
                return
            grid = world_manifest["grid"]
            world_seed = grid["world_seed"]
            lod = int(query.removeprefix("lod=")) if query else None
            registry = None
            if grid["uses_assets"]:
                registry_path = self.project_root / "assets/registry.json"
                if registry_path.is_symlink() or not registry_path.is_file():
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "world_chunk_render_failed",
                        "The declared asset registry is unavailable.",
                        head_only=head_only,
                    )
                    return
                try:
                    registry = AssetRegistry(self.project_root / "assets")
                except (OSError, RuntimeError, ValueError):
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "world_chunk_render_failed",
                        "The declared asset registry could not be read safely.",
                        head_only=head_only,
                    )
                    return
            try:
                payload = render_single_chunk(
                    chunk_x,
                    chunk_y,
                    world_seed=world_seed,
                    registry=registry,
                    lod=lod,
                )
            except ValidationError as exc:
                if _is_world_bounds_validation_error(exc):
                    self._error(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        "world_bounds_exceeded",
                        "The requested world chunk is outside the renderable geographic envelope.",
                        head_only=head_only,
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "world_chunk_render_failed",
                        "The world chunk failed internal layout validation.",
                        head_only=head_only,
                    )
                return
            except ValueError:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_world_chunk_request",
                    "The requested world chunk cannot be rendered from valid coordinates.",
                    head_only=head_only,
                )
                return
            except (ArithmeticError, OSError, RuntimeError):
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "world_chunk_render_failed",
                    "The world chunk could not be derived safely.",
                    head_only=head_only,
                )
                return
            digest = hashlib.sha256(payload).hexdigest()
            etag = f'"sha256:{digest}"'
            cache_control = "public, max-age=0, must-revalidate"
            request_etags = {
                candidate.strip()
                for candidate in self.headers.get("If-None-Match", "").split(",")
                if candidate.strip()
            }
            if "*" in request_etags or etag in request_etags:
                self._send_not_modified(etag, cache_control=cache_control)
                return
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                content_type="application/octet-stream",
                cache_control=cache_control,
                head_only=head_only,
                extra_headers={"ETag": etag},
            )
            return
        if request_path.startswith("/api/"):
            self._error(
                HTTPStatus.NOT_FOUND,
                "api_not_found",
                "Unknown Studio API endpoint.",
                head_only=head_only,
            )
            return
        if request_path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/web/studio/")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            return
        if request_path == "/web/data/production-camera-plan.json":
            try:
                payload = _canonical_production_camera_plan_payload()
            except (ArithmeticError, RuntimeError, ValueError):
                self._error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "production_camera_plan_unavailable",
                    "The production camera plan could not be derived safely.",
                    head_only=head_only,
                )
                return
            digest = hashlib.sha256(payload).hexdigest()
            etag = f'"sha256:{digest}"'
            cache_control = "public, max-age=0, must-revalidate"
            request_etags = {
                candidate.strip()
                for candidate in self.headers.get("If-None-Match", "").split(",")
                if candidate.strip()
            }
            if "*" in request_etags or etag in request_etags:
                self._send_not_modified(etag, cache_control=cache_control)
                return
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                content_type="application/json; charset=utf-8",
                cache_control=cache_control,
                head_only=head_only,
                extra_headers={"ETag": etag},
            )
            return
        if request_path == "/web/data/manifest.json":
            world_manifest = _read_world_manifest(self.project_root)
            if world_manifest is not None:
                runtime_manifest = dict(world_manifest)
                persisted_grid = world_manifest.get("grid")
                runtime_manifest["grid"] = {
                    **(persisted_grid if isinstance(persisted_grid, dict) else {}),
                    "on_demand": (
                        _valid_on_demand_world_manifest(world_manifest) is not None
                    ),
                }
                self._send_json(
                    HTTPStatus.OK,
                    runtime_manifest,
                    head_only=head_only,
                )
                return
        try:
            target = resolve_static_path(self.project_root, request_path)
        except PathAccessError:
            self._error(
                HTTPStatus.FORBIDDEN,
                "path_forbidden",
                "The requested path is outside approved project static roots.",
                head_only=head_only,
            )
            return
        if target.is_dir():
            index_path = f"{request_path.rstrip('/')}/index.html"
            try:
                target = resolve_static_path(self.project_root, index_path)
            except PathAccessError:
                self._error(
                    HTTPStatus.FORBIDDEN,
                    "path_forbidden",
                    "The requested path is outside approved project static roots.",
                    head_only=head_only,
                )
                return
            if not target.is_file():
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "static_not_found",
                    "Static file not found; directory listing is disabled.",
                    head_only=head_only,
                )
                return
        if not target.is_file():
            self._error(
                HTTPStatus.NOT_FOUND,
                "static_not_found",
                "Static file not found.",
                head_only=head_only,
            )
            return
        try:
            payload = target.read_bytes()
        except OSError:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "static_read_failed",
                "Static file could not be read.",
                head_only=head_only,
            )
            return
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            content_type=_content_type(target),
            cache_control="no-cache",
            head_only=head_only,
        )

    def do_GET(self) -> None:  # noqa: N802
        self._serve(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(head_only=True)

    def _method_not_allowed(self) -> None:
        payload = {
            "schema_version": 1,
            "error": {
                "code": "method_not_allowed",
                "message": "This Studio server is read-only; no job was started.",
                "status": HTTPStatus.METHOD_NOT_ALLOWED,
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(
            HTTPStatus.METHOD_NOT_ALLOWED,
            encoded,
            content_type="application/json; charset=utf-8",
            cache_control="no-store",
            extra_headers={"Allow": "GET, HEAD"},
        )

    def do_POST(self) -> None:  # noqa: N802
        if not getattr(self.server, "write_enabled", False):
            self._method_not_allowed()
            return
        if not self._canonical_request(discard_bounded_body=True):
            return
        if urlsplit(self.path).path != "/api/jobs":
            self._reject_post(
                HTTPStatus.NOT_FOUND,
                "api_not_found",
                "Unknown Studio API endpoint.",
            )
            return
        if self.headers.get("Origin") != self.server.canonical_origin:
            self._reject_post(
                HTTPStatus.FORBIDDEN,
                "invalid_origin",
                "The write request Origin is not the bound Studio origin.",
            )
            return
        token = self.headers.get("X-Nantai-Token", "")
        if not hmac.compare_digest(token, self.server.request_token):
            self._reject_post(
                HTTPStatus.FORBIDDEN,
                "invalid_token",
                "The write capability token is invalid.",
            )
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type.lower() != "application/json":
            self._reject_post(
                HTTPStatus.BAD_REQUEST,
                "invalid_content_type",
                "Write requests require application/json.",
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 0:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_content_length",
                "A valid Content-Length is required.",
            )
            return
        if content_length > MAX_JOB_BODY_BYTES:
            self._discard_request_body(
                content_length,
                byte_budget=MAX_REJECTED_BODY_DRAIN_BYTES,
            )
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "body_too_large",
                "The job request body is too large.",
            )
            return
        request_id = self.headers.get("X-Request-ID", "")
        try:
            value = json.loads(self.rfile.read(content_length))
            if (
                not isinstance(value, dict)
                or set(value) != {"command", "parameters"}
                or not isinstance(value["command"], str)
                or not isinstance(value["parameters"], dict)
            ):
                raise ValueError
            result = self.server.job_service.submit(
                command=value["command"],
                parameters=value["parameters"],
                request_id=request_id,
            )
        except (json.JSONDecodeError, UnicodeError, ValueError, JobContractError):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                "The job request is invalid.",
            )
            return
        except RequestConflictError:
            self._error(
                HTTPStatus.CONFLICT,
                "request_conflict",
                "The request ID was already used for a different job.",
            )
            return
        except (ActiveRunConflictError, WriterBusyError):
            self._error(
                HTTPStatus.CONFLICT,
                "writer_busy",
                "Another project writer is active.",
            )
            return
        self._send_json(
            HTTPStatus.ACCEPTED if result.created else HTTPStatus.OK,
            {
                "schema_version": 1,
                "created": result.created,
                "run": _run_payload(result.run),
            },
        )

    def do_PUT(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802
        self._method_not_allowed()


def make_server(
    project_root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    enable_jobs: bool = False,
) -> ThreadingHTTPServer:
    """Create a configured server without starting its event loop."""

    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)

    class ProjectRequestHandler(StudioRequestHandler):
        project_root = root

    server = ThreadingHTTPServer((host, port), ProjectRequestHandler)
    server.daemon_threads = True
    server.write_enabled = False
    server.job_service = None
    server.request_token = None
    server.capabilities = read_only_capabilities()
    bound_host, bound_port = server.server_address[:2]
    server.canonical_host = f"{bound_host}:{bound_port}"
    server.canonical_origin = f"http://{server.canonical_host}"
    if enable_jobs:
        try:
            address = ipaddress.ip_address(bound_host)
            if not address.is_loopback:
                raise JobContractError(
                    "Write mode requires a numeric loopback bind address.",
                )
            service = JobService(root)
            service.initialize()
            readiness = service.durability.self_test()
            if not readiness.ready:
                raise JobContractError(readiness.reason)
            recovery = service.recover_startup()
            if not recovery.ready:
                raise JobContractError(recovery.reason)
        except Exception as exc:
            reason = f"Studio jobs remain read-only: {exc}"
            server.capabilities = read_only_capabilities(reason)
        else:
            token = secrets.token_urlsafe(32)
            server.job_service = service
            server.request_token = token
            server.write_enabled = True
            server.capabilities = read_write_capabilities(token)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve Nantai 3D Studio and its read-only local snapshot API."
    )
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    parser.add_argument(
        "--enable-jobs",
        action="store_true",
        help="enable the B1 ingest job path when startup safety checks pass",
    )
    args = parser.parse_args(argv)
    server = make_server(
        args.root,
        host=args.host,
        port=args.port,
        enable_jobs=args.enable_jobs,
    )
    host, port = server.server_address[:2]
    print(f"Nantai 3D Studio: http://{host}:{port}/web/studio/")
    if server.write_enabled:
        print("B1 ingest jobs enabled on the bound loopback origin.")
    else:
        print(f"Read-only adapter: {server.capabilities['reason']}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if server.job_service is not None:
            server.job_service.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
