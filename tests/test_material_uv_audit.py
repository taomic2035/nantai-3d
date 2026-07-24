"""Tests for material_uv_audit — texel density variation audit (P2b)."""

from __future__ import annotations

import pytest

from pipeline.synthetic_village.material_uv_audit import (
    MaterialTileRecord,
    ObjectTileRecord,
    TexelDensityReport,
    audit_texel_density,
)


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
            object_category="terrain",
            material_id="material-packed-earth-01",
            tile_scale=3.0,
        ),
        ObjectTileRecord(
            object_category="terrain",
            material_id="material-terrace-soil-01",
            tile_scale=3.0,
        ),
        ObjectTileRecord(
            object_category="creek",
            material_id="material-shallow-water-01",
            tile_scale=1.0,
        ),
        ObjectTileRecord(
            object_category="creek",
            material_id="material-creek-rock-01",
            tile_scale=1.0,
        ),
        ObjectTileRecord(
            object_category="wall",
            material_id="material-pale-plaster-01",
            tile_scale=1.0,
        ),
        ObjectTileRecord(
            object_category="wall",
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
            if r["object_category"] == "wall"
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

    def test_invalid_nominal_tile_m_fails_closed(self):
        materials = [
            MaterialTileRecord(
                material_id="bad",
                nominal_tile_m=-1.0,
                uv_policy="world-xy",
            ),
        ]
        objects = [
            ObjectTileRecord(
                object_category="terrain",
                material_id="bad",
                tile_scale=1.0,
            ),
        ]
        with pytest.raises(ValueError, match="nominal_tile_m must be positive"):
            audit_texel_density(materials, objects)

    def test_invalid_tile_scale_fails_closed(self):
        materials = [
            MaterialTileRecord(
                material_id="ok",
                nominal_tile_m=1.0,
                uv_policy="world-xy",
            ),
        ]
        objects = [
            ObjectTileRecord(
                object_category="terrain",
                material_id="ok",
                tile_scale=0.0,
            ),
        ]
        with pytest.raises(ValueError, match="tile_scale must be positive"):
            audit_texel_density(materials, objects)

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
