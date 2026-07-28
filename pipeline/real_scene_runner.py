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


@dataclass(frozen=True)
class ResolvedProductionImport:
    workspace_root: Path
    import_root: Path
    import_receipt_path: Path
    stage_receipt_path: Path
    stage_receipt_sha256: str
    source_sha256: str


class RealSceneOperations(Protocol):
    def execute(
        self,
        stage: StageName,
        stage_root: Path,
        prerequisite_receipts: tuple[StageReceipt, ...],
    ) -> StageExecution: ...


# ---------------------------------------------------------------------------
# Snapshot schema (read-only status, no side effects)
# ---------------------------------------------------------------------------


SnapshotStageName = Literal[
    "fetch",
    "sfm",
    "train-preview",
    "train-production",
    "import",
    "accept",
]
SnapshotStageStatus = Literal["missing", "blocked", "unknown", "completed"]
SnapshotReasonCode = Literal[
    "receipt-missing",
    "stage-blocked",
    "stage-unknown",
]
SnapshotState = Literal[
    "blocked",
    "accepted-from-authoritative-decision",
]
SnapshotAcceptanceDecision = Literal[
    "not-reached",
    "allowed-from-authoritative-decision",
]


class SnapshotSourceIdentity(FrozenModel):
    dataset_id: str = Field(pattern=_ID_PATTERN)
    role: SourceRole
    source_sha256: str = Field(pattern=_SHA256_PATTERN)


class SnapshotStageEntry(FrozenModel):
    stage: SnapshotStageName
    status: SnapshotStageStatus
    receipt_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    reason_code: SnapshotReasonCode | None = None

    @model_validator(mode="after")
    def _consistency(self) -> SnapshotStageEntry:
        if self.status == "missing":
            if self.receipt_sha256 is not None or self.reason_code != "receipt-missing":
                raise ValueError("missing stage must have receipt-missing reason")
        elif self.status == "blocked":
            if self.receipt_sha256 is None or self.reason_code != "stage-blocked":
                raise ValueError("blocked stage must have stage-blocked reason")
        elif self.status == "unknown":
            if self.receipt_sha256 is None or self.reason_code != "stage-unknown":
                raise ValueError("unknown stage must have stage-unknown reason")
        elif self.status == "completed":
            if self.receipt_sha256 is None or self.reason_code is not None:
                raise ValueError("completed stage must have sha and no reason")
        return self


class SnapshotEarliestBlocker(FrozenModel):
    stage: SnapshotStageName
    reason_code: SnapshotReasonCode


class SnapshotAcceptanceSummary(FrozenModel):
    decision: SnapshotAcceptanceDecision
    acceptance_source: Literal["real-scene-acceptance", "none"] = "none"
    acceptance_report_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )

    @model_validator(mode="after")
    def _consistency(self) -> SnapshotAcceptanceSummary:
        if self.decision == "not-reached":
            if self.acceptance_source != "none":
                raise ValueError("not-reached requires acceptance_source=none")
            if self.acceptance_report_sha256 is not None:
                raise ValueError("not-reached forbids acceptance_report_sha256")
        elif self.decision == "allowed-from-authoritative-decision":
            if self.acceptance_source != "real-scene-acceptance":
                raise ValueError("allowed requires acceptance_source=real-scene-acceptance")
            if self.acceptance_report_sha256 is None:
                raise ValueError("allowed requires acceptance_report_sha256")
        return self


