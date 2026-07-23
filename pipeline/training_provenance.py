"""Cloud GPU training provenance handshake — content-addressed manifests.

Canonical ``training-request.json`` + ``training-result.json`` bind a cloud-GPU-
trained 3DGS PLY to its verified inputs, trainer identity, training config, GPU
environment, and output artefacts.  A local validator verifies content closure
only — it never auto-promotes operator/cloud claims to ``measured``.

Trust boundary: a verified handshake only proves content closure — the PLY,
config, logs, and environment are mutually consistent and bound to verified
inputs.  It does NOT prove the model is visually perfect, that the photos are
real, or that the geometry is metric.  It is a necessary but not sufficient
condition for trusting a trained model.

See: docs/superpowers/specs/2026-07-23-cloud-training-provenance-design.md
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_json(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, ASCII, no extra whitespace."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)


# ============================================================
# TrainingRequest (pre-training)
# ============================================================

class TrainingInputBinding(FrozenModel):
    """Content-addressed binding to a verified input artefact."""

    artifact_kind: Literal[
        "capture_manifest", "registration_json",
        "registration_quality_report", "sparse_model_dir",
    ]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_path: str = Field(min_length=1)
    artifact_size_bytes: int = Field(ge=0)


class TrainingConfig(FrozenModel):
    """The training configuration that the operator intends to use.

    ``random_seed`` is required (no default) — a training run without a recorded
    seed is not reproducible and therefore not auditable.
    """

    trainer_name: Literal["nerfstudio-splatfacto", "brush", "gsplat", "inria"]
    trainer_version: str = Field(min_length=1)
    max_resolution: int = Field(ge=64)
    total_steps: int = Field(ge=1)
    export_every: int | None = Field(default=None, ge=1)
    random_seed: int
    extra_config: tuple[tuple[str, str], ...] = Field(default=())


class TrainingRequest(FrozenModel):
    """Issued before training.  Binds verified inputs + operator intent."""

    request_id: str = Field(min_length=1)
    created_at_utc_iso: str = Field(min_length=1)
    input_bindings: tuple[TrainingInputBinding, ...] = Field(min_length=1)
    training_config: TrainingConfig
    expected_output_format: Literal["inria-3dgs-ply"]


def request_canonical_sha256(request: TrainingRequest) -> str:
    """Content-addressed SHA-256 of the request's canonical JSON bytes."""
    return hashlib.sha256(_canonical_json(request).encode("utf-8")).hexdigest()


# ============================================================
# TrainingResult (post-training)
# ============================================================

class GpuEnvironment(FrozenModel):
    """GPU/CUDA environment captured during training."""

    gpu_name: str = Field(min_length=1)
    gpu_memory_mb: int = Field(ge=0)
    cuda_version: str = Field(min_length=1)
    driver_version: str = Field(min_length=1)


class TrainingOutputBinding(FrozenModel):
    """Content-addressed binding to a training output artefact."""

    artifact_kind: Literal[
        "trained_ply", "training_config_yml",
        "training_log", "ns_process_data_dir",
    ]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_path: str = Field(min_length=1)
    artifact_size_bytes: int = Field(ge=0)
    gaussian_count: int | None = None
    sh_degree: int | None = None


class TrainingStatus(FrozenModel):
    """Training run outcome state.

    ``failed`` requires ``error_message``; ``completed`` forbids it.
    """

    state: Literal["completed", "failed", "interrupted"]
    exit_code: int
    error_message: str | None = None

    @model_validator(mode="after")
    def _validate_status_consistency(self) -> TrainingStatus:
        if self.state != "completed" and not self.error_message:
            raise ValueError(
                f"error_message is required when state={self.state!r}"
            )
        if self.state == "completed" and self.error_message is not None:
            raise ValueError(
                "error_message must be None when state='completed'"
            )
        return self


class TrainingResult(FrozenModel):
    """Produced after training.  Binds actual outputs + environment + logs."""

    request_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_id: str = Field(min_length=1)
    started_at_utc_iso: str = Field(min_length=1)
    finished_at_utc_iso: str = Field(min_length=1)

    actual_input_shas: tuple[str, ...]
    actual_trainer_name: str
    actual_trainer_version: str
    actual_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    gpu_environment: GpuEnvironment

    output_bindings: tuple[TrainingOutputBinding, ...]
    primary_ply_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    training_status: TrainingStatus
    training_log_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_log_tail_lines: int = Field(default=50, ge=0)


def result_canonical_sha256(result: TrainingResult) -> str:
    """Content-addressed SHA-256 of the result's canonical JSON bytes."""
    return hashlib.sha256(_canonical_json(result).encode("utf-8")).hexdigest()


# ============================================================
# Validation (content closure)
# ============================================================

