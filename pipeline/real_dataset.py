"""Real-dataset source, rights, lock, and receipt contracts.

These contracts prove content closure and operator policy only.  They never
promote source claims to real geometry, metric scale, or alignment.
"""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PortablePath = Annotated[str, StringConstraints(min_length=1)]
DatasetId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]


class DatasetEvidenceError(ValueError):
    """Dataset evidence is malformed, inconsistent, or no longer true."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


def _validate_portable_path(value: str) -> str:
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise ValueError("path must be a portable POSIX relative path")
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or parsed.as_posix() != value
    ):
        raise ValueError("path must be a canonical portable relative path")
    return value


def _validate_repository(value: str) -> str:
    parts = value.split("/")
    if (
        len(parts) != 2
        or any(not part for part in parts)
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
               for part in parts for char in part)
    ):
        raise ValueError("repository must be an owner/name identity")
    return value


class HfDatasetSource(FrozenModel):
    schema_id: Literal["nantai.real-dataset-source.v1"] = Field(alias="schema")
    dataset_id: DatasetId
    role: Literal["internal-canary"]
    source_kind: Literal["hf-dataset"]
    repository: str
    repository_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    subtree: PortablePath
    capture_subtree: PortablePath
    declared_file_count: int = Field(ge=1)
    declared_total_bytes: int = Field(ge=1)
    license_status: Literal["not-declared"]
    redistribution_allowed: Literal[False]
    release_inclusion_allowed: Literal[False]

    @field_validator("repository")
    @classmethod
    def _repository_identity(cls, value: str) -> str:
        return _validate_repository(value)

    @field_validator("subtree", "capture_subtree")
    @classmethod
    def _portable_paths(cls, value: str) -> str:
        return _validate_portable_path(value)

    @model_validator(mode="after")
    def _capture_is_within_subtree(self) -> HfDatasetSource:
        if (
            self.capture_subtree != self.subtree
            and not self.capture_subtree.startswith(f"{self.subtree}/")
        ):
            raise ValueError("capture_subtree must be within subtree")
        return self


class LocalCaptureSource(FrozenModel):
    schema_id: Literal["nantai.real-dataset-source.v1"] = Field(alias="schema")
    dataset_id: DatasetId
    role: Literal["production-acceptance"]
    source_kind: Literal["local-capture"]
    rights_receipt_sha256: Sha256
    redistribution_allowed: bool
    release_inclusion_allowed: bool


RealDatasetSource = Annotated[
    HfDatasetSource | LocalCaptureSource,
    Field(discriminator="source_kind"),
]
REAL_DATASET_SOURCE = TypeAdapter(RealDatasetSource)


class CaptureRightsReceipt(FrozenModel):
    schema_id: Literal["nantai.capture-rights-receipt.v1"] = Field(alias="schema")
    dataset_id: DatasetId
    operator: str = Field(min_length=1)
    capture_scope: str = Field(min_length=1)
    effective_date: date
    processing_purposes: tuple[str, ...] = Field(min_length=1)
    redistribution_allowed: bool
    release_inclusion_allowed: bool

    @field_validator("processing_purposes")
    @classmethod
    def _purposes_are_unique_and_nonempty(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("processing_purposes must be nonempty canonical strings")
        if len(values) != len(set(values)):
            raise ValueError("processing_purposes must be unique")
        return values


class DatasetLockEntry(FrozenModel):
    relative_path: PortablePath
    expected_bytes: int = Field(ge=0)
    server_identity: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _validate_portable_path(value)

    @field_validator("server_identity")
    @classmethod
    def _server_identity_is_bounded_text(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("server_identity must not contain control characters")
        return value


class DatasetReceiptEntry(FrozenModel):
    relative_path: PortablePath
    expected_bytes: int = Field(ge=0)
    server_identity: str = Field(min_length=1)
    actual_bytes: int = Field(ge=0)
    actual_sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _validate_portable_path(value)

    @field_validator("server_identity")
    @classmethod
    def _server_identity_is_bounded_text(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("server_identity must not contain control characters")
        return value


def _validate_entry_order_and_collisions(
    entries: tuple[DatasetLockEntry, ...] | tuple[DatasetReceiptEntry, ...],
) -> None:
    paths = [entry.relative_path for entry in entries]
    if paths != sorted(paths):
        raise ValueError("entries must be ordered by relative_path")
    if len(paths) != len({path.casefold() for path in paths}):
        raise ValueError("entries contain a casefold path collision")


class DatasetLock(FrozenModel):
    schema_id: Literal["nantai.dataset-lock.v1"] = Field(alias="schema")
    source_sha256: Sha256
    repository: str | None = None
    repository_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{40}$",
    )
    entries: tuple[DatasetLockEntry, ...] = Field(min_length=1)

    @field_validator("repository")
    @classmethod
    def _optional_repository_identity(cls, value: str | None) -> str | None:
        return None if value is None else _validate_repository(value)

    @model_validator(mode="after")
    def _repository_fields_and_entries_are_consistent(self) -> DatasetLock:
        if (self.repository is None) != (self.repository_revision is None):
            raise ValueError("repository and repository_revision must appear together")
        _validate_entry_order_and_collisions(self.entries)
        return self


class DatasetReceipt(FrozenModel):
    schema_id: Literal["nantai.dataset-receipt.v1"] = Field(alias="schema")
    source_sha256: Sha256
    lock_sha256: Sha256
    entries: tuple[DatasetReceiptEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _entries_are_consistent(self) -> DatasetReceipt:
        _validate_entry_order_and_collisions(self.entries)
        return self


def canonical_model_bytes(model: BaseModel) -> bytes:
    """Return deterministic ASCII JSON with compact separators and one LF."""

    try:
        text = json.dumps(
            model.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DatasetEvidenceError(f"model is not canonical JSON: {exc}") from exc
    return (text + "\n").encode("ascii")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_real_dataset_source(path: Path) -> HfDatasetSource | LocalCaptureSource:
    """Load a canonical source record without accepting duplicate JSON keys."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DatasetEvidenceError(f"cannot read dataset source: {exc}") from exc
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except DatasetEvidenceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetEvidenceError(f"invalid dataset source JSON: {exc}") from exc
    try:
        source = REAL_DATASET_SOURCE.validate_python(payload)
    except ValidationError as exc:
        raise DatasetEvidenceError(f"invalid dataset source: {exc}") from exc
    if raw != canonical_model_bytes(source):
        raise DatasetEvidenceError("dataset source JSON is not canonical")
    return source


