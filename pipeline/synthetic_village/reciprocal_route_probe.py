"""Reciprocal-route mesh/collision probe report (HANDOFF-CODEX-011 P0-1).

This module defines the content-addressed report schema produced by
``scripts/blender/probe_reciprocal_route_modules.py``.  The probe loads
the fresh reciprocal-route ``.blend`` and measures real geometric
properties from the Blender mesh: route clear width, slope, clearance,
module-module intersection, module-environment intersection, and
canonical topology attachment.

Provenance contract:
  * The probe is fail-closed: every measurement is real, taken from the
    Blender mesh via bmesh / BVH / ray_cast.  No measurement is inferred
    from the plan or the build report.
  * The probe does NOT promote ``modeled-unverified`` trust.  All trust
    fields remain Literal-locked to ``preview-only`` / ``L0`` / ``none``.
  * The probe report is content-addressed: ``probe_report_sha256`` is
    the SHA-256 of the canonical JSON bytes, and any field change
    (including input SHA, measurement value, or pass/fail flag) alters
    the report SHA.
  * The probe binds its own script SHA (``probe_script_sha256``), so a
    report cannot claim to come from a script whose bytes have been
    tampered with.

Schema boundaries:
  * ``ReciprocalRouteProbeReport`` is a *report*, not a plan.  It does
    not change ``ReciprocalRouteModulePlan`` or any v1 plan field.
  * The probe does not bind to ``production_render_id``; the caller
    (Codex §3 chain) decides whether to consume the probe as evidence
    for camera placement.
  * Fail-closed semantics: a measurement that cannot be taken (e.g.,
    ray missed, BVH empty) is recorded as ``passed=False`` with a
    ``failure_reason``; the report is still emitted, but its
    ``summary.overall_passed`` is ``False``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .reciprocal_route_module import (
    BATCH8_ARCHIVE_SHA256,
    BATCH8_RELEASE_MANIFEST_SHA256,
    BATCH9_ARCHIVE_SHA256,
    BATCH9_RELEASE_MANIFEST_SHA256,
    ModuleId,
)

PROBE_SCHEMA = "nantai.synthetic-village.reciprocal-route-probe.v1"
PROBE_ID = "synthetic-village-reciprocal-route-probe-v1"

#: Geometric thresholds for pass/fail (HANDOFF-OPUS-009 + HANDOFF-CODEX-011).
#: These are *measurement* thresholds, not trust thresholds: failing one
#: does not promote or demote trust; it marks the probe as ``passed=False``
#: so the caller cannot consume a measurement that did not actually clear.
MIN_ROUTE_CLEAR_WIDTH_M = 1.2
MAX_ROUTE_SLOPE_PCT = 12.0
MIN_ROUTE_CLEARANCE_M = 2.4
MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M = 2.0

#: Number of perpendicular ray samples per module route (HANDOFF-CODEX-011).
ROUTE_SAMPLES_PER_MODULE = 5

#: Expected module-module pair count: 6C2 = 15.
EXPECTED_MODULE_MODULE_PAIR_COUNT = 15

#: Expected module count.
EXPECTED_MODULE_COUNT = 6

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ModulePairKey = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9\-]+--[a-z0-9\-]+$"),
]


class ProbeError(ValueError):
    """The probe report cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Per-module route measurements.
# --------------------------------------------------------------------------- #


class RouteSampleMeasurement(FrozenModel):
    """One perpendicular sample along a module's route polyline."""

    arc_length_m: float = Field(ge=0.0, allow_inf_nan=False)
    #: Left/right perpendicular ray hit distances (m).  ``None`` if the
    #: ray missed every obstacle within ``max_distance_m`` -- recorded
    #: as a failure, not as ``inf``.
    left_clear_m: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    right_clear_m: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    #: Upward (clearance) ray hit distance (m).  ``None`` if no overhead
    #: obstacle was hit -- recorded as ``clearance_open`` in the parent
    #: measurement, not as a failure (open sky is legitimate).
    upward_clear_m: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    #: World-space sample position (m, scene-local).
    sample_position_m: tuple[float, float, float]
    #: Route forward direction unit vector at this sample (scene-local).
    route_forward: tuple[float, float, float]


