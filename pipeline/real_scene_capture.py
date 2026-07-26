"""Verified real-scene capture preparation.

This layer selects source media and binds existing ingest/capture contracts.
It proves byte closure only; ``synthetic=False`` does not promote geometry.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pipeline.ingest import ingest_all
from pipeline.ingest_manifest import MANIFEST_FILENAME
from pipeline.real_dataset import (
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
)
from pipeline.real_dataset_fetch import (
    DatasetDownloadError,
    verify_hf_dataset,
)
from pipeline.studio_revisions import (
    PreparedCaptureBundle,
    prepare_capture_bundle,
)


class RealSceneCaptureError(ValueError):
    """Source media cannot produce an auditable capture revision."""


@dataclass(frozen=True)
class PreparedRealCapture:
    source_sha256: str
    dataset_receipt_sha256: str
    selected_paths: tuple[str, ...]
    capture: PreparedCaptureBundle

    @property
    def capture_manifest(self):
        return self.capture.manifest

    @property
    def payload_root(self) -> Path:
        return self.capture.bundle / "payload"


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    measured = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                measured += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot hash source media {path.name}: {exc}"
        ) from exc
    return measured, digest.hexdigest()


def _require_absent_capture_boundary(run_root: Path) -> Path:
    boundary = run_root / "capture"
    if boundary.exists() or boundary.is_symlink():
        raise RealSceneCaptureError("capture output boundary must be absent")
    try:
        boundary.mkdir(parents=True)
    except OSError as exc:
        raise RealSceneCaptureError(
            f"cannot create capture output boundary: {exc}"
        ) from exc
    if boundary.is_symlink() or boundary.resolve(strict=True) != boundary.absolute():
        raise RealSceneCaptureError("capture output boundary is redirected")
    return boundary


def _select_hf_capture_paths(
    source: HfDatasetSource,
    receipt_paths: tuple[str, ...],
) -> tuple[str, ...]:
    prefix = f"{source.capture_subtree}/"
    direct: list[str] = []
    nested: list[str] = []
    for relative_path in receipt_paths:
        if not relative_path.startswith(prefix):
            continue
        remainder = relative_path.removeprefix(prefix)
        if "/" in remainder:
            nested.append(relative_path)
        else:
            direct.append(relative_path)
    if nested:
        raise RealSceneCaptureError(
            "capture_subtree contains nested members; source selection is ambiguous"
        )
    if not direct:
        raise RealSceneCaptureError("capture_subtree contains no direct source media")
    return tuple(sorted(direct))


def _stable_copy(
    source_path: Path,
    destination: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    try:
        before = source_path.lstat()
    except OSError as exc:
        raise RealSceneCaptureError("source media disappeared before copy") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RealSceneCaptureError("source media is not a regular file")
    measured_bytes, measured_sha = _sha256_file(source_path)
    if measured_bytes != expected_bytes:
        raise RealSceneCaptureError("source media length changed before copy")
    if measured_sha != expected_sha256:
        raise RealSceneCaptureError("source media sha256 changed before copy")

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source_path, destination, follow_symlinks=False)
        after = source_path.stat()
    except OSError as exc:
        raise RealSceneCaptureError("source media copy failed") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RealSceneCaptureError("source media changed while copying")
    copied_bytes, copied_sha = _sha256_file(destination)
    if copied_bytes != expected_bytes or copied_sha != expected_sha256:
        raise RealSceneCaptureError("copied source media failed byte verification")


def prepare_real_capture(
    source: HfDatasetSource | LocalCaptureSource,
    source_root: Path,
    run_root: Path,
) -> PreparedRealCapture:
    """Create one immutable capture revision from revalidated source bytes."""

    if not isinstance(source, HfDatasetSource):
        raise RealSceneCaptureError(
            "local-capture preparation requires the Task 8 private-media adapter"
        )
    try:
        receipt = verify_hf_dataset(source, source_root)
    except DatasetDownloadError as exc:
        raise RealSceneCaptureError(str(exc)) from exc

    selected_paths = _select_hf_capture_paths(
        source,
        tuple(entry.relative_path for entry in receipt.entries),
    )
    entries = {entry.relative_path: entry for entry in receipt.entries}
    boundary = _require_absent_capture_boundary(run_root)
    selected_root = boundary / "source"
    selected_root.mkdir()
    dataset_root = source_root / "dataset"
    for relative_path in selected_paths:
        entry = entries[relative_path]
        logical_name = PurePosixPath(relative_path).name
        _stable_copy(
            dataset_root.joinpath(*relative_path.split("/")),
            selected_root / logical_name,
            expected_bytes=entry.actual_bytes,
            expected_sha256=entry.actual_sha256,
        )

    ingest_root = boundary / "ingest"
    try:
        ingest_all(selected_root, ingest_root)
    except Exception as exc:
        raise RealSceneCaptureError(f"ingest failed: {exc}") from exc
    ingest_bytes = (ingest_root / MANIFEST_FILENAME).read_bytes()
    revision_id = (
        "capture-" + hashlib.sha256(ingest_bytes).hexdigest()[:32]
    )
    try:
        capture = prepare_capture_bundle(
            stage_dir=ingest_root,
            input_dir=selected_root,
            bundle_dir=boundary / "bundle",
            revision_id=revision_id,
            synthetic=False,
            created_utc=datetime.now(UTC),
        )
    except Exception as exc:
        raise RealSceneCaptureError(
            f"capture bundle preparation failed: {exc}"
        ) from exc
    return PreparedRealCapture(
        source_sha256=hashlib.sha256(canonical_model_bytes(source)).hexdigest(),
        dataset_receipt_sha256=hashlib.sha256(
            canonical_model_bytes(receipt)
        ).hexdigest(),
        selected_paths=selected_paths,
        capture=capture,
    )