class RealSceneStageSnapshot(FrozenModel):
    schema_id: Literal["nantai.real-scene-status.v1"] = Field(
        default="nantai.real-scene-status.v1",
        alias="schema",
        serialization_alias="schema",
    )
    state: SnapshotState
    source: SnapshotSourceIdentity
    run_id: str = Field(pattern=_ID_PATTERN)
    stages: tuple[SnapshotStageEntry, ...]
    earliest_blocker: SnapshotEarliestBlocker | None = None
    acceptance: SnapshotAcceptanceSummary
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _consistency(self) -> RealSceneStageSnapshot:
        if len(self.stages) != 5:
            raise ValueError("snapshot must have exactly 5 stages")
        training: SnapshotStageName = (
            "train-production"
            if self.source.role == "production-acceptance"
            else "train-preview"
        )
        expected = (
            "fetch",
            "sfm",
            training,
            "import",
            "accept",
        )
        actual = tuple(entry.stage for entry in self.stages)
        if actual != expected:
            raise ValueError("snapshot stages must match the role chain")
        first_incomplete = next(
            (
                entry
                for entry in self.stages
                if entry.status != "completed"
            ),
            None,
        )
        if self.state == "accepted-from-authoritative-decision":
            if any(entry.status != "completed" for entry in self.stages):
                raise ValueError(
                    "accepted state requires all stages completed"
                )
            if self.acceptance.decision != "allowed-from-authoritative-decision":
                raise ValueError("accepted state requires acceptance allowed")
            if self.earliest_blocker is not None:
                raise ValueError("accepted state forbids earliest_blocker")
        else:
            if first_incomplete is None or self.earliest_blocker is None:
                raise ValueError("blocked state requires an incomplete stage")
            if (
                self.earliest_blocker.stage != first_incomplete.stage
                or self.earliest_blocker.reason_code
                != first_incomplete.reason_code
            ):
                raise ValueError(
                    "earliest_blocker must match the first incomplete stage"
                )
            if self.acceptance.decision != "not-reached":
                raise ValueError("blocked state requires not-reached acceptance")
        if _compute_snapshot_sha256(self) != self.report_sha256:
            raise ValueError("snapshot report_sha256 does not match its content")
        return self


class RealSceneStatusError(ValueError):
    """The real-scene status snapshot is invalid or cannot be derived."""


class _InspectionOnlyOperations:
    """Operations stub that proves snapshot never executes a stage."""

    def execute(
        self,
        stage: StageName,
        stage_root: Path,
        prerequisite_receipts: tuple[StageReceipt, ...],
    ) -> StageExecution:
        raise AssertionError(
            f"snapshot must not execute stage {stage}"
        )


def _is_linklike(path: Path, result: os.stat_result | None = None) -> bool:
    """Return true for symlinks, junctions, and Windows reparse points."""

    try:
        observed = result if result is not None else path.lstat()
    except OSError:
        return path.is_symlink()
    attributes = int(getattr(observed, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return (
        stat.S_ISLNK(observed.st_mode)
        or bool(attributes & reparse_flag)
        or bool(getattr(path, "is_junction", lambda: False)())
    )


def _inspection_path(path: Path) -> Path:
    """Use Win32 extended syntax for I/O without changing receipt paths."""
    if os.name != "nt":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute.lstrip("\\"))
    return Path("\\\\?\\" + absolute)


def _require_real_directory(path: Path) -> None:
    """Reject an existing directory reached through a link-like component."""

    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return
    except (OSError, RuntimeError) as exc:
        raise RealSceneStatusError(
            "real-scene status directory is unavailable"
        ) from exc
    if (
        _is_linklike(path, observed)
        or not stat.S_ISDIR(observed.st_mode)
        or resolved != path.absolute()
    ):
        raise RealSceneStatusError(
            "real-scene status directory is link-like"
        )


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
    workspace = workspace.absolute()
    path = path.absolute()
    try:
        relative = path.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise DatasetEvidenceError("stage artifact escaped the real-scene workspace") from exc
    inspected_workspace = _inspection_path(workspace)
    inspected_path = _inspection_path(path)
    try:
        workspace_real = inspected_workspace.resolve(strict=True)
        resolved_before = inspected_path.resolve(strict=True)
        resolved_before.relative_to(workspace_real)
        before = inspected_path.lstat()
        if (
            _is_linklike(inspected_path, before)
            or not stat.S_ISREG(before.st_mode)
            or resolved_before != inspected_path
        ):
            raise DatasetEvidenceError(f"stage artifact is missing or link-like: {relative}")
        digest = hashlib.sha256()
        measured = 0
        with inspected_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                measured += len(chunk)
                digest.update(chunk)
        after = inspected_path.lstat()
        resolved_after = inspected_path.resolve(strict=True)
    except DatasetEvidenceError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise DatasetEvidenceError(f"stage artifact cannot be read: {relative}") from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or resolved_before != resolved_after
    ):
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