def validate_capture_rights(
    source: HfDatasetSource | LocalCaptureSource,
    rights: CaptureRightsReceipt,
) -> None:
    """Validate local-capture authorization without treating it as geometry."""

    if not isinstance(source, LocalCaptureSource):
        raise DatasetEvidenceError("capture rights apply only to local-capture sources")
    measured_sha = _sha256_bytes(canonical_model_bytes(rights))
    if source.rights_receipt_sha256 != measured_sha:
        raise DatasetEvidenceError("rights_receipt_sha256 does not match rights bytes")
    if source.dataset_id != rights.dataset_id:
        raise DatasetEvidenceError("rights dataset_id does not match source")
    if "3d-reconstruction" not in rights.processing_purposes:
        raise DatasetEvidenceError(
            "rights do not authorize the 3d-reconstruction purpose"
        )
    if source.redistribution_allowed and not rights.redistribution_allowed:
        raise DatasetEvidenceError("source claims redistribution absent from rights")
    if source.release_inclusion_allowed and not rights.release_inclusion_allowed:
        raise DatasetEvidenceError("source claims release inclusion absent from rights")


def _walk_regular_files(root: Path) -> dict[str, Path]:
    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise DatasetEvidenceError(f"dataset root is unavailable: {exc}") from exc
    if stat.S_ISLNK(root_mode):
        raise DatasetEvidenceError("dataset root must not be a symlink")
    if not stat.S_ISDIR(root_mode):
        raise DatasetEvidenceError("dataset root must be a directory")

    files: dict[str, Path] = {}
    try:
        candidates = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError as exc:
        raise DatasetEvidenceError(f"cannot enumerate dataset root: {exc}") from exc
    for candidate in candidates:
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise DatasetEvidenceError(f"cannot inspect dataset member: {exc}") from exc
        relative = candidate.relative_to(root).as_posix()
        if stat.S_ISLNK(mode):
            raise DatasetEvidenceError(f"dataset member is a symlink: {relative}")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise DatasetEvidenceError(
                f"dataset member is not a regular file: {relative}"
            )
        files[relative] = candidate
    return files


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    measured_bytes = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                measured_bytes += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise DatasetEvidenceError(f"cannot hash dataset member: {exc}") from exc
    return measured_bytes, digest.hexdigest()


