"""Cloud GPU training provenance handshake — content-addressed, fail-closed.

The builder/validator binds a cloud-GPU-trained 3DGS PLY to its verified inputs,
trainer identity, training config, GPU environment, and output artefacts by
re-deriving every claim from authoritative bytes.  It never promotes self-
reported or schema-present fields to verified facts: a SHA string that "looks
like" SHA-256 is not treated as evidence; only re-computed hashes of supplied
bytes count.

Trust boundary: a verified handshake only proves content closure — the PLY,
config, logs, and environment are mutually consistent and bound to verified
inputs.  It does NOT prove the model is visually perfect, that the photos are
real, or that the geometry is metric.  ``is_trustworthy=True`` is a necessary
but not sufficient condition.

See: handoff/REVIEW-CODEX-022-glm-registration-training-trust-contracts.md
See: docs/superpowers/specs/2026-07-23-cloud-training-provenance-design.md
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _canonical_json(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, ASCII, no extra whitespace."""
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, ensure_ascii=True
    )


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


# ============================================================
# Input bindings (request + result must match exactly, ordered)
# ============================================================

_INPUT_KINDS = Literal[
    "capture_manifest",
    "registration_json",
    "registration_quality_report",
    "sparse_model_dir",
]


class TrainingInputBinding(FrozenModel):
    """Content-addressed binding to a verified input artefact.

    Inputs are immutable: the result's ``actual_input_bindings`` must equal the
    request's ``input_bindings`` exactly (ordered, kinded, sha, path, size).
    Input drift is never silently tolerated.
    """

    artifact_kind: _INPUT_KINDS
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_path: str = Field(min_length=1)
    artifact_size_bytes: int = Field(ge=1)


# ============================================================
# TrainingRequest (pre-training intent)
# ============================================================

class TrainingConfig(FrozenModel):
    """The training configuration the operator intends to use.

    ``random_seed`` is required — a training run without a recorded seed is not
    reproducible and therefore not auditable.
    """

    trainer_name: Literal["nerfstudio-splatfacto", "brush", "gsplat", "inria"]
    trainer_version: str = Field(min_length=1)
    max_resolution: int = Field(ge=64)
    total_steps: int = Field(ge=1)
    export_every: int | None = Field(default=None, ge=1)
    random_seed: int
    extra_config: tuple[tuple[str, str], ...] = Field(default=())


class TrainingRequest(FrozenModel):
    """Issued before training.  Binds verified inputs + operator intent.

    ``requested_config_sha256`` is the SHA-256 of the actual config file the
    operator will feed to the trainer.  The result must bind the same SHA unless
    a policy explicitly allows config drift.
    """

    request_id: str = Field(min_length=1)
    created_at_utc: datetime
    input_bindings: tuple[TrainingInputBinding, ...] = Field(min_length=1)
    training_config: TrainingConfig
    expected_output_format: Literal["inria-3dgs-ply"]
    requested_config_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def request_canonical_sha256(request: TrainingRequest) -> str:
    """Content-addressed SHA-256 of the request's canonical JSON bytes."""
    return hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()


# ============================================================
# TrainingResult (post-training outcome)
# ============================================================

_OUTPUT_KINDS = Literal[
    "trained_ply",
    "training_config_yml",
    "training_log",
    "ns_process_data_dir",
]


class GpuEnvironment(FrozenModel):
    """GPU/CUDA environment captured during training (self-reported, bound)."""

    gpu_name: str = Field(min_length=1)
    gpu_memory_mb: int = Field(ge=0)
    cuda_version: str = Field(min_length=1)
    driver_version: str = Field(min_length=1)


class TrainingOutputBinding(FrozenModel):
    """Content-addressed binding to a training output artefact."""

    artifact_kind: _OUTPUT_KINDS
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_path: str = Field(min_length=1)
    artifact_size_bytes: int = Field(ge=0)
    gaussian_count: int | None = None
    sh_degree: int | None = None


