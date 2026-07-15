"""Build and load the tracked default synthetic-village resource files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .contracts import (
    CameraProfile,
    DefaultResourceRecipe,
    ElementBudget,
    SceneExtent,
    SlotCategory,
    VisualSlot,
    VisualSlotCatalog,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECIPE_PATH = (
    ROOT / "assets/default-resources/synthetic-mountain-village-v1.json"
)
DEFAULT_VISUAL_SLOTS_PATH = (
    ROOT
    / "assets/default-resources/synthetic-mountain-village-visual-slots-v1.json"
)
MAX_DEFAULT_RESOURCE_BYTES = 4 * 1024 * 1024
REPLACEMENT_CONTRACT = (
    "Replace this slot through a new content-addressed visual-source revision; "
    "consumers may depend only on the slot contract, never on exact pixels."
)

KEY_VIEWS = (
    ("key-view-establishing-small-01", "small hillside establishing view"),
    ("key-view-establishing-expanded-01", "expanded whole-village establishing view"),
    ("key-view-creekside-entrance-01", "ground-level creekside entrance and bridge route"),
    ("key-view-central-courtyard-01", "linked central stone courtyard from its south gate"),
    ("key-view-upper-switchback-01", "upper-cluster switchback path looking downhill"),
    ("key-view-opposite-slope-01", "reverse elevated overview from the northeast slope"),
    ("key-view-community-hall-01", "community hall square and three branching lanes"),
    ("key-view-orchard-terrace-01", "orchard terrace facing the central roofscape"),
    ("key-view-bamboo-lane-01", "narrow bamboo-edge lane between hillside homes"),
    ("key-view-irrigation-pond-01", "irrigation pond edge with terraces and homes beyond"),
    ("key-view-lower-bridge-01", "lower bridge side view connecting creekside homes"),
    ("key-view-upper-bridge-01", "upper stone bridge and converging footpaths"),
    ("key-view-south-ground-route-01", "southern ground route through farm plots"),
    ("key-view-east-ground-route-01", "eastern ground route beside retaining walls"),
    ("key-view-field-edge-01", "field-edge route with layered village facades"),
    ("key-view-roofline-crossing-01", "mid-slope roofline view across all three clusters"),
)

MATERIALS = (
    ("material-rammed-earth-01", "layered ochre rammed-earth wall"),
    ("material-pale-plaster-01", "aged pale lime-plaster wall"),
    ("material-gray-roof-tile-01", "weathered dark-gray clay roof tiles"),
    ("material-fieldstone-01", "irregular local fieldstone masonry"),
    ("material-dark-timber-01", "dark structural timber beams"),
    ("material-weathered-timber-01", "weathered timber boards and joinery"),
    ("material-wet-stone-paving-01", "slightly wet courtyard stone paving"),
    ("material-dry-stone-wall-01", "dry-laid terrace retaining wall"),
    ("material-clay-brick-01", "handmade muted clay brick infill"),
    ("material-moss-stone-01", "moss-flecked shaded stone"),
    ("material-packed-earth-01", "compacted earthen footpath"),
    ("material-terrace-soil-01", "cultivated dark terrace soil"),
    ("material-rice-paddy-water-01", "shallow rice-paddy water and mud"),
    ("material-vegetable-leaf-01", "mixed leafy vegetable crop surface"),
    ("material-bamboo-stem-01", "mature green bamboo stems"),
    ("material-bamboo-leaf-01", "dense bamboo foliage"),
    ("material-broadleaf-bark-01", "humid broadleaf tree bark"),
    ("material-broadleaf-canopy-01", "subtropical broadleaf canopy"),
    ("material-orchard-bark-01", "aged fruit-tree bark"),
    ("material-orchard-leaf-01", "fruit-tree leaves and small branches"),
    ("material-creek-rock-01", "rounded shallow-creek stones"),
    ("material-shallow-water-01", "clear shallow moving creek water"),
    ("material-aged-metal-01", "restrained aged farm-tool metal"),
    ("material-woven-bamboo-01", "tight woven-bamboo surface"),
)

DETAILS = (
    ("detail-timber-door-01", "traditional timber door and threshold assembly"),
    ("detail-timber-window-01", "simple timber lattice window assembly"),
    ("detail-tile-eave-01", "layered tile eave with timber support"),
    ("detail-roof-ridge-01", "weathered clay-tile roof ridge junction"),
    ("detail-stone-stair-01", "irregular exterior stone stair flight"),
    ("detail-drainage-channel-01", "courtyard drainage channel and outlet"),
    ("detail-retaining-corner-01", "fieldstone retaining-wall corner"),
    ("detail-timber-balcony-01", "modest timber balcony joinery"),
    ("detail-plaster-repair-01", "layered historic plaster repair patches"),
    ("detail-rammed-layer-01", "close rammed-earth lift layers"),
    ("detail-courtyard-joint-01", "stone courtyard paving joint network"),
    ("detail-bridge-parapet-01", "low stone bridge parapet and coping"),
)

ENVIRONMENTS = (
    ("environment-stone-bridge-01", "complete modest stone footbridge environment"),
    ("environment-creek-bend-01", "rocky creek bend with natural banks"),
    ("environment-irrigation-pond-01", "small irrigation pond and earthen edge"),
    ("environment-terrace-field-01", "stepped mixed-crop terrace field"),
    ("environment-orchard-slope-01", "fruit-tree orchard on a gentle slope"),
    ("environment-bamboo-grove-01", "dense bamboo grove edge and understory"),
    ("environment-forest-mountain-01", "rounded subtropical forest mountain"),
    ("environment-overcast-sky-01", "bright neutral overcast mountain sky"),
)

PROPS = (
    ("prop-water-jar-01", "plain glazed water storage jar"),
    ("prop-firewood-stack-01", "orderly split firewood stack"),
    ("prop-bamboo-basket-01", "hand-woven agricultural bamboo basket"),
    ("prop-wooden-bench-01", "simple weathered wooden work bench"),
    ("prop-farming-tools-01", "small grouping of manual farming tools"),
    ("prop-grain-rack-01", "restrained timber grain drying rack"),
    ("prop-stone-trough-01", "shallow carved stone water trough"),
    ("prop-handcart-01", "small traditional two-wheel handcart"),
)

CANARY_CRITICAL = frozenset({
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
})


def _slot_prompt(category: SlotCategory, subject: str) -> str:
    category_direction = {
        "key-view": (
            "Frame a deep-focus documentary photograph with realistic perspective, "
            "clear near, middle and far layers, and visible routes useful for multiview matching."
        ),
        "material": (
            "Frame an orthographic-looking square material study with even scale, sharp "
            "microtexture, diffuse illumination and enough uninterrupted surface for tiling."
        ),
        "detail": (
            "Frame a close documentary construction study that shows assembly, edge profiles, "
            "fastening logic, weathering and neighboring materials at believable scale."
        ),
        "environment": (
            "Frame a wide environmental study that shows terrain connection, vegetation edges, "
            "drainage, walkable scale and sufficient surrounding context for scene placement."
        ),
        "prop": (
            "Frame one complete rural object at human scale with all sides legible, neutral "
            "grounding, crisp edges and material wear suitable for later 3D interpretation."
        ),
    }[category]
    return (
        "Create a photorealistic natural asset for a fictional subtropical mountain village "
        "with no real-world identity. The required subject is: "
        f"{subject}. {category_direction} The village language uses weathered dark-gray clay "
        "roof tiles, pale lime plaster, ochre rammed earth, dark timber and irregular local "
        "fieldstone among humid terraces, bamboo, orchards and a shallow creek. Use soft bright "
        "overcast daylight just after light rain, neutral documentary color, deep focus and "
        "physically plausible scale. Keep the result generic and replaceable. Exclude people as "
        "the main subject, vehicles, modern resorts, highways, fantasy architecture, real "
        "landmarks, signs, readable text, logos, watermarks, collages and borders."
    )


def _make_slots(
    category: SlotCategory,
    definitions: Iterable[tuple[str, str]],
) -> list[VisualSlot]:
    return [
        VisualSlot(
            slot_id=slot_id,
            category=category,
            intended_use=(
                f"Replaceable {category} source for synthetic-village scene design: {subject}."
            ),
            prompt=_slot_prompt(category, subject),
            synthetic=True,
            canary_critical=slot_id in CANARY_CRITICAL,
            replacement_contract=REPLACEMENT_CONTRACT,
        )
        for slot_id, subject in definitions
    ]


def build_default_visual_slots() -> VisualSlotCatalog:
    slots = [
        *_make_slots("key-view", KEY_VIEWS),
        *_make_slots("material", MATERIALS),
        *_make_slots("detail", DETAILS),
        *_make_slots("environment", ENVIRONMENTS),
        *_make_slots("prop", PROPS),
    ]
    return VisualSlotCatalog(
        catalog_id="synthetic-mountain-village-visual-slots-v1",
        synthetic=True,
        slots=tuple(slots),
    )


def build_default_recipe() -> DefaultResourceRecipe:
    return DefaultResourceRecipe(
        recipe_id="synthetic-mountain-village-v1",
        seed=20260715,
        synthetic=True,
        verification_level="L2",
        coordinate_system="right-handed-z-up-meters",
        scene=SceneExtent(width_m=700, depth_m=500, relief_m=120),
        elements=ElementBudget(
            buildings_min=60,
            buildings_default=70,
            buildings_max=80,
            cluster_count=3,
            spatial_columns=4,
            spatial_rows=3,
            bridge_count=2,
        ),
        canary=CameraProfile(
            profile_id="canary-24",
            camera_count=24,
            width=1024,
            height=576,
            train_count=18,
            validation_count=4,
            test_count=2,
        ),
        full=CameraProfile(
            profile_id="full-180",
            camera_count=180,
            width=2048,
            height=1152,
            train_count=144,
            validation_count=24,
            test_count=12,
        ),
        visual_slots_path=(
            "assets/default-resources/"
            "synthetic-mountain-village-visual-slots-v1.json"
        ),
        prohibited_claims=(
            "measured real-world geometry",
            "authoritative structure-from-motion evidence",
            "real-scene fidelity",
            "3D Gaussian Splatting quality",
        ),
        replacement_contract=(
            "Replace the complete recipe through an immutable resource revision; consumers "
            "must not depend on a real village identity, exact pixels, or generated file count."
        ),
    )


def canonical_json_bytes(model: DefaultResourceRecipe | VisualSlotCatalog) -> bytes:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (payload + "\n").encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> bytes:
    expected_size = path.stat().st_size
    if expected_size <= 0 or expected_size > MAX_DEFAULT_RESOURCE_BYTES:
        raise ValueError(f"default resource size is invalid: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_DEFAULT_RESOURCE_BYTES + 1)
    if len(raw) != expected_size or len(raw) > MAX_DEFAULT_RESOURCE_BYTES:
        raise ValueError(f"default resource changed during bounded read: {path.name}")
    json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )
    return raw


def load_default_recipe(path: Path = DEFAULT_RECIPE_PATH) -> DefaultResourceRecipe:
    raw = _read_json(path)
    recipe = DefaultResourceRecipe.model_validate_json(raw)
    if raw != canonical_json_bytes(recipe):
        raise ValueError(f"default resource is not canonical JSON: {path.name}")
    return recipe


def load_default_visual_slots(
    path: Path = DEFAULT_VISUAL_SLOTS_PATH,
) -> VisualSlotCatalog:
    raw = _read_json(path)
    catalog = VisualSlotCatalog.model_validate_json(raw)
    if raw != canonical_json_bytes(catalog):
        raise ValueError(f"default resource is not canonical JSON: {path.name}")
    return catalog


def write_default_resources() -> None:
    DEFAULT_RECIPE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_RECIPE_PATH.write_bytes(canonical_json_bytes(build_default_recipe()))
    DEFAULT_VISUAL_SLOTS_PATH.write_bytes(
        canonical_json_bytes(build_default_visual_slots()),
    )


if __name__ == "__main__":
    write_default_resources()
