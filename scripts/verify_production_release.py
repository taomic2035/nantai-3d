#!/usr/bin/env python3
"""Offline verifier CLI for Nantai Production runtime releases."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.dont_write_bytecode = True
_RELEASE_ROOT = Path(__file__).resolve().parents[1]
if str(_RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_RELEASE_ROOT))

from pipeline.production_release_verifier import (  # noqa: E402
    ProductionReleaseVerificationError,
    verify_production_release_archive,
    verify_production_release_tree,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a Nantai Production runtime tree or ZIP.",
    )
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one ASCII JSON report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.target.is_dir():
            report = verify_production_release_tree(arguments.target)
        else:
            report = verify_production_release_archive(arguments.target)
    except (ProductionReleaseVerificationError, OSError) as exc:
        message = str(exc).encode("ascii", "backslashreplace").decode("ascii")
        print(f"verification failed: {message}", file=sys.stderr)
        return 2

    if arguments.json:
        print(
            json.dumps(
                asdict(report),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    else:
        print(
            "verified "
            f"{report.version} "
            f"{report.package_content_id} "
            f"{report.release_contract}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
