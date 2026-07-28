#!/usr/bin/env python3
"""Stage the four verified public assets for one Production release."""

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
    stage_production_release_assets,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify, privacy-audit and stage exactly four public "
            "Production Release assets."
        ),
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--privacy-policy",
        type=Path,
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--acceptance-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = stage_production_release_assets(
            repo_root=_REPO_ROOT,
            acceptance_root=arguments.acceptance_root,
            version=arguments.version,
            archive_path=arguments.archive,
            privacy_policy_path=arguments.privacy_policy,
            output_dir=arguments.output_dir,
        )
    except (ProductionReleaseAssetsError, OSError) as exc:
        message = str(exc).encode(
            "ascii",
            "backslashreplace",
        ).decode("ascii")
        print(
            f"Production release asset staging failed: {message}",
            file=sys.stderr,
        )
        return 2
    payload = asdict(result)
    for key in (
        "output_dir",
        "archive_path",
        "receipt_path",
        "checksums_path",
    ):
        payload[key] = str(payload[key])
    payload["retained_private_paths"] = [
        str(path) for path in payload["retained_private_paths"]
    ]
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
