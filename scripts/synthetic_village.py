"""Command-line entry point for private synthetic-village asset operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


DEFAULT_VISUAL_PACK_ROOT = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"
DEFAULT_MATERIAL_PUBLICATION_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles"
)
DEFAULT_MATERIAL_WORK_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/material-work"
)
DEFAULT_NEAR_MESH_WORK_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/mesh-near-v2-work"
)
DEFAULT_MESH_ASSET_PUBLICATION_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/mesh-asset-bundles"
)
DEFAULT_MACOS_BLENDER = Path(
    "/Applications/Blender.app/Contents/MacOS/Blender",
)


def _import_visual_source():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.visual_sources import import_visual_source

    return import_visual_source


def _prepare_h3_source_pack():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.h3_material_sources import (
        prepare_h3_source_pack,
    )

    return prepare_h3_source_pack


def _build_h3_authored_material_pack():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.h3_material_authoring import (
        build_h3_authored_material_pack,
    )

    return build_h3_authored_material_pack


def _compile_h3_ktx2_pack():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.ktx2_toolchain import compile_h3_ktx2_pack

    return compile_h3_ktx2_pack


def _publish_material_bundle():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.material_bundle import publish_material_bundle

    return publish_material_bundle


def _run_near_mesh_asset_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.mesh_asset_build_v2 import (
        run_mesh_asset_build_v2,
    )

    return run_mesh_asset_build_v2


def _revise_visual_source_pack():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.visual_sources import revise_visual_source_pack

    return revise_visual_source_pack


def _run_canary_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.canary import run_canary_build

    return run_canary_build


def _import_production_profile():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.production_profile import (
        build_production_camera_plan,
        canonical_production_plan_bytes,
        production_batch_slice,
        production_camera_registry_digest,
    )

    return (
        build_production_camera_plan,
        production_batch_slice,
        production_camera_registry_digest,
        canonical_production_plan_bytes,
    )


def _import_weather_profile():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village import weather_profile

    return weather_profile


def _run_canary_render():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.canary import run_canary_render

    return run_canary_render


def _run_local_production_render():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.local_production_runner import (
        run_local_production_render,
    )

    return run_local_production_render


def _run_windows_production_render():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.local_production_runner import (
        run_windows_production_render,
    )

    return run_windows_production_render


def _load_reciprocal_route_runtime_request():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.reciprocal_route_module_runtime import (
        load_reciprocal_route_runtime_request,
    )

    return load_reciprocal_route_runtime_request


def _verify_reciprocal_production_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.reciprocal_route_production import (
        verify_reciprocal_production_build,
    )

    return verify_reciprocal_production_build


def _run_reciprocal_production_batch():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.reciprocal_route_batch import (
        run_reciprocal_production_batch,
    )

    return run_reciprocal_production_batch


def _build_waterwheel_local_orbit_plan():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.local_orbit_audit import (
        build_waterwheel_local_orbit_plan,
    )

    return build_waterwheel_local_orbit_plan


def _canonical_local_orbit_plan_bytes():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.local_orbit_audit import (
        canonical_local_orbit_plan_bytes,
    )

    return canonical_local_orbit_plan_bytes


def _load_local_orbit_plan():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.local_orbit_audit import load_local_orbit_plan

    return load_local_orbit_plan


def _run_local_orbit_audit():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.local_orbit_runner import run_local_orbit_audit

    return run_local_orbit_audit


def _parse_reciprocal_batch_targets(values: list[str]):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.reciprocal_route_batch import (
        ReciprocalProductionBatchTarget,
    )

    targets = []
    for value in values:
        role_module_id, separator, target_camera_id = value.partition("=")
        if not separator:
            raise SystemExit(
                "--target must be ROLE_MODULE_ID=camera-ground-route-010|039",
            )
        try:
            targets.append(
                ReciprocalProductionBatchTarget(
                    role_module_id=role_module_id,
                    target_camera_id=target_camera_id,
                ),
            )
        except ValueError as exc:
            raise SystemExit(f"invalid reciprocal --target: {value}") from exc
    return tuple(targets)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _load_post_render_policy(path: Path):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village import canary
    from pipeline.synthetic_village.production_quality_gates import (
        ProductionFrameQualityPolicyV2,
        canonical_production_frame_quality_policy_v2_bytes,
    )

    raw = canary._read_stable_metadata(  # noqa: SLF001
        Path(path).absolute(),
        label="post-render quality policy",
    )
    json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=canary._reject_duplicate_keys,  # noqa: SLF001
        parse_constant=_reject_json_constant,
    )
    policy = ProductionFrameQualityPolicyV2.model_validate_json(raw)
    if raw != canonical_production_frame_quality_policy_v2_bytes(policy):
        raise ValueError("post-render quality policy is not canonical JSON")
    return policy


def _run_environment_module_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.environment_module_runtime import (
        run_environment_module_build,
    )

    return run_environment_module_build


def _verify_windows_production_build():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.windows_production_build import (
        verify_windows_production_build,
    )

    return verify_windows_production_build


def _audit_render_coverage():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.coverage_audit import audit_render_coverage

    return audit_render_coverage


def _audit_render_view_overlap():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.view_overlap import audit_render_view_overlap

    return audit_render_view_overlap


def _write_view_overlap_audit():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.view_overlap import write_view_overlap_audit

    return write_view_overlap_audit


def _coverage_threshold(min_pixels: int, min_cameras: int):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.coverage_audit import CoverageThreshold

    return CoverageThreshold(
        min_pixels=min_pixels,
        min_cameras=min_cameras,
        comparison="pixels-greater-or-equal",
    )


def _canonical_camera_id(value: str) -> str:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from pipeline.synthetic_village.canary import RENDER_CAMERA_IDS

    if value not in RENDER_CAMERA_IDS:
        raise argparse.ArgumentTypeError(f"unknown canonical camera ID: {value}")
    return value


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
    import_h3_sources = commands.add_parser(
        "import-h3-material-sources",
        help=(
            "Audit all 24 private AI candidates and publish only the eight "
            "selected content-addressed H3 sources."
        ),
    )
    import_h3_sources.add_argument(
        "--selection-receipt",
        type=Path,
        required=True,
    )
    import_h3_sources.add_argument(
        "--output-root",
        type=Path,
        required=True,
    )
    author_h3_materials = commands.add_parser(
        "author-h3-materials",
        help=(
            "Derive deterministic seamless 4096 masters and heuristic PBR "
            "maps from one verified H3 source pack."
        ),
    )
    author_h3_materials.add_argument(
        "--source-pack-root",
        type=Path,
        required=True,
    )
    author_h3_materials.add_argument(
        "--output-root",
        type=Path,
        required=True,
    )
    build_h3_ktx2 = commands.add_parser(
        "build-h3-ktx2",
        help=(
            "Compile, validate, quality-check, and atomically publish the "
            "complete H3 KTX2 material pack."
        ),
    )
    build_h3_ktx2.add_argument("--authored-root", type=Path, required=True)
    build_h3_ktx2.add_argument("--tool-receipt", type=Path, required=True)
    build_h3_ktx2.add_argument("--output-root", type=Path, required=True)
    revise_visual = commands.add_parser(
        "revise-visual",
        help=(
            "Create an absent immutable visual-source pack revision with exactly "
            "one slot replaced; the source pack remains byte-unchanged."
        ),
    )
    revise_visual.add_argument("--from-pack-root", type=Path, required=True)
    revise_visual.add_argument("--to-pack-root", type=Path, required=True)
    revise_visual.add_argument("--slot", required=True)
    revise_visual.add_argument("--source", type=Path, required=True)
    revise_visual.add_argument("--source-manifest", type=Path, required=True)
    build_materials = commands.add_parser(
        "build-materials",
        help="Derive and privately publish the complete 24-slot PBR material bundle.",
    )
    build_materials.add_argument(
        "--visual-pack-root",
        type=Path,
        default=DEFAULT_VISUAL_PACK_ROOT,
    )
    build_materials.add_argument(
        "--publication-root",
        type=Path,
        default=DEFAULT_MATERIAL_PUBLICATION_ROOT,
    )
    build_materials.add_argument(
        "--work-root",
        type=Path,
        default=DEFAULT_MATERIAL_WORK_ROOT,
    )
    build_near_mesh_assets = commands.add_parser(
        "build-near-mesh-assets",
        help=(
            "Rebuild only the eleven synthetic near LOD2 meshes, reuse exact "
            "v1 LOD0/1 objects, and privately publish one audited v2 bundle."
        ),
    )
    build_near_mesh_assets.add_argument(
        "--source-v1-bundle-root",
        type=Path,
        required=True,
    )
    build_near_mesh_assets.add_argument(
        "--material-bundle-root",
        type=Path,
        required=True,
    )
    build_near_mesh_assets.add_argument(
        "--blender",
        type=Path,
        default=DEFAULT_MACOS_BLENDER,
    )
    build_near_mesh_assets.add_argument(
        "--work-root",
        type=Path,
        default=DEFAULT_NEAR_MESH_WORK_ROOT,
    )
    build_near_mesh_assets.add_argument(
        "--publication-root",
        type=Path,
        default=DEFAULT_MESH_ASSET_PUBLICATION_ROOT,
    )
    build_near_mesh_assets.add_argument(
        "--timeout-seconds",
        type=int,
        default=60 * 60,
    )
    build_canary = commands.add_parser(
        "build-canary",
        help="Build, verify, and privately publish the Blender foundation canary.",
    )
    build_canary.add_argument("--timeout-seconds", type=int, default=30 * 60)
    render_canary = commands.add_parser(
        "render-canary",
        help="Resume, verify, and privately publish the six-layer canary frames.",
    )
    render_canary.add_argument("--camera", action="append", type=_canonical_camera_id)
    render_canary.add_argument("--timeout-seconds", type=int, default=15 * 60)
    audit_coverage = commands.add_parser(
        "audit-coverage",
        help=(
            "Recompute per-component per-camera observation evidence from the real "
            "instance masks. The threshold is mandatory and has no default: "
            "'how many pixels count as seen' is not ours to invent."
        ),
    )
    audit_coverage.add_argument("--build-directory", type=Path, required=True)
    audit_coverage.add_argument(
        "--min-pixels",
        type=int,
        required=True,
        help=(
            "Pixels a component must occupy in a frame to count as observed there "
            "(inclusive: pixels >= min-pixels). On the real 24 frames this single "
            "number moves the answer between 122 and 4 of 126 components."
        ),
    )
    audit_coverage.add_argument("--min-cameras", type=int, required=True)
    audit_coverage.add_argument("--report", type=Path)
    audit_view_overlap = commands.add_parser(
        "audit-view-overlap",
        help=(
            "Measure each verified camera's best symmetric depth-visible surface "
            "overlap. This is not a feature-match, SfM, or reconstructability claim."
        ),
    )
    audit_view_overlap.add_argument("--render-root", type=Path, required=True)
    audit_view_overlap.add_argument(
        "--sample-stride",
        type=int,
        default=16,
        help="Depth sampling stride in pixels (default: 16).",
    )
    audit_view_overlap.add_argument(
        "--depth-relative-tolerance",
        type=float,
        default=0.05,
        help="Maximum relative depth disagreement for one shared sample (default: 0.05).",
    )
    audit_view_overlap.add_argument(
        "--min-symmetric-overlap",
        type=float,
        default=0.65,
        help=(
            "Required minimum of both directional overlap ratios. The default 0.65 "
            "is the approved synthetic-village camera target."
        ),
    )
    audit_view_overlap.add_argument("--report", type=Path)
    plan_production = commands.add_parser(
        "plan-production",
        help=(
            "Emit the 180-camera production profile plan. The plan is placed along the "
            "real walkable path network and creek corridor; groups with no topology "
            "source are reported unplaced rather than sprayed geometrically."
        ),
    )
    plan_production.add_argument("--plan", type=Path, help="Write the full plan JSON here.")
    plan_production.add_argument(
        "--batch-count",
        type=int,
        help="Split the placed cameras into this many stable batches.",
    )
    plan_production.add_argument(
        "--batch-index",
        type=int,
        help="Print only this batch's camera IDs (requires --batch-count).",
    )
    render_production_local = commands.add_parser(
        "render-production-local",
        help=(
            "Resume an explicitly selected L0 production-camera subset from one "
            "verified local Blender build. Frames below the required valid-pixel "
            "ratio are retained for inspection but rejected from training."
        ),
    )
    render_production_local.add_argument(
        "--local-preview-build",
        "--build-directory",
        dest="build_directory",
        type=Path,
        required=True,
        help=(
            "Exact Mac local-preview training-build directory. "
            "--build-directory remains a compatibility alias."
        ),
    )
    render_production_local.add_argument(
        "--material-bundle-root",
        type=Path,
        required=True,
    )
    render_production_local.add_argument(
        "--visual-pack-root",
        type=Path,
        help=(
            "Verified visual-source pack bound to the selected build. Omit only "
            "when the build uses the default private pack."
        ),
    )
    render_production_local.add_argument(
        "--camera",
        action="append",
        help="Production camera ID; repeat for a bounded subset. Omit for all 180.",
    )
    render_production_local.add_argument(
        "--min-valid-pixel-ratio",
        type=float,
        required=True,
        help=(
            "Operator-selected inclusive training-quality threshold in (0, 1]. "
            "This filters frames and never upgrades geometry trust."
        ),
    )
    render_production_local.add_argument(
        "--post-render-policy",
        type=Path,
        required=True,
        help=(
            "Canonical explicit v2 post-render policy JSON. No candidate "
            "threshold is selected implicitly."
        ),
    )
    render_production_local.add_argument(
        "--clearance-near-distance-m",
        type=float,
        required=True,
        help=(
            "Operator-selected near-hit threshold in metres for the "
            "versioned upper/middle 5x5 clearance policy. This filters "
            "training suitability and never upgrades trust."
        ),
    )
    render_production_local.add_argument(
        "--min-upper-middle-near-hits",
        type=int,
        required=True,
        help=(
            "Operator-selected rejection count from the 15 upper/middle "
            "samples. The audited synthetic-village candidate is 5."
        ),
    )
    render_production_local.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Run and journal the scene-bound clearance probe without "
            "starting any six-layer frame renders."
        ),
    )
    render_production_local.add_argument(
        "--timeout-seconds",
        type=int,
        default=15 * 60,
    )
    render_production_local.add_argument("--render-root", type=Path)
    render_production_windows = commands.add_parser(
        "render-production-windows",
        help=(
            "Resume an L0 production-camera subset from one explicitly selected "
            "and byte-verified Windows schema-v2 Blender build."
        ),
    )
    render_production_windows.add_argument(
        "--verified-v2-build",
        type=Path,
        required=True,
        help=(
            "Exact private Windows schema-v2 build directory named by build ID."
        ),
    )
    render_production_windows.add_argument(
        "--material-bundle-root",
        type=Path,
        required=True,
    )
    render_production_windows.add_argument(
        "--surface-realism-profile",
        required=True,
        choices=(
            "single-scale-derived-pbr-v0",
            "source-consistent-multiscale-surface-v1",
        ),
        help="Explicit surface profile used to reconstruct the canonical request.",
    )
    render_production_windows.add_argument(
        "--visual-pack-root",
        type=Path,
        help=(
            "Verified visual-source pack bound to the selected build. Omit only "
            "when the build uses the default private pack."
        ),
    )
    render_production_windows.add_argument(
        "--camera",
        action="append",
        help="Production camera ID; repeat for a bounded subset. Omit for all 180.",
    )
    render_production_windows.add_argument(
        "--min-valid-pixel-ratio",
        type=float,
        required=True,
        help=(
            "Operator-selected inclusive candidate threshold in (0, 1]. "
            "This legacy gate will be superseded by measured v2 layer policy."
        ),
    )
    render_production_windows.add_argument(
        "--post-render-policy",
        type=Path,
        required=True,
        help=(
            "Canonical explicit v2 post-render policy JSON. No candidate "
            "threshold is selected implicitly."
        ),
    )
    render_production_windows.add_argument(
        "--clearance-near-distance-m",
        type=float,
        required=True,
        help=(
            "Operator-selected near-hit threshold in metres for the "
            "versioned upper/middle 5x5 clearance policy."
        ),
    )
    render_production_windows.add_argument(
        "--min-upper-middle-near-hits",
        type=int,
        required=True,
        help=(
            "Operator-selected rejection count from the 15 upper/middle samples."
        ),
    )
    render_production_windows.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Run and journal the scene-bound clearance probe without "
            "starting any six-layer frame renders."
        ),
    )
    render_production_windows.add_argument(
        "--timeout-seconds",
        type=int,
        default=15 * 60,
    )
    render_production_windows.add_argument("--render-root", type=Path)
    render_reciprocal_production = commands.add_parser(
        "render-reciprocal-production",
        help=(
            "Run or resume the exact six-role L0 caller against one verified "
            "exact-218 reciprocal Blender build and persist a Studio-readable "
            "batch journal."
        ),
    )
    render_reciprocal_production.add_argument(
        "--reciprocal-build",
        type=Path,
        required=True,
        help=(
            "Directory containing canonical reciprocal-route build request, "
            "report, and measured .blend bytes."
        ),
    )
    render_reciprocal_production.add_argument(
        "--blender",
        type=Path,
        required=True,
    )
    render_reciprocal_production.add_argument(
        "--target",
        action="append",
        required=True,
        help=(
            "ROLE_MODULE_ID=camera-ground-route-010|039; repeat exactly once "
            "for each of the six canonical roles in plan order."
        ),
    )
    render_reciprocal_production.add_argument(
        "--min-valid-pixel-ratio",
        type=float,
        required=True,
    )
    render_reciprocal_production.add_argument(
        "--post-render-policy",
        type=Path,
        required=True,
    )
    render_reciprocal_production.add_argument(
        "--clearance-near-distance-m",
        type=float,
        required=True,
    )
    render_reciprocal_production.add_argument(
        "--min-upper-middle-near-hits",
        type=int,
        required=True,
    )
    render_reciprocal_production.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help=(
            "One private batch directory; place it below "
            ".nantai-studio/sv-prod-win/reciprocal-production-batches/ "
            "for Studio discovery."
        ),
    )
    render_reciprocal_production.add_argument(
        "--timeout-seconds",
        type=int,
        default=30 * 60,
    )
    build_local_orbit_plan = commands.add_parser(
        "build-local-orbit-plan",
        help=(
            "Build the canonical eight-direction waterwheel audit plan bound "
            "to one verified exact-218 reciprocal scene."
        ),
    )
    build_local_orbit_plan.add_argument(
        "--reciprocal-build",
        type=Path,
        required=True,
    )
    build_local_orbit_plan.add_argument(
        "--anchor-m",
        type=float,
        nargs=3,
        required=True,
        metavar=("X", "Y", "Z"),
    )
    build_local_orbit_plan.add_argument(
        "--output-plan",
        type=Path,
        required=True,
    )
    audit_local_orbit = commands.add_parser(
        "audit-local-orbit",
        help=(
            "Run fresh preflight, six-layer render, visibility and post-render "
            "v2 gates for all eight bound local-orbit views."
        ),
    )
    audit_local_orbit.add_argument(
        "--reciprocal-build",
        type=Path,
        required=True,
    )
    audit_local_orbit.add_argument(
        "--local-orbit-plan",
        type=Path,
        required=True,
    )
    audit_local_orbit.add_argument("--blender", type=Path, required=True)
    audit_local_orbit.add_argument(
        "--min-valid-pixel-ratio",
        type=float,
        required=True,
    )
    audit_local_orbit.add_argument(
        "--post-render-policy",
        type=Path,
        required=True,
    )
    audit_local_orbit.add_argument(
        "--clearance-near-distance-m",
        type=float,
        required=True,
    )
    audit_local_orbit.add_argument(
        "--min-upper-middle-near-hits",
        type=int,
        required=True,
    )
    audit_local_orbit.add_argument("--output-root", type=Path, required=True)
    audit_local_orbit.add_argument(
        "--timeout-seconds",
        type=int,
        default=30 * 60,
    )
    build_environment_modules = commands.add_parser(
        "build-environment-modules",
        help=(
            "Apply the verified 45-part EnvironmentModulePlan to one "
            "byte-verified Windows schema-v2 build, producing a private "
            "modeled-unverified village-modules.blend scene. The plan is "
            "additive; the base build is not modified."
        ),
    )
    build_environment_modules.add_argument(
        "--verified-v2-build",
        type=Path,
        required=True,
        help=(
            "Exact private Windows schema-v2 build directory named by build ID. "
            "The base 130-root object_registry is reused as-is."
        ),
    )
    build_environment_modules.add_argument(
        "--material-bundle-root",
        type=Path,
        required=True,
    )
    build_environment_modules.add_argument(
        "--surface-realism-profile",
        required=True,
        choices=(
            "single-scale-derived-pbr-v0",
            "source-consistent-multiscale-surface-v1",
        ),
        help="Explicit surface profile used to reconstruct the canonical base request.",
    )
    build_environment_modules.add_argument(
        "--visual-pack-root",
        type=Path,
        help=(
            "Verified visual-source pack bound to the selected build. Omit only "
            "when the build uses the default private pack."
        ),
    )
    build_environment_modules.add_argument(
        "--build-root",
        type=Path,
        help=(
            "Output root for the private module build. Defaults to "
            ".nantai-studio/synthetic-village/hybrid-v4/work/environment-modules."
        ),
    )
    build_environment_modules.add_argument(
        "--timeout-seconds",
        type=int,
        default=20 * 60,
    )
    weather_variants = commands.add_parser(
        "weather-variants",
        help=(
            "Emit the multi-weather relighting manifest (clear-noon / overcast / "
            "golden-hour). Each variant is a distinct scene relighting that yields a "
            "new blend_sha256 on the build side; this repo has no 3DGS trainer, so the "
            "manifest is render input + contract only, not a trained multi-weather 3DGS."
        ),
    )
    weather_variants.add_argument(
        "--manifest",
        type=Path,
        help="Write the full canonical weather-variants manifest JSON here.",
    )
    weather_variants.add_argument(
        "--profile",
        help="A weather profile ID to emit a build-request weather block for.",
    )
    weather_variants.add_argument(
        "--request-block",
        type=Path,
        help="Write the --profile weather request block JSON here (needs --profile).",
    )
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
    if args.command == "import-h3-material-sources":
        prepared = _prepare_h3_source_pack()(
            args.selection_receipt,
            args.output_root,
        )
        manifest = prepared.manifest
        print(
            json.dumps(
                {
                    "ai_generated": manifest.ai_generated,
                    "output_root": str(prepared.root),
                    "real_photo_textures": manifest.real_photo_textures,
                    "record_count": len(manifest.records),
                    "schema_version": manifest.schema_version,
                    "source_pack_id": manifest.source_pack_id,
                    "synthetic": manifest.synthetic,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "author-h3-materials":
        prepared = _build_h3_authored_material_pack()(
            args.source_pack_root,
            args.output_root,
        )
        manifest = prepared.manifest
        print(
            json.dumps(
                {
                    "ai_generated": manifest.ai_generated,
                    "output_root": str(prepared.root),
                    "pack_id": manifest.pack_id,
                    "real_photo_textures": manifest.real_photo_textures,
                    "record_count": len(manifest.records),
                    "schema_version": manifest.schema_version,
                    "source_pack_id": manifest.source_pack_id,
                    "synthetic": manifest.synthetic,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "build-h3-ktx2":
        prepared = _compile_h3_ktx2_pack()(
            args.authored_root,
            args.output_root,
            receipt_path=args.tool_receipt,
        )
        manifest = prepared.manifest
        print(
            json.dumps(
                {
                    "ai_generated": manifest.ai_generated,
                    "authored_pack_id": manifest.authored_pack_id,
                    "output_root": str(prepared.root),
                    "pack_id": manifest.pack_id,
                    "real_photo_textures": manifest.real_photo_textures,
                    "record_count": len(manifest.records),
                    "schema_version": manifest.schema_version,
                    "source_pack_id": manifest.source_pack_id,
                    "synthetic": manifest.synthetic,
                    "texture_count": len(manifest.records) * 3,
                    "tool_version": manifest.tool_version,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "revise-visual":
        manifest = _revise_visual_source_pack()(
            source_pack_root=args.from_pack_root,
            revision_pack_root=args.to_pack_root,
            slot_id=args.slot,
            source=args.source,
            source_manifest=args.source_manifest,
        )
        record = next(row for row in manifest.records if row.slot_id == args.slot)
        print(
            json.dumps(
                {
                    "pack_id": manifest.pack_id,
                    "record_count": len(manifest.records),
                    "revision_pack_root": str(args.to_pack_root.absolute()),
                    "slot_id": record.slot_id,
                    "source_sha256": record.sha256,
                    "synthetic": manifest.synthetic,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "build-materials":
        result = _publish_material_bundle()(
            visual_pack_root=args.visual_pack_root,
            publication_root=args.publication_root,
            work_root=args.work_root,
        )
        print(
            json.dumps(
                {
                    "bundle_id": result.bundle_id,
                    "final_directory": str(result.final_directory),
                    "record_count": result.record_count,
                    "reused": result.reused,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "build-near-mesh-assets":
        result = _run_near_mesh_asset_build()(
            repo_root=ROOT,
            source_v1_bundle_root=args.source_v1_bundle_root,
            material_bundle_root=args.material_bundle_root,
            blender_executable=args.blender,
            builder_script=(
                ROOT / "scripts/blender/build_mesh_asset_bundle_v2.py"
            ),
            work_root=args.work_root,
            publication_root=args.publication_root,
            timeout_seconds=args.timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "build_id": result.request.build_id,
                    "bundle_id": result.bundle.bundle_id,
                    "bundle_root": str(result.bundle.final_directory),
                    "lod2_asset_count": len(result.report.artifacts),
                    "reused_lod_count": len(result.request.reused_lods),
                    "synthetic": result.request.synthetic,
                    "verification_level": (
                        result.request.verification_level
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
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
    if args.command == "render-canary":
        result = _run_canary_render()(
            repo_root=ROOT,
            camera_ids=tuple(args.camera) if args.camera else None,
            timeout_seconds=args.timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "journal_path": str(result.journal_path),
                    "render_id": result.render_id,
                    "render_root": str(result.render_root),
                    "rendered_count": result.rendered_count,
                    "reused_count": result.reused_count,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "audit-coverage":
        result = _audit_render_coverage()(
            build_directory=args.build_directory,
            threshold=_coverage_threshold(args.min_pixels, args.min_cameras),
        )
        report = result.report
        summary = {
            "audit_duration_seconds": report.audit_duration_seconds,
            "component_count": report.summary.component_count,
            "components_meeting_threshold": report.summary.components_meeting_threshold,
            "components_never_observed": report.summary.components_never_observed,
            "evidence_sha256": report.evidence_sha256,
            "frames_audited": report.summary.frames_audited,
            "instance_ids_crosscheck_agrees": report.instance_ids_crosscheck.agrees,
            "min_cameras": report.threshold.min_cameras,
            "min_pixels": report.threshold.min_pixels,
            "orientation_coverage": "unknown-for-every-component",
            "render_id": report.render_id,
            "trust_effect": report.trust_effect,
        }
        if args.report is not None:
            from pipeline.synthetic_village.coverage_audit import write_coverage_report

            summary["report_path"] = str(write_coverage_report(report, args.report))
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "audit-view-overlap":
        report = _audit_render_view_overlap()(
            args.render_root,
            sample_stride_px=args.sample_stride,
            depth_relative_tolerance=args.depth_relative_tolerance,
            minimum_symmetric_overlap_ratio=args.min_symmetric_overlap,
        )
        summary = {
            "camera_count": report.summary.camera_count,
            "passing_camera_count": report.summary.passing_camera_count,
            "failing_camera_ids": list(report.summary.failing_camera_ids),
            "minimum_best_overlap_ratio": report.summary.minimum_best_overlap_ratio,
            "median_best_overlap_ratio": report.summary.median_best_overlap_ratio,
            "maximum_best_overlap_ratio": report.summary.maximum_best_overlap_ratio,
            "minimum_symmetric_overlap_ratio": (
                report.parameters.minimum_symmetric_overlap_ratio
            ),
            "overlap_semantics": report.overlap_semantics,
            "passes": report.summary.passes,
            "render_id": report.source_render_id,
            "trust_effect": report.trust_effect,
        }
        if args.report is not None:
            summary["report_path"] = str(
                _write_view_overlap_audit()(report, args.report),
            )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if report.summary.passes else 2
    if args.command == "plan-production":
        build_plan, batch_slice, registry_digest, plan_bytes = _import_production_profile()
        plan = build_plan()
        if args.batch_index is not None and args.batch_count is None:
            raise SystemExit("--batch-index requires --batch-count")
        summary = {
            "profile_id": plan.profile_id,
            "journal_schema": plan.journal_schema,
            "declared_target_count": plan.declared_target_count,
            "camera_count": plan.camera_count,
            "complete": plan.complete,
            "camera_registry_sha256": registry_digest(plan),
            "scene_plan_sha256": plan.scene_plan_sha256,
            "elevated_topology_sha256": plan.elevated_topology_sha256,
            "geometry_trust": plan.geometry_trust,
            "synthetic": plan.synthetic,
            "group_coverage": [row.model_dump(mode="json") for row in plan.group_coverage],
            "route_loops": [row.model_dump(mode="json") for row in plan.route_loops],
            "unplaced_groups": [row.model_dump(mode="json") for row in plan.unplaced_groups],
            # 没做到的需求必须出现在【操作者实际读的那份输出】里。只写进 plan
            # JSON 等于没说。
            "undelivered_requirements": [
                row.model_dump(mode="json") for row in plan.undelivered_requirements
            ],
        }
        if args.batch_count is not None:
            if args.batch_index is None:
                summary["batch_sizes"] = [
                    len(batch_slice(plan, batch_index=index, batch_count=args.batch_count))
                    for index in range(args.batch_count)
                ]
            else:
                summary["batch_camera_ids"] = list(
                    batch_slice(
                        plan, batch_index=args.batch_index, batch_count=args.batch_count
                    )
                )
        if args.plan is not None:
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_bytes(plan_bytes(plan))
            summary["plan_path"] = str(args.plan)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "render-production-local":
        result = _run_local_production_render()(
            training_build_directory=args.build_directory,
            material_bundle_root=args.material_bundle_root,
            visual_pack_root=args.visual_pack_root,
            minimum_valid_pixel_ratio=args.min_valid_pixel_ratio,
            clearance_near_distance_m=args.clearance_near_distance_m,
            minimum_upper_middle_near_hit_count=(
                args.min_upper_middle_near_hits
            ),
            post_render_policy=_load_post_render_policy(
                args.post_render_policy,
            ),
            preflight_only=args.preflight_only,
            camera_ids=tuple(args.camera) if args.camera else None,
            timeout_seconds=args.timeout_seconds,
            render_root=args.render_root,
            repo_root=ROOT,
        )
        print(
            json.dumps(
                {
                    "journal_path": str(result.journal_path),
                    "render_id": result.render_id,
                    "render_root": str(result.render_root),
                    "rendered_count": result.rendered_count,
                    "rejected_count": result.rejected_count,
                    "reused_count": result.reused_count,
                    "preflight_id": result.preflight_id,
                    "preflight_report_path": str(
                        result.preflight_report_path,
                    ),
                    "preflight_rejected_count": (
                        result.preflight_rejected_count
                    ),
                    "preflight_only": result.preflight_only,
                    "verification_level": "L0",
                    "trust_effect": "none-quality-filter-only",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "render-production-windows":
        result = _run_windows_production_render()(
            verified_v2_build=args.verified_v2_build,
            material_bundle_root=args.material_bundle_root,
            surface_realism_profile_id=args.surface_realism_profile,
            visual_pack_root=args.visual_pack_root,
            minimum_valid_pixel_ratio=args.min_valid_pixel_ratio,
            clearance_near_distance_m=args.clearance_near_distance_m,
            minimum_upper_middle_near_hit_count=(
                args.min_upper_middle_near_hits
            ),
            post_render_policy=_load_post_render_policy(
                args.post_render_policy,
            ),
            preflight_only=args.preflight_only,
            camera_ids=tuple(args.camera) if args.camera else None,
            timeout_seconds=args.timeout_seconds,
            render_root=args.render_root,
            repo_root=ROOT,
        )
        print(
            json.dumps(
                {
                    "build_adapter": "windows-textured-v2",
                    "journal_path": str(result.journal_path),
                    "render_id": result.render_id,
                    "render_root": str(result.render_root),
                    "rendered_count": result.rendered_count,
                    "rejected_count": result.rejected_count,
                    "reused_count": result.reused_count,
                    "preflight_id": result.preflight_id,
                    "preflight_report_path": str(
                        result.preflight_report_path,
                    ),
                    "preflight_rejected_count": (
                        result.preflight_rejected_count
                    ),
                    "preflight_only": result.preflight_only,
                    "build_verification_level": "L2",
                    "frame_verification_level": "L0",
                    "trust_effect": "none-quality-filter-only",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "render-reciprocal-production":
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from pipeline.synthetic_village.production_preflight import (
            ProductionClearancePolicy,
        )
        from pipeline.synthetic_village.production_render import (
            LocalProductionQualityPolicy,
        )
        from pipeline.synthetic_village.reciprocal_route_module_runtime import (
            RECIPROCAL_ROUTE_REPORT_NAME,
            RECIPROCAL_ROUTE_REQUEST_NAME,
        )

        runtime_request = _load_reciprocal_route_runtime_request()(
            args.reciprocal_build / RECIPROCAL_ROUTE_REQUEST_NAME,
        )
        verified_build = _verify_reciprocal_production_build()(
            report_path=args.reciprocal_build / RECIPROCAL_ROUTE_REPORT_NAME,
            runtime_request=runtime_request,
        )
        build_plan, _, _, _ = _import_production_profile()
        result = _run_reciprocal_production_batch()(
            verified_build=verified_build,
            source_plan=build_plan(),
            targets=_parse_reciprocal_batch_targets(args.target),
            blender_executable=args.blender,
            output_root=args.output_root,
            clearance_policy=ProductionClearancePolicy(
                near_distance_m=args.clearance_near_distance_m,
                minimum_upper_middle_near_hit_count=(
                    args.min_upper_middle_near_hits
                ),
            ),
            quality_policy=LocalProductionQualityPolicy(
                minimum_valid_pixel_ratio=args.min_valid_pixel_ratio,
            ),
            post_render_policy=_load_post_render_policy(
                args.post_render_policy,
            ),
            timeout_seconds=args.timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "accepted_count": result.accepted_count,
                    "batch_id": result.batch_id,
                    "batch_root": str(result.batch_root),
                    "failed_count": result.failed_count,
                    "journal_path": str(result.journal_path),
                    "reused_count": result.reused_count,
                    "synthetic": True,
                    "verification_level": "L0",
                    "trust_effect": "none-quality-filter-only",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command in {"build-local-orbit-plan", "audit-local-orbit"}:
        from pipeline.synthetic_village.reciprocal_route_module_runtime import (
            RECIPROCAL_ROUTE_REPORT_NAME,
            RECIPROCAL_ROUTE_REQUEST_NAME,
        )

        runtime_request = _load_reciprocal_route_runtime_request()(
            args.reciprocal_build / RECIPROCAL_ROUTE_REQUEST_NAME,
        )
        verified_build = _verify_reciprocal_production_build()(
            report_path=args.reciprocal_build / RECIPROCAL_ROUTE_REPORT_NAME,
            runtime_request=runtime_request,
        )
        build_plan, _, _, _ = _import_production_profile()
        source_plan = build_plan()
        if args.command == "build-local-orbit-plan":
            plan = _build_waterwheel_local_orbit_plan()(
                source_plan=source_plan,
                environment_module_plan_sha256=(
                    runtime_request.base_environment_module_plan_sha256
                ),
                exact_build_id=verified_build.build_id,
                exact_blend_sha256=verified_build.blend_sha256,
                anchor_m=tuple(args.anchor_m),
            )
            plan_bytes = _canonical_local_orbit_plan_bytes()(plan)
            output_plan = args.output_plan.absolute()
            output_plan.parent.resolve(strict=True)
            with output_plan.open("xb") as stream:
                stream.write(plan_bytes)
            print(
                json.dumps(
                    {
                        "exact_build_id": plan.exact_build_id,
                        "exact_blend_sha256": plan.exact_blend_sha256,
                        "local_orbit_plan_sha256": hashlib.sha256(
                            plan_bytes,
                        ).hexdigest(),
                        "output_plan": str(output_plan),
                        "synthetic": True,
                        "verification_level": "L0",
                        "geometry_usability": "preview-only",
                        "training_use": "forbidden-as-multiview",
                        "trust_effect": "none-quality-filter-only",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return 0
        from pipeline.synthetic_village.production_preflight import (
            ProductionClearancePolicy,
        )
        from pipeline.synthetic_village.production_render import (
            LocalProductionQualityPolicy,
        )

        result = _run_local_orbit_audit()(
            verified_build=verified_build,
            source_plan=source_plan,
            local_orbit_plan=_load_local_orbit_plan()(
                args.local_orbit_plan,
            ),
            verified_environment_module_plan_sha256=(
                runtime_request.base_environment_module_plan_sha256
            ),
            blender_executable=args.blender,
            output_root=args.output_root,
            clearance_policy=ProductionClearancePolicy(
                near_distance_m=args.clearance_near_distance_m,
                minimum_upper_middle_near_hit_count=(
                    args.min_upper_middle_near_hits
                ),
            ),
            quality_policy=LocalProductionQualityPolicy(
                minimum_valid_pixel_ratio=args.min_valid_pixel_ratio,
            ),
            post_render_policy=_load_post_render_policy(
                args.post_render_policy,
            ),
            timeout_seconds=args.timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "accepted_frame_count": result.report.accepted_frame_count,
                    "assembly_visible_frame_count": (
                        result.report.assembly_visible_frame_count
                    ),
                    "audit_root": str(result.audit_root),
                    "local_orbit_plan_sha256": (
                        result.report.local_orbit_plan_sha256
                    ),
                    "report_path": str(result.report_path),
                    "report_sha256": result.report.report_sha256,
                    "synthetic": True,
                    "verification_level": "L0",
                    "geometry_usability": "preview-only",
                    "training_use": "forbidden-as-multiview",
                    "trust_effect": "none-quality-filter-only",
                    "wheel_visible_frame_count": (
                        result.report.wheel_visible_frame_count
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "build-environment-modules":
        verify_windows_production_build = _verify_windows_production_build()
        verified_build = verify_windows_production_build(
            directory=args.verified_v2_build,
            material_bundle_root=args.material_bundle_root,
            repo_root=ROOT,
            visual_pack_root=args.visual_pack_root,
            surface_realism_profile_id=args.surface_realism_profile,
        )
        build_kwargs = {
            "base_build": verified_build,
            "repo_root": ROOT,
            "timeout_seconds": args.timeout_seconds,
        }
        if args.build_root is not None:
            build_kwargs["build_root"] = args.build_root
        result = _run_environment_module_build()(
            **build_kwargs,
        )
        print(
            json.dumps(
                {
                    "build_adapter": "environment-module-runtime-v1",
                    "build_id": result.request.build_id,
                    "final_directory": str(result.final_directory),
                    "base_build_id": result.request.base_build_id,
                    "environment_module_plan_sha256": (
                        result.request.environment_module_plan_sha256
                    ),
                    "artifact_name": result.report.artifact.name,
                    "artifact_sha256": result.report.artifact.sha256,
                    "artifact_size_bytes": result.report.artifact.size_bytes,
                    "counts": result.report.counts.model_dump(mode="json"),
                    "verification_level": "L0",
                    "geometry_usability": "preview-only",
                    "stage": "modeled-unverified",
                    "trust_effect": "none",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return 0
    if args.command == "weather-variants":
        weather = _import_weather_profile()
        if args.request_block is not None and args.profile is None:
            raise SystemExit("--request-block requires --profile")
        manifest = weather.build_weather_variants_manifest()
        summary = {
            "schema": manifest["schema"],
            "synthetic": manifest["synthetic"],
            "geometry_trust": manifest["geometry_trust"],
            "sky_model": manifest["sky_model"],
            "variants": [
                {
                    "profile_id": row["profile_id"],
                    "description": row["description"],
                    "sun_elevation_deg": row["sun_elevation_deg"],
                    "sun_azimuth_deg": row["sun_azimuth_deg"],
                    "sun_color_temp_k": row["sun_color_temp_k"],
                    "lighting_digest": row["lighting_digest"],
                }
                for row in manifest["variants"]
            ],
            "pipeline_status_note": manifest["pipeline_status_note"],
        }
        if args.manifest is not None:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_bytes(weather.canonical_manifest_bytes(manifest))
            summary["manifest_path"] = str(args.manifest)
        if args.profile is not None:
            block = weather.weather_request_block(args.profile)
            if args.request_block is not None:
                args.request_block.parent.mkdir(parents=True, exist_ok=True)
                args.request_block.write_bytes(
                    (
                        json.dumps(block, ensure_ascii=False, indent=2, sort_keys=True)
                        + "\n"
                    ).encode("utf-8"),
                )
                summary["request_block_path"] = str(args.request_block)
            summary["selected_profile"] = {
                "profile_id": block["profile_id"],
                "lighting_digest": block["lighting_digest"],
            }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