class ModuleRouteProbe(FrozenModel):
    """Per-module route geometry measurements.

    Measurements are taken along the polyline through the module's part
    centers (ordered by ``instance_id``).  ``clear_width_min_m`` is the
    minimum of (left_clear + right_clear) across all samples; ``None``
    if any sample's rays missed (failure).  ``slope_pct`` is the gradient
    from the first to the last part center.  ``clearance_min_m`` is the
    minimum upward ray hit distance; ``None`` means open sky throughout
    (legitimate for outdoor routes).
    """

    role_module_id: ModuleId
    sample_count: int = Field(ge=1, le=100)
    samples: tuple[RouteSampleMeasurement, ...] = Field(min_length=1, max_length=100)
    clear_width_min_m: float | None = Field(
        default=None, ge=0.0, allow_inf_nan=False,
    )
    slope_pct: float = Field(allow_inf_nan=False)
    clearance_min_m: float | None = Field(
        default=None, ge=0.0, allow_inf_nan=False,
    )
    #: Horizontal route length (m), sum of part-center-to-part-center distances.
    route_length_m: float = Field(ge=0.0, allow_inf_nan=False)
    #: Pass flag: all samples have finite left/right clears, clear_width_min_m
    #: >= MIN_ROUTE_CLEAR_WIDTH_M, |slope_pct| <= MAX_ROUTE_SLOPE_PCT, and
    #: (clearance_min_m is None or clearance_min_m >= MIN_ROUTE_CLEARANCE_M).
    passed: bool
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _sample_count_matches_samples(self) -> ModuleRouteProbe:
        if self.sample_count != len(self.samples):
            raise ValueError(
                "ModuleRouteProbe.sample_count disagrees with len(samples)",
            )
        if self.passed and self.failure_reason is not None:
            raise ValueError(
                "ModuleRouteProbe.failure_reason must be None when passed=True",
            )
        if not self.passed and self.failure_reason is None:
            raise ValueError(
                "ModuleRouteProbe.failure_reason must be set when passed=False",
            )
        return self


# --------------------------------------------------------------------------- #
# Intersection probes.
# --------------------------------------------------------------------------- #


class ModuleModuleIntersectionProbe(FrozenModel):
    """One pairwise module-module BVH overlap test."""

    pair_key: ModulePairKey
    module_a: ModuleId
    module_b: ModuleId
    intersection_count: int = Field(ge=0)
    #: Pass flag: intersection_count == 0.  Any overlap is a fail-closed
    #: signal that two modules occupy the same space.
    passed: bool
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _pass_matches_count(self) -> ModuleModuleIntersectionProbe:
        expected_passed = self.intersection_count == 0
        if self.passed != expected_passed:
            raise ValueError(
                "ModuleModuleIntersectionProbe.passed disagrees with "
                "intersection_count",
            )
        if self.passed and self.failure_reason is not None:
            raise ValueError(
                "ModuleModuleIntersectionProbe.failure_reason must be None "
                "when passed=True",
            )
        if not self.passed and self.failure_reason is None:
            raise ValueError(
                "ModuleModuleIntersectionProbe.failure_reason must be set "
                "when passed=False",
            )
        return self


class ModuleEnvironmentIntersectionProbe(FrozenModel):
    """Per-module BVH overlap test against v1 environment objects."""

    role_module_id: ModuleId
    intersecting_object_ids: tuple[str, ...] = Field(default=())
    intersection_count: int = Field(ge=0)
    #: Pass flag: no intersections with v1 environment objects.  Some
    #: intersections may be legitimate (e.g., a reciprocal-route part
    #: intentionally attaches to a v1 path-network edge); the probe
    #: records them all and lets the caller decide.  For fail-closed
    #: semantics, ``passed`` is False if any intersection is found.
    passed: bool
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _pass_matches_count(self) -> ModuleEnvironmentIntersectionProbe:
        expected_passed = self.intersection_count == 0
        if self.passed != expected_passed:
            raise ValueError(
                "ModuleEnvironmentIntersectionProbe.passed disagrees with "
                "intersection_count",
            )
        if len(self.intersecting_object_ids) != self.intersection_count:
            raise ValueError(
                "ModuleEnvironmentIntersectionProbe.intersecting_object_ids "
                "length disagrees with intersection_count",
            )
        if self.passed and self.failure_reason is not None:
            raise ValueError(
                "ModuleEnvironmentIntersectionProbe.failure_reason must be "
                "None when passed=True",
            )
        if not self.passed and self.failure_reason is None:
            raise ValueError(
                "ModuleEnvironmentIntersectionProbe.failure_reason must be "
                "set when passed=False",
            )
        return self


