"""Verified local Brush preview execution evidence.

Brush on macOS/wgpu is a plumbing and visual-preview backend only. A verified
receipt proves exact binary/argv/log/PLY closure; it never becomes production
training evidence and never claims CUDA.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class LocalBrushExecutionError(ValueError):
    """A local Brush receipt or one of its bound artifacts is invalid."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_RECEIPT_BYTES = 1024 * 1024


def _require_utc(value: datetime) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class LocalBrushExecutionReceipt(FrozenModel):
    schema_id: Literal["nantai.local-brush-execution-receipt.v1"] = Field(
        default="nantai.local-brush-execution-receipt.v1",
        alias="schema",
        serialization_alias="schema",
    )
    executor_kind: Literal["local-brush"] = "local-brush"
    quality_role: Literal["preview-only"] = "preview-only"
    brush_stage_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    brush_binary_sha256: str = Field(pattern=_SHA256_PATTERN)
    brush_argv: tuple[str, ...] = Field(min_length=2)
    brush_started_at_utc: datetime
    brush_finished_at_utc: datetime
    returncode: Literal[0]
    brush_log_path: Literal["brush.log"] = "brush.log"
    brush_log_sha256: str = Field(pattern=_SHA256_PATTERN)
    brush_log_size_bytes: int = Field(ge=0)
    brush_export_ply_path: Literal["trained.brush-export.ply"] = (
        "trained.brush-export.ply"
    )
    brush_export_ply_sha256: str = Field(pattern=_SHA256_PATTERN)
    brush_export_ply_size_bytes: int = Field(ge=1)

    _start_utc = field_validator("brush_started_at_utc")(_require_utc)
    _finish_utc = field_validator("brush_finished_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _execution_is_consistent(self) -> LocalBrushExecutionReceipt:
        if self.brush_finished_at_utc < self.brush_started_at_utc:
            raise ValueError("Brush finished before it started")
        if "--export-name" not in self.brush_argv:
            raise ValueError("Brush argv must bind --export-name")
        export_name_index = self.brush_argv.index("--export-name")
        if (
            export_name_index + 1 >= len(self.brush_argv)
            or self.brush_argv[export_name_index + 1] != "trained.ply"
        ):
            raise ValueError("Brush argv must export trained.ply")
        return self


def canonical_local_brush_receipt_bytes(
    receipt: LocalBrushExecutionReceipt,
) -> bytes:
    return (
        json.dumps(
            receipt.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
    )


def _hash_file_stable(
    path: Path,
    *,
    label: str,
    allow_empty: bool,
) -> tuple[int, str]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise LocalBrushExecutionError(f"{label} is missing or link-like")
        digest = hashlib.sha256()
        measured = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                measured += len(chunk)
        after = path.lstat()
    except LocalBrushExecutionError:
        raise
    except OSError as exc:
        raise LocalBrushExecutionError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise LocalBrushExecutionError(f"{label} changed while being hashed")
    if not allow_empty and measured == 0:
        raise LocalBrushExecutionError(f"{label} is empty")
    return measured, digest.hexdigest()


def build_local_brush_execution_receipt(
    *,
    workspace: Path,
    brush_binary: Path,
    brush_argv: list[str],
    brush_stage_fingerprint: str,
    brush_started_at_utc: datetime,
    brush_finished_at_utc: datetime,
    returncode: int,
) -> LocalBrushExecutionReceipt:
    if returncode != 0:
        raise LocalBrushExecutionError(
            "local Brush success receipt requires returncode 0"
        )
    workspace = Path(workspace).expanduser().absolute()
    brush_binary = Path(brush_binary).expanduser().absolute()
    if not brush_argv:
        raise LocalBrushExecutionError("Brush argv is empty")
    try:
        argv_binary = Path(brush_argv[0]).expanduser().absolute()
    except (TypeError, ValueError) as exc:
        raise LocalBrushExecutionError("Brush argv binary is invalid") from exc
    if argv_binary != brush_binary:
        raise LocalBrushExecutionError(
            "Brush argv binary differs from measured binary"
        )
    binary_size, binary_sha = _hash_file_stable(
        brush_binary,
        label="Brush binary",
        allow_empty=False,
    )
    if binary_size <= 0:
        raise LocalBrushExecutionError("Brush binary is empty")
    log_size, log_sha = _hash_file_stable(
        workspace / "brush.log",
        label="Brush log",
        allow_empty=True,
    )
    ply_size, ply_sha = _hash_file_stable(
        workspace / "trained.brush-export.ply",
        label="Brush export PLY",
        allow_empty=False,
    )
    return LocalBrushExecutionReceipt(
        brush_stage_fingerprint=brush_stage_fingerprint,
        brush_binary_sha256=binary_sha,
        brush_argv=tuple(brush_argv),
        brush_started_at_utc=brush_started_at_utc,
        brush_finished_at_utc=brush_finished_at_utc,
        returncode=0,
        brush_log_sha256=log_sha,
        brush_log_size_bytes=log_size,
        brush_export_ply_sha256=ply_sha,
        brush_export_ply_size_bytes=ply_size,
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_local_brush_execution_receipt(
    path: Path,
) -> LocalBrushExecutionReceipt:
    receipt_path = Path(path).expanduser().absolute()
    try:
        before = receipt_path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise LocalBrushExecutionError(
                "local Brush receipt is missing or link-like"
            )
        if before.st_size <= 0 or before.st_size > _MAX_RECEIPT_BYTES:
            raise LocalBrushExecutionError(
                "local Brush receipt size is outside the allowed range"
            )
        raw = receipt_path.read_bytes()
        after = receipt_path.lstat()
    except LocalBrushExecutionError:
        raise
    except OSError as exc:
        raise LocalBrushExecutionError(
            "local Brush receipt cannot be read"
        ) from exc
    if _stat_signature(before) != _stat_signature(after):
        raise LocalBrushExecutionError(
            "local Brush receipt changed while being read"
        )
    try:
        json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        receipt = LocalBrushExecutionReceipt.model_validate_json(raw)
    except (UnicodeError, ValueError) as exc:
        raise LocalBrushExecutionError(
            "local Brush receipt validation failed"
        ) from exc
    if raw != canonical_local_brush_receipt_bytes(receipt):
        raise LocalBrushExecutionError(
            "local Brush receipt is not canonical JSON"
        )
    return receipt


def write_local_brush_execution_receipt(
    path: Path,
    receipt: LocalBrushExecutionReceipt,
) -> None:
    receipt_path = Path(path).expanduser().absolute()
    if receipt_path.exists() or receipt_path.is_symlink():
        existing = load_local_brush_execution_receipt(receipt_path)
        if existing != receipt:
            raise LocalBrushExecutionError(
                "existing local Brush receipt does not match this execution"
            )
        return
    parent = receipt_path.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise LocalBrushExecutionError(
            "local Brush receipt parent is unavailable"
        ) from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise LocalBrushExecutionError(
            "local Brush receipt parent must be a real directory"
        )
    temporary = parent / f".{receipt_path.name}.{uuid.uuid4().hex}.tmp"
    payload = canonical_local_brush_receipt_bytes(receipt)
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt_path)
    except OSError as exc:
        raise LocalBrushExecutionError(
            "local Brush receipt write failed"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def verify_local_brush_execution_receipt(
    path: Path,
    *,
    workspace: Path,
) -> LocalBrushExecutionReceipt:
    receipt = load_local_brush_execution_receipt(path)
    workspace = Path(workspace).expanduser().absolute()
    binary_size, binary_sha = _hash_file_stable(
        Path(receipt.brush_argv[0]).expanduser().absolute(),
        label="Brush binary",
        allow_empty=False,
    )
    if binary_size <= 0 or binary_sha != receipt.brush_binary_sha256:
        raise LocalBrushExecutionError("Brush binary sha256 mismatch")
    log_size, log_sha = _hash_file_stable(
        workspace / receipt.brush_log_path,
        label="Brush log",
        allow_empty=True,
    )
    if (
        log_size != receipt.brush_log_size_bytes
        or log_sha != receipt.brush_log_sha256
    ):
        raise LocalBrushExecutionError("Brush log sha256/length mismatch")
    ply_size, ply_sha = _hash_file_stable(
        workspace / receipt.brush_export_ply_path,
        label="Brush export PLY",
        allow_empty=False,
    )
    if (
        ply_size != receipt.brush_export_ply_size_bytes
        or ply_sha != receipt.brush_export_ply_sha256
    ):
        raise LocalBrushExecutionError(
            "Brush export PLY sha256/length mismatch"
        )
    return receipt
