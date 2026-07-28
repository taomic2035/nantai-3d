"""Canonical lossy-geometry lineage shared by producers and trust gates.

The full 3DGS PLY carries ``nantai_meta.lossy_edits`` so lineage survives
copying and renaming.  ``recon_manifest.json`` carries the same semantic value
for lightweight consumers.  Production gates must reopen the PLY and compare
both values; a manifest label alone is never evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_PLY_HEADER_BYTES = 16 * 1024 * 1024
_MISSING = object()


class LossyLineageError(ValueError):
    """The declared lossy-edit lineage is malformed or cannot be reopened."""


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
            raise LossyLineageError(
                f"{label}[{index}] must be finite JSON"
            ) from exc
        if item in normalized:
            raise LossyLineageError(f"{label} contains a duplicate edit")
        normalized.append(item)
    return normalized


def read_ply_lossy_edits(path: Path) -> list[dict[str, Any]] | None:
    """Read only the bounded PLY header and return embedded lossy edits.

    ``None`` means the historical PLY has no ``nantai_meta.lossy_edits`` key.
    Malformed, duplicate, oversized, or unterminated metadata fails closed.
    """

    metadata_rows: list[str] = []
    consumed = 0
    found_end = False
    try:
        with Path(path).open("rb") as stream:
            first_line = stream.readline()
            if first_line.rstrip(b"\r\n") != b"ply":
                raise LossyLineageError("full_3dgs is not a canonical PLY")
            consumed += len(first_line)
            for raw_line in stream:
                consumed += len(raw_line)
                if consumed > MAX_PLY_HEADER_BYTES:
                    raise LossyLineageError(
                        "PLY header exceeds bounded lineage read limit"
                    )
                try:
                    line = raw_line.rstrip(b"\r\n").decode("ascii")
                except UnicodeDecodeError as exc:
                    raise LossyLineageError(
                        "PLY header is not ASCII"
                    ) from exc
                prefix = "comment nantai_meta="
                if line.startswith(prefix):
                    metadata_rows.append(line[len(prefix):])
                if line == "end_header":
                    found_end = True
                    break
    except OSError as exc:
        raise LossyLineageError(f"PLY header cannot be reopened: {exc}") from exc

    if not found_end:
        raise LossyLineageError("PLY header is missing end_header")
    if len(metadata_rows) > 1:
        raise LossyLineageError("PLY contains duplicate nantai_meta comments")
    if not metadata_rows:
        return None
    try:
        metadata = json.loads(metadata_rows[0])
    except json.JSONDecodeError as exc:
        raise LossyLineageError(
            "PLY nantai_meta is not valid JSON"
        ) from exc
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
                "manifest.lossy_edits is missing while "
                "PLY nantai_meta.lossy_edits is non-empty"
            )
        return [], None
    if embedded is None:
        if declared:
            return [], (
                "PLY nantai_meta.lossy_edits is missing while "
                "manifest.lossy_edits is non-empty"
            )
        return [], None
    if declared != embedded:
        return [], (
            "manifest.lossy_edits differs from "
            "PLY nantai_meta.lossy_edits"
        )
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
        if (
            isinstance(units, str)
            and units
            and units not in threshold_units
        ):
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
