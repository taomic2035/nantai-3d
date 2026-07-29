#!/usr/bin/env python3
"""Measure the no-GPU contract of one freshly built Production CUDA image."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.durable_io import publish_file_noreplace
from pipeline.production_cuda_image_release import (
    ImageExecutableObservation,
    ProductionCudaImageProbe,
    canonical_production_cuda_image_probe_bytes,
)
from pipeline.production_cuda_runtime_lock import (
    ProductionCudaRuntimeLock,
    load_production_cuda_runtime_lock_bytes,
)
from pipeline.production_runtime_evidence import (
    training_cli_schema_sha256,
)

_MAX_LOCK_BYTES = 16 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_MAX_COMMAND_BYTES = 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 20
_OPTION_RE = re.compile(r"--[a-z0-9][a-z0-9.-]*")
_EXECUTABLE_ROLES = ("ns-export", "ns-train", "python")
_VERSION_KEYS = {
    "python_version",
    "torch_cuda_version",
    "torch_version",
    "torchvision_version",
}
_EXPECTED_VERSIONS = {
    "python_version": "3.11.9",
    "torch_cuda_version": "11.8",
    "torch_version": "2.1.2+cu118",
    "torchvision_version": "0.16.2+cu118",
    "nerfstudio_version": "1.1.5",
    "gsplat_version": "1.4.0",
}
_LOCK_ROLE_BY_VERSION = {
    "python_version": "cpython-source",
    "torch_version": "torch-wheel",
    "torchvision_version": "torchvision-wheel",
    "nerfstudio_version": "nerfstudio-wheel",
    "gsplat_version": "gsplat-sdist",
}
_VERSION_PROBE = (
    "import json,platform,torch,torchvision;"
    "print(json.dumps({"
    "'python_version':platform.python_version(),"
    "'torch_cuda_version':torch.version.cuda,"
    "'torch_version':torch.__version__,"
    "'torchvision_version':torchvision.__version__"
    "},sort_keys=True,separators=(',',':')))"
)


class ProductionCudaImageProbeError(RuntimeError):
    """The image-internal runtime contract could not be proved."""


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    reported_path: str
    byte_length: int
    sha256: str
    mode: int
    identity: tuple[int, int, int, int, int]


def _runtime_platform() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in {
        "amd64",
        "x86_64",
    }:
        return "linux/amd64"
    return f"{sys.platform}/{machine or 'unknown'}"


def _is_linklike(path: Path, observed: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    )


def _identity(observed: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
    )


def _reported_posix_path(path: Path) -> str:
    value = path.as_posix()
    if len(value) >= 2 and value[1] == ":":
        return f"/{value}"
    return value


def _hash_open_file(
    descriptor: int,
    *,
    byte_cap: int,
    label: str,
) -> tuple[str, int, os.stat_result]:
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(descriptor, "rb", closefd=True) as stream:
        before = os.fstat(stream.fileno())
        while True:
            chunk = stream.read(min(1024 * 1024, byte_cap + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > byte_cap:
                raise ProductionCudaImageProbeError(
                    f"{label} exceeds byte cap"
                )
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if _identity(before) != _identity(after):
        raise ProductionCudaImageProbeError(f"{label} changed while hashing")
    return digest.hexdigest(), total, after


def _open_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _snapshot_regular_file(
    path: Path,
    *,
    byte_cap: int,
    label: str,
    executable: bool,
) -> _FileSnapshot:
    try:
        observed = path.lstat()
        if (
            _is_linklike(path, observed)
            or not stat.S_ISREG(observed.st_mode)
            or (executable and not os.access(path, os.X_OK))
        ):
            raise ProductionCudaImageProbeError(
                f"{label} must be a regular non-link executable"
                if executable
                else f"{label} must be a regular non-link file"
            )
        descriptor = _open_nofollow(path)
        digest, byte_length, opened = _hash_open_file(
            descriptor,
            byte_cap=byte_cap,
            label=label,
        )
        final = path.lstat()
    except ProductionCudaImageProbeError:
        raise
    except OSError as exc:
        raise ProductionCudaImageProbeError(
            f"{label} could not be inspected"
        ) from exc
    if (
        _is_linklike(path, final)
        or _identity(observed) != _identity(opened)
        or _identity(observed) != _identity(final)
    ):
        raise ProductionCudaImageProbeError(f"{label} changed while hashing")
    receipt_mode = observed.st_mode
    if os.name == "nt" and executable:
        receipt_mode = stat.S_IFREG | 0o755
    return _FileSnapshot(
        path=path,
        reported_path=_reported_posix_path(path),
        byte_length=byte_length,
        sha256=digest,
        mode=receipt_mode,
        identity=_identity(observed),
    )


def _snapshot_runtime_lock(
    path: Path,
) -> tuple[bytes, _FileSnapshot, ProductionCudaRuntimeLock]:
    snapshot = _snapshot_regular_file(
        path,
        byte_cap=_MAX_LOCK_BYTES,
        label="runtime lock",
        executable=False,
    )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ProductionCudaImageProbeError(
            "runtime lock could not be read"
        ) from exc
    if (
        len(payload) != snapshot.byte_length
        or hashlib.sha256(payload).hexdigest() != snapshot.sha256
    ):
        raise ProductionCudaImageProbeError(
            "runtime lock changed while reading"
        )
    try:
        lock = load_production_cuda_runtime_lock_bytes(payload)
    except ValueError as exc:
        raise ProductionCudaImageProbeError(
            f"runtime lock is invalid: {exc}"
        ) from exc
    return payload, snapshot, lock


def _resolve_executables(
    which: Callable[[str], str | None],
) -> dict[str, _FileSnapshot]:
    snapshots: dict[str, _FileSnapshot] = {}
    for role in _EXECUTABLE_ROLES:
        try:
            raw = which(role)
        except Exception as exc:
            raise ProductionCudaImageProbeError(
                f"{role} executable resolution failed"
            ) from exc
        if raw is None:
            raise ProductionCudaImageProbeError(
                f"{role} executable is unavailable"
            )
        path = Path(raw)
        if not path.is_absolute():
            raise ProductionCudaImageProbeError(
                f"{role} executable path must be absolute"
            )
        snapshots[role] = _snapshot_regular_file(
            path,
            byte_cap=_MAX_EXECUTABLE_BYTES,
            label=f"{role} executable",
            executable=True,
        )
    identities = [item.identity for item in snapshots.values()]
    if len(set(identities)) != len(identities):
        raise ProductionCudaImageProbeError(
            "image executable identities must be distinct"
        )
    return snapshots


def _bounded_command(
    run_command: Callable[..., subprocess.CompletedProcess[bytes]],
    argv: list[str],
    *,
    label: str,
) -> tuple[str, str]:
    try:
        result = run_command(
            argv,
            capture_output=True,
            check=False,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise ProductionCudaImageProbeError(
            f"{label} command could not run"
        ) from exc
    stdout = result.stdout
    stderr = result.stderr
    if (
        type(stdout) is not bytes
        or type(stderr) is not bytes
        or len(stdout) > _MAX_COMMAND_BYTES
        or len(stderr) > _MAX_COMMAND_BYTES
    ):
        raise ProductionCudaImageProbeError(
            f"{label} output is not bounded ASCII"
        )
    try:
        stdout_text = stdout.decode("ascii")
        stderr_text = stderr.decode("ascii")
    except UnicodeError as exc:
        raise ProductionCudaImageProbeError(
            f"{label} output is not bounded ASCII"
        ) from exc
    if result.returncode != 0:
        raise ProductionCudaImageProbeError(f"{label} command failed")
    return stdout_text, stderr_text


def _reject_duplicate_pairs(pairs):
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionCudaImageProbeError(
                "version probe output contains duplicate keys"
            )
        result[key] = value
    return result


def _parse_versions(payload: str) -> dict[str, str]:
    try:
        parsed = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (json.JSONDecodeError, ProductionCudaImageProbeError) as exc:
        raise ProductionCudaImageProbeError(
            "version probe output is invalid"
        ) from exc
    if (
        type(parsed) is not dict
        or set(parsed) != _VERSION_KEYS
        or any(type(parsed[key]) is not str for key in _VERSION_KEYS)
    ):
        raise ProductionCudaImageProbeError(
            "version probe output has an unexpected schema"
        )
    return parsed


def _locked_versions(
    lock: ProductionCudaRuntimeLock,
) -> dict[str, str]:
    by_role = {item.role: item.version for item in lock.source_artifacts}
    return {
        field: by_role[role]
        for field, role in _LOCK_ROLE_BY_VERSION.items()
    }


def _verify_versions(
    observed: dict[str, str],
    *,
    lock: ProductionCudaRuntimeLock,
) -> None:
    locked = _locked_versions(lock)
    for field, expected in _EXPECTED_VERSIONS.items():
        if observed.get(field) != expected:
            raise ProductionCudaImageProbeError(
                f"{field} version differs from production contract"
            )
        if field in locked and observed[field] != locked[field]:
            raise ProductionCudaImageProbeError(
                f"{field} version differs from runtime lock"
            )


def _parse_cli_options(stdout: str, stderr: str) -> tuple[str, ...]:
    options = tuple(sorted(set(_OPTION_RE.findall(stdout + "\n" + stderr))))
    required = {
        "--data",
        "--machine.seed",
        "--max-num-iterations",
        "--output-dir",
        "--viewer.quit-on-train-completion",
    }
    if not required <= set(options):
        raise ProductionCudaImageProbeError(
            "required training CLI options are missing"
        )
    return options


def _verify_stable_files(
    before: dict[str, _FileSnapshot],
) -> None:
    for role, first in before.items():
        second = _snapshot_regular_file(
            first.path,
            byte_cap=_MAX_EXECUTABLE_BYTES,
            label=f"{role} executable",
            executable=True,
        )
        if second != first:
            raise ProductionCudaImageProbeError(
                f"{role} executable changed during probe"
            )


def collect_image_probe(
    runtime_lock_path: Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    run_command: Callable[
        ..., subprocess.CompletedProcess[bytes]
    ] = subprocess.run,
    package_version: Callable[[str], str] = metadata.version,
    module_importer: Callable[[str], object] = importlib.import_module,
) -> ProductionCudaImageProbe:
    """Derive the immutable no-GPU image contract from local observations."""

    if _runtime_platform() != "linux/amd64":
        raise ProductionCudaImageProbeError(
            "production image probe requires linux/amd64"
        )
    lock_path = Path(runtime_lock_path)
    initial_lock_bytes, initial_lock_snapshot, lock = (
        _snapshot_runtime_lock(lock_path)
    )
    if lock.platform != "linux/amd64":
        raise ProductionCudaImageProbeError(
            "runtime lock platform is not linux/amd64"
        )
    executables = _resolve_executables(which)
    version_stdout, version_stderr = _bounded_command(
        run_command,
        [
            executables["python"].reported_path,
            "-I",
            "-c",
            _VERSION_PROBE,
        ],
        label="version probe",
    )
    if version_stderr:
        raise ProductionCudaImageProbeError(
            "version probe produced unexpected stderr"
        )
    observed_versions = _parse_versions(version_stdout)
    try:
        observed_versions["nerfstudio_version"] = package_version(
            "nerfstudio"
        )
        observed_versions["gsplat_version"] = package_version("gsplat")
    except Exception as exc:
        raise ProductionCudaImageProbeError(
            "package version observation failed"
        ) from exc
    _verify_versions(observed_versions, lock=lock)
    help_stdout, help_stderr = _bounded_command(
        run_command,
        [
            executables["ns-train"].reported_path,
            "splatfacto",
            "-h",
        ],
        label="training CLI probe",
    )
    options = _parse_cli_options(help_stdout, help_stderr)
    for module_name in lock.required_imports:
        try:
            module_importer(module_name)
        except Exception as exc:
            raise ProductionCudaImageProbeError(
                f"required import failed: {module_name}"
            ) from exc
    _verify_stable_files(executables)
    final_lock_bytes, final_lock_snapshot, final_lock = (
        _snapshot_runtime_lock(lock_path)
    )
    if (
        final_lock_bytes != initial_lock_bytes
        or final_lock_snapshot != initial_lock_snapshot
        or final_lock != lock
    ):
        raise ProductionCudaImageProbeError(
            "runtime lock changed during probe"
        )
    observations = tuple(
        ImageExecutableObservation(
            role=role,
            resolved_path=executables[role].reported_path,
            byte_length=executables[role].byte_length,
            sha256=executables[role].sha256,
            mode=executables[role].mode,
        )
        for role in _EXECUTABLE_ROLES
    )
    return ProductionCudaImageProbe.create(
        platform="linux/amd64",
        runtime_lock_sha256=hashlib.sha256(
            initial_lock_bytes
        ).hexdigest(),
        python_version=observed_versions["python_version"],
        torch_version=observed_versions["torch_version"],
        torch_cuda_version=observed_versions["torch_cuda_version"],
        torchvision_version=observed_versions["torchvision_version"],
        nerfstudio_version=observed_versions["nerfstudio_version"],
        gsplat_version=observed_versions["gsplat_version"],
        executables=observations,
        training_cli_options=options,
        training_cli_schema_sha256=training_cli_schema_sha256(
            trainer_name="nerfstudio-splatfacto",
            observed_options=options,
        ),
        imported_modules=lock.required_imports,
    )


def _output_is_absent(output: Path) -> None:
    try:
        output.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProductionCudaImageProbeError(
            "output destination cannot be inspected"
        ) from exc
    raise ProductionCudaImageProbeError(
        "output destination already exists"
    )


def _publish_probe(
    output: Path,
    payload: bytes,
) -> None:
    output = output.absolute()
    _output_is_absent(output)
    parent = output.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise ProductionCudaImageProbeError(
            "output parent is unavailable"
        ) from exc
    if _is_linklike(parent, parent_stat) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise ProductionCudaImageProbeError(
            "output parent must be a real directory"
        )
    candidate = parent / f".{output.name}.{uuid.uuid4().hex}.candidate"
    try:
        with candidate.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        publish_file_noreplace(candidate, output)
    except Exception:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure a Production CUDA image without asserting GPU readiness."
        )
    )
    parser.add_argument(
        "--runtime-lock",
        required=True,
        type=Path,
        help="canonical embedded runtime lock",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new detached image-probe JSON path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _output_is_absent(args.output)
        probe = collect_image_probe(args.runtime_lock)
        _publish_probe(
            args.output,
            canonical_production_cuda_image_probe_bytes(probe),
        )
    except (OSError, ValueError, ProductionCudaImageProbeError) as exc:
        print(f"production CUDA image probe failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