def resolve_latest_production_import(
    source_path: Path,
    *,
    workspace_base: Path,
    run_id: str,
) -> ResolvedProductionImport:
    """Resolve and revalidate the latest completed production import."""

    if re.fullmatch(_ID_PATTERN, run_id) is None:
        raise ValueError("run_id must be a safe portable identifier")
    source = load_real_dataset_source(Path(source_path))
    if source.role != "production-acceptance":
        raise RealSceneBlockedError(
            "latest production import requires a production-acceptance source"
        )
    source_sha256 = hashlib.sha256(
        canonical_model_bytes(source)
    ).hexdigest()
    workspace = (
        Path(workspace_base).expanduser().absolute()
        / run_id
        / source.dataset_id
        / source_sha256[:16]
    )
    try:
        workspace_real = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RealSceneBlockedError(
            "real-scene workspace is unavailable"
        ) from exc
    if workspace_real != workspace or not workspace.is_dir():
        raise RealSceneBlockedError(
            "real-scene workspace must be a real directory"
        )
    receipt_directory = workspace / "receipts/import"
    try:
        directory_real = receipt_directory.resolve(strict=True)
        children = tuple(receipt_directory.iterdir())
    except (OSError, RuntimeError) as exc:
        raise RealSceneBlockedError(
            "production import receipts are unavailable"
        ) from exc
    if (
        directory_real != receipt_directory
        or not receipt_directory.is_dir()
        or not children
    ):
        raise RealSceneBlockedError(
            "production import receipt directory is invalid or empty"
        )
    paths: list[Path] = []
    for child in children:
        try:
            inspected = child.lstat()
        except OSError as exc:
            raise DatasetEvidenceError(
                "production import receipt member cannot be inspected"
            ) from exc
        if (
            child.suffix != ".json"
            or stat.S_ISLNK(inspected.st_mode)
            or not stat.S_ISREG(inspected.st_mode)
        ):
            raise DatasetEvidenceError(
                "production import receipt directory contains a foreign member"
            )
        paths.append(child)
    loaded = tuple(_read_receipt(path) for path in sorted(paths))
    for receipt, _digest in loaded:
        if (
            receipt.stage != "import"
            or receipt.dataset_id != source.dataset_id
            or receipt.source_sha256 != source_sha256
        ):
            raise DatasetEvidenceError(
                "production import receipt directory contains a foreign receipt"
            )
    receipt, digest = max(
        loaded,
        key=lambda item: (
            item[0].created_at_utc,
            item[0].attempt_id,
        ),
    )
    if receipt.status != "completed":
        raise RealSceneBlockedError(
            f"latest production import is {receipt.status}: "
            f"{receipt.reason}"
        )
    for binding in (*receipt.evidence, *receipt.outputs):
        actual = _hash_artifact(
            workspace.joinpath(
                *PurePosixPath(binding.path).parts
            ),
            workspace=workspace,
        )
        if (
            actual.sha256 != binding.sha256
            or actual.byte_length != binding.byte_length
        ):
            raise DatasetEvidenceError(
                f"stage artifact sha256/size mismatch: {binding.path}"
            )
    import_root = (
        workspace / "stages/import" / receipt.attempt_id
    )
    import_receipt_path = import_root / "import-receipt.json"
    expected_relative = import_receipt_path.relative_to(
        workspace
    ).as_posix()
    if (
        sum(
            binding.path == expected_relative
            for binding in receipt.outputs
        )
        != 1
    ):
        raise RealSceneBlockedError(
            "completed production import does not bind exactly one import receipt"
        )
    try:
        from pipeline.real_scene_import import (
            validate_real_scene_import_receipt,
        )

        imported = validate_real_scene_import_receipt(
            import_receipt_path,
            import_root,
        )
    except (OSError, ValueError) as exc:
        raise RealSceneBlockedError(
            f"latest production import cannot be revalidated: {exc}"
        ) from exc
    if imported.source_role != "production-acceptance":
        raise RealSceneBlockedError(
            "latest production import has a non-production source role"
        )
    return ResolvedProductionImport(
        workspace_root=workspace,
        import_root=import_root,
        import_receipt_path=import_receipt_path,
        stage_receipt_path=receipt_directory / f"{digest}.json",
        stage_receipt_sha256=digest,
        source_sha256=source_sha256,
    )


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
        raise RealSceneBlockedError(
            "unknown durable real-scene stage"
        )

    def _all_stages(self) -> tuple[StageName, ...]:
        training: StageName = (
            "train-production" if self.source.role == "production-acceptance" else "train-preview"
        )
        return ("fetch", "sfm", training, "import", "accept")

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

    def _verify_production_import_output(
        self,
        *,
        stage_root: Path,
        artifacts: tuple[Path, ...],
        claimed_alignment_rms_m: float | None,
    ) -> float:
        from pipeline.real_scene_import import (
            RealSceneImportError,
            validate_real_scene_import_receipt,
        )

        root = Path(stage_root).expanduser().absolute()
        expected_receipt = root / "import-receipt.json"
        artifact_paths = tuple(
            Path(path).expanduser().absolute() for path in artifacts
        )
        try:
            for path in artifact_paths:
                path.relative_to(root)
        except ValueError as exc:
            raise RealSceneBlockedError(
                "production import artifacts must stay inside the stage root"
            ) from exc
        if artifact_paths.count(expected_receipt) != 1:
            raise RealSceneBlockedError(
                "production import requires exactly one canonical import receipt"
            )
        try:
            imported = validate_real_scene_import_receipt(
                expected_receipt,
                root,
            )
        except (OSError, RealSceneImportError, ValueError) as exc:
            raise RealSceneBlockedError(
                f"production import receipt revalidation failed: {exc}"
            ) from exc
        if (
            imported.source_role != "production-acceptance"
            or imported.alignment_rms_m is None
            or imported.alignment_measurement_sha256 is None
            or imported.alignment_policy_sha256 is None
            or imported.alignment_decision_sha256 is None
        ):
            raise RealSceneBlockedError(
                "production import receipt has no accepted metric evidence"
            )
        if claimed_alignment_rms_m != imported.alignment_rms_m:
            raise RealSceneBlockedError(
                "production import caller RMS differs from verified receipt"
            )
        return imported.alignment_rms_m

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
        if (
            receipt.stage == "import"
            and self.source.role == "production-acceptance"
        ):
            self._verify_production_import_output(
                stage_root=(
                    self.workspace
                    / "stages"
                    / "import"
                    / receipt.attempt_id
                ),
                artifacts=tuple(
                    self.workspace.joinpath(
                        *PurePosixPath(binding.path).parts
                    )
                    for binding in receipt.outputs
                ),
                claimed_alignment_rms_m=receipt.alignment_rms_m,
            )
        if receipt.stage == "accept":
            self._verify_acceptance_output(receipt)
        return receipt

    def _verify_acceptance_output(self, receipt: StageReceipt) -> None:
        """Reopen the bound acceptance report and re-derive the decision.

        Accept completed receipts must bind exactly one
        ``real-scene-acceptance-<64hex>.json`` output.  The filename's
        SHA must match the bound SHA, and the authoritative validator
        must return a role-appropriate ``allowed`` decision.
        """
        acceptance_name = re.compile(
            r"^real-scene-acceptance-[0-9a-f]{64}\.json$"
        )
        matches = tuple(
            binding for binding in receipt.outputs
            if acceptance_name.fullmatch(PurePosixPath(binding.path).name)
        )
        if len(matches) != 1:
            raise DatasetEvidenceError(
                "accept stage must bind exactly one real-scene-acceptance report"
            )
        binding = matches[0]
        acceptance_path = self.workspace.joinpath(
            *PurePosixPath(binding.path).parts
        )
        if (
            PurePosixPath(binding.path).name
            != f"real-scene-acceptance-{binding.sha256}.json"
        ):
            raise DatasetEvidenceError(
                "acceptance report filename differs from its sha256"
            )
        from pipeline.real_scene_acceptance import (
            RealSceneAcceptanceError,
            validate_real_scene_acceptance,
        )

        if (
            len(receipt.prerequisites) != 1
            or receipt.prerequisites[0].stage != "import"
        ):
            raise DatasetEvidenceError(
                "accept stage must bind exactly one import receipt"
            )
        import_stage_path = (
            self.receipt_root
            / "import"
            / f"{receipt.prerequisites[0].receipt_sha256}.json"
        )
        try:
            import_stage_receipt, import_stage_sha = _read_receipt(
                import_stage_path
            )
            if (
                import_stage_sha
                != receipt.prerequisites[0].receipt_sha256
                or import_stage_receipt.stage != "import"
            ):
                raise DatasetEvidenceError(
                    "accept import prerequisite identity mismatch"
                )
            import_receipts = tuple(
                output
                for output in import_stage_receipt.outputs
                if PurePosixPath(output.path).name
                == "import-receipt.json"
            )
            if len(import_receipts) != 1:
                raise DatasetEvidenceError(
                    "import stage must bind exactly one import receipt output"
                )
            decision = validate_real_scene_acceptance(
                acceptance_path,
                expected_import_receipt_sha256=(
                    import_receipts[0].sha256
                ),
            )
        except DatasetEvidenceError:
            raise
        except (OSError, RealSceneAcceptanceError, ValueError) as exc:
            raise DatasetEvidenceError(
                "acceptance report revalidation failed"
            ) from exc
        if decision.source_role != self.source.role:
            raise DatasetEvidenceError(
                "acceptance decision source_role disagrees with source"
            )
        if decision.report_sha256 != binding.sha256:
            raise DatasetEvidenceError(
                "acceptance decision report_sha256 disagrees with binding"
            )
        allowed = (
            decision.production_release_allowed
            if self.source.role == "production-acceptance"
            else decision.canary_accepted
        )
        if not allowed:
            raise DatasetEvidenceError(
                "acceptance validator did not allow the source role"
            )

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
        recovering: StageReceipt | None = None
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
            if (
                stage == "train-production"
                and receipt.status == "unknown"
                and resume
                and not retry
            ):
                recovering = receipt
            elif not retry:
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

        if (
            recovering is not None
            and recovering.prerequisites != prerequisite_bindings
        ):
            raise RealSceneBlockedError(
                "train-production recovery prerequisite identity differs"
            )
        if stage == "import":
            self._preflight_control_points()
        attempt_id = (
            recovering.attempt_id
            if recovering is not None
            else "attempt-" + uuid.uuid4().hex
        )
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
        elif (
            execution.state == "completed"
            and stage == "import"
            and self.source.role == "production-acceptance"
        ):
            try:
                verified_rms = self._verify_production_import_output(
                    stage_root=stage_root,
                    artifacts=execution.artifacts,
                    claimed_alignment_rms_m=execution.alignment_rms_m,
                )
            except RealSceneBlockedError as exc:
                execution = StageExecution(
                    state="blocked",
                    artifacts=(),
                    reason=str(exc),
                    alignment_rms_m=execution.alignment_rms_m,
                    evidence_artifacts=(
                        *execution.evidence_artifacts,
                        *execution.artifacts,
                    ),
                )
            else:
                execution = StageExecution(
                    state="completed",
                    artifacts=execution.artifacts,
                    alignment_rms_m=verified_rms,
                    evidence_artifacts=execution.evidence_artifacts,
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
        created_at = self._now()
        if (
            recovering is not None
            and created_at <= recovering.created_at_utc
        ):
            created_at = recovering.created_at_utc + timedelta(
                microseconds=1,
            )
        receipt = StageReceipt(
            dataset_id=self.source.dataset_id,
            source_sha256=self.source.source_sha256,
            stage=stage,
            attempt_id=attempt_id,
            created_at_utc=created_at,
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
        }:
            raise RealSceneBlockedError(f"unknown real-scene target: {target}")
        if target in {"import", "accept"} and self.source.role == "production-acceptance":
            self._preflight_control_points()
        return self._run_stage(
            target,  # type: ignore[arg-type]
            resume=resume,
            retry=retry,
        )

    def snapshot_stages(self, *, run_id: str) -> RealSceneStageSnapshot:
        """Return a read-only 5-stage status snapshot without side effects.

        Walks the journal for each stage in the role-specific 5-stage chain
        (serve excluded).  Completed stages are revalidated via
        ``_verify_completed``; blocked/unknown stages re-check receipt
        canonical bytes and evidence artifact bindings.  Accept completed
        stages are revalidated through ``validate_real_scene_acceptance``.
        Any source/receipt/artifact/TOCTOU invalid raises
        ``RealSceneStatusError``.
        """
        if re.fullmatch(_ID_PATTERN, run_id) is None:
            raise RealSceneStatusError("run_id must be a safe portable identifier")

        training: StageName = (
            "train-production"
            if self.source.role == "production-acceptance"
            else "train-preview"
        )
        chain: tuple[StageName, ...] = (
            "fetch",
            "sfm",
            training,
            "import",
            "accept",
        )
        for directory in (
            self.workspace,
            self.receipt_root,
            *(
                self.receipt_root / stage
                for stage in chain
            ),
        ):
            _require_real_directory(directory)
        entries: list[SnapshotStageEntry] = []
        completed_receipts: list[tuple[StageReceipt, str]] = []
        earliest: SnapshotEarliestBlocker | None = None
        acceptance_report_sha: str | None = None
        for stage in chain:
            try:
                latest = self._latest(stage)
            except (
                DatasetEvidenceError,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                raise RealSceneStatusError(
                    "real-scene stage journal is invalid"
                ) from exc
            if latest is None:
                entry = SnapshotStageEntry(
                    stage=stage,  # type: ignore[arg-type]
                    status="missing",
                    reason_code="receipt-missing",
                )
                if earliest is None:
                    earliest = SnapshotEarliestBlocker(
                        stage=stage,  # type: ignore[arg-type]
                        reason_code="receipt-missing",
                    )
                entries.append(entry)
                continue
            receipt, digest = latest
            if receipt.status == "blocked":
                try:
                    self._verify_artifact_bindings(receipt.evidence)
                except (DatasetEvidenceError, OSError, ValueError) as exc:
                    raise RealSceneStatusError(
                        "blocked stage evidence is invalid"
                    ) from exc
                entry = SnapshotStageEntry(
                    stage=stage,  # type: ignore[arg-type]
                    status="blocked",
                    receipt_sha256=digest,
                    reason_code="stage-blocked",
                )
                if earliest is None:
                    earliest = SnapshotEarliestBlocker(
                        stage=stage,  # type: ignore[arg-type]
                        reason_code="stage-blocked",
                    )
                entries.append(entry)
                continue
            if receipt.status == "unknown":
                try:
                    self._verify_artifact_bindings(receipt.evidence)
                except (DatasetEvidenceError, OSError, ValueError) as exc:
                    raise RealSceneStatusError(
                        "unknown stage evidence is invalid"
                    ) from exc
                entry = SnapshotStageEntry(
                    stage=stage,  # type: ignore[arg-type]
                    status="unknown",
                    receipt_sha256=digest,
                    reason_code="stage-unknown",
                )
                if earliest is None:
                    earliest = SnapshotEarliestBlocker(
                        stage=stage,  # type: ignore[arg-type]
                        reason_code="stage-unknown",
                    )
                entries.append(entry)
                continue
            try:
                self._verify_completed(receipt)
            except (
                DatasetEvidenceError,
                RealSceneBlockedError,
            ) as exc:
                raise RealSceneStatusError(
                    f"{stage} completed receipt is invalid"
                ) from exc
            if stage == "accept":
                acceptance_report_sha = self._snapshot_acceptance_sha(receipt)
            completed_receipts.append((receipt, digest))
            entries.append(
                SnapshotStageEntry(
                    stage=stage,  # type: ignore[arg-type]
                    status="completed",
                    receipt_sha256=digest,
                )
            )
        all_completed = all(
            entry.status == "completed" for entry in entries
        )
        if all_completed:
            for index, (receipt, _digest) in enumerate(
                completed_receipts
            ):
                expected_prerequisites = (
                    ()
                    if index == 0
                    else (
                        StagePrerequisiteBinding(
                            stage=completed_receipts[index - 1][0].stage,
                            receipt_sha256=completed_receipts[index - 1][1],
                        ),
                    )
                )
                if receipt.prerequisites != expected_prerequisites:
                    raise RealSceneStatusError(
                        "latest completed stages do not form a coherent chain"
                    )
        if all_completed and acceptance_report_sha is not None:
            state: SnapshotState = "accepted-from-authoritative-decision"
            acceptance = SnapshotAcceptanceSummary(
                decision="allowed-from-authoritative-decision",
                acceptance_source="real-scene-acceptance",
                acceptance_report_sha256=acceptance_report_sha,
            )
            blocker: SnapshotEarliestBlocker | None = None
        else:
            state = "blocked"
            acceptance = SnapshotAcceptanceSummary(decision="not-reached")
            blocker = earliest
        signing_payload: dict[str, object] = {
            "schema": "nantai.real-scene-status.v1",
            "state": state,
            "source": self.source.model_dump(mode="json"),
            "run_id": run_id,
            "stages": [
                entry.model_dump(mode="json", by_alias=True)
                for entry in entries
            ],
            "earliest_blocker": (
                blocker.model_dump(mode="json", by_alias=True)
                if blocker is not None
                else None
            ),
            "acceptance": acceptance.model_dump(mode="json", by_alias=True),
        }
        report_sha = hashlib.sha256(
            _canonical_json_bytes(signing_payload)
        ).hexdigest()
        snapshot = RealSceneStageSnapshot(
            state=state,
            source=SnapshotSourceIdentity(
                dataset_id=self.source.dataset_id,
                role=self.source.role,
                source_sha256=self.source.source_sha256,
            ),
            run_id=run_id,
            stages=tuple(entries),
            earliest_blocker=blocker,
            acceptance=acceptance,
            report_sha256=report_sha,
        )
        if _compute_snapshot_sha256(snapshot) != report_sha:
            raise RealSceneStatusError("snapshot sha256 round-trip failed")
        return snapshot

    def _snapshot_acceptance_sha(self, receipt: StageReceipt) -> str:
        """Return the bound acceptance report SHA for a completed accept stage."""
        acceptance_name = re.compile(
            r"^real-scene-acceptance-[0-9a-f]{64}\.json$"
        )
        matches = tuple(
            binding for binding in receipt.outputs
            if acceptance_name.fullmatch(PurePosixPath(binding.path).name)
        )
        if len(matches) != 1:
            raise RealSceneStatusError(
                "accept stage must bind exactly one acceptance report"
            )
        return matches[0].sha256


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


def _canonical_json_bytes(payload: object) -> bytes:
    import json

    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def canonical_snapshot_bytes(snapshot: RealSceneStageSnapshot) -> bytes:
    """Canonical JSON bytes of a status snapshot (including ``report_sha256``)."""
    return _canonical_json_bytes(
        snapshot.model_dump(mode="json", by_alias=True)
    )


def _compute_snapshot_sha256(snapshot: RealSceneStageSnapshot) -> str:
    payload = snapshot.model_dump(mode="json", by_alias=True)
    payload.pop("report_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def snapshot_real_scene_stages(
    source_path: Path,
    *,
    workspace_base: Path,
    run_id: str,
) -> RealSceneStageSnapshot:
    """Read-only 5-stage status snapshot for one source-bound real-scene run.

    Loads the source, derives its canonical SHA, constructs a runner with
    an ``_InspectionOnlyOperations`` stub (so any accidental stage
    execution raises ``AssertionError``), and returns the snapshot.
    No network, no training, no publish, no side effects.
    """
    source_file = Path(source_path).expanduser().absolute()
    try:
        before = source_file.lstat()
        if _is_linklike(source_file, before) or not stat.S_ISREG(
            before.st_mode
        ):
            raise RealSceneStatusError(
                "real-scene status source is link-like"
            )
        source = load_real_dataset_source(source_file)
        after = source_file.lstat()
    except RealSceneStatusError:
        raise
    except (DatasetEvidenceError, OSError, RuntimeError, ValueError) as exc:
        raise RealSceneStatusError(
            "real-scene status source is invalid"
        ) from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneStatusError(
            "real-scene status source changed while read"
        )
    source_sha256 = hashlib.sha256(canonical_model_bytes(source)).hexdigest()
    workspace = Path(workspace_base).expanduser().absolute()
    _require_real_directory(workspace)
    runner = RealSceneRunner(
        source=RealSceneSourceIdentity(
            dataset_id=source.dataset_id,
            role=source.role,
            source_sha256=source_sha256,
        ),
        workspace_base=workspace / run_id,
        operations=_InspectionOnlyOperations(),
    )
    return runner.snapshot_stages(run_id=run_id)
