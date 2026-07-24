"""Tests for material_uv_audit — texel density variation audit (P2b)."""

from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.material_uv_audit import (
    MaterialTileRecord,
    ObjectTileRecord,
    TexelDensityReport,
    audit_texel_density,
)

ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = ROOT / "scripts/blender/probe_uv_texel_density.py"


@pytest.fixture(scope="module")
def uv_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_test_probe_uv_repeat_distance",
        PROBE_SCRIPT,
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


def _make_materials():
    return [
        MaterialTileRecord(
            material_id="material-packed-earth-01",
            nominal_tile_m=3.0,
            uv_policy="world-xy",
        ),
        MaterialTileRecord(
            material_id="material-terrace-soil-01",
            nominal_tile_m=4.0,
            uv_policy="world-xy",
        ),
        MaterialTileRecord(
            material_id="material-shallow-water-01",
            nominal_tile_m=5.0,
            uv_policy="world-xy",
        ),
        MaterialTileRecord(
            material_id="material-creek-rock-01",
            nominal_tile_m=2.5,
            uv_policy="world-xy",
        ),
        MaterialTileRecord(
            material_id="material-pale-plaster-01",
            nominal_tile_m=3.5,
            uv_policy="dominant-axis-box",
        ),
        MaterialTileRecord(
            material_id="material-dark-timber-01",
            nominal_tile_m=1.6,
            uv_policy="object-long-axis",
        ),
    ]


def _make_objects():
    return [
        ObjectTileRecord(
            object_id="terrain-packed-earth",
            object_category="terrain",
            material_id="material-packed-earth-01",
            tile_scale=3.0,
        ),
        ObjectTileRecord(
            object_id="terrain-terrace-soil",
            object_category="terrain",
            material_id="material-terrace-soil-01",
            tile_scale=3.0,
        ),
        ObjectTileRecord(
            object_id="creek-water",
            object_category="creek",
            material_id="material-shallow-water-01",
            tile_scale=1.0,
        ),
        ObjectTileRecord(
            object_id="creek-rock",
            object_category="creek",
            material_id="material-creek-rock-01",
            tile_scale=1.0,
        ),
        ObjectTileRecord(
            object_id="long-wall-plaster",
            object_category="long-wall",
            material_id="material-pale-plaster-01",
            tile_scale=1.0,
        ),
        ObjectTileRecord(
            object_id="long-wall-timber",
            object_category="long-wall",
            material_id="material-dark-timber-01",
            tile_scale=1.0,
        ),
    ]


