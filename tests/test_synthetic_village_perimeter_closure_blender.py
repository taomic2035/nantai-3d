"""Pure-boundary and geometry tests for the exact-266 Blender script."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/blender/apply_perimeter_closure_modules.py"


@pytest.fixture(scope="module")
def runtime() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_test_apply_perimeter_closure_modules",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    previous = sys.modules.get("bpy")
    sys.modules["bpy"] = SimpleNamespace()
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("bpy", None)
        else:
            sys.modules["bpy"] = previous


def _part(role: str) -> dict:
    center = {
        "support-retaining": [18.307, 13.627, 4.0],
        "drainage-water": [21.693, 6.373, 4.0],
    }.get(role, [20.0, 10.0, 4.0])
    return {
        "instance_id": 219,
        "module_id": "closure-upstream",
        "part_id": f"closure-upstream-{role}",
        "semantic_role": role,
        "geometry_family": {
            "terrain-contact": "terrain-bench",
            "bidirectional-corridor": "walking-corridor",
            "support-retaining": "retaining-support",
            "drainage-water": "open-drainage",
            "boundary-seam": "sector-seam",
            "vegetation-enclosure": "vegetation-cluster",
        }[role],
        "material_slot_id": "material-stone-block-01",
        "center_m": center,
        "extent_m": [30.0, 12.0, 4.0],
        "orientation_deg": 25.0,
        "inner_anchor_m": [5.0, 3.0, 2.0],
        "outer_anchor_m": [35.0, 17.0, 6.0],
        "previous_seam_m": [30.0, 30.0, 5.0],
        "next_seam_m": [45.0, 5.0, 6.0],
    }


def test_script_constants_are_literal_locked(runtime: ModuleType) -> None:
    assert runtime.REQUEST_SCHEMA == (
        "nantai.synthetic-village.perimeter-closure-runtime-request.v1"
    )
    assert runtime.REPORT_SCHEMA == (
        "nantai.synthetic-village.perimeter-closure-build-report.v1"
    )
    assert runtime.EXPECTED_BASE_ROOTS == 218
    assert runtime.EXPECTED_OVERLAY_ROOTS == 48
    assert runtime.EXPECTED_TOTAL_ROOTS == 266


def test_duplicate_json_keys_fail_closed(runtime: ModuleType) -> None:
    with pytest.raises(runtime.RuntimeBuildError, match="duplicate"):
        json.loads(
            '{"build_id":"a","build_id":"b"}',
            object_pairs_hook=runtime._reject_duplicate_keys,
        )


def test_runtime_paths_require_request_inside_staging(
    runtime: ModuleType,
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    request = staging / "request.json"
    request.write_text("{}", encoding="utf-8")
    actual_request, actual_staging = runtime._runtime_paths(
        ["blender", "--", str(request), str(staging)]
    )
    assert actual_request == request.resolve()
    assert actual_staging == staging.resolve()

    escaped = tmp_path / "escaped.json"
    escaped.write_text("{}", encoding="utf-8")
    with pytest.raises(runtime.RuntimeBuildError, match="inside staging"):
        runtime._runtime_paths(
            ["blender", "--", str(escaped), str(staging)]
        )


@pytest.mark.parametrize(
    ("function_name", "role"),
    (
        ("_build_terrain_contact", "terrain-contact"),
        ("_build_bidirectional_corridor", "bidirectional-corridor"),
        ("_build_support_retaining", "support-retaining"),
        ("_build_drainage_water", "drainage-water"),
        ("_build_boundary_seam", "boundary-seam"),
        ("_build_vegetation_enclosure", "vegetation-enclosure"),
    ),
)
def test_each_geometry_family_is_finite_nonempty_and_measured(
    runtime: ModuleType,
    function_name: str,
    role: str,
) -> None:
    result = getattr(runtime, function_name)(_part(role), None)
    assert result.assembler.vertices
    assert result.assembler.faces
    assert all(
        math.isfinite(value)
        for vertex in result.assembler.vertices
        for value in vertex
    )
    bounds = result.assembler.bounds()
    assert len(bounds) == 6
    assert all(math.isfinite(value) for value in bounds)
    assert result.evidence


def test_route_surface_and_side_structures_leave_standing_eye_open(
    runtime: ModuleType,
) -> None:
    terrain_part = _part("terrain-contact")
    terrain = runtime._build_terrain_contact(terrain_part, None)
    assert terrain.evidence["surface_inner_m"] == tuple(
        terrain_part["inner_anchor_m"]
    )
    assert terrain.evidence["surface_outer_m"] == tuple(
        terrain_part["outer_anchor_m"]
    )

    for role, builder_name in (
        ("support-retaining", "_build_support_retaining"),
        ("drainage-water", "_build_drainage_water"),
    ):
        part = _part(role)
        result = getattr(runtime, builder_name)(part, None)
        assert runtime._endpoint_gap_xy_m(
            result.evidence["side_inner_m"],
            part["inner_anchor_m"],
        ) >= 3.0
        assert runtime._endpoint_gap_xy_m(
            result.evidence["side_outer_m"],
            part["outer_anchor_m"],
        ) >= 3.0


def test_vegetation_trunks_leave_bidirectional_route_clear(
    runtime: ModuleType,
) -> None:
    part = _part("vegetation-enclosure")
    result = runtime._build_vegetation_enclosure(part, None)
    inner = part["inner_anchor_m"]
    outer = part["outer_anchor_m"]
    dx = outer[0] - inner[0]
    dy = outer[1] - inner[1]
    length = (dx * dx + dy * dy) ** 0.5

    assert len(result.evidence["trunk_centers_m"]) == 4
    for x_m, y_m, _z_m in result.evidence["trunk_centers_m"]:
        perpendicular_distance_m = abs(
            dy * x_m - dx * y_m + outer[0] * inner[1] - outer[1] * inner[0]
        ) / length
        assert perpendicular_distance_m >= 5.0


def test_contact_and_endpoint_gap_helpers_measure_not_assert(
    runtime: ModuleType,
) -> None:
    terrain = (0.0, 0.0, 0.0, 10.0, 10.0, 2.0)
    touching = (2.0, 2.0, 2.0, 8.0, 8.0, 7.0)
    floating = (2.0, 2.0, 2.4, 8.0, 8.0, 7.0)
    assert runtime._contact_gap_m(touching, terrain) == 0.0
    assert runtime._contact_gap_m(floating, terrain) == pytest.approx(0.4)
    assert runtime._endpoint_gap_m((0.0, 0.0, 0.0), (3.0, 4.0, 0.0)) == 5.0


def test_sector_validator_uses_measured_geometry(
    runtime: ModuleType,
) -> None:
    module = {
        "module_id": "closure-upstream",
        "inner_anchor_m": [5.0, 3.0, 2.0],
        "outer_anchor_m": [35.0, 17.0, 6.0],
        "previous_seam_m": [30.0, 30.0, 5.0],
        "next_seam_m": [45.0, 5.0, 6.0],
    }
    evidence = {
        "terrain-contact": {
            "bounds": (0.0, 0.0, 0.0, 40.0, 30.0, 2.0),
        },
        "support-retaining": {
            "bounds": (5.0, 3.0, 2.0, 35.0, 17.0, 8.0),
        },
        "bidirectional-corridor": {
            "inner_endpoint_m": (5.0, 3.0, 2.0),
            "outer_endpoint_m": (35.0, 17.0, 6.0),
        },
        "drainage-water": {
            "inner_endpoint_m": (5.0, 3.0, 2.0),
            "outer_endpoint_m": (35.0, 17.0, 6.0),
        },
        "boundary-seam": {
            "previous_endpoint_m": (30.0, 30.0, 5.0),
            "next_endpoint_m": (45.0, 5.0, 6.0),
        },
    }
    measured = runtime._validate_sector_geometry(evidence, module)
    assert measured["terrain_support_contact_gap_m"] == 0.0
    assert measured["corridor_endpoint_gap_m"] == 0.0
    assert measured["drainage_endpoint_gap_m"] == 0.0
    assert measured["previous_seam_gap_m"] == 0.0
    assert measured["next_seam_gap_m"] == 0.0

    floating = dict(evidence)
    floating["support-retaining"] = {
        "bounds": (5.0, 3.0, 2.3, 35.0, 17.0, 8.0),
    }
    with pytest.raises(runtime.RuntimeBuildError, match="contact"):
        runtime._validate_sector_geometry(floating, module)


def test_neighbor_seam_validator_rejects_geometry_gap(
    runtime: ModuleType,
) -> None:
    results = [
        {
            "module_id": "closure-upstream",
            "previous_seam_actual_m": (0.0, 0.0, 0.0),
            "next_seam_actual_m": (1.0, 0.0, 0.0),
        },
        {
            "module_id": "closure-northeast",
            "previous_seam_actual_m": (1.0, 0.0, 0.0),
            "next_seam_actual_m": (0.0, 0.0, 0.0),
        },
    ]
    assert runtime._validate_neighbor_seams(results, max_gap_m=0.2) == (0.0, 0.0)
    results[1]["previous_seam_actual_m"] = (1.3, 0.0, 0.0)
    with pytest.raises(runtime.RuntimeBuildError, match="neighbor seam"):
        runtime._validate_neighbor_seams(results, max_gap_m=0.2)
