from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.building_geometry import (
    BUILDING_GEOMETRY_V1,
    BUILDING_GEOMETRY_V2,
    BuildingGeometryEvidence,
    building_variant,
    expected_variant_counts,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan


def _building_ids() -> tuple[str, ...]:
    return tuple(
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    )


def _valid_evidence() -> dict[str, object]:
    return {
        "profile_id": BUILDING_GEOMETRY_V2,
        "building_count": 70,
        "covered_elevations": ("front", "left", "rear", "right"),
        "variant_counts": {
            "balanced-residence": 21,
            "rear-service-house": 20,
            "side-entry-workshop": 29,
        },
        "added_face_count": 1000,
        "maximum_added_faces_per_building": 20,
        "new_mesh_object_count": 0,
    }


def test_v2_variant_mapping_is_stable_for_all_canonical_buildings() -> None:
    building_ids = _building_ids()
    first = [
        building_variant(object_id, BUILDING_GEOMETRY_V2)
        for object_id in building_ids
    ]
    second = [
        building_variant(object_id, BUILDING_GEOMETRY_V2)
        for object_id in building_ids
    ]

    assert first == second
    assert Counter(first) == {
        "balanced-residence": 21,
        "side-entry-workshop": 29,
        "rear-service-house": 20,
    }
    assert expected_variant_counts(building_ids, BUILDING_GEOMETRY_V2) == {
        "balanced-residence": 21,
        "rear-service-house": 20,
        "side-entry-workshop": 29,
    }


def test_v1_has_no_building_variant_and_unknown_profile_fails_closed() -> None:
    assert building_variant("building-central-001", BUILDING_GEOMETRY_V1) is None
    assert expected_variant_counts(_building_ids(), BUILDING_GEOMETRY_V1) == {}

    with pytest.raises(ValueError, match="building geometry profile"):
        building_variant("building-central-001", "unknown-profile")  # type: ignore[arg-type]


def test_v2_evidence_accepts_only_exact_counts_elevations_and_budgets() -> None:
    evidence = BuildingGeometryEvidence.model_validate(_valid_evidence())

    assert evidence.profile_id == BUILDING_GEOMETRY_V2
    assert evidence.building_count == 70
    assert sum(evidence.variant_counts.values()) == 70

    mutations = (
        ("covered_elevations", ("front", "rear", "right")),
        ("added_face_count", 15401),
        ("maximum_added_faces_per_building", 221),
        ("new_mesh_object_count", 1),
        (
            "variant_counts",
            {
                "balanced-residence": 22,
                "rear-service-house": 19,
                "side-entry-workshop": 29,
            },
        ),
    )
    for key, value in mutations:
        mutated = _valid_evidence()
        mutated[key] = value
        with pytest.raises(ValidationError):
            BuildingGeometryEvidence.model_validate(mutated)
