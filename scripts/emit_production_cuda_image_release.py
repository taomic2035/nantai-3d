#!/usr/bin/env python3
"""Emit one detached, content-addressed Production CUDA image receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.durable_io import publish_file_noreplace
from pipeline.production_cuda_image_release import (
    OciAttestationBinding,
    ProductionCudaImageRelease,
    canonical_production_cuda_image_release_bytes,
    load_production_cuda_image_probe_bytes,
    load_production_cuda_image_release_bytes,
)
from pipeline.production_cuda_runtime_lock import (
    load_production_cuda_runtime_lock_bytes,
)

_MAX_LOCK_BYTES = 16 * 1024 * 1024
_MAX_PROBE_BYTES = 16 * 1024 * 1024
_MAX_DOCKERFILE_BYTES = 4 * 1024 * 1024
_MAX_REQUIREMENTS_BYTES = 16 * 1024 * 1024
_MAX_RECEIPT_BYTES = 32 * 1024 * 1024


class ProductionCudaImageReleaseCliError(RuntimeError):
    """A detached image receipt could not be safely produced."""


@dataclass(frozen=True)
class _StableFile:
    payload: bytes
    sha256: str
    identity: tuple[int, int, int, int, int]


def _identity(observed: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_size,
        observed.st_mtime_ns,
    )


def _is_linklike(path: Path, observed: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    )


def _open_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _read_once(
    path: Path,
    *,
    label: str,
    byte_cap: int,
) -> _StableFile:
    try:
        initial = path.lstat()
        if (
            _is_linklike(path, initial)
            or not stat.S_ISREG(initial.st_mode)
            or initial.st_size <= 0
            or initial.st_size > byte_cap
        ):
            raise ProductionCudaImageReleaseCliError(
                f"{label} must be a bounded regular non-link file"
            )
        descriptor = _open_nofollow(path)
        digest = hashlib.sha256()
        payload = bytearray()
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            opened = os.fstat(stream.fileno())
            while True:
                chunk = stream.read(
                    min(1024 * 1024, byte_cap + 1 - len(payload))
                )
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > byte_cap:
                    raise ProductionCudaImageReleaseCliError(
                        f"{label} exceeds byte cap"
                    )
                digest.update(chunk)
            closed = os.fstat(stream.fileno())
        final = path.lstat()
    except ProductionCudaImageReleaseCliError:
        raise
    except OSError as exc:
        raise ProductionCudaImageReleaseCliError(
            f"{label} could not be inspected"
        ) from exc
    identity = _identity(initial)
    if (
        _is_linklike(path, final)
        or identity != _identity(opened)
        or identity != _identity(closed)
        or identity != _identity(final)
        or len(payload) != initial.st_size
    ):
        raise ProductionCudaImageReleaseCliError(
            f"{label} changed while hashing"
        )
    return _StableFile(
        payload=bytes(payload),
        sha256=digest.hexdigest(),
        identity=identity,
    )


def _read_stable_regular_file(
    path: Path,
    *,
    label: str,
    byte_cap: int,
) -> _StableFile:
    first = _read_once(path, label=label, byte_cap=byte_cap)
    second = _read_once(path, label=label, byte_cap=byte_cap)
    if first != second:
        raise ProductionCudaImageReleaseCliError(
            f"{label} changed between observations"
        )
    return first


def _parse_attestation(value: str) -> OciAttestationBinding:
    parts = value.split(",")
    if len(parts) != 5 or any(not part for part in parts):
        raise argparse.ArgumentTypeError(
            "attestation must be role,predicate-type,"
            "manifest-digest,predicate-blob-digest,subject-digest"
        )
    try:
        return OciAttestationBinding(
            role=parts[0],
            predicate_type=parts[1],
            manifest_digest=parts[2],
            predicate_blob_digest=parts[3],
            subject_digest=parts[4],
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"attestation is invalid: {exc}"
        ) from exc


def _output_is_absent(output: Path) -> None:
    try:
        output.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProductionCudaImageReleaseCliError(
            "output destination cannot be inspected"
        ) from exc
    raise ProductionCudaImageReleaseCliError(
        "output destination already exists"
    )


def _publish_noreplace(output: Path, payload: bytes) -> None:
    output = output.absolute()
    _output_is_absent(output)
    try:
        parent = output.parent
        parent_stat = parent.lstat()
    except OSError as exc:
        raise ProductionCudaImageReleaseCliError(
            "output parent is unavailable"
        ) from exc
    if _is_linklike(parent, parent_stat) or not stat.S_ISDIR(
        parent_stat.st_mode
    ):
        raise ProductionCudaImageReleaseCliError(
            "output parent must be a real directory"
        )
    candidate = parent / f".{output.name}.{uuid.uuid4().hex}.candidate"
    try:
        with candidate.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        publish_file_noreplace(candidate, output)
    except Exception as exc:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        if isinstance(exc, ProductionCudaImageReleaseCliError):
            raise
        raise ProductionCudaImageReleaseCliError(
            "receipt publication failed"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bind a probed Production CUDA image digest to a detached receipt."
        )
    )
    parser.add_argument("--runtime-lock", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--platform-manifest-digest", required=True)
    parser.add_argument("--dockerfile", required=True, type=Path)
    parser.add_argument("--requirements-lock", required=True, type=Path)
    parser.add_argument("--workflow-repository", required=True)
    parser.add_argument("--workflow-run-id", required=True, type=int)
    parser.add_argument("--workflow-run-attempt", required=True, type=int)
    parser.add_argument(
        "--attestation",
        required=True,
        action="append",
        type=_parse_attestation,
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _build_release(args: argparse.Namespace) -> ProductionCudaImageRelease:
    runtime_lock_file = _read_stable_regular_file(
        args.runtime_lock,
        label="runtime lock",
        byte_cap=_MAX_LOCK_BYTES,
    )
    load_production_cuda_runtime_lock_bytes(runtime_lock_file.payload)
    probe_file = _read_stable_regular_file(
        args.probe,
        label="image probe",
        byte_cap=_MAX_PROBE_BYTES,
    )
    probe = load_production_cuda_image_probe_bytes(probe_file.payload)
    if probe.runtime_lock_sha256 != runtime_lock_file.sha256:
        raise ProductionCudaImageReleaseCliError(
            "image probe runtime lock SHA differs from input"
        )
    dockerfile = _read_stable_regular_file(
        args.dockerfile,
        label="Dockerfile",
        byte_cap=_MAX_DOCKERFILE_BYTES,
    )
    requirements = _read_stable_regular_file(
        args.requirements_lock,
        label="requirements lock",
        byte_cap=_MAX_REQUIREMENTS_BYTES,
    )
    return ProductionCudaImageRelease.create(
        source_commit=args.source_commit,
        image_name=args.image_name,
        image_digest=args.image_digest,
        platform_manifest_digest=args.platform_manifest_digest,
        dockerfile_sha256=dockerfile.sha256,
        requirements_lock_sha256=requirements.sha256,
        image_probe=probe,
        workflow_repository=args.workflow_repository,
        workflow_run_id=args.workflow_run_id,
        workflow_run_attempt=args.workflow_run_attempt,
        attestations=tuple(args.attestation),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        _output_is_absent(args.output)
        release = _build_release(args)
        payload = canonical_production_cuda_image_release_bytes(release)
        _publish_noreplace(args.output, payload)
        published = _read_stable_regular_file(
            args.output,
            label="published receipt",
            byte_cap=_MAX_RECEIPT_BYTES,
        )
        reopened = load_production_cuda_image_release_bytes(
            published.payload
        )
        if reopened != release:
            raise ProductionCudaImageReleaseCliError(
                "published receipt differs from candidate"
            )
    except (OSError, ValueError, ProductionCudaImageReleaseCliError) as exc:
        print(f"production CUDA image release failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "image_identity": release.image_identity,
                "receipt_sha256": release.content_sha256,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
