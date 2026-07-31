"""Provider-neutral training execution state and resume evidence.

This module records only content identities and bounded observations. It never
stores credentials, private hostnames, environment dumps or raw logs. An
unreachable or unverifiable job remains ``unknown``; terminal success requires
a locally verified result-bundle identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.real_scene_training import VerifiedTrainingJobBundle


class TrainingExecutorError(ValueError):
    """Executor evidence is inconsistent, unsafe or not resumable."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


Sha256 = str
ExecutorKind = Literal["local-brush", "remote-shell-nerfstudio"]
ExecutorState = Literal[
    "not-started",
    "running",
    "succeeded",
    "failed",
    "unknown",
]
QualityRole = Literal["preview-only", "production"]
ResumeDecision = Literal["reuse", "retry", "block-unknown"]
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_MAX_JOURNAL_BYTES = 8 * 1024 * 1024
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _require_utc(value: datetime) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


class ExecutorInputIdentity(FrozenModel):
    """Every identity that must match before an attempt can be resumed."""

    executor_kind: ExecutorKind
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    trainer_name: str = Field(min_length=1)
    trainer_version: str = Field(min_length=1)
    job_id: str = Field(pattern=_ID_PATTERN)

    @model_validator(mode="after")
    def _executor_matches_trainer(self) -> ExecutorInputIdentity:
        if (
            self.executor_kind == "local-brush"
            and self.trainer_name != "brush"
        ):
            raise ValueError("local-brush executor requires trainer_name=brush")
        if (
            self.executor_kind == "remote-shell-nerfstudio"
            and self.trainer_name != "nerfstudio-splatfacto"
        ):
            raise ValueError(
                "remote-shell executor requires nerfstudio-splatfacto"
            )
        return self


class ExecutorObservation(FrozenModel):
    """One bounded poll/fetch observation with hashes instead of raw logs."""

    state: ExecutorState
    observed_at_utc: datetime
    exit_code: int | None = None
    stdout_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    stderr_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    result_bundle_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )

    _utc_timestamp = field_validator("observed_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _state_evidence_is_consistent(self) -> ExecutorObservation:
        log_hashes = (self.stdout_sha256, self.stderr_sha256)
        if self.state in {"not-started", "running"}:
            if (
                self.exit_code is not None
                or any(value is not None for value in log_hashes)
                or self.result_bundle_sha256 is not None
            ):
                raise ValueError(
                    f"{self.state} observation cannot claim terminal evidence"
                )
        elif self.state == "succeeded":
            if (
                self.exit_code != 0
                or any(value is None for value in log_hashes)
                or self.result_bundle_sha256 is None
            ):
                raise ValueError(
                    "succeeded observation requires exit 0, log hashes and "
                    "a verified result bundle"
                )
        elif self.state == "failed":
            if (
                self.exit_code is None
                or self.exit_code == 0
                or any(value is None for value in log_hashes)
                or self.result_bundle_sha256 is not None
            ):
                raise ValueError(
                    "failed observation requires nonzero exit and log hashes"
                )
        elif self.result_bundle_sha256 is not None:
            raise ValueError(
                "unknown observation cannot claim a verified result bundle"
            )
        return self


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "not-started": frozenset({"running", "unknown", "failed"}),
    "running": frozenset(
        {"running", "succeeded", "failed", "unknown"}
    ),
    "unknown": frozenset(
        {"unknown", "running", "succeeded", "failed"}
    ),
    "succeeded": frozenset(),
    "failed": frozenset(),
}


def _validate_observation_history(
    observations: tuple[ExecutorObservation, ...],
) -> None:
    if not observations or observations[0].state != "not-started":
        raise ValueError("attempt observations must start at not-started")
    for previous, current in zip(observations, observations[1:], strict=False):
        if current.observed_at_utc < previous.observed_at_utc:
            raise ValueError("attempt observation timestamps must be monotonic")
        if current.state not in _ALLOWED_TRANSITIONS[previous.state]:
            raise ValueError(
                f"forbidden executor state transition "
                f"{previous.state}->{current.state}"
            )


