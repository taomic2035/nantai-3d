#!/usr/bin/env python3
"""Record an explicit, content-addressed human visual review receipt."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.real_scene_acceptance import (  # noqa: E402
    HumanReviewPolicy,
    RealSceneAcceptanceError,
    canonical_human_review_bytes,
    canonical_human_review_policy_bytes,
    record_human_visual_review,
    validate_human_visual_review,
)
from pipeline.viewer_acceptance import (  # noqa: E402
    ViewerAcceptanceError,
    ViewerPerformancePolicy,
    ViewerPerformanceReportV2,
    canonical_viewer_performance_policy_bytes,
    load_viewer_performance_report_bytes,
    verify_viewer_capture_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record explicit real-scene visual dispositions. Missing categories remain unknown."
        )
    )
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument(
        "--disposition",
        action="append",
        default=[],
        metavar="CATEGORY=accepted|rejected|unknown",
    )
    screenshots = parser.add_mutually_exclusive_group()
    screenshots.add_argument(
        "--screenshot",
        action="append",
        default=[],
        metavar="POSE_ID=RELATIVE.png",
    )
    screenshots.add_argument(
        "--viewer-report",
        help=("Verified Viewer v2 report whose screenshot bindings are reused for this review"),
    )
    parser.add_argument("--reviewed-at")
    parser.add_argument("--output")
    return parser


def _pairs(values: list[str], *, label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item or key in parsed:
            raise RealSceneAcceptanceError(f"{label} must use unique KEY=VALUE entries")
        parsed[key] = item
    return parsed


def _reviewed_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RealSceneAcceptanceError("reviewed-at must be an ISO-8601 timestamp") from exc


def _load_policy(path: Path) -> HumanReviewPolicy:
    try:
        payload = path.read_bytes()
        policy = HumanReviewPolicy.model_validate_json(payload)
    except (OSError, ValidationError) as exc:
        raise RealSceneAcceptanceError(f"human review policy is invalid: {exc}") from exc
    if payload != canonical_human_review_policy_bytes(policy):
        raise RealSceneAcceptanceError("human review policy is not canonical JSON")
    return policy


def _identity(item: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        item.st_dev,
        item.st_ino,
        stat.S_IFMT(item.st_mode),
        item.st_size,
        item.st_mtime_ns,
    )


def _read_evidence_file(
    root: Path,
    path: Path,
    *,
    label: str,
) -> bytes:
    try:
        root_real = root.resolve(strict=True)
        candidate = path if path.is_absolute() else root / path
        candidate = candidate.absolute()
        candidate.relative_to(root_real)
        current = root_real
        for part in candidate.relative_to(root_real).parts:
            current = current / part
            inspected = current.lstat()
            if stat.S_ISLNK(inspected.st_mode):
                raise RealSceneAcceptanceError(f"{label} must not traverse a symlink")
        before = candidate.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RealSceneAcceptanceError(f"{label} must be a real regular file")
        with candidate.open("rb") as stream:
            payload = stream.read()
            after = os.fstat(stream.fileno())
        final = candidate.lstat()
    except RealSceneAcceptanceError:
        raise
    except (OSError, ValueError) as exc:
        raise RealSceneAcceptanceError(f"{label} is unavailable or escapes the run root") from exc
    if not payload or _identity(before) != _identity(after) or _identity(after) != _identity(final):
        raise RealSceneAcceptanceError(f"{label} is empty or changed while being read")
    return payload


def _viewer_screenshots(
    *,
    root: Path,
    report_path: Path,
    human_policy: HumanReviewPolicy,
) -> dict[str, str]:
    report_payload = _read_evidence_file(
        root,
        report_path,
        label="Viewer capture report",
    )
    try:
        report = load_viewer_performance_report_bytes(report_payload)
    except ViewerAcceptanceError as exc:
        raise RealSceneAcceptanceError(f"Viewer capture report is invalid: {exc}") from exc
    if not isinstance(report, ViewerPerformanceReportV2):
        raise RealSceneAcceptanceError("human review requires a Viewer v2 capture report")
    viewer_policy_path = root.joinpath(*PurePosixPath(report.viewer_policy.path).parts)
    viewer_policy_payload = _read_evidence_file(
        root,
        viewer_policy_path,
        label="Viewer capture policy",
    )
    try:
        viewer_policy = ViewerPerformancePolicy.model_validate_json(viewer_policy_payload)
    except ValidationError as exc:
        raise RealSceneAcceptanceError("Viewer capture policy is invalid") from exc
    if viewer_policy_payload != canonical_viewer_performance_policy_bytes(viewer_policy):
        raise RealSceneAcceptanceError("Viewer capture policy is not canonical JSON")
    try:
        verify_viewer_capture_report(
            viewer_policy,
            report,
            root,
        )
    except ViewerAcceptanceError as exc:
        raise RealSceneAcceptanceError(f"Viewer capture cannot be verified: {exc}") from exc
    pose_ids = tuple(row.pose_id for row in report.poses)
    if human_policy.source_role != report.source_role or human_policy.required_pose_ids != pose_ids:
        raise RealSceneAcceptanceError(
            "human review policy differs from Viewer report role or pose order"
        )
    return {screenshot.pose_id: screenshot.path for screenshot in report.screenshots}


def _output_path(root: Path, requested: str | None) -> Path:
    try:
        root_real = root.resolve(strict=True)
    except OSError as exc:
        raise RealSceneAcceptanceError("run root is unavailable") from exc
    output = Path(requested) if requested else Path("evidence/human-visual-review.json")
    if not output.is_absolute():
        output = root / output
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        parent_real = output.parent.resolve(strict=True)
    except OSError as exc:
        raise RealSceneAcceptanceError("review output parent is unavailable") from exc
    if parent_real != root_real and root_real not in parent_real.parents:
        raise RealSceneAcceptanceError("review output must remain below run root")
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RealSceneAcceptanceError("review output cannot be inspected") from exc
    else:
        raise RealSceneAcceptanceError("review output already exists")
    return output


def _publish_review(output: Path, payload: bytes) -> None:
    from pipeline.durable_io import (
        DurableIOError,
        flush_file,
        publish_file_noreplace,
    )

    staging = output.parent / (f".{output.name}.{uuid.uuid4().hex}.staging")
    try:
        with staging.open("xb") as stream:
            stream.write(payload)
        flush_file(staging)
        publish_file_noreplace(staging, output)
    except DurableIOError as exc:
        state = "published but durability is unconfirmed" if exc.published else "not published"
        raise RealSceneAcceptanceError(
            f"human review receipt cannot be published ({state})"
        ) from exc
    except OSError as exc:
        raise RealSceneAcceptanceError("human review receipt cannot be published") from exc
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = Path(args.run_root)
        policy = _load_policy(Path(args.policy))
        screenshots = (
            _viewer_screenshots(
                root=root,
                report_path=Path(args.viewer_report),
                human_policy=policy,
            )
            if args.viewer_report
            else _pairs(
                args.screenshot,
                label="screenshot",
            )
        )
        review = record_human_visual_review(
            policy=policy,
            root=root,
            reviewer=args.reviewer,
            dispositions=_pairs(
                args.disposition,
                label="disposition",
            ),
            screenshots=screenshots,
            reviewed_at=_reviewed_at(args.reviewed_at),
        )
        decision = validate_human_visual_review(
            policy,
            review,
            root,
        )
        output = _output_path(root, args.output)
        _publish_review(
            output,
            canonical_human_review_bytes(review),
        )
    except (
        OSError,
        RealSceneAcceptanceError,
        ValidationError,
    ) as exc:
        print(f"INVALID: {exc}")
        return 2

    if decision.accepted:
        print(f"ACCEPTED: {review.review_id} ({decision.screenshot_count} screenshots)")
        return 0
    details = (
        *(f"{category}=unknown" for category in decision.unknown_categories),
        *(f"{category}=rejected" for category in decision.rejected_categories),
    )
    print(f"PENDING: {review.review_id}; " + ", ".join(details))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
