"""Strict SSH transport for production Nerfstudio Splatfacto jobs.

The local process always invokes ``ssh``/``scp`` with argv arrays and
``shell=False``. Remote commands target a pinned repository helper and contain
only validated, shell-quoted values. Network ambiguity is represented as
``unknown``; success is emitted only after the downloaded result archive and
its training provenance have both been revalidated locally.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

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
from pipeline.real_scene_training import (
    HeldOutSplit,
    RealSceneTrainingError,
    VerifiedTrainingJobBundle,
    held_out_split_canonical_bytes,
    load_training_job_input_bytes,
    verify_production_training_job_bundle,
)
from pipeline.render_evaluation import (
    RenderEvaluationError,
    RenderEvaluationPolicy,
    RenderEvaluationReport,
    canonical_render_evaluation_bytes,
    validate_render_evaluation,
)
from pipeline.training_executor import (
    ExecutorAttemptReceipt,
    ExecutorInputIdentity,
    ExecutorJobBundle,
    ExecutorJobRef,
    ExecutorObservation,
    advance_attempt,
    new_attempt,
    normalize_poll_result,
)
from pipeline.training_provenance import (
    TrainingRequest,
    TrainingResult,
    request_canonical_sha256,
    validate_training_provenance,
)


class RemoteShellExecutionError(ValueError):
    """Remote execution evidence is unsafe, incomplete, or ambiguous."""


class RemoteShellTransportError(RemoteShellExecutionError):
    """A bounded transport invocation could not produce an observation."""


class RemoteResultBundleError(ValueError):
    """A result archive failed closure or durable publication.

    ``published`` is ``None`` for validation/read failures, ``False`` when a
    durable writer proves the destination was not published, and ``True``
    when the namespace change happened but durability could not be confirmed.
    Callers must not infer this state from the human-readable message.
    """

    def __init__(
        self,
        message: str,
        *,
        published: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.published = published


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_FINGERPRINT_PATTERN = r"^SHA256:[A-Za-z0-9+/]{43}$"
_CONTAINER_PATTERN = (
    r"^[A-Za-z0-9._/:+-]+@sha256:[0-9a-f]{64}$"
)
PreflightFailureCode = Literal[
    "remote-probe-required",
    "remote-probe-not-executed",
    "ssh-binary-missing",
    "ssh-binary-invalid",
    "scp-binary-missing",
    "scp-binary-invalid",
    "private-key-missing",
    "private-key-uninspectable",
    "private-key-invalid",
    "known-hosts-missing",
    "known-hosts-invalid",
    "local-transport-drift",
    "remote-unreachable",
    "remote-runtime-invalid",
    "remote-worker-invalid",
    "remote-runtime-and-worker-invalid",
    "remote-checker-invalid",
    "remote-checker-config-mismatch",
    "remote-runtime-mismatch",
    "remote-container-mismatch",
    "remote-worker-mismatch",
]
_PREFLIGHT_FAILURE_REASONS: dict[PreflightFailureCode, str] = {
    "remote-probe-required": "remote probe is required for readiness",
    "remote-probe-not-executed": (
        "remote probe was requested but not executed"
    ),
    "ssh-binary-missing": "ssh binary is missing",
    "ssh-binary-invalid": "ssh binary is invalid",
    "scp-binary-missing": "scp binary is missing",
    "scp-binary-invalid": "scp binary is invalid",
    "private-key-missing": "SSH private key is missing",
    "private-key-uninspectable": "SSH private key cannot be inspected",
    "private-key-invalid": (
        "SSH private key permissions are too broad or invalid"
    ),
    "known-hosts-missing": "known-hosts file is missing",
    "known-hosts-invalid": (
        "known-hosts fingerprint or file verification failed"
    ),
    "local-transport-drift": (
        "local transport changed during remote probe"
    ),
    "remote-unreachable": "remote probe could not reach the target",
    "remote-runtime-invalid": "container runtime did not respond",
    "remote-worker-invalid": "worker binary not found on remote",
    "remote-runtime-and-worker-invalid": (
        "container runtime did not respond; "
        "worker binary not found on remote"
    ),
    "remote-checker-invalid": (
        "remote readiness checker response is invalid"
    ),
    "remote-checker-config-mismatch": (
        "remote readiness checker config does not match"
    ),
    "remote-runtime-mismatch": (
        "remote container runtime identity does not match"
    ),
    "remote-container-mismatch": (
        "remote container image digest does not match"
    ),
    "remote-worker-mismatch": (
        "remote worker identity does not match"
    ),
}
_MAX_STATUS_BYTES = 64 * 1024
_MAX_LIFECYCLE_BYTES = 64 * 1024
_DEFAULT_MAX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
_DEFAULT_MAX_MEMBER_BYTES = 12 * 1024 * 1024 * 1024
_DEFAULT_MAX_LOG_BYTES = 64 * 1024 * 1024
_BOUND_LOG_MEMBERS = frozenset(
    {"training.log", "worker.stdout.log", "worker.stderr.log"}
)
_BASE_RESULT_MEMBERS = frozenset(
    {
        "container-identity.txt",
        "dataparser_transforms.json",
        "operator-intent-config.yml",
        "point_cloud.ply",
        "training-request.json",
        "training-result.json",
        "training.log",
        "worker.stderr.log",
        "worker.stdout.log",
    }
)
_EVALUATION_FIXED_MEMBERS = frozenset(
    {
        "render-evaluation/policy.json",
        "render-evaluation/report.json",
        "render-evaluation/trainer-config.yml",
        "render-evaluation/transforms.json",
    }
)
_EVALUATION_DIRECTORIES = frozenset(
    {
        "render-evaluation",
        "render-evaluation/cameras",
        "render-evaluation/renders",
    }
)
_EVALUATION_PAYLOAD_PATTERN = re.compile(
    r"^render-evaluation/(cameras|renders)/([0-9a-f]{64})"
    r"\.(json|png)$"
)


def _require_utc(value: datetime) -> datetime:
    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def _no_controls(value: str, *, label: str) -> str:
    if not value or any(ord(character) < 32 or ord(character) == 127
                        for character in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _safe_remote_root(value: str, *, label: str) -> str:
    _no_controls(value, label=label)
    parsed = PurePosixPath(value)
    if (
        not parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or any(
            not all(
                character.isalnum() or character in "._-"
                for character in part
            )
            for part in parsed.parts[1:]
        )
    ):
        raise ValueError(
            f"{label} must be an absolute portable POSIX path"
        )
    return value


class RemoteShellExecutorConfig(FrozenModel):
    ssh_binary: Path
    scp_binary: Path
    private_key_path: Path
    known_hosts_path: Path
    expected_host_key_fingerprint: str = Field(
        pattern=_FINGERPRINT_PATTERN,
    )
    ssh_target: str
    known_host: str
    port: int = Field(default=22, ge=1, le=65535)
    remote_root: str
    remote_repo_root: str
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    container_runtime: Literal["docker", "podman"] = "docker"
    expected_worker_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_worker_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    expected_checker_config_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    connect_timeout_seconds: int = Field(default=20, ge=1, le=300)
    command_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_command_output_bytes: int = Field(
        default=1024 * 1024,
        ge=1024,
        le=64 * 1024 * 1024,
    )
    max_result_bundle_bytes: int = Field(
        default=_DEFAULT_MAX_ARCHIVE_BYTES,
        ge=1024,
    )
    max_result_member_bytes: int = Field(
        default=_DEFAULT_MAX_MEMBER_BYTES,
        ge=1024,
    )
    max_log_bytes: int = Field(
        default=_DEFAULT_MAX_LOG_BYTES,
        ge=1024,
    )

    @model_validator(mode="after")
    def _validate_transport_values(
        self,
    ) -> RemoteShellExecutorConfig:
        _no_controls(self.ssh_target, label="ssh_target")
        _no_controls(self.known_host, label="known_host")
        if (
            self.ssh_target.startswith("-")
            or self.known_host.startswith("-")
            or not all(
                character.isalnum() or character in "._@+-"
                for character in self.ssh_target
            )
            or not all(
                character.isalnum() or character in ".-"
                for character in self.known_host
            )
        ):
            raise ValueError("SSH target/known host is not a safe alias")
        _safe_remote_root(self.remote_root, label="remote_root")
        _safe_remote_root(
            self.remote_repo_root,
            label="remote_repo_root",
        )
        local_paths = (
            self.ssh_binary,
            self.scp_binary,
            self.private_key_path,
            self.known_hosts_path,
        )
        if any(not path.is_absolute() for path in local_paths):
            raise ValueError("remote executor local paths must be absolute")
        if self.max_log_bytes > self.max_result_member_bytes:
            raise ValueError("max_log_bytes cannot exceed member limit")
        return self


class RemoteShellStatus(FrozenModel):
    schema_id: Literal["nantai.remote-shell-status.v1"] = Field(
        default="nantai.remote-shell-status.v1",
        alias="schema",
        serialization_alias="schema",
    )
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    state: Literal["running", "succeeded", "failed"]
    updated_at_utc: datetime
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
    result_bundle_size_bytes: int | None = Field(default=None, ge=1)

    _utc = field_validator("updated_at_utc")(_require_utc)

    @model_validator(mode="after")
    def _state_evidence_is_consistent(self) -> RemoteShellStatus:
        log_hashes = (self.stdout_sha256, self.stderr_sha256)
        result_fields = (
            self.result_bundle_sha256,
            self.result_bundle_size_bytes,
        )
        if self.state == "running":
            if (
                self.exit_code is not None
                or any(value is not None for value in log_hashes)
                or any(value is not None for value in result_fields)
            ):
                raise ValueError("running remote status has terminal evidence")
        elif self.state == "succeeded":
            if (
                self.exit_code != 0
                or any(value is None for value in log_hashes)
                or any(value is None for value in result_fields)
            ):
                raise ValueError(
                    "succeeded remote status requires logs and result bundle"
                )
        elif (
            self.exit_code is None
            or self.exit_code == 0
            or any(value is None for value in log_hashes)
            or any(value is not None for value in result_fields)
        ):
            raise ValueError(
                "failed remote status requires nonzero exit and log hashes"
            )
        return self


class RemoteShellPreflightReport(FrozenModel):
    """Canonical machine report for credential-free remote-shell preflight.

    Binds the remote target identity (container digest, host key fingerprint,
    known_host, port, remote roots, container runtime) so a report cannot be
    replayed for a different config. ``status`` is the only outcome Literal:
    ``ready`` means every local and remote check passed; ``blocked-external-input``
    means a required external input (config, credentials, binary) is missing;
    ``failed`` means a checked item is misconfigured or broken. The report
    never carries connection secrets, private key paths, config JSON contents
    or unfiltered stderr.
    """

    schema_id: Literal["nantai.remote-shell-preflight.v1"] = Field(
        default="nantai.remote-shell-preflight.v1",
        alias="schema",
        serialization_alias="schema",
    )
    status: Literal["ready", "blocked-external-input", "failed"]
    checked_at_utc: datetime

    report_id: str = Field(
        pattern=r"^remote-preflight-[0-9a-f]{64}$",
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    ssh_binary_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    scp_binary_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    private_key_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    known_hosts_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )

    # Identity binding — proves this report is for one specific remote target.
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    expected_host_key_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    known_host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    remote_root: str = Field(min_length=1)
    remote_repo_root: str = Field(min_length=1)
    container_runtime: Literal["docker", "podman"] = "docker"

    # Local transport check results (no secrets, no private key paths).
    ssh_binary_found: bool = False
    scp_binary_found: bool = False
    private_key_protection_verified: bool = False
    known_hosts_verified: bool = False

    # Remote read-only capability check results (None when not probed).
    checker_version: (
        Literal["nantai.remote-readiness-checker.v1"] | None
    ) = None
    expected_checker_config_sha256: str = Field(
        pattern=_SHA256_PATTERN,
    )
    checker_config_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    container_runtime_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    measured_container_identity: str | None = Field(
        default=None,
        pattern=_CONTAINER_PATTERN,
    )
    container_runtime_verified: bool | None = None
    container_image_verified: bool | None = None
    worker_binary_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    worker_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    worker_binary_verified: bool | None = None

    failure_code: PreflightFailureCode | None = None
    failure_reason: str | None = None

    _utc = field_validator("checked_at_utc")(_require_utc)

    @field_validator("container_runtime_version")
    @classmethod
    def _safe_runtime_version(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None:
            _no_controls(value, label="container runtime version")
        return value

    @model_validator(mode="after")
    def _status_evidence_is_consistent(self) -> RemoteShellPreflightReport:
        if self.status == "ready":
            if (
                self.failure_code is not None
                or self.failure_reason is not None
            ):
                raise ValueError(
                    "ready preflight report must not carry failure evidence"
                )
            if not (
                self.ssh_binary_found
                and self.scp_binary_found
                and self.private_key_protection_verified
                and self.known_hosts_verified
                and self.ssh_binary_sha256 is not None
                and self.scp_binary_sha256 is not None
                and self.private_key_sha256 is not None
                and self.known_hosts_sha256 is not None
                and self.checker_version
                == "nantai.remote-readiness-checker.v1"
                and self.checker_config_sha256
                == self.expected_checker_config_sha256
                and self.container_runtime_version is not None
                and self.measured_container_identity
                == self.container_identity
                and self.container_runtime_verified is True
                and self.container_image_verified is True
                and self.worker_binary_sha256 is not None
                and self.worker_version is not None
                and self.worker_binary_verified is True
            ):
                raise ValueError(
                    "ready preflight report requires all checks to pass"
                )
        else:
            if (
                self.failure_code is None
                or self.failure_reason
                != _PREFLIGHT_FAILURE_REASONS[self.failure_code]
            ):
                raise ValueError(
                    f"{self.status} preflight report requires fixed failure"
                    " evidence"
                )
            if (
                self.failure_reason is None
                or self.failure_reason.strip() == ""
            ):
                raise ValueError(
                    f"{self.status} preflight report requires a"
                    " non-empty failure_reason"
                )
        expected_content = remote_shell_preflight_content_sha256(self)
        if self.content_sha256 != expected_content:
            raise ValueError(
                "preflight report content_sha256 disagrees"
            )
        if self.report_id != f"remote-preflight-{expected_content}":
            raise ValueError("preflight report_id disagrees")
        return self


class RemoteReadinessEvidence(FrozenModel):
    schema_id: Literal["nantai.remote-readiness-evidence.v1"] = Field(
        default="nantai.remote-readiness-evidence.v1",
        alias="schema",
        serialization_alias="schema",
    )
    checker_version: Literal["nantai.remote-readiness-checker.v1"]
    checker_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    container_runtime: Literal["docker", "podman"]
    container_runtime_version: str = Field(
        min_length=1,
        max_length=256,
    )
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    worker_sha256: str = Field(pattern=_SHA256_PATTERN)
    worker_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )

    @field_validator("container_runtime_version")
    @classmethod
    def _safe_runtime_version(cls, value: str) -> str:
        return _no_controls(
            value,
            label="container runtime version",
        )


class RemoteResultBundleMember(FrozenModel):
    path: str = Field(min_length=1)
    byte_length: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class RemoteResultBundleManifest(FrozenModel):
    schema_id: Literal["nantai.remote-result-bundle.v1"] = Field(
        default="nantai.remote-result-bundle.v1",
        alias="schema",
        serialization_alias="schema",
    )
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    members: tuple[RemoteResultBundleMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _members_are_exact(self) -> RemoteResultBundleManifest:
        paths = tuple(member.path for member in self.members)
        if paths != tuple(sorted(paths)):
            raise ValueError("remote result members must be path sorted")
        if len(set(paths)) != len(paths):
            raise ValueError("remote result member paths must be unique")
        path_set = set(paths)
        if path_set == _BASE_RESULT_MEMBERS:
            return self
        if not (
            _BASE_RESULT_MEMBERS <= path_set
            and _EVALUATION_FIXED_MEMBERS <= path_set
        ):
            raise ValueError(
                "remote successful result member set is incomplete"
            )
        camera_stems: set[str] = set()
        render_stems: set[str] = set()
        for path in path_set - _BASE_RESULT_MEMBERS - _EVALUATION_FIXED_MEMBERS:
            match = _EVALUATION_PAYLOAD_PATTERN.fullmatch(path)
            if match is None:
                raise ValueError(
                    "remote evaluation member set contains an extra path"
                )
            kind, stem, extension = match.groups()
            if (
                (kind == "cameras" and extension != "json")
                or (kind == "renders" and extension != "png")
            ):
                raise ValueError(
                    "remote evaluation member extension is invalid"
                )
            target = camera_stems if kind == "cameras" else render_stems
            target.add(stem)
        if not camera_stems or camera_stems != render_stems:
            raise ValueError(
                "remote evaluation camera/render members differ"
            )
        return self


class RemoteShellJobRef(ExecutorJobRef):
    executor_kind: Literal["remote-shell-nerfstudio"] = (
        "remote-shell-nerfstudio"
    )
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    remote_job_path: str

    @field_validator("remote_job_path")
    @classmethod
    def _remote_path(cls, value: str) -> str:
        return _safe_remote_root(value, label="remote_job_path")


class RemoteContainerLifecycleReceipt(FrozenModel):
    """Canonical, content-addressed receipt of fresh-container creation.

    Published by the worker after ``container-id.txt`` is durably written
    AND the runtime image inspect confirms the container ``.Image`` equals
    the resolved image ID and ``.Config.Image`` equals the immutable
    ``repo@sha256:...`` identity.  The receipt binds job/attempt/workspace
    identity SHA, immutable image digest and full container ID to a single
    durable transition; it never carries caller-reported GPU/CUDA/
    Nerfstudio pass observations — those belong to F1/G2
    ``production_runtime_evidence`` measured inside the container.
    ``receipt_sha256`` is the content-address of the full canonical bytes
    and must be recomputed by the loader; callers cannot self-report it.
    """

    schema_id: Literal["nantai.remote-container-lifecycle.v1"] = Field(
        default="nantai.remote-container-lifecycle.v1",
        alias="schema",
        serialization_alias="schema",
    )
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    workspace_identity_sha256: str = Field(pattern=_SHA256_PATTERN)
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    transition: Literal["container-created-identity-verified"]
    receipt_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _receipt_sha_is_content_addressed(
        self,
    ) -> RemoteContainerLifecycleReceipt:
        expected = compute_container_lifecycle_sha256(self)
        if self.receipt_sha256 != expected:
            raise ValueError(
                "container lifecycle receipt_sha256 is not content-addressed"
            )
        return self


@dataclass(frozen=True)
class VerifiedRemoteResultBundle:
    path: Path
    bundle_sha256: str
    byte_length: int
    manifest: RemoteResultBundleManifest
    member_bytes: dict[str, bytes]


@dataclass
class _JobContext:
    job: RemoteShellJobRef
    bundle: VerifiedTrainingJobBundle
    receipt: ExecutorAttemptReceipt
    last_status: RemoteShellStatus | None = None
    bound_container_id: str | None = None


def _canonical_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def canonical_remote_status_bytes(status: RemoteShellStatus) -> bytes:
    return _canonical_model_bytes(status)


def canonical_remote_result_manifest_bytes(
    manifest: RemoteResultBundleManifest,
) -> bytes:
    return _canonical_model_bytes(manifest)


def canonical_remote_shell_preflight_bytes(
    report: RemoteShellPreflightReport,
) -> bytes:
    return _canonical_model_bytes(report)


def canonical_remote_shell_job_ref_bytes(
    job: RemoteShellJobRef,
) -> bytes:
    return _canonical_model_bytes(job)


def compute_workspace_identity_sha256(
    *,
    job_id: str,
    attempt_id: str,
    workspace_path: str,
) -> str:
    """Compute the workspace identity SHA-256.

    Both caller and worker independently recompute this from the absolute
    POSIX workspace path.  The SHA binds the workspace path without
    exposing the private remote path in receipts or logs.
    """
    payload = (
        json.dumps(
            {
                "attempt_id": attempt_id,
                "job_id": job_id,
                "workspace": workspace_path,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def canonical_container_lifecycle_bytes(
    receipt: RemoteContainerLifecycleReceipt,
) -> bytes:
    return _canonical_model_bytes(receipt)


def container_lifecycle_signing_bytes(
    receipt: RemoteContainerLifecycleReceipt,
) -> bytes:
    """Canonical bytes used to compute ``receipt_sha256`` (excludes the sha)."""
    payload = receipt.model_dump(
        mode="json",
        by_alias=True,
        exclude={"receipt_sha256"},
    )
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def compute_container_lifecycle_sha256(
    receipt: RemoteContainerLifecycleReceipt,
) -> str:
    """Content-addressed SHA-256 of the receipt's signing bytes."""
    return hashlib.sha256(
        container_lifecycle_signing_bytes(receipt)
    ).hexdigest()


