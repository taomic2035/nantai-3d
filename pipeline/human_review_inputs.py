"""Derive one canonical human-review policy from verified Viewer evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from pipeline.durable_io import (
    DurableIOError,
    capture_real_directory_identity,
    flush_file,
    matches_real_directory_identity,
    publish_file_noreplace,
)
from pipeline.real_scene_acceptance import (
    REQUIRED_VISUAL_CATEGORIES,
    HumanReviewPolicy,
    canonical_human_review_policy_bytes,
)
from pipeline.viewer_acceptance import (
    ViewerAcceptanceError,
    ViewerPerformancePolicy,
    ViewerPerformanceReportV2,
    canonical_viewer_performance_policy_bytes,
    load_viewer_performance_report_bytes,
    verify_viewer_capture_report,
)

PRODUCTION_MAXIMUM_SCREENSHOT_BYTES = 16 * 1024 * 1024
_MAX_VIEWER_EVIDENCE_BYTES = 16 * 1024 * 1024


class HumanReviewInputError(ValueError):
    """Human-review inputs cannot be proven from the Viewer capture."""


@dataclass(frozen=True)
class HumanReviewPolicyMaterialization:
    output_path: Path
    policy_sha256: str
    viewer_report_sha256: str


def _cross_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _same_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _is_linklike(path: Path, observed: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _real_evidence_root(path: Path) -> Path:
    absolute = Path(path).expanduser().absolute()
    try:
        inspected = absolute.lstat()
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise HumanReviewInputError("Viewer capture evidence root is unavailable") from exc
    if (
        stat.S_ISLNK(inspected.st_mode)
        or not stat.S_ISDIR(inspected.st_mode)
        or os.path.normcase(str(resolved)) != os.path.normcase(str(absolute))
    ):
        raise HumanReviewInputError("Viewer capture evidence root must be a real directory")
    return resolved


def _below_root(path: Path, root: Path, *, label: str) -> Path:
    absolute = Path(path).expanduser()
    if not absolute.is_absolute():
        absolute = root / absolute
    absolute = absolute.absolute()
    try:
        absolute.relative_to(root)
    except ValueError as exc:
        raise HumanReviewInputError(f"{label} must remain below the evidence root") from exc
    return absolute


def _read_regular_bytes(
    path: Path,
    *,
    root: Path,
    label: str,
    max_bytes: int = _MAX_VIEWER_EVIDENCE_BYTES,
) -> bytes:
    path = _below_root(path, root, label=label)
    current = root
    try:
        for part in path.relative_to(root).parts:
            current = current / part
            inspected = current.lstat()
            if _is_linklike(current, inspected):
                raise HumanReviewInputError(f"{label} must not traverse a link")
        before = path.lstat()
        if (
            _is_linklike(path, before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > max_bytes
        ):
            raise HumanReviewInputError(f"{label} must be a bounded regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor = os.open(path, flags)
    except HumanReviewInputError:
        raise
    except OSError as exc:
        raise HumanReviewInputError(f"{label} cannot be read") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise HumanReviewInputError(f"{label} cannot be read") from exc
    payload = bytearray()
    try:
        with stream:
            fd_before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(fd_before.st_mode)
                or _cross_surface_signature(fd_before)
                != _cross_surface_signature(before)
            ):
                raise HumanReviewInputError(f"{label} changed before read")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise HumanReviewInputError(f"{label} exceeds its byte limit")
            fd_after = os.fstat(stream.fileno())
        after = path.lstat()
    except HumanReviewInputError:
        raise
    except OSError as exc:
        raise HumanReviewInputError(f"{label} cannot be read") from exc
    if (
        _same_surface_signature(fd_before)
        != _same_surface_signature(fd_after)
        or _same_surface_signature(before) != _same_surface_signature(after)
        or _cross_surface_signature(fd_after) != _cross_surface_signature(after)
        or len(payload) != before.st_size
    ):
        raise HumanReviewInputError(f"{label} changed while being read")
    return bytes(payload)


def _prepare_output(path: Path, root: Path) -> Path:
    output = _below_root(path, root, label="human review policy output")
    if output == root:
        raise HumanReviewInputError(
            "human review policy output must remain below the evidence root"
        )
    if output.exists() or output.is_symlink():
        raise HumanReviewInputError("human review policy output must be absent")
    current = root
    try:
        for part in output.parent.relative_to(root).parts:
            current = current / part
            if current.exists() or current.is_symlink():
                inspected = current.lstat()
                if _is_linklike(current, inspected) or not stat.S_ISDIR(inspected.st_mode):
                    raise HumanReviewInputError(
                        "human review policy output parent must be a real directory"
                    )
            else:
                current.mkdir()
        parent = output.parent.resolve(strict=True)
    except HumanReviewInputError:
        raise
    except OSError as exc:
        raise HumanReviewInputError("human review policy output parent is unavailable") from exc
    if os.path.normcase(str(parent)) != os.path.normcase(str(output.parent)):
        raise HumanReviewInputError(
            "human review policy output parent must remain below the evidence root"
        )
    return output


def _viewer_policy(payload: bytes) -> ViewerPerformancePolicy:
    try:
        policy = ViewerPerformancePolicy.model_validate_json(payload)
    except ValidationError as exc:
        raise HumanReviewInputError("Viewer capture policy is invalid") from exc
    if payload != canonical_viewer_performance_policy_bytes(policy):
        raise HumanReviewInputError("Viewer capture policy is not canonical JSON")
    return policy


def materialize_human_review_policy(
    *,
    evidence_root: Path,
    viewer_policy_path: Path,
    viewer_report_path: Path,
    output_path: Path,
) -> HumanReviewPolicyMaterialization:
    root = _real_evidence_root(Path(evidence_root))
    policy_path = _below_root(
        Path(viewer_policy_path),
        root,
        label="Viewer capture policy",
    )
    report_path = _below_root(
        Path(viewer_report_path),
        root,
        label="Viewer capture report",
    )
    policy_payload = _read_regular_bytes(
        policy_path,
        root=root,
        label="Viewer capture policy",
    )
    report_payload = _read_regular_bytes(
        report_path,
        root=root,
        label="Viewer capture report",
    )
    policy = _viewer_policy(policy_payload)
    try:
        report = load_viewer_performance_report_bytes(report_payload)
        if not isinstance(report, ViewerPerformanceReportV2):
            raise ViewerAcceptanceError("human review requires a Viewer v2 capture report")
        expected_policy = root.joinpath(*Path(report.viewer_policy.path).parts)
        if os.path.normcase(str(policy_path)) != os.path.normcase(str(expected_policy)):
            raise ViewerAcceptanceError(
                "Viewer capture policy path differs from its report binding"
            )
        verify_viewer_capture_report(policy, report, root)
    except ViewerAcceptanceError as exc:
        raise HumanReviewInputError(f"Viewer capture cannot be verified: {exc}") from exc

    human_policy = HumanReviewPolicy(
        source_role="production-acceptance",
        required_categories=REQUIRED_VISUAL_CATEGORIES,
        required_pose_ids=tuple(row.pose_id for row in report.poses),
        maximum_screenshot_bytes=PRODUCTION_MAXIMUM_SCREENSHOT_BYTES,
    )
    payload = canonical_human_review_policy_bytes(human_policy)
    output = _prepare_output(Path(output_path), root)
    parent_identity = capture_real_directory_identity(output.parent)
    staging = output.parent / (f".{output.name}.{uuid.uuid4().hex}.staging")
    try:
        if not matches_real_directory_identity(output.parent, parent_identity):
            raise HumanReviewInputError(
                "human review policy output parent changed before write"
            )
        staging_fd = os.open(
            staging,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        # Re-verify parent identity AFTER opening the staging descriptor.
        # O_NOFOLLOW only protects the final path component; ancestor symlinks
        # are still followed, so a parent swap between the pre-open check and
        # os.open would redirect the staging file.  This post-open check
        # closes that TOCTOU window.
        if not matches_real_directory_identity(output.parent, parent_identity):
            try:
                os.close(staging_fd)
            except OSError:
                pass
            raise HumanReviewInputError(
                "human review policy output parent changed before staging"
            )
        try:
            with os.fdopen(staging_fd, "wb") as stream:
                stream.write(payload)
        except OSError as exc:
            raise HumanReviewInputError(
                "human review policy output cannot be written"
            ) from exc
        flush_file(staging)
        publish_file_noreplace(staging, output)
    except (DurableIOError, OSError) as exc:
        state = (
            "published but durability is unconfirmed"
            if isinstance(exc, DurableIOError) and exc.published
            else "not published"
        )
        raise HumanReviewInputError(
            f"human review policy output cannot be published ({state})"
        ) from exc
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
    return HumanReviewPolicyMaterialization(
        output_path=output,
        policy_sha256=hashlib.sha256(payload).hexdigest(),
        viewer_report_sha256=report.content_sha256,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Derive a canonical human-review policy from one verified production Viewer capture."
        )
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--viewer-policy", type=Path, required=True)
    parser.add_argument("--viewer-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = materialize_human_review_policy(
            evidence_root=args.evidence_root,
            viewer_policy_path=args.viewer_policy,
            viewer_report_path=args.viewer_report,
            output_path=args.output,
        )
    except HumanReviewInputError as exc:
        print(f"Human review input materialization failed: {exc}")
        return 2
    print(f"Human review policy: {result.output_path}")
    print(f"Policy SHA-256: {result.policy_sha256}")
    print(f"Viewer report SHA-256: {result.viewer_report_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
