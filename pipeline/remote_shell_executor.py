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


class RemoteResultBundleError(ValueError):
    """A downloaded result archive failed content or semantic closure."""


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
_MAX_STATUS_BYTES = 64 * 1024
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
    remote_job_path: str

    @field_validator("remote_job_path")
    @classmethod
    def _remote_path(cls, value: str) -> str:
        return _safe_remote_root(value, label="remote_job_path")


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


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


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
) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
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
    sources: dict[str, tuple[Path, int, str, tuple[int, int, int, int, int]]] = {}
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
    try:
        with zipfile.ZipFile(
            output,
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
        with output.open("rb") as stream:
            os.fsync(stream.fileno())
    except RemoteResultBundleError:
        output.unlink(missing_ok=True)
        raise
    except OSError as exc:
        output.unlink(missing_ok=True)
        raise RemoteResultBundleError(
            "remote result bundle cannot be written"
        ) from exc
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
) -> None:
    try:
        result = path.lstat()
    except OSError as exc:
        raise RemoteShellExecutionError(f"{label} is unavailable") from exc
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
        )
        try:
            key_mode = stat.S_IMODE(config.private_key_path.lstat().st_mode)
        except OSError as exc:
            raise RemoteShellExecutionError(
                "SSH private key cannot be inspected"
            ) from exc
        if key_mode & 0o077:
            raise RemoteShellExecutionError(
                "SSH private key permissions are too broad"
            )
        _verify_known_host(config)

    def _common_options(self, *, scp: bool) -> list[str]:
        port_flag = "-P" if scp else "-p"
        return [
            "-F",
            "/dev/null",
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
            "GlobalKnownHostsFile=/dev/null",
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
        redacted = tuple(
            (
                "<redacted-private-key>"
                if item == str(self.config.private_key_path)
                else item
            )
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
        except (OSError, subprocess.SubprocessError) as exc:
            raise RemoteShellExecutionError(
                f"{phase} transport could not be executed"
            ) from exc
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

    def _context(self, job: ExecutorJobRef) -> _JobContext:
        context = self._jobs.get((job.job_id, job.attempt_id))
        if context is None or context.job != job:
            raise RemoteShellExecutionError(
                "remote job reference identity is unknown or changed"
            )
        return context

    def poll(self, job: ExecutorJobRef) -> ExecutorObservation:
        context = self._context(job)
        worker = (
            f"{self.config.remote_repo_root}/"
            "cloud/remote_training_worker.py"
        )
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
