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
import struct
import subprocess
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
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

from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    RealSceneTrainingError,
    VerifiedTrainingJobBundle,
    load_training_job_input_bytes,
    verify_training_job_bundle,
)
from pipeline.training_executor import (
    ExecutorAttemptReceipt,
    ExecutorInputIdentity,
    ExecutorObservation,
    advance_attempt,
    new_attempt,
)
from pipeline.training_provenance import (
    GpuEnvironment,
    TrainingConfig,
    TrainingRequest,
    TrainingResult,
    build_training_result,
    request_canonical_sha256,
    validate_training_provenance,
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


class LocalBrushExecutorConfig(FrozenModel):
    """Explicit local runtime identity for one preview-only Brush attempt."""

    execution_root: Path
    python_executable: Path
    reconstruct_script: Path
    colmap_binary: Path
    brush_binary: Path
    trainer_version: str = Field(min_length=1)
    total_steps: int = Field(ge=1)
    max_resolution: int = Field(ge=64)
    random_seed: int
    gpu_name: str = Field(min_length=1)
    gpu_memory_mb: int = Field(ge=0)
    driver_version: str = Field(min_length=1)
    timeout_seconds: int = Field(default=21_600, ge=1)

    @model_validator(mode="after")
    def _paths_are_absolute_and_separate(
        self,
    ) -> LocalBrushExecutorConfig:
        path_fields = (
            "execution_root",
            "python_executable",
            "reconstruct_script",
            "colmap_binary",
            "brush_binary",
        )
        for field_name in path_fields:
            if not getattr(self, field_name).is_absolute():
                raise ValueError(f"{field_name} must be absolute")
        identities = (
            self.python_executable,
            self.reconstruct_script,
            self.colmap_binary,
            self.brush_binary,
        )
        if len(set(identities)) != len(identities):
            raise ValueError("local Brush runtime paths must be distinct")
        return self


@dataclass(frozen=True)
class LocalBrushRunResult:
    """Fully revalidated local preview result and its two evidence layers."""

    training_request: TrainingRequest
    training_result: TrainingResult
    receipt: ExecutorAttemptReceipt
    execution_receipt: LocalBrushExecutionReceipt
    execution_root: Path
    photos_root: Path
    precomputed_colmap_root: Path
    workspace: Path
    held_out_names_path: Path
    training_result_path: Path
    attempt_receipt_path: Path


_REQUIRED_SPARSE_BIN = ("cameras.bin", "images.bin", "points3D.bin")
_LOCAL_CONFIG_NAME = "operator-intent-config.yml"
_LOCAL_RESULT_NAME = "training-result.json"
_LOCAL_ATTEMPT_NAME = "executor-attempt.json"


def canonical_local_brush_config_bytes(
    config: LocalBrushExecutorConfig,
) -> bytes:
    """Return JSON-as-YAML bytes binding every Brush consumption argument."""

    payload = {
        "cuda_version": "not-applicable",
        "executor_kind": "local-brush",
        "export_every": config.total_steps,
        "gpu_memory_mb": config.gpu_memory_mb,
        "gpu_name": config.gpu_name,
        "max_resolution": config.max_resolution,
        "quality_role": "preview-only",
        "random_seed": config.random_seed,
        "total_steps": config.total_steps,
        "trainer_name": "brush",
        "trainer_version": config.trainer_version,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _validate_runtime_file(
    path: Path,
    *,
    label: str,
    executable: bool,
    allow_symlink: bool = False,
) -> None:
    try:
        result = path.lstat()
    except OSError as exc:
        raise LocalBrushExecutionError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(result.st_mode):
        if not allow_symlink:
            raise LocalBrushExecutionError(
                f"{label} must be an explicit regular file"
            )
        try:
            resolved = path.resolve(strict=True)
            result = resolved.stat()
        except OSError as exc:
            raise LocalBrushExecutionError(
                f"{label} symlink target is unavailable"
            ) from exc
    if not stat.S_ISREG(result.st_mode):
        raise LocalBrushExecutionError(
            f"{label} must be an explicit regular file"
        )
    if result.st_size <= 0:
        raise LocalBrushExecutionError(f"{label} is empty")
    if executable and not os.access(path, os.X_OK):
        raise LocalBrushExecutionError(f"{label} is not executable")


def _write_new_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise LocalBrushExecutionError(
            f"cannot materialize immutable local input: {path.name}"
        ) from exc


def _read_file_stable(
    path: Path,
    *,
    label: str,
    allow_empty: bool,
) -> bytes:
    size, digest = _hash_file_stable(
        path,
        label=label,
        allow_empty=allow_empty,
    )
    try:
        before = path.lstat()
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise LocalBrushExecutionError(f"{label} cannot be read") from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(payload) != size
        or hashlib.sha256(payload).hexdigest() != digest
    ):
        raise LocalBrushExecutionError(f"{label} changed while being read")
    return payload


def _process_stream_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def _same_verified_bundle(
    expected: VerifiedTrainingJobBundle,
    actual: VerifiedTrainingJobBundle,
) -> bool:
    return (
        expected.bundle_sha256 == actual.bundle_sha256
        and expected.manifest == actual.manifest
        and expected.request == actual.request
        and expected.split == actual.split
        and expected.member_names == actual.member_names
    )


def _prepare_training_only_colmap(
    *,
    bundle: VerifiedTrainingJobBundle,
    execution_root: Path,
    colmap_binary: Path,
    run_command: Callable[..., subprocess.CompletedProcess],
    timeout_seconds: int,
) -> tuple[Path, Path, Path]:
    """Extract train pixels and remove every held-out camera from sparse SfM."""

    photos_root = execution_root / "photos"
    precomputed_root = execution_root / "precomputed-colmap"
    source_sparse = execution_root / "source-sparse"
    filtered_sparse = precomputed_root / "sparse" / "0"
    held_out_names = execution_root / "held-out-names.txt"
    photos_root.mkdir()
    (precomputed_root / "images").mkdir(parents=True)
    source_sparse.mkdir()
    filtered_sparse.mkdir(parents=True)
    _write_new_file(
        held_out_names,
        (
            "".join(
                f"{identity.logical_path}\n"
                for identity in bundle.split.held_out
            )
        ).encode("utf-8"),
    )

    try:
        with zipfile.ZipFile(bundle.path, "r") as archive:
            if tuple(archive.namelist()) != bundle.member_names:
                raise LocalBrushExecutionError(
                    "training archive members changed before extraction"
                )
            for identity in bundle.split.train:
                member_name = f"capture/payload/{identity.logical_path}"
                payload = archive.read(member_name)
                if hashlib.sha256(payload).hexdigest() != identity.sha256:
                    raise LocalBrushExecutionError(
                        f"training pixel sha256 mismatch: "
                        f"{identity.logical_path}"
                    )
                relative = Path(*identity.logical_path.split("/"))
                _write_new_file(photos_root / relative, payload)
                _write_new_file(
                    precomputed_root / "images" / relative,
                    payload,
                )
            for filename in _REQUIRED_SPARSE_BIN:
                _write_new_file(
                    source_sparse / filename,
                    archive.read(f"sfm/sparse/0/{filename}"),
                )
    except LocalBrushExecutionError:
        raise
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise LocalBrushExecutionError(
            "verified training archive cannot be extracted"
        ) from exc

    source_images = _read_file_stable(
        source_sparse / "images.bin",
        label="source sparse images.bin",
        allow_empty=False,
    )
    if len(source_images) < 8:
        raise LocalBrushExecutionError(
            "source sparse images.bin has no registered-image count"
        )
    registered_before = struct.unpack("<Q", source_images[:8])[0]
    expected_after = registered_before - len(bundle.split.held_out)
    if expected_after <= 0:
        raise LocalBrushExecutionError(
            "held-out removal would leave no registered training cameras"
        )

    argv = [
        str(colmap_binary),
        "image_deleter",
        "--input_path",
        str(source_sparse),
        "--output_path",
        str(filtered_sparse),
        "--image_names_path",
        str(held_out_names),
    ]
    try:
        completed = run_command(
            argv,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalBrushExecutionError(
            "COLMAP held-out camera filtering could not run"
        ) from exc
    if completed.returncode != 0:
        raise LocalBrushExecutionError(
            "COLMAP held-out camera filtering exited with code "
            f"{completed.returncode}"
        )
    for filename in _REQUIRED_SPARSE_BIN:
        _hash_file_stable(
            filtered_sparse / filename,
            label=f"filtered sparse {filename}",
            allow_empty=False,
        )
    filtered_images = _read_file_stable(
        filtered_sparse / "images.bin",
        label="filtered sparse images.bin",
        allow_empty=False,
    )
    if len(filtered_images) < 8:
        raise LocalBrushExecutionError(
            "filtered sparse images.bin has no registered-image count"
        )
    registered_after = struct.unpack("<Q", filtered_images[:8])[0]
    if registered_after != expected_after:
        raise LocalBrushExecutionError(
            "COLMAP held-out camera filtering count mismatch: "
            f"expected {expected_after}, observed {registered_after}"
        )
    return photos_root, precomputed_root, held_out_names


class LocalBrushExecutor:
    """One-shot, content-closed local Brush preview adapter."""

    def __init__(
        self,
        config: LocalBrushExecutorConfig,
        *,
        run_command: Callable[
            ..., subprocess.CompletedProcess
        ] = subprocess.run,
    ):
        self.config = config
        self._run_command = run_command

    def run(
        self,
        bundle: VerifiedTrainingJobBundle,
    ) -> LocalBrushRunResult:
        config = self.config
        for path, label, executable in (
            (config.python_executable, "Python executable", True),
            (config.reconstruct_script, "reconstruct_local script", False),
            (config.colmap_binary, "COLMAP binary", True),
            (config.brush_binary, "Brush binary", True),
        ):
            _validate_runtime_file(
                path,
                label=label,
                executable=executable,
                allow_symlink=label == "Python executable",
            )
        execution_root = config.execution_root
        if execution_root.exists() or execution_root.is_symlink():
            raise LocalBrushExecutionError(
                "local Brush execution_root must not already exist"
            )
        try:
            parent = execution_root.parent.lstat()
            if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(
                parent.st_mode
            ):
                raise LocalBrushExecutionError(
                    "local Brush execution_root parent must be a real directory"
                )
            execution_root.mkdir()
        except LocalBrushExecutionError:
            raise
        except OSError as exc:
            raise LocalBrushExecutionError(
                "local Brush execution_root cannot be created"
            ) from exc

        try:
            verified = verify_training_job_bundle(bundle.path)
        except RealSceneTrainingError as exc:
            raise LocalBrushExecutionError(
                f"training bundle verification failed: {exc}"
            ) from exc
        if not _same_verified_bundle(bundle, verified):
            raise LocalBrushExecutionError(
                "training bundle identity differs from supplied verification"
            )
        try:
            input_bytes = load_training_job_input_bytes(verified)
        except RealSceneTrainingError as exc:
            raise LocalBrushExecutionError(
                f"training input closure failed: {exc}"
            ) from exc

        config_bytes = canonical_local_brush_config_bytes(config)
        config_path = execution_root / _LOCAL_CONFIG_NAME
        _write_new_file(config_path, config_bytes)
        config_sha = hashlib.sha256(config_bytes).hexdigest()
        identity_payload = (
            f"{verified.bundle_sha256}:{config_sha}".encode("ascii")
        )
        identity_sha = hashlib.sha256(identity_payload).hexdigest()
        training_config = TrainingConfig(
            trainer_name="brush",
            trainer_version=config.trainer_version,
            max_resolution=config.max_resolution,
            total_steps=config.total_steps,
            export_every=config.total_steps,
            random_seed=config.random_seed,
            extra_config=(
                ("executor_kind", "local-brush"),
                ("quality_role", "preview-only"),
            ),
        )
        request = TrainingRequest(
            request_id=f"local-brush-{identity_sha[:24]}",
            created_at_utc=verified.request.created_at_utc,
            input_bindings=verified.request.input_bindings,
            training_config=training_config,
            expected_output_format="inria-3dgs-ply",
            requested_config_sha256=config_sha,
        )

        photos_root, precomputed_root, held_out_names = (
            _prepare_training_only_colmap(
                bundle=verified,
                execution_root=execution_root,
                colmap_binary=config.colmap_binary,
                run_command=self._run_command,
                timeout_seconds=config.timeout_seconds,
            )
        )
        post_extract = verify_training_job_bundle(verified.path)
        if not _same_verified_bundle(verified, post_extract):
            raise LocalBrushExecutionError(
                "training bundle changed during local input preparation"
            )

        workspace = execution_root / "workspace"
        receipt_path = execution_root / "brush-execution-receipt.json"
        argv = [
            str(config.python_executable),
            str(config.reconstruct_script),
            str(photos_root),
            "--work",
            str(workspace),
            "--steps",
            str(config.total_steps),
            "--max-res",
            str(config.max_resolution),
            "--brush-seed",
            str(config.random_seed),
            "--precomputed-colmap",
            str(precomputed_root),
            "--resume",
            "--stop-after-brush",
            "--receipt-out",
            str(receipt_path),
        ]
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join(
            (
                str(config.brush_binary.parent),
                str(config.colmap_binary.parent),
                environment.get("PATH", ""),
            )
        )
        repo_root = config.reconstruct_script.parent.parent
        environment["PYTHONPATH"] = os.pathsep.join(
            (
                str(repo_root),
                environment.get("PYTHONPATH", ""),
            )
        )
        try:
            completed = self._run_command(
                argv,
                cwd=repo_root,
                env=environment,
                capture_output=True,
                check=False,
                timeout=config.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LocalBrushExecutionError(
                "local Brush reconstruction process could not run"
            ) from exc
        stdout_bytes = _process_stream_bytes(completed.stdout)
        stderr_bytes = _process_stream_bytes(completed.stderr)
        _write_new_file(
            execution_root / "reconstruct.stdout.log",
            stdout_bytes,
        )
        _write_new_file(
            execution_root / "reconstruct.stderr.log",
            stderr_bytes,
        )
        if completed.returncode != 0:
            raise LocalBrushExecutionError(
                "local Brush reconstruction exited with code "
                f"{completed.returncode}"
            )
        if not receipt_path.is_file():
            raise LocalBrushExecutionError(
                "local Brush exited 0 without a verifiable receipt"
            )
        execution_receipt = verify_local_brush_execution_receipt(
            receipt_path,
            workspace=workspace,
        )
        expected_brush_argv = (
            str(config.brush_binary),
            str(workspace),
            "--total-steps",
            str(config.total_steps),
            "--max-resolution",
            str(config.max_resolution),
            "--seed",
            str(config.random_seed),
            "--export-every",
            str(config.total_steps),
            "--export-path",
            str(workspace),
            "--export-name",
            "trained.ply",
        )
        if execution_receipt.brush_argv != expected_brush_argv:
            raise LocalBrushExecutionError(
                "Brush receipt argv differs from the requested config"
            )

        actual_config = _read_file_stable(
            config_path,
            label="local Brush config",
            allow_empty=False,
        )
        if actual_config != config_bytes:
            raise LocalBrushExecutionError(
                "local Brush config changed during execution"
            )
        actual_ply = _read_file_stable(
            workspace / execution_receipt.brush_export_ply_path,
            label="local Brush PLY",
            allow_empty=False,
        )
        actual_log = _read_file_stable(
            workspace / execution_receipt.brush_log_path,
            label="local Brush log",
            allow_empty=True,
        )
        result_id = (
            f"local-brush-{hashlib.sha256(actual_ply).hexdigest()[:24]}"
        )
        training_result = build_training_result(
            request=request,
            result_id=result_id,
            started_at_utc=execution_receipt.brush_started_at_utc,
            finished_at_utc=execution_receipt.brush_finished_at_utc,
            actual_trainer_name="brush",
            actual_trainer_version=config.trainer_version,
            actual_config_bytes=actual_config,
            actual_ply_bytes=actual_ply,
            actual_log_bytes=actual_log,
            input_bytes_by_path=input_bytes,
            gpu_environment=GpuEnvironment(
                gpu_name=config.gpu_name,
                gpu_memory_mb=config.gpu_memory_mb,
                cuda_version="not-applicable",
                driver_version=config.driver_version,
            ),
            exit_code=0,
            actual_ply_path="workspace/trained.brush-export.ply",
            actual_config_path=_LOCAL_CONFIG_NAME,
            actual_log_path="workspace/brush.log",
        )
        try:
            validate_training_provenance(
                training_result,
                request,
                actual_ply_bytes=actual_ply,
                actual_config_bytes=actual_config,
                actual_log_bytes=actual_log,
                input_bytes_by_path=input_bytes,
            )
        except ValueError as exc:
            raise LocalBrushExecutionError(
                f"local Brush result provenance validation failed: {exc}"
            ) from exc

        result_path = execution_root / _LOCAL_RESULT_NAME
        result_bytes = canonical_model_bytes(training_result)
        _write_new_file(result_path, result_bytes)
        result_document_sha = hashlib.sha256(result_bytes).hexdigest()
        request_sha = request_canonical_sha256(request)
        executor_inputs = ExecutorInputIdentity(
            executor_kind="local-brush",
            request_sha256=request_sha,
            dataset_receipt_sha256=verified.manifest.dataset_receipt_sha256,
            training_config_sha256=config_sha,
            trainer_name="brush",
            trainer_version=config.trainer_version,
            job_id=f"local-brush-{identity_sha[:24]}",
        )
        attempt = new_attempt(
            executor_inputs,
            attempt_id=f"attempt-{identity_sha[:24]}",
            created_at_utc=execution_receipt.brush_started_at_utc,
            quality_role="preview-only",
        )
        attempt = advance_attempt(
            attempt,
            ExecutorObservation(
                state="running",
                observed_at_utc=execution_receipt.brush_started_at_utc,
            ),
        )
        attempt = advance_attempt(
            attempt,
            ExecutorObservation(
                state="succeeded",
                observed_at_utc=execution_receipt.brush_finished_at_utc,
                exit_code=0,
                stdout_sha256=hashlib.sha256(
                    stdout_bytes
                ).hexdigest(),
                stderr_sha256=hashlib.sha256(
                    stderr_bytes
                ).hexdigest(),
                result_bundle_sha256=result_document_sha,
            ),
        )
        attempt_path = execution_root / _LOCAL_ATTEMPT_NAME
        _write_new_file(attempt_path, canonical_model_bytes(attempt))

        return LocalBrushRunResult(
            training_request=request,
            training_result=training_result,
            receipt=attempt,
            execution_receipt=execution_receipt,
            execution_root=execution_root,
            photos_root=photos_root,
            precomputed_colmap_root=precomputed_root,
            workspace=workspace,
            held_out_names_path=held_out_names,
            training_result_path=result_path,
            attempt_receipt_path=attempt_path,
        )
