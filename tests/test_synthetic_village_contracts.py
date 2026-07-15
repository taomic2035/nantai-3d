"""Tracked default-resource contracts for the synthetic mountain village."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.contracts import (
    CameraProfile,
    DefaultResourceRecipe,
    ElementBudget,
    SceneExtent,
    VisualSlot,
    VisualSlotCatalog,
)
from pipeline.synthetic_village.defaults import (
    DEFAULT_RECIPE_PATH,
    DEFAULT_VISUAL_SLOTS_PATH,
    MAX_DEFAULT_RESOURCE_BYTES,
    build_default_recipe,
    build_default_visual_slots,
    canonical_json_bytes,
    load_default_recipe,
    load_default_visual_slots,
)

ROOT = Path(__file__).parents[1]


def test_default_recipe_has_confirmed_scene_and_camera_budgets():
    recipe = load_default_recipe()

    assert recipe.seed == 20260715
    assert recipe.scene == SceneExtent(width_m=700, depth_m=500, relief_m=120)
    assert recipe.elements == ElementBudget(
        buildings_min=60,
        buildings_default=70,
        buildings_max=80,
        cluster_count=3,
        spatial_columns=4,
        spatial_rows=3,
        bridge_count=2,
    )
    assert recipe.canary == CameraProfile(
        profile_id="canary-24",
        camera_count=24,
        width=1024,
        height=576,
        train_count=18,
        validation_count=4,
        test_count=2,
    )
    assert recipe.full == CameraProfile(
        profile_id="full-180",
        camera_count=180,
        width=2048,
        height=1152,
        train_count=144,
        validation_count=24,
        test_count=12,
    )
    assert recipe.synthetic is True
    assert recipe.verification_level == "L2"
    assert recipe.visual_slots_path == (
        "assets/default-resources/"
        "synthetic-mountain-village-visual-slots-v1.json"
    )


def test_every_default_model_is_frozen_and_rejects_unknown_fields():
    recipe = load_default_recipe()
    models_and_fields = (
        (recipe.scene, "width_m", 1),
        (recipe.elements, "buildings_default", 1),
        (recipe.canary, "camera_count", 1),
        (load_default_visual_slots().slots[0], "prompt", "changed"),
        (recipe, "seed", 1),
    )

    for model, field_name, replacement in models_and_fields:
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field_name, replacement)
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            type(model).model_validate({
                **model.model_dump(mode="python"),
                "untracked_setting": True,
            })


def test_visual_catalog_has_exact_category_counts_and_unique_ids():
    catalog = load_default_visual_slots()

    assert catalog.schema_version == 1
    assert catalog.synthetic is True
    assert len(catalog.slots) == 68
    assert Counter(slot.category for slot in catalog.slots) == {
        "key-view": 16,
        "material": 24,
        "detail": 12,
        "environment": 8,
        "prop": 8,
    }
    assert len({slot.slot_id for slot in catalog.slots}) == 68


def test_visual_catalog_rejects_count_or_category_drift():
    catalog = load_default_visual_slots()
    payload = catalog.model_dump(mode="python")

    with pytest.raises(ValidationError, match="exactly 68"):
        VisualSlotCatalog.model_validate({**payload, "slots": payload["slots"][:-1]})

    wrong_categories = [*payload["slots"]]
    wrong_categories[0] = {
        **wrong_categories[0],
        "slot_id": "material-reclassified-key-view-01",
        "category": "material",
    }
    with pytest.raises(ValidationError, match="category counts"):
        VisualSlotCatalog.model_validate({**payload, "slots": tuple(wrong_categories)})


def test_visual_catalog_contains_required_canary_slots():
    slots = {slot.slot_id: slot for slot in load_default_visual_slots().slots}
    required = {
        "key-view-establishing-small-01",
        "key-view-establishing-expanded-01",
        "key-view-creekside-entrance-01",
        "key-view-central-courtyard-01",
        "key-view-upper-switchback-01",
        "key-view-opposite-slope-01",
        "material-rammed-earth-01",
        "material-pale-plaster-01",
        "material-gray-roof-tile-01",
        "material-fieldstone-01",
        "detail-timber-door-01",
        "environment-stone-bridge-01",
        "prop-water-jar-01",
    }

    assert required <= slots.keys()
    assert all(slots[slot_id].canary_critical for slot_id in required)


def test_every_visual_slot_is_standalone_replaceable_and_generic():
    for slot in load_default_visual_slots().slots:
        assert len(slot.intended_use) >= 20
        assert len(slot.prompt) >= 240
        assert "fictional" in slot.prompt.lower()
        assert "no real-world identity" in slot.prompt.lower()
        assert "same as" not in slot.prompt.lower()
        assert "above" not in slot.prompt.lower()
        assert "reference image" not in slot.prompt.lower()
        assert len(slot.replacement_contract) >= 40
        assert slot.synthetic is True


def test_contract_validators_reject_inconsistent_budgets():
    with pytest.raises(ValidationError, match="building default"):
        ElementBudget(
            buildings_min=60,
            buildings_default=81,
            buildings_max=80,
            cluster_count=3,
            spatial_columns=4,
            spatial_rows=3,
            bridge_count=2,
        )
    with pytest.raises(ValidationError, match="split counts"):
        CameraProfile(
            profile_id="bad-profile",
            camera_count=24,
            width=1024,
            height=576,
            train_count=18,
            validation_count=4,
            test_count=1,
        )
    with pytest.raises(ValidationError):
        VisualSlot(
            slot_id="NOT PORTABLE",
            category="key-view",
            intended_use="A deliberately invalid test visual slot.",
            prompt="fictional no real-world identity " * 20,
            synthetic=True,
            canary_critical=False,
            replacement_contract="Replace this invalid slot through a new source revision.",
        )


def test_contracts_reject_type_coercion_and_slot_category_mismatch():
    with pytest.raises(ValidationError):
        SceneExtent(width_m="700", depth_m=500, relief_m=120)
    with pytest.raises(ValidationError):
        DefaultResourceRecipe.model_validate({
            **load_default_recipe().model_dump(mode="python"),
            "seed": "20260715",
        })
    with pytest.raises(ValidationError, match="prefix"):
        VisualSlot(
            slot_id="prop-mismatched-01",
            category="key-view",
            intended_use="A mismatched category contract used for regression coverage.",
            prompt="fictional no real-world identity " * 20,
            synthetic=True,
            canary_critical=False,
            replacement_contract="Replace this invalid slot through a new source revision.",
        )


@pytest.mark.parametrize(
    ("loader", "builder"),
    (
        (load_default_recipe, build_default_recipe),
        (load_default_visual_slots, build_default_visual_slots),
    ),
)
def test_loaders_reject_duplicate_keys_and_noncanonical_json(
    tmp_path,
    loader,
    builder,
):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        loader(duplicate)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(
        json.dumps(builder().model_dump(mode="json")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="canonical JSON"):
        loader(noncanonical)


def test_loader_rejects_oversized_resource_before_unbounded_read(tmp_path, monkeypatch):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_DEFAULT_RESOURCE_BYTES + 1))
    original_read_bytes = Path.read_bytes

    def reject_unbounded_read(path):
        if path == oversized:
            raise AssertionError("loader attempted Path.read_bytes on an oversized resource")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_unbounded_read)
    with pytest.raises(ValueError, match="size is invalid"):
        load_default_recipe(oversized)


def test_tracked_default_resources_are_canonical_json_without_binary_payloads():
    recipe = load_default_recipe(DEFAULT_RECIPE_PATH)
    catalog = load_default_visual_slots(DEFAULT_VISUAL_SLOTS_PATH)

    assert DEFAULT_RECIPE_PATH.read_bytes() == canonical_json_bytes(recipe)
    assert DEFAULT_VISUAL_SLOTS_PATH.read_bytes() == canonical_json_bytes(catalog)
    assert DEFAULT_RECIPE_PATH.read_bytes() == canonical_json_bytes(build_default_recipe())
    assert DEFAULT_VISUAL_SLOTS_PATH.read_bytes() == canonical_json_bytes(
        build_default_visual_slots(),
    )
    files = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "assets/default-resources").rglob("*")
        if path.is_file()
    }
    assert files == {
        "assets/default-resources/synthetic-mountain-village-v1.json",
        (
            "assets/default-resources/"
            "synthetic-mountain-village-visual-slots-v1.json"
        ),
    }