def load_container_lifecycle_receipt(
    raw: bytes | bytearray | str,
) -> RemoteContainerLifecycleReceipt:
    """Load and validate a lifecycle receipt from raw canonical bytes.

    Performs fail-closed checks: ASCII-only, no duplicate keys, Pydantic
    schema validation, canonical bytes strict equality, and SHA-256
    round-trip.  The caller cannot self-report the SHA; it is derived from
    the signing bytes and verified against the receipt's field.
    """
    if isinstance(raw, str):
        try:
            raw_bytes = raw.encode("ascii")
        except UnicodeError as exc:
            raise RemoteShellExecutionError(
                "container lifecycle receipt must be ASCII"
            ) from exc
    elif isinstance(raw, (bytes, bytearray)):
        raw_bytes = bytes(raw)
    else:
        raise RemoteShellExecutionError(
            "container lifecycle receipt must be bytes or str"
        )
    try:
        text = raw_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RemoteShellExecutionError(
            "container lifecycle receipt must be ASCII"
        ) from exc
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc:
        raise RemoteShellExecutionError(
            "container lifecycle receipt is not valid JSON"
        ) from exc
    except ValueError as exc:
        raise RemoteShellExecutionError(
            "container lifecycle receipt has duplicate keys"
        ) from exc
    if not isinstance(parsed, dict):
        raise RemoteShellExecutionError(
            "container lifecycle receipt must be a JSON object"
        )
    try:
        receipt = RemoteContainerLifecycleReceipt.model_validate_json(
            raw_bytes
        )
    except ValueError as exc:
        raise RemoteShellExecutionError(
            "container lifecycle receipt validation failed"
        ) from exc
    expected_sha = compute_container_lifecycle_sha256(receipt)
    if receipt.receipt_sha256 != expected_sha:
        raise RemoteShellExecutionError(
            "container lifecycle receipt sha256 does not match signing bytes"
        )
    if raw_bytes != canonical_container_lifecycle_bytes(receipt):
        raise RemoteShellExecutionError(
            "container lifecycle receipt is not canonical JSON"
        )
    return receipt


def remote_shell_executor_config_sha256(
    config: RemoteShellExecutorConfig,
) -> str:
    return hashlib.sha256(_canonical_model_bytes(config)).hexdigest()


