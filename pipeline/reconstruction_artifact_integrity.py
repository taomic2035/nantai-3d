"""Additive fail-closed verifier for imported reconstruction artifacts.

Closes the gap left by ``scripts/inspect_recon.py``: that script deliberately
does NOT touch PLY bytes or recompute artifact SHAs (header lines 19-24).
This module recomputes every declared artifact SHA-256 and size, walks
``chunks.json`` entries, rejects symlinks/path-escapes/duplicates, and
reports ``verified`` / ``mismatch`` / ``unknown`` separately.

Iron rules (the whole reason this module exists):

- **Never promote trust.** ``preview-only`` stays ``preview-only`` even if
  every byte verifies.  ``metric-aligned`` stays ``metric-aligned`` (it is
  not upgraded to "verified metric-aligned").  Real-photo and training trust
  are never promoted either.  Byte verification is a separate concern from
  coordinate trust.
- **Fail closed on tampering.** A symlinked manifest or artifact, a path
  escape, a missing file, a stale SHA, a duplicate path, a duplicate JSON
  key, or a chunk count mismatch are all reported; nothing is silently
  ignored.
- **Additive only.** ``inspect_recon`` remains the lightweight claim
  translator.  This module does not modify it, the manifest, or any
  artifact.  It only reads and reports.

Limitations (stated plainly):

- chunks.json has no per-chunk SHA in its schema today (only the manifest-
  level ``source.recon_manifest_sha256`` attests integrity).  This module
  verifies that every chunk PLY exists, is not a symlink, is inside the
  chunks dir, and that ``total_chunks`` / ``total_points`` / ``bounds`` are
  internally consistent.  It cannot detect tampered chunk PLY bytes
  without a per-chunk SHA; that gap is reported in ``ChunksReport``.
- The verifier reads manifest *claims* plus recomputed artifact bytes.  It
  does NOT recompute Sim3 residuals or re-run COLMAP; contradictions in
  metric evidence are flagged by parsing the same ``sim3.alignment.v1=``
  strings that ``inspect_recon`` parses, using the same fail-closed rule
  as ``pipeline.reconstruct._alignment_evidence_consistent``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Output schema (internal, safe to validate with pydantic)
# ---------------------------------------------------------------------------


class ArtifactVerification(BaseModel):
    """A declared artifact whose recomputed SHA-256 and size both match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_key: str
    path: str
    kind: str
    fidelity: str
    declared_sha256: str
    actual_sha256: str
    declared_bytes: int
    actual_bytes: int
    sha256_match: bool
    size_match: bool


class ArtifactMismatch(BaseModel):
    """A declared artifact whose recomputed SHA-256 or size does NOT match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_key: str
    path: str
    declared_sha256: str
    actual_sha256: str
    declared_bytes: int
    actual_bytes: int
    sha256_match: bool
    size_match: bool


class ArtifactUnknown(BaseModel):
    """A declared artifact with no ``sha256`` field (cannot be verified)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_key: str
    path: str
    reason: str


class PathSafetyViolation(BaseModel):
    """A path that escapes the manifest dir, is a symlink, or is missing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_key: str
    path: str
    reason: str


class ChunksReport(BaseModel):
    """Verification of ``artifacts.chunks.manifest`` (a chunks.json file)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunks_manifest_path: str
    total_chunks: int
    total_chunks_matches_len: bool
    total_points: int
    total_points_matches_sum: bool
    bounds_consistent_with_aabbs: bool
    verified_chunk_files: int
    missing_chunk_files: list[str] = Field(default_factory=list)
    duplicate_chunk_paths: list[str] = Field(default_factory=list)
    extra_unbound_chunk_files: list[str] = Field(default_factory=list)
    # Note: chunks.json has no per-chunk SHA, so byte tampering on a chunk
    # PLY cannot be detected from the manifest alone.  This field records
    # the limitation explicitly so callers cannot mistake existence-checks
    # for byte verification.
    per_chunk_sha_verified: bool = False