# --------------------------------------------------------------------------- #
# Topology attachment probes.
# --------------------------------------------------------------------------- #


class TopologyAttachmentProbe(FrozenModel):
    """Per-module attachment to its declared canonical topology edge/node.

    Measures the world-space distance from the module's first part center
    to the nearest surface of the declared ``topology_ref`` object (e.g.,
    ``path-network-003``).  ``passed`` is True iff the distance is <=
    MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M, proving the module's role route
    actually starts at the canonical topology edge.

    ``attachment_distance_m`` is ``None`` when the measurement cannot be
    taken (topology_ref object not in scene, module has no parts, or
    ``closest_point_on_mesh`` returned no hit).  ``None`` is honest:
    the report records the absence of a measurement rather than lying
    with ``inf``.  When ``attachment_distance_m is None``, ``passed``
    must be ``False`` and ``failure_reason`` must be set.
    """

    role_module_id: ModuleId
    topology_ref: str = Field(min_length=1)
    attachment_distance_m: float | None = Field(
        default=None, ge=0.0, allow_inf_nan=False,
    )
    passed: bool
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _pass_matches_distance(self) -> TopologyAttachmentProbe:
        if self.attachment_distance_m is None:
            if self.passed:
                raise ValueError(
                    "TopologyAttachmentProbe.passed must be False when "
                    "attachment_distance_m is None (no measurement)",
                )
            if self.failure_reason is None:
                raise ValueError(
                    "TopologyAttachmentProbe.failure_reason must be set "
                    "when attachment_distance_m is None",
                )
            return self
        expected_passed = (
            self.attachment_distance_m <= MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M
        )
        if self.passed != expected_passed:
            raise ValueError(
                "TopologyAttachmentProbe.passed disagrees with "
                "attachment_distance_m threshold",
            )
        if self.passed and self.failure_reason is not None:
            raise ValueError(
                "TopologyAttachmentProbe.failure_reason must be None when "
                "passed=True",
            )
        if not self.passed and self.failure_reason is None:
            raise ValueError(
                "TopologyAttachmentProbe.failure_reason must be set when "
                "passed=False",
            )
        return self


# --------------------------------------------------------------------------- #
# Summary.
# --------------------------------------------------------------------------- #


class ProbeSummary(FrozenModel):
    """Aggregate pass/fail counts across all measurements."""

    module_route_passed_count: int = Field(ge=0, le=EXPECTED_MODULE_COUNT)
    module_route_failed_count: int = Field(ge=0, le=EXPECTED_MODULE_COUNT)
    module_module_intersection_passed_count: int = Field(
        ge=0, le=EXPECTED_MODULE_MODULE_PAIR_COUNT,
    )
    module_module_intersection_failed_count: int = Field(
        ge=0, le=EXPECTED_MODULE_MODULE_PAIR_COUNT,
    )
    module_environment_intersection_passed_count: int = Field(
        ge=0, le=EXPECTED_MODULE_COUNT,
    )
    module_environment_intersection_failed_count: int = Field(
        ge=0, le=EXPECTED_MODULE_COUNT,
    )
    topology_attachment_passed_count: int = Field(
        ge=0, le=EXPECTED_MODULE_COUNT,
    )
    topology_attachment_failed_count: int = Field(
        ge=0, le=EXPECTED_MODULE_COUNT,
    )
    #: Overall pass: every measurement category has zero failures.
    overall_passed: bool

    @model_validator(mode="after")
    def _counts_match_total(self) -> ProbeSummary:
        if (
            self.module_route_passed_count + self.module_route_failed_count
            != EXPECTED_MODULE_COUNT
        ):
            raise ValueError("module_route counts must sum to 6")
        if (
            self.module_module_intersection_passed_count
            + self.module_module_intersection_failed_count
            != EXPECTED_MODULE_MODULE_PAIR_COUNT
        ):
            raise ValueError("module_module_intersection counts must sum to 15")
        if (
            self.module_environment_intersection_passed_count
            + self.module_environment_intersection_failed_count
            != EXPECTED_MODULE_COUNT
        ):
            raise ValueError("module_environment_intersection counts must sum to 6")
        if (
            self.topology_attachment_passed_count
            + self.topology_attachment_failed_count
            != EXPECTED_MODULE_COUNT
        ):
            raise ValueError("topology_attachment counts must sum to 6")
        expected_overall = (
            self.module_route_failed_count == 0
            and self.module_module_intersection_failed_count == 0
            and self.module_environment_intersection_failed_count == 0
            and self.topology_attachment_failed_count == 0
        )
        if self.overall_passed != expected_overall:
            raise ValueError(
                "ProbeSummary.overall_passed disagrees with category counts",
            )
        return self