def validate_dataset_receipt(
    source: HfDatasetSource | LocalCaptureSource,
    lock: DatasetLock,
    receipt: DatasetReceipt,
    dataset_root: Path,
) -> None:
    """Re-derive source, lock, receipt, path, length, and SHA closure."""

    if isinstance(source, HfDatasetSource):
        if len(lock.entries) != source.declared_file_count:
            raise DatasetEvidenceError(
                "lock count does not match declared_file_count"
            )
        expected_total = sum(entry.expected_bytes for entry in lock.entries)
        if expected_total != source.declared_total_bytes:
            raise DatasetEvidenceError(
                "lock bytes do not match declared_total_bytes"
            )
        if lock.repository != source.repository:
            raise DatasetEvidenceError("lock repository does not match source")
        if lock.repository_revision != source.repository_revision:
            raise DatasetEvidenceError(
                "lock repository_revision does not match source"
            )
        if any(
            entry.relative_path != source.subtree
            and not entry.relative_path.startswith(f"{source.subtree}/")
            for entry in lock.entries
        ):
            raise DatasetEvidenceError(
                "lock contains a member outside the declared subtree"
            )

    measured_source_sha = _sha256_bytes(canonical_model_bytes(source))
    if lock.source_sha256 != measured_source_sha:
        raise DatasetEvidenceError("lock source_sha256 does not match source bytes")
    if receipt.source_sha256 != measured_source_sha:
        raise DatasetEvidenceError(
            "receipt source_sha256 does not match source bytes"
        )

    measured_lock_sha = _sha256_bytes(canonical_model_bytes(lock))
    if receipt.lock_sha256 != measured_lock_sha:
        raise DatasetEvidenceError("receipt lock_sha256 does not match lock bytes")

    lock_by_path = {entry.relative_path: entry for entry in lock.entries}
    receipt_by_path = {entry.relative_path: entry for entry in receipt.entries}
    if set(lock_by_path) != set(receipt_by_path):
        raise DatasetEvidenceError("lock and receipt file sets differ")

    live_files = _walk_regular_files(dataset_root)
    if set(live_files) != set(lock_by_path):
        raise DatasetEvidenceError("live dataset file set differs from lock")

    for relative_path in sorted(lock_by_path):
        lock_entry = lock_by_path[relative_path]
        receipt_entry = receipt_by_path[relative_path]
        if receipt_entry.expected_bytes != lock_entry.expected_bytes:
            raise DatasetEvidenceError(
                f"expected_bytes drift for {relative_path}"
            )
        if receipt_entry.server_identity != lock_entry.server_identity:
            raise DatasetEvidenceError(
                f"server_identity drift for {relative_path}"
            )
        measured_bytes, measured_sha = _sha256_file(live_files[relative_path])
        if measured_bytes != lock_entry.expected_bytes:
            raise DatasetEvidenceError(
                f"expected byte length mismatch for {relative_path}"
            )
        if receipt_entry.actual_bytes != measured_bytes:
            raise DatasetEvidenceError(
                f"actual byte length mismatch for {relative_path}"
            )
        if receipt_entry.actual_sha256 != measured_sha:
            raise DatasetEvidenceError(f"sha256 mismatch for {relative_path}")