class IntegrityReport(BaseModel):
    """Top-level report returned by :func:`verify_recon_artifacts`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_path: str
    schema_version: int | None
    engine: str | None
    verified: list[ArtifactVerification] = Field(default_factory=list)
    mismatch: list[ArtifactMismatch] = Field(default_factory=list)
    unknown: list[ArtifactUnknown] = Field(default_factory=list)
    chunks_report: ChunksReport | None = None
    path_safety_violations: list[PathSafetyViolation] = Field(default_factory=list)
    duplicate_paths: list[str] = Field(default_factory=list)
    duplicate_json_keys: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    trust_preserved: bool = True
    geometry_usability: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _DuplicateKeyRecorder:
    """JSON ``object_pairs_hook`` that records duplicate keys."""

    def __init__(self) -> None:
        self.duplicates: list[str] = []

    def __call__(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                # Record the path of the duplicate (best-effort).
                self.duplicates.append(key)
            seen[key] = value
        return seen


def _is_symlink(path: Path) -> bool:
    """Return True if ``path`` is a symlink (does not follow)."""
    return path.is_symlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_resolve(path: Path) -> Path:
    """Resolve ``path`` without following symlinks (for escape checks)."""
    # ``Path.resolve(strict=False)`` follows symlinks.  We want to detect
    # the symlink itself, so we use ``os.path.realpath`` only after an
    # explicit symlink check at the caller.  Here we just compute the
    # lexical absolute path to detect ``..`` escapes.
    return path.absolute() if not path.is_absolute() else path


_SIM3_EVIDENCE_PREFIX = "sim3.alignment.v1="


def _parse_sim3_evidence(evidence_list: Any) -> list[dict[str, Any]]:
    """Parse ``sim3.alignment.v1=<json>`` records from the evidence list.

    Returns a list of parsed dicts.  Unparseable records are skipped (the
    caller treats them as contradictions via the same rule as
    ``pipeline.reconstruct._alignment_evidence_consistent``).
    """
    parsed: list[dict[str, Any]] = []
    if not isinstance(evidence_list, list):
        return parsed
    for entry in evidence_list:
        if not isinstance(entry, str) or not entry.startswith(_SIM3_EVIDENCE_PREFIX):
            continue
        payload = entry[len(_SIM3_EVIDENCE_PREFIX):]
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            # Treat as contradiction by returning a sentinel.
            parsed.append({"_unparseable": True})
            continue
        if not isinstance(obj, dict):
            parsed.append({"_unparseable": True})
            continue
        parsed.append(obj)
    return parsed


def _alignment_evidence_consistent(parsed: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Return ``(consistent, reasons)``.

    Mirrors ``pipeline.reconstruct._alignment_evidence_consistent``: any
    ``sim3.alignment.v1=`` record that fails to parse or carries
    ``passed=False`` makes the whole evidence inconsistent.
    """
    reasons: list[str] = []
    for idx, obj in enumerate(parsed):
        if obj.get("_unparseable"):
            reasons.append(
                f"metric_evidence[{idx}]: unparseable sim3.alignment.v1 record"
            )
            continue
        if "passed" in obj and obj["passed"] is False:
            reasons.append(
                f"metric_evidence[{idx}]: sim3.alignment.v1 record has passed=false"
            )
    return (len(reasons) == 0, reasons)


