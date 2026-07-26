#!/usr/bin/env python3
"""Verify a Nantai 3D Preview runtime archive or extracted tree."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from pipeline.preview_release import (
    ReleaseVerificationError,
    verify_release_archive,
    verify_release_tree,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    target = args.target
    try:
        report = (
            verify_release_tree(target)
            if target.is_dir()
            else verify_release_archive(target)
        )
    except (OSError, ReleaseVerificationError, zipfile.BadZipFile) as exc:
        print(f"Preview verification failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "valid": report.valid,
        "version": report.version,
        "source_commit": report.source_commit,
        "package_content_id": report.package_content_id,
        "artifact_count": report.artifact_count,
        "total_bytes": report.total_bytes,
        "scene_trust_effect": report.scene_trust_effect,
        "errors": list(report.errors),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"Preview package verified: {report.version}\n"
            f"  source: {report.source_commit}\n"
            f"  package: {report.package_content_id}\n"
            f"  artifacts: {report.artifact_count}"
            f" ({report.total_bytes} bytes)\n"
            f"  scene trust effect: {report.scene_trust_effect}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
