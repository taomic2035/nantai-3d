"""Content-addressed real-scene training inputs.

This module owns deterministic train/held-out partitioning and, later, the
portable training-job bundle. A split is an ordering decision over verified
capture identities; it does not promote capture, registration or geometry
trust.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_capture import PreparedRealCapture, RealSfmResult
from pipeline.recon_schema import RegistrationResult
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    RegistrationQualityReport,
    enumerate_sparse_models,
    validate_registration_quality,
)
from pipeline.studio_revisions import (
    CaptureRevisionManifest,
    canonical_manifest_bytes,
)
from pipeline.training_provenance import (
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
)


class RealSceneTrainingError(ValueError):
    """Training inputs are incomplete, ambiguous or content-damaged."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _portable_path(value: str) -> str:
    reserved_windows_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
    invalid_windows_characters = frozenset('<>:"|?*')
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or any(
            ord(character) < 32
            or character in invalid_windows_characters
            for character in value
        )
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or any(part.endswith((" ", ".")) for part in parsed.parts)
        or any(
            part.split(".", 1)[0].upper() in reserved_windows_names
            for part in parsed.parts
        )
    ):
        raise ValueError("logical_path must be a portable relative POSIX path")
    return value


class TrainingImageIdentity(FrozenModel):
    """One capture image bound by both logical identity and measured bytes."""

    logical_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _validate_logical_path = field_validator("logical_path")(_portable_path)