class ExecutorAttemptReceipt(FrozenModel):
    """Append-only evidence for one immutable executor attempt identity."""

    schema_id: Literal["nantai.executor-attempt-receipt.v1"] = Field(
        default="nantai.executor-attempt-receipt.v1",
        alias="schema",
        serialization_alias="schema",
    )
    executor_kind: ExecutorKind
    quality_role: QualityRole
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    trainer_name: str = Field(min_length=1)
    trainer_version: str = Field(min_length=1)
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    created_at_utc: datetime
    state: ExecutorState
    last_observed_at_utc: datetime
    exit_code: int | None = None
    stdout_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    stderr_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    result_bundle_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    observations: tuple[ExecutorObservation, ...] = Field(min_length=1)

    _created_utc = field_validator("created_at_utc")(_require_utc)
    _observed_utc = field_validator("last_observed_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _receipt_is_derived_from_observations(
        self,
    ) -> ExecutorAttemptReceipt:
        _validate_observation_history(self.observations)
        first = self.observations[0]
        last = self.observations[-1]
        if first.observed_at_utc != self.created_at_utc:
            raise ValueError(
                "created_at_utc must equal the initial observation timestamp"
            )
        mirrored = (
            self.state,
            self.last_observed_at_utc,
            self.exit_code,
            self.stdout_sha256,
            self.stderr_sha256,
            self.result_bundle_sha256,
        )
        expected = (
            last.state,
            last.observed_at_utc,
            last.exit_code,
            last.stdout_sha256,
            last.stderr_sha256,
            last.result_bundle_sha256,
        )
        if mirrored != expected:
            raise ValueError(
                "attempt summary must equal its latest observation"
            )
        if (
            self.executor_kind == "local-brush"
            and (
                self.quality_role != "preview-only"
                or self.trainer_name != "brush"
            )
        ):
            raise ValueError(
                "local-brush attempts are always brush preview-only"
            )
        if (
            self.executor_kind == "remote-shell-nerfstudio"
            and (
                self.quality_role != "production"
                or self.trainer_name != "nerfstudio-splatfacto"
            )
        ):
            raise ValueError(
                "remote-shell attempts require production Splatfacto"
            )
        return self

    @property
    def input_identity(self) -> ExecutorInputIdentity:
        return ExecutorInputIdentity(
            executor_kind=self.executor_kind,
            request_sha256=self.request_sha256,
            dataset_receipt_sha256=self.dataset_receipt_sha256,
            training_config_sha256=self.training_config_sha256,
            trainer_name=self.trainer_name,
            trainer_version=self.trainer_version,
            job_id=self.job_id,
        )


@dataclass(frozen=True)
class ExecutorJobBundle:
    bundle: VerifiedTrainingJobBundle
    input_identity: ExecutorInputIdentity


class ExecutorJobRef(FrozenModel):
    executor_kind: ExecutorKind
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    submitted_at_utc: datetime

    _utc_timestamp = field_validator("submitted_at_utc")(_require_utc)


class TrainingExecutor(Protocol):
    def prepare(
        self,
        bundle: VerifiedTrainingJobBundle,
    ) -> ExecutorJobBundle: ...

    def submit(self, bundle: ExecutorJobBundle) -> ExecutorJobRef: ...

    def poll(self, job: ExecutorJobRef) -> ExecutorObservation: ...

    def fetch(
        self,
        job: ExecutorJobRef,
        destination: Path,
    ) -> ExecutorAttemptReceipt: ...


