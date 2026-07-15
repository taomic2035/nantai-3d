"""Install or verify exact synthetic-village tools from the tracked lock."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="tool", required=True)
    blender = commands.add_parser("blender", help="Manage the pinned Blender LTS runtime.")
    modes = blender.add_mutually_exclusive_group(required=True)
    modes.add_argument("--archive", type=Path, help="Install from a local locked archive.")
    modes.add_argument(
        "--download",
        action="store_true",
        help="Download the locked archive into the private cache, then install.",
    )
    modes.add_argument(
        "--verify-only",
        action="store_true",
        help="Read-only verification of the existing locked installation.",
    )
    return parser


def _tool_lock_api():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village import tool_lock

    return tool_lock


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    api = _tool_lock_api()
    try:
        lock = api.load_tool_lock(ROOT / "tools.lock.json")
        if args.tool != "blender":  # pragma: no cover - parser owns this invariant
            raise AssertionError(f"unhandled tool: {args.tool}")
        tool = lock.blender
        destination = ROOT.joinpath(*PurePosixPath(tool.install_dir).parts)
        if args.verify_only:
            receipt = api.verify_locked_install(tool, destination)
            action = "verified"
        else:
            archive = args.archive
            if args.download:
                archive = api.download_locked_archive(tool)
            receipt = api.install_locked_archive(tool, archive, destination)
            receipt = api.verify_locked_install(tool, destination)
            action = "installed-and-verified"
    except (api.ToolLockError, api.ToolInstallError) as exc:
        print(f"synthetic tool setup failed: {exc}", file=sys.stderr)
        return 2

    payload = receipt.model_dump(mode="json")
    payload["action"] = action
    payload["install_dir"] = tool.install_dir
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
