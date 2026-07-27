#!/usr/bin/env python3
"""Record an explicit, content-addressed human visual review receipt."""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from pipeline.real_scene_acceptance import (
    HumanReviewPolicy,
    RealSceneAcceptanceError,
    canonical_human_review_bytes,
    canonical_human_review_policy_bytes,
    record_human_visual_review,
    validate_human_visual_review,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record explicit real-scene visual dispositions. "
            "Missing categories remain unknown."
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
    parser.add_argument(
        "--screenshot",
        action="append",
        default=[],
        metavar="POSE_ID=RELATIVE.png",
    )
    parser.add_argument("--reviewed-at")
    parser.add_argument("--output")
    return parser


def _pairs(values: list[str], *, label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if (
            not separator
            or not key
            or not item
            or key in parsed
        ):
            raise RealSceneAcceptanceError(
                f"{label} must use unique KEY=VALUE entries"
            )
        parsed[key] = item
    return parsed


def _reviewed_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RealSceneAcceptanceError(
            "reviewed-at must be an ISO-8601 timestamp"
        ) from exc


def _load_policy(path: Path) -> HumanReviewPolicy:
    try:
        payload = path.read_bytes()
        policy = HumanReviewPolicy.model_validate_json(payload)
    except (OSError, ValidationError) as exc:
        raise RealSceneAcceptanceError(
            f"human review policy is invalid: {exc}"
        ) from exc
    if payload != canonical_human_review_policy_bytes(policy):
        raise RealSceneAcceptanceError(
            "human review policy is not canonical JSON"
        )
    return policy


def _output_path(root: Path, requested: str | None) -> Path:
    try:
        root_real = root.resolve(strict=True)
    except OSError as exc:
        raise RealSceneAcceptanceError(
            "run root is unavailable"
        ) from exc
    output = (
        Path(requested)
        if requested
        else Path("evidence/human-visual-review.json")
    )
    if not output.is_absolute():
        output = root / output
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        parent_real = output.parent.resolve(strict=True)
    except OSError as exc:
        raise RealSceneAcceptanceError(
            "review output parent is unavailable"
        ) from exc
    if (
        parent_real != root_real
        and root_real not in parent_real.parents
    ):
        raise RealSceneAcceptanceError(
            "review output must remain below run root"
        )
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RealSceneAcceptanceError(
            "review output cannot be inspected"
        ) from exc
    else:
        raise RealSceneAcceptanceError(
            "review output already exists"
        )
    return output


def _publish_review(output: Path, payload: bytes) -> None:
    from pipeline.durable_io import (
        DurableIOError,
        flush_file,
        publish_file_noreplace,
    )

    staging = output.parent / (
        f".{output.name}.{uuid.uuid4().hex}.staging"
    )
    try:
        with staging.open("xb") as stream:
            stream.write(payload)
        flush_file(staging)
        publish_file_noreplace(staging, output)
    except DurableIOError as exc:
        state = (
            "published but durability is unconfirmed"
            if exc.published
            else "not published"
        )
        raise RealSceneAcceptanceError(
            f"human review receipt cannot be published ({state})"
        ) from exc
    except OSError as exc:
        raise RealSceneAcceptanceError(
            "human review receipt cannot be published"
        ) from exc
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
        review = record_human_visual_review(
            policy=policy,
            root=root,
            reviewer=args.reviewer,
            dispositions=_pairs(
                args.disposition,
                label="disposition",
            ),
            screenshots=_pairs(
                args.screenshot,
                label="screenshot",
            ),
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
        print(
            f"ACCEPTED: {review.review_id} "
            f"({decision.screenshot_count} screenshots)"
        )
        return 0
    details = (
        *(
            f"{category}=unknown"
            for category in decision.unknown_categories
        ),
        *(
            f"{category}=rejected"
            for category in decision.rejected_categories
        ),
    )
    print(
        f"PENDING: {review.review_id}; "
        + ", ".join(details)
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
