"""Content-addressed stage journal for the real reconstruction golden path."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.alignment import AlignmentError, load_control_points_json
from pipeline.real_dataset import (
    DatasetEvidenceError,
    canonical_model_bytes,
    load_real_dataset_source,
)


class RealSceneBlockedError(ValueError):
    """A prerequisite or stage has explicit blocked/unknown evidence."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


StageName = Literal[
    "fetch",
    "sfm",
    "train-preview",
    "train-production",
    "import",
    "accept",
    "serve",
]
StageState = Literal["completed", "blocked", "unknown"]
SourceRole = Literal["internal-canary", "production-acceptance"]
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_MAX_RECEIPT_BYTES = 8 * 1024 * 1024


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def _portable_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError("artifact path must be portable and relative")
    return value


class RealSceneSourceIdentity(FrozenModel):
    dataset_id: str = Field(pattern=_ID_PATTERN)
    role: SourceRole
    source_sha256: str = Field(pattern=_SHA256_PATTERN)


@dataclass(frozen=True)
class RealSceneRunOptions:
    workspace_base: Path = Path(".nantai-studio/real-scene")
    run_id: str = "default"
    media_root: Path | None = None
    rights_path: Path | None = None
    policy_path: Path | None = None
    control_points_path: Path | None = None
    geo_origin: tuple[float, float, float] | None = None
    remote_config_path: Path | None = None
    viewer_policy_path: Path | None = None
    viewer_report_path: Path | None = None
    human_review_policy_path: Path | None = None
    human_visual_review_path: Path | None = None
    remote_poll_interval_seconds: float = 15.0
    remote_timeout_seconds: float = 21_600.0
    chunk_size: float = 50.0

    def __post_init__(self) -> None:
        if re.fullmatch(_ID_PATTERN, self.run_id) is None:
            raise ValueError("run_id must be a safe portable identifier")
        if self.remote_poll_interval_seconds <= 0 or self.remote_timeout_seconds <= 0:
            raise ValueError("remote polling intervals must be positive")
        if (
            isinstance(self.chunk_size, bool)
            or not isinstance(self.chunk_size, (int, float))
            or not np.isfinite(float(self.chunk_size))
            or float(self.chunk_size) <= 0
        ):
            raise ValueError("chunk_size must be finite and positive")


class StageArtifactBinding(FrozenModel):
    path: str
    byte_length: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _path = field_validator("path")(_portable_path)


class StagePrerequisiteBinding(FrozenModel):
    stage: StageName
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)


