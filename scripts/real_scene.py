#!/usr/bin/env python3
"""Run one content-addressed stage of the real reconstruction golden path."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from pipeline.real_dataset import (
    DatasetEvidenceError,
    LocalCaptureSource,
    load_real_dataset_source,
)
from pipeline.real_scene_runner import (
    RealSceneBlockedError,
    RealSceneRunOptions,
    run_real_scene,
)
from pipeline.recon_schema import GeoAnchor

_TARGETS = (
    "fetch",
    "sfm",
    "train-preview",
    "train-production",
    "import",
    "accept",
    "serve",
    "all",
)


def _geo_origin(value: str | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    pieces = value.split(",")
    if len(pieces) != 3:
        raise ValueError(
            "--geo-origin requires LAT,LON,ALT with exactly three values"
        )
    try:
        coordinates = tuple(float(piece) for piece in pieces)
    except ValueError as exc:
        raise ValueError(
            "--geo-origin values must be finite numbers"
        ) from exc
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
        description=(
            "Resume-safe real image/video reconstruction orchestration"
        )
    )
    parser.add_argument("target", choices=_TARGETS)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(".nantai-studio/real-scene"),
    )
    parser.add_argument("--run-id", default="default")
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--rights", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--control-points", type=Path)
    parser.add_argument("--geo-origin")
    parser.add_argument("--remote-config", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry", action="store_true")
    return parser


def _validate_runtime_inputs(args, source) -> None:
    if args.resume and args.retry:
        raise ValueError("--resume and --retry are mutually exclusive")
    if isinstance(source, LocalCaptureSource):
        if args.media_root is None or args.rights is None:
            raise ValueError(
                "local-capture requires --media-root and --rights"
            )
        if args.policy is None:
            raise ValueError("local-capture requires an explicit --policy")
        if args.target in {"import", "accept", "serve", "all"} and (
            args.control_points is None or args.geo_origin is None
        ):
            raise ValueError(
                "production import/accept/serve requires --control-points "
                "and --geo-origin"
            )
    needs_remote = args.target == "train-production" or (
        args.target == "all" and isinstance(source, LocalCaptureSource)
    )
    if needs_remote and args.remote_config is None:
        raise ValueError(
            "train-production requires --remote-config"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        geo_origin = _geo_origin(args.geo_origin)
        source = load_real_dataset_source(args.source)
        _validate_runtime_inputs(args, source)
        options = RealSceneRunOptions(
            workspace_base=args.workspace,
            run_id=args.run_id,
            media_root=args.media_root,
            rights_path=args.rights,
            policy_path=args.policy,
            control_points_path=args.control_points,
            geo_origin=geo_origin,
            remote_config_path=args.remote_config,
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