class TrainerDriftRecord(FrozenModel):
    """Structured record when actual trainer differs from request.

    Presence does NOT auto-approve drift — the policy decides.  When no drift
    occurred this record must be ``None``.
    """

    requested_trainer_name: str = Field(min_length=1)
    requested_trainer_version: str = Field(min_length=1)
    actual_trainer_name: str = Field(min_length=1)
    actual_trainer_version: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TrainingStatus(FrozenModel):
    """Training run outcome state.

    ``completed`` requires ``exit_code==0`` and forbids ``error_message``;
    ``failed``/``interrupted`` require ``error_message``.
    """

    state: Literal["completed", "failed", "interrupted"]
    exit_code: int
    error_message: str | None = None

    @model_validator(mode="after")
    def _consistency(self) -> TrainingStatus:
        if self.state != "completed" and not self.error_message:
            raise ValueError(
                f"error_message is required when state={self.state!r}"
            )
        if self.state == "completed" and self.error_message is not None:
            raise ValueError("error_message must be None when state='completed'")
        return self


class TrainingResult(FrozenModel):
    """Produced after training.  Binds actual outputs + environment + logs.

    Every SHA/size field is re-derived from authoritative bytes by the
    validator — self-reported values are never trusted.
    """

    request_canonical_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_id: str = Field(min_length=1)
    started_at_utc: datetime
    finished_at_utc: datetime

    actual_input_bindings: tuple[TrainingInputBinding, ...] = Field(min_length=1)
    actual_trainer_name: str
    actual_trainer_version: str
    actual_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    actual_config_size_bytes: int = Field(ge=1)

    gpu_environment: GpuEnvironment

    output_bindings: tuple[TrainingOutputBinding, ...]
    primary_ply_sha256: str = Field(pattern=_SHA256_PATTERN)
    primary_ply_size_bytes: int = Field(ge=0)

    training_status: TrainingStatus
    training_log_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_log_size_bytes: int = Field(ge=0)

    trainer_drift: TrainerDriftRecord | None = None

    @field_validator("started_at_utc", "finished_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def _started_before_finished(self) -> TrainingResult:
        if self.started_at_utc > self.finished_at_utc:
            raise ValueError("started_at_utc must be <= finished_at_utc")
        return self


def result_canonical_sha256(result: TrainingResult) -> str:
    """Content-addressed SHA-256 of the result's canonical JSON bytes."""
    return hashlib.sha256(_canonical_json(result).encode("utf-8")).hexdigest()


# ============================================================
# Drift policy
# ============================================================

class TrainingDriftPolicy(FrozenModel):
    """Policy for tolerated trainer/config drift.  Defaults deny all drift."""

    allow_trainer_drift: bool = False
    allow_config_drift: bool = False


# ============================================================
# Validation (content closure) — re-derive, never trust self-reported
# ============================================================

def _check_request_binding(result: TrainingResult, request: TrainingRequest) -> None:
    expected = request_canonical_sha256(request)
    if result.request_canonical_sha256 != expected:
        raise ValueError(
            f"request_canonical_sha256 mismatch: result claims "
            f"{result.request_canonical_sha256} but request computes {expected}"
        )


def _check_input_closure(
    result: TrainingResult,
    request: TrainingRequest,
    input_bytes_by_path: dict[str, bytes],
) -> None:
    # Ordered/kinded/sha/path/size exact equality — no input drift.
    if result.actual_input_bindings != request.input_bindings:
        raise ValueError(
            "actual_input_bindings must exactly equal request.input_bindings "
            "(ordered, kinded, sha, path, size)"
        )
    paths = [b.artifact_path for b in result.actual_input_bindings]
    if len(set(paths)) != len(paths):
        raise ValueError("input binding artifact paths must be unique")
    # Verify actual input bytes against each binding's declared sha+size.
    for binding in result.actual_input_bindings:
        actual = input_bytes_by_path.get(binding.artifact_path)
        if actual is None:
            raise ValueError(
                f"missing actual input bytes for {binding.artifact_path!r}"
            )
        actual_sha = hashlib.sha256(actual).hexdigest()
        if actual_sha != binding.artifact_sha256:
            raise ValueError(
                f"input {binding.artifact_path!r} sha mismatch: binding "
                f"{binding.artifact_sha256} but bytes compute {actual_sha}"
            )
        if len(actual) != binding.artifact_size_bytes:
            raise ValueError(
                f"input {binding.artifact_path!r} size mismatch: binding "
                f"{binding.artifact_size_bytes} but bytes are {len(actual)}"
            )


def _check_trainer(
    result: TrainingResult,
    request: TrainingRequest,
    policy: TrainingDriftPolicy,
) -> bool:
    """Return True iff actual trainer matches request (no drift)."""
    req_name = request.training_config.trainer_name
    req_version = request.training_config.trainer_version
    matched = (
        result.actual_trainer_name == req_name
        and result.actual_trainer_version == req_version
    )
    if matched:
        if result.trainer_drift is not None:
            raise ValueError(
                "trainer_drift record present but no actual drift occurred"
            )
        return True
    # Drift detected.
    if not policy.allow_trainer_drift:
        raise ValueError(
            f"trainer drift not allowed: requested {req_name}/{req_version} "
            f"but got {result.actual_trainer_name}/{result.actual_trainer_version}"
        )
    drift = result.trainer_drift
    if drift is None:
        raise ValueError(
            "trainer drift allowed by policy but no trainer_drift record present"
        )
    if (
        drift.requested_trainer_name != req_name
        or drift.requested_trainer_version != req_version
        or drift.actual_trainer_name != result.actual_trainer_name
        or drift.actual_trainer_version != result.actual_trainer_version
    ):
        raise ValueError(
            "trainer_drift record inconsistent with request/result"
        )
    return False


def _check_config(
    result: TrainingResult,
    request: TrainingRequest,
    actual_config_bytes: bytes,
    policy: TrainingDriftPolicy,
) -> None:
    actual_sha = hashlib.sha256(actual_config_bytes).hexdigest()
    if actual_sha != result.actual_config_sha256:
        raise ValueError(
            f"actual_config_sha256 mismatch: result claims "
            f"{result.actual_config_sha256} but bytes compute {actual_sha}"
        )
    if len(actual_config_bytes) != result.actual_config_size_bytes:
        raise ValueError(
            f"actual_config_size_bytes mismatch: result claims "
            f"{result.actual_config_size_bytes} but bytes are "
            f"{len(actual_config_bytes)}"
        )
    if actual_sha != request.requested_config_sha256:
        if not policy.allow_config_drift:
            raise ValueError(
                f"config drift not allowed: requested "
                f"{request.requested_config_sha256} but actual {actual_sha}"
            )


def _check_status_and_ply(
    result: TrainingResult,
    actual_ply_bytes: bytes,
) -> None:
    state = result.training_status.state
    exit_code = result.training_status.exit_code
    ply_bindings = [
        b for b in result.output_bindings if b.artifact_kind == "trained_ply"
    ]
    expected_ply_sha = hashlib.sha256(actual_ply_bytes).hexdigest()

    if state == "completed":
        if exit_code != 0:
            raise ValueError(
                f"completed state requires exit_code==0 but got {exit_code}"
            )
        non_empty = [b for b in ply_bindings if b.artifact_size_bytes > 0]
        if len(non_empty) != 1:
            raise ValueError(
                f"completed requires exactly one non-empty trained_ply binding; "
                f"got {len(non_empty)}"
            )
        if len(actual_ply_bytes) == 0:
            raise ValueError("completed run cannot have empty PLY bytes")
        ply = non_empty[0]
        if ply.artifact_sha256 != result.primary_ply_sha256:
            raise ValueError(
                "trained_ply binding sha != primary_ply_sha256"
            )
        if ply.artifact_size_bytes != result.primary_ply_size_bytes:
            raise ValueError(
                "trained_ply binding size != primary_ply_size_bytes"
            )
        if result.primary_ply_sha256 != expected_ply_sha:
            raise ValueError(
                f"primary_ply_sha256 mismatch: result claims "
                f"{result.primary_ply_sha256} but bytes compute "
                f"{expected_ply_sha}"
            )
        if result.primary_ply_size_bytes != len(actual_ply_bytes):
            raise ValueError(
                f"primary_ply_size_bytes mismatch: result claims "
                f"{result.primary_ply_size_bytes} but bytes are "
                f"{len(actual_ply_bytes)}"
            )
    else:
        # failed / interrupted
        if exit_code == 0:
            raise ValueError(
                f"{state!r} state requires non-zero exit_code but got 0"
            )
        if ply_bindings:
            raise ValueError(
                f"{state!r} run cannot declare trained_ply output bindings"
            )
        empty_sha = hashlib.sha256(b"").hexdigest()
        if result.primary_ply_sha256 != empty_sha:
            raise ValueError(
                f"{state!r} run primary_ply_sha256 must be the empty-bytes sha"
            )
        if result.primary_ply_size_bytes != 0:
            raise ValueError(
                f"{state!r} run primary_ply_size_bytes must be 0"
            )
        if len(actual_ply_bytes) != 0:
            raise ValueError(
                f"{state!r} run actual PLY bytes must be empty"
            )


def _check_log(
    result: TrainingResult,
    actual_log_bytes: bytes,
) -> None:
    actual_sha = hashlib.sha256(actual_log_bytes).hexdigest()
    if result.training_log_sha256 != actual_sha:
        raise ValueError(
            f"training_log_sha256 mismatch: result claims "
            f"{result.training_log_sha256} but bytes compute {actual_sha}"
        )
    if result.training_log_size_bytes != len(actual_log_bytes):
        raise ValueError(
            f"training_log_size_bytes mismatch: result claims "
            f"{result.training_log_size_bytes} but bytes are "
            f"{len(actual_log_bytes)}"
        )
    log_bindings = [
        b for b in result.output_bindings if b.artifact_kind == "training_log"
    ]
    if len(log_bindings) != 1:
        raise ValueError(
            f"exactly one training_log output binding required; got "
            f"{len(log_bindings)}"
        )
    log = log_bindings[0]
    if log.artifact_sha256 != actual_sha:
        raise ValueError("training_log binding sha != actual log sha")
    if log.artifact_size_bytes != len(actual_log_bytes):
        raise ValueError("training_log binding size != actual log size")


def _check_config_output(
    result: TrainingResult,
    actual_config_bytes: bytes,
) -> None:
    cfg_bindings = [
        b for b in result.output_bindings
        if b.artifact_kind == "training_config_yml"
    ]
    if len(cfg_bindings) != 1:
        raise ValueError(
            f"exactly one training_config_yml output binding required; got "
            f"{len(cfg_bindings)}"
        )
    cfg = cfg_bindings[0]
    actual_sha = hashlib.sha256(actual_config_bytes).hexdigest()
    if cfg.artifact_sha256 != actual_sha:
        raise ValueError(
            "training_config_yml binding sha != actual config sha"
        )
    if cfg.artifact_size_bytes != len(actual_config_bytes):
        raise ValueError(
            "training_config_yml binding size != actual config size"
        )


def validate_training_provenance(
    result: TrainingResult,
    request: TrainingRequest,
    *,
    actual_ply_bytes: bytes,
    actual_config_bytes: bytes,
    actual_log_bytes: bytes,
    input_bytes_by_path: dict[str, bytes],
    policy: TrainingDriftPolicy | None = None,
) -> None:
    """Validate content closure: re-derive every claim from authoritative bytes.

    Checks (all fail-closed on mismatch):
    1. ``request_canonical_sha256`` matches the supplied request.
    2. Input closure: result's ``actual_input_bindings`` exactly equal the
       request's ``input_bindings`` (ordered, kinded, sha, path, size), paths
       are unique, and each binding's SHA/size are re-derived from supplied
       ``input_bytes_by_path`` bytes.
    3. Trainer name/version match the request's config, unless an explicit
       ``trainer_drift`` record is present and the policy allows it.
    4. Config binding: ``actual_config_sha256``/``actual_config_size_bytes``
       match ``sha256(actual_config_bytes)``/``len``; drift vs
       ``requested_config_sha256`` requires policy allowance.
    5. Status/PLY consistency: ``completed`` iff ``exit_code==0`` and exactly
       one non-empty ``trained_ply`` output binding matches the primary PLY
       sha+size, which in turn match ``sha256(actual_ply_bytes)``/``len``.
       ``failed``/``interrupted`` require non-zero exit, no trained_ply output,
       and empty primary PLY + empty actual bytes.
    6. Log binding: exactly one ``training_log`` output binding whose sha+size
       match ``sha256(actual_log_bytes)``/``len`` and the result's
       ``training_log_sha256``/``training_log_size_bytes``.
    7. Config output binding: exactly one ``training_config_yml`` output binding
       whose sha+size match the actual config bytes.

    Raises ``ValueError`` on any mismatch.  Timestamps (UTC, started<=finished)
    are enforced at schema level.
    """
    policy = policy or TrainingDriftPolicy()
    _check_request_binding(result, request)
    _check_input_closure(result, request, input_bytes_by_path)
    _check_trainer(result, request, policy)
    _check_config(result, request, actual_config_bytes, policy)
    _check_status_and_ply(result, actual_ply_bytes)
    _check_log(result, actual_log_bytes)
    _check_config_output(result, actual_config_bytes)


# ============================================================
# Builder — construct a TrainingResult from authoritative bytes
# ============================================================

def build_training_result(
    *,
    request: TrainingRequest,
    result_id: str,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    actual_trainer_name: str,
    actual_trainer_version: str,
    actual_config_bytes: bytes,
    actual_ply_bytes: bytes,
    actual_log_bytes: bytes,
    input_bytes_by_path: dict[str, bytes],
    gpu_environment: GpuEnvironment,
    exit_code: int,
    actual_ply_path: str = "export/point_cloud.ply",
    actual_config_path: str = "config.yml",
    actual_log_path: str = "train.log",
    error_message: str | None = None,
    gaussian_count: int | None = None,
    sh_degree: int | None = None,
    ns_process_data_dir_binding: TrainingOutputBinding | None = None,
    trainer_drift: TrainerDriftRecord | None = None,
) -> TrainingResult:
    """Construct a TrainingResult by deriving every field from real bytes.

    The training state is derived from ``exit_code`` and whether a non-empty
    PLY was produced:

    - ``exit_code == 0`` and non-empty PLY -> ``completed``.
    - ``exit_code == 0`` but no PLY -> ``interrupted``.
    - ``exit_code != 0`` -> ``failed``.

    An ``error_message`` is required for non-completed states; if omitted a
    default is derived from the exit code.

    ``actual_*_path`` arguments record the source paths of the bound artefacts
    so downstream consumers (e.g. ``prepare_import``) can re-read the same
    bytes for closure verification.  They default to relative names but the
    emitter should pass the real on-disk paths.
    """
    if actual_ply_bytes and exit_code == 0:
        state: Literal["completed", "failed", "interrupted"] = "completed"
        msg: str | None = None
    elif exit_code == 0:
        state = "interrupted"
        msg = error_message or "exit code 0 but no PLY produced"
    else:
        state = "failed"
        msg = error_message or f"trainer exited with code {exit_code}"

    config_sha = hashlib.sha256(actual_config_bytes).hexdigest()
    ply_sha = hashlib.sha256(actual_ply_bytes).hexdigest()
    log_sha = hashlib.sha256(actual_log_bytes).hexdigest()

    outputs: list[TrainingOutputBinding] = []
    if state == "completed":
        outputs.append(
            TrainingOutputBinding(
                artifact_kind="trained_ply",
                artifact_sha256=ply_sha,
                artifact_path=actual_ply_path,
                artifact_size_bytes=len(actual_ply_bytes),
                gaussian_count=gaussian_count,
                sh_degree=sh_degree,
            )
        )
    outputs.append(
        TrainingOutputBinding(
            artifact_kind="training_config_yml",
            artifact_sha256=config_sha,
            artifact_path=actual_config_path,
            artifact_size_bytes=len(actual_config_bytes),
        )
    )
    outputs.append(
        TrainingOutputBinding(
            artifact_kind="training_log",
            artifact_sha256=log_sha,
            artifact_path=actual_log_path,
            artifact_size_bytes=len(actual_log_bytes),
        )
    )
    if ns_process_data_dir_binding is not None:
        outputs.append(ns_process_data_dir_binding)

    primary_ply_sha = ply_sha if state == "completed" else hashlib.sha256(b"").hexdigest()
    primary_ply_size = len(actual_ply_bytes) if state == "completed" else 0

    return TrainingResult(
        request_canonical_sha256=request_canonical_sha256(request),
        result_id=result_id,
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
        actual_input_bindings=request.input_bindings,
        actual_trainer_name=actual_trainer_name,
        actual_trainer_version=actual_trainer_version,
        actual_config_sha256=config_sha,
        actual_config_size_bytes=len(actual_config_bytes),
        gpu_environment=gpu_environment,
        output_bindings=tuple(outputs),
        primary_ply_sha256=primary_ply_sha,
        primary_ply_size_bytes=primary_ply_size,
        training_status=TrainingStatus(
            state=state,
            exit_code=exit_code,
            error_message=msg,
        ),
        training_log_sha256=log_sha,
        training_log_size_bytes=len(actual_log_bytes),
        trainer_drift=trainer_drift,
    )


# ============================================================
# TrainingTrust derivation — booleans from completed verifications
# ============================================================

class TrainingTrust(FrozenModel):
    """7 independent trust booleans.  ``is_trustworthy = all(True)``.

    Each boolean is derived from a completed verification, never from schema
    field presence.  ``is_trustworthy=True`` does NOT imply metric/aligned/
    real-photos — it only proves content closure and binding consistency.
    """

    content_closed: bool
    inputs_verified: bool
    registration_quality_passed: bool
    trainer_identified: bool
    seed_recorded: bool
    log_bound: bool
    environment_captured: bool

    @property
    def is_trustworthy(self) -> bool:
        return (
            self.content_closed
            and self.inputs_verified
            and self.registration_quality_passed
            and self.trainer_identified
            and self.seed_recorded
            and self.log_bound
            and self.environment_captured
        )


def derive_training_trust(
    result: TrainingResult,
    request: TrainingRequest,
    *,
    actual_ply_bytes: bytes,
    actual_config_bytes: bytes,
    actual_log_bytes: bytes,
    input_bytes_by_path: dict[str, bytes],
    registration_quality_passed: bool,
    policy: TrainingDriftPolicy | None = None,
) -> TrainingTrust:
    """Derive trust booleans from completed verifications.

    ``content_closed`` is True iff ``validate_training_provenance`` passes AND
    the training state is ``completed``.  Every other boolean (except
    ``registration_quality_passed``, which is supplied externally, and
    ``trainer_identified``, which separately reflects trainer equality) is
    derived from the same atomic verification — a SHA-looking string or a
    non-empty field is never treated as evidence on its own.

    Honest boundary: ``environment_captured`` reflects that the GPU environment
    is self-reported by the cloud runner and bound to a verified result; it is
    not independently attested by local bytes.
    """
    policy = policy or TrainingDriftPolicy()
    try:
        validate_training_provenance(
            result,
            request,
            actual_ply_bytes=actual_ply_bytes,
            actual_config_bytes=actual_config_bytes,
            actual_log_bytes=actual_log_bytes,
            input_bytes_by_path=input_bytes_by_path,
            policy=policy,
        )
        content_closed = result.training_status.state == "completed"
    except ValueError:
        content_closed = False

    # trainer_identified: actual trainer matches request (independent of
    # whether drift was policy-allowed).  When drift occurred (even if allowed),
    # the trainer is not the requested one -> not identified.
    trainer_match = (
        result.actual_trainer_name == request.training_config.trainer_name
        and result.actual_trainer_version == request.training_config.trainer_version
    )
    trainer_identified = content_closed and trainer_match

    return TrainingTrust(
        content_closed=content_closed,
        inputs_verified=content_closed,
        registration_quality_passed=registration_quality_passed,
        trainer_identified=trainer_identified,
        seed_recorded=content_closed,
        log_bound=content_closed,
        environment_captured=content_closed,
    )
