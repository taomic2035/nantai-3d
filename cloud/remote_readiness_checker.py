#!/usr/bin/env python3
"""Measure one remote CUDA runtime and worker without changing remote state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

CHECKER_VERSION = "nantai.remote-readiness-checker.v1"
DEFAULT_CONFIG = Path("/etc/nantai/remote-readiness.json")
_MAX_CONFIG_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024
_MAX_WORKER_BYTES = 16 * 1024 * 1024
_CONTAINER_PATTERN = re.compile(
    r"^[A-Za-z0-9._/:+-]+@sha256:[0-9a-f]{64}$"
)
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class RemoteReadinessCheckError(ValueError):
    """The remote runtime identity cannot be proven."""


def _duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteReadinessCheckError(
                "remote readiness config has duplicate keys"
            )
        result[key] = value
    return result


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


def _stable_bytes(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(
            before.st_mode
        ):
            raise RemoteReadinessCheckError(
                f"{label} must be a regular file"
            )
        if before.st_size <= 0 or before.st_size > maximum_bytes:
            raise RemoteReadinessCheckError(
                f"{label} size is invalid"
            )
        payload = path.read_bytes()
        after = path.lstat()
    except RemoteReadinessCheckError:
        raise
    except OSError as exc:
        raise RemoteReadinessCheckError(
            f"{label} cannot be read"
        ) from exc
    before_signature = _stat_signature(before)
    if (
        before_signature != _stat_signature(after)
        or len(payload) != before.st_size
    ):
        raise RemoteReadinessCheckError(
            f"{label} changed while read"
        )
    return payload, before_signature


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
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


def _load_config(
    path: Path,
) -> tuple[
    dict[str, Any],
    bytes,
    tuple[int, int, int, int, int, int],
]:
    payload, signature = _stable_bytes(
        path,
        label="remote readiness config",
        maximum_bytes=_MAX_CONFIG_BYTES,
    )
    try:
        parsed = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_duplicate_keys,
        )
    except RemoteReadinessCheckError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteReadinessCheckError(
            "remote readiness config is invalid"
        ) from exc
    required = {
        "schema",
        "container_runtime",
        "container_identity",
        "worker_path",
        "worker_python",
    }
    if not isinstance(parsed, dict) or set(parsed) != required:
        raise RemoteReadinessCheckError(
            "remote readiness config fields are invalid"
        )
    if payload != _canonical_json_bytes(parsed):
        raise RemoteReadinessCheckError(
            "remote readiness config is not canonical"
        )
    if parsed["schema"] != "nantai.remote-readiness-config.v1":
        raise RemoteReadinessCheckError(
            "remote readiness config schema is invalid"
        )
    if parsed["container_runtime"] not in {"docker", "podman"}:
        raise RemoteReadinessCheckError(
            "container runtime is invalid"
        )
    if (
        not isinstance(parsed["container_identity"], str)
        or _CONTAINER_PATTERN.fullmatch(
            parsed["container_identity"]
        )
        is None
    ):
        raise RemoteReadinessCheckError(
            "container identity is invalid"
        )
    for field in ("worker_path", "worker_python"):
        value = parsed[field]
        if (
            not isinstance(value, str)
            or not value
            or not Path(value).is_absolute()
        ):
            raise RemoteReadinessCheckError(
                f"{field} must be an absolute path"
            )
    return parsed, payload, signature


def _run_bounded(
    argv: list[str],
    *,
    run_command: Callable[..., subprocess.CompletedProcess],
) -> bytes:
    try:
        completed = run_command(
            argv,
            shell=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RemoteReadinessCheckError(
            "readiness probe could not be executed"
        ) from exc
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8", errors="replace")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8", errors="replace")
    if (
        completed.returncode != 0
        or len(stdout) > _MAX_OUTPUT_BYTES
        or len(stderr) > _MAX_OUTPUT_BYTES
    ):
        raise RemoteReadinessCheckError(
            "readiness probe did not produce bounded success"
        )
    return stdout


def _safe_text(payload: bytes, *, label: str) -> str:
    try:
        value = payload.decode("ascii").strip()
    except UnicodeError as exc:
        raise RemoteReadinessCheckError(
            f"{label} is not safe ASCII"
        ) from exc
    if (
        not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127
               for character in value)
    ):
        raise RemoteReadinessCheckError(
            f"{label} is invalid"
        )
    return value


def collect_remote_readiness(
    config_path: str | Path = DEFAULT_CONFIG,
    *,
    run_command: Callable[..., subprocess.CompletedProcess] = (
        subprocess.run
    ),
) -> dict[str, Any]:
    config_file = Path(config_path)
    config, config_bytes, config_signature = _load_config(
        config_file
    )
    runtime = config["container_runtime"]
    identity = config["container_identity"]
    worker_path = Path(config["worker_path"])
    worker_python = config["worker_python"]

    runtime_version = _safe_text(
        _run_bounded(
            [runtime, "--version"],
            run_command=run_command,
        ),
        label="container runtime version",
    )
    image_output = _run_bounded(
        [
            runtime,
            "image",
            "inspect",
            "--format",
            "{{json .RepoDigests}}",
            identity,
        ],
        run_command=run_command,
    )
    try:
        digests = json.loads(image_output.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteReadinessCheckError(
            "container image inspection is invalid"
        ) from exc
    if (
        not isinstance(digests, list)
        or not all(isinstance(item, str) for item in digests)
        or identity not in digests
    ):
        raise RemoteReadinessCheckError(
            "container image digest was not measured"
        )

    worker_bytes, worker_signature = _stable_bytes(
        worker_path,
        label="remote worker",
        maximum_bytes=_MAX_WORKER_BYTES,
    )
    worker_version = _safe_text(
        _run_bounded(
            [worker_python, str(worker_path), "--version"],
            run_command=run_command,
        ),
        label="remote worker version",
    )
    if _VERSION_PATTERN.fullmatch(worker_version) is None:
        raise RemoteReadinessCheckError(
            "remote worker version is invalid"
        )
    worker_after, after_signature = _stable_bytes(
        worker_path,
        label="remote worker",
        maximum_bytes=_MAX_WORKER_BYTES,
    )
    if (
        worker_signature != after_signature
        or worker_bytes != worker_after
    ):
        raise RemoteReadinessCheckError(
            "remote worker changed during probe"
        )
    try:
        config_after, signature_after = _stable_bytes(
            config_file,
            label="remote readiness config",
            maximum_bytes=_MAX_CONFIG_BYTES,
        )
    except RemoteReadinessCheckError as exc:
        raise RemoteReadinessCheckError(
            "remote readiness config changed during probe"
        ) from exc
    if (
        config_signature != signature_after
        or config_bytes != config_after
    ):
        raise RemoteReadinessCheckError(
            "remote readiness config changed during probe"
        )

    return {
        "schema": "nantai.remote-readiness-evidence.v1",
        "checker_version": CHECKER_VERSION,
        "checker_config_sha256": hashlib.sha256(
            config_bytes
        ).hexdigest(),
        "container_runtime": runtime,
        "container_runtime_version": runtime_version,
        "container_identity": identity,
        "worker_sha256": hashlib.sha256(
            worker_bytes
        ).hexdigest(),
        "worker_version": worker_version,
    }


def canonical_evidence_bytes(evidence: dict[str, Any]) -> bytes:
    expected = {
        "schema",
        "checker_version",
        "checker_config_sha256",
        "container_runtime",
        "container_runtime_version",
        "container_identity",
        "worker_sha256",
        "worker_version",
    }
    if set(evidence) != expected:
        raise RemoteReadinessCheckError(
            "remote readiness evidence fields are invalid"
        )
    return _canonical_json_bytes(evidence)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit canonical evidence for one immutable remote runtime identity"
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence = collect_remote_readiness(args.config)
        sys.stdout.buffer.write(canonical_evidence_bytes(evidence))
    except RemoteReadinessCheckError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
