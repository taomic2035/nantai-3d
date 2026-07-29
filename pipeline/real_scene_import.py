"""Fail-closed import boundary for verified real-scene 3DGS artifacts.

The training executor proves how a PLY was produced.  This module proves that
the same bytes were semantically importable, copied and quaternion-normalized,
placed in an explicit coordinate frame, reconstructed without deduplication,
chunked without moving or duplicating Gaussians, and closed by byte identity.
None of those checks promote an internal canary to metric or commercial use.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from plyfile import PlyData
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from cloud.validate_dataparser_transform import (
    DataparserTransformError,
    validate_dataparser_transform,
)
from pipeline.alignment import (
    AlignmentError,
    align_registration,
    load_control_points_json,
)
from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.gaussian_scene import GaussianScene
from pipeline.metric_alignment_evidence import (
    MetricAlignmentDecision,
    MetricAlignmentEvidenceError,
    MetricAlignmentMeasurement,
    MetricAlignmentPolicy,
    canonical_control_points_bytes,
    canonical_metric_alignment_decision_bytes,
    canonical_metric_alignment_measurement_bytes,
    canonical_metric_alignment_policy_bytes,
    decide_metric_alignment,
    load_canonical_control_points_bytes,
    load_metric_alignment_decision_bytes,
    load_metric_alignment_measurement_bytes,
    load_metric_alignment_policy_bytes,
    measure_metric_alignment,
    verify_metric_alignment_decision,
)
from pipeline.production_runtime_evidence import (
    ProductionRuntimeEvidenceError,
    load_production_runtime_decision_bytes,
    load_production_runtime_measurement_bytes,
    load_production_runtime_policy_bytes,
)
from pipeline.production_training_closure import (
    ProductionTrainingClosure,
    ProductionTrainingClosureError,
    load_production_result_manifest_bytes,
    load_production_training_closure_bytes,
    verify_production_training_closure,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    RealSceneTrainingError,
    load_training_job_evaluation_bytes,
    load_training_job_input_bytes,
    verify_production_training_job_bundle,
    verify_training_job_bundle,
)
from pipeline.recon_schema import (
    AlignmentStatus,
    CoordinateUnits,
    FrameProvenance,
    GeoAnchor,
    MetricStatus,
    RegistrationResult,
    SplatInput,
)
from pipeline.reconstruct import reconstruct
from pipeline.reconstruction_artifact_integrity import (
    IntegrityReport,
    verify_recon_artifacts,
)
from pipeline.render_evaluation import (
    RenderDecision,
    RenderEvaluationError,
    RenderEvaluationPolicy,
    RenderEvaluationReport,
    validate_render_evaluation,
)
from pipeline.spatial_chunk import verify_chunks_integrity
from pipeline.training_executor import ExecutorAttemptReceipt
from pipeline.training_provenance import (
    TrainingRequest,
    TrainingResult,
    request_canonical_sha256,
    result_canonical_sha256,
    validate_training_provenance,
)
from scripts.normalize_ply_quats import normalize_quaternions
from scripts.prepare_import import prepare_from_registration


class RealSceneImportError(ValueError):
    """A training or reconstruction artifact cannot enter the scene."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SOURCE_ROLES = Literal["internal-canary", "production-acceptance"]
_QUALITY_ROLES = Literal["preview-only", "production"]

_ONE_MIB = 1024 * 1024


@dataclass(frozen=True)
class PlySemanticReport:
    gaussian_count: int
    sh_degree: int
    non_unit_quaternion_count: int


class ImportArtifactBinding(FrozenModel):
    path: str = Field(min_length=1)
    byte_length: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def _portable_relative_path(cls, value: str) -> str:
        parsed = Path(value)
        if (
            parsed.is_absolute()
            or "\\" in value
            or "\x00" in value
            or parsed.as_posix() != value
            or any(part in {"", ".", ".."} for part in parsed.parts)
        ):
            raise ValueError(
                "import artifact path must be portable and relative"
            )
        return value


class RealSceneImportIntegrity(FrozenModel):
    schema_id: Literal["nantai.real-scene-import-integrity.v1"] = Field(
        default="nantai.real-scene-import-integrity.v1",
        alias="schema",
        serialization_alias="schema",
    )
    recon_artifacts_verified: bool
    chunk_payloads_verified: int = Field(ge=0)
    chunk_payloads_total: int = Field(ge=0)
    coordinate_repack_verified: bool
    scene_gaussian_count: int = Field(ge=1)
    chunk_gaussian_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _all_integrity_claims_are_consistent(
        self,
    ) -> RealSceneImportIntegrity:
        if (
            not self.recon_artifacts_verified
            or self.chunk_payloads_verified != self.chunk_payloads_total
            or not self.coordinate_repack_verified
            or self.scene_gaussian_count != self.chunk_gaussian_count
        ):
            raise ValueError(
                "real-scene import integrity cannot claim partial closure"
            )
        return self


