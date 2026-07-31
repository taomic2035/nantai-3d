"""Canonical lossy-geometry lineage shared by producers and trust gates.

The full 3DGS PLY carries ``nantai_meta.lossy_edits`` so lineage survives
copying and renaming.  ``recon_manifest.json`` carries the same semantic value
for lightweight consumers.  Production gates must reopen the PLY and compare
both values; a manifest label alone is never evidence.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from pipeline.durable_io import first_linklike_path

MAX_PLY_HEADER_BYTES = 16 * 1024 * 1024
_MISSING = object()


class LossyLineageError(ValueError):
    """The declared lossy-edit lineage is malformed or cannot be reopened."""


def _is_linklike(path: Path, observed: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _cross_surface_signature(
    observed: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    """Identity fields that survive across lstat and fstat surfaces.

    ``S_IFMT`` ignores permission-bit differences Windows reports differently
    between path-based and descriptor-based stat.
    """
    return (
        observed.st_dev,
        observed.st_ino,
        stat.S_IFMT(observed.st_mode),
        observed.st_size,
        observed.st_mtime_ns,
        int(getattr(observed, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _same_surface_signature(
    observed: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    """Identity fields stable on the same surface (fstat vs fstat)."""
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
        int(getattr(observed, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def normalize_lossy_edits(value: Any, *, label: str) -> list[dict[str, Any]]:
    """Return a JSON-normalized list of edit objects or fail closed."""

    if not isinstance(value, list):
        raise LossyLineageError(f"{label} must be a JSON array")
    normalized: list[dict[str, Any]] = []
    for index, edit in enumerate(value):
        if not isinstance(edit, dict):
            raise LossyLineageError(f"{label}[{index}] must be a JSON object")
        try:
            payload = json.dumps(
                edit,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            item = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise LossyLineageError(f"{label}[{index}] must be finite JSON") from exc
        if item in normalized:
            raise LossyLineageError(f"{label} contains a duplicate edit")
        normalized.append(item)
    return normalized


def read_ply_lossy_edits(path: Path) -> list[dict[str, Any]] | None:
    """Read only the bounded PLY header and return embedded lossy edits.

    ``None`` means the historical PLY has no ``nantai_meta.lossy_edits`` key.
    Malformed, duplicate, oversized, or unterminated metadata fails closed.

    Opens through a single ``O_NOFOLLOW`` descriptor and verifies file identity
    before and after reading to close the check-then-reopen TOCTOU window that
    would otherwise allow a PLY swap between a prior SHA check and this header
    read.
    """

    absolute = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(absolute.anchor), absolute)
        before = absolute.lstat()
    except (OSError, ValueError, RuntimeError) as exc:
        raise LossyLineageError("PLY header cannot be inspected") from exc
    if redirected is not None or _is_linklike(absolute, before) or not stat.S_ISREG(before.st_mode):
        raise LossyLineageError("full_3dgs is not a bounded regular file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except OSError as exc:
        raise LossyLineageError("PLY header cannot be reopened") from exc

    metadata_rows: list[str] = []
    consumed = 0
    found_end = False
    fd_before: os.stat_result | None = None
    fd_after: os.stat_result | None = None
    try:
        fd_before = os.fstat(descriptor)
        if not stat.S_ISREG(fd_before.st_mode) or _cross_surface_signature(
            fd_before
        ) != _cross_surface_signature(before):
            raise LossyLineageError("PLY identity changed before read")
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise

    try:
        with stream:
            first_line = stream.readline()
            if first_line.rstrip(b"\r\n") != b"ply":
                raise LossyLineageError("full_3dgs is not a canonical PLY")
            consumed += len(first_line)
            for raw_line in stream:
                consumed += len(raw_line)
                if consumed > MAX_PLY_HEADER_BYTES:
                    raise LossyLineageError("PLY header exceeds bounded lineage read limit")
                try:
                    line = raw_line.rstrip(b"\r\n").decode("ascii")
                except UnicodeDecodeError as exc:
                    raise LossyLineageError("PLY header is not ASCII") from exc
                prefix = "comment nantai_meta="
                if line.startswith(prefix):
                    metadata_rows.append(line[len(prefix) :])
                if line == "end_header":
                    found_end = True
                    break
            fd_after = os.fstat(stream.fileno())
    except LossyLineageError:
        raise
    except OSError as exc:
        raise LossyLineageError(f"PLY header cannot be reopened: {exc}") from exc

    try:
        after = absolute.lstat()
    except OSError as exc:
        raise LossyLineageError("PLY header cannot be reinspected") from exc

    if (
        fd_after is None
        or fd_before is None
        or _same_surface_signature(fd_before) != _same_surface_signature(fd_after)
        or _cross_surface_signature(fd_after) != _cross_surface_signature(after)
    ):
        raise LossyLineageError("PLY identity changed during read")

    if not found_end:
        raise LossyLineageError("PLY header is missing end_header")
    if len(metadata_rows) > 1:
        raise LossyLineageError("PLY contains duplicate nantai_meta comments")
    if not metadata_rows:
        return None
    try:
        metadata = json.loads(metadata_rows[0])
    except json.JSONDecodeError as exc:
        raise LossyLineageError("PLY nantai_meta is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise LossyLineageError("PLY nantai_meta must be a JSON object")
    value = metadata.get("lossy_edits", _MISSING)
    if value is _MISSING:
        return None
    return normalize_lossy_edits(
        value,
        label="PLY nantai_meta.lossy_edits",
    )


def reconcile_lossy_edits(
    manifest: dict[str, Any],
    full_ply_path: Path,
) -> tuple[list[dict[str, Any]], str | None]:
    """Compare manifest and embedded PLY lineage without trusting either alone."""

    manifest_value = manifest.get("lossy_edits", _MISSING)
    try:
        declared = (
            None
            if manifest_value is _MISSING
            else normalize_lossy_edits(
                manifest_value,
                label="manifest.lossy_edits",
            )
        )
        embedded = read_ply_lossy_edits(full_ply_path)
    except LossyLineageError as exc:
        return [], str(exc)

    # Backward compatibility for historical clean PLYs: absence is accepted
    # only when neither side carries an actual lossy edit.
    if declared is None:
        if embedded:
            return [], (
                "manifest.lossy_edits is missing while PLY nantai_meta.lossy_edits is non-empty"
            )
        return [], None
    if embedded is None:
        if declared:
            return [], (
                "PLY nantai_meta.lossy_edits is missing while manifest.lossy_edits is non-empty"
            )
        return [], None
    if declared != embedded:
        return [], ("manifest.lossy_edits differs from PLY nantai_meta.lossy_edits")
    return declared, None


def summarize_lossy_edits(
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a bounded, human-facing disclosure without inventing units."""

    trim_ids: list[str] = []
    threshold_units: list[str] = []
    dropped = 0
    for edit in edits:
        trim_id = edit.get("trim_id")
        if isinstance(trim_id, str) and trim_id and trim_id not in trim_ids:
            trim_ids.append(trim_id)
        units = edit.get("threshold_units")
        if isinstance(units, str) and units and units not in threshold_units:
            threshold_units.append(units)
        dropped_count = edit.get("dropped")
        if type(dropped_count) is int and dropped_count >= 0:
            dropped += dropped_count
    return {
        "count": len(edits),
        "dropped": dropped,
        "trim_ids": trim_ids,
        "threshold_units": threshold_units,
    }
