#!/usr/bin/env python3
"""Fresh-container production clearance followed by training.

This file is the container entrypoint.  It observes the fixed G2 runtime
contract, durably publishes measurement/policy/decision, and only then
replaces itself with the training process when the derived decision is
``accepted``.  A rejected or ambiguous clearance can never reach training.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.durable_io import (
    DurableIOError,
    flush_file,
    publish_file_noreplace,
)
from pipeline.production_runtime_evidence import (
    ExecutableSnapshot,
    ExecutionEnvironmentObservation,
    GpuRuntimeObservation,
    ProbeObservationBinding,
    ProductionRuntimeEvidenceError,
    ProductionRuntimeMeasurement,
    StableExecutableObservation,
    TrainingCliObservation,
    canonical_production_runtime_decision_bytes,
    canonical_production_runtime_measurement_bytes,
    decide_production_runtime,
    execution_environment_sha256,
    load_production_runtime_policy_bytes,
    training_cli_schema_sha256,
    verify_production_runtime_decision,
)
from pipeline.remote_shell_executor import (
    canonical_container_lifecycle_bytes,
    load_container_lifecycle_receipt,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_OPTION_RE = re.compile(rb"--[a-z0-9][a-z0-9.-]*")
_MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MAX_PROBE_BYTES = 1024 * 1024

_PROBE_DEFINITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "container-identity",
        ("lifecycle-v2", "container-id.txt", "policy-v1"),
    ),
    (
        "cuda-runtime",
        ("python", "-c", "print(torch.version.cuda)"),
    ),
    (
        "gpu-device",
        (
            "nvidia-smi",
            "--query-gpu=uuid,name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ),
    ),
    (
        "nerfstudio-version",
        ("python", "-c", "print(metadata.version('nerfstudio'))"),
    ),
    (
        "python-runtime",
        ("python", "-c", "print(platform.python_version())"),
    ),
    (
        "training-cli-schema",
        ("ns-train", "splatfacto", "-h"),
    ),
)
_EXECUTABLE_DEFINITIONS = {
    role: ("stable-file-sha256-v1", role)
    for role in (
        "checker",
        "container-runtime",
        "ns-train",
        "nvidia-smi",
        "python",
        "worker",
    )
}


class ProductionRuntimeEntrypointError(ValueError):
    """Fresh-container clearance cannot be proven."""


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


def _definition_sha(payload: tuple[str, ...]) -> str:
    return hashlib.sha256(
        _canonical_json_bytes({"definition": list(payload)})
    ).hexdigest()


def fixed_production_probe_set_sha256() -> str:
    """Content identity of the only supported production probe registry."""
    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "probes": [
                    {
                        "definition_sha256": _definition_sha(definition),
                        "probe_id": probe_id,
                    }
                    for probe_id, definition in _PROBE_DEFINITIONS
                ]
            }
        )
    ).hexdigest()


def _stat_signature(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_stable(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > maximum_bytes
        ):
            raise ProductionRuntimeEntrypointError(
                f"{label} must be a bounded regular file"
            )
        digest_payload = bytearray()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_payload.extend(chunk)
                if len(digest_payload) > maximum_bytes:
                    raise ProductionRuntimeEntrypointError(
                        f"{label} exceeds its byte limit"
                    )
        after = path.lstat()
    except ProductionRuntimeEntrypointError:
        raise
    except OSError as exc:
        raise ProductionRuntimeEntrypointError(
            f"{label} cannot be read"
        ) from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(digest_payload) != before.st_size
    ):
        raise ProductionRuntimeEntrypointError(
            f"{label} changed while being read"
        )
    return bytes(digest_payload)


def _snapshot(
    path: Path,
    *,
    logical_path: Callable[[Path], str],
) -> ExecutableSnapshot:
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > _MAX_EXECUTABLE_BYTES
        ):
            raise ProductionRuntimeEntrypointError(
                "runtime role target must be a bounded regular file"
            )
        digest = hashlib.sha256()
        measured = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                measured += len(chunk)
        after = path.lstat()
    except ProductionRuntimeEntrypointError:
        raise
    except OSError as exc:
        raise ProductionRuntimeEntrypointError(
            "runtime role target cannot be read"
        ) from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or measured != before.st_size
    ):
        raise ProductionRuntimeEntrypointError(
            "runtime role target changed while being read"
        )
    try:
        return ExecutableSnapshot(
            resolved_path=logical_path(path),
            byte_length=measured,
            sha256=digest.hexdigest(),
            device=before.st_dev,
            inode=before.st_ino,
            mode=before.st_mode,
            mtime_ns=before.st_mtime_ns,
            ctime_ns=before.st_ctime_ns,
        )
    except ValueError as exc:
        raise ProductionRuntimeEntrypointError(
            "runtime role snapshot is invalid"
        ) from exc


def _run_bounded(
    argv: list[str],
    *,
    run_command: Callable[..., subprocess.CompletedProcess],
    label: str,
) -> tuple[bytes, bytes]:
    try:
        completed = run_command(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionRuntimeEntrypointError(
            f"{label} could not be executed"
        ) from exc
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8", errors="strict")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8", errors="strict")
    if (
        completed.returncode != 0
        or len(stdout) > _MAX_PROBE_BYTES
        or len(stderr) > _MAX_PROBE_BYTES
    ):
        raise ProductionRuntimeEntrypointError(
            f"{label} did not produce bounded success"
        )
    return stdout, stderr


def _safe_ascii_line(payload: bytes, *, label: str) -> str:
    try:
        value = payload.decode("ascii").strip()
    except UnicodeError as exc:
        raise ProductionRuntimeEntrypointError(
            f"{label} is not safe ASCII"
        ) from exc
    if (
        not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127
               for character in value)
    ):
        raise ProductionRuntimeEntrypointError(f"{label} is invalid")
    return value


def _resolve_role(
    name: str,
    *,
    which: Callable[[str], str | None],
) -> Path:
    resolved = which(name)
    if not resolved:
        raise ProductionRuntimeEntrypointError(
            f"required runtime role {name} is unavailable"
        )
    path = Path(resolved)
    if not path.is_absolute():
        raise ProductionRuntimeEntrypointError(
            f"required runtime role {name} did not resolve absolutely"
        )
    return path


def _ensure_real_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise ProductionRuntimeEntrypointError(
                    "evidence root has no real existing ancestor"
                ) from None
            cursor = parent
            continue
        except OSError as exc:
            raise ProductionRuntimeEntrypointError(
                "evidence directory boundary cannot be inspected"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ProductionRuntimeEntrypointError(
                "evidence directory boundary is link-like or not a directory"
            )
        break
    for candidate in reversed(missing):
        try:
            candidate.mkdir()
        except OSError as exc:
            raise ProductionRuntimeEntrypointError(
                "evidence directory cannot be created"
            ) from exc


def _publish_noreplace(path: Path, payload: bytes) -> None:
    staging = path.parent / f".{path.name}.{uuid.uuid4().hex}.staging"
    try:
        with staging.open("xb") as stream:
            stream.write(payload)
        flush_file(staging)
        publish_file_noreplace(staging, path)
    except (OSError, DurableIOError) as exc:
        raise ProductionRuntimeEntrypointError(
            "runtime evidence publication is ambiguous"
        ) from exc
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass


def _load_policy_and_lifecycle(
    *,
    job_dir: Path,
    expected_container_identity: str,
):
    policy_bytes = _read_stable(
        job_dir / "production-runtime-policy.json",
        label="production runtime policy",
        maximum_bytes=_MAX_EVIDENCE_BYTES,
    )
    try:
        policy = load_production_runtime_policy_bytes(policy_bytes)
    except ProductionRuntimeEvidenceError as exc:
        raise ProductionRuntimeEntrypointError(
            "production runtime policy is invalid"
        ) from exc
    lifecycle_bytes = _read_stable(
        job_dir / "container-lifecycle.json",
        label="container lifecycle",
        maximum_bytes=_MAX_EVIDENCE_BYTES,
    )
    try:
        lifecycle = load_container_lifecycle_receipt(lifecycle_bytes)
    except ValueError as exc:
        raise ProductionRuntimeEntrypointError(
            "container lifecycle is invalid"
        ) from exc
    if lifecycle_bytes != canonical_container_lifecycle_bytes(lifecycle):
        raise ProductionRuntimeEntrypointError(
            "container lifecycle is not canonical"
        )
    container_id_bytes = _read_stable(
        job_dir / "container-id.txt",
        label="container instance identity",
        maximum_bytes=128,
    )
    expected_id_bytes = (lifecycle.container_id + "\n").encode("ascii")
    if (
        container_id_bytes != expected_id_bytes
        or lifecycle.container_identity != expected_container_identity
        or policy.expected_container_identity
        != expected_container_identity
        or lifecycle.runtime_policy_sha256 != policy.content_sha256
    ):
        raise ProductionRuntimeEntrypointError(
            "container instance identity differs from lifecycle or policy"
        )
    return policy, policy_bytes, lifecycle


def _gpu_observation(
    payload: bytes,
    *,
    cuda_runtime_version: str,
    nvidia_smi_sha256: str,
) -> GpuRuntimeObservation:
    try:
        rows = list(csv.reader(payload.decode("ascii").splitlines()))
    except (UnicodeError, csv.Error) as exc:
        raise ProductionRuntimeEntrypointError(
            "GPU observation is invalid"
        ) from exc
    if len(rows) != 1 or len(rows[0]) != 5:
        raise ProductionRuntimeEntrypointError(
            "GPU observation must identify exactly one device"
        )
    uuid_value, name, memory, driver, capability = (
        field.strip() for field in rows[0]
    )
    try:
        memory_mib = int(memory)
        return GpuRuntimeObservation(
            uuid=uuid_value,
            name=name,
            memory_total_mib=memory_mib,
            driver_version=driver,
            cuda_runtime_version=cuda_runtime_version,
            compute_capability=capability,
            nvidia_smi_executable_sha256=nvidia_smi_sha256,
        )
    except ValueError as exc:
        raise ProductionRuntimeEntrypointError(
            "GPU observation fields are invalid"
        ) from exc


def run_clearance_and_train(
    *,
    job_dir: Path,
    repo_root: Path,
    mounted_container_runtime_path: Path,
    container_runtime: str,
    remote_target_sha256: str,
    durable_job_ref_sha256: str,
    expected_container_identity: str,
    training_argv: tuple[str, ...],
    checker_path: Path | None = None,
    worker_path: Path | None = None,
    python_executable: Path | None = None,
    run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    exec_command: Callable[[str, list[str]], Any] = os.execvp,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    logical_path: Callable[[Path], str] = (
        lambda path: path.resolve().as_posix()
    ),
) -> int:
    """Measure the fresh container and execute training only if accepted."""
    job_dir = Path(job_dir).absolute()
    repo_root = Path(repo_root).absolute()
    if (
        container_runtime not in {"docker", "podman"}
        or _SHA256_RE.fullmatch(remote_target_sha256) is None
        or _SHA256_RE.fullmatch(durable_job_ref_sha256) is None
        or _CONTAINER_RE.fullmatch(expected_container_identity) is None
        or not training_argv
        or any(not value or "\x00" in value for value in training_argv)
    ):
        raise ProductionRuntimeEntrypointError(
            "entrypoint identity arguments are invalid"
        )
    policy, policy_bytes, lifecycle = _load_policy_and_lifecycle(
        job_dir=job_dir,
        expected_container_identity=expected_container_identity,
    )
    checker = Path(checker_path or Path(__file__).resolve())
    worker = Path(
        worker_path or repo_root / "cloud" / "remote_training_worker.py"
    )
    runtime = Path(mounted_container_runtime_path)
    python = Path(python_executable or sys.executable)
    ns_train = _resolve_role("ns-train", which=which)
    nvidia_smi = _resolve_role("nvidia-smi", which=which)
    git = _resolve_role("git", which=which)
    role_paths = {
        "checker": checker,
        "container-runtime": runtime,
        "ns-train": ns_train,
        "nvidia-smi": nvidia_smi,
        "python": python,
        "worker": worker,
    }
    before = {
        role: _snapshot(path, logical_path=logical_path)
        for role, path in role_paths.items()
    }

    commit_stdout, _ = _run_bounded(
        [str(git), "rev-parse", "HEAD"],
        run_command=run_command,
        label="exact commit probe",
    )
    exact_commit = _safe_ascii_line(
        commit_stdout,
        label="exact commit",
    )
    dirty_stdout, _ = _run_bounded(
        [
            str(git),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        run_command=run_command,
        label="clean tree probe",
    )
    if dirty_stdout:
        raise ProductionRuntimeEntrypointError(
            "fresh-container repository is not clean"
        )

    container_stdout = _canonical_json_bytes(
        {
            "container_id": lifecycle.container_id,
            "container_identity": lifecycle.container_identity,
        }
    )
    probe_outputs: dict[str, tuple[bytes, bytes]] = {
        "container-identity": (container_stdout, b""),
    }
    cuda_stdout, cuda_stderr = _run_bounded(
        [
            str(python),
            "-c",
            "import torch; print(torch.version.cuda)",
        ],
        run_command=run_command,
        label="CUDA runtime probe",
    )
    probe_outputs["cuda-runtime"] = (cuda_stdout, cuda_stderr)
    gpu_stdout, gpu_stderr = _run_bounded(
        [
            str(nvidia_smi),
            "--query-gpu=uuid,name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ],
        run_command=run_command,
        label="GPU device probe",
    )
    probe_outputs["gpu-device"] = (gpu_stdout, gpu_stderr)
    nerfstudio_stdout, nerfstudio_stderr = _run_bounded(
        [
            str(python),
            "-c",
            "import importlib.metadata as m; print(m.version('nerfstudio'))",
        ],
        run_command=run_command,
        label="Nerfstudio version probe",
    )
    probe_outputs["nerfstudio-version"] = (
        nerfstudio_stdout,
        nerfstudio_stderr,
    )
    python_stdout, python_stderr = _run_bounded(
        [
            str(python),
            "-c",
            "import platform; print(platform.python_version())",
        ],
        run_command=run_command,
        label="Python runtime probe",
    )
    probe_outputs["python-runtime"] = (python_stdout, python_stderr)
    help_stdout, help_stderr = _run_bounded(
        [str(ns_train), "splatfacto", "-h"],
        run_command=run_command,
        label="training CLI schema probe",
    )
    probe_outputs["training-cli-schema"] = (
        help_stdout,
        help_stderr,
    )

    resolved_after = {
        "ns-train": _resolve_role("ns-train", which=which),
        "nvidia-smi": _resolve_role("nvidia-smi", which=which),
        "git": _resolve_role("git", which=which),
    }
    if (
        resolved_after["ns-train"] != ns_train
        or resolved_after["nvidia-smi"] != nvidia_smi
        or resolved_after["git"] != git
    ):
        raise ProductionRuntimeEntrypointError(
            "runtime role resolution changed during clearance"
        )
    after = {
        role: _snapshot(path, logical_path=logical_path)
        for role, path in role_paths.items()
    }
    if before != after:
        raise ProductionRuntimeEntrypointError(
            "runtime executable changed during clearance"
        )
    try:
        executable_observations = tuple(
            StableExecutableObservation(
                role=role,
                probe_definition_sha256=_definition_sha(
                    _EXECUTABLE_DEFINITIONS[role]
                ),
                before=before[role],
                after=after[role],
            )
            for role in (
                "checker",
                "container-runtime",
                "ns-train",
                "nvidia-smi",
                "python",
                "worker",
            )
        )
    except ValueError as exc:
        raise ProductionRuntimeEntrypointError(
            "runtime executable observation is invalid"
        ) from exc

    environment = ExecutionEnvironmentObservation(
        kind="fresh-job-container",
        container_runtime=container_runtime,
        container_instance_id=lifecycle.container_id,
        configured_container_identity=expected_container_identity,
        observed_container_identity=lifecycle.container_identity,
        runtime_executable_sha256=before[
            "container-runtime"
        ].sha256,
    )
    environment_sha = execution_environment_sha256(environment)
    probes = tuple(
        ProbeObservationBinding(
            probe_id=probe_id,
            definition_sha256=_definition_sha(definition),
            execution_environment_sha256=environment_sha,
            stdout_sha256=hashlib.sha256(
                probe_outputs[probe_id][0]
            ).hexdigest(),
            stderr_sha256=hashlib.sha256(
                probe_outputs[probe_id][1]
            ).hexdigest(),
            exit_code=0,
        )
        for probe_id, definition in _PROBE_DEFINITIONS
    )
    options = tuple(
        sorted(
            {
                match.group(0).decode("ascii")
                for match in _OPTION_RE.finditer(help_stdout)
            }
        )
    )
    python_version = _safe_ascii_line(
        python_stdout,
        label="Python version",
    )
    nerfstudio_version = _safe_ascii_line(
        nerfstudio_stdout,
        label="Nerfstudio version",
    )
    cuda_runtime_version = _safe_ascii_line(
        cuda_stdout,
        label="CUDA runtime version",
    )
    try:
        measurement = ProductionRuntimeMeasurement.create(
            observed_at_utc=now(),
            exact_commit=exact_commit,
            clean_tree=True,
            remote_target_sha256=remote_target_sha256,
            durable_job_ref_sha256=durable_job_ref_sha256,
            workspace_identity_sha256=(
                lifecycle.workspace_identity_sha256
            ),
            environment=environment,
            executables=executable_observations,
            gpu=_gpu_observation(
                gpu_stdout,
                cuda_runtime_version=cuda_runtime_version,
                nvidia_smi_sha256=before["nvidia-smi"].sha256,
            ),
            training_cli=TrainingCliObservation(
                trainer_name="nerfstudio-splatfacto",
                python_version=python_version,
                nerfstudio_version=nerfstudio_version,
                observed_options=options,
                schema_sha256=training_cli_schema_sha256(
                    trainer_name="nerfstudio-splatfacto",
                    observed_options=options,
                ),
                help_stdout_sha256=hashlib.sha256(
                    help_stdout
                ).hexdigest(),
                python_executable_sha256=before["python"].sha256,
                training_cli_executable_sha256=before[
                    "ns-train"
                ].sha256,
            ),
            probes=probes,
        )
        decision = decide_production_runtime(measurement, policy)
        verify_production_runtime_decision(
            measurement=measurement,
            policy=policy,
            decision=decision,
        )
    except (ValueError, ProductionRuntimeEvidenceError) as exc:
        raise ProductionRuntimeEntrypointError(
            "runtime measurement cannot be closed"
        ) from exc

    evidence_root = (
        job_dir
        / "runtime"
        / "production-run"
        / "result"
        / "production-runtime"
    )
    _ensure_real_directory(evidence_root)
    _publish_noreplace(
        evidence_root / "policy.json",
        policy_bytes,
    )
    _publish_noreplace(
        evidence_root / "measurement.json",
        canonical_production_runtime_measurement_bytes(measurement),
    )
    _publish_noreplace(
        evidence_root / "decision.json",
        canonical_production_runtime_decision_bytes(decision),
    )
    if decision.status != "accepted":
        return 78
    exec_command(training_argv[0], list(training_argv))
    raise ProductionRuntimeEntrypointError(
        "training exec unexpectedly returned"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed fresh-container clearance before training",
    )
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument(
        "--mounted-container-runtime-path",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--container-runtime",
        choices=("docker", "podman"),
        required=True,
    )
    parser.add_argument("--remote-target-sha256", required=True)
    parser.add_argument("--durable-job-ref-sha256", required=True)
    parser.add_argument("--container-identity", required=True)
    parser.add_argument("training_argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    training_argv = tuple(args.training_argv)
    if training_argv[:1] == ("--",):
        training_argv = training_argv[1:]
    try:
        return run_clearance_and_train(
            job_dir=args.job_dir,
            repo_root=args.repo_root,
            mounted_container_runtime_path=(
                args.mounted_container_runtime_path
            ),
            container_runtime=args.container_runtime,
            remote_target_sha256=args.remote_target_sha256,
            durable_job_ref_sha256=args.durable_job_ref_sha256,
            expected_container_identity=args.container_identity,
            training_argv=training_argv,
        )
    except ProductionRuntimeEntrypointError:
        print("INVALID: production runtime clearance failed", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