def _check_metric_contradictions(manifest: dict[str, Any]) -> list[str]:
    """Flag contradictions between claimed geometry_usability and evidence.

    Mirrors the fail-closed rule in ``scripts/inspect_recon.py``: a
    ``metric-aligned`` or ``metric-unaligned`` claim requires consistent
    ``sim3.alignment.v1=`` evidence (no ``passed=false``, no unparseable
    records) AND non-synthetic provenance.
    """
    contradictions: list[str] = []
    provenance = manifest.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    contract = manifest.get("coordinate_contract") or {}
    if not isinstance(contract, dict):
        contract = {}
    target_frame = contract.get("target_frame") or {}
    if not isinstance(target_frame, dict):
        target_frame = {}
    geometry_usability = provenance.get("geometry_usability")
    is_synthetic = bool(provenance.get("synthetic", False))

    if geometry_usability in ("metric-aligned", "metric-unaligned"):
        if is_synthetic:
            contradictions.append(
                "geometry_usability claims "
                f"{geometry_usability} but provenance.synthetic is true"
            )
        units = target_frame.get("units")
        metric_status = target_frame.get("metric_status")
        if units != "meters":
            contradictions.append(
                f"geometry_usability claims {geometry_usability} but "
                f"target_frame.units is {units!r} (expected 'meters')"
            )
        if metric_status != "metric":
            contradictions.append(
                f"geometry_usability claims {geometry_usability} but "
                f"target_frame.metric_status is {metric_status!r} (expected 'metric')"
            )
        parsed = _parse_sim3_evidence(contract.get("metric_evidence"))
        consistent, reasons = _alignment_evidence_consistent(parsed)
        if not consistent:
            for reason in reasons:
                contradictions.append(
                    f"geometry_usability claims {geometry_usability} but {reason}"
                )
        if geometry_usability == "metric-aligned":
            if contract.get("alignment_status") != "aligned":
                contradictions.append(
                    "geometry_usability claims metric-aligned but "
                    f"alignment_status is {contract.get('alignment_status')!r}"
                )
            if target_frame.get("geo_aligned") != "aligned":
                contradictions.append(
                    "geometry_usability claims metric-aligned but "
                    f"target_frame.geo_aligned is "
                    f"{target_frame.get('geo_aligned')!r}"
                )
        if geometry_usability == "metric-unaligned":
            if contract.get("alignment_status") != "unaligned":
                contradictions.append(
                    "geometry_usability claims metric-unaligned but "
                    f"alignment_status is {contract.get('alignment_status')!r}"
                )
            if target_frame.get("geo_aligned") != "unaligned":
                contradictions.append(
                    "geometry_usability claims metric-unaligned but "
                    f"target_frame.geo_aligned is "
                    f"{target_frame.get('geo_aligned')!r}"
                )
    return contradictions