def remote_shell_preflight_content_sha256(
    report: RemoteShellPreflightReport,
) -> str:
    payload = report.model_dump(
        mode="json",
        by_alias=True,
        exclude={"report_id", "content_sha256"},
    )
    canonical = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _build_remote_shell_preflight_report(
    **fields,
) -> RemoteShellPreflightReport:
    zero = "0" * 64
    provisional = RemoteShellPreflightReport.model_construct(
        report_id=f"remote-preflight-{zero}",
        content_sha256=zero,
        **fields,
    )
    digest = remote_shell_preflight_content_sha256(provisional)
    return RemoteShellPreflightReport(
        report_id=f"remote-preflight-{digest}",
        content_sha256=digest,
        **fields,
    )


def _publish_remote_private_record(
    payload: bytes,
    output: Path,
    *,
    label: str,
) -> Path:
    destination = Path(output)
    if not destination.is_absolute():
        raise RemoteShellExecutionError(
            f"{label} output path must be absolute"
        )
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        parent_stat = parent.lstat()
    except OSError as exc:
        raise RemoteShellExecutionError(
            f"{label} cannot be published"
        ) from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise RemoteShellExecutionError(
            f"{label} cannot be published"
        )
    if destination.exists() or destination.is_symlink():
        raise RemoteShellExecutionError(
            f"{label} cannot replace an existing path"
        )

    from pipeline.durable_io import (
        DurableIOError,
        flush_file,
        publish_file_noreplace,
    )

    descriptor = -1
    staging = ""
    try:
        descriptor, staging = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".staging",
            dir=parent,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
        flush_file(staging)
        publish_file_noreplace(staging, destination)
        staging = ""
    except DurableIOError as exc:
        state = (
            "published but durability is unconfirmed"
            if exc.published
            else "not published"
        )
        raise RemoteShellExecutionError(
            f"{label} cannot be published ({state})"
        ) from exc
    except OSError as exc:
        raise RemoteShellExecutionError(
            f"{label} cannot be published"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if staging:
            try:
                Path(staging).unlink(missing_ok=True)
            except OSError:
                pass
    return destination


def publish_remote_shell_preflight(
    report: RemoteShellPreflightReport,
    output: Path,
) -> Path:
    """Durably publish one immutable preflight report without replacement."""

    return _publish_remote_private_record(
        canonical_remote_shell_preflight_bytes(report),
        output,
        label="remote preflight report",
    )


def publish_remote_shell_job_ref(
    job: RemoteShellJobRef,
    output: Path,
) -> Path:
    """Durably publish one immutable private remote-job reference."""

    return _publish_remote_private_record(
        canonical_remote_shell_job_ref_bytes(job),
        output,
        label="remote job reference",
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_remote_shell_executor_config(
    path: str | Path,
) -> RemoteShellExecutorConfig:
    config_path = Path(path)
    if not config_path.is_absolute():
        raise RemoteShellExecutionError(
            "remote executor config path must be absolute"
        )
    try:
        before = config_path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(
            before.st_mode
        ):
            raise RemoteShellExecutionError(
                "remote executor config must be a regular file"
            )
        if before.st_size <= 0 or before.st_size > 1024 * 1024:
            raise RemoteShellExecutionError(
                "remote executor config size is invalid"
            )
        payload = config_path.read_bytes()
        after = config_path.lstat()
    except RemoteShellExecutionError:
        raise
    except OSError as exc:
        raise RemoteShellExecutionError(
            "remote executor config cannot be read"
        ) from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(payload) != before.st_size
    ):
        raise RemoteShellExecutionError(
            "remote executor config changed while read"
        )
    try:
        raw = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        if not isinstance(raw, dict):
            raise ValueError("remote executor config must be an object")
        config = RemoteShellExecutorConfig.model_validate_json(payload)
    except (UnicodeError, ValueError) as exc:
        raise RemoteShellExecutionError(
            "remote executor config is invalid or has duplicate keys"
        ) from exc
    if payload != _canonical_model_bytes(config):
        raise RemoteShellExecutionError(
            "remote executor config is not canonical"
        )
    return config


def load_remote_shell_job_ref(
    path: str | Path,
) -> RemoteShellJobRef:
    job_path = Path(path)
    if not job_path.is_absolute():
        raise RemoteShellExecutionError(
            "remote job reference path must be absolute"
        )
    try:
        before = job_path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(
            before.st_mode
        ):
            raise RemoteShellExecutionError(
                "remote job reference must be a regular file"
            )
        if before.st_size <= 0 or before.st_size > _MAX_STATUS_BYTES:
            raise RemoteShellExecutionError(
                "remote job reference size is invalid"
            )
        payload = job_path.read_bytes()
        after = job_path.lstat()
    except RemoteShellExecutionError:
        raise
    except OSError as exc:
        raise RemoteShellExecutionError(
            "remote job reference cannot be read"
        ) from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(payload) != before.st_size
    ):
        raise RemoteShellExecutionError(
            "remote job reference changed while read"
        )
    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        job = RemoteShellJobRef.model_validate_json(payload)
    except (UnicodeError, ValueError) as exc:
        raise RemoteShellExecutionError(
            "remote job reference is invalid or has duplicate keys"
        ) from exc
    if payload != canonical_remote_shell_job_ref_bytes(job):
        raise RemoteShellExecutionError(
            "remote job reference is not canonical"
        )
    return job


def _portable_member(value: str) -> PurePosixPath:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise RemoteResultBundleError(
            "result archive member is not a portable relative path"
        )
    return parsed


def _enumerate_result_tree(
    root: Path,
) -> tuple[tuple[Path, ...], frozenset[str]]:
    files: list[Path] = []
    directories: set[str] = set()
    try:
        for current, directory_names, file_names in os.walk(
            root,
            followlinks=False,
        ):
            parent = Path(current)
            for name in directory_names:
                candidate = parent / name
                mode = candidate.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise RemoteResultBundleError(
                        "remote result tree contains a link-like directory"
                    )
                directories.add(
                    candidate.relative_to(root).as_posix()
                )
            for name in file_names:
                candidate = parent / name
                mode = candidate.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise RemoteResultBundleError(
                        "remote result tree contains a non-regular member"
                    )
                files.append(candidate)
    except RemoteResultBundleError:
        raise
    except OSError as exc:
        raise RemoteResultBundleError(
            "remote result root cannot be enumerated"
        ) from exc
    return (
        tuple(
            sorted(
                files,
                key=lambda path: path.relative_to(root).as_posix(),
            )
        ),
        frozenset(directories),
    )


def _validate_evaluation_member_contract(
    bindings: dict[str, RemoteResultBundleMember],
    payloads: dict[str, bytes],
    *,
    container_identity: str,
) -> None:
    paths = set(bindings)
    has_evaluation = bool(paths & _EVALUATION_FIXED_MEMBERS)
    if not has_evaluation:
        if paths != _BASE_RESULT_MEMBERS:
            raise RemoteResultBundleError(
                "remote result member set is incomplete"
            )
        return
    try:
        policy_bytes = payloads["render-evaluation/policy.json"]
        report_bytes = payloads["render-evaluation/report.json"]
        policy = RenderEvaluationPolicy.model_validate_json(policy_bytes)
        report = RenderEvaluationReport.model_validate_json(report_bytes)
    except (KeyError, ValueError) as exc:
        raise RemoteResultBundleError(
            "remote evaluation policy/report is invalid"
        ) from exc
    if (
        policy_bytes != canonical_render_evaluation_bytes(policy)
        or report_bytes != canonical_render_evaluation_bytes(report)
    ):
        raise RemoteResultBundleError(
            "remote evaluation policy/report is not canonical"
        )
    if (
        report.policy_sha256
        != hashlib.sha256(policy_bytes).hexdigest()
        or report.held_out_split_sha256
        != policy.held_out_split_sha256
        or report.evaluator_container_digest != container_identity
        or policy.evaluator_container_digest != container_identity
        or report.protocol != policy.protocol
    ):
        raise RemoteResultBundleError(
            "remote evaluation identity differs from result bundle"
        )
    trainer = bindings.get(
        "render-evaluation/trainer-config.yml"
    )
    transforms = bindings.get("render-evaluation/transforms.json")
    if (
        trainer is None
        or transforms is None
        or trainer.sha256 != report.trainer_config_sha256
        or transforms.sha256 != policy.transforms_sha256
    ):
        raise RemoteResultBundleError(
            "remote evaluation config/transforms binding mismatch"
        )
    expected = set(_BASE_RESULT_MEMBERS | _EVALUATION_FIXED_MEMBERS)
    for frame in report.frames:
        if not frame.render_path.startswith("result/"):
            raise RemoteResultBundleError(
                "remote evaluation render path is outside result root"
            )
        if not frame.camera_path.startswith("result/"):
            raise RemoteResultBundleError(
                "remote evaluation camera path is outside result root"
            )
        render_path = frame.render_path.removeprefix("result/")
        camera_path = frame.camera_path.removeprefix("result/")
        render = bindings.get(render_path)
        camera = bindings.get(camera_path)
        if (
            render is None
            or camera is None
            or render.byte_length != frame.render_byte_length
            or render.sha256 != frame.render_sha256
            or camera.byte_length != frame.camera_byte_length
            or camera.sha256 != frame.camera_sha256
        ):
            raise RemoteResultBundleError(
                "remote evaluation frame binding mismatch"
            )
        expected.update((render_path, camera_path))
    if paths != expected:
        raise RemoteResultBundleError(
            "remote evaluation member set differs from report"
        )


def _load_held_out_source_bytes(
    bundle: VerifiedTrainingJobBundle,
    split: HeldOutSplit,
) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(bundle.path, "r") as archive:
            payloads = {
                identity.logical_path: archive.read(
                    f"evaluation/payload/{identity.logical_path}"
                )
                for identity in split.held_out
            }
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise RemoteResultBundleError(
            "held-out source bytes cannot be reopened"
        ) from exc
    for identity in split.held_out:
        payload = payloads[identity.logical_path]
        if hashlib.sha256(payload).hexdigest() != identity.sha256:
            raise RemoteResultBundleError(
                "held-out source sha256 differs from split"
            )
    try:
        rechecked = verify_production_training_job_bundle(bundle.path)
    except RealSceneTrainingError as exc:
        raise RemoteResultBundleError(
            "training bundle changed during evaluation validation"
        ) from exc
    if rechecked.bundle_sha256 != bundle.bundle_sha256:
        raise RemoteResultBundleError(
            "training bundle identity changed during evaluation validation"
        )
    return payloads


