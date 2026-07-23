#!/usr/bin/env python3
"""Emit registration quality report from COLMAP sparse model + registration.json.

Drives ``pipeline.registration_quality.build_registration_quality_report`` to
derive every measured field from authoritative artifacts:

- ``registration.json`` (RegistrationResult bytes) — produced by
  ``pipeline.registration.register()`` or ``pipeline.reconstruct()``.
- ``capture_manifest.json`` (optional) — produced by ingest or the cloud
  script's inline generator.
- COLMAP ``sparse/`` directory — enumerated by
  ``enumerate_sparse_models()`` to bind multi-component model evidence.

The emitted ``quality-report.json`` is what ``scripts/prepare_import.py``
later verifies locally via ``validate_registration_quality()``.  Every SHA
and count is re-derived from bytes — self-reported values are never trusted.

Trust boundary: ``quality_accepted=True`` / ``training_allowed=True`` only
proves the registration satisfies the operator's coverage policy.  It does
NOT prove the photos are real, the camera coverage is geometrically
sufficient for 3DGS, or the scale is metric.

Usage::

    # After running COLMAP via pipeline.reconstruct or pipeline.registration:
    python scripts/emit_registration_quality.py \
        --registration-json recon/registration.json \
        --sparse-dir recon/colmap_ws/sparse \
        --capture-manifest manifests/capture_manifest.json \
        --policy policy.json \
        --output quality-report.json

    # policy.json is a RegistrationQualityPolicy JSON (all 5 thresholds).
    # Build one with:
    #   python -c "from pipeline.registration_quality import \
    #     RegistrationQualityPolicy as P; print(P(min_registered_count=10, \
    #     min_registered_ratio=0.7, min_session_coverage_ratio=0.6, \
    #     max_unregistered_consecutive_run=5, \
    #     min_largest_connected_model_share=0.6).model_dump_json(indent=2))"

For mock-engine registrations (no COLMAP sparse dir), omit ``--sparse-dir``;
the report will have ``engine='mock'`` and no model_enumeration.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.recon_schema import RegistrationResult  # noqa: E402
from pipeline.registration_quality import (  # noqa: E402
    RegistrationQualityPolicy,
    SparseModelEnumeration,
    build_registration_quality_report,
    enumerate_sparse_models,
    policy_canonical_sha256,
    validate_registration_quality,
)


def _write_json(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Emit registration quality report from COLMAP artifacts.")
    ap.add_argument("--registration-json", required=True,
                    help="registration.json produced by pipeline.registration "
                         "or pipeline.reconstruct (RegistrationResult JSON)")
    ap.add_argument("--sparse-dir", default=None,
                    help="COLMAP sparse/ directory (required for engine=colmap; "
                         "omit for mock-engine registrations)")
    ap.add_argument("--capture-manifest", default=None,
                    help="capture_manifest.json (optional; binds capture "
                         "provenance to the quality report)")
    ap.add_argument("--policy", required=True,
                    help="RegistrationQualityPolicy JSON (all 5 thresholds)")
    ap.add_argument("--total-input-images", type=int, default=None,
                    help="total input image count for sparse enumeration "
                         "(default: derive from registration sessions)")
    ap.add_argument("--invocation-succeeded", action="store_true",
                    default=True,
                    help="mark the COLMAP invocation as succeeded (default; "
                         "omit only if the SfM run itself crashed)")
    ap.add_argument("--no-invocation-succeeded", dest="invocation_succeeded",
                    action="store_false",
                    help="mark the invocation as failed (crashed SfM)")
    ap.add_argument("--engine-version", default=None,
                    help="COLMAP engine version string (e.g. '4.1.0')")
    ap.add_argument("--output", default="quality-report.json",
                    help="output quality-report.json path")
    args = ap.parse_args(argv)

    # ── Load registration.json bytes (authoritative) ──
    reg_path = Path(args.registration_json)
    if not reg_path.is_file():
        raise SystemExit(f"registration.json not found: {reg_path}")
    reg_bytes = reg_path.read_bytes()
    try:
        registration = RegistrationResult.model_validate_json(reg_bytes)
    except Exception as exc:
        raise SystemExit(
            f"registration.json does not parse as RegistrationResult: {exc}"
        ) from exc
    print(f"[REG] engine={registration.engine}  poses={len(registration.poses)}  "
          f"sessions={len(registration.sessions)}  "
          f"sha256={hashlib.sha256(reg_bytes).hexdigest()[:16]}...")

    # ── Load policy (authoritative) ──
    policy_path = Path(args.policy)
    if not policy_path.is_file():
        raise SystemExit(f"policy not found: {policy_path}")
    try:
        policy = RegistrationQualityPolicy.model_validate_json(
            policy_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"policy does not parse: {exc}") from exc
    print(f"[POLICY] sha256={policy_canonical_sha256(policy)[:16]}...")

    # ── Load capture manifest (optional, authoritative bytes) ──
    capture_manifest = None
    capture_manifest_bytes: bytes | None = None
    if args.capture_manifest:
        cm_path = Path(args.capture_manifest)
        if not cm_path.is_file():
            raise SystemExit(f"capture manifest not found: {cm_path}")
        capture_manifest_bytes = cm_path.read_bytes()
        try:
            from pipeline.studio_revisions import CaptureRevisionManifest
            capture_manifest = CaptureRevisionManifest.model_validate_json(
                capture_manifest_bytes)
        except Exception as exc:
            raise SystemExit(
                f"capture manifest does not parse: {exc}") from exc
        print(f"[CAPTURE] sha256="
              f"{hashlib.sha256(capture_manifest_bytes).hexdigest()[:16]}...  "
              f"sources={capture_manifest.source_count}  "
              f"outputs={capture_manifest.output_count}")

    # ── Enumerate sparse models (engine=colmap only) ──
    sparse_enum: SparseModelEnumeration | None = None
    if registration.engine == "colmap":
        if not args.sparse_dir:
            raise SystemExit(
                "engine='colmap' requires --sparse-dir (COLMAP sparse/ "
                "directory containing <index>/images.txt + points3D.txt)")
        sparse_dir = Path(args.sparse_dir)
        if not sparse_dir.is_dir():
            raise SystemExit(f"sparse directory not found: {sparse_dir}")

        # Derive total input images from registration sessions if not given.
        if args.total_input_images is not None:
            total_input = args.total_input_images
        else:
            total_input = sum(len(s.images) for s in registration.sessions)
            if total_input == 0:
                raise SystemExit(
                    "cannot derive total_input_images from registration "
                    "(sessions have no images); pass --total-input-images")
        sparse_enum = enumerate_sparse_models(sparse_dir, total_input)
        print(f"[SPARSE] models={len(sparse_enum.models)}  "
              f"selected={sparse_enum.selected_model_index}  "
              f"rule={sparse_enum.selection_rule}  "
              f"total_input={sparse_enum.total_input_images}")
        for m in sparse_enum.models:
            print(f"  model[{m.model_index}]: "
                  f"images={m.image_count}  points3d={m.point3d_count}")
    elif args.sparse_dir:
        raise SystemExit(
            f"--sparse-dir not allowed for engine={registration.engine!r} "
            "(only engine='colmap' binds sparse model evidence)")

    # ── Build the report (derives every field from authoritative artifacts) ──
    report = build_registration_quality_report(
        registration=registration,
        registration_json_bytes=reg_bytes,
        capture_manifest=capture_manifest,
        capture_manifest_bytes=capture_manifest_bytes,
        policy=policy,
        sparse_enumeration=sparse_enum,
        invocation_succeeded=args.invocation_succeeded,
        engine_version=args.engine_version,
    )

    # ── Round-trip validate (fail-closed self-check) ──
    validate_registration_quality(
        report=report,
        policy=policy,
        registration_json_bytes=reg_bytes,
        capture_manifest_bytes=capture_manifest_bytes,
        sparse_enumeration=sparse_enum,
    )

    # ── Write ──
    _write_json(Path(args.output), report)
    print(f"[REPORT] wrote {args.output}")
    print(f"  registered_count={report.registered_count}  "
          f"total={report.total_input_images}  "
          f"ratio={report.registered_ratio:.3f}")
    print(f"  quality_accepted={report.quality_accepted}  "
          f"training_allowed={report.training_allowed}")
    if report.rejection_reasons:
        print("  rejection_reasons:")
        for r in report.rejection_reasons:
            print(f"    - {r}")
    print(f"  report_sha256="
          f"{hashlib.sha256(report.model_dump_json().encode()).hexdigest()[:16]}...")
    # Honest boundary reminder.
    print()
    print("Trust boundary: quality_accepted/training_allowed only proves the "
          "registration satisfies the coverage policy. It does NOT prove the "
          "photos are real, the coverage is geometrically sufficient for 3DGS, "
          "or the scale is metric.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
