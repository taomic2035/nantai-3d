"""Runner for the reciprocal-route mesh/collision probe (HANDOFF-CODEX-011 P0-1).

This module wraps the Blender subprocess invocation that runs
``scripts/blender/probe_reciprocal_route_modules.py``.  It builds the
content-addressed request, runs Blender, parses the output report, and
verifies it against the expected input SHAs.

The runner is intentionally thin: all measurement logic lives in the
Blender script.  This keeps the runner testable via mock subprocess
(per project_memory.md: "runner tests must use mock subprocess; do not
perform actual rendering of 175-root Blender files").

Provenance contract:
  * ``probe_script_sha256`` is computed from the script file bytes; the
    report must carry the same SHA, fail-closed.
  * ``input_blend_sha256`` is computed from the ``.blend`` file bytes;
    the report must carry the same SHA, fail-closed.
  * The other input SHAs (plan, build_id, build_report, object_registry)
    are caller-supplied; the report must carry the same SHAs, fail-closed.
  * The runner never re-measures the mesh; it only verifies content
    addressing.  Re-measurement is the Blender script's job.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .reciprocal_route_probe import (
    ProbeError,
    ReciprocalRouteProbeReport,
    verify_reciprocal_route_probe_report,
)

REQUEST_SCHEMA = "nantai.synthetic-village.reciprocal-route-probe-request.v1"
PROBE_REPORT_NAME = "reciprocal-route-probe-report.json"
PROBE_REQUEST_NAME = "reciprocal-route-probe-request.json"

#: Placeholder SHA-256 returned by ``build_reciprocal_route_probe_request``
#: when ``probe_script_path`` is omitted.  This mirrors the Phase 4.2
#: ``ReciprocalRoleCameraCandidate`` placeholder design: a 64-zero digest
#: can never collide with a real 64-hex SHA, and production callers always
#: supply ``probe_script_path`` so they get the real measurement.
PROBE_SCRIPT_PLACEHOLDER_SHA256 = "0" * 64

#: Default Blender invocation timeout (s).  The probe reads a 150 MB
#: .blend and runs ~200 ray casts + 15 BVH overlap tests; in practice
#: this takes 5-30 minutes on the dev machine.  The default leaves
#: generous headroom for slower machines.
DEFAULT_PROBE_TIMEOUT_S = 3600


def _sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes (chunked to avoid loading 150 MB at once)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)  # 1 MB
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _probe_script_sha256(probe_script_path: Path) -> str:
    """SHA-256 of the probe Blender script bytes.

    Patched in tests so the runner can be exercised without the actual
    script file being present on disk.
    """
    return _sha256_file(probe_script_path)


def _run_blender(
    blender_path: Path,
    probe_script_path: Path,
    request_path: Path,
    staging_dir: Path,
    *,
    timeout_s: int = DEFAULT_PROBE_TIMEOUT_S,
) -> Path:
    """Invoke Blender headless to run the probe script.

    Returns the path to the probe report JSON written by the script.
    Patched in tests so no real Blender invocation happens.

    The script reads the request path from ``sys.argv`` (after ``--``)
    and writes the report to ``staging_dir / PROBE_REPORT_NAME``.
    """
    report_path = staging_dir / PROBE_REPORT_NAME
    if report_path.exists():
        report_path.unlink()

    cmd = [
        str(blender_path),
        "--background",
        "--factory-startup",
        "--python",
        str(probe_script_path),
        "--",
        str(request_path),
        str(staging_dir),
    ]
    result = subprocess.run(  # noqa: S603 -- controlled Blender invocation
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if result.returncode != 0:
        raise ProbeError(
            f"Blender probe failed with exit code {result.returncode}:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
        )
    if not report_path.exists():
        raise ProbeError(
            f"Blender probe did not write the expected report at {report_path}",
        )
    return report_path


def build_reciprocal_route_probe_request(
    *,
    blend_path: Path,
    plan_sha256: str,
    build_id: str,
    build_report_sha256: str,
    object_registry_sha256: str,
    probe_script_path: Path | None = None,
    build_request_path: Path | None = None,
) -> dict[str, Any]:
    """Build the content-addressed probe request.

    The request is what the Blender script reads to know what to probe
    and what SHAs to bind into the report.  ``probe_script_path`` is
    used to compute ``probe_script_sha256``; if omitted, the caller is
    expected to supply it later (testing convenience).

    ``build_request_path`` points to the reciprocal-route-build-request.json
    that lives next to the probed ``.blend``.  The Blender script reads
    the full ``reciprocal_route_module_plan`` from there (so it knows
    module→parts mapping and each module's ``topology_ref``), and
    re-validates the plan SHA, build_id, build_report_sha and
    object_registry_sha against this request's expected SHAs.
    Production callers must supply it; tests may omit it.
    """
    if probe_script_path is not None:
        probe_script_sha = _probe_script_sha256(probe_script_path)
    else:
        # Placeholder when the script path is omitted (testing
        # convenience).  Production callers always supply the path so
        # they get the real script-bytes SHA.  The placeholder is 64
        # zeros and can never collide with a real 64-hex SHA.
        probe_script_sha = PROBE_SCRIPT_PLACEHOLDER_SHA256
    blend_sha = _sha256_file(blend_path)
    return {
        "schema_version": REQUEST_SCHEMA,
        "probe_script_sha256": probe_script_sha,
        "input_blend_path": str(blend_path),
        "input_blend_sha256": blend_sha,
        "input_plan_sha256": plan_sha256,
        "input_build_id": build_id,
        "input_build_report_sha256": build_report_sha256,
        "input_object_registry_sha256": object_registry_sha256,
        "build_request_path": str(build_request_path) if build_request_path else "",
    }


def run_reciprocal_route_probe(
    *,
    blend_path: Path,
    plan_sha256: str,
    build_id: str,
    build_report_sha256: str,
    object_registry_sha256: str,
    blender_path: Path,
    probe_script_path: Path,
    staging_dir: Path,
    build_request_path: Path | None = None,
    timeout_s: int = DEFAULT_PROBE_TIMEOUT_S,
) -> ReciprocalRouteProbeReport:
    """Run the probe end-to-end and return the verified report.

    Steps:
      1. Build the request (computes probe_script_sha256 + blend_sha256).
      2. Invoke Blender via ``_run_blender`` (mocked in tests).
      3. Parse the report JSON.
      4. Verify the report's input SHAs match the request's SHAs.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    request = build_reciprocal_route_probe_request(
        blend_path=blend_path,
        plan_sha256=plan_sha256,
        build_id=build_id,
        build_report_sha256=build_report_sha256,
        object_registry_sha256=object_registry_sha256,
        probe_script_path=probe_script_path,
        build_request_path=build_request_path,
    )
    request_path = staging_dir / PROBE_REQUEST_NAME
    request_path.write_text(
        json.dumps(request, indent=2) + "\n",
        encoding="utf-8",
    )

    report_path = _run_blender(
        blender_path=blender_path,
        probe_script_path=probe_script_path,
        request_path=request_path,
        staging_dir=staging_dir,
        timeout_s=timeout_s,
    )

    report_bytes = report_path.read_bytes()
    report = ReciprocalRouteProbeReport.model_validate_json(report_bytes)

    verify_reciprocal_route_probe_report(
        report,
        expected_probe_script_sha256=request["probe_script_sha256"],
        expected_blend_sha256=request["input_blend_sha256"],
        expected_build_id=request["input_build_id"],
        expected_plan_sha256=request["input_plan_sha256"],
        expected_build_report_sha256=request["input_build_report_sha256"],
        expected_object_registry_sha256=request["input_object_registry_sha256"],
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``python -m pipeline.synthetic_village.reciprocal_route_probe_runner``.

    Usage:
        python -m pipeline.synthetic_village.reciprocal_route_probe_runner \\
            --blend PATH --blender PATH --probe-script PATH \\
            --plan-sha SHA --build-id SHA --build-report-sha SHA \\
            --object-registry-sha SHA --staging PATH \\
            --build-request PATH
    """
    import argparse

    parser = argparse.ArgumentParser(description="Run reciprocal-route probe")
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--probe-script", type=Path, required=True)
    parser.add_argument("--plan-sha", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--build-report-sha", required=True)
    parser.add_argument("--object-registry-sha", required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--build-request", type=Path, default=None)
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_PROBE_TIMEOUT_S)
    args = parser.parse_args(argv)

    report = run_reciprocal_route_probe(
        blend_path=args.blend,
        plan_sha256=args.plan_sha,
        build_id=args.build_id,
        build_report_sha256=args.build_report_sha,
        object_registry_sha256=args.object_registry_sha,
        blender_path=args.blender,
        probe_script_path=args.probe_script,
        staging_dir=args.staging,
        build_request_path=args.build_request,
        timeout_s=args.timeout_s,
    )
    print(
        f"probe_report_sha256={reciprocal_route_probe_report_sha256_cli(report)}",
    )
    print(f"overall_passed={report.summary.overall_passed}")
    return 0 if report.summary.overall_passed else 2


def reciprocal_route_probe_report_sha256_cli(
    report: ReciprocalRouteProbeReport,
) -> str:
    """CLI helper: compute report SHA-256."""
    from .reciprocal_route_probe import (
        canonical_reciprocal_route_probe_report_bytes,
    )
    return hashlib.sha256(
        canonical_reciprocal_route_probe_report_bytes(report),
    ).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
