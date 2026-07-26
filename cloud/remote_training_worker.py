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

from pipeline.remote_shell_executor import (  # noqa: E402
    RemoteResultBundleError,
    RemoteShellStatus,
    build_remote_result_bundle,
    canonical_remote_status_bytes,
)


class RemoteWorkerError(ValueError):
    """The immutable remote attempt cannot be initialized or advanced."""


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
    schema_id: Literal["nantai.remote-worker-spec.v1"] = Field(
        default="nantai.remote-worker-spec.v1",
        alias="schema",
        serialization_alias="schema",
    )
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)


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
        result = path.lstat()
    except OSError as exc:
        raise RemoteWorkerError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise RemoteWorkerError(f"{label} must be a real directory")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise RemoteWorkerError(
            f"cannot publish remote worker file: {path.name}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _read_stable(path: Path, *, max_bytes: int, label: str) -> bytes:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise RemoteWorkerError(f"{label} is missing or link-like")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise RemoteWorkerError(f"{label} size is outside allowed range")
        raw = path.read_bytes()
        after = path.lstat()
    except RemoteWorkerError:
        raise
    except OSError as exc:
        raise RemoteWorkerError(f"{label} cannot be read") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise RemoteWorkerError(f"{label} changed while being read")
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


def _container_argv(
    *,
    runtime: str,
    repo_root: Path,
    job_dir: Path,
    container_identity: str,
) -> list[str]:
    return [
        runtime,
        "run",
        "--rm",
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
        "--workdir",
        "/workspace",
        "--env",
        "WORK=/job/runtime",
        container_identity,
        "bash",
        "cloud/train_3dgs_nerfstudio.sh",
        "--prepared-bundle",
        "/job/training-job.zip",
        "--container-identity",
        container_identity,
    ]


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
            state="running",
            updated_at_utc=datetime.now(UTC),
        ),
    )
    stdout_path = job_dir / "worker.stdout.log"
    stderr_path = job_dir / "worker.stderr.log"
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            completed = subprocess.run(
                _container_argv(
                    runtime=container_runtime,
                    repo_root=repo_root,
                    job_dir=job_dir,
                    container_identity=container_identity,
                ),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                check=False,
            )
        _, stdout_sha = _hash_stable(
            stdout_path,
            label="worker stdout log",
        )
        _, stderr_sha = _hash_stable(
            stderr_path,
            label="worker stderr log",
        )
        if completed.returncode != 0:
            _write_status(
                job_dir,
                RemoteShellStatus(
                    job_id=spec.job_id,
                    attempt_id=spec.attempt_id,
                    request_sha256=spec.request_sha256,
                    training_bundle_sha256=spec.training_bundle_sha256,
                    state="failed",
                    updated_at_utc=datetime.now(UTC),
                    exit_code=completed.returncode,
                    stdout_sha256=stdout_sha,
                    stderr_sha256=stderr_sha,
                ),
            )
            return completed.returncode
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
        verified = build_remote_result_bundle(
            result_root=result_root,
            output_path=job_dir / "result-bundle.zip",
            job_id=spec.job_id,
            attempt_id=spec.attempt_id,
            request_sha256=spec.request_sha256,
            training_bundle_sha256=spec.training_bundle_sha256,
            container_identity=container_identity,
        )
        _write_status(
            job_dir,
            RemoteShellStatus(
                job_id=spec.job_id,
                attempt_id=spec.attempt_id,
                request_sha256=spec.request_sha256,
                training_bundle_sha256=spec.training_bundle_sha256,
                state="succeeded",
                updated_at_utc=datetime.now(UTC),
                exit_code=0,
                stdout_sha256=stdout_sha,
                stderr_sha256=stderr_sha,
                result_bundle_sha256=verified.bundle_sha256,
                result_bundle_size_bytes=verified.byte_length,
            ),
        )
        return 0
    except (OSError, RemoteResultBundleError, RemoteWorkerError) as exc:
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
        _write_status(
            job_dir,
            RemoteShellStatus(
                job_id=spec.job_id,
                attempt_id=spec.attempt_id,
                request_sha256=spec.request_sha256,
                training_bundle_sha256=spec.training_bundle_sha256,
                state="failed",
                updated_at_utc=datetime.now(UTC),
                exit_code=75,
                stdout_sha256=stdout_sha,
                stderr_sha256=stderr_sha,
            ),
        )
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
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--job-dir", type=Path, required=True)
    init.add_argument("--job-id", required=True)
    init.add_argument("--attempt-id", required=True)
    init.add_argument("--request-sha256", required=True)
    init.add_argument("--training-bundle-sha256", required=True)
    start = subparsers.add_parser("start")
    _add_start_arguments(start)
    start.add_argument("--detach", action="store_true")
    run = subparsers.add_parser("run")
    _add_start_arguments(run)
    status = subparsers.add_parser("status")
    status.add_argument("--job-dir", type=Path, required=True)
    status.add_argument("--max-bytes", type=int, required=True)
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