class RealSceneImportReceipt(FrozenModel):
    schema_id: Literal[
        "nantai.real-scene-import-receipt.v2",
        "nantai.real-scene-import-receipt.v3",
    ] = Field(
        default="nantai.real-scene-import-receipt.v3",
        alias="schema",
        serialization_alias="schema",
    )
    source_role: _SOURCE_ROLES
    training_quality_role: _QUALITY_ROLES
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_result_sha256: str = Field(pattern=_SHA256_PATTERN)
    production_training_closure_path: (
        Literal["evidence/production-training-closure.json"] | None
    ) = None
    production_training_closure_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    production_runtime_decision_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    gaussian_count: int = Field(ge=1)
    sh_degree: int = Field(ge=0)
    normalized_quaternion_count: int = Field(ge=0)
    target_frame_id: str = Field(min_length=1)
    target_units: Literal["arbitrary", "meters"]
    geometry_usability: Literal["preview-only", "metric-aligned"]
    chunk_size: float = Field(gt=0.0, allow_inf_nan=False)
    chunk_units: Literal["source-units", "metres"]
    alignment_rms_m: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    alignment_source_registration_path: (
        Literal["alignment/source-registration.json"] | None
    ) = None
    alignment_control_points_path: (
        Literal["alignment/control-points.json"] | None
    ) = None
    alignment_observed_registration_path: (
        Literal["alignment/observed-registration.json"] | None
    ) = None
    alignment_measurement_path: (
        Literal["alignment/measurement.json"] | None
    ) = None
    alignment_policy_path: Literal["alignment/policy.json"] | None = None
    alignment_decision_path: (
        Literal["alignment/decision.json"] | None
    ) = None
    alignment_measurement_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    alignment_policy_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    alignment_decision_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    source_ply_path: Literal["inputs/source.ply"] = "inputs/source.ply"
    normalized_ply_path: Literal["inputs/normalized.ply"] = (
        "inputs/normalized.ply"
    )
    registration_path: Literal["contracts/registration.json"] = (
        "contracts/registration.json"
    )
    splat_input_path: Literal["contracts/splat-input.json"] = (
        "contracts/splat-input.json"
    )
    manifest_path: Literal["web/recon_manifest.json"] = (
        "web/recon_manifest.json"
    )
    chunks_manifest_path: Literal["web/chunks/chunks.json"] = (
        "web/chunks/chunks.json"
    )
    integrity_report_path: Literal["import-integrity.json"] = (
        "import-integrity.json"
    )
    artifacts: tuple[ImportArtifactBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _receipt_is_self_consistent(self) -> RealSceneImportReceipt:
        paths = tuple(binding.path for binding in self.artifacts)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError(
                "import artifact bindings must be path sorted and unique"
            )
        required = {
            self.source_ply_path,
            self.normalized_ply_path,
            self.registration_path,
            self.splat_input_path,
            self.manifest_path,
            self.chunks_manifest_path,
            self.integrity_report_path,
        }
        alignment_paths = (
            self.alignment_source_registration_path,
            self.alignment_control_points_path,
            self.alignment_observed_registration_path,
            self.alignment_measurement_path,
            self.alignment_policy_path,
            self.alignment_decision_path,
        )
        alignment_shas = (
            self.alignment_measurement_sha256,
            self.alignment_policy_sha256,
            self.alignment_decision_sha256,
        )
        production_closure_fields = (
            self.production_training_closure_path,
            self.production_training_closure_sha256,
            self.production_runtime_decision_sha256,
        )
        if not required <= set(paths):
            raise ValueError(
                "import receipt is missing a required artifact binding"
            )
        if self.source_role == "production-acceptance":
            if (
                self.training_quality_role != "production"
                or self.schema_id
                != "nantai.real-scene-import-receipt.v3"
                or self.gaussian_count < 100_000
                or self.target_units != "meters"
                or self.geometry_usability != "metric-aligned"
                or self.chunk_units != "metres"
                or self.alignment_rms_m is None
                or self.alignment_rms_m > 0.25
                or any(value is None for value in alignment_paths)
                or any(value is None for value in alignment_shas)
                or any(
                    value is None
                    for value in production_closure_fields
                )
            ):
                raise ValueError(
                    "production import requires production training, "
                    "at least 100000 Gaussians, and verified metric evidence"
                )
            if not set(alignment_paths) <= set(paths):
                raise ValueError(
                    "production import is missing bound alignment evidence"
                )
            if self.production_training_closure_path not in paths:
                raise ValueError(
                    "production import is missing bound training closure"
                )
        elif (
            self.target_units != "arbitrary"
            or self.geometry_usability != "preview-only"
            or self.chunk_units != "source-units"
            or self.alignment_rms_m is not None
            or any(value is not None for value in alignment_paths)
            or any(value is not None for value in alignment_shas)
            or any(
                value is not None for value in production_closure_fields
            )
        ):
            raise ValueError(
                "internal canary must remain arbitrary and preview-only"
            )
        return self


@dataclass(frozen=True)
class _TrainingMaterial:
    quality_role: Literal["preview-only", "production"]
    bundle_sha256: str
    request: TrainingRequest
    result: TrainingResult
    attempt: ExecutorAttemptReceipt
    source_ply: Path
    source_ply_bytes: bytes
    config_bytes: bytes
    log_bytes: bytes
    dataparser_bytes: bytes | None
    input_bytes_by_path: dict[str, bytes]
    registration: RegistrationResult


def inspect_real_scene_ply(
    path: Path,
    *,
    minimum_gaussians: int | None = None,
) -> PlySemanticReport:
    """Inspect an external 3DGS PLY before normalizing a copied artifact."""

    source = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(source.anchor), source)
        source_stat = source.lstat()
        if (
            redirected is not None
            or stat.S_ISLNK(source_stat.st_mode)
            or not stat.S_ISREG(source_stat.st_mode)
            or _is_linklike(source, observed=source_stat)
        ):
            raise RealSceneImportError(
                "source PLY must be a regular non-link file"
            )
        ply = PlyData.read(str(source), mmap=False)
        vertex = ply["vertex"]
    except RealSceneImportError:
        raise
    except (OSError, KeyError, ValueError) as exc:
        raise RealSceneImportError(
            f"source PLY cannot be parsed: {exc}"
        ) from exc

    data = vertex.data
    names = data.dtype.names
    if names is None or data.dtype.hasobject:
        raise RealSceneImportError(
            "source PLY requires scalar numeric vertex properties"
        )
    required = {
        "x",
        "y",
        "z",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        "opacity",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    }
    missing = sorted(required - set(names))
    if missing:
        raise RealSceneImportError(
            "source 3DGS PLY is missing required properties: "
            + ", ".join(missing)
        )
    if len(data) == 0:
        raise RealSceneImportError("source 3DGS PLY contains no Gaussians")
    if (
        minimum_gaussians is not None
        and len(data) < minimum_gaussians
    ):
        raise RealSceneImportError(
            "source 3DGS PLY contains "
            f"{len(data)} Gaussians; production requires at least "
            f"{minimum_gaussians}"
        )

    for name in names:
        values = np.asarray(data[name])
        if not np.issubdtype(values.dtype, np.number):
            raise RealSceneImportError(
                f"source PLY property {name} is not numeric"
            )
        if not np.all(np.isfinite(values)):
            raise RealSceneImportError(
                f"source PLY property {name} contains non-finite values"
            )

    rest_names = [name for name in names if name.startswith("f_rest_")]
    try:
        rest_names.sort(key=lambda name: int(name.removeprefix("f_rest_")))
    except ValueError as exc:
        raise RealSceneImportError(
            "source PLY f_rest properties require integer indices"
        ) from exc
    expected_rest = [
        f"f_rest_{index}" for index in range(len(rest_names))
    ]
    if rest_names != expected_rest:
        raise RealSceneImportError(
            "source PLY f_rest properties must be contiguous from zero"
        )
    if len(rest_names) % 3:
        raise RealSceneImportError(
            "source PLY f_rest properties do not contain complete RGB SH"
        )
    coefficients_per_channel = len(rest_names) // 3
    root = math.isqrt(coefficients_per_channel + 1)
    if root * root != coefficients_per_channel + 1:
        raise RealSceneImportError(
            "source PLY f_rest properties do not form a complete SH degree"
        )
    sh_degree = root - 1

    quaternions = np.stack(
        [np.asarray(data[f"rot_{index}"], dtype=np.float64)
         for index in range(4)],
        axis=1,
    )
    norms = np.linalg.norm(quaternions, axis=1)
    if np.any(norms < 1e-12):
        raise RealSceneImportError(
            "source PLY contains a zero quaternion"
        )
    non_unit = ~np.isclose(norms, 1.0, rtol=1e-3, atol=1e-4)
    return PlySemanticReport(
        gaussian_count=len(data),
        sh_degree=sh_degree,
        non_unit_quaternion_count=int(np.count_nonzero(non_unit)),
    )


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