def normalize_poll_result(
    *,
    exit_code: int | None,
    reachable: bool,
    observed_at_utc: datetime | None = None,
    outputs_verified: bool = False,
    stdout_sha256: str | None = None,
    stderr_sha256: str | None = None,
    result_bundle_sha256: str | None = None,
) -> ExecutorObservation:
    """Normalize process/network evidence without inventing a terminal state."""

    observed_at = observed_at_utc or datetime.now(UTC)
    if not reachable:
        return ExecutorObservation(
            state="unknown",
            observed_at_utc=observed_at,
            exit_code=exit_code,
            stdout_sha256=stdout_sha256,
            stderr_sha256=stderr_sha256,
        )
    if exit_code is None:
        return ExecutorObservation(
            state="running",
            observed_at_utc=observed_at,
        )
    if exit_code != 0:
        return ExecutorObservation(
            state="failed",
            observed_at_utc=observed_at,
            exit_code=exit_code,
            stdout_sha256=stdout_sha256 or _EMPTY_SHA256,
            stderr_sha256=stderr_sha256 or _EMPTY_SHA256,
        )
    if not outputs_verified:
        return ExecutorObservation(
            state="unknown",
            observed_at_utc=observed_at,
            exit_code=0,
            stdout_sha256=stdout_sha256,
            stderr_sha256=stderr_sha256,
        )
    return ExecutorObservation(
        state="succeeded",
        observed_at_utc=observed_at,
        exit_code=0,
        stdout_sha256=stdout_sha256 or _EMPTY_SHA256,
        stderr_sha256=stderr_sha256 or _EMPTY_SHA256,
        result_bundle_sha256=result_bundle_sha256,
    )


def new_attempt(
    inputs: ExecutorInputIdentity,
    *,
    attempt_id: str,
    created_at_utc: datetime,
    quality_role: QualityRole,
) -> ExecutorAttemptReceipt:
    initial = ExecutorObservation(
        state="not-started",
        observed_at_utc=created_at_utc,
    )
    return ExecutorAttemptReceipt(
        executor_kind=inputs.executor_kind,
        quality_role=quality_role,
        request_sha256=inputs.request_sha256,
        dataset_receipt_sha256=inputs.dataset_receipt_sha256,
        training_config_sha256=inputs.training_config_sha256,
        trainer_name=inputs.trainer_name,
        trainer_version=inputs.trainer_version,
        job_id=inputs.job_id,
        attempt_id=attempt_id,
        created_at_utc=created_at_utc,
        state=initial.state,
        last_observed_at_utc=initial.observed_at_utc,
        exit_code=initial.exit_code,
        stdout_sha256=initial.stdout_sha256,
        stderr_sha256=initial.stderr_sha256,
        result_bundle_sha256=initial.result_bundle_sha256,
        observations=(initial,),
    )


def advance_attempt(
    previous: ExecutorAttemptReceipt,
    observation: ExecutorObservation,
) -> ExecutorAttemptReceipt:
    if observation.observed_at_utc < previous.last_observed_at_utc:
        raise TrainingExecutorError(
            "executor observation timestamp precedes previous evidence"
        )
    if observation.state not in _ALLOWED_TRANSITIONS[previous.state]:
        raise TrainingExecutorError(
            f"forbidden executor state transition "
            f"{previous.state}->{observation.state}"
        )
    candidate = previous.model_copy(
        update={
            "state": observation.state,
            "last_observed_at_utc": observation.observed_at_utc,
            "exit_code": observation.exit_code,
            "stdout_sha256": observation.stdout_sha256,
            "stderr_sha256": observation.stderr_sha256,
            "result_bundle_sha256": observation.result_bundle_sha256,
            "observations": (*previous.observations, observation),
        },
    )
    try:
        return ExecutorAttemptReceipt.model_validate_json(
            candidate.model_dump_json(by_alias=True)
        )
    except ValueError as exc:
        raise TrainingExecutorError(
            f"advanced attempt evidence is invalid: {exc}"
        ) from exc


def resume_decision(
    previous: ExecutorAttemptReceipt,
    current: ExecutorInputIdentity,
) -> ResumeDecision:
    if previous.input_identity != current:
        return "retry"
    if previous.state == "unknown":
        return "block-unknown"
    if previous.state == "failed":
        return "retry"
    return "reuse"