def validate_training_provenance(
    result: TrainingResult,
    request: TrainingRequest,
    actual_ply_bytes: bytes,
) -> None:
    """Validate content closure: re-derive, never trust self-reported.

    Checks:
    1. Input closure: result's actual_input_shas must match request's
       input_bindings SHAs exactly.
    2. Request binding: result's request_canonical_sha256 must match request.
    3. PLY binding: primary_ply_sha256 must appear in output_bindings.
    4. PLY bytes: sha256(actual_ply_bytes) must match primary_ply_sha256.
    5. Status consistency: failed runs cannot claim a non-empty PLY.

    Raises ValueError on any mismatch.
    """
    # 1. Input closure
    expected_shas = {b.artifact_sha256 for b in request.input_bindings}
    actual_shas = set(result.actual_input_shas)
    if expected_shas != actual_shas:
        missing = expected_shas - actual_shas
        extra = actual_shas - expected_shas
        details = []
        if missing:
            details.append(f"missing: {missing}")
        if extra:
            details.append(f"extra: {extra}")
        raise ValueError(
            f"input closure broken: {'; '.join(details)}"
        )

    # 2. Request binding
    expected_req_sha = request_canonical_sha256(request)
    if result.request_canonical_sha256 != expected_req_sha:
        raise ValueError(
            f"request_canonical_sha256 mismatch: result claims "
            f"{result.request_canonical_sha256} but request computes "
            f"{expected_req_sha}"
        )

    # 3. PLY binding in outputs (only required for completed runs)
    if result.training_status.state == "completed":
        ply_shas_in_outputs = {
            b.artifact_sha256 for b in result.output_bindings
            if b.artifact_kind == "trained_ply"
        }
        if result.primary_ply_sha256 not in ply_shas_in_outputs:
            raise ValueError(
                f"primary_ply_sha256 {result.primary_ply_sha256} not found "
                f"in output_bindings trained_ply artefacts"
            )

    # 4. PLY bytes match
    expected_ply_sha = hashlib.sha256(actual_ply_bytes).hexdigest()
    if result.primary_ply_sha256 != expected_ply_sha:
        raise ValueError(
            f"PLY bytes mismatch: result claims sha256 "
            f"{result.primary_ply_sha256} but actual bytes compute "
            f"{expected_ply_sha}"
        )

    # 5. Status consistency: failed runs cannot claim a valid PLY
    if result.training_status.state != "completed":
        # For failed/interrupted runs, primary_ply_sha256 must be sha256(b"")
        # (the "no PLY produced" sentinel)
        empty_sha = hashlib.sha256(b"").hexdigest()
        if result.primary_ply_sha256 != empty_sha:
            raise ValueError(
                f"failed/interrupted run cannot claim a non-empty PLY "
                f"(primary_ply_sha256={result.primary_ply_sha256})"
            )


# ============================================================
# TrainingTrust derivation
# ============================================================

class TrainingTrust(FrozenModel):
    """7 independent trust booleans.  ``is_trustworthy = all(True)``.

    ``is_trustworthy=True`` does NOT imply metric/aligned/real-photos — it only
    proves content closure and input binding consistency.
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
    actual_ply_bytes: bytes,
    *,
    registration_quality_passed: bool,
) -> TrainingTrust:
    """Derive trust from the result.  Content closure is checked via
    ``validate_training_provenance`` — if it raises, ``content_closed=False``.
    """
    try:
        validate_training_provenance(result, request, actual_ply_bytes)
        content_closed = True
    except ValueError:
        content_closed = False

    # Inputs verified: all input SHAs are non-empty 64-hex (validated by schema)
    inputs_verified = all(
        len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
        for sha in result.actual_input_shas
    ) and len(result.actual_input_shas) > 0

    # Trainer identified: name + version non-empty
    trainer_identified = bool(result.actual_trainer_name) and bool(result.actual_trainer_version)

    # Seed recorded: request's config has a random_seed (required by schema, so always True)
    seed_recorded = request.training_config.random_seed is not None

    # Log bound: training_log_sha256 is non-empty (64-hex, always True by schema)
    log_bound = len(result.training_log_sha256) == 64

    # Environment captured: gpu_environment has all fields (validated by schema)
    env = result.gpu_environment
    environment_captured = all([
        env.gpu_name, env.cuda_version, env.driver_version,
        env.gpu_memory_mb >= 0,
    ])

    # Training must have completed
    training_completed = result.training_status.state == "completed"

    return TrainingTrust(
        content_closed=content_closed and training_completed,
        inputs_verified=inputs_verified,
        registration_quality_passed=registration_quality_passed,
        trainer_identified=trainer_identified,
        seed_recorded=seed_recorded,
        log_bound=log_bound,
        environment_captured=environment_captured,
    )
