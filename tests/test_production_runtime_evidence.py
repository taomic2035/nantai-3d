from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pipeline.production_runtime_evidence import (
    ExecutableSnapshot,
    ExecutionEnvironmentObservation,
    GpuRuntimeObservation,
    ProbeObservationBinding,
    ProductionRuntimeEvidenceError,
    ProductionRuntimeMeasurement,
    ProductionRuntimePolicy,
    StableExecutableObservation,
    TrainingCliObservation,
    canonical_production_runtime_decision_bytes,
    canonical_production_runtime_measurement_bytes,
    canonical_production_runtime_policy_bytes,
    decide_production_runtime,
    execution_environment_sha256,
    load_production_runtime_decision_bytes,
    load_production_runtime_measurement_bytes,
    load_production_runtime_policy_bytes,
    training_cli_schema_sha256,
    verify_production_runtime_decision,
)

_ROLES = (
    "checker",
    "container-runtime",
    "ns-train",
    "nvidia-smi",
    "python",
    "worker",
)
_PROBES = (
    "container-identity",
    "cuda-runtime",
    "gpu-device",
    "nerfstudio-version",
    "python-runtime",
    "training-cli-schema",
)
_OPTIONS = (
    "--data",
    "--max-num-iterations",
    "--output-dir",
    "--pipeline.datamanager.dataparser.auto-scale-poses",
    "--pipeline.datamanager.dataparser.center-method",
    "--pipeline.datamanager.dataparser.orientation-method",
    "--pipeline.datamanager.dataparser.scale-factor",
)
_CONTAINER = "registry.example/nantai@sha256:" + "c" * 64


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _executable(role: str) -> StableExecutableObservation:
    snapshot = ExecutableSnapshot(
        resolved_path=f"/opt/nantai/bin/{role}",
        byte_length=4096,
        sha256=_sha(role),
        device=8,
        inode=100 + _ROLES.index(role),
        mode=0o100755,
        mtime_ns=1_700_000_000_000_000_000,
        ctime_ns=1_700_000_000_000_000_001,
    )
    return StableExecutableObservation(
        role=role,
        probe_definition_sha256=_sha(f"definition:{role}"),
        before=snapshot,
        after=snapshot,
    )


def _measurement() -> ProductionRuntimeMeasurement:
    executables = tuple(_executable(role) for role in _ROLES)
    environment = ExecutionEnvironmentObservation(
        kind="fresh-job-container",
        container_runtime="docker",
        container_instance_id="1" * 64,
        configured_container_identity=_CONTAINER,
        observed_container_identity=_CONTAINER,
        runtime_executable_sha256=_sha("container-runtime"),
    )
    environment_sha = execution_environment_sha256(environment)
    cli_schema = training_cli_schema_sha256(
        trainer_name="nerfstudio-splatfacto",
        observed_options=_OPTIONS,
    )
    return ProductionRuntimeMeasurement.create(
        observed_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        exact_commit="a" * 40,
        remote_target_sha256="b" * 64,
        durable_job_ref_sha256="d" * 64,
        workspace_identity_sha256="e" * 64,
        environment=environment,
        executables=executables,
        gpu=GpuRuntimeObservation(
            uuid="GPU-12345678-1234-1234-1234-123456789abc",
            name="NVIDIA RTX 4090",
            memory_total_mib=24_564,
            driver_version="575.64.03",
            cuda_runtime_version="12.8",
            compute_capability="8.9",
            nvidia_smi_executable_sha256=_sha("nvidia-smi"),
        ),
        training_cli=TrainingCliObservation(
            trainer_name="nerfstudio-splatfacto",
            python_version="3.11.9",
            nerfstudio_version="1.1.5",
            observed_options=_OPTIONS,
            schema_sha256=cli_schema,
            help_stdout_sha256=_sha("ns-train-help"),
            python_executable_sha256=_sha("python"),
            training_cli_executable_sha256=_sha("ns-train"),
        ),
        probes=tuple(
            ProbeObservationBinding(
                probe_id=probe_id,
                definition_sha256=_sha(f"probe:{probe_id}"),
                execution_environment_sha256=environment_sha,
                stdout_sha256=_sha(f"stdout:{probe_id}"),
                stderr_sha256=_sha(f"stderr:{probe_id}"),
                exit_code=0,
            )
            for probe_id in _PROBES
        ),
    )


