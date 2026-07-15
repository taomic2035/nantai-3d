"""Strict public contracts for replaceable synthetic-village resources."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SlotCategory = Literal["key-view", "material", "detail", "environment", "prop"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SceneExtent(FrozenModel):
    width_m: float = Field(gt=0, allow_inf_nan=False)
    depth_m: float = Field(gt=0, allow_inf_nan=False)
    relief_m: float = Field(gt=0, allow_inf_nan=False)


class ElementBudget(FrozenModel):
    buildings_min: int = Field(ge=1)
    buildings_default: int = Field(ge=1)
    buildings_max: int = Field(ge=1)
    cluster_count: int = Field(ge=1)
    spatial_columns: int = Field(ge=1)
    spatial_rows: int = Field(ge=1)
    bridge_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_building_range(self) -> ElementBudget:
        if not self.buildings_min <= self.buildings_default <= self.buildings_max:
            raise ValueError("building default must be inside the declared range")
        return self


class CameraProfile(FrozenModel):
    profile_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    camera_count: int = Field(ge=1)
    width: int = Field(ge=256)
    height: int = Field(ge=256)
    train_count: int = Field(ge=1)
    validation_count: int = Field(ge=0)
    test_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_split_counts(self) -> CameraProfile:
        if self.train_count + self.validation_count + self.test_count != self.camera_count:
            raise ValueError("split counts must equal camera_count")
        return self


class VisualSlot(FrozenModel):
    slot_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category: SlotCategory
    intended_use: str = Field(min_length=20)
    prompt: str = Field(min_length=240)
    synthetic: Literal[True] = True
    canary_critical: bool
    replacement_contract: str = Field(min_length=40)

    @model_validator(mode="after")
    def _category_prefix_matches(self) -> VisualSlot:
        if not self.slot_id.startswith(f"{self.category}-"):
            raise ValueError("visual slot ID prefix must match its category")
        return self


class VisualSlotCatalog(FrozenModel):
    schema_version: Literal[1] = 1
    catalog_id: Literal["synthetic-mountain-village-visual-slots-v1"]
    synthetic: Literal[True] = True
    slots: tuple[VisualSlot, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_slots(self) -> VisualSlotCatalog:
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("visual slot IDs must be unique")
        if len(self.slots) != 68:
            raise ValueError("visual catalog must contain exactly 68 slots")
        expected_counts = {
            "key-view": 16,
            "material": 24,
            "detail": 12,
            "environment": 8,
            "prop": 8,
        }
        if Counter(slot.category for slot in self.slots) != expected_counts:
            raise ValueError("visual catalog category counts do not match the v1 contract")
        return self


class DefaultResourceRecipe(FrozenModel):
    schema_version: Literal[1] = 1
    recipe_id: Literal["synthetic-mountain-village-v1"]
    seed: int = Field(ge=0)
    synthetic: Literal[True] = True
    verification_level: Literal["L2"] = "L2"
    coordinate_system: Literal["right-handed-z-up-meters"]
    scene: SceneExtent
    elements: ElementBudget
    canary: CameraProfile
    full: CameraProfile
    visual_slots_path: str = Field(
        pattern=(
            r"^assets/default-resources/"
            r"synthetic-mountain-village-visual-slots-v1\.json$"
        ),
    )
    prohibited_claims: tuple[str, ...] = Field(min_length=1)
    replacement_contract: str = Field(min_length=40)
