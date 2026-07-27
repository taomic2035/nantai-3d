"""Content-addressed production CUDA runtime evidence.

The fixed remote checker owns observation.  This module owns only the pure
trust contract: immutable measurements, independently versioned policy, and a
derived decision.  No caller can provide a ``ready`` boolean.
"""

from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class ProductionRuntimeEvidenceError(ValueError):
    """Runtime evidence is malformed, noncanonical, or inconsistent."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_CONTAINER_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
_GPU_UUID_PATTERN = (
    r"^GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9._+-]*)?$"
_MEASUREMENT_ID_PATTERN = (
    r"^production-runtime-measurement-[0-9a-f]{64}$"
)
_POLICY_ID_PATTERN = r"^production-runtime-policy-[0-9a-f]{64}$"
_DECISION_ID_PATTERN = r"^production-runtime-decision-[0-9a-f]{64}$"

ExecutableRole = Literal[
    "checker",
    "container-runtime",
    "ns-train",
    "nvidia-smi",
    "python",
    "worker",
]
ProbeId = Literal[
    "container-identity",
    "cuda-runtime",
    "gpu-device",
    "nerfstudio-version",
    "python-runtime",
    "training-cli-schema",
]
RuntimeFailureCode = Literal[
    "commit-identity-mismatch",
    "remote-target-mismatch",
    "probe-set-mismatch",
    "container-identity-mismatch",
    "gpu-identity-mismatch",
    "gpu-memory-insufficient",
    "cuda-runtime-mismatch",
    "python-version-mismatch",
    "nerfstudio-version-mismatch",
    "training-cli-schema-mismatch",
    "training-cli-options-missing",
    "checker-identity-mismatch",
    "container-runtime-identity-mismatch",
    "nvidia-smi-identity-mismatch",
    "python-identity-mismatch",
    "training-cli-identity-mismatch",
    "worker-identity-mismatch",
]

_REQUIRED_EXECUTABLE_ROLES: tuple[ExecutableRole, ...] = (
    "checker",
    "container-runtime",
    "ns-train",
    "nvidia-smi",
    "python",
    "worker",
)
_REQUIRED_PROBE_IDS: tuple[ProbeId, ...] = (
    "container-identity",
    "cuda-runtime",
    "gpu-device",
    "nerfstudio-version",
    "python-runtime",
    "training-cli-schema",
)


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
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


def _content_sha(model: BaseModel) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            model.model_dump(
                mode="json",
                by_alias=True,
                exclude={
                    "measurement_id",
                    "policy_id",
                    "decision_id",
                    "content_sha256",
                },
            )
        )
    ).hexdigest()


def _require_utc(value: datetime) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("runtime observation timestamp must be UTC")
    return value


def _absolute_remote_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or not path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise ValueError("runtime executable path must be absolute POSIX")
    return value


def _validated_options(options: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not options
        or options != tuple(sorted(options))
        or len(options) != len(set(options))
        or any(
            not option.startswith("--")
            or len(option) < 3
            or any(
                not (
                    character.islower()
                    or character.isdigit()
                    or character in {".", "-"}
                )
                for character in option[2:]
            )
            for option in options
        )
    ):
        raise ValueError(
            "training CLI options must be sorted unique long options"
        )
    return options


def training_cli_schema_sha256(
    *,
    trainer_name: str,
    observed_options: tuple[str, ...],
) -> str:
    options = _validated_options(observed_options)
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "observed_options": list(options),
                "trainer_name": trainer_name,
            }
        )
    ).hexdigest()


class ExecutableSnapshot(FrozenModel):
    resolved_path: str
    byte_length: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    device: int = Field(ge=0)
    inode: int = Field(ge=1)
    mode: int = Field(ge=0, le=0o177777)
    mtime_ns: int = Field(ge=0)
    ctime_ns: int = Field(ge=0)

    _path = field_validator("resolved_path")(_absolute_remote_path)

    @model_validator(mode="after")
    def _is_regular_and_executable(self) -> ExecutableSnapshot:
        if not stat.S_ISREG(self.mode) or self.mode & 0o111 == 0:
            raise ValueError(
                "runtime executable snapshot must be regular and executable"
            )
        return self


class StableExecutableObservation(FrozenModel):
    role: ExecutableRole
    probe_definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    before: ExecutableSnapshot
    after: ExecutableSnapshot

    @model_validator(mode="after")
    def _snapshot_is_stable(self) -> StableExecutableObservation:
        if self.before != self.after:
            raise ValueError(
                f"{self.role} executable changed during probe"
            )
        return self


class ExecutionEnvironmentObservation(FrozenModel):
    kind: Literal["fresh-job-container"]
    container_runtime: Literal["docker", "podman"]
    container_instance_id: str = Field(pattern=_SHA256_PATTERN)
    configured_container_identity: str = Field(
        pattern=_CONTAINER_PATTERN
    )
    observed_container_identity: str = Field(
        pattern=_CONTAINER_PATTERN
    )
    runtime_executable_sha256: str = Field(pattern=_SHA256_PATTERN)


class GpuRuntimeObservation(FrozenModel):
    uuid: str = Field(pattern=_GPU_UUID_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    memory_total_mib: int = Field(ge=1)
    driver_version: str = Field(pattern=_VERSION_PATTERN)
    cuda_runtime_version: str = Field(pattern=_VERSION_PATTERN)
    compute_capability: str = Field(pattern=_VERSION_PATTERN)
    nvidia_smi_executable_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("name")
    @classmethod
    def _name_has_no_controls(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("GPU name contains control characters")
        return value


class TrainingCliObservation(FrozenModel):
    trainer_name: Literal["nerfstudio-splatfacto"]
    python_version: str = Field(pattern=_VERSION_PATTERN)
    nerfstudio_version: str = Field(pattern=_VERSION_PATTERN)
    observed_options: tuple[str, ...]
    schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    help_stdout_sha256: str = Field(pattern=_SHA256_PATTERN)
    python_executable_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_cli_executable_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )

    @field_validator("observed_options")
    @classmethod
    def _options_are_canonical(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validated_options(value)

    @model_validator(mode="after")
    def _schema_is_derived(self) -> TrainingCliObservation:
        expected = training_cli_schema_sha256(
            trainer_name=self.trainer_name,
            observed_options=self.observed_options,
        )
        if self.schema_sha256 != expected:
            raise ValueError("training CLI schema SHA disagrees")
        return self


class ProbeObservationBinding(FrozenModel):
    probe_id: ProbeId
    definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_environment_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    stdout_sha256: str = Field(pattern=_SHA256_PATTERN)
    stderr_sha256: str = Field(pattern=_SHA256_PATTERN)
    exit_code: Literal[0]


def probe_set_definition_sha256(
    probes: tuple[ProbeObservationBinding, ...],
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "probes": [
                    {
                        "definition_sha256": probe.definition_sha256,
                        "probe_id": probe.probe_id,
                    }
                    for probe in probes
                ]
            }
        )
    ).hexdigest()


class ProductionRuntimeMeasurement(FrozenModel):
    schema_id: Literal[
        "nantai.production-runtime-measurement.v1"
    ] = Field(
        default="nantai.production-runtime-measurement.v1",
        alias="schema",
        serialization_alias="schema",
    )
    measurement_id: str = Field(pattern=_MEASUREMENT_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_at_utc: datetime
    exact_commit: str = Field(pattern=_COMMIT_PATTERN)
    clean_tree: Literal[True] = True
    remote_target_sha256: str = Field(pattern=_SHA256_PATTERN)
    durable_job_ref_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    probe_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    environment: ExecutionEnvironmentObservation
    executables: tuple[StableExecutableObservation, ...]
    gpu: GpuRuntimeObservation
    training_cli: TrainingCliObservation
    probes: tuple[ProbeObservationBinding, ...]

    _utc = field_validator("observed_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _measurement_is_closed(
        self,
    ) -> ProductionRuntimeMeasurement:
        roles = tuple(item.role for item in self.executables)
        probe_ids = tuple(item.probe_id for item in self.probes)
        if roles != _REQUIRED_EXECUTABLE_ROLES:
            raise ValueError(
                "runtime measurement requires the fixed executable set"
            )
        if probe_ids != _REQUIRED_PROBE_IDS:
            raise ValueError(
                "runtime measurement requires the fixed probe set"
            )
        if self.probe_set_sha256 != probe_set_definition_sha256(
            self.probes
        ):
            raise ValueError(
                "runtime measurement probe-set SHA disagrees"
            )
        environment_sha = execution_environment_sha256(self.environment)
        if any(
            probe.execution_environment_sha256 != environment_sha
            for probe in self.probes
        ):
            raise ValueError(
                "runtime probe execution environment identity disagrees"
            )
        executable_sha = {
            item.role: item.before.sha256 for item in self.executables
        }
        if (
            self.environment.runtime_executable_sha256
            != executable_sha["container-runtime"]
        ):
            raise ValueError(
                "container runtime executable identity disagrees"
            )
        if (
            self.gpu.nvidia_smi_executable_sha256
            != executable_sha["nvidia-smi"]
        ):
            raise ValueError("nvidia-smi executable identity disagrees")
        if (
            self.training_cli.python_executable_sha256
            != executable_sha["python"]
        ):
            raise ValueError("Python executable identity disagrees")
        if (
            self.training_cli.training_cli_executable_sha256
            != executable_sha["ns-train"]
        ):
            raise ValueError("training CLI executable identity disagrees")
        expected = _content_sha(self)
        if self.content_sha256 != expected:
            raise ValueError("runtime measurement content SHA disagrees")
        if (
            self.measurement_id
            != f"production-runtime-measurement-{expected}"
        ):
            raise ValueError("runtime measurement id disagrees")
        return self

    @classmethod
    def create(cls, **fields) -> ProductionRuntimeMeasurement:
        probes = fields.get("probes")
        if not isinstance(probes, tuple):
            raise ValueError(
                "runtime measurement requires tuple probe observations"
            )
        derived_probe_set = probe_set_definition_sha256(probes)
        supplied_probe_set = fields.get("probe_set_sha256")
        if (
            supplied_probe_set is not None
            and supplied_probe_set != derived_probe_set
        ):
            raise ValueError(
                "runtime measurement probe-set SHA disagrees"
            )
        fields = {
            **fields,
            "probe_set_sha256": derived_probe_set,
        }
        zero = "0" * 64
        provisional = cls.model_construct(
            measurement_id=f"production-runtime-measurement-{zero}",
            content_sha256=zero,
            **fields,
        )
        digest = _content_sha(provisional)
        return cls(
            measurement_id=f"production-runtime-measurement-{digest}",
            content_sha256=digest,
            **fields,
        )


class ProductionRuntimePolicy(FrozenModel):
    schema_id: Literal["nantai.production-runtime-policy.v1"] = Field(
        default="nantai.production-runtime-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    policy_id: str = Field(pattern=_POLICY_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_exact_commit: str = Field(pattern=_COMMIT_PATTERN)
    expected_remote_target_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_probe_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_container_identity: str = Field(
        pattern=_CONTAINER_PATTERN
    )
    expected_gpu_uuid: str = Field(pattern=_GPU_UUID_PATTERN)
    min_gpu_memory_mib: int = Field(ge=1)
    expected_cuda_runtime_version: str = Field(
        pattern=_VERSION_PATTERN
    )
    expected_python_version: str = Field(pattern=_VERSION_PATTERN)
    expected_nerfstudio_version: str = Field(
        pattern=_VERSION_PATTERN
    )
    expected_training_cli_schema_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    required_training_cli_options: tuple[str, ...]
    expected_checker_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_container_runtime_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_nvidia_smi_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_python_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_training_cli_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_worker_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("required_training_cli_options")
    @classmethod
    def _options_are_canonical(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _validated_options(value)

    @model_validator(mode="after")
    def _policy_is_content_addressed(self) -> ProductionRuntimePolicy:
        expected = _content_sha(self)
        if self.content_sha256 != expected:
            raise ValueError("runtime policy content SHA disagrees")
        if self.policy_id != f"production-runtime-policy-{expected}":
            raise ValueError("runtime policy id disagrees")
        return self

    @classmethod
    def create(cls, **fields) -> ProductionRuntimePolicy:
        zero = "0" * 64
        provisional = cls.model_construct(
            policy_id=f"production-runtime-policy-{zero}",
            content_sha256=zero,
            **fields,
        )
        digest = _content_sha(provisional)
        return cls(
            policy_id=f"production-runtime-policy-{digest}",
            content_sha256=digest,
            **fields,
        )


class ProductionRuntimeDecision(FrozenModel):
    schema_id: Literal["nantai.production-runtime-decision.v1"] = Field(
        default="nantai.production-runtime-decision.v1",
        alias="schema",
        serialization_alias="schema",
    )
    decision_id: str = Field(pattern=_DECISION_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_measurement_sha256: str = Field(pattern=_SHA256_PATTERN)
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["accepted", "rejected"]
    failure_codes: tuple[RuntimeFailureCode, ...]
    execution_environment_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    container_identity: str | None = Field(
        default=None,
        pattern=_CONTAINER_PATTERN,
    )
    gpu_uuid: str | None = Field(
        default=None,
        pattern=_GPU_UUID_PATTERN,
    )

    @model_validator(mode="after")
    def _decision_is_closed(self) -> ProductionRuntimeDecision:
        claims = (
            self.execution_environment_sha256,
            self.container_identity,
            self.gpu_uuid,
        )
        if self.status == "accepted":
            if self.failure_codes or any(value is None for value in claims):
                raise ValueError(
                    "accepted runtime decision requires complete claims"
                )
        elif not self.failure_codes or any(
            value is not None for value in claims
        ):
            raise ValueError(
                "rejected runtime decision cannot carry output claims"
            )
        expected = _content_sha(self)
        if self.content_sha256 != expected:
            raise ValueError("runtime decision content SHA disagrees")
        if self.decision_id != f"production-runtime-decision-{expected}":
            raise ValueError("runtime decision id disagrees")
        return self


def execution_environment_sha256(
    environment: ExecutionEnvironmentObservation,
) -> str:
    return hashlib.sha256(
        _canonical_json_bytes(
            environment.model_dump(mode="json", by_alias=True)
        )
    ).hexdigest()


def _policy_failures(
    measurement: ProductionRuntimeMeasurement,
    policy: ProductionRuntimePolicy,
) -> tuple[RuntimeFailureCode, ...]:
    failures: list[RuntimeFailureCode] = []
    executable_sha = {
        item.role: item.before.sha256 for item in measurement.executables
    }
    checks = (
        (
            measurement.exact_commit != policy.expected_exact_commit,
            "commit-identity-mismatch",
        ),
        (
            measurement.remote_target_sha256
            != policy.expected_remote_target_sha256,
            "remote-target-mismatch",
        ),
        (
            measurement.probe_set_sha256
            != policy.expected_probe_set_sha256,
            "probe-set-mismatch",
        ),
        (
            measurement.environment.configured_container_identity
            != policy.expected_container_identity
            or measurement.environment.observed_container_identity
            != policy.expected_container_identity,
            "container-identity-mismatch",
        ),
        (
            measurement.gpu.uuid != policy.expected_gpu_uuid,
            "gpu-identity-mismatch",
        ),
        (
            measurement.gpu.memory_total_mib
            < policy.min_gpu_memory_mib,
            "gpu-memory-insufficient",
        ),
        (
            measurement.gpu.cuda_runtime_version
            != policy.expected_cuda_runtime_version,
            "cuda-runtime-mismatch",
        ),
        (
            measurement.training_cli.python_version
            != policy.expected_python_version,
            "python-version-mismatch",
        ),
        (
            measurement.training_cli.nerfstudio_version
            != policy.expected_nerfstudio_version,
            "nerfstudio-version-mismatch",
        ),
        (
            measurement.training_cli.schema_sha256
            != policy.expected_training_cli_schema_sha256,
            "training-cli-schema-mismatch",
        ),
        (
            not set(policy.required_training_cli_options)
            <= set(measurement.training_cli.observed_options),
            "training-cli-options-missing",
        ),
        (
            executable_sha["checker"]
            != policy.expected_checker_sha256,
            "checker-identity-mismatch",
        ),
        (
            executable_sha["container-runtime"]
            != policy.expected_container_runtime_sha256,
            "container-runtime-identity-mismatch",
        ),
        (
            executable_sha["nvidia-smi"]
            != policy.expected_nvidia_smi_sha256,
            "nvidia-smi-identity-mismatch",
        ),
        (
            executable_sha["python"]
            != policy.expected_python_sha256,
            "python-identity-mismatch",
        ),
        (
            executable_sha["ns-train"]
            != policy.expected_training_cli_sha256,
            "training-cli-identity-mismatch",
        ),
        (
            executable_sha["worker"] != policy.expected_worker_sha256,
            "worker-identity-mismatch",
        ),
    )
    for failed, code in checks:
        if failed:
            failures.append(code)  # type: ignore[arg-type]
    return tuple(failures)


def decide_production_runtime(
    measurement: ProductionRuntimeMeasurement,
    policy: ProductionRuntimePolicy,
) -> ProductionRuntimeDecision:
    failures = _policy_failures(measurement, policy)
    accepted = not failures
    fields = {
        "runtime_measurement_sha256": measurement.content_sha256,
        "policy_sha256": policy.content_sha256,
        "status": "accepted" if accepted else "rejected",
        "failure_codes": failures,
        "execution_environment_sha256": (
            execution_environment_sha256(measurement.environment)
            if accepted
            else None
        ),
        "container_identity": (
            measurement.environment.observed_container_identity
            if accepted
            else None
        ),
        "gpu_uuid": measurement.gpu.uuid if accepted else None,
    }
    zero = "0" * 64
    provisional = ProductionRuntimeDecision.model_construct(
        decision_id=f"production-runtime-decision-{zero}",
        content_sha256=zero,
        **fields,
    )
    digest = _content_sha(provisional)
    return ProductionRuntimeDecision(
        decision_id=f"production-runtime-decision-{digest}",
        content_sha256=digest,
        **fields,
    )


def verify_production_runtime_decision(
    *,
    measurement: ProductionRuntimeMeasurement,
    policy: ProductionRuntimePolicy,
    decision: ProductionRuntimeDecision,
) -> None:
    expected = decide_production_runtime(measurement, policy)
    if expected != decision:
        raise ProductionRuntimeEvidenceError(
            "production runtime decision disagrees with evidence"
        )


def canonical_production_runtime_measurement_bytes(
    measurement: ProductionRuntimeMeasurement,
) -> bytes:
    return _canonical_json_bytes(
        measurement.model_dump(mode="json", by_alias=True)
    )


def canonical_production_runtime_policy_bytes(
    policy: ProductionRuntimePolicy,
) -> bytes:
    return _canonical_json_bytes(
        policy.model_dump(mode="json", by_alias=True)
    )


def canonical_production_runtime_decision_bytes(
    decision: ProductionRuntimeDecision,
) -> bytes:
    return _canonical_json_bytes(
        decision.model_dump(mode="json", by_alias=True)
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProductionRuntimeEvidenceError(
                "production runtime evidence has duplicate keys"
            )
        result[key] = value
    return result


def _load_canonical(payload: bytes, model_type, canonicalizer):
    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        model = model_type.model_validate_json(payload)
    except ProductionRuntimeEvidenceError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ProductionRuntimeEvidenceError(
            "production runtime evidence is invalid"
        ) from exc
    if payload != canonicalizer(model):
        raise ProductionRuntimeEvidenceError(
            "production runtime evidence is not canonical"
        )
    return model


def load_production_runtime_measurement_bytes(
    payload: bytes,
) -> ProductionRuntimeMeasurement:
    return _load_canonical(
        payload,
        ProductionRuntimeMeasurement,
        canonical_production_runtime_measurement_bytes,
    )


def load_production_runtime_policy_bytes(
    payload: bytes,
) -> ProductionRuntimePolicy:
    return _load_canonical(
        payload,
        ProductionRuntimePolicy,
        canonical_production_runtime_policy_bytes,
    )


def load_production_runtime_decision_bytes(
    payload: bytes,
) -> ProductionRuntimeDecision:
    return _load_canonical(
        payload,
        ProductionRuntimeDecision,
        canonical_production_runtime_decision_bytes,
    )