# --------------------------------------------------------------------------- #
# Main report.
# --------------------------------------------------------------------------- #


class ReciprocalRouteProbeReport(FrozenModel):
    """Content-addressed probe report for the fresh reciprocal-route build.

    The report is the only output the §3 caller chain consumes to decide
    whether to materialise the six standing-eye role camera candidates
    into real ``ProductionCameraPose`` instances.  It does NOT promote
    ``modeled-unverified`` trust: all trust fields remain Literal-locked.
    """

    schema_version: Literal[PROBE_SCHEMA] = PROBE_SCHEMA
    probe_id: Literal[PROBE_ID] = PROBE_ID

    # ------------------------------------------------------------------ #
    # Input identities (content-addressed bindings).
    # ------------------------------------------------------------------ #
    probe_script_sha256: Sha256
    input_blend_sha256: Sha256
    input_build_id: Sha256
    input_plan_sha256: Sha256
    input_build_report_sha256: Sha256
    input_object_registry_sha256: Sha256

    # Bind the plan's Batch 8/9 manifest SHAs to prevent report forgery
    # against a plan that does not actually exist.
    batch8_release_manifest_sha256: Literal[BATCH8_RELEASE_MANIFEST_SHA256] = (
        BATCH8_RELEASE_MANIFEST_SHA256
    )
    batch8_archive_sha256: Literal[BATCH8_ARCHIVE_SHA256] = BATCH8_ARCHIVE_SHA256
    batch9_release_manifest_sha256: Literal[BATCH9_RELEASE_MANIFEST_SHA256] = (
        BATCH9_RELEASE_MANIFEST_SHA256
    )
    batch9_archive_sha256: Literal[BATCH9_ARCHIVE_SHA256] = BATCH9_ARCHIVE_SHA256

    # ------------------------------------------------------------------ #
    # Measurements.
    # ------------------------------------------------------------------ #
    module_route_probes: tuple[ModuleRouteProbe, ...] = Field(
        min_length=EXPECTED_MODULE_COUNT, max_length=EXPECTED_MODULE_COUNT,
    )
    module_module_intersections: tuple[
        ModuleModuleIntersectionProbe, ...
    ] = Field(
        min_length=EXPECTED_MODULE_MODULE_PAIR_COUNT,
        max_length=EXPECTED_MODULE_MODULE_PAIR_COUNT,
    )
    module_environment_intersections: tuple[
        ModuleEnvironmentIntersectionProbe, ...
    ] = Field(
        min_length=EXPECTED_MODULE_COUNT, max_length=EXPECTED_MODULE_COUNT,
    )
    topology_attachment_probes: tuple[TopologyAttachmentProbe, ...] = Field(
        min_length=EXPECTED_MODULE_COUNT, max_length=EXPECTED_MODULE_COUNT,
    )

    summary: ProbeSummary

    # ------------------------------------------------------------------ #
    # Trust (Literal-locked, no promotion).
    # ------------------------------------------------------------------ #
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"
    verification_level: Literal["L0"] = "L0"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none"] = "none"

    # ------------------------------------------------------------------ #
    # Honest disclosure.
    # ------------------------------------------------------------------ #
    disclosure: str = Field(min_length=10)

    @model_validator(mode="after")
    def _modules_are_exact_and_ordered(self) -> ReciprocalRouteProbeReport:
        expected = (
            "central-courtyard-downhill",
            "bridge-deck-crossing",
            "watermill-tailrace",
            "covered-gallery-underpass",
            "forest-orchard-boundary",
            "lower-valley-uphill",
        )
        route_ids = tuple(p.role_module_id for p in self.module_route_probes)
        if route_ids != expected:
            raise ValueError(
                "module_route_probes must be ordered one-per-module matching "
                "the six module IDs",
            )
        env_ids = tuple(
            p.role_module_id for p in self.module_environment_intersections
        )
        if env_ids != expected:
            raise ValueError(
                "module_environment_intersections must be ordered one-per-module",
            )
        attach_ids = tuple(
            p.role_module_id for p in self.topology_attachment_probes
        )
        if attach_ids != expected:
            raise ValueError(
                "topology_attachment_probes must be ordered one-per-module",
            )
        # module_module_intersections: 15 pairs, sorted by pair_key.
        pair_keys = tuple(p.pair_key for p in self.module_module_intersections)
        if len(set(pair_keys)) != len(pair_keys):
            raise ValueError(
                "module_module_intersections pair_keys must be unique",
            )
        return self


