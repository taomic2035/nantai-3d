"""Fail-closed Preview release receipts and extracted-tree verification.

This module deliberately uses only the Python standard library so a downloaded
runtime can verify its own Nantai project bytes before optional dependencies are
installed.  Package immutability is separate from scene trust: verification
never promotes synthetic, preview-only or arbitrary-scale geometry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from pipeline.release_archive import (
    canonical_json_bytes,
    deterministic_zip_info,
    safe_posix_member_path,
    stable_regular_file_bytes,
    stable_regular_file_digest,
)

PREVIEW_RELEASE_SCHEMA = "nantai.preview-release.v1"
PREVIEW_LAYOUT = "nantai.preview-runtime.v1"
RELEASE_MANIFEST_NAME = "RELEASE-MANIFEST.json"
CHECKSUMS_NAME = "SHA256SUMS.txt"
INPUT_LOCK_SCHEMA = "nantai.preview-release-input-lock.v1"
PRIVATE_MESH_REASON = "private-mesh-bundles-not-in-preview2"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+-preview\.[0-9]+$")
_RUNTIME_MUTABLE_ROOTS = frozenset({
    ".venv",
    "nantai_infinite_village.egg-info",
})
_PREVIEW_SCENE_TRUST = {
    "synthetic": True,
    "geometry_usability": "preview-only",
    "units": "arbitrary",
    "alignment_status": "unaligned",
    "real_photo_textures": False,
    "trust_effect": "none",
}


class ReleaseVerificationError(ValueError):
    """Raised when a release receipt or protected byte fails verification."""


@dataclass(frozen=True)
class ReleaseVerification:
    valid: bool
    version: str
    source_commit: str
    package_content_id: str
    artifact_count: int
    total_bytes: int
    scene_trust_effect: str
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseBuild:
    archive_path: Path
    archive_sha256: str
    package_content_id: str
    artifact_count: int
    total_bytes: int
    asset_count: int
    world_chunk_count: int
    gaussian_count: int


def sha256_file(path: Path) -> str:
    return stable_regular_file_digest(path).sha256


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validated_artifacts(
    artifacts: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in artifacts:
        if not isinstance(raw, Mapping):
            raise ValueError("release artifact must be an object")
        if set(raw) != {"path", "role", "bytes", "sha256"}:
            raise ValueError("release artifact fields are not canonical")
        path = safe_posix_member_path(raw["path"]).as_posix()
        if path in seen:
            raise ValueError(f"duplicate release artifact path: {path}")
        seen.add(path)
        role = raw["role"]
        byte_length = raw["bytes"]
        sha256 = raw["sha256"]
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"release artifact role is invalid: {path}")
        if isinstance(byte_length, bool) or not isinstance(byte_length, int) or byte_length < 0:
            raise ValueError(f"release artifact bytes are invalid: {path}")
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ValueError(f"release artifact SHA-256 is invalid: {path}")
        validated.append(
            {
                "path": path,
                "role": role,
                "bytes": byte_length,
                "sha256": sha256,
            }
        )
    return sorted(validated, key=lambda item: item["path"])


def _validated_protected_roots(values: Iterable[str]) -> list[str]:
    roots = sorted({safe_posix_member_path(value).as_posix() for value in values})
    if not roots:
        raise ValueError("release receipt requires at least one protected root")
    for index, root in enumerate(roots):
        for other in roots[index + 1 :]:
            if other.startswith(f"{root}/"):
                raise ValueError(f"overlapping protected roots: {root}, {other}")
    return roots


def _validated_entrypoints(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError("release receipt requires entrypoints")
    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("release entrypoint key is invalid")
        if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
            raise ValueError(f"release entrypoint is invalid: {key}")
        result[key] = value
    return dict(sorted(result.items()))


def _validated_exclusions(values: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for raw in values:
        if not isinstance(raw, str) or not raw:
            raise ValueError("release exclusion must be a non-empty string")
        canonical = safe_posix_member_path(raw[:-1] if raw.endswith("/") else raw).as_posix()
        result.add(f"{canonical}/" if raw.endswith("/") else canonical)
    return sorted(result)


def _validated_scene_trust(scene_trust: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(scene_trust, Mapping):
        raise ValueError("release scene trust must be an object")
    candidate = dict(scene_trust)
    if candidate != _PREVIEW_SCENE_TRUST:
        raise ValueError("Preview release scene trust would be promoted or is incomplete")
    return candidate


def build_receipt(
    *,
    version: str,
    source_commit: str,
    artifacts: Iterable[Mapping[str, Any]],
    protected_roots: Iterable[str],
    entrypoints: Mapping[str, str],
    exclusions: Iterable[str],
    scene_trust: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one canonical content-addressed Preview receipt."""
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ValueError("Preview release version is invalid")
    if not isinstance(source_commit, str) or not _COMMIT_RE.fullmatch(source_commit):
        raise ValueError("Preview release source commit is invalid")

    receipt: dict[str, Any] = {
        "schema": PREVIEW_RELEASE_SCHEMA,
        "version": version,
        "source": {"git_commit": source_commit, "tag": version},
        "package": {
            "layout": PREVIEW_LAYOUT,
            "immutable": True,
            "content_id": None,
        },
        "artifacts": _validated_artifacts(artifacts),
        "protected_roots": _validated_protected_roots(protected_roots),
        "entrypoints": _validated_entrypoints(entrypoints),
        "exclusions": _validated_exclusions(exclusions),
        "scene_trust": _validated_scene_trust(scene_trust),
    }
    receipt["package"]["content_id"] = hashlib.sha256(
        canonical_json_bytes(receipt)
    ).hexdigest()
    return receipt