def retry_attempt(
    previous: ExecutorAttemptReceipt,
    current: ExecutorInputIdentity,
    *,
    attempt_id: str,
    created_at_utc: datetime,
) -> ExecutorAttemptReceipt:
    if attempt_id == previous.attempt_id:
        raise TrainingExecutorError("retry requires a new attempt_id")
    if resume_decision(previous, current) != "retry":
        raise TrainingExecutorError(
            "retry is not allowed for reusable or unknown evidence"
        )
    return new_attempt(
        current,
        attempt_id=attempt_id,
        created_at_utc=created_at_utc,
        quality_role=previous.quality_role,
    )


class RealSceneJournal(FrozenModel):
    """Canonical local journal retaining every immutable attempt observation."""

    schema_id: Literal["nantai.real-scene-journal.v1"] = Field(
        default="nantai.real-scene-journal.v1",
        alias="schema",
        serialization_alias="schema",
    )
    run_id: str = Field(pattern=_ID_PATTERN)
    created_at_utc: datetime
    attempts: tuple[ExecutorAttemptReceipt, ...] = ()

    _utc_timestamp = field_validator("created_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _attempts_are_unique_and_ordered(self) -> RealSceneJournal:
        attempt_ids = tuple(attempt.attempt_id for attempt in self.attempts)
        if len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("journal attempt IDs must be unique")
        timestamps = tuple(
            attempt.created_at_utc for attempt in self.attempts
        )
        if timestamps != tuple(sorted(timestamps)):
            raise ValueError("journal attempts must be chronologically ordered")
        if any(timestamp < self.created_at_utc for timestamp in timestamps):
            raise ValueError("journal attempt predates the journal")
        return self


def canonical_real_scene_journal_bytes(
    journal: RealSceneJournal,
) -> bytes:
    return (
        json.dumps(
            journal.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _reject_duplicate_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _is_linklike(
    path: Path,
    observed: os.stat_result,
) -> bool:
    reparse_flag = getattr(
        stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        0x400,
    )
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0))
        & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def load_real_scene_journal(path: Path) -> RealSceneJournal:
    journal_path = Path(path).expanduser().absolute()
    try:
        before = journal_path.lstat()
        if (
            _is_linklike(journal_path, before)
            or not stat.S_ISREG(before.st_mode)
        ):
            raise TrainingExecutorError(
                "real-scene journal is missing or link-like"
            )
        if before.st_size <= 0 or before.st_size > _MAX_JOURNAL_BYTES:
            raise TrainingExecutorError(
                "real-scene journal size is outside the allowed range"
            )
        descriptor = os.open(
            journal_path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            stream = os.fdopen(descriptor, "rb", buffering=0)
        except OSError:
            os.close(descriptor)
            raise
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(descriptor_before.st_mode)
                or _stat_signature(descriptor_before) != _stat_signature(before)
            ):
                raise TrainingExecutorError(
                    "real-scene journal changed before read"
                )
            raw = stream.read(_MAX_JOURNAL_BYTES + 1)
            descriptor_after = os.fstat(stream.fileno())
        after = journal_path.lstat()
    except TrainingExecutorError:
        raise
    except OSError as exc:
        raise TrainingExecutorError(
            "real-scene journal cannot be read"
        ) from exc
    if (
        _stat_signature(descriptor_before) != _stat_signature(descriptor_after)
        or _stat_signature(before) != _stat_signature(after)
        or len(raw) > _MAX_JOURNAL_BYTES
        or len(raw) != before.st_size
    ):
        raise TrainingExecutorError(
            "real-scene journal changed while being read"
        )
    try:
        json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_reject_duplicate_json_pairs,
        )
        journal = RealSceneJournal.model_validate_json(raw)
    except (UnicodeError, ValueError) as exc:
        raise TrainingExecutorError(
            "real-scene journal validation failed"
        ) from exc
    if raw != canonical_real_scene_journal_bytes(journal):
        raise TrainingExecutorError(
            "real-scene journal is not canonical JSON"
        )
    return journal


