#!/usr/bin/env python3
"""Remote host worker for one immutable, containerized training attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from pipeline.durable_io import (  # noqa: E402
    DurableIOError,
    _is_linklike,
    atomic_replace,
    first_linklike_path,
    flush_file,
    publish_file_noreplace,
)
from pipeline.production_runtime_evidence import (  # noqa: E402
    ProductionRuntimeEvidenceError,
    load_production_runtime_policy_bytes,
)
from pipeline.remote_shell_executor import (  # noqa: E402
    RemoteContainerLifecycleReceipt,
    RemoteResultBundleError,
    RemoteShellStatus,
    build_production_remote_result_bundle,
    build_remote_result_bundle,
    canonical_container_lifecycle_bytes,
    canonical_remote_status_bytes,
    compute_container_lifecycle_sha256,
    compute_workspace_identity_sha256,
    load_container_lifecycle_receipt,
)

REMOTE_WORKER_VERSION = "1.0.0"


class RemoteWorkerError(ValueError):
    """The immutable remote attempt cannot be initialized or advanced."""


class _LifecycleCollisionError(RemoteWorkerError):
    """Another writer won lifecycle publication; preserve all evidence."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_CONTAINER_PATTERN = re.compile(
    r"^[A-Za-z0-9._/:+-]+@sha256:[0-9a-f]{64}$"
)
_MAX_SPEC_BYTES = 1024 * 1024


class RemoteWorkerSpec(FrozenModel):
    schema_id: Literal["nantai.remote-worker-spec.v3"] = Field(
        default="nantai.remote-worker-spec.v3",
        alias="schema",
        serialization_alias="schema",
    )
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    remote_target_sha256: str = Field(pattern=_SHA256_PATTERN)
    durable_job_ref_sha256: str = Field(pattern=_SHA256_PATTERN)


