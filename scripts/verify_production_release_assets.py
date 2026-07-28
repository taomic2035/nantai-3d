#!/usr/bin/env python3
"""Verify a downloaded four-file Nantai Production Release bundle."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.production_release_assets import (  # noqa: E402
    ProductionReleaseAssetsError,
    verify_production_release_assets,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify all four downloaded Production Release assets as "
            "one content-bound bundle."
        ),
    )
    parser.add_argument("bundle_dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_production_release_assets(
            arguments.bundle_dir
        )
    except (ProductionReleaseAssetsError, OSError) as exc:
        message = str(exc).encode(
            "ascii",
            "backslashreplace",
        ).decode("ascii")
        print(
            f"Production release asset verification failed: {message}",
            file=sys.stderr,
        )
        return 2
    payload = asdict(result)
    for key in ("bundle_dir", "archive_path"):
        payload[key] = str(payload[key])
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