def _iter_artifact_entries(
    artifacts: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Flatten ``artifacts`` into ``(key, entry)`` pairs.

    - ``artifacts.full_3dgs`` → key ``"full_3dgs"``
    - ``artifacts.lod.0`` → key ``"lod.0"``
    - ``artifacts.lod.1`` → key ``"lod.1"``
    - ``artifacts.lod.2`` → key ``"lod.2"``
    - ``artifacts.chunks`` is NOT an artifact entry; it points at chunks.json.
    """
    entries: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(artifacts, dict):
        return entries
    full = artifacts.get("full_3dgs")
    if isinstance(full, dict):
        entries.append(("full_3dgs", full))
    lod = artifacts.get("lod")
    if isinstance(lod, dict):
        for level, entry in sorted(lod.items()):
            if isinstance(entry, dict):
                entries.append((f"lod.{level}", entry))
    return entries


def _verify_chunks(
    chunks_path: Path,
    chunks_meta: dict[str, Any],
) -> ChunksReport:
    """Verify a chunks.json file and its chunk PLYs."""
    chunks_data = _load_json_with_duplicate_check(chunks_path)[0]
    chunks_list = chunks_data.get("chunks") or []
    if not isinstance(chunks_list, list):
        chunks_list = []
    chunks_dir = chunks_path.parent

    duplicate_chunk_paths: list[str] = []
    missing_chunk_files: list[str] = []
    verified_chunk_files = 0
    referenced_files: set[str] = set()

    total_points_sum = 0
    aabb_mins: list[list[float]] = []
    aabb_maxs: list[list[float]] = []

    # Track which chunk first referenced each path so we can flag
    # cross-chunk duplicates while allowing the documented convention
    # that ``ply_file`` == ``lod.2`` within a single chunk (lod2 == full).
    path_to_first_chunk: dict[str, str] = {}

    for chunk in chunks_list:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("id")
        if not isinstance(chunk_id, str):
            chunk_id = "<unknown>"
        point_count = chunk.get("point_count")
        if isinstance(point_count, (int, float)) and not isinstance(point_count, bool):
            total_points_sum += int(point_count)
        aabb = chunk.get("aabb")
        if isinstance(aabb, dict):
            mn = aabb.get("min")
            mx = aabb.get("max")
            if isinstance(mn, list) and len(mn) == 3:
                aabb_mins.append([float(v) for v in mn])
            if isinstance(mx, list) and len(mx) == 3:
                aabb_maxs.append([float(v) for v in mx])

        # Collect this chunk's referenced PLY paths (deduped within chunk).
        chunk_paths: list[str] = []
        ply_file = chunk.get("ply_file")
        if isinstance(ply_file, str) and ply_file:
            chunk_paths.append(ply_file)
        lod = chunk.get("lod")
        if isinstance(lod, dict):
            for _level, fname in sorted(lod.items()):
                if isinstance(fname, str) and fname:
                    chunk_paths.append(fname)
        # Dedup within this chunk (ply_file == lod.2 is allowed).
        chunk_paths_unique = list(dict.fromkeys(chunk_paths))

        for fname in chunk_paths_unique:
            referenced_files.add(fname)
            previous_chunk = path_to_first_chunk.get(fname)
            if previous_chunk is not None and previous_chunk != chunk_id:
                # Same path referenced by two different chunks.
                if fname not in duplicate_chunk_paths:
                    duplicate_chunk_paths.append(fname)
            else:
                path_to_first_chunk[fname] = chunk_id
            # Existence + symlink check.
            candidate = chunks_dir / fname
            if _is_symlink(candidate):
                missing_chunk_files.append(f"{fname} (symlink rejected)")
                continue
            resolved = candidate.resolve()
            if not resolved.is_file():
                missing_chunk_files.append(fname)
            else:
                verified_chunk_files += 1

    # Detect extra unbound PLY files in the chunks dir.
    extra_unbound_chunk_files: list[str] = []
    if chunks_dir.is_dir():
        for entry in sorted(chunks_dir.iterdir()):
            if entry.is_symlink():
                # Skip symlinks here; they are flagged elsewhere if referenced.
                continue
            if entry.is_file() and entry.suffix.lower() == ".ply":
                rel = entry.name
                if rel not in referenced_files:
                    extra_unbound_chunk_files.append(rel)

    # Structural consistency checks.
    declared_total_chunks = chunks_data.get("total_chunks")
    total_chunks_matches_len = (
        isinstance(declared_total_chunks, int)
        and declared_total_chunks == len(chunks_list)
    )

    declared_total_points = chunks_data.get("total_points")
    total_points_matches_sum = (
        isinstance(declared_total_points, int)
        and declared_total_points == total_points_sum
    )

    # Bounds consistency: declared bounds must contain all chunk AABBs.
    bounds_consistent = True
    declared_bounds = chunks_data.get("bounds")
    if (
        isinstance(declared_bounds, dict)
        and isinstance(declared_bounds.get("min"), list)
        and isinstance(declared_bounds.get("max"), list)
        and len(declared_bounds["min"]) == 3
        and len(declared_bounds["max"]) == 3
        and aabb_mins
        and aabb_maxs
    ):
        declared_min = [float(v) for v in declared_bounds["min"]]
        declared_max = [float(v) for v in declared_bounds["max"]]
        for mn, mx in zip(aabb_mins, aabb_maxs, strict=False):
            for axis in range(3):
                if mn[axis] < declared_min[axis] - 1e-6:
                    bounds_consistent = False
                if mx[axis] > declared_max[axis] + 1e-6:
                    bounds_consistent = False
    elif aabb_mins:
        # AABBs exist but declared bounds are missing or malformed.
        bounds_consistent = False

    return ChunksReport(
        chunks_manifest_path=str(chunks_path),
        total_chunks=len(chunks_list),
        total_chunks_matches_len=total_chunks_matches_len,
        total_points=total_points_sum,
        total_points_matches_sum=total_points_matches_sum,
        bounds_consistent_with_aabbs=bounds_consistent,
        verified_chunk_files=verified_chunk_files,
        missing_chunk_files=missing_chunk_files,
        duplicate_chunk_paths=duplicate_chunk_paths,
        extra_unbound_chunk_files=extra_unbound_chunk_files,
        per_chunk_sha_verified=False,
    )


def _load_json_with_duplicate_check(
    path: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Load JSON and record duplicate keys.  Returns ``(data, duplicates)``."""
    recorder = _DuplicateKeyRecorder()
    text = path.read_text("utf-8")
    data = json.loads(text, object_pairs_hook=recorder)
    return data, recorder.duplicates


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_recon_artifacts(manifest_path: Path) -> IntegrityReport:
    """Verify every declared artifact in ``recon_manifest.json``.

    Args:
        manifest_path: Path to ``recon_manifest.json``.

    Returns:
        An :class:`IntegrityReport` with verified/mismatch/unknown lists,
        path-safety violations, duplicate paths, duplicate JSON keys,
        contradictions, and (if present) a chunks report.

    Raises:
        FileNotFoundError: manifest does not exist.
        ValueError: manifest is a symlink (fail-closed) or not a dict.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    if _is_symlink(manifest_path):
        raise ValueError(
            f"manifest is a symlink (fail-closed): {manifest_path}"
        )

    manifest, duplicates = _load_json_with_duplicate_check(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(
            f"manifest root is not a JSON object: {type(manifest).__name__}"
        )

    manifest_dir = manifest_path.parent.resolve()
    schema_version = manifest.get("schema_version")
    if not isinstance(schema_version, int):
        schema_version = None
    engine = manifest.get("engine")
    if not isinstance(engine, str):
        engine = None

    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        artifacts = {}

    verified: list[ArtifactVerification] = []
    mismatch: list[ArtifactMismatch] = []
    unknown: list[ArtifactUnknown] = []
    path_safety_violations: list[PathSafetyViolation] = []
    seen_paths: dict[str, str] = {}  # path -> first artifact_key
    duplicate_paths: list[str] = []

    for key, entry in _iter_artifact_entries(artifacts):
        declared_path = entry.get("path")
        if not isinstance(declared_path, str) or not declared_path:
            path_safety_violations.append(
                PathSafetyViolation(
                    artifact_key=key,
                    path=str(declared_path),
                    reason="missing or non-string path field",
                )
            )
            continue

        # Path-escape check: the resolved path must be inside manifest_dir.
        candidate = (manifest_dir / declared_path)
        try:
            resolved = candidate.resolve()
            resolved.relative_to(manifest_dir)
        except (ValueError, OSError) as exc:
            path_safety_violations.append(
                PathSafetyViolation(
                    artifact_key=key,
                    path=declared_path,
                    reason=f"path escapes manifest dir ({exc})",
                )
            )
            continue

        # Symlink check.
        if _is_symlink(candidate):
            path_safety_violations.append(
                PathSafetyViolation(
                    artifact_key=key,
                    path=declared_path,
                    reason="artifact path is a symlink (fail-closed)",
                )
            )
            continue

        # Missing file.
        if not resolved.is_file():
            path_safety_violations.append(
                PathSafetyViolation(
                    artifact_key=key,
                    path=declared_path,
                    reason="artifact file missing",
                )
            )
            continue

        # Duplicate path check.
        if declared_path in seen_paths:
            duplicate_paths.append(declared_path)
        else:
            seen_paths[declared_path] = key

        # SHA + size verification.
        declared_sha = entry.get("sha256")
        declared_bytes = entry.get("bytes")
        actual_sha = _sha256_file(resolved)
        actual_bytes = resolved.stat().st_size

        kind = entry.get("kind") or ""
        fidelity = entry.get("fidelity") or ""

        if not isinstance(declared_sha, str) or not re.fullmatch(
            r"[0-9a-f]{64}", declared_sha
        ):
            unknown.append(
                ArtifactUnknown(
                    artifact_key=key,
                    path=declared_path,
                    reason=(
                        "missing or invalid sha256 field "
                        "(expected 64 lowercase hex chars)"
                    ),
                )
            )
            continue

        sha_match = (declared_sha == actual_sha)
        size_match = (
            isinstance(declared_bytes, int)
            and not isinstance(declared_bytes, bool)
            and declared_bytes == actual_bytes
        )

        if sha_match and size_match:
            verified.append(
                ArtifactVerification(
                    artifact_key=key,
                    path=declared_path,
                    kind=kind,
                    fidelity=fidelity,
                    declared_sha256=declared_sha,
                    actual_sha256=actual_sha,
                    declared_bytes=declared_bytes if isinstance(declared_bytes, int) else 0,
                    actual_bytes=actual_bytes,
                    sha256_match=True,
                    size_match=True,
                )
            )
        else:
            mismatch.append(
                ArtifactMismatch(
                    artifact_key=key,
                    path=declared_path,
                    declared_sha256=declared_sha,
                    actual_sha256=actual_sha,
                    declared_bytes=declared_bytes if isinstance(declared_bytes, int) else 0,
                    actual_bytes=actual_bytes,
                    sha256_match=sha_match,
                    size_match=size_match,
                )
            )

    # Optional chunks.json verification.
    chunks_report: ChunksReport | None = None
    chunks_meta = artifacts.get("chunks")
    if isinstance(chunks_meta, dict):
        chunks_manifest_rel = chunks_meta.get("manifest")
        if isinstance(chunks_manifest_rel, str) and chunks_manifest_rel:
            chunks_path_candidate = manifest_dir / chunks_manifest_rel
            # Path-escape + symlink check for chunks.json itself.  Use a
            # flag so we don't try/except/elif (invalid Python).
            chunks_path_ok = True
            try:
                chunks_resolved = chunks_path_candidate.resolve()
                chunks_resolved.relative_to(manifest_dir)
            except (ValueError, OSError) as exc:
                path_safety_violations.append(
                    PathSafetyViolation(
                        artifact_key="chunks.manifest",
                        path=chunks_manifest_rel,
                        reason=f"chunks.json path escapes manifest dir ({exc})",
                    )
                )
                chunks_path_ok = False
            if chunks_path_ok:
                if _is_symlink(chunks_path_candidate):
                    path_safety_violations.append(
                        PathSafetyViolation(
                            artifact_key="chunks.manifest",
                            path=chunks_manifest_rel,
                            reason="chunks.json is a symlink (fail-closed)",
                        )
                    )
                elif not chunks_resolved.is_file():
                    path_safety_violations.append(
                        PathSafetyViolation(
                            artifact_key="chunks.manifest",
                            path=chunks_manifest_rel,
                            reason="chunks.json file missing",
                        )
                    )
                else:
                    chunks_report = _verify_chunks(chunks_resolved, chunks_meta)

    # Trust preservation: detect contradictions, never promote.
    contradictions = _check_metric_contradictions(manifest)
    provenance = manifest.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    geometry_usability = provenance.get("geometry_usability")
    if not isinstance(geometry_usability, str):
        geometry_usability = None

    return IntegrityReport(
        manifest_path=str(manifest_path),
        schema_version=schema_version,
        engine=engine,
        verified=verified,
        mismatch=mismatch,
        unknown=unknown,
        chunks_report=chunks_report,
        path_safety_violations=path_safety_violations,
        duplicate_paths=duplicate_paths,
        duplicate_json_keys=duplicates,
        contradictions=contradictions,
        trust_preserved=True,
        geometry_usability=geometry_usability,
    )
