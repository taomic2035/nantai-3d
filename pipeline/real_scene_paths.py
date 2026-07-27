"""Resolve the content-bound workspace and latest production import paths."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pipeline.real_dataset import DatasetEvidenceError
from pipeline.real_scene_runner import (
    RealSceneBlockedError,
    ResolvedProductionImport,
    resolve_latest_production_import,
)


def canonical_real_scene_paths_bytes(
    resolved: ResolvedProductionImport,
) -> bytes:
    return (
        json.dumps(
            {
                "schema": "nantai.real-scene-paths.v1",
                "workspace_root": str(resolved.workspace_root),
                "import_root": str(resolved.import_root),
                "import_receipt_path": str(
                    resolved.import_receipt_path
                ),
                "stage_receipt_path": str(
                    resolved.stage_receipt_path
                ),
                "stage_receipt_sha256": (
                    resolved.stage_receipt_sha256
                ),
                "source_sha256": resolved.source_sha256,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve and revalidate the latest completed production "
            "import for one real-scene runner identity."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        resolved = resolve_latest_production_import(
            Path(args.source).expanduser().absolute(),
            workspace_base=(
                Path(args.workspace).expanduser().absolute()
            ),
            run_id=args.run_id,
        )
    except (
        DatasetEvidenceError,
        RealSceneBlockedError,
        OSError,
        ValueError,
    ) as exc:
        print(f"real-scene paths blocked: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(
        canonical_real_scene_paths_bytes(resolved)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
