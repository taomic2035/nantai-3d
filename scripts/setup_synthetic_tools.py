"""Install or verify exact synthetic-village tools from the tracked lock."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-ktx-4.4.2",
        dest="install_ktx_4_4_2",
        action="store_true",
        help=(
            "Verify the cached official signed package for the measured host and "
            "prepare a project-private KTX 4.4.2 runtime."
        ),
    )
    commands = parser.add_subparsers(dest="tool")
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


def _current_ktx_runtime():
    from pipeline.synthetic_village.ktx2_toolchain import (
        KTX_DARWIN_ARM64_ASSET,
        KTX_WINDOWS_X64_ASSET,
        KtxToolchainError,
        prepare_private_ktx_runtime,
        prepare_private_windows_ktx_runtime,
    )

    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine == "arm64":
        return (
            KTX_DARWIN_ARM64_ASSET,
            ".nantai-studio/tools/ktx-4.4.2",
            prepare_private_ktx_runtime,
        )
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return (
            KTX_WINDOWS_X64_ASSET,
            ".nantai-studio/tools/ktx-4.4.2-windows-x64",
            prepare_private_windows_ktx_runtime,
        )
    raise KtxToolchainError(
        f"KTX 4.4.2 private install does not support {system} {machine}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.install_ktx_4_4_2:
        if args.tool is not None:
            print(
                "synthetic tool setup failed: choose KTX or Blender, not both",
                file=sys.stderr,
            )
            return 2
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from pipeline.synthetic_village.ktx2_toolchain import KtxToolchainError

        try:
            asset, relative_root, prepare_runtime = _current_ktx_runtime()
            output_root = ROOT / relative_root
            package = output_root / "downloads" / asset
            receipt = prepare_runtime(package, output_root)
        except KtxToolchainError as exc:
            print(f"synthetic tool setup failed: {exc}", file=sys.stderr)
            return 2
        payload = receipt.model_dump(mode="json")
        payload["action"] = "project-private-installed-and-verified"
        payload["receipt"] = str(output_root / "receipt.json")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.tool is None:
        _parser().error("choose --install-ktx-4.4.2 or the blender command")
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
