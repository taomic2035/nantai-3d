"""Run one provenance-bound production Viewer capture against a private import."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from pipeline.durable_io import first_linklike_path
from pipeline.human_review_inputs import (
    HumanReviewInputError,
    materialize_human_review_policy,
)
from pipeline.studio_server import make_server


class ViewerSessionError(ValueError):
    """Raised when a production Viewer session cannot remain fail closed."""


def _is_linklike(path: Path) -> bool:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _require_regular_file(path: Path, *, label: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(
            Path(candidate.anchor),
            candidate,
        )
        observed = candidate.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ViewerSessionError(
            f"{label} must be an existing regular file"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(candidate)
        or not stat.S_ISREG(observed.st_mode)
    ):
        raise ViewerSessionError(
            f"{label} must be an existing regular file"
        )
    return candidate


def _require_absent(path: Path, *, label: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(
            Path(candidate.anchor),
            candidate,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ViewerSessionError(
            f"{label} path cannot be inspected"
        ) from exc
    if redirected is not None:
        raise ViewerSessionError(f"{label} path is redirected")
    try:
        candidate.lstat()
    except FileNotFoundError:
        return candidate
    except OSError as exc:
        raise ViewerSessionError(
            f"{label} path cannot be inspected"
        ) from exc
    raise ViewerSessionError(f"{label} already exists")


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


def _stable_file_identity(
    observed: os.stat_result,
) -> tuple[int, int, int, int, int]:
    """Cross-surface stable identity for a regular file.

    Uses ``S_IFMT`` for the file type so Windows permission-bit differences
    between ``lstat`` and ``fstat`` do not cause false rejections.
    """

    return (
        observed.st_dev,
        observed.st_ino,
        stat.S_IFMT(observed.st_mode),
        observed.st_size,
        observed.st_mtime_ns,
    )


def _capture_launch_identities(
    validated: ViewerSessionOptions,
) -> list[tuple[str, Path, tuple[int, int, int, int, int]]]:
    """Capture stable identities for trust-critical inputs after validation.

    Called immediately after ``_validated_options`` so the launch preflight
    can re-verify each file has not been swapped before ``subprocess.run``.
    """

    capture_script = (
        validated.project_root / "scripts/capture_viewer_acceptance.mjs"
    )
    scene_manifest = validated.import_root / "web/recon_manifest.json"
    entries: list[tuple[str, Path, tuple[int, int, int, int, int]]] = []
    for label, path in (
        ("Viewer capture script", capture_script),
        ("scene manifest", scene_manifest),
        ("Viewer policy", validated.policy_path),
        ("Viewer camera set", validated.camera_set_path),
        ("Node executable", validated.node_executable),
        ("Python executable", validated.python_executable),
    ):
        entries.append((label, path, _stable_file_identity(path.lstat())))
    return entries


def _verify_launch_identity(
    path: Path,
    expected: tuple[int, int, int, int, int],
    *,
    label: str,
) -> None:
    """Re-verify a trust-critical input's identity just before launch."""

    try:
        redirected = first_linklike_path(
            Path(path.absolute().anchor),
            path,
        )
        observed = path.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ViewerSessionError(
            f"{label} is no longer accessible"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(path)
        or not stat.S_ISREG(observed.st_mode)
    ):
        raise ViewerSessionError(
            f"{label} is no longer a regular file"
        )
    actual = _stable_file_identity(observed)
    if actual != expected:
        raise ViewerSessionError(f"{label} changed before launch")


def _verify_output_still_absent(
    path: Path,
    *,
    label: str,
) -> None:
    """Re-verify an output path is still absent just before launch."""

    try:
        redirected = first_linklike_path(
            Path(path.absolute().anchor),
            path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ViewerSessionError(
            f"{label} path cannot be inspected"
        ) from exc
    if redirected is not None:
        raise ViewerSessionError(f"{label} path is redirected")
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ViewerSessionError(
            f"{label} path cannot be inspected"
        ) from exc
    raise ViewerSessionError(f"{label} was pre-occupied before launch")


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
    human_review_policy_output_path: Path | None = None
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
            redirected = first_linklike_path(
                Path(path.anchor),
                path,
            )
            observed = path.lstat()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ViewerSessionError(
                f"{label} must be an existing regular directory"
            ) from exc
        if (
            redirected is not None
            or _is_linklike(path)
            or not stat.S_ISDIR(observed.st_mode)
        ):
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
    human_review_policy_output_path = (
        _require_absent(
            options.human_review_policy_output_path,
            label="human review policy",
        )
        if options.human_review_policy_output_path is not None
        else None
    )
    bounded_paths = [
        (import_root, "real-scene import root"),
        (policy_path, "Viewer policy"),
        (camera_set_path, "Viewer camera set"),
        (output_path, "Viewer report"),
        (decision_path, "Viewer decision"),
    ]
    if human_review_policy_output_path is not None:
        bounded_paths.append(
            (
                human_review_policy_output_path,
                "human review policy",
            )
        )
    for path, label in bounded_paths:
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
        human_review_policy_output_path=(
            human_review_policy_output_path
        ),
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
    launch_identities = _capture_launch_identities(validated)
    try:
        server = make_server(
            validated.project_root,
            host="127.0.0.1",
            port=0,
            real_scene_import_root=validated.import_root,
        )
    except (OSError, ValueError) as exc:
        raise ViewerSessionError(
            "verified Studio server could not start"
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
        for label, path, expected in launch_identities:
            _verify_launch_identity(path, expected, label=label)
        _verify_output_still_absent(
            validated.output_path,
            label="Viewer report",
        )
        _verify_output_still_absent(
            validated.decision_path,
            label="Viewer decision",
        )
        if validated.human_review_policy_output_path is not None:
            _verify_output_still_absent(
                validated.human_review_policy_output_path,
                label="human review policy",
            )
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
    if (
        return_code == 0
        and validated.human_review_policy_output_path is not None
    ):
        try:
            materialize_human_review_policy(
                evidence_root=validated.evidence_root,
                viewer_policy_path=validated.policy_path,
                viewer_report_path=validated.output_path,
                output_path=(
                    validated.human_review_policy_output_path
                ),
            )
        except HumanReviewInputError as exc:
            raise ViewerSessionError(
                "Viewer capture completed but its human review policy "
                "could not be materialized"
            ) from exc
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
    parser.add_argument(
        "--human-review-policy-output",
        type=Path,
        help=(
            "After an accepted capture, derive one bound human-review "
            "policy at this absent path"
        ),
    )
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
                human_review_policy_output_path=(
                    Path(
                        args.human_review_policy_output
                    ).expanduser().absolute()
                    if args.human_review_policy_output is not None
                    else None
                ),
                headless=args.headless,
                measurement_timeout_ms=args.measurement_timeout_ms,
            )
        )
    except ViewerSessionError as exc:
        print(f"production Viewer session blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
