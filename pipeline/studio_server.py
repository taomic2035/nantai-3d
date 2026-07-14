"""Read-only local adapter and static server for Nantai 3D Studio.

This module reports evidence already present below a project root.  It never
starts ingest, registration, reconstruction, rendering, or asset mutation.
Legacy and incomplete files remain visible as untrusted proxy evidence instead
of being upgraded from an engine name or a human-readable convention string.

API contract:

* ``GET /api/project`` -> Studio ``ProjectSnapshot`` schema version 2.
* ``GET /api/runs`` -> ``{"items": [...], "cursor": "..."}``.
* ``GET``/``HEAD`` below approved static roots -> project-relative files.
* every mutating method -> structured HTTP 405; no job is started.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

SNAPSHOT_SCHEMA_VERSION = 2
RUN_LEDGER_SCHEMA_VERSION = 1
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_PLY_HEADER_BYTES = 1024 * 1024

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
STATIC_ROOTS = {"assets", "handoff", "recon", "web"}
ALLOWED_RUN_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' "
    "https://cdn.jsdelivr.net https://sparkjs.dev; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; "
    "connect-src 'self' data: https://cdn.jsdelivr.net https://sparkjs.dev; "
    "worker-src 'self' blob:; frame-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
)


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
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if _is_below(root, resolved) and resolved.is_file():
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
        "units": "unknown",
        "handedness": "unknown",
        "up_axis": "unknown",
        "transform_chain": [],
        "metric_evidence": [],
        "registered_images": 0,
        "total_images": 0,
    }


def _registration_counts(root: Path, sessions: Any) -> tuple[int, int]:
    registration, error = _read_json(root / "recon/registration.json")
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
    units = target_frame.get("units")
    handedness = target_frame.get("handedness")
    transform_chain = contract.get("transform_chain")
    metric_evidence = contract.get("metric_evidence")
    registered, total = _registration_counts(root, manifest.get("sessions"))
    coordinate.update(
        {
            "source_frame": source_id if isinstance(source_id, str) and source_id else "unknown",
            "world_frame": target_id if isinstance(target_id, str) and target_id else "unknown",
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
    root: Path, manifest: dict[str, Any] | None, manifest_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    is_v2 = bool(manifest and manifest.get("schema_version") == 2)
    provenance = manifest.get("provenance") if is_v2 and manifest else None
    if not isinstance(provenance, dict):
        provenance = {}

    requested = provenance.get("requested_reconstruction_engine")
    actual = provenance.get("actual_reconstruction_engine")
    declared_synthetic = provenance.get("synthetic")
    reconstruction: dict[str, Any] = {
        "requested_engine": requested if isinstance(requested, str) else "unknown",
        "actual_engine": actual if isinstance(actual, str) else "unknown",
        "synthetic": declared_synthetic if isinstance(declared_synthetic, bool) else True,
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
    if full_path is None and not is_v2:
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
            _, vertex_count = _ply_header(lod_path)
            valid_lods.append(level)
            lod_counts.append(vertex_count or 0)
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
        reconstruction["evidence_status"] = "invalid-artifact-descriptor"
        reconstruction["integrity_error"] = "invalid-descriptor"
    elif full_path is not None and measured_artifact is not None:
        properties, header_count = _ply_header(full_path)
        reconstruction["artifact"] = measured_artifact
        reconstruction["attributes"] = properties
        reconstruction["sh_degree"] = _sh_degree(properties)
        reconstruction["renderer_capabilities"] = ["dc-color"] if properties else []
        if reconstruction["gaussian_count"] == 0 and header_count is not None:
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
        reconstruction["evidence_status"] = "missing-artifact"

    bounds = manifest.get("bounds") if is_v2 and manifest else None
    if isinstance(bounds, dict):
        reconstruction["bounds"] = bounds
    return reconstruction, stitch


def _asset_snapshot(root: Path) -> dict[str, Any]:
    registry_path = root / "assets/registry.json"
    registry, error = _read_json(registry_path)
    if error is not None or not registry or registry.get("schema_version") != 2:
        return {
            "registered": 0,
            "consumed": 0,
            "blocked": 0,
            "registry_revision": "missing-or-invalid",
            "items": [],
        }
    assets = registry.get("assets")
    if not isinstance(assets, dict):
        assets = {}
    revision = f"sha256:{_sha256_file(registry_path)[:16]}"

    world_path = root / "web/data/manifest.json"
    world, _ = _read_json(world_path)
    rows = world.get("asset_consumption") if isinstance(world, dict) else []
    if not isinstance(rows, list):
        rows = []
    valid_chunk_ids: set[str] = set()
    chunks = world.get("chunks") if isinstance(world, dict) else []
    if isinstance(chunks, list):
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = chunk.get("id")
            chunk_path = _resolve_evidence_path(
                root, chunk.get("ply_file"), relative_to=world_path.parent
            )
            if isinstance(chunk_id, str) and chunk_id and chunk_path is not None:
                valid_chunk_ids.add(chunk_id)

    items: list[dict[str, Any]] = []
    for asset_id in sorted(assets):
        entry = assets[asset_id]
        if not isinstance(entry, dict):
            continue
        reason: str | None = None
        raw_ply = entry.get("ply")
        try:
            payload = _resolve_asset_payload(root / "assets", raw_ply)
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
        validated = reason is None
        version = entry.get("version")
        matching_consumption = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("asset_id") == asset_id
            and row.get("version") == version
            and row.get("sha256") == expected_sha
            and row.get("chunk_id") in valid_chunk_ids
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
        "items": items,
    }


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

    resolved_root = assets_root.resolve(strict=True)
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
    manifest_path = root / "web/data/recon/recon_manifest.json"
    manifest, manifest_error = _read_json(manifest_path)
    sources = _scan_sources(root)
    coordinate = _coordinate_snapshot(root, manifest)
    reconstruction, stitch = _reconstruction_snapshot(root, manifest, manifest_path)
    assets = _asset_snapshot(root)
    runs = _load_runs(root)

    artifact_present = isinstance(reconstruction.get("artifact"), dict)
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
            trust="proxy" if reconstruction["synthetic"] else "verified",
        ),
        "reconstruct": _step(available=artifact_present, trust="proxy"),
        "stitch": _step(
            available=artifact_present and bool(stitch["sessions"] or reconstruction["lod"]),
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
            {"id": runs["items"][-1]["id"], "status": runs["items"][-1]["status"]}
            if runs["items"]
            else None
        ),
        "diagnostics": [],
    }
    if manifest_error:
        snapshot["diagnostics"].append(f"reconstruction-manifest:{manifest_error}")
    elif manifest and manifest.get("schema_version") != 2:
        snapshot["diagnostics"].append("reconstruction-manifest:legacy-schema")
    if reconstruction["evidence_status"] == "missing-artifact":
        snapshot["diagnostics"].append("reconstruction-artifact:missing")
    elif reconstruction["evidence_status"] == "invalid-artifact-descriptor":
        snapshot["diagnostics"].append("reconstruction-artifact:invalid-descriptor")
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
    try:
        approved_root = approved_path.resolve(strict=True)
    except FileNotFoundError:
        return root.joinpath(*parts)
    except (OSError, RuntimeError) as exc:
        raise PathAccessError("unsafe static root") from exc
    if not _is_below(root, approved_root):
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


class StudioRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler configured with a project root by :func:`make_server`."""

    server_version = "NantaiStudio/0.1"
    sys_version = ""
    project_root: Path

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

    def _error(self, status: int, code: str, message: str, *, head_only: bool = False) -> None:
        self._send_json(
            status,
            {
                "schema_version": 1,
                "error": {"code": code, "message": message, "status": status},
            },
            head_only=head_only,
        )

    def _serve(self, *, head_only: bool) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/api/project":
            try:
                snapshot = build_project_snapshot(self.project_root)
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
        if request_path == "/api/runs":
            self._send_json(HTTPStatus.OK, _load_runs(self.project_root), head_only=head_only)
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
        self._method_not_allowed()

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
) -> ThreadingHTTPServer:
    """Create a configured server without starting its event loop."""

    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)

    class ProjectRequestHandler(StudioRequestHandler):
        project_root = root

    server = ThreadingHTTPServer((host, port), ProjectRequestHandler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve Nantai 3D Studio and its read-only local snapshot API."
    )
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8765, help="bind port")
    args = parser.parse_args(argv)
    server = make_server(args.root, host=args.host, port=args.port)
    host, port = server.server_address[:2]
    print(f"Nantai 3D Studio: http://{host}:{port}/web/studio/")
    print("Read-only adapter: GET /api/project and GET /api/runs")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
