#!/usr/bin/env python3
"""Execute and publish the fixed remote-training recovery drill suite."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.remote_training_drill import (  # noqa: E402
    RemoteTrainingDrillError,
    canonical_remote_training_drill_bytes,
    publish_remote_training_drill_report,
    run_remote_training_drills,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed P1-3A/B/C transport-fixture drills from a clean "
            "exact Git commit and publish a canonical no-replace report."
        )
    )
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--output", required=True)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--training-config-sha256", required=True)
    parser.add_argument("--dataset-receipt-sha256", required=True)
    parser.add_argument("--trainer-name", required=True)
    parser.add_argument("--trainer-version", required=True)
    parser.add_argument("--container-identity", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_remote_training_drills(
            args.repo_root,
            request_sha256=args.request_sha256,
            training_config_sha256=args.training_config_sha256,
            dataset_receipt_sha256=args.dataset_receipt_sha256,
            trainer_name=args.trainer_name,
            trainer_version=args.trainer_version,
            container_identity=args.container_identity,
        )
        publish_remote_training_drill_report(args.output, report)
    except (RemoteTrainingDrillError, FileExistsError) as exc:
        print(f"remote training drill blocked: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_remote_training_drill_bytes(report))
    return 0 if report.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