def _policy(
    measurement: ProductionRuntimeMeasurement,
    **updates,
) -> ProductionRuntimePolicy:
    fields = {
        "expected_exact_commit": measurement.exact_commit,
        "expected_remote_target_sha256": (
            measurement.remote_target_sha256
        ),
        "expected_probe_set_sha256": measurement.probe_set_sha256,
        "expected_container_identity": _CONTAINER,
        "expected_gpu_uuid": measurement.gpu.uuid,
        "min_gpu_memory_mib": 16_384,
        "expected_cuda_runtime_version": "12.8",
        "expected_python_version": "3.11.9",
        "expected_nerfstudio_version": "1.1.5",
        "expected_training_cli_schema_sha256": (
            measurement.training_cli.schema_sha256
        ),
        "required_training_cli_options": _OPTIONS,
        "expected_checker_sha256": _sha("checker"),
        "expected_container_runtime_sha256": _sha(
            "container-runtime"
        ),
        "expected_nvidia_smi_sha256": _sha("nvidia-smi"),
        "expected_python_sha256": _sha("python"),
        "expected_training_cli_sha256": _sha("ns-train"),
        "expected_worker_sha256": _sha("worker"),
    }
    fields.update(updates)
    return ProductionRuntimePolicy.create(**fields)


def test_runtime_measurement_policy_and_decision_are_content_addressed():
    measurement = _measurement()
    policy = _policy(measurement)
    decision = decide_production_runtime(measurement, policy)

    assert decision.status == "accepted"
    assert decision.failure_codes == ()
    assert decision.runtime_measurement_sha256 == (
        measurement.content_sha256
    )
    assert decision.policy_sha256 == policy.content_sha256
    assert decision.container_identity == _CONTAINER
    assert decision.gpu_uuid == measurement.gpu.uuid
    assert load_production_runtime_measurement_bytes(
        canonical_production_runtime_measurement_bytes(measurement)
    ) == measurement
    assert load_production_runtime_policy_bytes(
        canonical_production_runtime_policy_bytes(policy)
    ) == policy
    assert load_production_runtime_decision_bytes(
        canonical_production_runtime_decision_bytes(decision)
    ) == decision
    verify_production_runtime_decision(
        measurement=measurement,
        policy=policy,
        decision=decision,
    )


def test_policy_change_does_not_rewrite_runtime_measurement():
    measurement = _measurement()
    first = _policy(measurement)
    second = _policy(measurement, min_gpu_memory_mib=32_768)

    first_decision = decide_production_runtime(measurement, first)
    second_decision = decide_production_runtime(measurement, second)

    assert first.content_sha256 != second.content_sha256
    assert first_decision.content_sha256 != second_decision.content_sha256
    assert measurement == _measurement()
    assert second_decision.status == "rejected"
    assert second_decision.failure_codes == ("gpu-memory-insufficient",)


@pytest.mark.parametrize(
    ("update", "failure_code"),
    [
        ({"expected_exact_commit": "9" * 40}, "commit-identity-mismatch"),
        (
            {"expected_remote_target_sha256": "9" * 64},
            "remote-target-mismatch",
        ),
        (
            {
                "expected_container_identity": (
                    "registry.example/other@sha256:" + "9" * 64
                )
            },
            "container-identity-mismatch",
        ),
        (
            {"expected_cuda_runtime_version": "12.4"},
            "cuda-runtime-mismatch",
        ),
        (
            {"expected_nerfstudio_version": "1.1.4"},
            "nerfstudio-version-mismatch",
        ),
        (
            {"expected_training_cli_schema_sha256": "9" * 64},
            "training-cli-schema-mismatch",
        ),
        (
            {"expected_checker_sha256": "9" * 64},
            "checker-identity-mismatch",
        ),
        (
            {"expected_container_runtime_sha256": "9" * 64},
            "container-runtime-identity-mismatch",
        ),
        (
            {"expected_nvidia_smi_sha256": "9" * 64},
            "nvidia-smi-identity-mismatch",
        ),
        (
            {"expected_python_sha256": "9" * 64},
            "python-identity-mismatch",
        ),
        (
            {"expected_training_cli_sha256": "9" * 64},
            "training-cli-identity-mismatch",
        ),
        (
            {"expected_worker_sha256": "9" * 64},
            "worker-identity-mismatch",
        ),
    ],
)
def test_runtime_policy_mismatches_reject_without_output_claims(
    update,
    failure_code,
):
    measurement = _measurement()
    decision = decide_production_runtime(
        measurement,
        _policy(measurement, **update),
    )

    assert decision.status == "rejected"
    assert failure_code in decision.failure_codes
    assert decision.container_identity is None
    assert decision.gpu_uuid is None
    assert decision.execution_environment_sha256 is None


def test_executable_toctou_is_rejected():
    executable = _executable("python")
    changed = executable.after.model_copy(update={"inode": 999})

    with pytest.raises(ValidationError, match="changed during probe"):
        StableExecutableObservation(
            role="python",
            probe_definition_sha256=executable.probe_definition_sha256,
            before=executable.before,
            after=changed,
        )