class HeldOutSplit(FrozenModel):
    """Canonical content-ordered train/evaluation partition."""

    schema_id: Literal["nantai.held-out-split.v1"] = Field(
        default="nantai.held-out-split.v1",
        alias="schema",
        serialization_alias="schema",
    )
    selection_rule: Literal["sha256-logical-path-order-first-n"] = (
        "sha256-logical-path-order-first-n"
    )
    ratio: float = Field(gt=0.0, lt=1.0, allow_inf_nan=False)
    total_count: int = Field(ge=2)
    train: tuple[TrainingImageIdentity, ...] = Field(min_length=1)
    held_out: tuple[TrainingImageIdentity, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _partition_is_exact(self) -> HeldOutSplit:
        combined = (*self.held_out, *self.train)
        if len(combined) != self.total_count:
            raise ValueError("split members must equal total_count")
        if len(set(combined)) != len(combined):
            raise ValueError("split contains duplicate content identities")
        if tuple(sorted(self.held_out, key=_identity_key)) != self.held_out:
            raise ValueError("held_out identities must be content ordered")
        if tuple(sorted(self.train, key=_identity_key)) != self.train:
            raise ValueError("train identities must be content ordered")
        expected_held_out = _round_half_up(self.total_count, self.ratio)
        if len(self.held_out) != expected_held_out:
            raise ValueError("held_out count does not match ratio")
        ordered = tuple(sorted(combined, key=_identity_key))
        if (
            self.held_out != ordered[:expected_held_out]
            or self.train != ordered[expected_held_out:]
        ):
            raise ValueError(
                "split partition violates the declared selection rule"
            )
        return self


class TrainingBundleMember(FrozenModel):
    """One regular portable member covered by the bundle manifest."""

    path: str
    byte_length: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _validate_path = field_validator("path")(_portable_path)


class TrainingBundleManifest(FrozenModel):
    """Outer closure record for every payload member in a training ZIP."""

    schema_id: Literal["nantai.training-job-bundle.v1"] = Field(
        default="nantai.training-job-bundle.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    capture_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    registration_json_sha256: str = Field(pattern=_SHA256_PATTERN)
    registration_quality_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    registration_quality_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    sparse_model_enumeration_sha256: str = Field(pattern=_SHA256_PATTERN)
    selected_sparse_model_index: int = Field(ge=0)
    members: tuple[TrainingBundleMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _members_are_unique_and_sorted(self) -> TrainingBundleManifest:
        paths = tuple(member.path for member in self.members)
        if paths != tuple(sorted(paths)):
            raise ValueError("bundle manifest members must be path sorted")
        if len({path.casefold() for path in paths}) != len(paths):
            raise ValueError("bundle manifest contains duplicate member paths")
        if "bundle-manifest.json" in paths:
            raise ValueError("bundle manifest cannot recursively bind itself")
        return self


@dataclass(frozen=True)
class TrainingJobBundle:
    path: Path
    bundle_sha256: str
    manifest: TrainingBundleManifest
    request: TrainingRequest
    split: HeldOutSplit


@dataclass(frozen=True)
class VerifiedTrainingJobBundle(TrainingJobBundle):
    member_names: tuple[str, ...]


def _identity_key(identity: TrainingImageIdentity) -> tuple[str, str]:
    return identity.sha256, identity.logical_path


def _round_half_up(count: int, ratio: float) -> int:
    try:
        ratio_decimal = Decimal(str(ratio))
    except (InvalidOperation, ValueError) as exc:
        raise RealSceneTrainingError("ratio must be a finite number") from exc
    rounded = (Decimal(count) * ratio_decimal).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    return int(rounded)


def held_out_split_canonical_bytes(split: HeldOutSplit) -> bytes:
    """Return sorted compact ASCII JSON with one trailing LF."""

    return (
        json.dumps(
            split.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def build_held_out_split(
    capture: PreparedRealCapture,
    ratio: float = 0.10,
) -> HeldOutSplit:
    """Partition capture identities independently of manifest/filename order."""

    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not math.isfinite(float(ratio))
        or not 0.0 < float(ratio) < 1.0
    ):
        raise RealSceneTrainingError("ratio must be finite and between 0 and 1")
    manifest = capture.capture.manifest
    if manifest.output_count != len(manifest.payloads):
        raise RealSceneTrainingError(
            "capture output_count differs from payload count"
        )
    if len(manifest.payloads) < 2:
        raise RealSceneTrainingError(
            "capture requires at least two images for a held-out partition"
        )

    identities = tuple(
        TrainingImageIdentity(
            logical_path=payload.logical_path,
            sha256=payload.sha256,
        )
        for payload in manifest.payloads
    )
    if len(set(identities)) != len(identities):
        raise RealSceneTrainingError(
            "capture contains duplicate content identities"
        )

    manifest_sha256 = hashlib.sha256(
        canonical_manifest_bytes(manifest)
    ).hexdigest()
    if capture.capture.manifest_digest != manifest_sha256:
        raise RealSceneTrainingError(
            "capture manifest digest differs from canonical manifest bytes"
        )

    ordered = tuple(sorted(identities, key=_identity_key))
    held_out_count = _round_half_up(len(ordered), float(ratio))
    if held_out_count <= 0 or held_out_count >= len(ordered):
        raise RealSceneTrainingError(
            "ratio does not produce a non-empty train/held-out partition"
        )
    return HeldOutSplit(
        ratio=float(ratio),
        total_count=len(ordered),
        held_out=ordered[:held_out_count],
        train=ordered[held_out_count:],
    )


_SPARSE_FILENAMES = (
    "cameras.bin",
    "cameras.txt",
    "images.bin",
    "images.txt",
    "points3D.bin",
    "points3D.txt",
)
_REQUIRED_NERFSTUDIO_EXTRAS = {
    "auto_scale_poses": "false",
    "center_method": "none",
    "orientation_method": "none",
    "scale_factor": "1.0",
}
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class _MemberSource:
    member: TrainingBundleMember
    data: bytes | None = None
    path: Path | None = None
    stat_signature: tuple[int, int, int, int, int] | None = None


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
) -> tuple[int, str, tuple[int, int, int, int, int]]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RealSceneTrainingError(f"{label} is missing or link-like")
        digest = hashlib.sha256()
        measured = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                measured += len(chunk)
        after = path.lstat()
    except RealSceneTrainingError:
        raise
    except OSError as exc:
        raise RealSceneTrainingError(f"{label} cannot be read") from exc
    signature = _stat_signature(before)
    if signature != _stat_signature(after):
        raise RealSceneTrainingError(f"{label} changed while being hashed")
    if measured <= 0:
        raise RealSceneTrainingError(f"{label} is empty")
    return measured, digest.hexdigest(), signature


def _read_file_stable(path: Path, *, label: str) -> bytes:
    measured, expected_sha, signature = _hash_file_stable(path, label=label)
    try:
        before = path.lstat()
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise RealSceneTrainingError(f"{label} cannot be read") from exc
    if (
        _stat_signature(before) != signature
        or _stat_signature(after) != signature
        or len(payload) != measured
        or hashlib.sha256(payload).hexdigest() != expected_sha
    ):
        raise RealSceneTrainingError(f"{label} changed while being read")
    return payload


def _bytes_source(path: str, data: bytes) -> _MemberSource:
    return _MemberSource(
        member=TrainingBundleMember(
            path=path,
            byte_length=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
        data=data,
    )


def _file_source(
    member_path: str,
    source_path: Path,
    *,
    label: str,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> _MemberSource:
    measured, digest, signature = _hash_file_stable(
        source_path,
        label=label,
    )
    if expected_bytes is not None and measured != expected_bytes:
        raise RealSceneTrainingError(f"{label} length differs from evidence")
    if expected_sha256 is not None and digest != expected_sha256:
        raise RealSceneTrainingError(f"{label} sha256 differs from evidence")
    return _MemberSource(
        member=TrainingBundleMember(
            path=member_path,
            byte_length=measured,
            sha256=digest,
        ),
        path=source_path,
        stat_signature=signature,
    )


def _canonical_training_config(config: TrainingConfig) -> TrainingConfig:
    if not isinstance(config, TrainingConfig):
        raise RealSceneTrainingError("config must be a TrainingConfig")
    if config.trainer_name != "nerfstudio-splatfacto":
        raise RealSceneTrainingError(
            "training bundle requires nerfstudio-splatfacto"
        )
    if config.trainer_version != "1.1.5":
        raise RealSceneTrainingError(
            "training bundle requires pinned Nerfstudio 1.1.5"
        )
    keys = [key for key, _ in config.extra_config]
    if len(keys) != len(set(keys)):
        raise RealSceneTrainingError(
            "training config contains duplicate extra_config keys"
        )
    extras = dict(config.extra_config)
    for key, expected in _REQUIRED_NERFSTUDIO_EXTRAS.items():
        if extras.get(key) != expected:
            raise RealSceneTrainingError(
                f"training config requires {key}={expected}"
            )
    return config.model_copy(
        update={"extra_config": tuple(sorted(config.extra_config))}
    )


def _operator_config_bytes(config: TrainingConfig) -> bytes:
    payload = config.model_dump(mode="json")
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _sparse_descriptor(
    members: tuple[TrainingBundleMember, ...],
) -> bytes:
    prefix = "sfm/sparse/0/"
    sparse = tuple(
        {
            "path": member.path.removeprefix(prefix),
            "byte_length": member.byte_length,
            "sha256": member.sha256,
        }
        for member in members
        if member.path.startswith(prefix)
    )
    expected_names = tuple(sorted(_SPARSE_FILENAMES))
    actual_names = tuple(item["path"] for item in sparse)
    if actual_names != expected_names:
        raise RealSceneTrainingError(
            "sparse bundle members are missing, extra, or unsorted"
        )
    payload = (
        json.dumps(
            sparse,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return payload


def _training_request(
    *,
    capture_manifest_bytes: bytes,
    split_bytes: bytes | None,
    registration_bytes: bytes,
    quality_bytes: bytes,
    sparse_descriptor_bytes: bytes,
    config: TrainingConfig,
    config_bytes: bytes,
    source_sha256: str,
    dataset_receipt_sha256: str,
    created_at_utc: datetime,
    policy_bytes: bytes,
) -> TrainingRequest:
    capture_binding = TrainingInputBinding(
        artifact_kind="capture_manifest",
        artifact_sha256=hashlib.sha256(
            capture_manifest_bytes
        ).hexdigest(),
        artifact_path="capture/manifest.json",
        artifact_size_bytes=len(capture_manifest_bytes),
    )
    registration_binding = TrainingInputBinding(
        artifact_kind="registration_json",
        artifact_sha256=hashlib.sha256(registration_bytes).hexdigest(),
        artifact_path="sfm/registration.json",
        artifact_size_bytes=len(registration_bytes),
    )
    quality_binding = TrainingInputBinding(
        artifact_kind="registration_quality_report",
        artifact_sha256=hashlib.sha256(quality_bytes).hexdigest(),
        artifact_path="sfm/registration-quality-report.json",
        artifact_size_bytes=len(quality_bytes),
    )
    sparse_binding = TrainingInputBinding(
        artifact_kind="sparse_model_dir",
        artifact_sha256=hashlib.sha256(
            sparse_descriptor_bytes
        ).hexdigest(),
        artifact_path="sfm/sparse/0",
        artifact_size_bytes=len(sparse_descriptor_bytes),
    )
    inputs = [
        capture_binding,
        registration_binding,
        quality_binding,
    ]
    split_binding = None
    if split_bytes is not None:
        split_binding = TrainingInputBinding(
            artifact_kind="held_out_split",
            artifact_sha256=hashlib.sha256(split_bytes).hexdigest(),
            artifact_path="training/held-out-split.json",
            artifact_size_bytes=len(split_bytes),
        )
        inputs.append(split_binding)
    inputs.append(sparse_binding)
    request_identity_payload = {
        "capture_manifest_sha256":
            capture_binding.artifact_sha256,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "dataset_receipt_sha256": dataset_receipt_sha256,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "quality_sha256": quality_binding.artifact_sha256,
        "registration_sha256":
            registration_binding.artifact_sha256,
        "source_sha256": source_sha256,
        "sparse_sha256": sparse_binding.artifact_sha256,
    }
    if split_binding is not None:
        request_identity_payload["held_out_split_sha256"] = (
            split_binding.artifact_sha256
        )
    request_identity = (
        json.dumps(
            request_identity_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return TrainingRequest(
        request_id=(
            "training-" + hashlib.sha256(request_identity).hexdigest()[:32]
        ),
        created_at_utc=created_at_utc,
        input_bindings=tuple(inputs),
        training_config=config,
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_member(
    archive: zipfile.ZipFile,
    source: _MemberSource,
) -> None:
    info = _zip_info(source.member.path)
    if source.data is not None:
        archive.writestr(info, source.data)
        return
    if source.path is None or source.stat_signature is None:
        raise RealSceneTrainingError("bundle member source is incomplete")
    try:
        before = source.path.lstat()
        if _stat_signature(before) != source.stat_signature:
            raise RealSceneTrainingError(
                f"bundle source changed before write: {source.member.path}"
            )
        digest = hashlib.sha256()
        measured = 0
        with source.path.open("rb") as input_stream, archive.open(
            info,
            "w",
            force_zip64=True,
        ) as output_stream:
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                digest.update(chunk)
                measured += len(chunk)
                output_stream.write(chunk)
        after = source.path.lstat()
    except RealSceneTrainingError:
        raise
    except OSError as exc:
        raise RealSceneTrainingError(
            f"cannot write bundle member: {source.member.path}"
        ) from exc
    if (
        _stat_signature(after) != source.stat_signature
        or measured != source.member.byte_length
        or digest.hexdigest() != source.member.sha256
    ):
        raise RealSceneTrainingError(
            f"bundle source changed during write: {source.member.path}"
        )


def _require_absent_output(output_dir: Path) -> Path:
    output = output_dir.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise RealSceneTrainingError(
            "training bundle output boundary must be absent and non-link"
        )
    parent = output.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise RealSceneTrainingError(
            "training bundle output parent is unavailable"
        ) from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise RealSceneTrainingError(
            "training bundle output parent must be a real directory"
        )
    return output


def build_training_job_bundle(
    capture: PreparedRealCapture,
    sfm: RealSfmResult,
    config: TrainingConfig,
    output_dir: Path,
    *,
    policy: RegistrationQualityPolicy,
) -> TrainingJobBundle:
    """Build one deterministic, deeply bound Nerfstudio training ZIP."""

    if sfm.registration.engine != "colmap":
        raise RealSceneTrainingError(
            "training bundle requires a non-mock COLMAP registration"
        )
    if not sfm.quality.training_allowed:
        raise RealSceneTrainingError(
            "registration training gate did not allow this bundle"
        )
    if sfm.sparse_enumeration is None:
        raise RealSceneTrainingError(
            "COLMAP training bundle requires sparse model enumeration"
        )

    config = _canonical_training_config(config)
    capture_path = capture.capture.bundle / "manifest.json"
    capture_bytes = _read_file_stable(
        capture_path,
        label="capture manifest",
    )
    capture_sha = hashlib.sha256(capture_bytes).hexdigest()
    if (
        capture_sha != capture.capture.manifest_digest
        or capture_bytes != canonical_manifest_bytes(capture.capture.manifest)
    ):
        raise RealSceneTrainingError(
            "capture manifest bytes differ from prepared evidence"
        )
    try:
        parsed_capture = CaptureRevisionManifest.model_validate_json(
            capture_bytes
        )
    except ValueError as exc:
        raise RealSceneTrainingError("capture manifest is invalid") from exc
    if parsed_capture != capture.capture.manifest:
        raise RealSceneTrainingError(
            "capture manifest object differs from live bytes"
        )

    registration_bytes = _read_file_stable(
        sfm.registration_path,
        label="registration JSON",
    )
    if hashlib.sha256(registration_bytes).hexdigest() != sfm.registration_sha256:
        raise RealSceneTrainingError(
            "registration JSON sha256 differs from SfM evidence"
        )
    try:
        parsed_registration = RegistrationResult.model_validate_json(
            registration_bytes
        )
    except ValueError as exc:
        raise RealSceneTrainingError("registration JSON is invalid") from exc
    if parsed_registration != sfm.registration:
        raise RealSceneTrainingError(
            "registration object differs from live bytes"
        )

    quality_bytes = _read_file_stable(
        sfm.quality_path,
        label="registration quality report",
    )
    if hashlib.sha256(quality_bytes).hexdigest() != sfm.quality_sha256:
        raise RealSceneTrainingError(
            "registration quality report sha256 differs from SfM evidence"
        )
    try:
        parsed_quality = RegistrationQualityReport.model_validate_json(
            quality_bytes
        )
    except ValueError as exc:
        raise RealSceneTrainingError(
            "registration quality report is invalid"
        ) from exc
    if parsed_quality != sfm.quality:
        raise RealSceneTrainingError(
            "registration quality object differs from live bytes"
        )

    selected_index = sfm.sparse_enumeration.selected_model_index
    sparse_root = sfm.registration_path.parent / "colmap" / "sparse"
    try:
        live_enumeration = enumerate_sparse_models(
            sparse_root,
            capture.capture.manifest.output_count,
        )
    except ValueError as exc:
        raise RealSceneTrainingError(
            "live sparse model enumeration failed"
        ) from exc
    if live_enumeration != sfm.sparse_enumeration:
        raise RealSceneTrainingError(
            "live sparse model enumeration differs from SfM evidence"
        )
    try:
        validate_registration_quality(
            parsed_quality,
            policy,
            registration_bytes,
            capture_manifest_bytes=capture_bytes,
            sparse_enumeration=live_enumeration,
        )
    except ValueError as exc:
        raise RealSceneTrainingError(
            f"registration quality validation failed: {exc}"
        ) from exc

    split = build_held_out_split(capture)
    registered_names = {pose.image for pose in parsed_registration.poses}
    held_out_names = {identity.logical_path for identity in split.held_out}
    if not held_out_names <= registered_names:
        raise RealSceneTrainingError(
            "held-out images must all have registered camera poses"
        )

    sources: dict[str, _MemberSource] = {}

    def add(source: _MemberSource) -> None:
        key = source.member.path.casefold()
        if key in {name.casefold() for name in sources}:
            raise RealSceneTrainingError(
                f"duplicate training bundle member: {source.member.path}"
            )
        sources[source.member.path] = source

    add(_bytes_source("capture/manifest.json", capture_bytes))
    payload_by_path = {
        payload.logical_path: payload
        for payload in capture.capture.manifest.payloads
    }
    for identity in split.train:
        payload = payload_by_path[identity.logical_path]
        add(
            _file_source(
                f"capture/payload/{identity.logical_path}",
                capture.payload_root / PurePosixPath(identity.logical_path),
                label=f"capture payload {identity.logical_path}",
                expected_bytes=payload.byte_length,
                expected_sha256=payload.sha256,
            )
        )
    for identity in split.held_out:
        payload = payload_by_path[identity.logical_path]
        add(
            _file_source(
                f"evaluation/payload/{identity.logical_path}",
                capture.payload_root / PurePosixPath(identity.logical_path),
                label=f"held-out evaluation payload {identity.logical_path}",
                expected_bytes=payload.byte_length,
                expected_sha256=payload.sha256,
            )
        )

    add(_bytes_source("sfm/registration.json", registration_bytes))
    policy_bytes = canonical_model_bytes(policy)
    add(
        _bytes_source(
            "sfm/registration-quality-policy.json",
            policy_bytes,
        )
    )
    add(
        _bytes_source(
            "sfm/registration-quality-report.json",
            quality_bytes,
        )
    )
    selected_model_root = sparse_root / str(selected_index)
    for filename in _SPARSE_FILENAMES:
        add(
            _file_source(
                f"sfm/sparse/0/{filename}",
                selected_model_root / filename,
                label=f"selected sparse model {filename}",
            )
        )

    split_bytes = held_out_split_canonical_bytes(split)
    add(_bytes_source("training/held-out-split.json", split_bytes))
    config_bytes = _operator_config_bytes(config)
    add(
        _bytes_source(
            "training/operator-intent-config.yml",
            config_bytes,
        )
    )
    sparse_members = tuple(
        sources[path].member for path in sorted(sources)
    )
    sparse_descriptor_bytes = _sparse_descriptor(
        sparse_members
    )
    request = _training_request(
        capture_manifest_bytes=capture_bytes,
        split_bytes=split_bytes,
        registration_bytes=registration_bytes,
        quality_bytes=quality_bytes,
        sparse_descriptor_bytes=sparse_descriptor_bytes,
        config=config,
        config_bytes=config_bytes,
        source_sha256=capture.source_sha256,
        dataset_receipt_sha256=capture.dataset_receipt_sha256,
        created_at_utc=capture.capture.manifest.created_utc,
        policy_bytes=policy_bytes,
    )
    add(
        _bytes_source(
            "training/training-request.json",
            canonical_model_bytes(request),
        )
    )

    members = tuple(sources[path].member for path in sorted(sources))
    enumeration_bytes = canonical_model_bytes(live_enumeration)
    manifest = TrainingBundleManifest(
        source_sha256=capture.source_sha256,
        dataset_receipt_sha256=capture.dataset_receipt_sha256,
        capture_manifest_sha256=capture_sha,
        registration_json_sha256=sfm.registration_sha256,
        registration_quality_policy_sha256=hashlib.sha256(
            policy_bytes
        ).hexdigest(),
        registration_quality_report_sha256=sfm.quality_sha256,
        sparse_model_enumeration_sha256=hashlib.sha256(
            enumeration_bytes
        ).hexdigest(),
        selected_sparse_model_index=selected_index,
        members=members,
    )
    manifest_source = _bytes_source(
        "bundle-manifest.json",
        canonical_model_bytes(manifest),
    )

    from pipeline.durable_io import (
        flush_directory,
        flush_file,
        publish_directory_noreplace,
    )

    output = _require_absent_output(Path(output_dir))
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.staging"
    final = staging / "training-job.zip"
    try:
        staging.mkdir()
        with zipfile.ZipFile(
            final,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            for source in (
                manifest_source,
                *(sources[path] for path in sorted(sources)),
            ):
                _write_member(archive, source)
        flush_file(final)
        verify_training_job_bundle(final)
        flush_directory(staging)
        publish_directory_noreplace(staging, output)
    except RealSceneTrainingError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise RealSceneTrainingError(
            f"cannot create deterministic training ZIP: {exc}"
        ) from exc
    finally:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)

    verified = verify_training_job_bundle(output / "training-job.zip")
    return TrainingJobBundle(
        path=verified.path,
        bundle_sha256=verified.bundle_sha256,
        manifest=verified.manifest,
        request=verified.request,
        split=verified.split,
    )


def _validate_archive_member_name(name: str) -> None:
    try:
        _portable_path(name)
    except ValueError as exc:
        raise RealSceneTrainingError(
            f"archive member path is unsafe: {name!r}"
        ) from exc


def _zip_member_digest(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> str:
    digest = hashlib.sha256()
    measured = 0
    with archive.open(info, "r") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            measured += len(chunk)
    if measured != info.file_size:
        raise RealSceneTrainingError(
            f"archive member length changed while reading: {info.filename}"
        )
    return digest.hexdigest()


def _parse_colmap_images_bytes(payload: bytes) -> tuple[str, ...]:
    try:
        raw_lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RealSceneTrainingError(
            "bundled sparse images.txt is not UTF-8"
        ) from exc
    images: list[str] = []
    index = 0
    while index < len(raw_lines):
        header = raw_lines[index].strip()
        index += 1
        if not header or header.startswith("#"):
            continue
        parts = header.split()
        if len(parts) < 10:
            raise RealSceneTrainingError(
                "bundled sparse images.txt has an invalid image header"
            )
        images.append(parts[9])
        if index >= len(raw_lines):
            raise RealSceneTrainingError(
                "bundled sparse images.txt is missing a POINTS2D row"
            )
        index += 1
    return tuple(images)


def _points3d_count(payload: bytes) -> int:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RealSceneTrainingError(
            "bundled sparse points3D.txt is not UTF-8"
        ) from exc
    return sum(
        bool(line.strip()) and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )


def verify_training_job_bundle(path: Path) -> VerifiedTrainingJobBundle:
    """Deeply revalidate a portable training ZIP without trusting its names."""

    archive_path = Path(path).expanduser().absolute()
    archive_size, archive_sha, archive_signature = _hash_file_stable(
        archive_path,
        label="training job archive",
    )
    if archive_size > _MAX_ARCHIVE_BYTES:
        raise RealSceneTrainingError("training job archive exceeds size limit")
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_MEMBERS:
                raise RealSceneTrainingError(
                    "archive member count is outside the allowed range"
                )
            names = tuple(info.filename for info in infos)
            if len({name.casefold() for name in names}) != len(names):
                raise RealSceneTrainingError(
                    "archive contains duplicate member paths"
                )
            if names != tuple(sorted(names)):
                raise RealSceneTrainingError(
                    "archive members are not deterministically sorted"
                )
            for info in infos:
                _validate_archive_member_name(info.filename)
                unix_mode = info.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise RealSceneTrainingError(
                        f"archive member is a symlink: {info.filename}"
                    )
                if info.is_dir() or not stat.S_ISREG(unix_mode):
                    raise RealSceneTrainingError(
                        f"archive member is not a regular file: {info.filename}"
                    )
                if info.compress_type != zipfile.ZIP_STORED:
                    raise RealSceneTrainingError(
                        "archive members must use deterministic stored encoding"
                    )
                if info.date_time != _FIXED_ZIP_TIMESTAMP:
                    raise RealSceneTrainingError(
                        "archive member timestamp is not deterministic"
                    )
                if info.file_size <= 0 or info.file_size != info.compress_size:
                    raise RealSceneTrainingError(
                        f"archive member has invalid length: {info.filename}"
                    )
            if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
                raise RealSceneTrainingError(
                    "archive expanded bytes exceed size limit"
                )
            by_name = {info.filename: info for info in infos}
            manifest_info = by_name.get("bundle-manifest.json")
            if manifest_info is None:
                raise RealSceneTrainingError(
                    "archive is missing bundle-manifest.json"
                )
            manifest_bytes = archive.read(manifest_info)
            try:
                manifest = TrainingBundleManifest.model_validate_json(
                    manifest_bytes
                )
            except ValueError as exc:
                raise RealSceneTrainingError(
                    "bundle manifest is invalid"
                ) from exc
            if manifest_bytes != canonical_model_bytes(manifest):
                raise RealSceneTrainingError(
                    "bundle manifest is not canonical JSON"
                )
            expected_names = (
                "bundle-manifest.json",
                *(member.path for member in manifest.members),
            )
            if names != tuple(sorted(expected_names)):
                raise RealSceneTrainingError(
                    "archive members differ from bundle manifest"
                )

            manifest_members = {
                member.path: member for member in manifest.members
            }
            for member in manifest.members:
                info = by_name[member.path]
                if info.file_size != member.byte_length:
                    raise RealSceneTrainingError(
                        f"archive member length mismatch: {member.path}"
                    )
                if _zip_member_digest(archive, info) != member.sha256:
                    raise RealSceneTrainingError(
                        f"archive member sha256 mismatch: {member.path}"
                    )

            structured_names = (
                "capture/manifest.json",
                "sfm/registration.json",
                "sfm/registration-quality-policy.json",
                "sfm/registration-quality-report.json",
                "sfm/sparse/0/images.txt",
                "sfm/sparse/0/points3D.txt",
                "training/held-out-split.json",
                "training/operator-intent-config.yml",
                "training/training-request.json",
            )
            structured = {
                name: archive.read(by_name[name])
                for name in structured_names
                if name in by_name
            }
    except RealSceneTrainingError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise RealSceneTrainingError(
            f"training job archive cannot be verified: {exc}"
        ) from exc

    try:
        capture = CaptureRevisionManifest.model_validate_json(
            structured["capture/manifest.json"]
        )
        registration = RegistrationResult.model_validate_json(
            structured["sfm/registration.json"]
        )
        policy = RegistrationQualityPolicy.model_validate_json(
            structured["sfm/registration-quality-policy.json"]
        )
        quality = RegistrationQualityReport.model_validate_json(
            structured["sfm/registration-quality-report.json"]
        )
        split = HeldOutSplit.model_validate_json(
            structured["training/held-out-split.json"]
        )
        request = TrainingRequest.model_validate_json(
            structured["training/training-request.json"]
        )
    except (KeyError, ValueError) as exc:
        raise RealSceneTrainingError(
            "a nested training bundle contract is missing or invalid"
        ) from exc

    if structured["capture/manifest.json"] != canonical_manifest_bytes(capture):
        raise RealSceneTrainingError("capture manifest is not canonical")
    if (
        structured["sfm/registration-quality-policy.json"]
        != canonical_model_bytes(policy)
    ):
        raise RealSceneTrainingError(
            "registration quality policy is not canonical"
        )
    if (
        structured["training/held-out-split.json"]
        != held_out_split_canonical_bytes(split)
    ):
        raise RealSceneTrainingError("held-out split is not canonical")
    if (
        structured["training/training-request.json"]
        != canonical_model_bytes(request)
    ):
        raise RealSceneTrainingError("training request is not canonical")

    enumeration = quality.model_enumeration
    if registration.engine != "colmap" or enumeration is None:
        raise RealSceneTrainingError(
            "training bundle requires COLMAP enumeration evidence"
        )
    try:
        validate_registration_quality(
            quality,
            policy,
            structured["sfm/registration.json"],
            capture_manifest_bytes=structured["capture/manifest.json"],
            sparse_enumeration=enumeration,
        )
    except ValueError as exc:
        raise RealSceneTrainingError(
            f"nested registration quality validation failed: {exc}"
        ) from exc
    if not quality.training_allowed:
        raise RealSceneTrainingError(
            "nested registration training gate is not allowed"
        )

    selected = next(
        (
            model
            for model in enumeration.models
            if model.model_index == enumeration.selected_model_index
        ),
        None,
    )
    if selected is None:
        raise RealSceneTrainingError(
            "selected sparse enumeration model is missing"
        )
    bundled_images = _parse_colmap_images_bytes(
        structured["sfm/sparse/0/images.txt"]
    )
    if set(bundled_images) != set(selected.images):
        raise RealSceneTrainingError(
            "bundled sparse images differ from enumeration"
        )
    if _points3d_count(
        structured["sfm/sparse/0/points3D.txt"]
    ) != selected.point3d_count:
        raise RealSceneTrainingError(
            "bundled sparse points differ from enumeration"
        )
    if set(bundled_images) != {pose.image for pose in registration.poses}:
        raise RealSceneTrainingError(
            "bundled sparse images differ from registration poses"
        )

    sparse_descriptor_bytes = _sparse_descriptor(
        manifest.members
    )
    canonical_config = _canonical_training_config(request.training_config)
    if canonical_config != request.training_config:
        raise RealSceneTrainingError(
            "training request extra_config order is not canonical"
        )
    config_bytes = _operator_config_bytes(canonical_config)
    if structured["training/operator-intent-config.yml"] != config_bytes:
        raise RealSceneTrainingError(
            "operator intent config differs from training request"
        )
    expected_request = _training_request(
        capture_manifest_bytes=structured["capture/manifest.json"],
        split_bytes=(
            structured["training/held-out-split.json"]
            if any(
                binding.artifact_kind == "held_out_split"
                for binding in request.input_bindings
            )
            else None
        ),
        registration_bytes=structured["sfm/registration.json"],
        quality_bytes=structured["sfm/registration-quality-report.json"],
        sparse_descriptor_bytes=sparse_descriptor_bytes,
        config=canonical_config,
        config_bytes=config_bytes,
        source_sha256=manifest.source_sha256,
        dataset_receipt_sha256=manifest.dataset_receipt_sha256,
        created_at_utc=capture.created_utc,
        policy_bytes=structured["sfm/registration-quality-policy.json"],
    )
    if request != expected_request:
        raise RealSceneTrainingError(
            "training request differs from re-derived input bindings"
        )

    identities = {
        TrainingImageIdentity(
            logical_path=payload.logical_path,
            sha256=payload.sha256,
        )
        for payload in capture.payloads
    }
    if identities != set((*split.held_out, *split.train)):
        raise RealSceneTrainingError(
            "held-out split does not partition the capture manifest"
        )
    expected_payload_members = {
        f"capture/payload/{identity.logical_path}"
        for identity in split.train
    }
    actual_payload_members = {
        member.path
        for member in manifest.members
        if member.path.startswith("capture/payload/")
    }
    if actual_payload_members != expected_payload_members:
        raise RealSceneTrainingError(
            "capture payload members do not exactly match the training split"
        )
    payloads = {payload.logical_path: payload for payload in capture.payloads}
    for identity in split.train:
        member = manifest_members[
            f"capture/payload/{identity.logical_path}"
        ]
        payload = payloads[identity.logical_path]
        if (
            member.sha256 != payload.sha256
            or member.byte_length != payload.byte_length
        ):
            raise RealSceneTrainingError(
                f"capture payload evidence mismatch: {identity.logical_path}"
            )
    expected_evaluation_members = {
        f"evaluation/payload/{identity.logical_path}"
        for identity in split.held_out
    }
    actual_evaluation_members = {
        member.path
        for member in manifest.members
        if member.path.startswith("evaluation/payload/")
    }
    if (
        actual_evaluation_members
        and actual_evaluation_members != expected_evaluation_members
    ):
        raise RealSceneTrainingError(
            "evaluation payload members do not exactly match held-out split"
        )
    if actual_evaluation_members:
        for identity in split.held_out:
            member = manifest_members[
                f"evaluation/payload/{identity.logical_path}"
            ]
            payload = payloads[identity.logical_path]
            if (
                member.sha256 != payload.sha256
                or member.byte_length != payload.byte_length
            ):
                raise RealSceneTrainingError(
                    "held-out evaluation payload evidence mismatch: "
                    f"{identity.logical_path}"
                )
    if not {
        identity.logical_path for identity in split.held_out
    } <= {pose.image for pose in registration.poses}:
        raise RealSceneTrainingError(
            "held-out images do not all have registered camera poses"
        )

    field_hashes = {
        "capture_manifest_sha256": hashlib.sha256(
            structured["capture/manifest.json"]
        ).hexdigest(),
        "registration_json_sha256": hashlib.sha256(
            structured["sfm/registration.json"]
        ).hexdigest(),
        "registration_quality_policy_sha256": hashlib.sha256(
            structured["sfm/registration-quality-policy.json"]
        ).hexdigest(),
        "registration_quality_report_sha256": hashlib.sha256(
            structured["sfm/registration-quality-report.json"]
        ).hexdigest(),
        "sparse_model_enumeration_sha256": hashlib.sha256(
            canonical_model_bytes(enumeration)
        ).hexdigest(),
    }
    for field_name, expected in field_hashes.items():
        if getattr(manifest, field_name) != expected:
            raise RealSceneTrainingError(
                f"bundle manifest {field_name} mismatch"
            )
    if manifest.selected_sparse_model_index != enumeration.selected_model_index:
        raise RealSceneTrainingError(
            "bundle manifest selected sparse model index mismatch"
        )

    try:
        after = archive_path.lstat()
    except OSError as exc:
        raise RealSceneTrainingError(
            "training job archive disappeared after verification"
        ) from exc
    if _stat_signature(after) != archive_signature:
        raise RealSceneTrainingError(
            "training job archive changed while being verified"
        )
    return VerifiedTrainingJobBundle(
        path=archive_path,
        bundle_sha256=archive_sha,
        manifest=manifest,
        request=request,
        split=split,
        member_names=names,
    )


def load_training_job_input_bytes(
    bundle: VerifiedTrainingJobBundle,
) -> dict[str, bytes]:
    """Materialize the exact bytes named by every TrainingRequest binding.

    File bindings return their canonical ZIP member bytes. The sparse-directory
    binding returns the canonical member descriptor whose own SHA and length
    are stored in the request; it never pretends a directory has one native
    byte stream.
    """

    verified = verify_training_job_bundle(bundle.path)
    if verified.bundle_sha256 != bundle.bundle_sha256:
        raise RealSceneTrainingError(
            "training bundle identity changed before loading inputs"
        )
    try:
        with zipfile.ZipFile(verified.path, "r") as archive:
            actual = {
                "capture/manifest.json": archive.read(
                    "capture/manifest.json"
                ),
                "sfm/registration.json": archive.read(
                    "sfm/registration.json"
                ),
                "sfm/registration-quality-report.json": archive.read(
                    "sfm/registration-quality-report.json"
                ),
                "sfm/sparse/0": _sparse_descriptor(
                    verified.manifest.members
                ),
                "training/held-out-split.json": archive.read(
                    "training/held-out-split.json"
                ),
            }
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise RealSceneTrainingError(
            f"training bundle inputs cannot be loaded: {exc}"
        ) from exc
    expected_paths = {
        binding.artifact_path
        for binding in verified.request.input_bindings
    }
    if not expected_paths <= set(actual):
        raise RealSceneTrainingError(
            "training request input paths differ from loadable bundle inputs"
        )
    actual = {
        path: actual[path]
        for path in expected_paths
    }
    for binding in verified.request.input_bindings:
        payload = actual[binding.artifact_path]
        if (
            len(payload) != binding.artifact_size_bytes
            or hashlib.sha256(payload).hexdigest()
            != binding.artifact_sha256
        ):
            raise RealSceneTrainingError(
                f"training input binding mismatch: {binding.artifact_path}"
            )
    return actual


def verify_production_training_job_bundle(
    path: Path,
) -> VerifiedTrainingJobBundle:
    """Require split-bound train/evaluation pixels for the production lane.

    Legacy bundles remain verifiable for historical/local preview replay, but
    they cannot enter remote production training because their split choice or
    held-out evaluation pixels are not content-closed.
    """

    verified = verify_training_job_bundle(path)
    split_bindings = tuple(
        binding
        for binding in verified.request.input_bindings
        if binding.artifact_kind == "held_out_split"
    )
    if (
        len(split_bindings) != 1
        or split_bindings[0].artifact_path
        != "training/held-out-split.json"
    ):
        raise RealSceneTrainingError(
            "production training bundle requires one held-out split binding"
        )
    expected_evaluation_members = {
        f"evaluation/payload/{identity.logical_path}"
        for identity in verified.split.held_out
    }
    actual_evaluation_members = {
        member.path
        for member in verified.manifest.members
        if member.path.startswith("evaluation/payload/")
    }
    if actual_evaluation_members != expected_evaluation_members:
        raise RealSceneTrainingError(
            "production training bundle requires complete held-out "
            "evaluation payloads"
        )
    return verified
