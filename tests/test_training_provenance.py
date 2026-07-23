"""Cloud GPU training provenance handshake — fail-closed TDD tests.

Every REVIEW-CODEX-022 P0.2 adversarial path is covered: trainer/config drift,
exit-code/state inconsistency, fake PLY/config/log sizes & SHAs, input drift,
and timestamp ordering.  Trust booleans are derived from completed verifications
only — a SHA-looking string is never evidence on its own.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pipeline.training_provenance import (
    GpuEnvironment,
    TrainerDriftRecord,
    TrainingConfig,
    TrainingDriftPolicy,
    TrainingInputBinding,
    TrainingOutputBinding,
    TrainingRequest,
    TrainingResult,
    TrainingStatus,
    TrainingTrust,
    build_training_result,
    derive_training_trust,
    request_canonical_sha256,
    result_canonical_sha256,
    validate_training_provenance,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_PLY_BYTES = b"fake ply bytes for testing"
_PLY_SHA = hashlib.sha256(_PLY_BYTES).hexdigest()
_CONFIG_BYTES = b"trainer: nerfstudio\nmax_resolution: 1024\nseed: 42\n"
_CONFIG_SHA = hashlib.sha256(_CONFIG_BYTES).hexdigest()
_LOG_BYTES = b"[10:00] training started\n[11:30] done\n"
_LOG_SHA = hashlib.sha256(_LOG_BYTES).hexdigest()
_T0 = datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 7, 23, 11, 30, 0, tzinfo=UTC)


# ============================================================
# Helpers
# ============================================================

def _make_config(
    *,
    trainer_name: str = "nerfstudio-splatfacto",
    trainer_version: str = "nerfstudio 0.3.4",
    random_seed: int = 42,
) -> TrainingConfig:
    return TrainingConfig(
        trainer_name=trainer_name,  # type: ignore[arg-type]
        trainer_version=trainer_version,
        max_resolution=1024,
        total_steps=30000,
        random_seed=random_seed,
    )


def _make_input_binding(
    *,
    path: str = "capture/manifest.json",
    sha: str = _SHA_A,
    size: int = 1000,
    kind: str = "capture_manifest",
) -> TrainingInputBinding:
    return TrainingInputBinding(
        artifact_kind=kind,  # type: ignore[arg-type]
        artifact_sha256=sha,
        artifact_path=path,
        artifact_size_bytes=size,
    )


def _make_request(
    *,
    config: TrainingConfig | None = None,
    input_bindings: tuple[TrainingInputBinding, ...] | None = None,
    requested_config_sha256: str | None = None,
) -> TrainingRequest:
    if config is None:
        config = _make_config()
    if input_bindings is None:
        input_bindings = (_make_input_binding(),)
    if requested_config_sha256 is None:
        requested_config_sha256 = _CONFIG_SHA
    return TrainingRequest(
        request_id="req-001",
        created_at_utc=_T0,
        input_bindings=input_bindings,
        training_config=config,
        expected_output_format="inria-3dgs-ply",  # type: ignore[arg-type]
        requested_config_sha256=requested_config_sha256,
    )


def _make_honest(
    *,
    request: TrainingRequest | None = None,
    ply_bytes: bytes = _PLY_BYTES,
    config_bytes: bytes = _CONFIG_BYTES,
    log_bytes: bytes = _LOG_BYTES,
    exit_code: int = 0,
    trainer_name: str | None = None,
    trainer_version: str | None = None,
    trainer_drift: TrainerDriftRecord | None = None,
    gaussian_count: int | None = 50000,
    sh_degree: int | None = 3,
) -> tuple[TrainingResult, TrainingRequest, dict[str, bytes], bytes, bytes, bytes]:
    """Build an honest result via the builder and return all artifacts.

    The input bindings' SHAs are re-derived from real bytes so the validator
    accepts them.  Returns (result, request, input_bytes, ply, config, log).
    """
    if request is None:
        request = _make_request(requested_config_sha256=_CONFIG_SHA)
    # Re-derive input binding SHAs/sizes from real bytes so closure holds.
    real_input_bytes: dict[str, bytes] = {}
    patched_bindings: list[TrainingInputBinding] = []
    for binding in request.input_bindings:
        data = b"input-" + binding.artifact_path.encode("utf-8")
        data = (data * (binding.artifact_size_bytes // len(data) + 1))[
            : binding.artifact_size_bytes
        ]
        real_input_bytes[binding.artifact_path] = data
        patched_bindings.append(
            binding.model_copy(
                update={
                    "artifact_sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        )
    request = request.model_copy(update={"input_bindings": tuple(patched_bindings)})

    tn = trainer_name or request.training_config.trainer_name
    tv = trainer_version or request.training_config.trainer_version
    result = build_training_result(
        request=request,
        result_id="res-001",
        started_at_utc=_T0,
        finished_at_utc=_T1,
        actual_trainer_name=tn,
        actual_trainer_version=tv,
        actual_config_bytes=config_bytes,
        actual_ply_bytes=ply_bytes,
        actual_log_bytes=log_bytes,
        input_bytes_by_path=real_input_bytes,
        gpu_environment=GpuEnvironment(
            gpu_name="NVIDIA GeForce RTX 3060",
            gpu_memory_mb=12288,
            cuda_version="11.8",
            driver_version="535.104.05",
        ),
        exit_code=exit_code,
        gaussian_count=gaussian_count,
        sh_degree=sh_degree,
        trainer_drift=trainer_drift,
    )
    # For failed/interrupted runs the builder empties the primary PLY, so the
    # validator must receive empty actual PLY bytes too.
    validation_ply = ply_bytes if (exit_code == 0 and ply_bytes) else b""
    return result, request, real_input_bytes, validation_ply, config_bytes, log_bytes


def _validate(
    pack: tuple[TrainingResult, TrainingRequest, dict[str, bytes], bytes, bytes, bytes],
    *,
    policy: TrainingDriftPolicy | None = None,
) -> None:
    result, request, input_bytes, ply, config, log = pack
    validate_training_provenance(
        result,
        request,
        actual_ply_bytes=ply,
        actual_config_bytes=config,
        actual_log_bytes=log,
        input_bytes_by_path=input_bytes,
        policy=policy,
    )


def _trust(
    pack: tuple[TrainingResult, TrainingRequest, dict[str, bytes], bytes, bytes, bytes],
    *,
    registration_quality_passed: bool = True,
    policy: TrainingDriftPolicy | None = None,
) -> TrainingTrust:
    result, request, input_bytes, ply, config, log = pack
    return derive_training_trust(
        result,
        request,
        actual_ply_bytes=ply,
        actual_config_bytes=config,
        actual_log_bytes=log,
        input_bytes_by_path=input_bytes,
        registration_quality_passed=registration_quality_passed,
        policy=policy,
    )


# ============================================================
# Phase 1: Schema basics
# ============================================================

class TestSchema:
    def test_request_requires_all_fields(self):
        with pytest.raises(ValidationError):
            TrainingRequest()  # type: ignore[call-arg]

    def test_request_requires_requested_config_sha256(self):
        with pytest.raises(ValidationError):
            TrainingRequest(
                request_id="r1",
                created_at_utc=_T0,
                input_bindings=(_make_input_binding(),),
                training_config=_make_config(),
                expected_output_format="inria-3dgs-ply",
            )

    def test_config_requires_seed(self):
        with pytest.raises(ValidationError):
            TrainingConfig(
                trainer_name="nerfstudio-splatfacto",
                trainer_version="0.3.4",
                max_resolution=1024,
                total_steps=30000,
            )

    def test_request_is_frozen(self):
        req = _make_request()
        with pytest.raises((ValidationError, TypeError)):
            req.request_id = "x"  # type: ignore[misc]

    def test_input_binding_sha_must_be_64_hex(self):
        with pytest.raises(ValidationError):
            _make_input_binding(sha="not-a-sha")

    def test_failed_status_requires_error_message(self):
        with pytest.raises(ValidationError):
            TrainingStatus(state="failed", exit_code=1)

    def test_completed_status_rejects_error_message(self):
        with pytest.raises(ValidationError):
            TrainingStatus(state="completed", exit_code=0, error_message="oops")

    def test_non_utc_timestamp_rejected(self):
        naive = datetime(2026, 7, 23, 10, 0, 0)
        with pytest.raises(ValidationError):
            TrainingRequest(
                request_id="r1",
                created_at_utc=naive,
                input_bindings=(_make_input_binding(),),
                training_config=_make_config(),
                expected_output_format="inria-3dgs-ply",
                requested_config_sha256=_CONFIG_SHA,
            )

    def test_started_after_finished_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        with pytest.raises(ValidationError):
            TrainingResult(
                **{
                    **result.model_dump(),
                    "started_at_utc": _T1,
                    "finished_at_utc": _T0,
                }
            )


# ============================================================
# Phase 2: Canonical SHA determinism
# ============================================================

class TestCanonicalSha:
    def test_request_canonical_sha_deterministic(self):
        assert request_canonical_sha256(_make_request()) == request_canonical_sha256(
            _make_request()
        )

    def test_request_sha_changes_with_config(self):
        r1 = _make_request(config=_make_config(random_seed=42))
        r2 = _make_request(config=_make_config(random_seed=99))
        assert request_canonical_sha256(r1) != request_canonical_sha256(r2)

    def test_result_canonical_sha_deterministic(self):
        pack = _make_honest()
        assert result_canonical_sha256(pack[0]) == result_canonical_sha256(
            _make_honest()[0]
        )


# ============================================================
# Phase 3: Honest validation passes
# ============================================================

class TestHonestValidation:
    def test_builder_produces_valid_result(self):
        _validate(_make_honest())

    def test_completed_honest_result_passes_all_checks(self):
        pack = _make_honest()
        trust = _trust(pack, registration_quality_passed=True)
        assert trust.is_trustworthy is True
        assert trust.content_closed is True
        assert trust.trainer_identified is True

    def test_roundtrip_json(self):
        pack = _make_honest()
        result = pack[0]
        loaded = TrainingResult.model_validate_json(result.model_dump_json())
        assert loaded == result


# ============================================================
# Phase 4: Trainer drift (REVIEW-CODEX-022 P0.2 #2)
# ============================================================

class TestTrainerDrift:
    def test_trainer_name_mismatch_rejected_by_default(self):
        pack = _make_honest(trainer_name="brush")
        with pytest.raises(ValueError, match="trainer drift"):
            _validate(pack)

    def test_trainer_version_mismatch_rejected_by_default(self):
        pack = _make_honest(trainer_version="brush 0.3.0")
        with pytest.raises(ValueError, match="trainer drift"):
            _validate(pack)

    def test_trainer_drift_allowed_with_record_and_policy(self):
        request = _make_request(config=_make_config(trainer_name="nerfstudio-splatfacto"))
        drift = TrainerDriftRecord(
            requested_trainer_name="nerfstudio-splatfacto",
            requested_trainer_version="nerfstudio 0.3.4",
            actual_trainer_name="brush",
            actual_trainer_version="brush 0.3.0",
            reason="cloud image upgraded brush",
        )
        pack = _make_honest(
            request=request,
            trainer_name="brush",
            trainer_version="brush 0.3.0",
            trainer_drift=drift,
        )
        _validate(pack, policy=TrainingDriftPolicy(allow_trainer_drift=True))

    def test_trainer_drift_allowed_but_no_record_rejected(self):
        pack = _make_honest(trainer_name="brush")
        with pytest.raises(ValueError, match="no trainer_drift record"):
            _validate(pack, policy=TrainingDriftPolicy(allow_trainer_drift=True))

    def test_drift_record_inconsistent_rejected(self):
        request = _make_request(config=_make_config(trainer_name="nerfstudio-splatfacto"))
        drift = TrainerDriftRecord(
            requested_trainer_name="brush",  # wrong: should be nerfstudio
            requested_trainer_version="x",
            actual_trainer_name="brush",
            actual_trainer_version="brush 0.3.0",
            reason="x",
        )
        pack = _make_honest(
            request=request,
            trainer_name="brush",
            trainer_version="brush 0.3.0",
            trainer_drift=drift,
        )
        with pytest.raises(ValueError, match="inconsistent"):
            _validate(pack, policy=TrainingDriftPolicy(allow_trainer_drift=True))

    def test_drift_record_present_without_drift_rejected(self):
        drift = TrainerDriftRecord(
            requested_trainer_name="nerfstudio-splatfacto",
            requested_trainer_version="nerfstudio 0.3.4",
            actual_trainer_name="nerfstudio-splatfacto",
            actual_trainer_version="nerfstudio 0.3.4",
            reason="no actual drift",
        )
        pack = _make_honest(trainer_drift=drift)
        with pytest.raises(ValueError, match="no actual drift"):
            _validate(pack)

    def test_allowed_drift_still_not_trustworthy(self):
        request = _make_request(config=_make_config(trainer_name="nerfstudio-splatfacto"))
        drift = TrainerDriftRecord(
            requested_trainer_name="nerfstudio-splatfacto",
            requested_trainer_version="nerfstudio 0.3.4",
            actual_trainer_name="brush",
            actual_trainer_version="brush 0.3.0",
            reason="cloud image upgraded",
        )
        pack = _make_honest(
            request=request,
            trainer_name="brush",
            trainer_version="brush 0.3.0",
            trainer_drift=drift,
        )
        trust = _trust(
            pack,
            registration_quality_passed=True,
            policy=TrainingDriftPolicy(allow_trainer_drift=True),
        )
        assert trust.content_closed is True
        assert trust.trainer_identified is False
        assert trust.is_trustworthy is False


# ============================================================
# Phase 5: Config drift (REVIEW-CODEX-022 P0.2 #3)
# ============================================================

class TestConfigDrift:
    def test_config_sha_mismatch_with_bytes_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        # Tamper: claim a different config sha (still 64-hex) but pass real bytes.
        tampered = result.model_copy(
            update={"actual_config_sha256": _SHA_B}
        )
        with pytest.raises(ValueError, match="actual_config_sha256"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_config_size_mismatch_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"actual_config_size_bytes": len(config) + 999}
        )
        with pytest.raises(ValueError, match="actual_config_size_bytes"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_config_drift_vs_requested_rejected_by_default(self):
        # Request declares one config sha; actual config bytes hash differently.
        request = _make_request(requested_config_sha256=_SHA_A)
        pack = _make_honest(request=request, config_bytes=_CONFIG_BYTES)
        with pytest.raises(ValueError, match="config drift"):
            _validate(pack)

    def test_config_drift_allowed_by_policy(self):
        request = _make_request(requested_config_sha256=_SHA_A)
        pack = _make_honest(request=request, config_bytes=_CONFIG_BYTES)
        _validate(pack, policy=TrainingDriftPolicy(allow_config_drift=True))


# ============================================================
# Phase 6: Status / PLY consistency (REVIEW-CODEX-022 P0.2 #4)
# ============================================================

class TestStatusPly:
    def test_completed_with_exit_code_99_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        # Schema allows exit_code=99 with state=completed (only error_message
        # consistency is schema-checked). Validator must catch exit_code!=0.
        tampered = result.model_copy(
            update={
                "training_status": TrainingStatus(
                    state="completed", exit_code=99
                ),
            }
        )
        with pytest.raises(ValueError, match="exit_code"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_failed_with_exit_code_0_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        empty_sha = hashlib.sha256(b"").hexdigest()
        tampered = result.model_copy(
            update={
                "training_status": TrainingStatus(
                    state="failed", exit_code=0, error_message="x"
                ),
                "primary_ply_sha256": empty_sha,
                "primary_ply_size_bytes": 0,
                "output_bindings": tuple(
                    b for b in result.output_bindings
                    if b.artifact_kind != "trained_ply"
                ),
            }
        )
        with pytest.raises(ValueError, match="non-zero exit_code"):
            _validate((tampered, request, input_bytes, b"", config, log))

    def test_completed_without_trained_ply_binding_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={
                "output_bindings": tuple(
                    b for b in result.output_bindings
                    if b.artifact_kind != "trained_ply"
                ),
            }
        )
        with pytest.raises(ValueError, match="non-empty trained_ply"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_completed_with_two_trained_ply_bindings_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        extra = TrainingOutputBinding(
            artifact_kind="trained_ply",
            artifact_sha256=_PLY_SHA,
            artifact_path="other.ply",
            artifact_size_bytes=len(ply),
        )
        tampered = result.model_copy(
            update={"output_bindings": (*result.output_bindings, extra)}
        )
        with pytest.raises(ValueError, match="exactly one non-empty"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_trained_ply_binding_sha_not_primary_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        outputs = list(result.output_bindings)
        for i, b in enumerate(outputs):
            if b.artifact_kind == "trained_ply":
                outputs[i] = b.model_copy(update={"artifact_sha256": _SHA_B})
        tampered = result.model_copy(update={"output_bindings": tuple(outputs)})
        with pytest.raises(ValueError, match="trained_ply binding sha"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_primary_ply_sha_not_matching_bytes_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(update={"primary_ply_sha256": _SHA_B})
        with pytest.raises(ValueError, match="primary_ply_sha256"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_primary_ply_size_mismatch_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"primary_ply_size_bytes": len(ply) + 1}
        )
        with pytest.raises(ValueError, match="primary_ply_size_bytes"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_failed_run_with_trained_ply_output_rejected(self):
        pack = _make_honest(exit_code=1)
        result, request, input_bytes, ply, config, log = pack
        # Builder correctly omits trained_ply for failed; inject one to test.
        bad_ply = TrainingOutputBinding(
            artifact_kind="trained_ply",
            artifact_sha256=_PLY_SHA,
            artifact_path="x.ply",
            artifact_size_bytes=len(ply),
        )
        tampered = result.model_copy(
            update={"output_bindings": (*result.output_bindings, bad_ply)}
        )
        with pytest.raises(ValueError, match="cannot declare trained_ply"):
            _validate((tampered, request, input_bytes, b"", config, log))

    def test_failed_run_with_nonempty_primary_ply_rejected(self):
        pack = _make_honest(exit_code=1)
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"primary_ply_sha256": _PLY_SHA, "primary_ply_size_bytes": len(ply)}
        )
        with pytest.raises(ValueError, match="primary_ply_sha256 must be"):
            _validate((tampered, request, input_bytes, b"", config, log))

    def test_builder_exit_code_0_no_ply_is_interrupted(self):
        request = _make_request()
        result = build_training_result(
            request=request,
            result_id="r",
            started_at_utc=_T0,
            finished_at_utc=_T1,
            actual_trainer_name=request.training_config.trainer_name,
            actual_trainer_version=request.training_config.trainer_version,
            actual_config_bytes=_CONFIG_BYTES,
            actual_ply_bytes=b"",
            actual_log_bytes=_LOG_BYTES,
            input_bytes_by_path={
                request.input_bindings[0].artifact_path: b"x" * 1000
            },
            gpu_environment=GpuEnvironment(
                gpu_name="g", gpu_memory_mb=1, cuda_version="1", driver_version="1"
            ),
            exit_code=0,
        )
        assert result.training_status.state == "interrupted"


# ============================================================
# Phase 7: Log binding (REVIEW-CODEX-022 P0.2 #5)
# ============================================================

class TestLogBinding:
    def test_log_sha_not_matching_bytes_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(update={"training_log_sha256": _SHA_B})
        with pytest.raises(ValueError, match="training_log_sha256"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_log_size_mismatch_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"training_log_size_bytes": len(log) + 5}
        )
        with pytest.raises(ValueError, match="training_log_size_bytes"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_no_training_log_output_binding_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={
                "output_bindings": tuple(
                    b for b in result.output_bindings
                    if b.artifact_kind != "training_log"
                ),
            }
        )
        with pytest.raises(ValueError, match="training_log output binding"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_log_output_binding_sha_not_matching_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        outputs = list(result.output_bindings)
        for i, b in enumerate(outputs):
            if b.artifact_kind == "training_log":
                outputs[i] = b.model_copy(update={"artifact_sha256": _SHA_B})
        tampered = result.model_copy(update={"output_bindings": tuple(outputs)})
        with pytest.raises(ValueError, match="training_log binding sha"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_two_log_bindings_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        extra = TrainingOutputBinding(
            artifact_kind="training_log",
            artifact_sha256=_LOG_SHA,
            artifact_path="other.log",
            artifact_size_bytes=len(log),
        )
        tampered = result.model_copy(
            update={"output_bindings": (*result.output_bindings, extra)}
        )
        with pytest.raises(ValueError, match="exactly one training_log"):
            _validate((tampered, request, input_bytes, ply, config, log))


# ============================================================
# Phase 8: Config output binding (REVIEW-CODEX-022 P0.2 #3)
# ============================================================

class TestConfigOutputBinding:
    def test_no_config_yml_output_binding_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={
                "output_bindings": tuple(
                    b for b in result.output_bindings
                    if b.artifact_kind != "training_config_yml"
                ),
            }
        )
        with pytest.raises(ValueError, match="training_config_yml"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_config_yml_binding_sha_not_matching_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        outputs = list(result.output_bindings)
        for i, b in enumerate(outputs):
            if b.artifact_kind == "training_config_yml":
                outputs[i] = b.model_copy(update={"artifact_sha256": _SHA_B})
        tampered = result.model_copy(update={"output_bindings": tuple(outputs)})
        with pytest.raises(ValueError, match="training_config_yml binding sha"):
            _validate((tampered, request, input_bytes, ply, config, log))


# ============================================================
# Phase 9: Input closure (REVIEW-CODEX-022 P0.2 #1)
# ============================================================

class TestInputClosure:
    def test_request_sha_mismatch_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"request_canonical_sha256": _SHA_B}
        )
        with pytest.raises(ValueError, match="request_canonical_sha256"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_input_binding_order_mismatch_rejected(self):
        b1 = _make_input_binding(path="a.json", sha=_SHA_A, size=10)
        b2 = _make_input_binding(
            path="b.json", sha=_SHA_B, size=20, kind="registration_json"
        )
        request = _make_request(input_bindings=(b1, b2))
        pack = _make_honest(request=request)
        result, request, input_bytes, ply, config, log = pack
        # Swap order in result.
        tampered = result.model_copy(
            update={"actual_input_bindings": (b2, b1)}
        )
        with pytest.raises(ValueError, match="actual_input_bindings"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_input_binding_kind_mismatch_rejected(self):
        b1 = _make_input_binding(path="a.json", sha=_SHA_A, size=10)
        request = _make_request(input_bindings=(b1,))
        pack = _make_honest(request=request)
        result, request, input_bytes, ply, config, log = pack
        wrong_kind = b1.model_copy(update={"artifact_kind": "registration_json"})
        tampered = result.model_copy(
            update={"actual_input_bindings": (wrong_kind,)}
        )
        with pytest.raises(ValueError, match="actual_input_bindings"):
            _validate((tampered, request, input_bytes, ply, config, log))

    def test_input_bytes_sha_mismatch_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        path = request.input_bindings[0].artifact_path
        bad = input_bytes.copy()
        bad[path] = b"tampered bytes content"
        with pytest.raises(ValueError, match="sha mismatch"):
            _validate((result, request, bad, ply, config, log))

    def test_input_bytes_size_mismatch_rejected(self):
        # Binding declares a size that lies about the real artifact while the
        # SHA is honest to the (smaller) real bytes.  Input closure holds
        # (request/result bindings match) and the SHA check passes, but the
        # size check fails.  The result is built from the same lying request so
        # request_canonical_sha256 stays consistent.
        real_bytes = b"x" * 1000
        real_sha = hashlib.sha256(real_bytes).hexdigest()
        lying_binding = TrainingInputBinding(
            artifact_kind="capture_manifest",
            artifact_sha256=real_sha,
            artifact_path="capture/manifest.json",
            artifact_size_bytes=1100,
        )
        request = _make_request(input_bindings=(lying_binding,))
        result = build_training_result(
            request=request,
            result_id="r",
            started_at_utc=_T0,
            finished_at_utc=_T1,
            actual_trainer_name=request.training_config.trainer_name,
            actual_trainer_version=request.training_config.trainer_version,
            actual_config_bytes=_CONFIG_BYTES,
            actual_ply_bytes=_PLY_BYTES,
            actual_log_bytes=_LOG_BYTES,
            input_bytes_by_path={"capture/manifest.json": real_bytes},
            gpu_environment=GpuEnvironment(
                gpu_name="g", gpu_memory_mb=1, cuda_version="1", driver_version="1"
            ),
            exit_code=0,
        )
        with pytest.raises(ValueError, match="size mismatch"):
            validate_training_provenance(
                result,
                request,
                actual_ply_bytes=_PLY_BYTES,
                actual_config_bytes=_CONFIG_BYTES,
                actual_log_bytes=_LOG_BYTES,
                input_bytes_by_path={"capture/manifest.json": real_bytes},
            )

    def test_missing_input_bytes_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        path = request.input_bindings[0].artifact_path
        bad = {k: v for k, v in input_bytes.items() if k != path}
        with pytest.raises(ValueError, match="missing actual input bytes"):
            _validate((result, request, bad, ply, config, log))

    def test_duplicate_input_paths_rejected(self):
        # Two bindings with same path — schema doesn't check uniqueness of
        # path across bindings, validator does.
        b1 = _make_input_binding(path="dup.json", sha=_SHA_A, size=10)
        b2 = _make_input_binding(
            path="dup.json", sha=_SHA_B, size=20, kind="registration_json"
        )
        request = _make_request(input_bindings=(b1, b2))
        pack = _make_honest(request=request)
        with pytest.raises(ValueError, match="unique"):
            _validate(pack)


# ============================================================
# Phase 10: Timestamps (REVIEW-CODEX-022 P0.2 #6)
# ============================================================

class TestTimestamps:
    def test_started_equals_finished_accepted(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        same = result.model_copy(
            update={"started_at_utc": _T1, "finished_at_utc": _T1}
        )
        _validate((same, request, input_bytes, ply, config, log))

    def test_non_utc_result_timestamp_rejected(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        naive_dump = result.model_dump()
        naive_dump["started_at_utc"] = datetime(2026, 7, 23, 10, 0, 0)
        with pytest.raises(ValidationError):
            TrainingResult(**naive_dump)


# ============================================================
# Phase 11: Adversarial fail-closed (the REVIEW-CODEX-022 cases)
# ============================================================

class TestAdversarialFailClosed:
    def test_trainer_drift_config_drift_exit99_fake_log_is_not_trustworthy(self):
        """The headline adversarial case: unrequested trainer, arbitrary config
        SHA, exit code 99, false output size, invented log SHA — must NOT
        produce is_trustworthy=True even with registration quality passed."""
        request = _make_request(config=_make_config(trainer_name="nerfstudio-splatfacto"))
        pack = _make_honest(request=request)
        result, req, input_bytes, ply, config, log = pack
        # Tamper everything the old validator ignored.
        tampered = result.model_copy(
            update={
                "actual_trainer_name": "brush",
                "actual_trainer_version": "brush 9.9",
                "actual_config_sha256": _SHA_B,
                "actual_config_size_bytes": 1,
                "training_log_sha256": _SHA_A,
                "training_log_size_bytes": 999,
                "training_status": TrainingStatus(
                    state="completed", exit_code=99
                ),
            }
        )
        trust = _trust(
            (tampered, req, input_bytes, ply, config, log),
            registration_quality_passed=True,
        )
        assert trust.is_trustworthy is False
        assert trust.content_closed is False
        assert trust.trainer_identified is False

    def test_sha_looking_string_not_treated_as_evidence(self):
        """A 64-hex string in actual_config_sha256 that does not match real
        config bytes must not unlock trust."""
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"actual_config_sha256": "0" * 64}
        )
        trust = _trust(
            (tampered, request, input_bytes, ply, config, log),
            registration_quality_passed=True,
        )
        assert trust.is_trustworthy is False

    def test_log_sha_not_matching_bytes_blocks_trust(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={"training_log_sha256": "1" * 64}
        )
        trust = _trust(
            (tampered, request, input_bytes, ply, config, log),
            registration_quality_passed=True,
        )
        assert trust.is_trustworthy is False

    def test_exit_code_99_completed_blocks_trust(self):
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        tampered = result.model_copy(
            update={
                "training_status": TrainingStatus(
                    state="completed", exit_code=99
                ),
            }
        )
        trust = _trust(
            (tampered, request, input_bytes, ply, config, log),
            registration_quality_passed=True,
        )
        assert trust.is_trustworthy is False

    def test_registration_quality_false_blocks_trust(self):
        pack = _make_honest()
        trust = _trust(pack, registration_quality_passed=False)
        assert trust.is_trustworthy is False
        assert trust.registration_quality_passed is False

    def test_inputs_verified_requires_real_byte_verification(self):
        """Input bytes that don't match declared SHAs must block trust even
        when everything else is honest."""
        pack = _make_honest()
        result, request, input_bytes, ply, config, log = pack
        path = request.input_bindings[0].artifact_path
        bad = input_bytes.copy()
        bad[path] = b"completely different content"
        trust = _trust(
            (result, request, bad, ply, config, log),
            registration_quality_passed=True,
        )
        assert trust.is_trustworthy is False
        assert trust.inputs_verified is False


# ============================================================
# Phase 12: Builder honesty
# ============================================================

class TestBuilder:
    def test_builder_completed_produces_trained_ply(self):
        pack = _make_honest()
        result = pack[0]
        ply_bindings = [
            b for b in result.output_bindings if b.artifact_kind == "trained_ply"
        ]
        assert len(ply_bindings) == 1
        assert result.training_status.state == "completed"
        assert result.training_status.exit_code == 0

    def test_builder_failed_omits_trained_ply(self):
        pack = _make_honest(exit_code=1)
        result = pack[0]
        assert result.training_status.state == "failed"
        ply_bindings = [
            b for b in result.output_bindings if b.artifact_kind == "trained_ply"
        ]
        assert len(ply_bindings) == 0
        assert result.primary_ply_sha256 == hashlib.sha256(b"").hexdigest()
        assert result.primary_ply_size_bytes == 0

    def test_builder_result_passes_validation(self):
        _validate(_make_honest())
        _validate(_make_honest(exit_code=1))

    def test_trust_true_does_not_imply_metric(self):
        pack = _make_honest()
        trust = _trust(pack, registration_quality_passed=True)
        assert trust.is_trustworthy is True
        assert not hasattr(trust, "metric")
        assert not hasattr(trust, "aligned")
        assert not hasattr(trust, "real_photos")
