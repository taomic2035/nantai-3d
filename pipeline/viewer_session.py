"""Run one provenance-bound production Viewer capture against a private import."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from pipeline.studio_server import make_server


class ViewerSessionError(ValueError):
    """Raised when a production Viewer session cannot remain fail closed."""


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


def _require_regular_file(path: Path, *, label: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ViewerSessionError(
            f"{label} must be an existing regular file"
        ) from exc
    if (
        _is_linklike(candidate)
        or not candidate.is_file()
        or resolved != candidate
    ):
        raise ViewerSessionError(
            f"{label} must be an existing regular file"
        )
    return candidate


def _require_absent(path: Path, *, label: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    if candidate.exists() or _is_linklike(candidate):
        raise ViewerSessionError(f"{label} already exists")
    return candidate


def _require_below_evidence_root(
    evidence_root: Path,
    path: Path,
    *,
    label: str,
) -> None:
    try:
        relative = path.relative_to(evidence_root)
    except ValueError as exc:
        raise ViewerSessionError(
            f"{label} must stay below the evidence root"
        ) from exc
    if not relative.parts:
        raise ViewerSessionError(
            f"{label} must stay below the evidence root"
        )


@dataclass(frozen=True)
class ViewerSessionOptions:
    project_root: Path
    import_root: Path
    policy_path: Path
    camera_set_path: Path
    output_path: Path
    decision_path: Path
    evidence_root: Path
    node_executable: Path
    python_executable: Path
    headless: bool = False
    measurement_timeout_ms: int = 120_000


def _validated_options(
    options: ViewerSessionOptions,
) -> ViewerSessionOptions:
    project_root = Path(options.project_root).expanduser().absolute()
    import_root = Path(options.import_root).expanduser().absolute()
    evidence_root = Path(options.evidence_root).expanduser().absolute()
    for path, label in (
        (project_root, "project root"),
        (import_root, "real-scene import root"),
        (evidence_root, "evidence root"),
    ):
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ViewerSessionError(
                f"{label} must be an existing regular directory"
            ) from exc
        if _is_linklike(path) or not path.is_dir() or resolved != path:
            raise ViewerSessionError(
                f"{label} must be an existing regular directory"
            )
    capture_script = _require_regular_file(
        project_root / "scripts/capture_viewer_acceptance.mjs",
        label="Viewer capture script",
    )
    del capture_script
    _require_regular_file(
        import_root / "web/recon_manifest.json",
        label="scene manifest",
    )
    policy_path = _require_regular_file(
        options.policy_path,
        label="Viewer policy",
    )
    camera_set_path = _require_regular_file(
        options.camera_set_path,
        label="Viewer camera set",
    )
    node_executable = _require_regular_file(
        options.node_executable,
        label="Node executable",
    )
    python_executable = _require_regular_file(
        options.python_executable,
        label="Python executable",
    )
    output_path = _require_absent(
        options.output_path,
        label="Viewer report",
    )
    decision_path = _require_absent(
        options.decision_path,
        label="Viewer decision",
    )
    for path, label in (
        (import_root, "real-scene import root"),
        (policy_path, "Viewer policy"),
        (camera_set_path, "Viewer camera set"),
        (output_path, "Viewer report"),
        (decision_path, "Viewer decision"),
    ):
        _require_below_evidence_root(
            evidence_root,
            path,
            label=label,
        )
    if (
        isinstance(options.measurement_timeout_ms, bool)
        or not isinstance(options.measurement_timeout_ms, int)
        or options.measurement_timeout_ms <= 0
    ):
        raise ViewerSessionError(
            "measurement timeout must be a positive integer"
        )
    return ViewerSessionOptions(
        project_root=project_root,
        import_root=import_root,
        policy_path=policy_path,
        camera_set_path=camera_set_path,
        output_path=output_path,
        decision_path=decision_path,
        evidence_root=evidence_root,
        node_executable=node_executable,
        python_executable=python_executable,
        headless=options.headless,
        measurement_timeout_ms=options.measurement_timeout_ms,
    )


def _capture_argv(
    options: ViewerSessionOptions,
    *,
    studio_url: str,
) -> list[str]:
    argv = [
        str(options.node_executable),
        str(
            options.project_root
            / "scripts/capture_viewer_acceptance.mjs"
        ),
        "--policy",
        str(options.policy_path),
        "--camera-set",
        str(options.camera_set_path),
        "--studio-url",
        studio_url,
        "--scene-manifest",
        str(options.import_root / "web/recon_manifest.json"),
        "--output",
        str(options.output_path),
        "--decision",
        str(options.decision_path),
        "--source-role",
        "production-acceptance",
        "--evidence-root",
        str(options.evidence_root),
        "--python",
        str(options.python_executable),
        "--measurement-timeout-ms",
        str(options.measurement_timeout_ms),
    ]
    if options.headless:
        argv.append("--headless")
    return argv


def run_production_viewer_session(
    options: ViewerSessionOptions,
) -> int:
    """Start a bound Studio server, run capture, and close it on every path."""

    validated = _validated_options(options)
    try:
        server = make_server(
            validated.project_root,
            host="127.0.0.1",
            port=0,
            real_scene_import_root=validated.import_root,
        )
    except (OSError, ValueError) as exc:
        raise ViewerSessionError(
            f"verified Studio server could not start: {exc}"
        ) from exc
    host, port = server.server_address[:2]
    if host != "127.0.0.1" or not isinstance(port, int) or port <= 0:
        server.server_close()
        raise ViewerSessionError(
            "verified Studio server did not bind numeric loopback"
        )
    thread = threading.Thread(
        target=server.serve_forever,
        name="nantai-production-viewer-session",
        daemon=True,
    )
    thread.start()
    process_error: OSError | None = None
    return_code: int | None = None
    try:
        result = subprocess.run(
            _capture_argv(
                validated,
                studio_url=(
                    f"http://127.0.0.1:{port}/web/studio/"
                ),
            ),
            cwd=validated.project_root,
            check=False,
        )
        return_code = result.returncode
    except OSError as exc:
        process_error = exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if thread.is_alive():
        raise ViewerSessionError(
            "verified Studio server did not stop cleanly"
        )
    if process_error is not None:
        raise ViewerSessionError(
            "Viewer capture process could not be started"
        ) from process_error
    if not isinstance(return_code, int):
        raise ViewerSessionError(
            "Viewer capture process returned no exit status"
        )
    return return_code


def _resolve_executable(value: str, *, label: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute() or raw.parent != Path("."):
        return raw.absolute()
    resolved = shutil.which(value)
    if resolved is None:
        raise ViewerSessionError(f"{label} executable was not found")
    return Path(resolved).absolute()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture production Viewer evidence against one verified import."
        )
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--import-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--camera-set", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decision", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--node", default="node")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--measurement-timeout-ms",
        type=int,
        default=120_000,
    )
    parser.add_argument("--headless", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        node_executable = _resolve_executable(
            args.node,
            label="Node",
        )
        python_executable = _resolve_executable(
            args.python,
            label="Python",
        )
        output_path = Path(args.output).expanduser().absolute()
        decision_path = (
            Path(args.decision).expanduser().absolute()
            if args.decision is not None
            else Path(f"{output_path}.decision.json")
        )
        return run_production_viewer_session(
            ViewerSessionOptions(
                project_root=Path(
                    args.project_root
                ).expanduser().absolute(),
                import_root=Path(args.import_root).expanduser().absolute(),
                policy_path=Path(args.policy).expanduser().absolute(),
                camera_set_path=Path(
                    args.camera_set
                ).expanduser().absolute(),
                output_path=output_path,
                decision_path=decision_path,
                evidence_root=Path(
                    args.evidence_root
                ).expanduser().absolute(),
                node_executable=node_executable,
                python_executable=python_executable,
                headless=args.headless,
                measurement_timeout_ms=args.measurement_timeout_ms,
            )
        )
    except ViewerSessionError as exc:
        print(f"production Viewer session blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