def _read_regular_bytes(
    path: Path,
    *,
    label: str,
    allow_empty: bool = False,
) -> bytes:
    candidate = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(candidate.anchor), candidate)
        before = candidate.lstat()
        if (
            redirected is not None
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or _is_linklike(candidate, observed=before)
        ):
            raise RealSceneImportError(
                f"{label} is missing or link-like"
            )
        payload = candidate.read_bytes()
        after = candidate.lstat()
    except RealSceneImportError:
        raise
    except OSError as exc:
        raise RealSceneImportError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RealSceneImportError(f"{label} changed while being read")
    if not allow_empty and not payload:
        raise RealSceneImportError(f"{label} is empty")
    return payload


def _stream_regular_digest(
    path: Path,
    *,
    label: str,
    allow_empty: bool = False,
) -> tuple[int, str]:
    """Stream a regular file in bounded chunks (<= 1 MiB) returning
    (byte_length, sha256_hex).

    Probes lstat -> open -> fstat before reading, fstat after reading, then
    path lstat after close, rejecting symlink/junction/non-regular members
    and any device/inode/mode/size/mtime drift.  Uses ``os.read`` so callers
    can intercept reads via monkeypatch for mid-read modification tests.
    """

    candidate = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(candidate.anchor), candidate)
        before_lstat = candidate.lstat()
    except OSError as exc:
        raise RealSceneImportError(f"{label} cannot be read") from exc
    if (
        redirected is not None
        or stat.S_ISLNK(before_lstat.st_mode)
        or not stat.S_ISREG(before_lstat.st_mode)
        or _is_linklike(candidate, observed=before_lstat)
    ):
        raise RealSceneImportError(f"{label} is missing or link-like")

    try:
        handle = os.open(candidate, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise RealSceneImportError(f"{label} cannot be read") from exc
    try:
        fd = handle
        try:
            before_fstat = os.fstat(fd)
        except OSError as exc:
            raise RealSceneImportError(f"{label} cannot be read") from exc
        if _stat_signature(before_lstat) != _stat_signature(before_fstat):
            raise RealSceneImportError(
                f"{label} changed while being read"
            )

        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                chunk = os.read(fd, _ONE_MIB)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
        except OSError as exc:
            raise RealSceneImportError(f"{label} cannot be read") from exc

        try:
            after_fstat = os.fstat(fd)
        except OSError as exc:
            raise RealSceneImportError(f"{label} cannot be read") from exc
        if _stat_signature(before_fstat) != _stat_signature(after_fstat):
            raise RealSceneImportError(
                f"{label} changed while being read"
            )
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    try:
        after_lstat = candidate.lstat()
    except OSError as exc:
        raise RealSceneImportError(f"{label} cannot be read") from exc
    if _stat_signature(before_lstat) != _stat_signature(after_lstat):
        raise RealSceneImportError(f"{label} changed while being read")

    if not allow_empty and total == 0:
        raise RealSceneImportError(f"{label} is empty")

    return total, digest.hexdigest()


def _load_canonical_model(
    path: Path,
    model_type,
    *,
    label: str,
):
    payload = _read_regular_bytes(path, label=label)
    try:
        model = model_type.model_validate_json(payload)
    except ValueError as exc:
        raise RealSceneImportError(f"{label} is invalid") from exc
    if payload != canonical_model_bytes(model):
        raise RealSceneImportError(f"{label} is not canonical JSON")
    return model, payload


def _one_output_binding(
    result: TrainingResult,
    kind: str,
):
    matches = tuple(
        binding
        for binding in result.output_bindings
        if binding.artifact_kind == kind
    )
    if len(matches) != 1:
        raise RealSceneImportError(
            f"training result requires exactly one {kind} binding"
        )
    return matches[0]


def _load_training_material(
    training_root: Path,
) -> _TrainingMaterial:
    root = Path(training_root).expanduser().absolute()
    try:
        redirected_root = first_linklike_path(Path(root.anchor), root)
        root_stat = root.lstat()
    except OSError as exc:
        raise RealSceneImportError(
            "training stage root is unavailable"
        ) from exc
    if (
        redirected_root is not None
        or stat.S_ISLNK(root_stat.st_mode)
        or not stat.S_ISDIR(root_stat.st_mode)
        or _is_linklike(root, observed=root_stat)
    ):
        raise RealSceneImportError(
            "training stage root must be a real directory"
        )
    local_root = root / "local-brush"
    remote_root = root / "remote-result"
    available = (
        local_root.is_dir() and not _is_linklike(local_root),
        remote_root.is_dir() and not _is_linklike(remote_root),
    )
    if sum(available) != 1:
        raise RealSceneImportError(
            "training stage must contain exactly one local or remote result"
        )
    if available[0]:
        quality_role: Literal["preview-only", "production"] = "preview-only"
        result_root = local_root
        attempt_path = result_root / "executor-attempt.json"
        source_ply = result_root / "workspace/trained.brush-export.ply"
        config_path = result_root / "operator-intent-config.yml"
        log_path = result_root / "workspace/brush.log"
        dataparser_path = None
    else:
        quality_role = "production"
        result_root = remote_root
        attempt_path = root / "executor-attempt.json"
        source_ply = result_root / "point_cloud.ply"
        config_path = result_root / "operator-intent-config.yml"
        log_path = result_root / "training.log"
        dataparser_path = result_root / "dataparser_transforms.json"

    attempt, _attempt_bytes = _load_canonical_model(
        attempt_path,
        ExecutorAttemptReceipt,
        label="executor attempt receipt",
    )
    request, request_bytes = _load_canonical_model(
        result_root / "training-request.json",
        TrainingRequest,
        label="training request",
    )
    result, _result_bytes = _load_canonical_model(
        result_root / "training-result.json",
        TrainingResult,
        label="training result",
    )
    source_ply_bytes = _read_regular_bytes(
        source_ply,
        label="trained PLY",
    )
    config_bytes = _read_regular_bytes(
        config_path,
        label="training config",
    )
    log_bytes = _read_regular_bytes(
        log_path,
        label="training log",
        allow_empty=True,
    )
    dataparser_bytes = (
        _read_regular_bytes(
            dataparser_path,
            label="dataparser transform",
        )
        if dataparser_path is not None
        else None
    )

    bundle_path = root / "training-bundle/training-job.zip"
    try:
        bundle = (
            verify_production_training_job_bundle(bundle_path)
            if quality_role == "production"
            else verify_training_job_bundle(bundle_path)
        )
        input_bytes = load_training_job_input_bytes(bundle)
    except RealSceneTrainingError as exc:
        raise RealSceneImportError(
            f"training bundle cannot be revalidated: {exc}"
        ) from exc

    if quality_role == "production":
        if request != bundle.request:
            raise RealSceneImportError(
                "production result request differs from training bundle"
            )
    elif request.input_bindings != bundle.request.input_bindings:
        raise RealSceneImportError(
            "preview result inputs differ from training bundle"
        )
    if (
        attempt.state != "succeeded"
        or attempt.quality_role != quality_role
        or attempt.request_sha256 != request_canonical_sha256(request)
        or attempt.dataset_receipt_sha256
        != bundle.manifest.dataset_receipt_sha256
        or attempt.training_config_sha256
        != hashlib.sha256(config_bytes).hexdigest()
        or attempt.trainer_name != request.training_config.trainer_name
        or attempt.trainer_version
        != request.training_config.trainer_version
    ):
        raise RealSceneImportError(
            "executor attempt identity differs from training result"
        )
    if quality_role == "preview-only":
        if (
            attempt.executor_kind != "local-brush"
            or attempt.result_bundle_sha256
            != hashlib.sha256(
                canonical_model_bytes(result)
            ).hexdigest()
        ):
            raise RealSceneImportError(
                "preview-only executor receipt is not content-closed"
            )
    else:
        archive_bytes = _read_regular_bytes(
            remote_root / "result-bundle.zip",
            label="remote result bundle",
        )
        if (
            attempt.executor_kind != "remote-shell-nerfstudio"
            or attempt.result_bundle_sha256
            != hashlib.sha256(archive_bytes).hexdigest()
        ):
            raise RealSceneImportError(
                "production executor receipt is not content-closed"
            )
        try:
            validate_dataparser_transform(dataparser_path)
        except DataparserTransformError as exc:
            raise RealSceneImportError(
                f"dataparser transform is unsafe: {exc}"
            ) from exc

    try:
        validate_training_provenance(
            result,
            request,
            actual_ply_bytes=source_ply_bytes,
            actual_config_bytes=config_bytes,
            actual_log_bytes=log_bytes,
            actual_dataparser_transform_bytes=dataparser_bytes,
            input_bytes_by_path=input_bytes,
        )
    except ValueError as exc:
        raise RealSceneImportError(
            f"training result is not content-closed: {exc}"
        ) from exc
    if result.training_status.state != "completed":
        raise RealSceneImportError(
            "training result is not a completed run"
        )

    registration_bindings = tuple(
        binding
        for binding in request.input_bindings
        if binding.artifact_kind == "registration_json"
    )
    if len(registration_bindings) != 1:
        raise RealSceneImportError(
            "training request requires one registration binding"
        )
    registration_bytes = input_bytes.get(
        registration_bindings[0].artifact_path
    )
    if registration_bytes is None:
        raise RealSceneImportError(
            "training registration bytes are unavailable"
        )
    try:
        registration = RegistrationResult.model_validate_json(
            registration_bytes
        )
    except ValueError as exc:
        raise RealSceneImportError(
            "training registration is invalid"
        ) from exc
    if (
        registration.engine != "colmap"
        or registration.pose_frame.provenance is not FrameProvenance.SFM
    ):
        raise RealSceneImportError(
            "real-scene import requires non-mock COLMAP registration"
        )
    return _TrainingMaterial(
        quality_role=quality_role,
        bundle_sha256=bundle.bundle_sha256,
        request=request,
        result=result,
        attempt=attempt,
        source_ply=source_ply,
        source_ply_bytes=source_ply_bytes,
        config_bytes=config_bytes,
        log_bytes=log_bytes,
        dataparser_bytes=dataparser_bytes,
        input_bytes_by_path=input_bytes,
        registration=registration,
    )


def _verify_production_closure_evidence(
    training_root: Path,
    material: _TrainingMaterial,
    closure: ProductionTrainingClosure,
) -> None:
    result_root = (
        Path(training_root).expanduser().absolute() / "remote-result"
    )
    try:
        manifest = load_production_result_manifest_bytes(
            _read_regular_bytes(
                result_root / "result-bundle-manifest.json",
                label="production result manifest",
            )
        )
        runtime_measurement = (
            load_production_runtime_measurement_bytes(
                _read_regular_bytes(
                    result_root
                    / "production-runtime/measurement.json",
                    label="production runtime measurement",
                )
            )
        )
        runtime_policy = load_production_runtime_policy_bytes(
            _read_regular_bytes(
                result_root / "production-runtime/policy.json",
                label="production runtime policy",
            )
        )
        runtime_decision = load_production_runtime_decision_bytes(
            _read_regular_bytes(
                result_root / "production-runtime/decision.json",
                label="production runtime decision",
            )
        )
        render_policy, _ = _load_canonical_model(
            result_root / "render-evaluation/policy.json",
            RenderEvaluationPolicy,
            label="render evaluation policy",
        )
        render_report, _ = _load_canonical_model(
            result_root / "render-evaluation/report.json",
            RenderEvaluationReport,
            label="render evaluation report",
        )
        render_decision, _ = _load_canonical_model(
            result_root / "render-evaluation/decision.json",
            RenderDecision,
            label="render evaluation decision",
        )
        member_payloads: dict[str, bytes] = {}
        for member in manifest.members:
            payload = _read_regular_bytes(
                result_root / member.path,
                label=f"production result member {member.path}",
                allow_empty=member.path
                in {"worker.stdout.log", "worker.stderr.log"},
            )
            if (
                len(payload) != member.byte_length
                or hashlib.sha256(payload).hexdigest()
                != member.sha256
            ):
                raise RealSceneImportError(
                    "production result manifest member differs: "
                    f"{member.path}"
                )
            member_payloads[member.path] = payload
        bundle = verify_production_training_job_bundle(
            Path(training_root).expanduser().absolute()
            / "training-bundle/training-job.zip"
        )
        if bundle.bundle_sha256 != material.bundle_sha256:
            raise RealSceneImportError(
                "production training bundle changed before render "
                "revalidation"
            )
        evaluation_sources = load_training_job_evaluation_bytes(
            bundle
        )
        split_bytes = material.input_bytes_by_path.get(
            "training/held-out-split.json"
        )
        if split_bytes is None:
            raise RealSceneImportError(
                "production held-out split bytes are unavailable"
            )
        with tempfile.TemporaryDirectory(
            prefix="nantai-import-render-",
        ) as temporary:
            evaluation_root = Path(temporary) / "run"
            split_path = (
                evaluation_root
                / "prepared/evidence/held-out-split.json"
            )
            split_path.parent.mkdir(parents=True)
            split_path.write_bytes(split_bytes)
            transforms_path = (
                evaluation_root / "prepared/transforms.json"
            )
            transforms_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            transforms_path.write_bytes(
                member_payloads[
                    "render-evaluation/transforms.json"
                ]
            )
            for logical_path, payload in evaluation_sources.items():
                source_path = (
                    evaluation_root / "prepared/images" / logical_path
                )
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(payload)
            for relative, payload in member_payloads.items():
                if not relative.startswith("render-evaluation/"):
                    continue
                target = evaluation_root / "result" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            derived_render_decision = validate_render_evaluation(
                render_policy,
                render_report,
                evaluation_root,
            )
        if derived_render_decision != render_decision:
            raise RealSceneImportError(
                "render evaluation decision differs from reopened bytes"
            )
        verify_production_training_closure(
            closure=closure,
            training_bundle_sha256=material.bundle_sha256,
            result_bundle_archive_sha256=(
                material.attempt.result_bundle_sha256
            ),
            manifest=manifest,
            attempt=material.attempt,
            request=material.request,
            result=material.result,
            runtime_measurement=runtime_measurement,
            runtime_policy=runtime_policy,
            runtime_decision=runtime_decision,
            render_policy=render_policy,
            render_report=render_report,
            render_decision=render_decision,
        )
    except (
        OSError,
        ProductionRuntimeEvidenceError,
        ProductionTrainingClosureError,
        RealSceneImportError,
        RealSceneTrainingError,
        RenderEvaluationError,
        ValueError,
    ) as exc:
        if isinstance(exc, RealSceneImportError):
            raise
        raise RealSceneImportError(
            "production closure runtime, manifest, or render evidence "
            "cannot be revalidated"
        ) from exc


def _recon_integrity_is_closed(report: IntegrityReport) -> bool:
    chunks = report.chunks_report
    return (
        not report.mismatch
        and not report.unknown
        and not report.path_safety_violations
        and not report.duplicate_paths
        and not report.duplicate_json_keys
        and not report.contradictions
        and chunks is not None
        and chunks.total_chunks_matches_len
        and chunks.total_points_matches_sum
        and chunks.bounds_consistent_with_aabbs
        and not chunks.missing_chunk_files
        and not chunks.duplicate_chunk_paths
        and not chunks.extra_unbound_chunk_files
        and chunks.per_chunk_sha_verified is True
        and not chunks.payload_integrity_mismatches
    )


def _ply_vertex_rows(
    path: Path,
) -> tuple[tuple, np.ndarray]:
    """Return the exact vertex schema and an owned structured row array."""

    try:
        vertex = PlyData.read(str(path), mmap=False)["vertex"].data
    except (OSError, KeyError, ValueError) as exc:
        raise RealSceneImportError(
            f"chunk PLY cannot be reopened: {path.name}"
        ) from exc
    if vertex.dtype.names is None or vertex.dtype.hasobject:
        raise RealSceneImportError(
            "chunk PLY requires scalar vertex properties"
        )
    return tuple(vertex.dtype.descr), np.ascontiguousarray(vertex)


def _canonical_vertex_bytes(rows: np.ndarray) -> bytes:
    names = rows.dtype.names
    if names is None:
        raise RealSceneImportError("PLY vertex schema has no properties")
    # np.lexsort is vectorized and materially faster than hashing or sorting
    # Python objects for million-Gaussian scenes.  The first PLY property is
    # the primary key; subsequent properties deterministically break ties.
    order = np.lexsort(
        tuple(np.asarray(rows[name]) for name in reversed(names))
    )
    return np.ascontiguousarray(rows[order]).tobytes()


def _verify_coordinate_repack(
    root: Path,
    manifest: dict,
) -> tuple[int, int]:
    scene = GaussianScene.load_ply(
        root / "recon/scene_full.ply",
        require_3dgs=True,
    )
    scene_schema, scene_rows = _ply_vertex_rows(
        root / "recon/scene_full.ply"
    )
    chunks_path = root / "web/chunks/chunks.json"
    try:
        chunks_manifest = json.loads(chunks_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RealSceneImportError(
            "chunk manifest cannot be reopened"
        ) from exc
    chunk_scenes: list[GaussianScene] = []
    chunk_row_sets: list[np.ndarray] = []
    for chunk in chunks_manifest.get("chunks", []):
        full_path = root / "web/chunks" / chunk["lod"]["2"]
        loaded = GaussianScene.load_ply(full_path, require_3dgs=True)
        chunk_schema, rows = _ply_vertex_rows(full_path)
        if chunk_schema != scene_schema:
            raise RealSceneImportError(
                "chunk vertex schema differs from source scene"
            )
        chunk_row_sets.append(rows)
        if len(loaded) != chunk["point_count"]:
            raise RealSceneImportError(
                "chunk point count differs from its PLY"
            )
        if (
            loaded.frame_id != scene.frame_id
            or loaded.units != scene.units
            or loaded.applied_transform_ids
            != scene.applied_transform_ids
        ):
            raise RealSceneImportError(
                "chunk coordinate contract differs from source scene"
            )
        chunk_scenes.append(loaded)
    if not chunk_scenes:
        raise RealSceneImportError("reconstruction emitted no chunks")
    chunk_count = sum(len(loaded) for loaded in chunk_scenes)
    combined_chunk_rows = np.concatenate(chunk_row_sets)
    if (
        len(scene) != chunk_count
        or _canonical_vertex_bytes(scene_rows)
        != _canonical_vertex_bytes(combined_chunk_rows)
    ):
        raise RealSceneImportError(
            "chunking changed, duplicated, or dropped Gaussian records"
        )
    target = manifest["coordinate_contract"]["target_frame"]
    source = chunks_manifest.get("source", {})
    if (
        source.get("frame_id") != target["frame_id"]
        or source.get("units") != target["units"]
        or source.get("geometry_usability")
        != manifest["provenance"]["geometry_usability"]
    ):
        raise RealSceneImportError(
            "chunk manifest changed coordinate or trust provenance"
        )
    return len(scene), chunk_count


def _write_new(path: Path, payload: bytes) -> None:
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
        raise RealSceneImportError(
            f"cannot materialize import artifact: {path.name}"
        ) from exc


def _regular_output_files(
    root: Path,
    *,
    exclude_receipt: bool,
) -> tuple[Path, ...]:
    files: list[Path] = []
    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = parent / name
            try:
                candidate_stat = candidate.lstat()
            except OSError as exc:
                raise RealSceneImportError(
                    "import output cannot be enumerated"
                ) from exc
            mode = candidate_stat.st_mode
            if (
                stat.S_ISLNK(mode)
                or _is_linklike(candidate, observed=candidate_stat)
            ):
                raise RealSceneImportError(
                    "import output contains a link"
                )
            if stat.S_ISREG(mode):
                if (
                    exclude_receipt
                    and candidate == root / "import-receipt.json"
                ):
                    continue
                files.append(candidate)
            elif not stat.S_ISDIR(mode):
                raise RealSceneImportError(
                    "import output contains a non-regular member"
                )
    return tuple(
        sorted(files, key=lambda path: path.relative_to(root).as_posix())
    )


def _artifact_bindings(root: Path) -> tuple[ImportArtifactBinding, ...]:
    bindings: list[ImportArtifactBinding] = []
    for path in _regular_output_files(root, exclude_receipt=True):
        byte_length, sha256 = _stream_regular_digest(
            path,
            label=f"import artifact {path.name}",
            allow_empty=True,
        )
        bindings.append(
            ImportArtifactBinding(
                path=path.relative_to(root).as_posix(),
                byte_length=byte_length,
                sha256=sha256,
            )
        )
    return tuple(bindings)


def _validate_manifest_claims(
    root: Path,
    receipt: RealSceneImportReceipt,
) -> None:
    try:
        manifest = json.loads(
            (root / receipt.manifest_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise RealSceneImportError(
            "reconstruction manifest cannot be reopened"
        ) from exc
    target = manifest.get("coordinate_contract", {}).get(
        "target_frame", {}
    )
    if (
        manifest.get("gaussian_count") != receipt.gaussian_count
        or target.get("frame_id") != receipt.target_frame_id
        or target.get("units") != receipt.target_units
        or manifest.get("provenance", {}).get("geometry_usability")
        != receipt.geometry_usability
    ):
        raise RealSceneImportError(
            "import receipt differs from reconstruction manifest"
        )


def _validate_metric_alignment_claims(
    root: Path,
    receipt: RealSceneImportReceipt,
) -> None:
    if receipt.source_role != "production-acceptance":
        return
    alignment_paths = (
        receipt.alignment_source_registration_path,
        receipt.alignment_control_points_path,
        receipt.alignment_observed_registration_path,
        receipt.alignment_measurement_path,
        receipt.alignment_policy_path,
        receipt.alignment_decision_path,
    )
    if any(value is None for value in alignment_paths):
        raise RealSceneImportError(
            "production import alignment evidence paths are incomplete"
        )
    (
        source_path,
        control_points_path,
        observed_path,
        measurement_path,
        policy_path,
        decision_path,
    ) = alignment_paths
    assert source_path is not None
    assert control_points_path is not None
    assert observed_path is not None
    assert measurement_path is not None
    assert policy_path is not None
    assert decision_path is not None
    source, _source_bytes = _load_canonical_model(
        root / source_path,
        RegistrationResult,
        label="source alignment registration",
    )
    observed, _observed_bytes = _load_canonical_model(
        root / observed_path,
        RegistrationResult,
        label="observed aligned registration",
    )
    prepared_bytes = _read_regular_bytes(
        root / receipt.registration_path,
        label="prepared import registration",
    )
    try:
        prepared = RegistrationResult.model_validate_json(prepared_bytes)
    except ValueError as exc:
        raise RealSceneImportError(
            "prepared import registration is invalid"
        ) from exc
    try:
        control_points = load_canonical_control_points_bytes(
            _read_regular_bytes(
                root / control_points_path,
                label="metric alignment control points",
            )
        )
        measurement = load_metric_alignment_measurement_bytes(
            _read_regular_bytes(
                root / measurement_path,
                label="metric alignment measurement",
            )
        )
        policy = load_metric_alignment_policy_bytes(
            _read_regular_bytes(
                root / policy_path,
                label="metric alignment policy",
            )
        )
        decision = load_metric_alignment_decision_bytes(
            _read_regular_bytes(
                root / decision_path,
                label="metric alignment decision",
            )
        )
        verify_metric_alignment_decision(
            source_registration=source,
            control_points=control_points,
            aligned_registration=observed,
            measurement=measurement,
            policy=policy,
            decision=decision,
        )
    except MetricAlignmentEvidenceError as exc:
        raise RealSceneImportError(
            f"metric alignment evidence is invalid: {exc}"
        ) from exc
    if (
        decision.status != "accepted"
        or receipt.alignment_measurement_sha256
        != measurement.content_sha256
        or receipt.alignment_policy_sha256 != policy.content_sha256
        or receipt.alignment_decision_sha256 != decision.content_sha256
        or receipt.target_frame_id != observed.target_frame.frame_id
        or prepared.target_frame != observed.target_frame
        or prepared.world_frame != observed.world_frame
        or prepared.pose_to_world != observed.pose_to_world
        or receipt.alignment_rms_m != measurement.rms_residual_m
    ):
        raise RealSceneImportError(
            "production import metric decision differs from receipt output"
        )


def _validate_production_closure_claims(
    root: Path,
    receipt: RealSceneImportReceipt,
) -> None:
    if receipt.source_role != "production-acceptance":
        return
    path = receipt.production_training_closure_path
    if path is None:
        raise RealSceneImportError(
            "production import training closure path is missing"
        )
    try:
        closure = load_production_training_closure_bytes(
            _read_regular_bytes(
                root / path,
                label="bound production training closure",
            )
        )
    except ProductionTrainingClosureError as exc:
        raise RealSceneImportError(
            "bound production training closure is invalid"
        ) from exc
    if (
        receipt.production_training_closure_sha256
        != closure.content_sha256
        or receipt.production_runtime_decision_sha256
        != closure.runtime_decision_sha256
        or receipt.training_bundle_sha256
        != closure.training_bundle_sha256
        or receipt.training_request_sha256 != closure.request_sha256
        or receipt.training_result_sha256 != closure.result_sha256
        or receipt.gaussian_count != closure.gaussian_count
        or receipt.sh_degree != closure.sh_degree
    ):
        raise RealSceneImportError(
            "production import receipt differs from training closure"
        )


def validate_real_scene_import_receipt(
    receipt_path: Path,
    output_root: Path,
) -> RealSceneImportReceipt:
    """Reopen every emitted byte and re-derive the import integrity gates."""

    root = Path(output_root).expanduser().absolute()
    receipt, _raw = _load_canonical_model(
        receipt_path,
        RealSceneImportReceipt,
        label="real-scene import receipt",
    )
    current_files = _regular_output_files(root, exclude_receipt=True)
    current_paths = tuple(
        path.relative_to(root).as_posix() for path in current_files
    )
    declared_paths = tuple(binding.path for binding in receipt.artifacts)
    if current_paths != declared_paths:
        raise RealSceneImportError(
            "import output file set differs from receipt"
        )
    for binding in receipt.artifacts:
        byte_length, sha256 = _stream_regular_digest(
            root / binding.path,
            label=f"bound import artifact {binding.path}",
            allow_empty=True,
        )
        if (
            byte_length != binding.byte_length
            or sha256 != binding.sha256
        ):
            raise RealSceneImportError(
                f"import artifact sha256/size mismatch: {binding.path}"
            )
    _validate_metric_alignment_claims(root, receipt)
    _validate_production_closure_claims(root, receipt)
    _validate_manifest_claims(root, receipt)
    recon_report = verify_recon_artifacts(root / receipt.manifest_path)
    if not _recon_integrity_is_closed(recon_report):
        raise RealSceneImportError(
            "reconstruction artifact integrity is not closed"
        )
    chunks = verify_chunks_integrity(
        (root / receipt.chunks_manifest_path).parent
    )
    if not chunks["valid"]:
        raise RealSceneImportError(
            "chunk payload integrity is not closed"
        )
    scene_count, chunk_count = _verify_coordinate_repack(
        root,
        json.loads((root / receipt.manifest_path).read_text(encoding="utf-8")),
    )
    if scene_count != receipt.gaussian_count or chunk_count != scene_count:
        raise RealSceneImportError(
            "receipt Gaussian counts differ from reconstructed bytes"
        )
    declared_integrity, _integrity_bytes = _load_canonical_model(
        root / receipt.integrity_report_path,
        RealSceneImportIntegrity,
        label="real-scene import integrity report",
    )
    derived_integrity = RealSceneImportIntegrity(
        recon_artifacts_verified=True,
        chunk_payloads_verified=chunks["verified_payloads"],
        chunk_payloads_total=chunks["total_payloads"],
        coordinate_repack_verified=True,
        scene_gaussian_count=scene_count,
        chunk_gaussian_count=chunk_count,
    )
    if declared_integrity != derived_integrity:
        raise RealSceneImportError(
            "real-scene import integrity report differs from verified bytes"
        )
    return receipt


def import_real_scene(
    training_root: Path,
    output_root: Path,
    *,
    source_role: _SOURCE_ROLES,
    control_points_path: Path | None = None,
    geo_origin: tuple[float, float, float] | None = None,
    chunk_size: float = 50.0,
) -> RealSceneImportReceipt:
    """Import one verified training stage into a fully closed scene revision."""

    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, (int, float))
        or not math.isfinite(float(chunk_size))
        or float(chunk_size) <= 0
    ):
        raise RealSceneImportError(
            "chunk_size must be a finite positive number"
        )
    material = _load_training_material(training_root)
    production_closure: ProductionTrainingClosure | None = None
    closure_bytes: bytes | None = None
    if (
        source_role == "production-acceptance"
        and material.quality_role != "production"
    ):
        raise RealSceneImportError(
            "preview-only Brush training cannot satisfy production import"
        )
    if source_role == "production-acceptance":
        closure_path = (
            Path(training_root).expanduser().absolute()
            / "remote-result/production-training-closure.json"
        )
        try:
            closure_bytes = _read_regular_bytes(
                closure_path,
                label="production training closure",
            )
            production_closure = load_production_training_closure_bytes(
                closure_bytes
            )
        except (
            OSError,
            ProductionTrainingClosureError,
            RealSceneImportError,
        ) as exc:
            raise RealSceneImportError(
                "production training closure is missing or invalid"
            ) from exc
        required_closure_evidence = (
            "result-bundle-manifest.json",
            "production-runtime/measurement.json",
            "production-runtime/policy.json",
            "production-runtime/decision.json",
            "render-evaluation/policy.json",
            "render-evaluation/report.json",
            "render-evaluation/decision.json",
        )
        if any(
            (closure_path.parent / relative).is_symlink()
            or not (closure_path.parent / relative).is_file()
            for relative in required_closure_evidence
        ):
            raise RealSceneImportError(
                "production closure raw runtime, manifest, or render "
                "evidence is missing"
            )
        trained = _one_output_binding(material.result, "trained_ply")
        dataparser = _one_output_binding(
            material.result,
            "dataparser_transform_json",
        )
        if (
            production_closure.job_id != material.attempt.job_id
            or production_closure.attempt_id
            != material.attempt.attempt_id
            or production_closure.training_bundle_sha256
            != material.bundle_sha256
            or production_closure.result_bundle_archive_sha256
            != material.attempt.result_bundle_sha256
            or production_closure.request_sha256
            != request_canonical_sha256(material.request)
            or production_closure.result_sha256
            != result_canonical_sha256(material.result)
            or production_closure.point_cloud_sha256
            != trained.artifact_sha256
            or production_closure.trainer_config_sha256
            != hashlib.sha256(material.config_bytes).hexdigest()
            or production_closure.training_log_sha256
            != hashlib.sha256(material.log_bytes).hexdigest()
            or production_closure.dataparser_transform_sha256
            != dataparser.artifact_sha256
        ):
            raise RealSceneImportError(
                "production training closure identity differs from "
                "reopened training evidence"
            )
        _verify_production_closure_evidence(
            training_root,
            material,
            production_closure,
        )
    minimum = 100_000 if source_role == "production-acceptance" else None
    semantics = inspect_real_scene_ply(
        material.source_ply,
        minimum_gaussians=minimum,
    )
    trained_binding = _one_output_binding(
        material.result,
        "trained_ply",
    )
    if (
        trained_binding.gaussian_count is not None
        and trained_binding.gaussian_count != semantics.gaussian_count
    ):
        raise RealSceneImportError(
            "training result Gaussian count differs from PLY"
        )
    if (
        trained_binding.sh_degree is not None
        and trained_binding.sh_degree != semantics.sh_degree
    ):
        raise RealSceneImportError(
            "training result SH degree differs from PLY"
        )

    root = Path(output_root).expanduser().absolute()
    if root.exists() or root.is_symlink():
        raise RealSceneImportError(
            "real-scene import output boundary must be absent"
        )
    try:
        root.mkdir(parents=True)
        if (
            production_closure is not None
            and closure_bytes is not None
        ):
            _write_new(
                root
                / "evidence/production-training-closure.json",
                closure_bytes,
            )
        source_copy = root / "inputs/source.ply"
        _write_new(source_copy, material.source_ply_bytes)
        normalized = root / "inputs/normalized.ply"
        normalized.parent.mkdir(parents=True, exist_ok=True)
        try:
            normalize_quaternions(source_copy, normalized)
        except (OSError, ValueError, SystemExit) as exc:
            raise RealSceneImportError(
                f"source PLY quaternion normalization failed: {exc}"
            ) from exc
        normalized_semantics = inspect_real_scene_ply(normalized)
        if (
            normalized_semantics.gaussian_count
            != semantics.gaussian_count
            or normalized_semantics.sh_degree != semantics.sh_degree
            or normalized_semantics.non_unit_quaternion_count != 0
        ):
            raise RealSceneImportError(
                "normalized PLY changed semantic content"
            )

        source_registration = material.registration
        registration = source_registration
        alignment_rms_m: float | None = None
        alignment_measurement: MetricAlignmentMeasurement | None = None
        alignment_policy: MetricAlignmentPolicy | None = None
        alignment_decision: MetricAlignmentDecision | None = None
        if source_role == "production-acceptance":
            if control_points_path is None or geo_origin is None:
                raise RealSceneImportError(
                    "production import requires measured control points "
                    "and a geo origin"
                )
            try:
                control_points = load_control_points_json(
                    control_points_path
                )
                if (
                    len(control_points) < 4
                    or any(
                        point.derived_from_alignment is not None
                        for point in control_points
                    )
                ):
                    raise RealSceneImportError(
                        "production import requires at least four measured "
                        "control points"
                    )
                alignment_policy = MetricAlignmentPolicy.create(
                    max_rms_m=0.25,
                    max_residual_m=0.25,
                    min_span_ratio=1e-3,
                )
                registration = align_registration(
                    source_registration,
                    control_points,
                    geo_origin=GeoAnchor(
                        lat=float(geo_origin[0]),
                        lon=float(geo_origin[1]),
                        alt=float(geo_origin[2]),
                    ),
                    world_frame_id="project-enu",
                    max_rms_m=alignment_policy.max_rms_m,
                    min_span_ratio=alignment_policy.min_span_ratio,
                )
                alignment_measurement = measure_metric_alignment(
                    source_registration,
                    registration,
                    control_points,
                )
                alignment_decision = decide_metric_alignment(
                    alignment_measurement,
                    alignment_policy,
                    aligned_registration=registration,
                )
                verify_metric_alignment_decision(
                    source_registration=source_registration,
                    control_points=control_points,
                    aligned_registration=registration,
                    measurement=alignment_measurement,
                    policy=alignment_policy,
                    decision=alignment_decision,
                )
                if alignment_decision.status != "accepted":
                    raise RealSceneImportError(
                        "production metric alignment policy rejected output: "
                        + ",".join(alignment_decision.failure_codes)
                    )
                alignment_rms_m = alignment_measurement.rms_residual_m
                _write_new(
                    root / "alignment/source-registration.json",
                    canonical_model_bytes(source_registration),
                )
                _write_new(
                    root / "alignment/control-points.json",
                    canonical_control_points_bytes(control_points),
                )
                _write_new(
                    root / "alignment/observed-registration.json",
                    canonical_model_bytes(registration),
                )
                _write_new(
                    root / "alignment/measurement.json",
                    canonical_metric_alignment_measurement_bytes(
                        alignment_measurement
                    ),
                )
                _write_new(
                    root / "alignment/policy.json",
                    canonical_metric_alignment_policy_bytes(
                        alignment_policy
                    ),
                )
                _write_new(
                    root / "alignment/decision.json",
                    canonical_metric_alignment_decision_bytes(
                        alignment_decision
                    ),
                )
            except RealSceneImportError:
                raise
            except (
                AlignmentError,
                MetricAlignmentEvidenceError,
                OSError,
                ValueError,
            ) as exc:
                raise RealSceneImportError(
                    f"production alignment failed: {exc}"
                ) from exc
        elif (
            registration.pose_to_world is not None
            or registration.alignment_status is not AlignmentStatus.UNALIGNED
            or registration.target_frame.units is not CoordinateUnits.ARBITRARY
            or registration.target_frame.metric_status
            is not MetricStatus.ARBITRARY
        ):
            raise RealSceneImportError(
                "internal canary must remain arbitrary and unaligned"
            )

        result_sha = result_canonical_sha256(material.result)
        evidence_prefix = (
            "training_provenance.v1="
            if material.quality_role == "production"
            else "training_content_closed.v1="
        )
        registration_path, splat_path = prepare_from_registration(
            normalized,
            root / "contracts",
            registration,
            session_id="real-scene-trained",
            extra_evidence=(evidence_prefix + result_sha,),
        )
        prepared_registration = RegistrationResult.model_validate_json(
            registration_path.read_bytes()
        )
        splat_input = SplatInput.model_validate_json(
            splat_path.read_bytes()
        )
        (root / "capture").mkdir()
        manifest = reconstruct(
            photos_dir=root / "capture",
            out_dir=root / "recon",
            web_dir=root / "web",
            engine="import",
            splat_map=[splat_input],
            registration=prepared_registration,
            dedup_voxel=0.0,
            replace_margin=0.0,
            chunk_size_m=float(chunk_size),
        )
        geometry_usability = manifest["provenance"][
            "geometry_usability"
        ]
        expected_usability = (
            "metric-aligned"
            if source_role == "production-acceptance"
            else "preview-only"
        )
        if geometry_usability != expected_usability:
            raise RealSceneImportError(
                "reconstruction did not preserve expected geometry trust"
            )

        recon_report = verify_recon_artifacts(
            root / "web/recon_manifest.json"
        )
        if not _recon_integrity_is_closed(recon_report):
            raise RealSceneImportError(
                "reconstruction artifact integrity is not closed"
            )
        chunks = verify_chunks_integrity(root / "web/chunks")
        if not chunks["valid"]:
            raise RealSceneImportError(
                "chunk payload integrity is not closed"
            )
        scene_count, chunk_count = _verify_coordinate_repack(
            root,
            manifest,
        )
        integrity = RealSceneImportIntegrity(
            recon_artifacts_verified=True,
            chunk_payloads_verified=chunks["verified_payloads"],
            chunk_payloads_total=chunks["total_payloads"],
            coordinate_repack_verified=True,
            scene_gaussian_count=scene_count,
            chunk_gaussian_count=chunk_count,
        )
        _write_new(
            root / "import-integrity.json",
            canonical_model_bytes(integrity),
        )
        receipt = RealSceneImportReceipt(
            source_role=source_role,
            training_quality_role=material.quality_role,
            training_bundle_sha256=material.bundle_sha256,
            training_request_sha256=request_canonical_sha256(
                material.request
            ),
            training_result_sha256=result_sha,
            production_training_closure_path=(
                "evidence/production-training-closure.json"
                if production_closure is not None
                else None
            ),
            production_training_closure_sha256=(
                production_closure.content_sha256
                if production_closure is not None
                else None
            ),
            production_runtime_decision_sha256=(
                production_closure.runtime_decision_sha256
                if production_closure is not None
                else None
            ),
            gaussian_count=semantics.gaussian_count,
            sh_degree=semantics.sh_degree,
            normalized_quaternion_count=(
                semantics.non_unit_quaternion_count
            ),
            target_frame_id=prepared_registration.target_frame.frame_id,
            target_units=prepared_registration.target_frame.units.value,
            geometry_usability=geometry_usability,
            chunk_size=float(chunk_size),
            chunk_units=(
                "metres"
                if source_role == "production-acceptance"
                else "source-units"
            ),
            alignment_rms_m=alignment_rms_m,
            alignment_source_registration_path=(
                "alignment/source-registration.json"
                if alignment_measurement is not None
                else None
            ),
            alignment_control_points_path=(
                "alignment/control-points.json"
                if alignment_measurement is not None
                else None
            ),
            alignment_observed_registration_path=(
                "alignment/observed-registration.json"
                if alignment_measurement is not None
                else None
            ),
            alignment_measurement_path=(
                "alignment/measurement.json"
                if alignment_measurement is not None
                else None
            ),
            alignment_policy_path=(
                "alignment/policy.json"
                if alignment_policy is not None
                else None
            ),
            alignment_decision_path=(
                "alignment/decision.json"
                if alignment_decision is not None
                else None
            ),
            alignment_measurement_sha256=(
                alignment_measurement.content_sha256
                if alignment_measurement is not None
                else None
            ),
            alignment_policy_sha256=(
                alignment_policy.content_sha256
                if alignment_policy is not None
                else None
            ),
            alignment_decision_sha256=(
                alignment_decision.content_sha256
                if alignment_decision is not None
                else None
            ),
            artifacts=_artifact_bindings(root),
        )
        _write_new(
            root / "import-receipt.json",
            canonical_model_bytes(receipt),
        )
        return validate_real_scene_import_receipt(
            root / "import-receipt.json",
            root,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