def _atomic_write_journal(path: Path, payload: bytes) -> None:
    from pipeline.durable_io import DurableIOError, atomic_replace

    parent = path.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise TrainingExecutorError(
            "real-scene journal parent is unavailable"
        ) from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise TrainingExecutorError(
            "real-scene journal parent must be a real directory"
        )
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
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
        atomic_replace(temporary, path)
    except DurableIOError as exc:
        state = (
            "published but durability is unconfirmed"
            if exc.published
            else "not published"
        )
        raise TrainingExecutorError(
            f"real-scene journal atomic write failed ({state})"
        ) from exc
    except OSError as exc:
        raise TrainingExecutorError(
            "real-scene journal atomic write failed"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def create_or_load_real_scene_journal(
    path: Path,
    expected: RealSceneJournal,
) -> RealSceneJournal:
    expected = _revalidate_journal_object(
        expected,
        label="expected journal",
    )
    journal_path = Path(path).expanduser().absolute()
    if journal_path.exists() or journal_path.is_symlink():
        current = load_real_scene_journal(journal_path)
        if current != expected:
            raise TrainingExecutorError(
                "existing journal is not matching expected creation evidence"
            )
        return current
    _atomic_write_journal(
        journal_path,
        canonical_real_scene_journal_bytes(expected),
    )
    return load_real_scene_journal(journal_path)


def _revalidate_journal_object(
    journal: RealSceneJournal,
    *,
    label: str,
) -> RealSceneJournal:
    try:
        validated = RealSceneJournal.model_validate_json(
            canonical_real_scene_journal_bytes(journal)
        )
    except ValueError as exc:
        raise TrainingExecutorError(f"{label} is invalid: {exc}") from exc
    if validated != journal:
        raise TrainingExecutorError(
            f"{label} differs after full model validation"
        )
    return validated


def _receipt_advances(
    previous: ExecutorAttemptReceipt,
    updated: ExecutorAttemptReceipt,
) -> bool:
    immutable_fields = (
        "executor_kind",
        "quality_role",
        "request_sha256",
        "dataset_receipt_sha256",
        "training_config_sha256",
        "trainer_name",
        "trainer_version",
        "job_id",
        "attempt_id",
        "created_at_utc",
    )
    if any(
        getattr(previous, field) != getattr(updated, field)
        for field in immutable_fields
    ):
        return False
    return (
        len(updated.observations) > len(previous.observations)
        and updated.observations[: len(previous.observations)]
        == previous.observations
    )


def _journal_advances(
    previous: RealSceneJournal,
    updated: RealSceneJournal,
) -> bool:
    if (
        previous.run_id != updated.run_id
        or previous.created_at_utc != updated.created_at_utc
    ):
        return False
    if len(updated.attempts) == len(previous.attempts):
        if not previous.attempts:
            return False
        return (
            updated.attempts[:-1] == previous.attempts[:-1]
            and _receipt_advances(
                previous.attempts[-1],
                updated.attempts[-1],
            )
        )
    if len(updated.attempts) == len(previous.attempts) + 1:
        if (
            updated.attempts[:-1] != previous.attempts
            or updated.attempts[-1].state != "not-started"
        ):
            return False
        if not previous.attempts:
            return True
        return (
            resume_decision(
                previous.attempts[-1],
                updated.attempts[-1].input_identity,
            )
            == "retry"
        )
    return False


def write_real_scene_journal(
    path: Path,
    *,
    previous: RealSceneJournal,
    updated: RealSceneJournal,
) -> None:
    previous = _revalidate_journal_object(
        previous,
        label="previous journal",
    )
    updated = _revalidate_journal_object(
        updated,
        label="updated journal",
    )
    journal_path = Path(path).expanduser().absolute()
    try:
        live = load_real_scene_journal(journal_path)
    except TrainingExecutorError as exc:
        raise TrainingExecutorError(
            f"real-scene journal blocks update: {exc}"
        ) from exc
    if live != previous:
        raise TrainingExecutorError(
            "real-scene journal update used stale previous evidence"
        )
    if not _journal_advances(previous, updated):
        raise TrainingExecutorError(
            "real-scene journal update is not append-only"
        )
    _atomic_write_journal(
        journal_path,
        canonical_real_scene_journal_bytes(updated),
    )
