#!/usr/bin/env python3
"""Audit a verified Nantai Production runtime for private material."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
_RELEASE_ROOT = Path(__file__).resolve().parents[1]
if str(_RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_RELEASE_ROOT))

from pipeline.production_release_privacy import (  # noqa: E402
    ProductionReleasePrivacyError,
    audit_production_release_privacy,
    publish_privacy_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and privacy-audit a Production runtime tree or ZIP."
        ),
    )
    parser.add_argument("target", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.target.is_dir():
            try:
                release_root = arguments.target.resolve(strict=True)
                report_path = arguments.report.resolve(strict=False)
            except OSError as exc:
                raise ProductionReleasePrivacyError(
                    "privacy report location cannot be resolved"
                ) from exc
            if report_path.is_relative_to(release_root):
                raise ProductionReleasePrivacyError(
                    "privacy report must remain outside the public release"
                )
        report = audit_production_release_privacy(
            arguments.target,
            arguments.policy,
        )
        publish_privacy_report(report, arguments.report)
    except (ProductionReleasePrivacyError, OSError) as exc:
        message = str(exc).encode("ascii", "backslashreplace").decode("ascii")
        print(f"privacy audit error: {message}", file=sys.stderr)
        return 2

    if not report.valid:
        print(
            f"privacy audit failed: {report.finding_count} finding(s)",
            file=sys.stderr,
        )
        return 2
    print(f"privacy audit passed {report.package_content_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
