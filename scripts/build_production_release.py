#!/usr/bin/env python3
"""Build one verified Nantai Production runtime archive."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.production_release_builder import (  # noqa: E402
    ProductionReleaseBuilderError,
    build_production_release_archive,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed Nantai Production runtime ZIP.",
    )
    parser.add_argument("--acceptance-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _git_output(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProductionReleaseBuilderError(
            "Git source identity cannot be resolved"
        )
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        source_commit = _git_output(
            ["rev-parse", "--verify", "HEAD"]
        ).strip()
        tracked_files = tuple(
            relative
            for relative in _git_output(["ls-files", "-z"]).split("\0")
            if relative
        )
        result = build_production_release_archive(
            repo_root=_REPO_ROOT,
            acceptance_root=arguments.acceptance_root,
            output_path=arguments.output,
            version=arguments.version,
            source_commit=source_commit,
            tracked_files=tracked_files,
        )
    except (ProductionReleaseBuilderError, OSError) as exc:
        message = str(exc).encode(
            "ascii",
            "backslashreplace",
        ).decode("ascii")
        print(f"Production release build failed: {message}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                **asdict(result),
                "archive_path": str(result.archive_path),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