def test_runtime_measurement_forbids_reused_container_identity():
    measurement = _measurement()

    with pytest.raises(ValidationError, match="fresh-job-container"):
        ExecutionEnvironmentObservation.model_validate(
            measurement.environment.model_copy(
                update={"kind": "existing-container"}
            ).model_dump()
        )


def test_python_invoked_checker_and_worker_sources_may_be_nonexecutable():
    for role in ("checker", "worker"):
        snapshot = ExecutableSnapshot(
            resolved_path=f"/workspace/cloud/{role}.py",
            byte_length=4096,
            sha256=_sha(role),
            device=8,
            inode=200 + _ROLES.index(role),
            mode=0o100644,
            mtime_ns=1_700_000_000_000_000_000,
            ctime_ns=1_700_000_000_000_000_001,
        )
        observation = StableExecutableObservation(
            role=role,
            probe_definition_sha256=_sha(f"definition:{role}"),
            before=snapshot,
            after=snapshot,
        )

        assert observation.before.mode == 0o100644


def test_direct_runtime_executable_must_retain_execute_mode():
    snapshot = ExecutableSnapshot(
        resolved_path="/opt/nantai/bin/ns-train",
        byte_length=4096,
        sha256=_sha("ns-train"),
        device=8,
        inode=203,
        mode=0o100644,
        mtime_ns=1_700_000_000_000_000_000,
        ctime_ns=1_700_000_000_000_000_001,
    )

    with pytest.raises(
        ValidationError,
        match="direct runtime executable must be executable",
    ):
        StableExecutableObservation(
            role="ns-train",
            probe_definition_sha256=_sha("definition:ns-train"),
            before=snapshot,
            after=snapshot,
        )


@pytest.mark.parametrize(
    ("field", "remaining"),
    [
        ("executables", _ROLES[:-1]),
        ("probes", _PROBES[:-1]),
    ],
)
def test_measurement_requires_the_complete_fixed_sets(field, remaining):
    measurement = _measurement()
    update = (
        tuple(
            item
            for item in measurement.executables
            if item.role in remaining
        )
        if field == "executables"
        else tuple(
            item
            for item in measurement.probes
            if item.probe_id in remaining
        )
    )

    with pytest.raises(ValidationError, match="fixed"):
        ProductionRuntimeMeasurement.model_validate(
            measurement.model_copy(
                update={field: update}
            ).model_dump(by_alias=True)
        )


def test_measurement_rejects_cross_role_executable_identity():
    measurement = _measurement()
    gpu = measurement.gpu.model_copy(
        update={"nvidia_smi_executable_sha256": _sha("python")}
    )

    with pytest.raises(ValidationError, match="nvidia-smi"):
        ProductionRuntimeMeasurement.model_validate(
            measurement.model_copy(
                update={"gpu": gpu}
            ).model_dump(by_alias=True)
        )


def test_measurement_rejects_host_probe_masquerading_as_container_probe():
    measurement = _measurement()
    first = measurement.probes[0].model_copy(
        update={"execution_environment_sha256": "9" * 64}
    )

    with pytest.raises(ValidationError, match="execution environment"):
        ProductionRuntimeMeasurement.model_validate(
            measurement.model_copy(
                update={"probes": (first, *measurement.probes[1:])}
            ).model_dump(by_alias=True)
        )


def test_canonical_loader_rejects_duplicate_keys_and_sha_tamper():
    measurement = _measurement()
    payload = canonical_production_runtime_measurement_bytes(measurement)
    duplicate = payload.replace(
        b'{"clean_tree":true,',
        b'{"clean_tree":true,"clean_tree":true,',
        1,
    )
    assert duplicate != payload

    with pytest.raises(
        ProductionRuntimeEvidenceError,
        match="duplicate",
    ):
        load_production_runtime_measurement_bytes(duplicate)

    tampered = payload.replace(measurement.content_sha256.encode(), b"9" * 64)
    with pytest.raises(
        ProductionRuntimeEvidenceError,
        match="invalid",
    ):
        load_production_runtime_measurement_bytes(tampered)


def test_decision_verifier_rejects_policy_or_measurement_drift():
    measurement = _measurement()
    policy = _policy(measurement)
    decision = decide_production_runtime(measurement, policy)

    with pytest.raises(
        ProductionRuntimeEvidenceError,
        match="decision",
    ):
        verify_production_runtime_decision(
            measurement=measurement,
            policy=_policy(
                measurement,
                min_gpu_memory_mib=32_768,
            ),
            decision=decision,
        )