class TestAuditTexelDensity:
    def test_returns_report_for_valid_inputs(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        assert isinstance(report, TexelDensityReport)
        assert len(report.per_object) == 6

    def test_effective_tile_m_is_nominal_times_scale(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        for rec in report.per_object:
            expected = rec["nominal_tile_m"] * rec["tile_scale"]
            assert rec["effective_tile_m"] == expected

    def test_terrain_has_highest_effective_tile_m(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        terrain_tiles = [
            r["effective_tile_m"]
            for r in report.per_object
            if r["object_category"] == "terrain"
        ]
        assert all(t >= 9.0 for t in terrain_tiles)

    def test_wall_has_lower_effective_tile_m_than_terrain(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        terrain_max = max(
            r["effective_tile_m"]
            for r in report.per_object
            if r["object_category"] == "terrain"
        )
        wall_max = max(
            r["effective_tile_m"]
            for r in report.per_object
            if r["object_category"] == "long-wall"
        )
        assert terrain_max > wall_max

    def test_variation_ratio_is_extreme_for_default_tile_scales(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        # terrain 9.0 vs wall 1.6 => ratio 5.625
        assert report.variation_ratio > 5.0
        assert report.extreme_variation is True

    def test_variation_ratio_improves_when_terrain_scale_reduced(self):
        default_report = audit_texel_density(_make_materials(), _make_objects())
        objects = [
            ObjectTileRecord(
                object_id=o.object_id,
                object_category=o.object_category,
                material_id=o.material_id,
                tile_scale=1.0 if o.object_category == "terrain" else o.tile_scale,
            )
            for o in _make_objects()
        ]
        report = audit_texel_density(_make_materials(), objects)
        # Reducing terrain tile_scale from 3.0 to 1.0 lowers the ratio from 7.5
        # to 3.125 (max=5.0 shallow-water, min=1.6 dark-timber).  Variation
        # improves but remains > 3.0 because inherent material nominal_tile_m
        # range (1.6 .. 5.0) is itself wide — this is a real finding, not a
        # test bug.
        assert report.variation_ratio < default_report.variation_ratio
        assert report.overall_max_tile_m == 5.0  # shallow-water, no longer terrain

    def test_by_category_has_min_max_median(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        for _cat, data in report.by_category.items():
            assert "min" in data
            assert "max" in data
            assert "median" in data
            assert "material_count" in data
            assert data["min"] <= data["median"] <= data["max"]

    def test_overall_min_and_max_are_correct(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        # min = dark-timber 1.6 * 1.0 = 1.6
        # max = terrace-soil 4.0 * 3.0 = 12.0
        assert report.overall_min_tile_m == 1.6
        assert report.overall_max_tile_m == 12.0

    def test_empty_materials_fails_closed(self):
        with pytest.raises(ValueError, match="materials list is empty"):
            audit_texel_density([], _make_objects())

    def test_empty_objects_fails_closed(self):
        with pytest.raises(ValueError, match="objects list is empty"):
            audit_texel_density(_make_materials(), [])

    @pytest.mark.parametrize("bad_value", (0.0, -1.0, math.nan, math.inf))
    def test_invalid_nominal_tile_m_fails_closed(self, bad_value):
        with pytest.raises(ValidationError):
            MaterialTileRecord(
                material_id="bad",
                nominal_tile_m=bad_value,
                uv_policy="world-xy",
            )

    @pytest.mark.parametrize("bad_value", (0.0, -1.0, math.nan, math.inf))
    def test_invalid_tile_scale_fails_closed(self, bad_value):
        with pytest.raises(ValidationError):
            ObjectTileRecord(
                object_id="bad-object",
                object_category="terrain",
                material_id="material-packed-earth-01",
                tile_scale=bad_value,
            )

    def test_invalid_uv_policy_fails_closed(self):
        materials = [
            MaterialTileRecord(
                material_id="bad",
                nominal_tile_m=1.0,
                uv_policy="unknown",
            ),
        ]
        objects = [
            ObjectTileRecord(
                object_id="terrain-missing",
                object_category="terrain",
                material_id="bad",
                tile_scale=1.0,
            ),
        ]
        with pytest.raises(ValueError, match="unsupported uv_policy"):
            audit_texel_density(materials, objects)

    def test_missing_material_id_fails_closed(self):
        materials = _make_materials()
        objects = [
            ObjectTileRecord(
                object_id="terrain-nonexistent",
                object_category="terrain",
                material_id="nonexistent",
                tile_scale=1.0,
            ),
        ]
        with pytest.raises(ValueError, match="material_id not found"):
            audit_texel_density(materials, objects)

    def test_report_is_frozen(self):
        report = audit_texel_density(_make_materials(), _make_objects())
        with pytest.raises((TypeError, ValueError)):
            report.variation_ratio = 0.0

    def test_duplicate_material_ids_fail_closed(self):
        materials = _make_materials()
        materials.append(materials[0])

        with pytest.raises(ValueError, match="duplicate material"):
            audit_texel_density(materials, _make_objects())

    def test_duplicate_object_ids_fail_closed(self):
        objects = _make_objects()
        objects.append(objects[0])

        with pytest.raises(ValueError, match="duplicate object"):
            audit_texel_density(_make_materials(), objects)

    def test_missing_required_category_fails_closed(self):
        objects = [
            row for row in _make_objects() if row.object_category != "creek"
        ]

        with pytest.raises(ValueError, match="required categories.*creek"):
            audit_texel_density(_make_materials(), objects)

    def test_report_names_repeat_distance_not_texels(self):
        report = audit_texel_density(_make_materials(), _make_objects())

        assert report.measurement_unit == "repeat-distance-m"
        assert report.trust_effect == "none-quality-filter-only"
        assert all(
            "texel" not in key
            for record in report.per_object
            for key in record
        )


def _probe_measurements() -> list[dict]:
    return [
        {
            "object_id": "terrain-root",
            "object_name": "terrain",
            "category": "terrain",
            "material_ids": ["material-packed-earth-01"],
            "uv_area_total": 4.0,
            "mesh_area_total_m2": 2.0,
            "uv_area_per_m2": 2.0,
            "triangle_count": 2,
            "tile_scale": 1.0,
        },
        {
            "object_id": "creek-root",
            "object_name": "creek",
            "category": "creek",
            "material_ids": ["material-shallow-water-01"],
            "uv_area_total": 2.0,
            "mesh_area_total_m2": 2.0,
            "uv_area_per_m2": 1.0,
            "triangle_count": 2,
            "tile_scale": 1.0,
        },
        {
            "object_id": "long-wall-root",
            "object_name": "long-wall",
            "category": "long-wall",
            "material_ids": ["material-pale-plaster-01"],
            "uv_area_total": 1.0,
            "mesh_area_total_m2": 2.0,
            "uv_area_per_m2": 0.5,
            "triangle_count": 2,
            "tile_scale": 1.0,
        },
    ]


def test_blender_probe_measures_both_triangles_of_a_quad(
    uv_probe: ModuleType,
) -> None:
    triangles = (
        (
            ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 2.0, 0.0)),
            ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)),
        ),
        (
            ((0.0, 0.0, 0.0), (2.0, 2.0, 0.0), (0.0, 2.0, 0.0)),
            ((0.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
        ),
    )

    result = uv_probe._measure_triangles(triangles)

    assert result["triangle_count"] == 2
    assert result["mesh_area_total_m2"] == 4.0
    assert result["uv_area_total"] == 1.0
    assert result["uv_area_per_m2"] == 0.25
    assert "texels_per_meter_sq" not in result


def test_blender_probe_rejects_duplicate_objects_and_missing_categories(
    uv_probe: ModuleType,
) -> None:
    measurements = _probe_measurements()
    with pytest.raises(
        uv_probe.UvProbeError,
        match="duplicate object",
    ):
        uv_probe._summarize_measurements(
            [*measurements, measurements[0]]
        )
    with pytest.raises(
        uv_probe.UvProbeError,
        match="required categories.*creek",
    ):
        uv_probe._summarize_measurements(
            [row for row in measurements if row["category"] != "creek"]
        )


def test_blender_probe_allows_mesh_children_of_the_same_stable_root(
    uv_probe: ModuleType,
) -> None:
    measurements = _probe_measurements()
    measurements[0]["stable_root_id"] = "shared-root"
    measurements[1]["stable_root_id"] = "shared-root"

    summary = uv_probe._summarize_measurements(measurements)

    assert summary["by_category"]["terrain"]["object_count"] == 1
    assert summary["by_category"]["creek"]["object_count"] == 1


def test_blender_probe_binds_unique_object_and_shared_root_ids(
    uv_probe: ModuleType,
) -> None:
    obj = SimpleNamespace(
        name="mesh-child-001",
        get=lambda key, default=None: {
            "nv_stable_id": "shared-root",
        }.get(key, default),
    )

    assert uv_probe._measurement_object_identity(obj) == {
        "object_id": "mesh-child-001",
        "stable_root_id": "shared-root",
    }


@pytest.mark.parametrize("bad_value", (math.nan, math.inf, -math.inf))
def test_blender_probe_rejects_non_finite_measurements(
    uv_probe: ModuleType,
    bad_value: float,
) -> None:
    measurements = _probe_measurements()
    measurements[0]["uv_area_per_m2"] = bad_value

    with pytest.raises(uv_probe.UvProbeError, match="finite"):
        uv_probe._summarize_measurements(measurements)


def test_blender_probe_report_binds_runtime_and_source_bytes(
    uv_probe: ModuleType,
) -> None:
    identities = {
        "source_blend_sha256": "1" * 64,
        "build_report_sha256": "2" * 64,
        "probe_script_sha256": "3" * 64,
        "blender_executable_sha256": "4" * 64,
    }

    report = uv_probe._build_report(
        identities=identities,
        measurements=_probe_measurements(),
    )

    assert report["schema_version"] == (
        "nantai.synthetic-village.uv-repeat-density-probe.v1"
    )
    assert report["measurement_unit"] == "uv-area-per-m2"
    assert report["trust_effect"] == "none-quality-filter-only"
    assert all(report[key] == value for key, value in identities.items())
    assert report["content_sha256"] == hashlib.sha256(
        uv_probe._canonical_bytes(
            {
                key: value
                for key, value in report.items()
                if key != "content_sha256"
            }
        )
    ).hexdigest()
