#!/usr/bin/env python3
"""Build the deterministic Nantai 3D Preview runtime archive."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pipeline.preview_release import ReleaseVerificationError, build_release_archive

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = Path("release/preview2-inputs.json")
DEFAULT_OUTPUT = Path(
    ".nantai-studio/releases/v1.0.0-preview.2/"
    "nantai-3d-v1.0.0-preview.2-runtime.zip"
)
_RELEASE_OWNED_FILES = {
    "LICENSE",
    "README.md",
    "assets/registry.json",
    "make.py",
    "pyproject.toml",
}
_RELEASE_OWNED_PREFIXES = (
    "docs/manual/",
    "docs/releases/",
    "pipeline/",
    "release/",
    "scripts/",
    "web/studio/",
    "web/viewer/",
)


def _git_source_commit(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()


def _git_tracked_files(root: Path) -> list[str]:
    payload = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=root,
    )
    return sorted(
        path.decode("utf-8")
        for path in payload.split(b"\0")
        if path
    )


def _git_name_list(root: Path, command: list[str]) -> set[str]:
    payload = subprocess.check_output(command, cwd=root)
    return {
        path.decode("utf-8")
        for path in payload.split(b"\0")
        if path
    }


def _git_assert_release_inputs_clean(root: Path) -> None:
    changed = set()
    changed.update(
        _git_name_list(root, ["git", "diff", "--name-only", "-z", "--"])
    )
    changed.update(
        _git_name_list(root, ["git", "diff", "--cached", "--name-only", "-z", "--"])
    )
    changed.update(
        _git_name_list(
            root,
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        )
    )
    release_owned = sorted(
        path
        for path in changed
        if path in _RELEASE_OWNED_FILES
        or any(path.startswith(prefix) for prefix in _RELEASE_OWNED_PREFIXES)
    )
    if release_owned:
        raise ReleaseVerificationError(
            f"dirty release-owned source: {release_owned[0]}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--input-lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-commit")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repo_root.resolve()
    lock = args.input_lock
    if not lock.is_absolute():
        lock = root / lock
    output = args.output
    if not output.is_absolute():
        output = root / output
    try:
        _git_assert_release_inputs_clean(root)
        source_commit = args.source_commit or _git_source_commit(root)
        tracked_files = _git_tracked_files(root)
        report = build_release_archive(
            root,
            output,
            input_lock_path=lock,
            source_commit=source_commit,
            tracked_files=tracked_files,
        )
    except (OSError, ReleaseVerificationError, subprocess.CalledProcessError) as exc:
        print(f"Preview build failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "archive": str(report.archive_path),
        "archive_sha256": report.archive_sha256,
        "package_content_id": report.package_content_id,
        "artifact_count": report.artifact_count,
        "total_bytes": report.total_bytes,
        "asset_count": report.asset_count,
        "world_chunk_count": report.world_chunk_count,
        "gaussian_count": report.gaussian_count,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "Preview runtime built:"
            f" {report.archive_path}\n"
            f"  sha256: {report.archive_sha256}\n"
            f"  package: {report.package_content_id}\n"
            f"  artifacts: {report.artifact_count}"
            f" ({report.total_bytes} bytes)\n"
            f"  scene: {report.world_chunk_count} chunks,"
            f" {report.asset_count} assets,"
            f" {report.gaussian_count} Gaussians"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
