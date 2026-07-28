#!/usr/bin/env python3
"""Run one content-addressed stage of the real reconstruction golden path."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.real_dataset import (  # noqa: E402
    DatasetEvidenceError,
    LocalCaptureSource,
    load_real_dataset_source,
)
from pipeline.real_scene_runner import (  # noqa: E402
    RealSceneBlockedError,
    RealSceneRunOptions,
    RealSceneStatusError,
    canonical_snapshot_bytes,
    run_real_scene,
    snapshot_real_scene_stages,
)
from pipeline.recon_schema import GeoAnchor  # noqa: E402
from pipeline.remote_shell_executor import (  # noqa: E402
    RemoteShellExecutionError,
    canonical_remote_shell_preflight_bytes,
    publish_remote_shell_preflight,
    run_remote_shell_preflight_from_path,
)

_TARGETS = (
    "preflight-remote",
    "fetch",
    "sfm",
    "train-preview",
    "train-production",
    "import",
    "accept",
    "serve",
    "status",
    "all",
)


def _geo_origin(value: str | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    pieces = value.split(",")
    if len(pieces) != 3:
        raise ValueError("--geo-origin requires LAT,LON,ALT with exactly three values")
    try:
        coordinates = tuple(float(piece) for piece in pieces)
    except ValueError as exc:
        raise ValueError("--geo-origin values must be finite numbers") from exc
    if not all(math.isfinite(item) for item in coordinates):
        raise ValueError("--geo-origin values must be finite numbers")
    try:
        anchor = GeoAnchor(
            lat=coordinates[0],
            lon=coordinates[1],
            alt=coordinates[2],
        )
    except ValueError as exc:
        raise ValueError(f"--geo-origin is invalid: {exc}") from exc
    return anchor.lat, anchor.lon, anchor.alt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Resume-safe real image/video reconstruction orchestration")
    )
    parser.add_argument("target", choices=_TARGETS)
    parser.add_argument("--source", type=Path)
    parser.add_argument(
        "--workspace",
        type=Path,
    )
    parser.add_argument("--run-id")
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--rights", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--control-points", type=Path)
    parser.add_argument("--geo-origin")
    parser.add_argument("--remote-config", type=Path)
    parser.add_argument("--preflight-report", type=Path)
    parser.add_argument("--viewer-policy", type=Path)
    parser.add_argument("--viewer-report", type=Path)
    parser.add_argument("--human-review-policy", type=Path)
    parser.add_argument("--human-visual-review", type=Path)
    parser.add_argument("--chunk-size", type=float)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry", action="store_true")
    return parser


def _validate_runtime_inputs(args, source) -> None:
    if args.resume and args.retry:
        raise ValueError("--resume and --retry are mutually exclusive")
    if isinstance(source, LocalCaptureSource):
        if args.media_root is None or args.rights is None:
            raise ValueError("local-capture requires --media-root and --rights")
        if args.policy is None:
            raise ValueError("local-capture requires an explicit --policy")
        if args.target in {"import", "accept", "serve", "all"} and (
            args.control_points is None or args.geo_origin is None
        ):
            raise ValueError(
                "production import/accept/serve requires --control-points and --geo-origin"
            )
    needs_remote = args.target == "train-production" or (
        args.target == "all" and isinstance(source, LocalCaptureSource)
    )
    if needs_remote and args.remote_config is None:
        raise ValueError("train-production requires --remote-config")


def _run_status(args) -> int:
    try:
        if args.source is None:
            raise ValueError("status requires --source")
        if args.workspace is None:
            raise ValueError("status requires --workspace")
        if args.run_id is None:
            raise ValueError("status requires --run-id")
        irrelevant = (
            args.media_root,
            args.rights,
            args.policy,
            args.control_points,
            args.geo_origin,
            args.remote_config,
            args.preflight_report,
            args.viewer_policy,
            args.viewer_report,
            args.human_review_policy,
            args.human_visual_review,
            args.chunk_size,
        )
        if any(value is not None for value in irrelevant) or args.resume or args.retry:
            raise ValueError(
                "status accepts only --source, --workspace and --run-id"
            )
        snapshot = snapshot_real_scene_stages(
            args.source,
            workspace_base=args.workspace,
            run_id=args.run_id,
        )
    except (DatasetEvidenceError, RealSceneStatusError, ValueError):
        print("real-scene status invalid", file=sys.stderr)
        return 1
    print(canonical_snapshot_bytes(snapshot).decode("ascii"), end="")
    if snapshot.state == "accepted-from-authoritative-decision":
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.target == "preflight-remote":
            if args.remote_config is None:
                raise ValueError(
                    "preflight-remote requires --remote-config"
                )
            if args.preflight_report is None:
                raise ValueError(
                    "preflight-remote requires --preflight-report"
                )
            irrelevant = (
                args.source,
                args.workspace,
                args.run_id,
                args.media_root,
                args.rights,
                args.policy,
                args.control_points,
                args.geo_origin,
                args.viewer_policy,
                args.viewer_report,
                args.human_review_policy,
                args.human_visual_review,
                args.chunk_size,
            )
            if (
                any(value is not None for value in irrelevant)
                or args.resume
                or args.retry
            ):
                raise ValueError(
                    "preflight-remote accepts only --remote-config and"
                    " --preflight-report"
                )
            report = run_remote_shell_preflight_from_path(
                args.remote_config,
            )
            publish_remote_shell_preflight(
                report,
                args.preflight_report,
            )
            print(
                canonical_remote_shell_preflight_bytes(
                    report,
                ).decode("ascii"),
                end="",
            )
            return 0 if report.status == "ready" else 2
        if args.target == "status":
            return _run_status(args)
        if args.source is None:
            raise ValueError(
                f"{args.target} requires --source"
            )
        geo_origin = _geo_origin(args.geo_origin)
        source = load_real_dataset_source(args.source)
        _validate_runtime_inputs(args, source)
        options = RealSceneRunOptions(
            workspace_base=(
                args.workspace
                if args.workspace is not None
                else Path(".nantai-studio/real-scene")
            ),
            run_id=(
                args.run_id
                if args.run_id is not None
                else "default"
            ),
            media_root=args.media_root,
            rights_path=args.rights,
            policy_path=args.policy,
            control_points_path=args.control_points,
            geo_origin=geo_origin,
            remote_config_path=args.remote_config,
            viewer_policy_path=args.viewer_policy,
            viewer_report_path=args.viewer_report,
            human_review_policy_path=args.human_review_policy,
            human_visual_review_path=args.human_visual_review,
            chunk_size=(
                args.chunk_size
                if args.chunk_size is not None
                else 50.0
            ),
        )
        receipt = run_real_scene(
            args.source,
            args.target,
            options,
            resume=args.resume,
            retry=args.retry,
        )
    except (
        DatasetEvidenceError,
        RealSceneBlockedError,
        RemoteShellExecutionError,
        ValueError,
    ) as exc:
        print(f"real-scene blocked: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(
        receipt.model_dump(mode="json", by_alias=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