def _validate_downloaded_evaluation(
    verified_result: VerifiedRemoteResultBundle,
    verified_input: VerifiedTrainingJobBundle,
    split_bytes: bytes,
) -> None:
    members = verified_result.member_bytes
    if "render-evaluation/report.json" not in members:
        return
    try:
        split = HeldOutSplit.model_validate_json(split_bytes)
    except ValueError as exc:
        raise RemoteResultBundleError(
            "submitted held-out split is invalid"
        ) from exc
    if split_bytes != held_out_split_canonical_bytes(split):
        raise RemoteResultBundleError(
            "submitted held-out split is not canonical"
        )
    held_out_sources = _load_held_out_source_bytes(
        verified_input,
        split,
    )
    try:
        policy = RenderEvaluationPolicy.model_validate_json(
            members["render-evaluation/policy.json"]
        )
        report = RenderEvaluationReport.model_validate_json(
            members["render-evaluation/report.json"]
        )
    except (KeyError, ValueError) as exc:
        raise RemoteResultBundleError(
            "downloaded render evaluation JSON is invalid"
        ) from exc
    with tempfile.TemporaryDirectory(
        prefix="nantai-render-eval-",
    ) as temporary:
        root = Path(temporary) / "run"
        (root / "prepared/evidence").mkdir(parents=True)
        (root / "prepared/images").mkdir()
        (root / "result/render-evaluation").mkdir(parents=True)
        (root / "prepared/evidence/held-out-split.json").write_bytes(
            split_bytes
        )
        (root / "prepared/transforms.json").write_bytes(
            members["render-evaluation/transforms.json"]
        )
        for frame_id, payload in held_out_sources.items():
            path = root / "prepared/images" / frame_id
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        for name, payload in members.items():
            if not name.startswith("render-evaluation/"):
                continue
            path = root / "result" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        try:
            validate_render_evaluation(policy, report, root)
        except RenderEvaluationError as exc:
            raise RemoteResultBundleError(
                f"downloaded render evaluation is invalid: {exc}"
            ) from exc


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _stable_file_sha(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    allow_empty: bool = False,
) -> tuple[int, str]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RemoteResultBundleError(f"{label} is missing or link-like")
        if (
            (before.st_size == 0 and not allow_empty)
            or before.st_size < 0
            or before.st_size > max_bytes
        ):
            raise RemoteResultBundleError(
                f"{label} size is outside the allowed range"
            )
        digest = hashlib.sha256()
        measured = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                measured += len(chunk)
        after = path.lstat()
    except RemoteResultBundleError:
        raise
    except OSError as exc:
        raise RemoteResultBundleError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RemoteResultBundleError(f"{label} changed while being read")
    return measured, digest.hexdigest()


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def build_remote_result_bundle(
    *,
    result_root: Path,
    output_path: Path,
    job_id: str,
    attempt_id: str,
    request_sha256: str,
    training_bundle_sha256: str,
    container_identity: str,
    max_member_bytes: int = _DEFAULT_MAX_MEMBER_BYTES,
    max_log_bytes: int = _DEFAULT_MAX_LOG_BYTES,
) -> VerifiedRemoteResultBundle:
    root = Path(result_root).expanduser().absolute()
    output = Path(output_path).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise RemoteResultBundleError(
            "remote result bundle output must be absent"
        )
    try:
        root_stat = root.lstat()
        parent_stat = output.parent.lstat()
    except OSError as exc:
        raise RemoteResultBundleError(
            "remote result bundle boundary is unavailable"
        ) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(
        root_stat.st_mode
    ):
        raise RemoteResultBundleError(
            "remote result root must be a real directory"
        )
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise RemoteResultBundleError(
            "remote result bundle parent must be a real directory"
        )
    result_files, result_directories = _enumerate_result_tree(root)
    actual_names = {
        path.relative_to(root).as_posix() for path in result_files
    }
    has_evaluation = bool(actual_names & _EVALUATION_FIXED_MEMBERS)
    expected_directories = (
        _EVALUATION_DIRECTORIES if has_evaluation else frozenset()
    )
    if result_directories != expected_directories:
        raise RemoteResultBundleError(
            "remote evaluation directory set is incomplete or contains extras"
        )
    sources: dict[
        str,
        tuple[Path, int, str, tuple[int, int, int, int, int, int]],
    ] = {}
    members: list[RemoteResultBundleMember] = []
    for source in result_files:
        name = source.relative_to(root).as_posix()
        source = root / name
        limit = max_log_bytes if name == "training.log" else max_member_bytes
        allow_empty = name in _BOUND_LOG_MEMBERS
        if allow_empty:
            limit = max_log_bytes
        size, digest = _stable_file_sha(
            source,
            label=f"remote result member {name}",
            max_bytes=limit,
            allow_empty=allow_empty,
        )
        signature = _stat_signature(source.lstat())
        sources[name] = (source, size, digest, signature)
        members.append(
            RemoteResultBundleMember(
                path=name,
                byte_length=size,
                sha256=digest,
            )
        )
    binding_by_path = {member.path: member for member in members}
    contract_payloads: dict[str, bytes] = {}
    for name in _EVALUATION_FIXED_MEMBERS & actual_names:
        source, expected_size, expected_sha, signature = sources[name]
        try:
            payload = source.read_bytes()
            after = source.lstat()
        except OSError as exc:
            raise RemoteResultBundleError(
                "remote evaluation contract cannot be read"
            ) from exc
        if (
            _stat_signature(after) != signature
            or len(payload) != expected_size
            or hashlib.sha256(payload).hexdigest() != expected_sha
        ):
            raise RemoteResultBundleError(
                "remote evaluation contract changed while being read"
            )
        contract_payloads[name] = payload
    _validate_evaluation_member_contract(
        binding_by_path,
        contract_payloads,
        container_identity=container_identity,
    )
    expected_container = (container_identity + "\n").encode("ascii")
    try:
        container_bytes = (
            root / "container-identity.txt"
        ).read_bytes()
    except OSError as exc:
        raise RemoteResultBundleError(
            "container identity cannot be reread"
        ) from exc
    if container_bytes != expected_container:
        raise RemoteResultBundleError(
            "result container identity bytes mismatch"
        )
    manifest = RemoteResultBundleManifest(
        job_id=job_id,
        attempt_id=attempt_id,
        request_sha256=request_sha256,
        training_bundle_sha256=training_bundle_sha256,
        container_identity=container_identity,
        members=tuple(members),
    )
    from pipeline.durable_io import (
        DurableIOError,
        flush_file,
        publish_file_noreplace,
    )

    staging = output.parent / (
        f".{output.name}.{uuid.uuid4().hex}.staging"
    )
    try:
        with zipfile.ZipFile(
            staging,
            "x",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            archive.writestr(
                _zip_info("result-bundle-manifest.json"),
                canonical_remote_result_manifest_bytes(manifest),
            )
            for name in sorted(sources):
                source, expected_size, expected_sha, signature = sources[name]
                before = source.lstat()
                if _stat_signature(before) != signature:
                    raise RemoteResultBundleError(
                        f"remote result member changed before pack: {name}"
                    )
                measured = 0
                digest = hashlib.sha256()
                with source.open("rb") as input_stream, archive.open(
                    _zip_info(name),
                    "w",
                    force_zip64=True,
                ) as output_stream:
                    for chunk in iter(
                        lambda: input_stream.read(1024 * 1024),
                        b"",
                    ):
                        measured += len(chunk)
                        digest.update(chunk)
                        output_stream.write(chunk)
                after = source.lstat()
                if (
                    _stat_signature(after) != signature
                    or measured != expected_size
                    or digest.hexdigest() != expected_sha
                ):
                    raise RemoteResultBundleError(
                        f"remote result member changed during pack: {name}"
                    )
        flush_file(staging)
        verify_remote_result_bundle(
            staging,
            expected_job_id=job_id,
            expected_attempt_id=attempt_id,
            expected_request_sha256=request_sha256,
            expected_training_bundle_sha256=training_bundle_sha256,
            expected_container_identity=container_identity,
            max_member_bytes=max_member_bytes,
            max_log_bytes=max_log_bytes,
        )
        publish_file_noreplace(staging, output)
    except RemoteResultBundleError:
        raise
    except DurableIOError as exc:
        state = (
            "published but durability is unconfirmed"
            if exc.published
            else "not published"
        )
        raise RemoteResultBundleError(
            f"remote result bundle cannot be written ({state})",
            published=exc.published,
        ) from exc
    except OSError as exc:
        raise RemoteResultBundleError(
            "remote result bundle cannot be written"
        ) from exc
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
    return verify_remote_result_bundle(
        output,
        expected_job_id=job_id,
        expected_attempt_id=attempt_id,
        expected_request_sha256=request_sha256,
        expected_training_bundle_sha256=training_bundle_sha256,
        expected_container_identity=container_identity,
        max_member_bytes=max_member_bytes,
        max_log_bytes=max_log_bytes,
    )


def verify_remote_result_bundle(
    path: Path,
    *,
    expected_job_id: str,
    expected_attempt_id: str,
    expected_request_sha256: str,
    expected_training_bundle_sha256: str,
    expected_container_identity: str,
    max_archive_bytes: int = _DEFAULT_MAX_ARCHIVE_BYTES,
    max_member_bytes: int = _DEFAULT_MAX_MEMBER_BYTES,
    max_log_bytes: int = _DEFAULT_MAX_LOG_BYTES,
) -> VerifiedRemoteResultBundle:
    archive_path = Path(path).expanduser().absolute()
    archive_size, archive_sha = _stable_file_sha(
        archive_path,
        label="remote result bundle",
        max_bytes=max_archive_bytes,
    )
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = tuple(info.filename for info in infos)
            if len(names) != len(set(names)):
                raise RemoteResultBundleError(
                    "result archive contains duplicate members"
                )
            for info in infos:
                _portable_member(info.filename)
                mode = info.external_attr >> 16
                if (
                    info.is_dir()
                    or stat.S_ISLNK(mode)
                    or info.flag_bits & 0x1
                    or info.compress_type != zipfile.ZIP_STORED
                ):
                    raise RemoteResultBundleError(
                        "result archive member type is not allowed"
                    )
                limit = (
                    max_log_bytes
                    if info.filename in _BOUND_LOG_MEMBERS
                    else max_member_bytes
                )
                if (
                    (
                        info.file_size == 0
                        and info.filename not in _BOUND_LOG_MEMBERS
                    )
                    or info.file_size < 0
                    or info.file_size > limit
                ):
                    raise RemoteResultBundleError(
                        "result archive member size is outside allowed range"
                    )
            if sum(info.file_size for info in infos) > max_archive_bytes:
                raise RemoteResultBundleError(
                    "result archive expanded size exceeds limit"
                )
            try:
                manifest_raw = archive.read(
                    "result-bundle-manifest.json",
                )
            except KeyError as exc:
                raise RemoteResultBundleError(
                    "result archive manifest is missing"
                ) from exc
            if len(manifest_raw) > 1024 * 1024:
                raise RemoteResultBundleError(
                    "result archive manifest size exceeds limit"
                )
            json.loads(
                manifest_raw.decode("ascii"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
            manifest = RemoteResultBundleManifest.model_validate_json(
                manifest_raw,
            )
            if (
                manifest_raw
                != canonical_remote_result_manifest_bytes(manifest)
            ):
                raise RemoteResultBundleError(
                    "result archive manifest is not canonical JSON"
                )
            expected_names = {
                "result-bundle-manifest.json",
                *(member.path for member in manifest.members),
            }
            if set(names) != expected_names:
                raise RemoteResultBundleError(
                    "result archive members differ from manifest"
                )
            identity = (
                manifest.job_id,
                manifest.attempt_id,
                manifest.request_sha256,
                manifest.training_bundle_sha256,
                manifest.container_identity,
            )
            expected_identity = (
                expected_job_id,
                expected_attempt_id,
                expected_request_sha256,
                expected_training_bundle_sha256,
                expected_container_identity,
            )
            if identity != expected_identity:
                labels = (
                    "job",
                    "attempt",
                    "request",
                    "training bundle",
                    "container",
                )
                mismatch = next(
                    label
                    for label, actual, expected in zip(
                        labels,
                        identity,
                        expected_identity,
                        strict=True,
                    )
                    if actual != expected
                )
                raise RemoteResultBundleError(
                    f"result archive {mismatch} identity mismatch"
                )
            member_bytes: dict[str, bytes] = {}
            for member in manifest.members:
                payload = archive.read(member.path)
                if (
                    len(payload) != member.byte_length
                    or hashlib.sha256(payload).hexdigest() != member.sha256
                ):
                    raise RemoteResultBundleError(
                        f"result member sha256/size mismatch: {member.path}"
                    )
                member_bytes[member.path] = payload
    except RemoteResultBundleError:
        raise
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        raise RemoteResultBundleError(
            "remote result bundle validation failed"
        ) from exc
    expected_container_bytes = (
        expected_container_identity + "\n"
    ).encode("ascii")
    if member_bytes["container-identity.txt"] != expected_container_bytes:
        raise RemoteResultBundleError(
            "result container identity bytes mismatch"
        )
    _validate_evaluation_member_contract(
        {member.path: member for member in manifest.members},
        member_bytes,
        container_identity=expected_container_identity,
    )
    return VerifiedRemoteResultBundle(
        path=archive_path,
        bundle_sha256=archive_sha,
        byte_length=archive_size,
        manifest=manifest,
        member_bytes=member_bytes,
    )


def _validate_local_regular_file(
    path: Path,
    *,
    label: str,
    executable: bool,
    sensitive: bool = False,
) -> None:
    try:
        result = path.lstat()
    except OSError as exc:
        error = RemoteShellExecutionError(f"{label} is unavailable")
        if sensitive:
            raise error from None
        raise error from exc
    if (
        stat.S_ISLNK(result.st_mode)
        or not stat.S_ISREG(result.st_mode)
        or result.st_size <= 0
    ):
        raise RemoteShellExecutionError(
            f"{label} must be a non-empty regular file"
        )
    if executable and not os.access(path, os.X_OK):
        raise RemoteShellExecutionError(f"{label} is not executable")


_WINDOWS_SYSTEM_SID = "S-1-5-18"
_WINDOWS_ADMINISTRATORS_SID = "S-1-5-32-544"


def _assert_private_key_protected(path: Path):
    """Reject private keys that are readable by group/other.

    POSIX keeps the existing ``st_mode & 0o077`` check. Windows cannot
    rely on the synthesized POSIX bits (often 0o666), so the owner and
    protected DACL are inspected via ``pywin32``. Only the current user,
    LocalSystem and BUILTIN\\Administrators may receive allow ACEs.
    Unknown or unsupported ACL evidence fails closed.
    """

    if os.name != "nt":
        try:
            mode = stat.S_IMODE(path.lstat().st_mode)
        except OSError:
            raise RemoteShellExecutionError(
                "SSH private key cannot be inspected"
            ) from None
        if mode & 0o077:
            raise RemoteShellExecutionError(
                "SSH private key permissions are too broad"
            )
        return

    try:
        import pywintypes
        import win32api
        import win32con
        import win32file
        import win32security
    except ImportError:
        raise RemoteShellExecutionError(
            "Windows private key ACL check requires pywin32; "
            "cannot prove key is protected (fail-closed)"
        ) from None
    handle = None
    try:
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_READ | win32con.READ_CONTROL,
            win32file.FILE_SHARE_READ,
            None,
            win32file.OPEN_EXISTING,
            (
                win32file.FILE_ATTRIBUTE_NORMAL
                | win32file.FILE_FLAG_OPEN_REPARSE_POINT
            ),
            None,
        )
        file_info = win32file.GetFileInformationByHandle(handle)
        if file_info[0] & (
            win32file.FILE_ATTRIBUTE_DIRECTORY
            | win32con.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RemoteShellExecutionError(
                "SSH private key handle is redirected or not a file"
            )
        if file_info[5] == 0 and file_info[6] == 0:
            raise RemoteShellExecutionError(
                "SSH private key must be non-empty"
            )
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_QUERY,
        )
        try:
            current_sid = win32security.GetTokenInformation(
                token,
                win32security.TokenUser,
            )[0]
        finally:
            token.Close()
        current_sid_string = win32security.ConvertSidToStringSid(
            current_sid
        )
        sd = win32security.GetSecurityInfo(
            handle,
            win32security.SE_FILE_OBJECT,
            (
                win32security.OWNER_SECURITY_INFORMATION
                | win32security.DACL_SECURITY_INFORMATION
            ),
        )
        owner = sd.GetSecurityDescriptorOwner()
        owner_sid_string = win32security.ConvertSidToStringSid(owner)
        control, _revision = sd.GetSecurityDescriptorControl()
        dacl = sd.GetSecurityDescriptorDacl()
    except RemoteShellExecutionError:
        if handle is not None:
            handle.Close()
        raise
    except (OSError, pywintypes.error):
        if handle is not None:
            handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key ACL cannot be inspected"
        ) from None
    if dacl is None:
        handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key has no DACL (everyone can read)"
        )
    if not control & win32security.SE_DACL_PROTECTED:
        handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key DACL must be protected from inheritance"
        )
    allowed_sids = {
        current_sid_string,
        _WINDOWS_SYSTEM_SID,
        _WINDOWS_ADMINISTRATORS_SID,
    }
    if owner_sid_string not in allowed_sids:
        handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key owner is not approved"
        )
    try:
        ace_count = dacl.GetAceCount()
    except pywintypes.error:
        handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key ACL entry count cannot be inspected"
        ) from None
    for ace_index in range(ace_count):
        try:
            ace = dacl.GetAce(ace_index)
            ace_header = ace[0]
            ace_type = ace_header[0]
        except (IndexError, TypeError, pywintypes.error):
            handle.Close()
            raise RemoteShellExecutionError(
                "SSH private key ACL entry is malformed"
            ) from None
        if ace_type == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE or len(ace) != 3:
            handle.Close()
            raise RemoteShellExecutionError(
                "SSH private key ACL contains an unsupported allow entry"
            )
        try:
            sid_string = win32security.ConvertSidToStringSid(ace[2])
        except (OSError, pywintypes.error):
            handle.Close()
            raise RemoteShellExecutionError(
                "SSH private key ACL principal cannot be inspected"
            ) from None
        if sid_string not in allowed_sids:
            handle.Close()
            raise RemoteShellExecutionError(
                "SSH private key ACL grants an unapproved principal"
            )
    try:
        _status, first_byte = win32file.ReadFile(handle, 1, None)
    except pywintypes.error:
        handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key readability cannot be proven"
        ) from None
    if not first_byte:
        handle.Close()
        raise RemoteShellExecutionError(
            "SSH private key must be readable and non-empty"
        )
    return handle