# --------------------------------------------------------------------------- #
# Canonical bytes + content addressing.
# --------------------------------------------------------------------------- #


def canonical_reciprocal_route_probe_report_bytes(
    report: ReciprocalRouteProbeReport,
) -> bytes:
    return _canonical(report.model_dump(mode="json"))


def reciprocal_route_probe_report_sha256(
    report: ReciprocalRouteProbeReport,
) -> str:
    return hashlib.sha256(
        canonical_reciprocal_route_probe_report_bytes(report),
    ).hexdigest()


def verify_reciprocal_route_probe_report(
    report: ReciprocalRouteProbeReport,
    *,
    expected_probe_script_sha256: str,
    expected_blend_sha256: str,
    expected_build_id: str,
    expected_plan_sha256: str,
    expected_build_report_sha256: str,
    expected_object_registry_sha256: str,
) -> None:
    """Re-bind every input identity; raise on any mismatch.

    The verifier checks that the report's input SHAs match the
    caller-supplied expected values.  It does NOT re-measure the mesh
    (that would require loading Blender); it only verifies content
    addressing.  Re-canonicalisation round-trips the report through
    JSON to catch any non-canonical bytes.
    """

    if report.probe_script_sha256 != expected_probe_script_sha256:
        raise ProbeError(
            "probe report probe_script_sha256 disagrees with expected script SHA",
        )
    if report.input_blend_sha256 != expected_blend_sha256:
        raise ProbeError(
            "probe report input_blend_sha256 disagrees with expected blend SHA",
        )
    if report.input_build_id != expected_build_id:
        raise ProbeError(
            "probe report input_build_id disagrees with expected build ID",
        )
    if report.input_plan_sha256 != expected_plan_sha256:
        raise ProbeError(
            "probe report input_plan_sha256 disagrees with expected plan SHA",
        )
    if report.input_build_report_sha256 != expected_build_report_sha256:
        raise ProbeError(
            "probe report input_build_report_sha256 disagrees with expected "
            "build report SHA",
        )
    if report.input_object_registry_sha256 != expected_object_registry_sha256:
        raise ProbeError(
            "probe report input_object_registry_sha256 disagrees with expected "
            "object registry SHA",
        )
    # Re-validate canonical bytes (re-runs every model_validator).
    revalidated = ReciprocalRouteProbeReport.model_validate_json(
        canonical_reciprocal_route_probe_report_bytes(report),
    )
    if revalidated != report:
        raise ProbeError(
            "probe report is not canonical JSON",
        )
