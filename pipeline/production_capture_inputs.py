"""Atomically materialize canonical production source and rights inputs."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from pipeline.durable_io import (
    DurableIOError,
    flush_directory,
    flush_file,
    publish_directory_noreplace,
)
from pipeline.real_dataset import (
    CaptureRightsReceipt,
    LocalCaptureSource,
    canonical_model_bytes,
    load_capture_rights_receipt,
    load_real_dataset_source,
    validate_capture_rights,
)


class ProductionCaptureInputError(ValueError):
    """Production capture source/rights inputs cannot be published safely."""


@dataclass(frozen=True)
class ProductionCaptureInputMaterialization:
    output_dir: Path
    rights_path: Path
    source_path: Path
    rights_sha256: str
    source_sha256: str


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


def _output_directory(path: Path) -> Path:
    output = Path(path).expanduser().absolute()
    if output.exists() or _is_linklike(output):
        raise ProductionCaptureInputError(
            "production capture input output directory must be absent"
        )
    try:
        parent_stat = output.parent.lstat()
        parent_real = output.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProductionCaptureInputError(
            "production capture input output parent is unavailable"
        ) from exc
    if (
        _is_linklike(output.parent)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or parent_real != output.parent
    ):
        raise ProductionCaptureInputError(
            "production capture input output parent must be a real directory"
        )
    return output


def _models(
    *,
    dataset_id: str,
    operator: str,
    capture_scope: str,
    effective_date: date,
    processing_purposes: tuple[str, ...],
    redistribution_allowed: bool,
    release_inclusion_allowed: bool,
) -> tuple[CaptureRightsReceipt, LocalCaptureSource]:
    try:
        rights = CaptureRightsReceipt(
            schema="nantai.capture-rights-receipt.v1",
            dataset_id=dataset_id,
            operator=operator,
            capture_scope=capture_scope,
            effective_date=effective_date,
            processing_purposes=processing_purposes,
            redistribution_allowed=redistribution_allowed,
            release_inclusion_allowed=release_inclusion_allowed,
        )
        rights_payload = canonical_model_bytes(rights)
        source = LocalCaptureSource(
            schema="nantai.real-dataset-source.v1",
            dataset_id=dataset_id,
            role="production-acceptance",
            source_kind="local-capture",
            rights_receipt_sha256=hashlib.sha256(
                rights_payload
            ).hexdigest(),
            redistribution_allowed=redistribution_allowed,
            release_inclusion_allowed=release_inclusion_allowed,
        )
        validate_capture_rights(source, rights)
    except (ValidationError, ValueError) as exc:
        raise ProductionCaptureInputError(
            f"production capture inputs are invalid: {exc}"
        ) from exc
    return rights, source


def _write_private_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def materialize_production_capture_inputs(
    *,
    output_dir: Path,
    dataset_id: str,
    operator: str,
    capture_scope: str,
    effective_date: date,
    processing_purposes: tuple[str, ...],
    redistribution_allowed: bool,
    release_inclusion_allowed: bool,
) -> ProductionCaptureInputMaterialization:
    output = _output_directory(Path(output_dir))
    rights, source = _models(
        dataset_id=dataset_id,
        operator=operator,
        capture_scope=capture_scope,
        effective_date=effective_date,
        processing_purposes=processing_purposes,
        redistribution_allowed=redistribution_allowed,
        release_inclusion_allowed=release_inclusion_allowed,
    )
    rights_payload = canonical_model_bytes(rights)
    source_payload = canonical_model_bytes(source)
    staging = output.parent / (
        f".{output.name}.{uuid.uuid4().hex}.staging"
    )
    published = False
    try:
        staging.mkdir(mode=0o700)
        staging_rights = staging / "capture-rights-receipt.json"
        staging_source = staging / "production-source.json"
        _write_private_file(staging_rights, rights_payload)
        _write_private_file(staging_source, source_payload)
        flush_file(staging_rights)
        flush_file(staging_source)
        flush_directory(staging)
        reopened_rights = load_capture_rights_receipt(
            staging_rights
        )
        reopened_source = load_real_dataset_source(staging_source)
        if (
            reopened_rights != rights
            or reopened_source != source
            or not isinstance(reopened_source, LocalCaptureSource)
        ):
            raise ProductionCaptureInputError(
                "production capture inputs changed before publication"
            )
        validate_capture_rights(
            reopened_source,
            reopened_rights,
        )
        publish_directory_noreplace(staging, output)
        published = True
    except ProductionCaptureInputError:
        raise
    except (DurableIOError, OSError, ValueError) as exc:
        if isinstance(exc, DurableIOError) and exc.published:
            published = True
        state = (
            "published but durability is unconfirmed"
            if isinstance(exc, DurableIOError) and exc.published
            else "not published"
        )
        raise ProductionCaptureInputError(
            f"production capture inputs cannot be published ({state})"
        ) from exc
    finally:
        if (
            not published
            and staging.is_dir()
            and not _is_linklike(staging)
        ):
            for name in (
                "capture-rights-receipt.json",
                "production-source.json",
            ):
                candidate = staging / name
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                staging.rmdir()
            except OSError:
                pass
    return ProductionCaptureInputMaterialization(
        output_dir=output,
        rights_path=output / "capture-rights-receipt.json",
        source_path=output / "production-source.json",
        rights_sha256=hashlib.sha256(rights_payload).hexdigest(),
        source_sha256=hashlib.sha256(source_payload).hexdigest(),
    )


def _effective_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "effective date must use YYYY-MM-DD"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Atomically generate a canonical private rights receipt and "
            "its content-bound production local-capture source."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--capture-scope", required=True)
    parser.add_argument(
        "--effective-date",
        type=_effective_date,
        required=True,
    )
    parser.add_argument(
        "--processing-purpose",
        action="append",
        required=True,
    )
    parser.add_argument(
        "--redistribution-allowed",
        action="store_true",
    )
    parser.add_argument(
        "--release-inclusion-allowed",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = materialize_production_capture_inputs(
            output_dir=args.output_dir,
            dataset_id=args.dataset_id,
            operator=args.operator,
            capture_scope=args.capture_scope,
            effective_date=args.effective_date,
            processing_purposes=tuple(args.processing_purpose),
            redistribution_allowed=args.redistribution_allowed,
            release_inclusion_allowed=(
                args.release_inclusion_allowed
            ),
        )
    except ProductionCaptureInputError as exc:
        print(f"production capture inputs blocked: {exc}")
        return 2
    print(f"Rights receipt: {result.rights_path}")
    print(f"Rights SHA-256: {result.rights_sha256}")
    print(f"Production source: {result.source_path}")
    print(f"Source SHA-256: {result.source_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