def _canonical_spec_bytes(spec: RemoteWorkerSpec) -> bytes:
    return (
        json.dumps(
            spec.model_dump(mode="json", by_alias=True),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


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


def _real_directory(path: Path, *, label: str) -> None:
    try:
        redirected = first_linklike_path(Path(path.anchor), path)
        result = path.lstat()
    except OSError as exc:
        raise RemoteWorkerError(f"{label} is unavailable") from exc
    if (
        redirected is not None
        or stat.S_ISLNK(result.st_mode)
        or not stat.S_ISDIR(result.st_mode)
        or _is_linklike(path, observed=result)
    ):
        raise RemoteWorkerError(f"{label} must be a real directory")


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write payload via temp + fsync + atomic_replace.

    Propagates :class:`DurableIOError` so callers can distinguish
    "not published" (safe to write a failure status) from "published but
    durability unconfirmed" (ambiguous — must not overwrite or cleanup).
    """
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        flush_file(temporary)
        atomic_replace(temporary, path)
    except DurableIOError:
        raise
    except OSError as exc:
        raise RemoteWorkerError(
            f"cannot publish remote worker file: {path.name}"
        ) from exc
    finally:
        _best_effort_unlink(temporary)


def _publish_container_id(path: Path, container_id: str) -> None:
    """Publish container-id.txt with no-replace via durable primitive.

    A pre-existing file indicates replay, attempt swap or container
    swap; overwriting it would silently erase the audit trail.
    :class:`DurableIOError` is propagated so callers can distinguish
    "not published" from "published but durability unconfirmed" — the
    latter must not be treated as "file readable therefore durable".
    """
    payload = (container_id + "\n").encode("ascii")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        flush_file(temporary)
        publish_file_noreplace(temporary, path)
    except DurableIOError:
        raise
    except FileExistsError as exc:
        raise RemoteWorkerError(
            "container-id.txt already exists; replay or collision blocked"
        ) from exc
    except OSError as exc:
        raise RemoteWorkerError(
            "container-id.txt publication cannot be opened"
        ) from exc
    finally:
        _best_effort_unlink(temporary)


def _read_stable(path: Path, *, max_bytes: int, label: str) -> bytes:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RemoteWorkerError(f"{label} is missing or link-like")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise RemoteWorkerError(f"{label} size is outside allowed range")
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        after = path.lstat()
    except RemoteWorkerError:
        raise
    except OSError as exc:
        raise RemoteWorkerError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RemoteWorkerError(f"{label} changed while being read")
    if len(raw) > max_bytes:
        raise RemoteWorkerError(f"{label} size is outside allowed range")
    return raw


def _hash_stable(path: Path, *, label: str) -> tuple[int, str]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RemoteWorkerError(f"{label} is missing or link-like")
        digest = hashlib.sha256()
        measured = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                measured += len(chunk)
                digest.update(chunk)
        after = path.lstat()
    except RemoteWorkerError:
        raise
    except OSError as exc:
        raise RemoteWorkerError(f"{label} cannot be hashed") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RemoteWorkerError(f"{label} changed while being hashed")
    return measured, digest.hexdigest()


def _load_spec(job_dir: Path) -> RemoteWorkerSpec:
    raw = _read_stable(
        job_dir / "job-spec.json",
        max_bytes=_MAX_SPEC_BYTES,
        label="remote worker spec",
    )
    try:
        spec = RemoteWorkerSpec.model_validate_json(raw)
    except ValueError as exc:
        raise RemoteWorkerError(
            "remote worker spec validation failed"
        ) from exc
    if raw != _canonical_spec_bytes(spec):
        raise RemoteWorkerError("remote worker spec is not canonical JSON")
    return spec


def initialize_job(
    *,
    job_dir: Path,
    spec: RemoteWorkerSpec,
) -> None:
    job_dir = Path(job_dir).absolute()
    remote_root = job_dir.parent.parent
    _real_directory(remote_root, label="remote jobs root")
    parent = job_dir.parent
    try:
        parent.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise RemoteWorkerError(
            "remote job identity directory cannot be created"
        ) from exc
    _real_directory(parent, label="remote job identity directory")
    try:
        job_dir.mkdir(mode=0o700)
    except OSError as exc:
        raise RemoteWorkerError(
            "remote attempt directory must be absent"
        ) from exc
    _atomic_write(job_dir / "job-spec.json", _canonical_spec_bytes(spec))


def _write_status(job_dir: Path, status: RemoteShellStatus) -> None:
    _atomic_write(
        job_dir / "status.json",
        canonical_remote_status_bytes(status),
    )


def _write_failure_status(
    job_dir: Path,
    spec: RemoteWorkerSpec,
    *,
    exit_code: int,
    stdout_sha: str,
    stderr_sha: str,
) -> None:
    """Publish a terminal failure status without re-deriving identity fields.

    Keeps exception handlers free of identity-bearing strings so that
    failure paths cannot leak the originating bundle SHA via logs or
    stack traces.
    """
    _write_status(
        job_dir,
        RemoteShellStatus(
            job_id=spec.job_id,
            attempt_id=spec.attempt_id,
            request_sha256=spec.request_sha256,
            training_bundle_sha256=spec.training_bundle_sha256,
            runtime_policy_sha256=spec.runtime_policy_sha256,
            state="failed",
            updated_at_utc=datetime.now(UTC),
            exit_code=exit_code,
            stdout_sha256=stdout_sha,
            stderr_sha256=stderr_sha,
        ),
    )


def read_status(job_dir: Path, *, max_bytes: int) -> bytes:
    raw = _read_stable(
        Path(job_dir).absolute() / "status.json",
        max_bytes=max_bytes,
        label="remote status",
    )
    try:
        status = RemoteShellStatus.model_validate_json(raw)
    except ValueError as exc:
        raise RemoteWorkerError("remote status validation failed") from exc
    if raw != canonical_remote_status_bytes(status):
        raise RemoteWorkerError("remote status is not canonical JSON")
    return raw


_MAX_LIFECYCLE_BYTES = 64 * 1024


def build_container_lifecycle_receipt(
    *,
    job_id: str,
    attempt_id: str,
    request_sha256: str,
    training_bundle_sha256: str,
    runtime_policy_sha256: str,
    workspace_path: str,
    container_identity: str,
    container_id: str,
) -> RemoteContainerLifecycleReceipt:
    """Construct a lifecycle receipt with a recomputed ``receipt_sha256``.

    The caller cannot self-report the SHA; it is derived from the signing
    bytes (all fields except ``receipt_sha256``).  A round-trip check
    ensures the computed SHA survives canonical serialisation.
    """
    workspace_identity_sha256 = compute_workspace_identity_sha256(
        job_id=job_id,
        attempt_id=attempt_id,
        workspace_path=workspace_path,
    )
    provisional = RemoteContainerLifecycleReceipt.model_construct(
        job_id=job_id,
        attempt_id=attempt_id,
        request_sha256=request_sha256,
        training_bundle_sha256=training_bundle_sha256,
        runtime_policy_sha256=runtime_policy_sha256,
        workspace_identity_sha256=workspace_identity_sha256,
        container_identity=container_identity,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256="0" * 64,
    )
    digest = compute_container_lifecycle_sha256(provisional)
    receipt = RemoteContainerLifecycleReceipt(
        job_id=job_id,
        attempt_id=attempt_id,
        request_sha256=request_sha256,
        training_bundle_sha256=training_bundle_sha256,
        runtime_policy_sha256=runtime_policy_sha256,
        workspace_identity_sha256=workspace_identity_sha256,
        container_identity=container_identity,
        container_id=container_id,
        transition="container-created-identity-verified",
        receipt_sha256=digest,
    )
    if compute_container_lifecycle_sha256(receipt) != digest:
        raise RemoteWorkerError(
            "container lifecycle receipt sha256 round-trip failed"
        )
    return receipt


def _publish_container_lifecycle(
    job_dir: Path,
    receipt: RemoteContainerLifecycleReceipt,
) -> None:
    """Durably publish one immutable lifecycle receipt without replacement.

    A pre-existing ``container-lifecycle.json`` indicates replay, attempt
    swap or container swap; overwriting it would silently erase the audit
    trail.  ``DurableIOError`` is propagated so callers can distinguish
    "not published" from "published but durability unconfirmed" — the
    latter must not be treated as "file readable therefore durable".
    """
    payload = canonical_container_lifecycle_bytes(receipt)
    temporary = (
        job_dir
        / f".container-lifecycle.{uuid.uuid4().hex}.tmp"
    )
    temporary_created = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        temporary_created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        flush_file(temporary)
        try:
            publish_file_noreplace(
                temporary,
                job_dir / "container-lifecycle.json",
            )
        except FileExistsError as exc:
            raise _LifecycleCollisionError(
                "container-lifecycle.json already exists; "
                "replay or collision blocked"
            ) from exc
    except DurableIOError:
        raise
    except OSError as exc:
        raise RemoteWorkerError(
            "container-lifecycle.json publication cannot be opened"
        ) from exc
    finally:
        if temporary_created:
            _best_effort_unlink(temporary)


def read_lifecycle(job_dir: Path, *, max_bytes: int) -> bytes:
    """Bounded, stable, canonical read of ``container-lifecycle.json``.

    Uses the same stat-snapshot stability check as :func:`read_status`:
    symlink-like files are rejected, the file must be a regular file
    within the allowed size range, and the stat signature must not change
    while being read.  The loaded bytes must survive canonical JSON
    strict equality.
    """
    raw = _read_stable(
        Path(job_dir).absolute() / "container-lifecycle.json",
        max_bytes=max_bytes,
        label="container lifecycle receipt",
    )
    try:
        receipt = load_container_lifecycle_receipt(raw)
    except ValueError as exc:
        raise RemoteWorkerError(
            "container lifecycle validation or canonical check failed"
        ) from exc
    if raw != canonical_container_lifecycle_bytes(receipt):
        raise RemoteWorkerError(
            "container lifecycle is not canonical JSON"
        )
    return raw


def _require_lifecycle_absent(job_dir: Path) -> None:
    lifecycle = job_dir / "container-lifecycle.json"
    try:
        existing = lifecycle.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RemoteWorkerError(
            "container lifecycle namespace cannot be checked"
        ) from exc
    if stat.S_ISLNK(existing.st_mode):
        raise RemoteWorkerError(
            "container lifecycle namespace is link-like"
        )
    raise RemoteWorkerError(
        "container-lifecycle.json already exists; collision blocked"
    )


_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _create_container_argv(
    *,
    runtime: str,
    runtime_name: str,
    repo_root: Path,
    job_dir: Path,
    container_identity: str,
    remote_target_sha256: str,
    durable_job_ref_sha256: str,
) -> list[str]:
    """Build structured docker create argv (no string concatenation)."""
    return [
        runtime,
        "create",
        "--gpus",
        "all",
        "--network",
        "none",
        "--security-opt",
        "no-new-privileges",
        "--shm-size",
        "8g",
        "--mount",
        f"type=bind,src={repo_root},dst=/workspace,readonly",
        "--mount",
        f"type=bind,src={job_dir},dst=/job",
        "--mount",
        (
            "type=bind,"
            f"src={runtime},"
            "dst=/nantai-host/container-runtime,readonly"
        ),
        "--workdir",
        "/workspace",
        "--env",
        "PYTHONPATH=/workspace",
        "--env",
        "WORK=/job/runtime",
        container_identity,
        "python",
        "cloud/production_runtime_entrypoint.py",
        "--job-dir",
        "/job",
        "--repo-root",
        "/workspace",
        "--mounted-container-runtime-path",
        "/nantai-host/container-runtime",
        "--container-runtime",
        runtime_name,
        "--remote-target-sha256",
        remote_target_sha256,
        "--durable-job-ref-sha256",
        durable_job_ref_sha256,
        "--container-identity",
        container_identity,
        "--",
        "/bin/bash",
        "cloud/train_3dgs_nerfstudio.sh",
        "--prepared-bundle",
        "/job/training-job.zip",
        "--container-identity",
        container_identity,
    ]


def _run_container_command(
    argv: list[str],
    *,
    stdout=None,
    stderr=None,
    capture_stdout: bool = False,
) -> subprocess.CompletedProcess:
    """Run a docker subcommand with structured argv."""
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=stdout if not capture_stdout else subprocess.PIPE,
        stderr=stderr if stderr is not None else subprocess.PIPE,
        shell=False,
        check=False,
        text=capture_stdout,
    )


def _create_fresh_container(
    *,
    runtime: str,
    runtime_name: str,
    repo_root: Path,
    job_dir: Path,
    container_identity: str,
    remote_target_sha256: str,
    durable_job_ref_sha256: str,
) -> str:
    """docker create with immutable digest; return full container ID."""
    argv = _create_container_argv(
        runtime=runtime,
        runtime_name=runtime_name,
        repo_root=repo_root,
        job_dir=job_dir,
        container_identity=container_identity,
        remote_target_sha256=remote_target_sha256,
        durable_job_ref_sha256=durable_job_ref_sha256,
    )
    completed = _run_container_command(argv, capture_stdout=True)
    if completed.returncode != 0:
        raise RemoteWorkerError(
            "fresh container could not be created"
        )
    container_id = (completed.stdout or "").strip()
    if not _CONTAINER_ID_PATTERN.fullmatch(container_id):
        raise RemoteWorkerError(
            "container create did not return a full 64-hex ID"
        )
    return container_id


def _resolve_image_id(
    *,
    runtime: str,
    identity: str,
) -> str:
    """Resolve immutable repo@sha256:<manifest> to its content image ID.

    ``docker image inspect <identity> --format {{.Id}}`` returns the
    image's content sha256:64-hex; container .Image must equal this
    exact ID.  Without this resolution, ``inspect {{.Image}}`` would
    accept any sha256:* and a wrong image could pass.
    """
    completed = _run_container_command(
        [runtime, "image", "inspect", "--format", "{{.Id}}", identity],
        capture_stdout=True,
    )
    if completed.returncode != 0:
        raise RemoteWorkerError(
            "image identity could not be resolved"
        )
    image_id = (completed.stdout or "").strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RemoteWorkerError(
            "resolved image identity is not a sha256:64-hex digest"
        )
    return image_id


def _verify_container_digest(
    *,
    runtime: str,
    container_id: str,
    expected_image_id: str,
    expected_identity: str,
) -> None:
    """docker inspect must confirm container .Image == resolved image ID.

    Two equalities are required:

    1. ``.Image`` (the content the container actually runs) must equal
       the image ID resolved from the immutable repo@sha256 digest;
       accepting any ``sha256:*`` here would let a wrong image pass.
    2. ``.Config.Image`` (the configured ref) must equal the immutable
       repo@sha256 identity, so the container was created from the
       intended reference.
    """
    completed = _run_container_command(
        [runtime, "inspect", "--format", "{{.Image}}", container_id],
        capture_stdout=True,
    )
    if completed.returncode != 0:
        raise RemoteWorkerError(
            "container identity could not be inspected"
        )
    image_ref = (completed.stdout or "").strip()
    if image_ref != expected_image_id:
        raise RemoteWorkerError(
            "container inspect digest does not match"
        )
    completed_digests = _run_container_command(
        [
            runtime,
            "inspect",
            "--format",
            "{{json .Config.Image}}",
            container_id,
        ],
        capture_stdout=True,
    )
    if completed_digests.returncode != 0:
        raise RemoteWorkerError(
            "container image digest could not be inspected"
        )
    config_image = (completed_digests.stdout or "").strip().strip('"')
    if config_image != expected_identity:
        raise RemoteWorkerError(
            "container image reference drifted from spec"
        )


def _start_container(
    *,
    runtime: str,
    container_id: str,
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    """docker start -a with stdout/stderr to log files; return exit code."""
    with stdout_path.open("xb") as stdout, stderr_path.open(
        "xb"
    ) as stderr:
        completed = subprocess.run(
            [runtime, "start", "-a", container_id],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            check=False,
        )
    return completed.returncode


def _record_cleanup_observation(
    job_dir: Path,
    *,
    runtime: str,
    container_id: str,
    rm_exit_code: int,
) -> None:
    """Write a bounded, secret-free cleanup observation.

    Cleanup failure does NOT rewrite the terminal status or result
    bundle; it is recorded separately so audit trails reflect the
    real terminal publication plus the cleanup outcome.
    """
    payload = (
        json.dumps(
            {
                "schema": "nantai.remote-cleanup-observation.v1",
                "container_runtime": runtime,
                "container_id_prefix": container_id[:12],
                "rm_exit_code": rm_exit_code,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    _atomic_write(job_dir / "cleanup-observation.json", payload)


def _publication_published(exc: BaseException) -> bool:
    """True if *exc* proves a namespace change whose durability is
    unconfirmed.

    Such a state is ambiguous: the file may already be on disk, so the
    caller must NOT write a failure status (it could overwrite a
    succeeded status) and must NOT remove the container (audit must be
    able to recover the terminal evidence).
    """
    if isinstance(exc, RemoteResultBundleError):
        return exc.published is True
    if isinstance(exc, DurableIOError):
        return exc.published is True
    if isinstance(exc, _LifecycleCollisionError):
        return True
    return False


def _safe_record_cleanup_observation(
    job_dir: Path,
    *,
    runtime: str,
    container_id: str | None,
    rm_exit_code: int,
) -> bool:
    """Record cleanup observation without propagating publication faults.

    A failure here must NOT re-enter the outer handler, rewrite the
    terminal result, or trigger a second ``rm``.  Swallow the error so
    the terminal state remains intact for audit recovery.  Returns
    ``True`` if the observation was published, ``False`` if it failed.
    """
    if container_id is None:
        return True
    try:
        _record_cleanup_observation(
            job_dir,
            runtime=runtime,
            container_id=container_id,
            rm_exit_code=rm_exit_code,
        )
        return True
    except (DurableIOError, OSError, RemoteWorkerError):
        return False


def _remove_container(
    *,
    runtime: str,
    container_id: str,
) -> int:
    """docker rm only after durable publication; return rm exit code."""
    completed = _run_container_command(
        [runtime, "rm", "-f", container_id],
        capture_stdout=False,
    )
    return completed.returncode


def _materialize_runtime_evidence(
    *,
    job_dir: Path,
    result_root: Path,
    container_id: str,
) -> None:
    source_root = job_dir / "production-runtime"
    target_root = result_root / "production-runtime"
    expected_names = {
        "measurement.json",
        "policy.json",
        "decision.json",
    }
    try:
        redirected_source = first_linklike_path(
            Path(source_root.anchor),
            source_root,
        )
        source_before = source_root.lstat()
        if (
            redirected_source is not None
            or stat.S_ISLNK(source_before.st_mode)
            or not stat.S_ISDIR(source_before.st_mode)
            or _is_linklike(source_root, observed=source_before)
        ):
            raise RemoteWorkerError(
                "production runtime evidence root is link-like"
            )
        children = tuple(source_root.iterdir())
        if {child.name for child in children} != expected_names:
            raise RemoteWorkerError(
                "production runtime evidence file set is incomplete"
            )
        payloads = {
            child.name: _read_stable(
                child,
                max_bytes=1024 * 1024,
                label=f"production runtime evidence {child.name}",
            )
            for child in children
        }
        source_after = source_root.lstat()
        if _stat_signature(source_before) != _stat_signature(
            source_after
        ):
            raise RemoteWorkerError(
                "production runtime evidence root changed while read"
            )
        target_root.mkdir()
        for name in sorted(payloads):
            _write_result_member_noreplace(
                target_root / name,
                payloads[name],
                label=f"production runtime evidence {name}",
            )
        _write_result_member_noreplace(
            result_root / "container-id.txt",
            (container_id + "\n").encode("ascii"),
            label="production result container identity",
        )
    except RemoteWorkerError:
        raise
    except OSError as exc:
        raise RemoteWorkerError(
            "production runtime evidence cannot be materialized"
        ) from exc


def _write_result_member_noreplace(
    path: Path,
    payload: bytes,
    *,
    label: str,
) -> None:
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
        flush_file(path)
    except OSError as exc:
        raise RemoteWorkerError(
            f"{label} cannot be materialized without replacement"
        ) from exc


def run_job(
    *,
    job_dir: Path,
    repo_root: Path,
    container_identity: str,
    container_runtime: str,
) -> int:
    job_dir = Path(job_dir).absolute()
    repo_root = Path(repo_root).absolute()
    _real_directory(job_dir, label="remote attempt directory")
    _real_directory(repo_root, label="remote repository")
    if not _CONTAINER_PATTERN.fullmatch(container_identity):
        raise RemoteWorkerError(
            "container identity must be an immutable digest"
        )
    if container_runtime not in {"docker", "podman"}:
        raise RemoteWorkerError("unsupported container runtime")
    try:
        _require_lifecycle_absent(job_dir)
    except RemoteWorkerError:
        return 75
    spec = _load_spec(job_dir)
    bundle_path = job_dir / "training-job.zip"
    _, bundle_sha = _hash_stable(
        bundle_path,
        label="remote training bundle",
    )
    if bundle_sha != spec.training_bundle_sha256:
        raise RemoteWorkerError(
            "remote training bundle sha256 differs from job spec"
        )
    _write_status(
        job_dir,
        RemoteShellStatus(
            job_id=spec.job_id,
            attempt_id=spec.attempt_id,
            request_sha256=spec.request_sha256,
            training_bundle_sha256=spec.training_bundle_sha256,
            runtime_policy_sha256=spec.runtime_policy_sha256,
            state="running",
            updated_at_utc=datetime.now(UTC),
        ),
    )
    stdout_path = job_dir / "worker.stdout.log"
    stderr_path = job_dir / "worker.stderr.log"
    container_id: str | None = None
    container_started = False
    try:
        try:
            runtime_policy = load_production_runtime_policy_bytes(
                _read_stable(
                    job_dir / "production-runtime-policy.json",
                    max_bytes=1024 * 1024,
                    label="production runtime policy",
                )
            )
        except ProductionRuntimeEvidenceError as exc:
            raise RemoteWorkerError(
                "production runtime policy is invalid"
            ) from exc
        if (
            runtime_policy.content_sha256
            != spec.runtime_policy_sha256
            or runtime_policy.expected_container_identity
            != container_identity
            or runtime_policy.expected_remote_target_sha256
            != spec.remote_target_sha256
        ):
            raise RemoteWorkerError(
                "production runtime policy differs from job spec"
            )
        _, checker_sha256 = _hash_stable(
            repo_root / "cloud" / "production_runtime_entrypoint.py",
            label="production runtime entrypoint",
        )
        if checker_sha256 != runtime_policy.expected_checker_sha256:
            raise RemoteWorkerError(
                "production runtime entrypoint differs from policy"
            )
        resolved_runtime = shutil.which(container_runtime)
        if not resolved_runtime or not Path(resolved_runtime).is_absolute():
            raise RemoteWorkerError(
                "container runtime did not resolve to an absolute path"
            )
        runtime_path = Path(resolved_runtime)
        _, runtime_sha256 = _hash_stable(
            runtime_path,
            label="container runtime executable",
        )
        if (
            runtime_sha256
            != runtime_policy.expected_container_runtime_sha256
        ):
            raise RemoteWorkerError(
                "container runtime executable differs from policy"
            )
        runtime_command = str(runtime_path)
        expected_image_id = _resolve_image_id(
            runtime=runtime_command,
            identity=container_identity,
        )
        container_id = _create_fresh_container(
            runtime=runtime_command,
            runtime_name=container_runtime,
            repo_root=repo_root,
            job_dir=job_dir,
            container_identity=container_identity,
            remote_target_sha256=spec.remote_target_sha256,
            durable_job_ref_sha256=spec.durable_job_ref_sha256,
        )
        _publish_container_id(
            job_dir / "container-id.txt",
            container_id,
        )
        _verify_container_digest(
            runtime=runtime_command,
            container_id=container_id,
            expected_image_id=expected_image_id,
            expected_identity=container_identity,
        )
        lifecycle_receipt = build_container_lifecycle_receipt(
            job_id=spec.job_id,
            attempt_id=spec.attempt_id,
            request_sha256=spec.request_sha256,
            training_bundle_sha256=spec.training_bundle_sha256,
            runtime_policy_sha256=spec.runtime_policy_sha256,
            workspace_path=str(job_dir),
            container_identity=container_identity,
            container_id=container_id,
        )
        _publish_container_lifecycle(job_dir, lifecycle_receipt)
        exit_code = _start_container(
            runtime=runtime_command,
            container_id=container_id,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        container_started = True
        _, stdout_sha = _hash_stable(
            stdout_path,
            label="worker stdout log",
        )
        _, stderr_sha = _hash_stable(
            stderr_path,
            label="worker stderr log",
        )
        if exit_code != 0:
            # Training failed; write failure status then cleanup.
            # If failure-status publication is ambiguous (published=True),
            # do NOT cleanup or record an observation — preserve container
            # and terminal evidence for audit recovery.
            try:
                _write_failure_status(
                    job_dir,
                    spec,
                    exit_code=exit_code,
                    stdout_sha=stdout_sha,
                    stderr_sha=stderr_sha,
                )
            except DurableIOError as status_exc:
                if _publication_published(status_exc):
                    return 75
                # Not published — status could not be proven; preserve
                # the container and return without cleanup observation.
                return 75
            rm_exit = -1
            if container_id is not None:
                rm_exit = _remove_container(
                    runtime=runtime_command,
                    container_id=container_id,
                )
            observation_published = _safe_record_cleanup_observation(
                job_dir,
                runtime=container_runtime,
                container_id=container_id,
                rm_exit_code=rm_exit,
            )
            if not observation_published:
                return 75
            return exit_code
        result_root = (
            job_dir / "runtime" / "production-run" / "result"
        )
        shutil.copyfile(
            stdout_path,
            result_root / "worker.stdout.log",
        )
        shutil.copyfile(
            stderr_path,
            result_root / "worker.stderr.log",
        )
        # Terminal publication: result bundle first, then succeeded status.
        # If EITHER fails with published=True, the state is ambiguous:
        # do NOT write a failure status (it may overwrite a succeeded
        # status already on disk), and do NOT remove the container.
        try:
            try:
                (job_dir / "production-runtime").lstat()
                has_runtime_evidence = True
            except FileNotFoundError:
                has_runtime_evidence = False
            except OSError as exc:
                raise RemoteWorkerError(
                    "production runtime evidence namespace is ambiguous"
                ) from exc
            if has_runtime_evidence:
                _materialize_runtime_evidence(
                    job_dir=job_dir,
                    result_root=result_root,
                    container_id=container_id,
                )
                verified = build_production_remote_result_bundle(
                    result_root=result_root,
                    output_path=job_dir / "result-bundle.zip",
                    job_id=spec.job_id,
                    attempt_id=spec.attempt_id,
                    request_sha256=spec.request_sha256,
                    training_bundle_sha256=(
                        spec.training_bundle_sha256
                    ),
                    container_instance_id=container_id,
                    container_identity=container_identity,
                    remote_target_sha256=spec.remote_target_sha256,
                    durable_job_ref_sha256=(
                        spec.durable_job_ref_sha256
                    ),
                    workspace_identity_sha256=(
                        lifecycle_receipt.workspace_identity_sha256
                    ),
                )
            else:
                verified = build_remote_result_bundle(
                    result_root=result_root,
                    output_path=job_dir / "result-bundle.zip",
                    job_id=spec.job_id,
                    attempt_id=spec.attempt_id,
                    request_sha256=spec.request_sha256,
                    training_bundle_sha256=(
                        spec.training_bundle_sha256
                    ),
                    container_identity=container_identity,
                )
            _write_status(
                job_dir,
                RemoteShellStatus(
                    job_id=spec.job_id,
                    attempt_id=spec.attempt_id,
                    request_sha256=spec.request_sha256,
                    training_bundle_sha256=(
                        spec.training_bundle_sha256
                    ),
                    runtime_policy_sha256=spec.runtime_policy_sha256,
                    state="succeeded",
                    updated_at_utc=datetime.now(UTC),
                    exit_code=0,
                    stdout_sha256=stdout_sha,
                    stderr_sha256=stderr_sha,
                    result_bundle_sha256=verified.bundle_sha256,
                    result_bundle_size_bytes=verified.byte_length,
                ),
            )
        except (RemoteResultBundleError, DurableIOError) as exc:
            if _publication_published(exc):
                # Ambiguous: a namespace change happened but durability
                # is unconfirmed.  Do NOT write a failure status (it may
                # overwrite a succeeded status already on disk), do NOT
                # remove the container, and do NOT record a cleanup
                # observation — touching nothing preserves the terminal
                # evidence for audit recovery.
                return 75
            # Not published — safe to write a failure status.  The
            # container exited zero but results could not be proven, so
            # preserve it for audit (docker cp / inspect) rather than
            # removing; no cleanup observation is emitted because no
            # removal was attempted.
            _write_failure_status(
                job_dir,
                spec,
                exit_code=75,
                stdout_sha=stdout_sha,
                stderr_sha=stderr_sha,
            )
            return 75
        # Terminal publication proven durable — cleanup permitted.
        rm_exit = -1
        if container_id is not None:
            rm_exit = _remove_container(
                runtime=runtime_command,
                container_id=container_id,
            )
        observation_published = _safe_record_cleanup_observation(
            job_dir,
            runtime=container_runtime,
            container_id=container_id,
            rm_exit_code=rm_exit,
        )
        # Terminal status/result remain succeeded; a cleanup-observation
        # publication failure must not rewrite them or trigger a second
        # rm, but it must surface as non-zero so the audit gap is visible.
        if not observation_published:
            return 75
        return 0
    except (
        OSError,
        RemoteResultBundleError,
        DurableIOError,
        RemoteWorkerError,
    ) as exc:
        if _publication_published(exc):
            # Ambiguous pre-terminal publication (e.g. container-id
            # namespace published but durability unconfirmed).  Do NOT
            # write a failure status, do NOT remove the container, and
            # do NOT record a cleanup observation — touching nothing
            # preserves the terminal evidence for audit recovery.
            return 75
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
        with stderr_path.open("ab") as stream:
            stream.write(
                f"remote worker failed: {type(exc).__name__}\n".encode(
                    "ascii",
                )
            )
        _, stdout_sha = _hash_stable(
            stdout_path,
            label="worker stdout log",
        )
        _, stderr_sha = _hash_stable(
            stderr_path,
            label="worker stderr log",
        )
        try:
            _write_failure_status(
                job_dir,
                spec,
                exit_code=75,
                stdout_sha=stdout_sha,
                stderr_sha=stderr_sha,
            )
        except DurableIOError as status_exc:
            if _publication_published(status_exc):
                # Ambiguous: status namespace may be on disk; preserve
                # everything for audit recovery.
                return 75
            # Not published — preserve the container and return without
            # cleanup observation.
            return 75
        # If the container has already started, preserve it for audit
        # (docker cp / inspect can recover post-start evidence).  Only
        # remove containers that were created but never started, where
        # there is no runtime evidence to preserve.
        if container_started:
            return 75
        rm_exit = -1
        if container_id is not None:
            rm_exit = _remove_container(
                runtime=runtime_command,
                container_id=container_id,
            )
        observation_published = _safe_record_cleanup_observation(
            job_dir,
            runtime=container_runtime,
            container_id=container_id,
            rm_exit_code=rm_exit,
        )
        if not observation_published:
            return 75
        return 75


def start_job(
    *,
    job_dir: Path,
    repo_root: Path,
    container_identity: str,
    container_runtime: str,
    detach: bool,
) -> int:
    job_dir = Path(job_dir).absolute()
    _load_spec(job_dir)
    lock_path = job_dir / "start.lock"
    try:
        descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(descriptor)
    except OSError as exc:
        raise RemoteWorkerError(
            "remote attempt was already started or is ambiguous"
        ) from exc
    if not detach:
        return run_job(
            job_dir=job_dir,
            repo_root=repo_root,
            container_identity=container_identity,
            container_runtime=container_runtime,
        )
    argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--job-dir",
        str(job_dir),
        "--repo-root",
        str(Path(repo_root).absolute()),
        "--container-identity",
        container_identity,
        "--container-runtime",
        container_runtime,
    ]
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise RemoteWorkerError(
            "detached remote worker could not start"
        ) from exc
    return 0


def _add_start_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--container-identity", required=True)
    parser.add_argument(
        "--container-runtime",
        choices=("docker", "podman"),
        default="docker",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one immutable remote Nantai training attempt",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=REMOTE_WORKER_VERSION,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--job-dir", type=Path, required=True)
    init.add_argument("--job-id", required=True)
    init.add_argument("--attempt-id", required=True)
    init.add_argument("--request-sha256", required=True)
    init.add_argument("--training-bundle-sha256", required=True)
    init.add_argument("--runtime-policy-sha256", required=True)
    init.add_argument("--remote-target-sha256", required=True)
    init.add_argument("--durable-job-ref-sha256", required=True)
    start = subparsers.add_parser("start")
    _add_start_arguments(start)
    start.add_argument("--detach", action="store_true")
    run = subparsers.add_parser("run")
    _add_start_arguments(run)
    status = subparsers.add_parser("status")
    status.add_argument("--job-dir", type=Path, required=True)
    status.add_argument("--max-bytes", type=int, required=True)
    lifecycle = subparsers.add_parser("lifecycle")
    lifecycle.add_argument("--job-dir", type=Path, required=True)
    lifecycle.add_argument("--max-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize_job(
                job_dir=args.job_dir,
                spec=RemoteWorkerSpec(
                    job_id=args.job_id,
                    attempt_id=args.attempt_id,
                    request_sha256=args.request_sha256,
                    training_bundle_sha256=(
                        args.training_bundle_sha256
                    ),
                    runtime_policy_sha256=args.runtime_policy_sha256,
                    remote_target_sha256=args.remote_target_sha256,
                    durable_job_ref_sha256=args.durable_job_ref_sha256,
                ),
            )
            return 0
        if args.command == "status":
            if args.max_bytes <= 0 or args.max_bytes > 1024 * 1024:
                raise RemoteWorkerError("status byte limit is invalid")
            sys.stdout.buffer.write(
                read_status(args.job_dir, max_bytes=args.max_bytes)
            )
            return 0
        if args.command == "lifecycle":
            if args.max_bytes <= 0 or args.max_bytes > 1024 * 1024:
                raise RemoteWorkerError("lifecycle byte limit is invalid")
            sys.stdout.buffer.write(
                read_lifecycle(args.job_dir, max_bytes=args.max_bytes)
            )
            return 0
        if args.command == "start":
            return start_job(
                job_dir=args.job_dir,
                repo_root=args.repo_root,
                container_identity=args.container_identity,
                container_runtime=args.container_runtime,
                detach=args.detach,
            )
        return run_job(
            job_dir=args.job_dir,
            repo_root=args.repo_root,
            container_identity=args.container_identity,
            container_runtime=args.container_runtime,
        )
    except (RemoteWorkerError, ValueError) as exc:
        print(f"remote worker error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
