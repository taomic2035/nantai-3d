"""Command-line entry point for private synthetic-village asset operations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


DEFAULT_VISUAL_PACK_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"
)


def _import_visual_source():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.visual_sources import import_visual_source

    return import_visual_source


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
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