def _validated_receipt_from_bytes(payload: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"release receipt is invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ReleaseVerificationError("release receipt root must be an object")
    try:
        source = parsed["source"]
        rebuilt = build_receipt(
            version=parsed["version"],
            source_commit=source["git_commit"],
            artifacts=parsed["artifacts"],
            protected_roots=parsed["protected_roots"],
            entrypoints=parsed["entrypoints"],
            exclusions=parsed["exclusions"],
            scene_trust=parsed["scene_trust"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseVerificationError(f"release receipt contract is invalid: {exc}") from exc
    if parsed != rebuilt:
        raise ReleaseVerificationError("release receipt content ID or canonical fields mismatch")
    if payload != canonical_json_bytes(parsed):
        raise ReleaseVerificationError("release receipt bytes are not canonical")
    return parsed


def _path_has_symlink(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _protected_files(root: Path, protected_root: str) -> set[str]:
    relative_root = safe_posix_member_path(protected_root)
    start = root.joinpath(*relative_root.parts)
    if not start.exists():
        return set()
    if _path_has_symlink(root, relative_root):
        raise ReleaseVerificationError(f"symlink protected root is forbidden: {protected_root}")
    if not start.is_dir():
        raise ReleaseVerificationError(f"protected root is not a directory: {protected_root}")

    files: set[str] = set()
    for current, directories, names in os.walk(start, followlinks=False):
        current_path = Path(current)
        for directory in tuple(directories):
            candidate = current_path / directory
            if candidate.is_symlink():
                relative = candidate.relative_to(root).as_posix()
                raise ReleaseVerificationError(f"symlink protected path is forbidden: {relative}")
        for name in names:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ReleaseVerificationError(f"symlink protected file is forbidden: {relative}")
            files.add(safe_posix_member_path(relative).as_posix())
    return files


def _all_release_files(root: Path) -> set[str]:
    files: set[str] = set()
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in tuple(directories):
            candidate = current_path / directory
            if candidate.is_symlink():
                relative = candidate.relative_to(root).as_posix()
                raise ReleaseVerificationError(f"symlink release path is forbidden: {relative}")
            relative = candidate.relative_to(root)
            if (
                directory == "__pycache__"
                or relative.as_posix() in _RUNTIME_MUTABLE_ROOTS
            ):
                directories.remove(directory)
        for name in names:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                raise ReleaseVerificationError(f"symlink release file is forbidden: {relative}")
            files.add(safe_posix_member_path(relative).as_posix())
    return files


def verify_release_tree(root: str | Path) -> ReleaseVerification:
    """Verify one extracted release tree and return its non-promoting report."""
    project_root = Path(root)
    manifest_path = project_root / RELEASE_MANIFEST_NAME
    try:
        manifest_bytes, _ = stable_regular_file_bytes(manifest_path)
    except Exception as exc:
        raise ReleaseVerificationError(
            "release receipt is missing or unsafe"
        ) from exc

    receipt = _validated_receipt_from_bytes(manifest_bytes)
    declared_paths = {item["path"] for item in receipt["artifacts"]}
    total_bytes = 0
    for item in receipt["artifacts"]:
        relative = safe_posix_member_path(item["path"])
        path = project_root.joinpath(*relative.parts)
        if _path_has_symlink(project_root, relative):
            raise ReleaseVerificationError(f"symlink artifact is forbidden: {item['path']}")
        if not path.is_file():
            raise ReleaseVerificationError(f"missing protected artifact: {item['path']}")
        observed_bytes = path.stat().st_size
        if observed_bytes != item["bytes"]:
            raise ReleaseVerificationError(
                f"changed protected artifact size: {item['path']}"
            )
        observed_sha256 = sha256_file(path)
        if observed_sha256 != item["sha256"]:
            raise ReleaseVerificationError(
                f"changed protected artifact SHA-256: {item['path']}"
            )
        total_bytes += observed_bytes

    observed_protected: set[str] = set()
    for protected_root in receipt["protected_roots"]:
        observed_protected.update(_protected_files(project_root, protected_root))
    unexpected = sorted(observed_protected - declared_paths)
    if unexpected:
        raise ReleaseVerificationError(
            f"unexpected protected file: {unexpected[0]}"
        )

    allowed_files = declared_paths | {RELEASE_MANIFEST_NAME, CHECKSUMS_NAME}
    observed_files = _all_release_files(project_root)
    unexpected_release = sorted(observed_files - allowed_files)
    if unexpected_release:
        raise ReleaseVerificationError(
            f"unexpected release file: {unexpected_release[0]}"
        )

    checksums_path = project_root / CHECKSUMS_NAME
    if checksums_path.exists():
        try:
            checksum_bytes, _ = stable_regular_file_bytes(checksums_path)
        except Exception as exc:
            raise ReleaseVerificationError(
                "release checksum file is unsafe"
            ) from exc
        checksum_rows = [
            f"{item['sha256']}  {item['path']}\n"
            for item in receipt["artifacts"]
        ]
        checksum_rows.append(
            f"{_sha256_bytes(manifest_bytes)}  {RELEASE_MANIFEST_NAME}\n"
        )
        expected_checksums = "".join(sorted(checksum_rows)).encode("utf-8")
        if checksum_bytes != expected_checksums:
            raise ReleaseVerificationError("release checksum file is changed or noncanonical")

    return ReleaseVerification(
        valid=True,
        version=receipt["version"],
        source_commit=receipt["source"]["git_commit"],
        package_content_id=receipt["package"]["content_id"],
        artifact_count=len(receipt["artifacts"]),
        total_bytes=total_bytes,
        scene_trust_effect=receipt["scene_trust"]["trust_effect"],
    )


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw, _ = stable_regular_file_bytes(path)
    except Exception as exc:
        raise ReleaseVerificationError(f"{label} is missing or unsafe") from exc
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseVerificationError(f"{label} root must be an object")
    return payload


def _load_input_lock(path: Path) -> dict[str, Any]:
    payload = _read_json_object(path, label="Preview input lock")
    required = {
        "schema",
        "version",
        "runtime_allowlist",
        "runtime_exclusions",
        "source_manifests",
        "expected_counts",
        "protected_roots",
        "entrypoints",
        "excluded_prefixes",
        "scene_trust",
    }
    if set(payload) != required:
        raise ReleaseVerificationError("Preview input lock fields are not canonical")
    if payload["schema"] != INPUT_LOCK_SCHEMA:
        raise ReleaseVerificationError("Preview input lock schema is unsupported")
    if not isinstance(payload["runtime_allowlist"], list) or not payload["runtime_allowlist"]:
        raise ReleaseVerificationError("Preview runtime allowlist is empty")
    if not isinstance(payload["runtime_exclusions"], list):
        raise ReleaseVerificationError("Preview runtime exclusions are invalid")
    if not isinstance(payload["source_manifests"], list):
        raise ReleaseVerificationError("Preview source manifests are invalid")
    if not isinstance(payload["expected_counts"], dict):
        raise ReleaseVerificationError("Preview expected counts are invalid")
    return payload


def _matches_release_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    if fnmatchcase(path, pattern):
        return True
    if "/**/" in pattern:
        return fnmatchcase(path, pattern.replace("/**/", "/"))
    return False


def _is_excluded(path: str, patterns: Iterable[str]) -> bool:
    return any(_matches_release_pattern(path, pattern) for pattern in patterns)


def _checked_source_file(root: Path, relative: str, *, label: str) -> Path:
    member = safe_posix_member_path(relative)
    path = root.joinpath(*member.parts)
    if _path_has_symlink(root, member):
        raise ReleaseVerificationError(f"symlink {label} is forbidden: {relative}")
    if not path.is_file():
        raise ReleaseVerificationError(f"missing {label}: {relative}")
    return path


def _add_payload(
    payloads: dict[str, tuple[bytes, str]],
    path: str,
    payload: bytes,
    role: str,
) -> None:
    canonical = safe_posix_member_path(path).as_posix()
    existing = payloads.get(canonical)
    if existing is not None and existing != (payload, role):
        raise ReleaseVerificationError(f"conflicting release payload: {canonical}")
    payloads[canonical] = (payload, role)


def _collect_runtime_payloads(
    root: Path,
    lock: Mapping[str, Any],
    tracked_files: Iterable[str],
) -> dict[str, tuple[bytes, str]]:
    payloads: dict[str, tuple[bytes, str]] = {}
    seen: set[str] = set()
    for raw in tracked_files:
        relative = safe_posix_member_path(raw).as_posix()
        if relative in seen:
            continue
        seen.add(relative)
        if not any(
            _matches_release_pattern(relative, pattern)
            for pattern in lock["runtime_allowlist"]
        ):
            continue
        if _is_excluded(relative, lock["runtime_exclusions"]):
            continue
        path = _checked_source_file(root, relative, label="tracked runtime file")
        _add_payload(payloads, relative, path.read_bytes(), "runtime")
    required_roots = {"LICENSE", "README.md", "make.py", "pyproject.toml"}
    missing = sorted(required_roots - payloads.keys())
    if missing:
        raise ReleaseVerificationError(
            f"runtime allowlist is missing required file: {missing[0]}"
        )
    return payloads


def _locked_source_manifests(
    root: Path,
    lock: Mapping[str, Any],
) -> dict[str, tuple[Path, dict[str, Any]]]:
    by_role: dict[str, tuple[Path, dict[str, Any]]] = {}
    for raw in lock["source_manifests"]:
        if not isinstance(raw, dict) or set(raw) != {"path", "role", "sha256"}:
            raise ReleaseVerificationError("source manifest lock row is invalid")
        role = raw["role"]
        if not isinstance(role, str) or role in by_role:
            raise ReleaseVerificationError("source manifest role is invalid or duplicated")
        relative = safe_posix_member_path(raw["path"]).as_posix()
        path = _checked_source_file(root, relative, label="source manifest")
        expected_sha = raw["sha256"]
        if not isinstance(expected_sha, str) or not _SHA256_RE.fullmatch(expected_sha):
            raise ReleaseVerificationError("source manifest lock SHA-256 is invalid")
        if sha256_file(path) != expected_sha:
            raise ReleaseVerificationError(
                f"source manifest SHA-256 mismatch: {relative}"
            )
        by_role[role] = (path, _read_json_object(path, label=role))
    expected_roles = {
        "asset-registry",
        "world-manifest",
        "model-preview-manifest",
        "reconstruction-manifest",
    }
    if set(by_role) != expected_roles:
        raise ReleaseVerificationError("source manifest roles are incomplete")
    return by_role


def _expected_count(lock: Mapping[str, Any], name: str) -> int:
    value = lock["expected_counts"].get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseVerificationError(f"Preview expected count is invalid: {name}")
    return value


def _collect_asset_payloads(
    root: Path,
    registry_path: Path,
    registry: Mapping[str, Any],
    expected_count: int,
    payloads: dict[str, tuple[bytes, str]],
) -> int:
    assets = registry.get("assets")
    if not isinstance(assets, dict) or len(assets) != expected_count:
        raise ReleaseVerificationError("asset registry count mismatch")
    _add_payload(
        payloads,
        registry_path.relative_to(root).as_posix(),
        stable_regular_file_bytes(registry_path)[0],
        "asset-registry",
    )
    for asset_id, raw in sorted(assets.items()):
        if not isinstance(asset_id, str) or not isinstance(raw, dict):
            raise ReleaseVerificationError("asset registry row is invalid")
        ply = raw.get("ply")
        expected_sha = raw.get("sha256")
        if not isinstance(ply, str) or "/" in ply or "\\" in ply:
            raise ReleaseVerificationError(f"asset payload path is invalid: {asset_id}")
        relative = f"assets/{ply}"
        path = _checked_source_file(root, relative, label="registry asset")
        try:
            asset_bytes, asset_digest = stable_regular_file_bytes(path)
        except Exception as exc:
            raise ReleaseVerificationError(
                f"registry asset is unreadable: {asset_id}"
            ) from exc
        if not isinstance(expected_sha, str) or asset_digest.sha256 != expected_sha:
            raise ReleaseVerificationError(f"registry asset SHA-256 mismatch: {asset_id}")
        _add_payload(payloads, relative, asset_bytes, "registry-asset")
    return len(assets)


def _collect_world_payloads(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    expected_count: int,
    payloads: dict[str, tuple[bytes, str]],
) -> int:
    chunks = manifest.get("chunks")
    if (
        not isinstance(chunks, list)
        or len(chunks) != expected_count
        or manifest.get("total_chunks") != expected_count
    ):
        raise ReleaseVerificationError("world chunk count mismatch")
    grid = manifest.get("grid")
    if not isinstance(grid, dict) or grid.get("on_demand") is not False:
        raise ReleaseVerificationError("static Preview world must keep grid.on_demand=false")
    seen_ids: set[str] = set()
    referenced: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ReleaseVerificationError("world chunk row is invalid")
        chunk_id = chunk.get("id")
        if not isinstance(chunk_id, str) or chunk_id in seen_ids:
            raise ReleaseVerificationError("world chunk ID is invalid or duplicated")
        seen_ids.add(chunk_id)
        candidates = [chunk.get("ply_file")]
        lod = chunk.get("lod")
        if not isinstance(lod, dict) or set(lod) != {"0", "1", "2"}:
            raise ReleaseVerificationError(f"world chunk LOD contract is invalid: {chunk_id}")
        candidates.extend(lod.values())
        for candidate in candidates:
            if not isinstance(candidate, str) or "/" in candidate or "\\" in candidate:
                raise ReleaseVerificationError(f"world chunk path is invalid: {chunk_id}")
            referenced.add(f"web/data/{candidate}")
    for relative in sorted(referenced):
        path = _checked_source_file(root, relative, label="world chunk")
        _add_payload(payloads, relative, path.read_bytes(), "world-chunk")

    projected = json.loads(json.dumps(manifest))
    if "mesh_grid" in projected:
        projected["mesh_grid"] = {
            "on_demand": False,
            "reason": PRIVATE_MESH_REASON,
        }
    _add_payload(
        payloads,
        manifest_path.relative_to(root).as_posix(),
        canonical_json_bytes(projected),
        "world-manifest",
    )
    return len(chunks)


def _collect_reconstruction_payloads(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    expected_gaussians: int,
    payloads: dict[str, tuple[bytes, str]],
) -> int:
    if manifest.get("gaussian_count") != expected_gaussians:
        raise ReleaseVerificationError("reconstruction Gaussian count mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ReleaseVerificationError("reconstruction artifacts are missing")
    full = artifacts.get("full_3dgs")
    lod = artifacts.get("lod")
    if not isinstance(full, dict) or not isinstance(lod, dict) or set(lod) != {"0", "1", "2"}:
        raise ReleaseVerificationError("reconstruction artifact contract is invalid")
    for role, raw in [("reconstruction-3dgs", full), *[
        (f"reconstruction-lod-{level}", lod[level]) for level in ("0", "1", "2")
    ]]:
        relative_name = raw.get("path")
        expected_bytes = raw.get("bytes")
        expected_sha = raw.get("sha256")
        if (
            not isinstance(relative_name, str)
            or "/" in relative_name
            or "\\" in relative_name
        ):
            raise ReleaseVerificationError(f"reconstruction path is invalid: {role}")
        relative = f"web/data/recon/{relative_name}"
        path = _checked_source_file(root, relative, label=role)
        try:
            recon_bytes, recon_digest = stable_regular_file_bytes(path)
        except Exception as exc:
            raise ReleaseVerificationError(
                f"reconstruction artifact is unreadable: {relative}"
            ) from exc
        if (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or recon_digest.byte_length != expected_bytes
            or not isinstance(expected_sha, str)
            or recon_digest.sha256 != expected_sha
        ):
            raise ReleaseVerificationError(f"reconstruction artifact mismatch: {relative}")
        _add_payload(payloads, relative, recon_bytes, role)
    _add_payload(
        payloads,
        manifest_path.relative_to(root).as_posix(),
        stable_regular_file_bytes(manifest_path)[0],
        "reconstruction-manifest",
    )
    return expected_gaussians


def _collect_model_payloads(
    root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    expected_meshes: int,
    expected_materials: int,
    payloads: dict[str, tuple[bytes, str]],
) -> None:
    counts = manifest.get("counts")
    if not isinstance(counts, dict) or counts != {
        "mesh_objects": expected_meshes,
        "visual_materials": expected_materials,
    }:
        raise ReleaseVerificationError("model preview counts mismatch")
    if (
        manifest.get("synthetic") is not True
        or manifest.get("geometry_usability") != "preview-only"
    ):
        raise ReleaseVerificationError("model preview trust contract is invalid")
    model = manifest.get("model")
    if not isinstance(model, dict):
        raise ReleaseVerificationError("model preview payload is missing")
    name = model.get("path")
    expected_bytes = model.get("byte_length")
    expected_sha = model.get("sha256")
    if not isinstance(name, str) or "/" in name or "\\" in name:
        raise ReleaseVerificationError("model preview path is invalid")
    relative = f"web/data/recon/model-preview/{name}"
    path = _checked_source_file(root, relative, label="model preview")
    try:
        model_bytes, model_digest = stable_regular_file_bytes(path)
    except Exception as exc:
        raise ReleaseVerificationError(
            "model preview payload is unreadable"
        ) from exc
    if (
        isinstance(expected_bytes, bool)
        or not isinstance(expected_bytes, int)
        or model_digest.byte_length != expected_bytes
        or not isinstance(expected_sha, str)
        or model_digest.sha256 != expected_sha
    ):
        raise ReleaseVerificationError("model preview payload mismatch")
    _add_payload(payloads, relative, model_bytes, "model-preview")
    _add_payload(
        payloads,
        manifest_path.relative_to(root).as_posix(),
        stable_regular_file_bytes(manifest_path)[0],
        "model-preview-manifest",
    )


def _checksum_bytes(
    artifacts: Iterable[Mapping[str, Any]],
    receipt_bytes: bytes,
) -> bytes:
    rows = [f"{item['sha256']}  {item['path']}\n" for item in artifacts]
    rows.append(f"{_sha256_bytes(receipt_bytes)}  {RELEASE_MANIFEST_NAME}\n")
    return "".join(sorted(rows)).encode("utf-8")


def _zip_info(path: str) -> zipfile.ZipInfo:
    return deterministic_zip_info(path)


def build_release_archive(
    repo_root: str | Path,
    output_path: str | Path,
    *,
    input_lock_path: str | Path,
    source_commit: str,
    tracked_files: Iterable[str],
) -> ReleaseBuild:
    """Build, re-open and verify one deterministic Preview runtime archive."""
    root = Path(repo_root)
    output = Path(output_path)
    lock_path = Path(input_lock_path)
    if not lock_path.is_absolute():
        lock_path = root / lock_path
    lock = _load_input_lock(lock_path)
    if lock["version"] != "v1.0.0-preview.2":
        raise ReleaseVerificationError("Preview input lock version is unsupported")

    payloads = _collect_runtime_payloads(root, lock, tracked_files)
    manifests = _locked_source_manifests(root, lock)
    asset_count = _collect_asset_payloads(
        root,
        manifests["asset-registry"][0],
        manifests["asset-registry"][1],
        _expected_count(lock, "registry_assets"),
        payloads,
    )
    world_chunk_count = _collect_world_payloads(
        root,
        manifests["world-manifest"][0],
        manifests["world-manifest"][1],
        _expected_count(lock, "baked_world_chunks"),
        payloads,
    )
    gaussian_count = _collect_reconstruction_payloads(
        root,
        manifests["reconstruction-manifest"][0],
        manifests["reconstruction-manifest"][1],
        _expected_count(lock, "gaussians"),
        payloads,
    )
    _collect_model_payloads(
        root,
        manifests["model-preview-manifest"][0],
        manifests["model-preview-manifest"][1],
        _expected_count(lock, "model_mesh_objects"),
        _expected_count(lock, "model_visual_materials"),
        payloads,
    )

    artifact_rows = [
        {
            "path": path,
            "role": role,
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        }
        for path, (payload, role) in payloads.items()
    ]
    receipt = build_receipt(
        version=lock["version"],
        source_commit=source_commit,
        artifacts=artifact_rows,
        protected_roots=lock["protected_roots"],
        entrypoints=lock["entrypoints"],
        exclusions=lock["excluded_prefixes"],
        scene_trust=lock["scene_trust"],
    )
    receipt_bytes = canonical_json_bytes(receipt)
    checksum_bytes = _checksum_bytes(receipt["artifacts"], receipt_bytes)
    archive_payloads = {
        **{path: payload for path, (payload, _role) in payloads.items()},
        RELEASE_MANIFEST_NAME: receipt_bytes,
        CHECKSUMS_NAME: checksum_bytes,
    }
    wrapper = f"nantai-3d-{lock['version']}"

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.partial")
    temporary.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    sidecar = output.with_suffix(f"{output.suffix}.sha256")
    sidecar.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for relative, payload in sorted(archive_payloads.items()):
                member = f"{wrapper}/{safe_posix_member_path(relative).as_posix()}"
                archive.writestr(_zip_info(member), payload, compresslevel=9)
        os.replace(temporary, output)
        verified = verify_release_archive(output)
        archive_sha = sha256_file(output)
        sidecar.write_text(
            f"{archive_sha}  {output.name}\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    return ReleaseBuild(
        archive_path=output,
        archive_sha256=archive_sha,
        package_content_id=verified.package_content_id,
        artifact_count=verified.artifact_count,
        total_bytes=verified.total_bytes,
        asset_count=asset_count,
        world_chunk_count=world_chunk_count,
        gaussian_count=gaussian_count,
    )


def verify_release_archive(archive_path: str | Path) -> ReleaseVerification:
    """Safely extract one runtime ZIP to a temporary tree and verify its receipt."""
    source = Path(archive_path)
    if source.is_symlink() or not source.is_file():
        raise ReleaseVerificationError("release archive is missing or unsafe")
    with tempfile.TemporaryDirectory(prefix="nantai-preview-verify-") as temporary:
        extraction_root = Path(temporary)
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if not infos:
                raise ReleaseVerificationError("release archive is empty")
            seen: set[str] = set()
            wrappers: set[str] = set()
            for info in infos:
                if info.is_dir():
                    raise ReleaseVerificationError(
                        "release archive directory entries are forbidden"
                    )
                member = safe_posix_member_path(info.filename)
                if info.filename in seen:
                    raise ReleaseVerificationError("duplicate release archive member")
                seen.add(info.filename)
                if len(member.parts) < 2:
                    raise ReleaseVerificationError("release archive member lacks wrapper root")
                wrappers.add(member.parts[0])
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in {0, stat.S_IFREG}:
                    raise ReleaseVerificationError("release archive contains a non-file member")
            if len(wrappers) != 1:
                raise ReleaseVerificationError("release archive must have exactly one wrapper root")
            for info in infos:
                member = safe_posix_member_path(info.filename)
                relative = PurePosixPath(*member.parts[1:])
                destination = extraction_root.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source_handle, destination.open("wb") as target:
                    shutil.copyfileobj(source_handle, target)
        return verify_release_tree(extraction_root)
