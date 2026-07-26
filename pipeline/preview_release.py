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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PREVIEW_RELEASE_SCHEMA = "nantai.preview-release.v1"
PREVIEW_LAYOUT = "nantai.preview-runtime.v1"
RELEASE_MANIFEST_NAME = "RELEASE-MANIFEST.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+-preview\.[0-9]+$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
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


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize canonical UTF-8 JSON with LF and one trailing newline."""
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def safe_posix_member_path(value: str) -> PurePosixPath:
    """Return one unambiguous relative POSIX path or fail closed."""
    if not isinstance(value, str) or not value:
        raise ValueError("release member path must be a non-empty string")
    if "\\" in value:
        raise ValueError("release member path must not contain backslashes")
    if value.startswith("/") or value.startswith("//") or _DRIVE_RE.match(value):
        raise ValueError("release member path must be relative")
    if "//" in value:
        raise ValueError("release member path must not contain empty components")

    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("release member path contains an unsafe component")
    for part in raw_parts:
        if part.endswith((".", " ")):
            raise ValueError("release member path has a Windows-ambiguous suffix")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise ValueError("release member path uses a Windows reserved name")

    path = PurePosixPath(*raw_parts)
    if path.is_absolute() or path.as_posix() != value:
        raise ValueError("release member path is not canonical POSIX")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def verify_release_tree(root: str | Path) -> ReleaseVerification:
    """Verify one extracted release tree and return its non-promoting report."""
    project_root = Path(root)
    manifest_path = project_root / RELEASE_MANIFEST_NAME
    if manifest_path.is_symlink():
        raise ReleaseVerificationError("release receipt symlink is forbidden")
    if not manifest_path.is_file():
        raise ReleaseVerificationError("release receipt is missing")

    receipt = _validated_receipt_from_bytes(manifest_path.read_bytes())
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

    return ReleaseVerification(
        valid=True,
        version=receipt["version"],
        source_commit=receipt["source"]["git_commit"],
        package_content_id=receipt["package"]["content_id"],
        artifact_count=len(receipt["artifacts"]),
        total_bytes=total_bytes,
        scene_trust_effect=receipt["scene_trust"]["trust_effect"],
    )
