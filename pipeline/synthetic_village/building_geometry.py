"""Stable identities and fail-closed evidence for synthetic building geometry."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from .contracts import FrozenModel

BUILDING_GEOMETRY_V1 = "front-facade-box-v1"
BUILDING_GEOMETRY_V2 = "four-sided-rural-building-v2"
BUILDING_ELEVATIONS = ("front", "left", "rear", "right")
BUILDING_VARIANTS = (
    "balanced-residence",
    "side-entry-workshop",
    "rear-service-house",
)

BuildingGeometryProfileId = Literal[
    "front-facade-box-v1",
    "four-sided-rural-building-v2",
]
BuildingVariantId = Literal[
    "balanced-residence",
    "side-entry-workshop",
    "rear-service-house",
]


def building_variant(
    object_id: str,
    profile_id: BuildingGeometryProfileId,
) -> BuildingVariantId | None:
    """Derive a cross-process variant without Python's randomized ``hash``."""

    if profile_id == BUILDING_GEOMETRY_V1:
        return None
    if profile_id != BUILDING_GEOMETRY_V2:
        raise ValueError(f"unknown building geometry profile: {profile_id!r}")
    digest = hashlib.sha256(
        f"{BUILDING_GEOMETRY_V2}\0{object_id}".encode(),
    ).digest()
    return BUILDING_VARIANTS[digest[0] % len(BUILDING_VARIANTS)]


def expected_variant_counts(
    object_ids: tuple[str, ...],
    profile_id: BuildingGeometryProfileId,
) -> dict[str, int]:
    """Return canonical sorted counts for the supplied stable building IDs."""

    if profile_id == BUILDING_GEOMETRY_V1:
        return {}
    counts = Counter(building_variant(object_id, profile_id) for object_id in object_ids)
    return dict(sorted(counts.items()))


class BuildingGeometryEvidence(FrozenModel):
    """Measured Blender polygon evidence for the approved four-sided profile."""

    profile_id: Literal["four-sided-rural-building-v2"]
    building_count: Literal[70]
    covered_elevations: tuple[
        Literal["front"],
        Literal["left"],
        Literal["rear"],
        Literal["right"],
    ]
    variant_counts: dict[BuildingVariantId, int]
    added_face_count: int = Field(ge=1, le=15_400)
    maximum_added_faces_per_building: int = Field(ge=1, le=220)
    new_mesh_object_count: Literal[0]

    @model_validator(mode="after")
    def _validate_exact_v2_counts(self) -> BuildingGeometryEvidence:
        if self.variant_counts != {
            "balanced-residence": 21,
            "rear-service-house": 20,
            "side-entry-workshop": 29,
        }:
            raise ValueError("building variant counts do not match stable IDs")
        return self
