"""Reciprocal-route mesh/collision probe report tests (HANDOFF-CODEX-011 P0-1).

These tests lock the schema, content addressing, validators, and the
runner's mock-subprocess contract for the reciprocal-route probe.  They
do NOT exercise Blender; runner tests use mock subprocess per
project_memory.md ("runner tests must use mock subprocess; do not
perform actual rendering of 175-root Blender files").
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.reciprocal_route_probe import (
    EXPECTED_MODULE_COUNT,
    EXPECTED_MODULE_MODULE_PAIR_COUNT,
    MAX_ROUTE_SLOPE_PCT,
    MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M,
    MIN_ROUTE_CLEAR_WIDTH_M,
    MIN_ROUTE_CLEARANCE_M,
    PROBE_ID,
    PROBE_SCHEMA,
    ModuleEnvironmentIntersectionProbe,
    ModuleModuleIntersectionProbe,
    ModuleRouteProbe,
    ProbeError,
    ProbeSummary,
    ReciprocalRouteProbeReport,
    RouteSampleMeasurement,
    TopologyAttachmentProbe,
    canonical_reciprocal_route_probe_report_bytes,
    reciprocal_route_probe_report_sha256,
    verify_reciprocal_route_probe_report,
)

# --------------------------------------------------------------------------- #
# Test fixtures: a canonical valid report.
# --------------------------------------------------------------------------- #


_PLACEHOLDER_SHA = "0" * 64
_REAL_SHA = "a" * 64


def _sample_measurement(arc_length: float = 0.0) -> RouteSampleMeasurement:
    return RouteSampleMeasurement(
        arc_length_m=arc_length,
        left_clear_m=2.0,
        right_clear_m=2.5,
        upward_clear_m=None,  # open sky
        sample_position_m=(40.0, 30.0, 70.0),
        route_forward=(0.0, -1.0, 0.0),
    )


def _route_probe(
    role_module_id: str = "central-courtyard-downhill",
    *,
    passed: bool = True,
    failure_reason: str | None = None,
) -> ModuleRouteProbe:
    samples = tuple(_sample_measurement(i * 5.0) for i in range(5))
    return ModuleRouteProbe(
        role_module_id=role_module_id,
        sample_count=5,
        samples=samples,
        clear_width_min_m=4.5 if passed else None,
        slope_pct=5.0,
        clearance_min_m=None,
        route_length_m=20.0,
        passed=passed,
        failure_reason=failure_reason,
    )


_MODULE_IDS = (
    "central-courtyard-downhill",
    "bridge-deck-crossing",
    "watermill-tailrace",
    "covered-gallery-underpass",
    "forest-orchard-boundary",
    "lower-valley-uphill",
)


def _module_pair_probes() -> tuple[ModuleModuleIntersectionProbe, ...]:
    """15 pairwise intersection probes, all passing."""
    probes = []
    for i in range(len(_MODULE_IDS)):
        for j in range(i + 1, len(_MODULE_IDS)):
            a = _MODULE_IDS[i]
            b = _MODULE_IDS[j]
            probes.append(
                ModuleModuleIntersectionProbe(
                    pair_key=f"{a}--{b}",
                    module_a=a,
                    module_b=b,
                    intersection_count=0,
                    passed=True,
                    failure_reason=None,
                ),
            )
    return tuple(probes)


def _module_env_probes(passed: bool = True) -> tuple[ModuleEnvironmentIntersectionProbe, ...]:
    return tuple(
        ModuleEnvironmentIntersectionProbe(
            role_module_id=mid,
            intersecting_object_ids=() if passed else ("path-network-001",),
            intersection_count=0 if passed else 1,
            passed=passed,
            failure_reason=None if passed else "intersection detected",
        )
        for mid in _MODULE_IDS
    )


def _topology_probes(passed: bool = True) -> tuple[TopologyAttachmentProbe, ...]:
    return tuple(
        TopologyAttachmentProbe(
            role_module_id=mid,
            topology_ref="path-network-003",
            attachment_distance_m=0.5 if passed else 5.0,
            passed=passed,
            failure_reason=None if passed else "distance exceeds threshold",
        )
        for mid in _MODULE_IDS
    )


def _summary(
    *,
    route_fail: int = 0,
    pair_fail: int = 0,
    env_fail: int = 0,
    attach_fail: int = 0,
) -> ProbeSummary:
    return ProbeSummary(
        module_route_passed_count=EXPECTED_MODULE_COUNT - route_fail,
        module_route_failed_count=route_fail,
        module_module_intersection_passed_count=EXPECTED_MODULE_MODULE_PAIR_COUNT - pair_fail,
        module_module_intersection_failed_count=pair_fail,
        module_environment_intersection_passed_count=EXPECTED_MODULE_COUNT - env_fail,
        module_environment_intersection_failed_count=env_fail,
        topology_attachment_passed_count=EXPECTED_MODULE_COUNT - attach_fail,
        topology_attachment_failed_count=attach_fail,
        overall_passed=(route_fail + pair_fail + env_fail + attach_fail == 0),
    )


def _valid_report(
    *,
    probe_script_sha: str = _REAL_SHA,
    blend_sha: str = _REAL_SHA,
    build_id: str = _REAL_SHA,
    plan_sha: str = _REAL_SHA,
    build_report_sha: str = _REAL_SHA,
    object_registry_sha: str = _REAL_SHA,
    route_passed: bool = True,
    env_passed: bool = True,
    attach_passed: bool = True,
) -> ReciprocalRouteProbeReport:
    return ReciprocalRouteProbeReport(
        probe_script_sha256=probe_script_sha,
        input_blend_sha256=blend_sha,
        input_build_id=build_id,
        input_plan_sha256=plan_sha,
        input_build_report_sha256=build_report_sha,
        input_object_registry_sha256=object_registry_sha,
        module_route_probes=tuple(
            _route_probe(mid, passed=route_passed,
                         failure_reason=None if route_passed else "probe failed")
            for mid in _MODULE_IDS
        ),
        module_module_intersections=_module_pair_probes(),
        module_environment_intersections=_module_env_probes(passed=env_passed),
        topology_attachment_probes=_topology_probes(passed=attach_passed),
        summary=_summary(
            route_fail=0 if route_passed else EXPECTED_MODULE_COUNT,
            env_fail=0 if env_passed else EXPECTED_MODULE_COUNT,
            attach_fail=0 if attach_passed else EXPECTED_MODULE_COUNT,
        ),
        disclosure=(
            "modeled-unverified mesh probe; "
            "measurements are real but trust remains preview-only"
        ),
    )


# --------------------------------------------------------------------------- #
# Schema constants.
# --------------------------------------------------------------------------- #


def test_schema_constants_are_locked() -> None:
    assert PROBE_SCHEMA == "nantai.synthetic-village.reciprocal-route-probe.v1"
    assert PROBE_ID == "synthetic-village-reciprocal-route-probe-v1"
    assert MIN_ROUTE_CLEAR_WIDTH_M == 1.2
    assert MAX_ROUTE_SLOPE_PCT == 12.0
    assert MIN_ROUTE_CLEARANCE_M == 2.4
    assert MAX_TOPOLOGY_ATTACHMENT_DISTANCE_M == 2.0
    assert EXPECTED_MODULE_COUNT == 6
    assert EXPECTED_MODULE_MODULE_PAIR_COUNT == 15


# --------------------------------------------------------------------------- #
# Valid report construction.
# --------------------------------------------------------------------------- #


def test_valid_report_constructs() -> None:
    report = _valid_report()
    assert report.schema_version == PROBE_SCHEMA
    assert report.probe_id == PROBE_ID
    assert report.synthetic is True
    assert report.geometry_usability == "preview-only"
    assert report.verification_level == "L0"
    assert report.trust_effect == "none"
    assert len(report.module_route_probes) == 6
    assert len(report.module_module_intersections) == 15
    assert len(report.module_environment_intersections) == 6
    assert len(report.topology_attachment_probes) == 6
    assert report.summary.overall_passed is True


# --------------------------------------------------------------------------- #
# ModuleRouteProbe validators.
# --------------------------------------------------------------------------- #


def test_route_probe_rejects_sample_count_mismatch() -> None:
    with pytest.raises(ValidationError, match="sample_count"):
        ModuleRouteProbe(
            role_module_id="central-courtyard-downhill",
            sample_count=10,  # mismatch
            samples=tuple(_sample_measurement(i) for i in range(5)),
            clear_width_min_m=4.5,
            slope_pct=5.0,
            clearance_min_m=None,
            route_length_m=20.0,
            passed=True,
            failure_reason=None,
        )


def test_route_probe_rejects_failure_reason_when_passed() -> None:
    with pytest.raises(ValidationError, match="failure_reason"):
        ModuleRouteProbe(
            role_module_id="central-courtyard-downhill",
            sample_count=5,
            samples=tuple(_sample_measurement(i) for i in range(5)),
            clear_width_min_m=4.5,
            slope_pct=5.0,
            clearance_min_m=None,
            route_length_m=20.0,
            passed=True,
            failure_reason="should be None",
        )


def test_route_probe_rejects_missing_failure_reason_when_failed() -> None:
    with pytest.raises(ValidationError, match="failure_reason"):
        ModuleRouteProbe(
            role_module_id="central-courtyard-downhill",
            sample_count=5,
            samples=tuple(_sample_measurement(i) for i in range(5)),
            clear_width_min_m=None,
            slope_pct=5.0,
            clearance_min_m=None,
            route_length_m=20.0,
            passed=False,
            failure_reason=None,
        )


# --------------------------------------------------------------------------- #
# ModuleModuleIntersectionProbe validators.
# --------------------------------------------------------------------------- #


def test_pair_probe_rejects_pass_with_nonzero_intersection() -> None:
    with pytest.raises(ValidationError, match="passed"):
        ModuleModuleIntersectionProbe(
            pair_key="central-courtyard-downhill--bridge-deck-crossing",
            module_a="central-courtyard-downhill",
            module_b="bridge-deck-crossing",
            intersection_count=3,
            passed=True,  # wrong
            failure_reason=None,
        )


def test_pair_probe_rejects_failure_reason_when_passed() -> None:
    with pytest.raises(ValidationError, match="failure_reason"):
        ModuleModuleIntersectionProbe(
            pair_key="central-courtyard-downhill--bridge-deck-crossing",
            module_a="central-courtyard-downhill",
            module_b="bridge-deck-crossing",
            intersection_count=0,
            passed=True,
            failure_reason="should be None",
        )


# --------------------------------------------------------------------------- #
# ModuleEnvironmentIntersectionProbe validators.
# --------------------------------------------------------------------------- #


def test_env_probe_rejects_object_ids_count_mismatch() -> None:
    with pytest.raises(ValidationError, match="intersecting_object_ids"):
        ModuleEnvironmentIntersectionProbe(
            role_module_id="central-courtyard-downhill",
            intersecting_object_ids=("path-network-001", "path-network-002"),
            intersection_count=1,  # mismatch
            passed=False,
            failure_reason="count mismatch",
        )


def test_env_probe_rejects_pass_with_intersections() -> None:
    with pytest.raises(ValidationError, match="passed"):
        ModuleEnvironmentIntersectionProbe(
            role_module_id="central-courtyard-downhill",
            intersecting_object_ids=("path-network-001",),
            intersection_count=1,
            passed=True,  # wrong
            failure_reason=None,
        )


# --------------------------------------------------------------------------- #
# TopologyAttachmentProbe validators.
# --------------------------------------------------------------------------- #


def test_topology_probe_rejects_pass_when_distance_exceeds_threshold() -> None:
    with pytest.raises(ValidationError, match="passed"):
        TopologyAttachmentProbe(
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            attachment_distance_m=10.0,  # exceeds threshold
            passed=True,  # wrong
            failure_reason=None,
        )


def test_topology_probe_rejects_failure_reason_when_passed() -> None:
    with pytest.raises(ValidationError, match="failure_reason"):
        TopologyAttachmentProbe(
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            attachment_distance_m=0.5,
            passed=True,
            failure_reason="should be None",
        )


def test_topology_probe_accepts_none_distance_with_failure() -> None:
    """When attachment_distance_m is None (no measurement), the probe
    records the honest absence rather than ``inf`` and must be failed
    with a failure_reason.  Required for the real-Blender probe where
    ``closest_point_on_mesh`` may return no hit on missing / mesh-less
    topology_ref objects."""
    probe = TopologyAttachmentProbe(
        role_module_id="central-courtyard-downhill",
        topology_ref="path-network-003",
        attachment_distance_m=None,
        passed=False,
        failure_reason="closest_point_on_mesh returned no hit",
    )
    assert probe.attachment_distance_m is None
    assert probe.passed is False


def test_topology_probe_rejects_pass_when_distance_is_none() -> None:
    with pytest.raises(ValidationError, match="passed must be False"):
        TopologyAttachmentProbe(
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            attachment_distance_m=None,
            passed=True,  # wrong: cannot pass without a measurement
            failure_reason=None,
        )


def test_topology_probe_rejects_none_distance_without_failure_reason() -> None:
    with pytest.raises(ValidationError, match="failure_reason must be set"):
        TopologyAttachmentProbe(
            role_module_id="central-courtyard-downhill",
            topology_ref="path-network-003",
            attachment_distance_m=None,
            passed=False,
            failure_reason=None,  # wrong: must explain why no measurement
        )


# --------------------------------------------------------------------------- #
# ProbeSummary validators.
# --------------------------------------------------------------------------- #


def test_summary_rejects_route_counts_not_summing_to_six() -> None:
    with pytest.raises(ValidationError, match="module_route"):
        ProbeSummary(
            module_route_passed_count=5,
            module_route_failed_count=2,  # sums to 7, not 6
            module_module_intersection_passed_count=15,
            module_module_intersection_failed_count=0,
            module_environment_intersection_passed_count=6,
            module_environment_intersection_failed_count=0,
            topology_attachment_passed_count=6,
            topology_attachment_failed_count=0,
            overall_passed=False,
        )


def test_summary_rejects_overall_passed_when_failures_exist() -> None:
    with pytest.raises(ValidationError, match="overall_passed"):
        ProbeSummary(
            module_route_passed_count=5,
            module_route_failed_count=1,
            module_module_intersection_passed_count=15,
            module_module_intersection_failed_count=0,
            module_environment_intersection_passed_count=6,
            module_environment_intersection_failed_count=0,
            topology_attachment_passed_count=6,
            topology_attachment_failed_count=0,
            overall_passed=True,  # wrong: should be False
        )


# --------------------------------------------------------------------------- #
# ReciprocalRouteProbeReport module order + count validators.
# --------------------------------------------------------------------------- #


def test_report_rejects_wrong_module_route_order() -> None:
    import json as _json

    report = _valid_report()
    payload = report.model_dump(mode="json")
    # Swap route probes 0 and 1.
    (
        payload["module_route_probes"][0],
        payload["module_route_probes"][1],
    ) = (
        payload["module_route_probes"][1],
        payload["module_route_probes"][0],
    )
    with pytest.raises(ValidationError, match="module_route_probes"):
        ReciprocalRouteProbeReport.model_validate_json(_json.dumps(payload))


def test_report_rejects_wrong_intersection_pair_count() -> None:
    import json as _json

    report = _valid_report()
    payload = report.model_dump(mode="json")
    payload["module_module_intersections"] = payload["module_module_intersections"][:14]
    with pytest.raises(ValidationError):
        ReciprocalRouteProbeReport.model_validate_json(_json.dumps(payload))


# --------------------------------------------------------------------------- #
# Canonical bytes + content addressing.
# --------------------------------------------------------------------------- #


def test_canonical_bytes_end_with_newline() -> None:
    report = _valid_report()
    assert canonical_reciprocal_route_probe_report_bytes(report).endswith(b"\n")


def test_report_sha256_is_64_hex() -> None:
    report = _valid_report()
    sha = reciprocal_route_probe_report_sha256(report)
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)


def test_report_sha_changes_when_measurement_changes() -> None:
    report = _valid_report()
    original_sha = reciprocal_route_probe_report_sha256(report)
    # Tamper the first route probe's clear_width_min_m.
    first = report.module_route_probes[0]
    tampered_probe = first.model_copy(update={"clear_width_min_m": 5.0})
    tampered_report = report.model_copy(
        update={
            "module_route_probes": (
                tampered_probe,
                *report.module_route_probes[1:],
            ),
        },
    )
    assert (
        reciprocal_route_probe_report_sha256(tampered_report) != original_sha
    )


def test_report_sha_changes_when_input_sha_changes() -> None:
    report = _valid_report()
    original_sha = reciprocal_route_probe_report_sha256(report)
    tampered = report.model_copy(
        update={"input_blend_sha256": "b" * 64},
    )
    assert reciprocal_route_probe_report_sha256(tampered) != original_sha


# --------------------------------------------------------------------------- #
# verify_reciprocal_route_probe_report.
# --------------------------------------------------------------------------- #


def test_verify_passes_for_valid_report() -> None:
    report = _valid_report()
    verify_reciprocal_route_probe_report(
        report,
        expected_probe_script_sha256=_REAL_SHA,
        expected_blend_sha256=_REAL_SHA,
        expected_build_id=_REAL_SHA,
        expected_plan_sha256=_REAL_SHA,
        expected_build_report_sha256=_REAL_SHA,
        expected_object_registry_sha256=_REAL_SHA,
    )


def test_verify_rejects_probe_script_sha_mismatch() -> None:
    report = _valid_report()
    with pytest.raises(ProbeError, match="probe_script_sha256"):
        verify_reciprocal_route_probe_report(
            report,
            expected_probe_script_sha256="b" * 64,
            expected_blend_sha256=_REAL_SHA,
            expected_build_id=_REAL_SHA,
            expected_plan_sha256=_REAL_SHA,
            expected_build_report_sha256=_REAL_SHA,
            expected_object_registry_sha256=_REAL_SHA,
        )


def test_verify_rejects_blend_sha_mismatch() -> None:
    report = _valid_report()
    with pytest.raises(ProbeError, match="input_blend_sha256"):
        verify_reciprocal_route_probe_report(
            report,
            expected_probe_script_sha256=_REAL_SHA,
            expected_blend_sha256="b" * 64,
            expected_build_id=_REAL_SHA,
            expected_plan_sha256=_REAL_SHA,
            expected_build_report_sha256=_REAL_SHA,
            expected_object_registry_sha256=_REAL_SHA,
        )


def test_verify_rejects_plan_sha_mismatch() -> None:
    report = _valid_report()
    with pytest.raises(ProbeError, match="input_plan_sha256"):
        verify_reciprocal_route_probe_report(
            report,
            expected_probe_script_sha256=_REAL_SHA,
            expected_blend_sha256=_REAL_SHA,
            expected_build_id=_REAL_SHA,
            expected_plan_sha256="b" * 64,
            expected_build_report_sha256=_REAL_SHA,
            expected_object_registry_sha256=_REAL_SHA,
        )


# --------------------------------------------------------------------------- #
# Runner tests (mock subprocess, no real Blender invocation).
# --------------------------------------------------------------------------- #


def test_runner_builds_request_with_input_shas(tmp_path: Path) -> None:
    from pipeline.synthetic_village.reciprocal_route_probe_runner import (
        build_reciprocal_route_probe_request,
    )

    blend_path = tmp_path / "village.blend"
    blend_path.write_bytes(b"fake blend bytes")
    expected_blend_sha = hashlib.sha256(b"fake blend bytes").hexdigest()
    request = build_reciprocal_route_probe_request(
        blend_path=blend_path,
        plan_sha256=_REAL_SHA,
        build_id=_REAL_SHA,
        build_report_sha256=_REAL_SHA,
        object_registry_sha256=_REAL_SHA,
    )
    assert request["schema_version"] == "nantai.synthetic-village.reciprocal-route-probe-request.v1"
    assert request["input_blend_sha256"] == expected_blend_sha
    assert request["input_plan_sha256"] == _REAL_SHA
    assert request["input_build_id"] == _REAL_SHA
    assert request["input_build_report_sha256"] == _REAL_SHA
    assert request["input_object_registry_sha256"] == _REAL_SHA
    assert request["probe_script_sha256"]  # auto-computed from script file


def test_runner_runs_probe_via_mock_subprocess(tmp_path: Path) -> None:
    """Runner calls Blender via subprocess, parses the report JSON,
    and verifies it against expected SHAs.  Mock subprocess so no real
    Blender invocation happens."""
    from pipeline.synthetic_village.reciprocal_route_probe_runner import (
        run_reciprocal_route_probe,
    )

    blend_path = tmp_path / "village.blend"
    blend_path.write_bytes(b"fake blend bytes")
    expected_blend_sha = hashlib.sha256(b"fake blend bytes").hexdigest()

    # Build a valid report and write it as the probe output.
    report = _valid_report(
        blend_sha=expected_blend_sha,
        probe_script_sha=_REAL_SHA,  # will be replaced by runner
    )
    # Patch the runner's probe_script_sha256 to match the report.
    report_path = tmp_path / "probe-report.json"
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    # Patch: the runner computes probe_script_sha256 from the script file
    # in real life.  In the test, we patch the SHA-reading helper to return
    # the same SHA the report was constructed with.
    with patch(
        "pipeline.synthetic_village.reciprocal_route_probe_runner._probe_script_sha256",
        return_value=_REAL_SHA,
    ), patch(
        "pipeline.synthetic_village.reciprocal_route_probe_runner._run_blender",
        return_value=report_path,
    ):
        returned_report = run_reciprocal_route_probe(
            blend_path=blend_path,
            plan_sha256=_REAL_SHA,
            build_id=_REAL_SHA,
            build_report_sha256=_REAL_SHA,
            object_registry_sha256=_REAL_SHA,
            blender_path=Path("fake/blender.exe"),
            probe_script_path=Path("fake/probe_script.py"),
            staging_dir=tmp_path,
        )
    assert returned_report.input_blend_sha256 == expected_blend_sha
    assert returned_report.summary.overall_passed is True


def test_runner_rejects_probe_report_with_wrong_blend_sha(tmp_path: Path) -> None:
    """If the probe report claims a different blend SHA, the runner must
    fail-closed via verify_reciprocal_route_probe_report."""
    from pipeline.synthetic_village.reciprocal_route_probe_runner import (
        run_reciprocal_route_probe,
    )

    blend_path = tmp_path / "village.blend"
    blend_path.write_bytes(b"fake blend bytes")

    # Build a report that claims the WRONG blend SHA.
    wrong_report = _valid_report(
        blend_sha="b" * 64,  # wrong
        probe_script_sha=_REAL_SHA,
    )
    report_path = tmp_path / "probe-report.json"
    report_path.write_text(
        json.dumps(wrong_report.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )

    with patch(
        "pipeline.synthetic_village.reciprocal_route_probe_runner._probe_script_sha256",
        return_value=_REAL_SHA,
    ), patch(
        "pipeline.synthetic_village.reciprocal_route_probe_runner._run_blender",
        return_value=report_path,
    ):
        with pytest.raises(ProbeError, match="input_blend_sha256"):
            run_reciprocal_route_probe(
                blend_path=blend_path,
                plan_sha256=_REAL_SHA,
                build_id=_REAL_SHA,
                build_report_sha256=_REAL_SHA,
                object_registry_sha256=_REAL_SHA,
                blender_path=Path("fake/blender.exe"),
                probe_script_path=Path("fake/probe_script.py"),
                staging_dir=tmp_path,
            )
