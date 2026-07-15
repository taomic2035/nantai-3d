"""Command-line entry point for private synthetic-village asset operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


DEFAULT_VISUAL_PACK_ROOT = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"


def _import_visual_source():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.visual_sources import import_visual_source

    return import_visual_source


def _run_canary_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.canary import run_canary_build

    return run_canary_build


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    import_visual = commands.add_parser(
        "import-visual",
        help="Import one declared image2 source into the private content-addressed pack.",
    )
    import_visual.add_argument("--slot", required=True)
    import_visual.add_argument("--source", type=Path, required=True)
    import_visual.add_argument("--source-manifest", type=Path, required=True)
    build_canary = commands.add_parser(
        "build-canary",
        help="Build, verify, and privately publish the Blender foundation canary.",
    )
    build_canary.add_argument("--timeout-seconds", type=int, default=30 * 60)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-visual":
        record = _import_visual_source()(
            slot_id=args.slot,
            source=args.source,
            source_manifest=args.source_manifest,
            pack_root=DEFAULT_VISUAL_PACK_ROOT,
        )
        print(json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "build-canary":
        result = _run_canary_build()(
            repo_root=ROOT,
            visual_pack_root=DEFAULT_VISUAL_PACK_ROOT,
            timeout_seconds=args.timeout_seconds,
        )
        report = result.report
        print(
            json.dumps(
                {
                    "artifact_count": len(report.artifacts),
                    "build_id": report.build_id,
                    "camera_count": len(report.camera_registry),
                    "final_directory": str(result.final_directory),
                    "preview_count": len(report.preview_registry),
                    "verification_level": report.verification_level,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