class StageReceipt(FrozenModel):
    schema_id: Literal["nantai.real-scene-stage-receipt.v1"] = Field(
        default="nantai.real-scene-stage-receipt.v1",
        alias="schema",
        serialization_alias="schema",
    )
    dataset_id: str = Field(pattern=_ID_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: StageName
    attempt_id: str = Field(pattern=_ID_PATTERN)
    created_at_utc: datetime
    status: StageState
    prerequisites: tuple[StagePrerequisiteBinding, ...]
    evidence: tuple[StageArtifactBinding, ...] = ()
    outputs: tuple[StageArtifactBinding, ...]
    reason: str | None = None
    alignment_rms_m: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )

    _utc = field_validator("created_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _state_is_consistent(self) -> StageReceipt:
        stages = tuple(item.stage for item in self.prerequisites)
        if len(stages) != len(set(stages)):
            raise ValueError("stage prerequisite receipts must be unique")
        paths = tuple(item.path for item in self.outputs)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("stage output paths must be sorted and unique")
        evidence_paths = tuple(item.path for item in self.evidence)
        if (
            evidence_paths != tuple(sorted(evidence_paths))
            or len(evidence_paths) != len(set(evidence_paths))
            or set(evidence_paths) & set(paths)
        ):
            raise ValueError("stage evidence paths must be sorted, unique, and not outputs")
        if self.status == "completed":
            if not self.outputs or self.reason is not None:
                raise ValueError("completed stage requires outputs and forbids reason")
        elif self.outputs or not self.reason:
            raise ValueError("blocked/unknown stage requires reason and forbids outputs")
        if self.stage != "import" and self.alignment_rms_m is not None:
            raise ValueError("alignment RMS is only valid for import")
        return self


class StageRevalidationFailure(FrozenModel):
    schema_id: Literal["nantai.stage-revalidation-failure.v1"] = Field(
        default="nantai.stage-revalidation-failure.v1",
        alias="schema",
        serialization_alias="schema",
    )
    dataset_id: str = Field(pattern=_ID_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage: StageName
    previous_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    detected_at_utc: datetime
    failure_kind: Literal["artifact-integrity"] = "artifact-integrity"
    reason: str = Field(min_length=1, max_length=4096)

    _utc = field_validator("detected_at_utc")(_require_utc)


@dataclass(frozen=True)
class StageExecution:
    state: StageState
    artifacts: tuple[Path, ...]
    reason: str | None = None
    alignment_rms_m: float | None = None
    evidence_artifacts: tuple[Path, ...] = ()


class RealSceneOperations(Protocol):
    def execute(
        self,
        stage: StageName,
        stage_root: Path,
        prerequisite_receipts: tuple[StageReceipt, ...],
    ) -> StageExecution: ...


def canonical_stage_receipt_bytes(receipt: StageReceipt) -> bytes:
    return (
        json.dumps(
            receipt.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
    )


def _hash_artifact(
    path: Path,
    *,
    workspace: Path,
) -> StageArtifactBinding:
    try:
        relative = path.absolute().relative_to(workspace).as_posix()
    except ValueError as exc:
        raise DatasetEvidenceError("stage artifact escaped the real-scene workspace") from exc
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise DatasetEvidenceError(f"stage artifact is missing or link-like: {relative}")
        digest = hashlib.sha256()
        measured = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                measured += len(chunk)
                digest.update(chunk)
        after = path.lstat()
    except DatasetEvidenceError:
        raise
    except OSError as exc:
        raise DatasetEvidenceError(f"stage artifact cannot be read: {relative}") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise DatasetEvidenceError(f"stage artifact changed while hashing: {relative}")
    return StageArtifactBinding(
        path=relative,
        byte_length=measured,
        sha256=digest.hexdigest(),
    )


def _read_receipt(path: Path) -> tuple[StageReceipt, str]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise DatasetEvidenceError("stage receipt is missing or link-like")
        if before.st_size <= 0 or before.st_size > _MAX_RECEIPT_BYTES:
            raise DatasetEvidenceError("stage receipt size is outside the allowed range")
        raw = path.read_bytes()
        after = path.lstat()
    except DatasetEvidenceError:
        raise
    except OSError as exc:
        raise DatasetEvidenceError("stage receipt cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise DatasetEvidenceError("stage receipt changed while being read")
    digest = hashlib.sha256(raw).hexdigest()
    if path.name != f"{digest}.json":
        raise DatasetEvidenceError("stage receipt filename differs from its sha256")
    try:
        receipt = StageReceipt.model_validate_json(raw)
    except ValueError as exc:
        raise DatasetEvidenceError("stage receipt validation failed") from exc
    if raw != canonical_stage_receipt_bytes(receipt):
        raise DatasetEvidenceError("stage receipt is not canonical JSON")
    return receipt, digest


class RealSceneRunner:
    def __init__(
        self,
        *,
        source: RealSceneSourceIdentity,
        workspace_base: Path,
        operations: RealSceneOperations,
        control_points_path: Path | None = None,
        geo_origin: tuple[float, float, float] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.source = source
        self.operations = operations
        self.control_points_path = control_points_path
        self.geo_origin = geo_origin
        self._now = now or (lambda: datetime.now(UTC))
        self.workspace = (
            Path(workspace_base).expanduser().absolute()
            / source.dataset_id
            / source.source_sha256[:16]
        )
        self.receipt_root = self.workspace / "receipts"

    def _dependency(self, stage: StageName) -> StageName | None:
        if stage == "fetch":
            return None
        if stage == "sfm":
            return "fetch"
        if stage in {"train-preview", "train-production"}:
            return "sfm"
        if stage == "import":
            if self.source.role == "production-acceptance":
                return "train-production"
            # ``all`` intentionally takes the cheap local preview path.  An
            # operator who explicitly completed or attempted production
            # training must not then have ``import`` silently fall back to a
            # different Brush artifact; even blocked/unknown production
            # evidence remains the selected dependency until explicit retry.
            if self._latest("train-production") is not None:
                return "train-production"
            return "train-preview"
        if stage == "accept":
            return "import"
        return "accept"

    def _all_stages(self) -> tuple[StageName, ...]:
        training: StageName = (
            "train-production" if self.source.role == "production-acceptance" else "train-preview"
        )
        return ("fetch", "sfm", training, "import", "accept", "serve")

    def _latest(self, stage: StageName) -> tuple[StageReceipt, str] | None:
        directory = self.receipt_root / stage
        if not directory.exists():
            return None
        try:
            paths = tuple(sorted(directory.glob("*.json")))
        except OSError as exc:
            raise DatasetEvidenceError("stage receipt directory cannot be enumerated") from exc
        receipts = tuple(_read_receipt(path) for path in paths)
        matching = tuple(
            item
            for item in receipts
            if (
                item[0].stage == stage
                and item[0].dataset_id == self.source.dataset_id
                and item[0].source_sha256 == self.source.source_sha256
            )
        )
        if len(matching) != len(receipts):
            raise DatasetEvidenceError("stage receipt directory contains a foreign receipt")
        if not matching:
            return None
        return max(
            matching,
            key=lambda item: (
                item[0].created_at_utc,
                item[0].attempt_id,
            ),
        )

    def _verify_completed(
        self,
        receipt: StageReceipt,
        *,
        seen: frozenset[str] = frozenset(),
    ) -> StageReceipt:
        if receipt.status != "completed":
            raise RealSceneBlockedError(
                f"{receipt.stage} has {receipt.status} evidence; explicit retry is required"
            )
        if (
            receipt.stage == "import"
            and self.source.role == "production-acceptance"
            and (receipt.alignment_rms_m is None or receipt.alignment_rms_m > 0.25)
        ):
            raise RealSceneBlockedError(
                "production import alignment RMS is missing or exceeds 0.25 m"
            )
        for prerequisite in receipt.prerequisites:
            if prerequisite.receipt_sha256 in seen:
                raise DatasetEvidenceError("stage receipt prerequisite cycle detected")
            prerequisite_path = (
                self.receipt_root / prerequisite.stage / f"{prerequisite.receipt_sha256}.json"
            )
            prerequisite_receipt, digest = _read_receipt(prerequisite_path)
            if (
                digest != prerequisite.receipt_sha256
                or prerequisite_receipt.stage != prerequisite.stage
                or prerequisite_receipt.dataset_id != self.source.dataset_id
                or prerequisite_receipt.source_sha256 != self.source.source_sha256
            ):
                raise DatasetEvidenceError("stage prerequisite receipt identity mismatch")
            self._verify_completed(
                prerequisite_receipt,
                seen=seen | {prerequisite.receipt_sha256},
            )
        self._verify_artifact_bindings(receipt.evidence)
        self._verify_artifact_bindings(receipt.outputs)
        return receipt

    def _verify_artifact_bindings(
        self,
        bindings: tuple[StageArtifactBinding, ...],
    ) -> None:
        for expected in bindings:
            actual = _hash_artifact(
                self.workspace / expected.path,
                workspace=self.workspace,
            )
            if actual.sha256 != expected.sha256 or actual.byte_length != expected.byte_length:
                raise DatasetEvidenceError(f"stage artifact sha256/size mismatch: {expected.path}")

    def _write_receipt(self, receipt: StageReceipt) -> tuple[StageReceipt, str]:
        payload = canonical_stage_receipt_bytes(receipt)
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.receipt_root / receipt.stage
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{digest}.json"
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
            raise DatasetEvidenceError("stage receipt cannot be published") from exc
        return _read_receipt(path)

    def _record_completed_revalidation_failure(
        self,
        *,
        stage: StageName,
        previous_receipt_sha256: str,
        error: DatasetEvidenceError,
    ) -> None:
        attempt_id = "attempt-" + uuid.uuid4().hex
        stage_root = self.workspace / "stages" / stage / attempt_id
        reason = f"{stage} completed receipt revalidation failed: {error}"
        failure = StageRevalidationFailure(
            dataset_id=self.source.dataset_id,
            source_sha256=self.source.source_sha256,
            stage=stage,
            previous_receipt_sha256=previous_receipt_sha256,
            detected_at_utc=self._now(),
            reason=reason,
        )
        evidence_path = stage_root / "revalidation-failure.json"
        try:
            stage_root.mkdir(parents=True, exist_ok=False)
            descriptor = os.open(
                evidence_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_model_bytes(failure))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise DatasetEvidenceError(
                "stage revalidation evidence cannot be published"
            ) from exc
        evidence = (
            _hash_artifact(
                evidence_path,
                workspace=self.workspace,
            ),
        )
        receipt = StageReceipt(
            dataset_id=self.source.dataset_id,
            source_sha256=self.source.source_sha256,
            stage=stage,
            attempt_id=attempt_id,
            created_at_utc=failure.detected_at_utc,
            status="blocked",
            prerequisites=(),
            evidence=evidence,
            outputs=(),
            reason=reason,
        )
        self._write_receipt(receipt)
        raise RealSceneBlockedError(
            f"{reason}; receipt: {self.receipt_root / stage}"
        ) from error

    def _preflight_control_points(self) -> None:
        if self.source.role != "production-acceptance":
            return
        if self.control_points_path is None:
            raise RealSceneBlockedError("production import requires measured control points")
        if self.geo_origin is None or not np.all(
            np.isfinite(np.asarray(self.geo_origin, dtype=np.float64))
        ):
            raise RealSceneBlockedError("production import requires a finite ENU geo origin")
        try:
            points = load_control_points_json(self.control_points_path)
        except (AlignmentError, OSError, ValueError) as exc:
            raise RealSceneBlockedError(f"production control points are invalid: {exc}") from exc
        if len(points) < 4:
            raise RealSceneBlockedError("production import requires at least four control points")
        if any(point.derived_from_alignment is not None for point in points):
            raise RealSceneBlockedError("production import requires measured control points")
        explicit = tuple(point.source_xyz for point in points if point.source_xyz is not None)
        if len(explicit) == len(points):
            matrix = np.asarray(explicit, dtype=np.float64)
            if np.linalg.matrix_rank(matrix - matrix.mean(axis=0)) < 3:
                raise RealSceneBlockedError("production control points must be non-coplanar")

    def _run_stage(
        self,
        stage: StageName,
        *,
        resume: bool,
        retry: bool,
    ) -> StageReceipt:
        latest = self._latest(stage)
        if latest is not None:
            receipt, digest = latest
            if receipt.status == "completed":
                try:
                    return self._verify_completed(receipt)
                except DatasetEvidenceError as exc:
                    self._record_completed_revalidation_failure(
                        stage=stage,
                        previous_receipt_sha256=digest,
                        error=exc,
                    )
            self._verify_artifact_bindings(receipt.evidence)
            if not retry:
                raise RealSceneBlockedError(
                    f"{stage} has {receipt.status} evidence "
                    f"({receipt.reason}); explicit retry is required"
                )
        elif resume:
            raise RealSceneBlockedError(f"{stage} has no receipt to resume")

        dependency = self._dependency(stage)
        prerequisite_receipts: tuple[StageReceipt, ...] = ()
        prerequisite_bindings: tuple[StagePrerequisiteBinding, ...] = ()
        if dependency is not None:
            prerequisite = self._run_stage(
                dependency,
                resume=False,
                retry=False,
            )
            latest_dependency = self._latest(dependency)
            assert latest_dependency is not None
            prerequisite_receipts = (prerequisite,)
            prerequisite_bindings = (
                StagePrerequisiteBinding(
                    stage=dependency,
                    receipt_sha256=latest_dependency[1],
                ),
            )

        if stage == "import":
            self._preflight_control_points()
        attempt_id = "attempt-" + uuid.uuid4().hex
        stage_root = self.workspace / "stages" / stage / attempt_id
        execution = self.operations.execute(
            stage,
            stage_root,
            prerequisite_receipts,
        )
        if (
            execution.state == "completed"
            and stage == "import"
            and self.source.role == "production-acceptance"
            and (execution.alignment_rms_m is None or execution.alignment_rms_m > 0.25)
        ):
            execution = StageExecution(
                state="blocked",
                artifacts=(),
                reason=("production import alignment RMS is missing or exceeds 0.25 m"),
                alignment_rms_m=execution.alignment_rms_m,
                evidence_artifacts=(
                    *execution.evidence_artifacts,
                    *execution.artifacts,
                ),
            )
        evidence = tuple(
            sorted(
                (
                    _hash_artifact(
                        Path(path),
                        workspace=self.workspace,
                    )
                    for path in execution.evidence_artifacts
                ),
                key=lambda item: item.path,
            )
        )
        if execution.state == "completed":
            outputs = tuple(
                sorted(
                    (
                        _hash_artifact(
                            Path(path),
                            workspace=self.workspace,
                        )
                        for path in execution.artifacts
                    ),
                    key=lambda item: item.path,
                )
            )
            reason = None
        else:
            outputs = ()
            reason = execution.reason or (f"{stage} returned {execution.state}")
        receipt = StageReceipt(
            dataset_id=self.source.dataset_id,
            source_sha256=self.source.source_sha256,
            stage=stage,
            attempt_id=attempt_id,
            created_at_utc=self._now(),
            status=execution.state,
            prerequisites=prerequisite_bindings,
            evidence=evidence,
            outputs=outputs,
            reason=reason,
            alignment_rms_m=execution.alignment_rms_m,
        )
        receipt, _digest = self._write_receipt(receipt)
        if receipt.status != "completed":
            raise RealSceneBlockedError(
                f"{stage} {receipt.status}: {receipt.reason}; receipt: {self.receipt_root / stage}"
            )
        return receipt

    def run(
        self,
        target: str,
        *,
        resume: bool = False,
        retry: bool = False,
    ) -> StageReceipt:
        if target == "all":
            if self.source.role == "production-acceptance":
                self._preflight_control_points()
            result: StageReceipt | None = None
            for stage in self._all_stages():
                result = self._run_stage(
                    stage,
                    resume=resume,
                    retry=retry,
                )
                resume = False
                retry = False
            assert result is not None
            return result
        if target not in {
            "fetch",
            "sfm",
            "train-preview",
            "train-production",
            "import",
            "accept",
            "serve",
        }:
            raise RealSceneBlockedError(f"unknown real-scene target: {target}")
        if target in {"import", "accept", "serve"} and self.source.role == "production-acceptance":
            self._preflight_control_points()
        return self._run_stage(
            target,  # type: ignore[arg-type]
            resume=resume,
            retry=retry,
        )


def run_real_scene(
    source_path: Path,
    target: str,
    options: RealSceneRunOptions,
    *,
    operations: RealSceneOperations | None = None,
    resume: bool = False,
    retry: bool = False,
) -> StageReceipt:
    """Run one source-bound real-scene stage through the durable journal."""

    source = load_real_dataset_source(Path(source_path))
    source_sha256 = hashlib.sha256(canonical_model_bytes(source)).hexdigest()
    if operations is None:
        from pipeline.real_scene_operations import (
            RealScenePipelineOperations,
        )

        operations = RealScenePipelineOperations(
            source=source,
            options=options,
        )
    runner = RealSceneRunner(
        source=RealSceneSourceIdentity(
            dataset_id=source.dataset_id,
            role=source.role,
            source_sha256=source_sha256,
        ),
        workspace_base=Path(options.workspace_base) / options.run_id,
        operations=operations,
        control_points_path=options.control_points_path,
        geo_origin=options.geo_origin,
    )
    return runner.run(target, resume=resume, retry=retry)