def _host_key_fingerprint(key_blob: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha256(key_blob).digest())
    return "SHA256:" + encoded.decode("ascii").rstrip("=")


def _verify_known_host(config: RemoteShellExecutorConfig) -> None:
    _validate_local_regular_file(
        config.known_hosts_path,
        label="known-hosts file",
        executable=False,
    )
    try:
        raw = config.known_hosts_path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise RemoteShellExecutionError(
            "known-hosts file cannot be read"
        ) from exc
    labels = {
        config.known_host,
        f"[{config.known_host}]:{config.port}",
    }
    fingerprints: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if fields[0].startswith("@") or len(fields) != 3:
            continue
        if not set(fields[0].split(",")) & labels:
            continue
        try:
            key_blob = base64.b64decode(fields[2], validate=True)
        except ValueError:
            continue
        fingerprints.add(_host_key_fingerprint(key_blob))
    if config.expected_host_key_fingerprint not in fingerprints:
        raise RemoteShellExecutionError(
            "known-host fingerprint mismatch"
        )


class RemoteShellExecutor:
    """Strict submit/poll/fetch implementation for one configured host."""

    def __init__(
        self,
        config: RemoteShellExecutorConfig,
        *,
        run_command: Callable[..., subprocess.CompletedProcess] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.config = config
        self._run_command = run_command or subprocess.run
        self._now = now or (lambda: datetime.now(UTC))
        self.command_audit: list[tuple[str, ...]] = []
        self._jobs: dict[tuple[str, str], _JobContext] = {}
        self._private_key_guard = None
        self._closed = False
        _validate_local_regular_file(
            config.ssh_binary,
            label="ssh binary",
            executable=True,
        )
        _validate_local_regular_file(
            config.scp_binary,
            label="scp binary",
            executable=True,
        )
        _validate_local_regular_file(
            config.private_key_path,
            label="SSH private key",
            executable=False,
            sensitive=True,
        )
        key_guard = _assert_private_key_protected(config.private_key_path)
        try:
            _verify_known_host(config)
        except BaseException:
            if key_guard is not None:
                key_guard.Close()
            raise
        self._private_key_guard = key_guard

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        guard = self._private_key_guard
        if guard is not None:
            guard.Close()
            self._private_key_guard = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _common_options(self, *, scp: bool) -> list[str]:
        port_flag = "-P" if scp else "-p"
        return [
            "-F",
            os.devnull,
            "-i",
            str(self.config.private_key_path),
            port_flag,
            str(self.config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.config.known_hosts_path}",
            "-o",
            f"GlobalKnownHostsFile={os.devnull}",
            "-o",
            f"HostKeyAlias={self.config.known_host}",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout_seconds}",
        ]

    def _invoke(
        self,
        argv: list[str],
        *,
        phase: str,
    ) -> subprocess.CompletedProcess:
        if self._closed:
            raise RemoteShellExecutionError(
                "remote shell executor is closed"
            )

        def redact(item: str) -> str:
            if item == str(self.config.private_key_path):
                return "<redacted-private-key>"
            if item == str(self.config.ssh_binary):
                return "<ssh-binary>"
            if item == str(self.config.scp_binary):
                return "<scp-binary>"
            if item == self.config.ssh_target:
                return "<redacted-ssh-target>"
            if item.startswith("UserKnownHostsFile="):
                return "UserKnownHostsFile=<redacted-known-hosts>"
            if item.startswith("HostKeyAlias="):
                return "HostKeyAlias=<redacted-known-host>"
            return item

        redacted = tuple(
            redact(item)
            for item in argv
        )
        self.command_audit.append(redacted)
        try:
            completed = self._run_command(
                argv,
                shell=False,
                capture_output=True,
                timeout=self.config.command_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            raise RemoteShellTransportError(
                f"{phase} transport could not be executed"
            ) from None
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8", errors="replace")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="replace")
        if (
            len(stdout) > self.config.max_command_output_bytes
            or len(stderr) > self.config.max_command_output_bytes
        ):
            raise RemoteShellExecutionError(
                f"{phase} output exceeded the bounded capture limit"
            )
        return completed

    def _ssh(self, remote_argv: list[str], *, phase: str):
        command = shlex.join(remote_argv)
        argv = [
            str(self.config.ssh_binary),
            *self._common_options(scp=False),
            "--",
            self.config.ssh_target,
            command,
        ]
        return self._invoke(argv, phase=phase)

    def _scp_upload(self, source: Path, destination: str):
        argv = [
            str(self.config.scp_binary),
            *self._common_options(scp=True),
            "--",
            str(source),
            f"{self.config.ssh_target}:{destination}",
        ]
        return self._invoke(argv, phase="training bundle upload")

    def _scp_download(self, source: str, destination: Path):
        argv = [
            str(self.config.scp_binary),
            *self._common_options(scp=True),
            "--",
            f"{self.config.ssh_target}:{source}",
            str(destination),
        ]
        return self._invoke(argv, phase="result bundle download")

    @staticmethod
    def _require_zero(
        completed: subprocess.CompletedProcess,
        *,
        phase: str,
    ) -> None:
        if completed.returncode != 0:
            raise RemoteShellExecutionError(
                f"{phase} failed with code {completed.returncode}"
            )

    def prepare(
        self,
        bundle: VerifiedTrainingJobBundle,
    ) -> ExecutorJobBundle:
        try:
            verified = verify_production_training_job_bundle(bundle.path)
        except RealSceneTrainingError as exc:
            raise RemoteShellExecutionError(
                f"production bundle verification failed: {exc}"
            ) from exc
        if (
            verified.bundle_sha256 != bundle.bundle_sha256
            or verified.request != bundle.request
        ):
            raise RemoteShellExecutionError(
                "prepared bundle identity changed during verification"
            )
        config = verified.request.training_config
        if (
            config.trainer_name != "nerfstudio-splatfacto"
            or config.trainer_version != "1.1.5"
        ):
            raise RemoteShellExecutionError(
                "remote production executor requires Splatfacto 1.1.5"
            )
        request_sha = request_canonical_sha256(verified.request)
        job_id = "ns-" + verified.bundle_sha256[:32]
        identity = ExecutorInputIdentity(
            executor_kind="remote-shell-nerfstudio",
            request_sha256=request_sha,
            dataset_receipt_sha256=(
                verified.manifest.dataset_receipt_sha256
            ),
            training_config_sha256=(
                verified.request.requested_config_sha256
            ),
            trainer_name=config.trainer_name,
            trainer_version=config.trainer_version,
            job_id=job_id,
        )
        return ExecutorJobBundle(
            bundle=verified,
            input_identity=identity,
        )

    def submit(self, bundle: ExecutorJobBundle) -> RemoteShellJobRef:
        if bundle.input_identity.executor_kind != (
            "remote-shell-nerfstudio"
        ):
            raise RemoteShellExecutionError(
                "remote submit received the wrong executor identity"
            )
        attempt_id = "attempt-" + uuid.uuid4().hex
        submitted_at = self._now()
        remote_job_path = (
            f"{self.config.remote_root}/{bundle.input_identity.job_id}/"
            f"{attempt_id}"
        )
        worker = (
            f"{self.config.remote_repo_root}/"
            "cloud/remote_training_worker.py"
        )
        init = self._ssh(
            [
                "python3",
                worker,
                "init",
                "--job-dir",
                remote_job_path,
                "--job-id",
                bundle.input_identity.job_id,
                "--attempt-id",
                attempt_id,
                "--request-sha256",
                bundle.input_identity.request_sha256,
                "--training-bundle-sha256",
                bundle.bundle.bundle_sha256,
            ],
            phase="remote job initialization",
        )
        self._require_zero(init, phase="remote job initialization")
        upload = self._scp_upload(
            bundle.bundle.path,
            f"{remote_job_path}/training-job.zip",
        )
        self._require_zero(upload, phase="training bundle upload")
        start = self._ssh(
            [
                "python3",
                worker,
                "start",
                "--detach",
                "--job-dir",
                remote_job_path,
                "--repo-root",
                self.config.remote_repo_root,
                "--container-identity",
                self.config.container_identity,
                "--container-runtime",
                self.config.container_runtime,
            ],
            phase="remote job start",
        )
        self._require_zero(start, phase="remote job start")
        job = RemoteShellJobRef(
            job_id=bundle.input_identity.job_id,
            attempt_id=attempt_id,
            submitted_at_utc=submitted_at,
            request_sha256=bundle.input_identity.request_sha256,
            training_bundle_sha256=bundle.bundle.bundle_sha256,
            config_identity_sha256=(
                remote_shell_executor_config_sha256(self.config)
            ),
            remote_job_path=remote_job_path,
        )
        receipt = new_attempt(
            bundle.input_identity,
            attempt_id=attempt_id,
            created_at_utc=submitted_at,
            quality_role="production",
        )
        receipt = advance_attempt(
            receipt,
            ExecutorObservation(
                state="running",
                observed_at_utc=submitted_at,
            ),
        )
        self._jobs[(job.job_id, job.attempt_id)] = _JobContext(
            job=job,
            bundle=bundle.bundle,
            receipt=receipt,
        )
        return job

    def restore(
        self,
        bundle: ExecutorJobBundle,
        job: RemoteShellJobRef,
    ) -> RemoteShellJobRef:
        """Attach one verified submitted job without remote side effects."""

        if self._closed:
            raise RemoteShellExecutionError(
                "remote shell executor is closed"
            )
        verified = self.prepare(bundle.bundle)
        if (
            verified.input_identity != bundle.input_identity
            or verified.bundle.bundle_sha256
            != bundle.bundle.bundle_sha256
            or verified.bundle.request != bundle.bundle.request
        ):
            raise RemoteShellExecutionError(
                "restore bundle identity changed during verification"
            )
        expected_config = remote_shell_executor_config_sha256(
            self.config
        )
        if job.config_identity_sha256 != expected_config:
            raise RemoteShellExecutionError(
                "remote job config identity differs"
            )
        expected_path = (
            f"{self.config.remote_root}/{job.job_id}/"
            f"{job.attempt_id}"
        )
        expected_identity = (
            bundle.input_identity.job_id,
            bundle.input_identity.request_sha256,
            bundle.bundle.bundle_sha256,
            expected_path,
        )
        measured_identity = (
            job.job_id,
            job.request_sha256,
            job.training_bundle_sha256,
            job.remote_job_path,
        )
        if measured_identity != expected_identity:
            raise RemoteShellExecutionError(
                "remote job identity differs from prepared bundle"
            )
        key = (job.job_id, job.attempt_id)
        if key in self._jobs:
            raise RemoteShellExecutionError(
                "remote job is already attached"
            )
        try:
            lifecycle_receipt = self._fetch_lifecycle(job)
        except RemoteShellTransportError as exc:
            raise RemoteShellExecutionError(
                "restore requires a durable lifecycle receipt"
            ) from exc
        self._verify_lifecycle_identity(lifecycle_receipt, job)
        receipt = new_attempt(
            bundle.input_identity,
            attempt_id=job.attempt_id,
            created_at_utc=job.submitted_at_utc,
            quality_role="production",
        )
        receipt = advance_attempt(
            receipt,
            ExecutorObservation(
                state="running",
                observed_at_utc=job.submitted_at_utc,
            ),
        )
        self._jobs[key] = _JobContext(
            job=job,
            bundle=verified.bundle,
            receipt=receipt,
            bound_container_id=lifecycle_receipt.container_id,
        )
        return job

    def _context(self, job: ExecutorJobRef) -> _JobContext:
        context = self._jobs.get((job.job_id, job.attempt_id))
        if context is None or context.job != job:
            raise RemoteShellExecutionError(
                "remote job reference identity is unknown or changed"
            )
        return context

    def _fetch_lifecycle(
        self,
        job: RemoteShellJobRef,
    ) -> RemoteContainerLifecycleReceipt:
        """Fetch and validate the durable lifecycle receipt from the worker.

        Returns the loaded, canonical, content-addressed receipt.  Transport
        failures raise :class:`RemoteShellTransportError` so callers can
        distinguish "unreachable" (return unknown) from "received but invalid"
        (raise fail-closed).  The caller never self-reports the receipt SHA;
        it is recomputed by :func:`load_container_lifecycle_receipt`.
        """
        worker = (
            f"{self.config.remote_repo_root}/"
            "cloud/remote_training_worker.py"
        )
        completed = self._ssh(
            [
                "python3",
                worker,
                "lifecycle",
                "--job-dir",
                job.remote_job_path,
                "--max-bytes",
                str(_MAX_LIFECYCLE_BYTES),
            ],
            phase="remote lifecycle poll",
        )
        if completed.returncode != 0:
            raise RemoteShellTransportError(
                "remote lifecycle poll returned nonzero exit"
            )
        stdout = completed.stdout or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8", errors="replace")
        if not stdout or len(stdout) > _MAX_LIFECYCLE_BYTES:
            raise RemoteShellExecutionError(
                "remote lifecycle output is empty or oversized"
            )
        return load_container_lifecycle_receipt(stdout)

    def _verify_lifecycle_identity(
        self,
        receipt: RemoteContainerLifecycleReceipt,
        job: RemoteShellJobRef,
    ) -> None:
        """Fail-closed identity check binding receipt to submitted job.

        Ensures the receipt's job/attempt/request/training-bundle identity
        matches the submitted job, the workspace identity SHA matches the
        recomputed remote path, and the container identity matches the
        executor config.  Container ID binding is checked separately by
        callers to distinguish first-bind from revalidation.
        """
        expected_workspace_sha = compute_workspace_identity_sha256(
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            workspace_path=job.remote_job_path,
        )
        if (
            receipt.job_id != job.job_id
            or receipt.attempt_id != job.attempt_id
            or receipt.request_sha256 != job.request_sha256
            or receipt.training_bundle_sha256
            != job.training_bundle_sha256
            or receipt.workspace_identity_sha256
            != expected_workspace_sha
            or receipt.container_identity
            != self.config.container_identity
        ):
            raise RemoteShellExecutionError(
                "remote lifecycle identity differs from submitted job"
            )

    def poll(self, job: ExecutorJobRef) -> ExecutorObservation:
        context = self._context(job)
        worker = (
            f"{self.config.remote_repo_root}/"
            "cloud/remote_training_worker.py"
        )
        try:
            completed = self._ssh(
                [
                    "python3",
                    worker,
                    "status",
                    "--job-dir",
                    context.job.remote_job_path,
                    "--max-bytes",
                    str(_MAX_STATUS_BYTES),
                ],
                phase="remote status poll",
            )
        except RemoteShellTransportError:
            return normalize_poll_result(
                exit_code=None,
                reachable=False,
                observed_at_utc=self._now(),
            )
        if completed.returncode != 0:
            return normalize_poll_result(
                exit_code=None,
                reachable=False,
                observed_at_utc=self._now(),
            )
        stdout = completed.stdout
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8")
        if not stdout or len(stdout) > _MAX_STATUS_BYTES:
            raise RemoteShellExecutionError(
                "remote status output is empty or oversized"
            )
        try:
            json.loads(
                stdout.decode("ascii"),
                object_pairs_hook=_reject_duplicate_pairs,
            )
            status = RemoteShellStatus.model_validate_json(stdout)
        except (UnicodeError, ValueError) as exc:
            raise RemoteShellExecutionError(
                "remote status JSON is invalid"
            ) from exc
        if stdout != canonical_remote_status_bytes(status):
            raise RemoteShellExecutionError(
                "remote status JSON is not canonical"
            )
        identity = (
            status.job_id,
            status.attempt_id,
            status.request_sha256,
            status.training_bundle_sha256,
        )
        expected = (
            context.job.job_id,
            context.job.attempt_id,
            context.job.request_sha256,
            context.job.training_bundle_sha256,
        )
        if identity != expected:
            raise RemoteShellExecutionError(
                "remote status identity differs from submitted job"
            )
        context.last_status = status
        if status.state == "running":
            if context.bound_container_id is None:
                try:
                    receipt = self._fetch_lifecycle(context.job)
                except RemoteShellTransportError:
                    return normalize_poll_result(
                        exit_code=None,
                        reachable=False,
                        observed_at_utc=self._now(),
                    )
                self._verify_lifecycle_identity(receipt, context.job)
                context.bound_container_id = receipt.container_id
            else:
                try:
                    receipt = self._fetch_lifecycle(context.job)
                except RemoteShellTransportError:
                    return normalize_poll_result(
                        exit_code=None,
                        reachable=False,
                        observed_at_utc=self._now(),
                    )
                self._verify_lifecycle_identity(receipt, context.job)
                if receipt.container_id != context.bound_container_id:
                    raise RemoteShellExecutionError(
                        "remote lifecycle container swap detected"
                    )
            return ExecutorObservation(
                state="running",
                observed_at_utc=status.updated_at_utc,
            )
        if status.state == "failed":
            return normalize_poll_result(
                exit_code=status.exit_code,
                reachable=True,
                observed_at_utc=status.updated_at_utc,
                stdout_sha256=status.stdout_sha256,
                stderr_sha256=status.stderr_sha256,
            )
        return normalize_poll_result(
            exit_code=0,
            reachable=True,
            observed_at_utc=status.updated_at_utc,
            outputs_verified=False,
            stdout_sha256=status.stdout_sha256,
            stderr_sha256=status.stderr_sha256,
        )

    def _verify_result_semantics(
        self,
        verified_result: VerifiedRemoteResultBundle,
        context: _JobContext,
    ) -> None:
        members = verified_result.member_bytes
        try:
            request = TrainingRequest.model_validate_json(
                members["training-request.json"],
            )
            result = TrainingResult.model_validate_json(
                members["training-result.json"],
            )
        except ValueError as exc:
            raise RemoteResultBundleError(
                "result training provenance JSON is invalid"
            ) from exc
        try:
            verified_input = verify_production_training_job_bundle(
                context.bundle.path,
            )
            input_bytes = load_training_job_input_bytes(verified_input)
        except RealSceneTrainingError as exc:
            raise RemoteResultBundleError(
                f"submitted production bundle changed: {exc}"
            ) from exc
        if (
            verified_input.bundle_sha256
            != context.job.training_bundle_sha256
            or request != verified_input.request
        ):
            raise RemoteResultBundleError(
                "result request differs from submitted production bundle"
            )
        try:
            validate_dataparser_transform(
                verified_result.path.parent
                / "dataparser_transforms.json",
            )
        except DataparserTransformError as exc:
            raise RemoteResultBundleError(
                f"result dataparser transform is unsafe: {exc}"
            ) from exc
        try:
            validate_training_provenance(
                result,
                request,
                actual_ply_bytes=members["point_cloud.ply"],
                actual_config_bytes=members[
                    "operator-intent-config.yml"
                ],
                actual_log_bytes=members["training.log"],
                actual_dataparser_transform_bytes=members[
                    "dataparser_transforms.json"
                ],
                input_bytes_by_path=input_bytes,
            )
        except ValueError as exc:
            raise RemoteResultBundleError(
                f"result training provenance is not content-closed: {exc}"
            ) from exc
        if result.training_status.state != "completed":
            raise RemoteResultBundleError(
                "remote successful result is not a completed training run"
            )
        _validate_downloaded_evaluation(
            verified_result,
            verified_input,
            input_bytes["training/held-out-split.json"],
        )

    def fetch(
        self,
        job: ExecutorJobRef,
        destination: Path,
    ) -> ExecutorAttemptReceipt:
        context = self._context(job)
        status = context.last_status
        if status is None or status.state != "succeeded":
            raise RemoteShellExecutionError(
                "fetch requires a succeeded remote status observation"
            )
        if context.bound_container_id is None:
            try:
                receipt = self._fetch_lifecycle(context.job)
            except RemoteShellTransportError as exc:
                raise RemoteShellExecutionError(
                    "fetch requires a durable lifecycle receipt"
                ) from exc
            self._verify_lifecycle_identity(receipt, context.job)
            context.bound_container_id = receipt.container_id
        else:
            try:
                receipt = self._fetch_lifecycle(context.job)
            except RemoteShellTransportError as exc:
                raise RemoteShellExecutionError(
                    "fetch cannot verify lifecycle container"
                ) from exc
            self._verify_lifecycle_identity(receipt, context.job)
            if receipt.container_id != context.bound_container_id:
                raise RemoteShellExecutionError(
                    "fetch detected lifecycle container swap"
                )
        destination = Path(destination).expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise RemoteShellExecutionError(
                "result destination boundary must be absent"
            )
        parent = destination.parent
        try:
            parent_stat = parent.lstat()
        except OSError as exc:
            raise RemoteShellExecutionError(
                "result destination parent is unavailable"
            ) from exc
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(
            parent_stat.st_mode
        ):
            raise RemoteShellExecutionError(
                "result destination parent must be a real directory"
            )
        staging = parent / f".{destination.name}.{uuid.uuid4().hex}.staging"
        try:
            staging.mkdir(mode=0o700)
            archive_path = staging / "result-bundle.zip"
            completed = self._scp_download(
                f"{context.job.remote_job_path}/result-bundle.zip",
                archive_path,
            )
            self._require_zero(
                completed,
                phase="result bundle download",
            )
            verified = verify_remote_result_bundle(
                archive_path,
                expected_job_id=context.job.job_id,
                expected_attempt_id=context.job.attempt_id,
                expected_request_sha256=context.job.request_sha256,
                expected_training_bundle_sha256=(
                    context.job.training_bundle_sha256
                ),
                expected_container_identity=(
                    self.config.container_identity
                ),
                max_archive_bytes=self.config.max_result_bundle_bytes,
                max_member_bytes=self.config.max_result_member_bytes,
                max_log_bytes=self.config.max_log_bytes,
            )
            if (
                verified.bundle_sha256
                != status.result_bundle_sha256
                or verified.byte_length
                != status.result_bundle_size_bytes
            ):
                raise RemoteResultBundleError(
                    "downloaded result differs from remote status identity"
                )
            if (
                hashlib.sha256(
                    verified.member_bytes["worker.stdout.log"]
                ).hexdigest()
                != status.stdout_sha256
                or hashlib.sha256(
                    verified.member_bytes["worker.stderr.log"]
                ).hexdigest()
                != status.stderr_sha256
            ):
                raise RemoteResultBundleError(
                    "downloaded worker logs differ from remote status hashes"
                )
            for name, payload in verified.member_bytes.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            self._verify_result_semantics(verified, context)
            os.replace(staging, destination)
        except (
            OSError,
            RemoteResultBundleError,
            RemoteShellExecutionError,
        ):
            shutil.rmtree(staging, ignore_errors=True)
            raise
        observation = ExecutorObservation(
            state="succeeded",
            observed_at_utc=self._now(),
            exit_code=0,
            stdout_sha256=status.stdout_sha256,
            stderr_sha256=status.stderr_sha256,
            result_bundle_sha256=status.result_bundle_sha256,
        )
        context.receipt = advance_attempt(
            context.receipt,
            observation,
        )
        return context.receipt


# ---------------------------------------------------------------------------
# Credential-free preflight (P1-2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LocalCheckOutcome:
    verified: bool
    blocked: bool
    code: PreflightFailureCode | None = None
    detail: str | None = None


def _probe_local_regular_file(
    path: Path,
    *,
    label: str,
    executable: bool,
    missing_code: PreflightFailureCode,
    invalid_code: PreflightFailureCode,
) -> _LocalCheckOutcome:
    """Check a local binary or config file without raising.

    ``blocked=True`` means the file is missing (external input unavailable);
    ``blocked=False`` with ``verified=False`` means the file exists but is
    misconfigured (wrong type, empty, not executable).
    """
    if not path.exists():
        return _LocalCheckOutcome(
            verified=False,
            blocked=True,
            code=missing_code,
            detail=f"{label} is missing",
        )
    try:
        _validate_local_regular_file(
            path,
            label=label,
            executable=executable,
        )
    except RemoteShellExecutionError as exc:
        return _LocalCheckOutcome(
            verified=False,
            blocked=False,
            code=invalid_code,
            detail=str(exc),
        )
    return _LocalCheckOutcome(verified=True, blocked=False)


def _probe_private_key_protection(
    path: Path,
) -> _LocalCheckOutcome:
    """Check private key protection without holding the handle."""
    if not path.exists():
        return _LocalCheckOutcome(
            verified=False,
            blocked=True,
            code="private-key-missing",
            detail="SSH private key is missing",
        )
    try:
        handle = _assert_private_key_protected(path)
    except RemoteShellExecutionError as exc:
        detail = str(exc)
        blocked = "cannot be inspected" in detail
        return _LocalCheckOutcome(
            verified=False,
            blocked=blocked,
            code=(
                "private-key-uninspectable"
                if blocked
                else "private-key-invalid"
            ),
            detail=detail,
        )
    if handle is not None:
        handle.Close()
    return _LocalCheckOutcome(verified=True, blocked=False)


def _probe_known_hosts(
    config: RemoteShellExecutorConfig,
) -> _LocalCheckOutcome:
    """Check known_hosts file and fingerprint without raising."""
    if not config.known_hosts_path.exists():
        return _LocalCheckOutcome(
            verified=False,
            blocked=True,
            code="known-hosts-missing",
            detail="known-hosts file is missing",
        )
    try:
        _verify_known_host(config)
    except RemoteShellExecutionError as exc:
        return _LocalCheckOutcome(
            verified=False,
            blocked=False,
            code="known-hosts-invalid",
            detail=str(exc),
        )
    return _LocalCheckOutcome(verified=True, blocked=False)


@dataclass(frozen=True)
class _PreflightInputSnapshot:
    sha256: str
    stat_signature: tuple[int, int, int, int, int, int]


def _preflight_input_snapshot(
    path: Path,
    *,
    label: str,
) -> _PreflightInputSnapshot | None:
    try:
        before = path.lstat()
        sha256 = _stable_file_sha(
            path,
            label=label,
            max_bytes=512 * 1024 * 1024,
        )[1]
        after = path.lstat()
    except (OSError, RemoteResultBundleError):
        return None
    before_signature = _stat_signature(before)
    after_signature = _stat_signature(after)
    if before_signature != after_signature:
        return None
    return _PreflightInputSnapshot(
        sha256=sha256,
        stat_signature=after_signature,
    )


@dataclass(frozen=True)
class _RemoteProbeOutcome:
    runtime_verified: bool | None
    image_verified: bool | None
    worker_verified: bool | None
    blocked: bool
    code: PreflightFailureCode | None = None
    detail: str | None = None
    evidence: RemoteReadinessEvidence | None = None


def _parse_remote_readiness_evidence(
    payload: bytes,
) -> RemoteReadinessEvidence:
    if not payload or len(payload) > _MAX_STATUS_BYTES:
        raise RemoteShellExecutionError(
            "remote readiness evidence size is invalid"
        )
    try:
        raw = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        evidence = RemoteReadinessEvidence.model_validate(raw)
    except (UnicodeError, ValueError) as exc:
        raise RemoteShellExecutionError(
            "remote readiness evidence is invalid"
        ) from exc
    if payload != _canonical_model_bytes(evidence):
        raise RemoteShellExecutionError(
            "remote readiness evidence is not canonical"
        )
    return evidence


def _probe_remote_capabilities(
    config: RemoteShellExecutorConfig,
    *,
    run_command: Callable[..., subprocess.CompletedProcess] | None,
    command_audit: list[tuple[str, ...]] | None = None,
) -> _RemoteProbeOutcome:
    """Run fixed-argv read-only remote probes.

    Probes (neither creates a directory, uploads a bundle, nor starts a
    container):

    1. ``<container_runtime> --version`` — verifies the runtime is callable.
    2. ``test -f <remote_repo_root>/cloud/remote_training_worker.py`` —
       verifies the worker binary exists on the remote.

    When ``command_audit`` is provided, the redacted argv tuples recorded by
    the internal executor are appended so callers can verify secret/path
    redaction without inspecting raw subprocess argv.
    """
    try:
        executor = RemoteShellExecutor(config, run_command=run_command)
    except RemoteShellExecutionError as exc:
        return _RemoteProbeOutcome(
            runtime_verified=None,
            image_verified=None,
            worker_verified=None,
            blocked=False,
            code="local-transport-drift",
            detail=f"local transport changed during remote probe: {exc}",
        )
    try:
        completed = executor._ssh(
            ["nantai-remote-readiness-checker"],
            phase="remote readiness checker",
        )
        if completed.returncode != 0:
            return _RemoteProbeOutcome(
                runtime_verified=False,
                image_verified=False,
                worker_verified=False,
                blocked=False,
                code="remote-checker-invalid",
                detail=_PREFLIGHT_FAILURE_REASONS[
                    "remote-checker-invalid"
                ],
            )
        try:
            evidence = _parse_remote_readiness_evidence(
                completed.stdout,
            )
        except RemoteShellExecutionError:
            return _RemoteProbeOutcome(
                runtime_verified=False,
                image_verified=False,
                worker_verified=False,
                blocked=False,
                code="remote-checker-invalid",
                detail=_PREFLIGHT_FAILURE_REASONS[
                    "remote-checker-invalid"
                ],
            )
        runtime_verified = (
            evidence.container_runtime == config.container_runtime
        )
        image_verified = (
            evidence.container_identity == config.container_identity
        )
        worker_verified = (
            evidence.worker_sha256 == config.expected_worker_sha256
            and evidence.worker_version
            == config.expected_worker_version
        )
        checker_config_verified = (
            evidence.checker_config_sha256
            == config.expected_checker_config_sha256
        )
        if not checker_config_verified:
            code: PreflightFailureCode = (
                "remote-checker-config-mismatch"
            )
        elif not runtime_verified:
            code: PreflightFailureCode = "remote-runtime-mismatch"
        elif not image_verified:
            code = "remote-container-mismatch"
        elif not worker_verified:
            code = "remote-worker-mismatch"
        else:
            code = None
        return _RemoteProbeOutcome(
            runtime_verified=runtime_verified,
            image_verified=image_verified,
            worker_verified=worker_verified,
            blocked=False,
            code=code,
            detail=(
                None
                if code is None
                else _PREFLIGHT_FAILURE_REASONS[code]
            ),
            evidence=evidence,
        )
    except RemoteShellExecutionError:
        return _RemoteProbeOutcome(
            runtime_verified=None,
            image_verified=None,
            worker_verified=None,
            blocked=True,
            code="remote-unreachable",
            detail="remote probe could not reach the target",
        )
    finally:
        if command_audit is not None:
            command_audit.extend(executor.command_audit)
        executor.close()


def run_remote_shell_preflight(
    config: RemoteShellExecutorConfig,
    *,
    probe_remote: bool = False,
    run_command: Callable[..., subprocess.CompletedProcess] | None = None,
    now: Callable[[], datetime] | None = None,
    command_audit: list[tuple[str, ...]] | None = None,
) -> RemoteShellPreflightReport:
    """Run credential-free preflight without submitting a job.

    Local transport checks (ssh/scp binaries, private key protection,
    known_hosts fingerprint) are captured without raising. When
    ``probe_remote`` is true and local checks pass, two fixed-argv
    read-only SSH commands probe the remote target; neither uploads a
    bundle, creates a directory, nor starts a container.

    The report never carries connection secrets, private key paths, or
    config file contents. ``failure_reason`` is a sanitized summary
    derived from the check labels, not from raw exception tracebacks.

    When ``command_audit`` is provided, the redacted argv tuples recorded
    during the remote probe are appended so callers can verify secret/path
    redaction without inspecting raw subprocess argv.
    """
    now_fn = now or (lambda: datetime.now(UTC))

    ssh_outcome = _probe_local_regular_file(
        config.ssh_binary,
        label="ssh binary",
        executable=True,
        missing_code="ssh-binary-missing",
        invalid_code="ssh-binary-invalid",
    )
    scp_outcome = _probe_local_regular_file(
        config.scp_binary,
        label="scp binary",
        executable=True,
        missing_code="scp-binary-missing",
        invalid_code="scp-binary-invalid",
    )
    key_outcome = _probe_private_key_protection(config.private_key_path)
    hosts_outcome = _probe_known_hosts(config)

    local_outcomes = [ssh_outcome, scp_outcome, key_outcome, hosts_outcome]
    local_snapshot_inputs = {
        "ssh_binary_sha256": _preflight_input_snapshot(
            config.ssh_binary,
            label="ssh binary",
        ),
        "scp_binary_sha256": _preflight_input_snapshot(
            config.scp_binary,
            label="scp binary",
        ),
        "private_key_sha256": _preflight_input_snapshot(
            config.private_key_path,
            label="SSH private key",
        ),
        "known_hosts_sha256": _preflight_input_snapshot(
            config.known_hosts_path,
            label="known-hosts file",
        ),
    }
    local_input_hashes = {
        field: (
            snapshot.sha256
            if snapshot is not None
            else None
        )
        for field, snapshot in local_snapshot_inputs.items()
    }
    if any(
        outcome.verified and snapshot is None
        for outcome, snapshot in zip(
            local_outcomes,
            local_snapshot_inputs.values(),
            strict=True,
        )
    ):
        local_outcomes.append(
            _LocalCheckOutcome(
                verified=False,
                blocked=False,
                code="local-transport-drift",
                detail="local transport changed during snapshot",
            )
        )
    local_all_passed = all(o.verified for o in local_outcomes)
    config_identity_sha256 = hashlib.sha256(
        _canonical_model_bytes(config),
    ).hexdigest()

    container_runtime_verified: bool | None = None
    container_image_verified: bool | None = None
    worker_binary_verified: bool | None = None
    remote_outcome: _RemoteProbeOutcome | None = None

    if local_all_passed and probe_remote:
        remote_outcome = _probe_remote_capabilities(
            config,
            run_command=run_command,
            command_audit=command_audit,
        )
        container_runtime_verified = remote_outcome.runtime_verified
        container_image_verified = remote_outcome.image_verified
        worker_binary_verified = remote_outcome.worker_verified
    local_drift = False
    if local_all_passed and probe_remote:
        post_snapshots = {
            "ssh_binary_sha256": _preflight_input_snapshot(
                config.ssh_binary,
                label="ssh binary",
            ),
            "scp_binary_sha256": _preflight_input_snapshot(
                config.scp_binary,
                label="scp binary",
            ),
            "private_key_sha256": _preflight_input_snapshot(
                config.private_key_path,
                label="SSH private key",
            ),
            "known_hosts_sha256": _preflight_input_snapshot(
                config.known_hosts_path,
                label="known-hosts file",
            ),
        }
        local_drift = (
            post_snapshots != local_snapshot_inputs
            or hashlib.sha256(
                _canonical_model_bytes(config),
            ).hexdigest()
            != config_identity_sha256
        )

    status: Literal["ready", "blocked-external-input", "failed"]
    failure_code: PreflightFailureCode | None
    failure_reason: str | None

    if local_drift:
        status = "failed"
        failure_code = "local-transport-drift"
    elif local_all_passed:
        if not probe_remote:
            status = "failed"
            failure_code = "remote-probe-required"
        elif remote_outcome is None:
            status = "failed"
            failure_code = "remote-probe-not-executed"
        elif remote_outcome.blocked:
            status = "blocked-external-input"
            failure_code = remote_outcome.code
        elif (
            remote_outcome.runtime_verified
            and remote_outcome.image_verified
            and remote_outcome.worker_verified
        ):
            status = "ready"
            failure_code = None
        else:
            status = "failed"
            failure_code = remote_outcome.code
    else:
        failed_outcomes = [
            outcome
            for outcome in local_outcomes
            if not outcome.verified and not outcome.blocked
        ]
        blocked_outcomes = [
            outcome
            for outcome in local_outcomes
            if not outcome.verified and outcome.blocked
        ]
        if failed_outcomes:
            status = "failed"
            failure_code = failed_outcomes[0].code
        else:
            status = "blocked-external-input"
            failure_code = blocked_outcomes[0].code

    failure_reason = (
        None
        if failure_code is None
        else _PREFLIGHT_FAILURE_REASONS[failure_code]
    )

    return _build_remote_shell_preflight_report(
        status=status,
        checked_at_utc=now_fn(),
        config_identity_sha256=config_identity_sha256,
        **local_input_hashes,
        container_identity=config.container_identity,
        expected_host_key_fingerprint=config.expected_host_key_fingerprint,
        known_host=config.known_host,
        port=config.port,
        remote_root=config.remote_root,
        remote_repo_root=config.remote_repo_root,
        container_runtime=config.container_runtime,
        ssh_binary_found=ssh_outcome.verified,
        scp_binary_found=scp_outcome.verified,
        private_key_protection_verified=key_outcome.verified,
        known_hosts_verified=hosts_outcome.verified,
        checker_version=(
            remote_outcome.evidence.checker_version
            if remote_outcome is not None
            and remote_outcome.evidence is not None
            else None
        ),
        expected_checker_config_sha256=(
            config.expected_checker_config_sha256
        ),
        checker_config_sha256=(
            remote_outcome.evidence.checker_config_sha256
            if remote_outcome is not None
            and remote_outcome.evidence is not None
            else None
        ),
        container_runtime_version=(
            remote_outcome.evidence.container_runtime_version
            if remote_outcome is not None
            and remote_outcome.evidence is not None
            else None
        ),
        measured_container_identity=(
            remote_outcome.evidence.container_identity
            if remote_outcome is not None
            and remote_outcome.evidence is not None
            else None
        ),
        container_runtime_verified=container_runtime_verified,
        container_image_verified=container_image_verified,
        worker_binary_sha256=(
            remote_outcome.evidence.worker_sha256
            if remote_outcome is not None
            and remote_outcome.evidence is not None
            else None
        ),
        worker_version=(
            remote_outcome.evidence.worker_version
            if remote_outcome is not None
            and remote_outcome.evidence is not None
            else None
        ),
        worker_binary_verified=worker_binary_verified,
        failure_code=failure_code,
        failure_reason=failure_reason,
    )


def run_remote_shell_preflight_from_path(
    config_path: str | Path,
    *,
    run_command: Callable[..., subprocess.CompletedProcess] | None = None,
    now: Callable[[], datetime] | None = None,
    command_audit: list[tuple[str, ...]] | None = None,
) -> RemoteShellPreflightReport:
    """Load, probe, and recheck one immutable config-path snapshot."""

    path = Path(config_path)
    if not path.is_absolute():
        raise RemoteShellExecutionError(
            "remote executor config path must be absolute"
        )
    try:
        before = path.lstat()
    except OSError as exc:
        raise RemoteShellExecutionError(
            "remote executor config cannot be read"
        ) from exc
    config = load_remote_shell_executor_config(path)
    report = run_remote_shell_preflight(
        config,
        probe_remote=True,
        run_command=run_command,
        now=now,
        command_audit=command_audit,
    )

    drifted = False
    try:
        after_config = load_remote_shell_executor_config(path)
        after = path.lstat()
        drifted = (
            _stat_signature(before) != _stat_signature(after)
            or after_config != config
        )
    except RemoteShellExecutionError:
        drifted = True
    if not drifted:
        return report

    fields = report.model_dump(
        mode="python",
        by_alias=False,
        exclude={
            "report_id",
            "content_sha256",
            "status",
            "failure_code",
            "failure_reason",
        },
    )
    return _build_remote_shell_preflight_report(
        **fields,
        status="failed",
        failure_code="local-transport-drift",
        failure_reason=_PREFLIGHT_FAILURE_REASONS[
            "local-transport-drift"
        ],
    )
