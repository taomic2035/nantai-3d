"""Additive Batch24 perimeter-closure plan for the exact-266 scene.

The plan owns only canonical instances 219..266.  It records the sixteen
Batch24 PNGs as ``design-only`` provenance and never derives geometry, camera
calibration, scale, or coverage from their pixels.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

PERIMETER_CLOSURE_SCHEMA = (
    "nantai.synthetic-village.perimeter-closure-module.v1"
)
PERIMETER_CLOSURE_RECIPE_VERSION = "v1"
BATCH24_BATCH_ID = "synthetic-village-design-inputs-batch24-2026-07-23"

ModuleId = Literal[
    "closure-upstream",
    "closure-northeast",
    "closure-east",
    "closure-southeast",
    "closure-downstream",
    "closure-southwest",
    "closure-west",
    "closure-northwest",
]
SectorId = Literal[
    "upstream",
    "northeast",
    "east",
    "southeast",
    "downstream",
    "southwest",
    "west",
    "northwest",
]
SemanticRole = Literal[
    "terrain-contact",
    "bidirectional-corridor",
    "support-retaining",
    "drainage-water",
    "boundary-seam",
    "vegetation-enclosure",
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
SourceFile = Annotated[
    str,
    StringConstraints(pattern=r"^(?:reciprocal|section)-[a-z0-9-]+-01\.png$"),
]
Vec3 = tuple[float, float, float]

PERIMETER_CLOSURE_MODULE_ORDER: tuple[ModuleId, ...] = (
    "closure-upstream",
    "closure-northeast",
    "closure-east",
    "closure-southeast",
    "closure-downstream",
    "closure-southwest",
    "closure-west",
    "closure-northwest",
)
PERIMETER_CLOSURE_ROLE_ORDER: tuple[SemanticRole, ...] = (
    "terrain-contact",
    "bidirectional-corridor",
    "support-retaining",
    "drainage-water",
    "boundary-seam",
    "vegetation-enclosure",
)
PERIMETER_CLOSURE_INSTANCE_RANGES: Mapping[ModuleId, range] = {
    module_id: range(219 + index * 6, 225 + index * 6)
    for index, module_id in enumerate(PERIMETER_CLOSURE_MODULE_ORDER)
}

_MODULE_SECTORS: Mapping[ModuleId, SectorId] = {
    "closure-upstream": "upstream",
    "closure-northeast": "northeast",
    "closure-east": "east",
    "closure-southeast": "southeast",
    "closure-downstream": "downstream",
    "closure-southwest": "southwest",
    "closure-west": "west",
    "closure-northwest": "northwest",
}

# Explicit scene coordinates.  These are modeling inputs, not estimates
# recovered from the Batch24 pixels.
_MODULE_ANCHORS_XY: Mapping[
    ModuleId,
    tuple[tuple[float, float], tuple[float, float]],
] = {
    "closure-upstream": ((225.0, 137.0), (335.0, 235.0)),
    "closure-northeast": ((125.0, 155.0), (205.0, 232.0)),
    "closure-east": ((230.0, 55.0), (335.0, 70.0)),
    "closure-southeast": ((180.0, -110.0), (285.0, -185.0)),
    "closure-downstream": ((-230.0, -154.0), (-340.0, -212.0)),
    "closure-southwest": ((-135.0, -165.0), (-220.0, -235.0)),
    "closure-west": ((-230.0, -55.0), (-335.0, -65.0)),
    "closure-northwest": ((-165.0, 115.0), (-270.0, 190.0)),
}

# filename, lowercase SHA-256.  Both records in each sector are provenance
# bindings only; no pixel metadata participates in placement.
_BATCH24_SOURCES: Mapping[
    SectorId,
    Mapping[Literal["reciprocal-perimeter", "section-closure"], tuple[str, str]],
] = {
    "upstream": {
        "reciprocal-perimeter": (
            "reciprocal-upstream-creek-valley-inbound-01.png",
            "8ff37aa89b68cb3c6fa63d2ae27caa938c45d251badfab248b324a4039d06526",
        ),
        "section-closure": (
            "section-upstream-flume-creek-support-01.png",
            "b4a6dcd299d35286605097da4e2b5958177cc4c143bc84b3554d365fe512618a",
        ),
    },
    "northeast": {
        "reciprocal-perimeter": (
            "reciprocal-northeast-forest-terrace-inbound-01.png",
            "6f056c7f5bbbcefb8b8af6b0e5980656beaf281a162c6f3e4057c9d99f35753d",
        ),
        "section-closure": (
            "section-northeast-terrace-drainage-01.png",
            "904c1f177553368ddd46bab097129555a3e64ceba26b2e9833b945481bc75980",
        ),
    },
    "east": {
        "reciprocal-perimeter": (
            "reciprocal-east-orchard-route-inbound-01.png",
            "39bc303359bf1f1c4028c1dba42619dcb7b22ac21945fd8e8d0d4c5eded91a38",
        ),
        "section-closure": (
            "section-east-orchard-route-cutfill-01.png",
            "4b751defe9f54e82ffa1d3fffc8ef8bf8c4b93f6e84dbd571ea90fbe98797395",
        ),
    },
    "southeast": {
        "reciprocal-perimeter": (
            "reciprocal-southeast-service-edge-inbound-01.png",
            "9ed97dd7d1cc61b4817e021b79b8dd27580818db333d67740a923564d6de1b59",
        ),
        "section-closure": (
            "section-southeast-service-yard-drainage-01.png",
            "4b70625e0e1250749756ef9344d01af32d4fa8ce2138e51890e4d0d55d00ad45",
        ),
    },
    "downstream": {
        "reciprocal-perimeter": (
            "reciprocal-downstream-creek-basin-inbound-01.png",
            "1099282dd6d8a4ffad94b61c989e0a7fd1bab229be916d0565c203d8712a7e9b",
        ),
        "section-closure": (
            "section-downstream-tailwater-floodbench-01.png",
            "961a01195a750190433d843cb956a7a2ed33e6a3e1b9fffd4221abeb114c0623",
        ),
    },
    "southwest": {
        "reciprocal-perimeter": (
            "reciprocal-southwest-stone-bank-inbound-01.png",
            "b4a7dbe7cac6bffe6fa90ed817cee69e0e661d00914216d083adecdeb3412c44",
        ),
        "section-closure": (
            "section-southwest-bridge-bank-foundation-01.png",
            "8c38577a47c174c8a358135651dc537946cf82e20e46868a5524090d17ecca35",
        ),
    },
    "west": {
        "reciprocal-perimeter": (
            "reciprocal-west-uphill-forest-inbound-01.png",
            "c4dd5f94fd10723a6ae6b1decde9992bd498d73997237bd326367782db4b5a77",
        ),
        "section-closure": (
            "section-west-forest-loop-retaining-01.png",
            "d28577a334db61abb3c4ab5076a062a1dceb7698f1a960b6d8069112acced6bd",
        ),
    },
    "northwest": {
        "reciprocal-perimeter": (
            "reciprocal-northwest-flume-ridge-inbound-01.png",
            "622a1264f7432cf29523a699bf9bc5b24031e25f6d4c8846c7ffca27fc392a18",
        ),
        "section-closure": (
            "section-northwest-flume-ridge-support-01.png",
            "87e615ad0108668f1b71274d7357e5c7c55cddc1faa243a1416f49c87221e5c5",
        ),
    },
}

_ROLE_GEOMETRY: Mapping[SemanticRole, str] = {
    "terrain-contact": "terrain-bench",
    "bidirectional-corridor": "walking-corridor",
    "support-retaining": "retaining-support",
    "drainage-water": "open-drainage",
    "boundary-seam": "sector-seam",
    "vegetation-enclosure": "vegetation-cluster",
}
_ROLE_MATERIALS: Mapping[SemanticRole, str] = {
    "terrain-contact": "material-stone-block-01",
    "bidirectional-corridor": "material-courtyard-stone-01",
    "support-retaining": "material-creek-stone-01",
    "drainage-water": "material-water-01",
    "boundary-seam": "material-service-iron-01",
    "vegetation-enclosure": "material-courtyard-timber-01",
}

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class PerimeterClosureError(ValueError):
    """The perimeter-closure plan or its provenance cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _finite_vec3(value: Vec3, *, field_name: str) -> None:
    if not all(math.isfinite(component) for component in value):
        raise ValueError(f"{field_name} must contain only finite coordinates")


class PerimeterClosurePart(FrozenModel):
    instance_id: int = Field(ge=219, le=266)
    module_id: ModuleId
    part_id: Slug
    semantic_role: SemanticRole
    geometry_family: Slug
    material_slot_id: Slug
    center_m: Vec3
    extent_m: Vec3
    orientation_deg: float
    inner_anchor_m: Vec3
    outer_anchor_m: Vec3
    previous_seam_m: Vec3
    next_seam_m: Vec3

    @model_validator(mode="after")
    def _geometry_is_finite_and_non_degenerate(self) -> PerimeterClosurePart:
        for field_name in (
            "center_m",
            "extent_m",
            "inner_anchor_m",
            "outer_anchor_m",
            "previous_seam_m",
            "next_seam_m",
        ):
            _finite_vec3(getattr(self, field_name), field_name=field_name)
        if not math.isfinite(self.orientation_deg):
            raise ValueError("orientation_deg must be finite")
        if any(component <= 0.0 for component in self.extent_m):
            raise ValueError("extent_m components must be positive")
        if self.inner_anchor_m == self.outer_anchor_m:
            raise ValueError("inner and outer anchors must differ")
        if self.previous_seam_m == self.next_seam_m:
            raise ValueError("previous and next seam anchors must differ")
        return self


class PerimeterClosureModule(FrozenModel):
    module_id: ModuleId
    sector: SectorId
    reciprocal_source_file: SourceFile
    reciprocal_source_sha256: Sha256
    section_source_file: SourceFile
    section_source_sha256: Sha256
    inner_anchor_m: Vec3
    outer_anchor_m: Vec3
    previous_seam_m: Vec3
    next_seam_m: Vec3
    parts: tuple[PerimeterClosurePart, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _module_is_exact(self) -> PerimeterClosureModule:
        if self.sector != _MODULE_SECTORS[self.module_id]:
            raise ValueError("module sector disagrees with canonical module id")
        expected_sources = _BATCH24_SOURCES[self.sector]
        if (
            self.reciprocal_source_file,
            self.reciprocal_source_sha256,
        ) != expected_sources["reciprocal-perimeter"]:
            raise ValueError("reciprocal source binding disagrees with Batch24")
        if (
            self.section_source_file,
            self.section_source_sha256,
        ) != expected_sources["section-closure"]:
            raise ValueError("section source binding disagrees with Batch24")
        if self.inner_anchor_m == self.outer_anchor_m:
            raise ValueError("module route anchors must differ")
        if self.previous_seam_m == self.next_seam_m:
            raise ValueError("module seam anchors must differ")
        expected_instances = tuple(PERIMETER_CLOSURE_INSTANCE_RANGES[self.module_id])
        if tuple(part.instance_id for part in self.parts) != expected_instances:
            raise ValueError("module instances disagree with canonical segment")
        if tuple(part.semantic_role for part in self.parts) != (
            PERIMETER_CLOSURE_ROLE_ORDER
        ):
            raise ValueError("module parts disagree with canonical role order")
        for part in self.parts:
            if part.module_id != self.module_id:
                raise ValueError("part module id disagrees with wrapper")
            if part.inner_anchor_m != self.inner_anchor_m:
                raise ValueError("part inner anchor disagrees with module")
            if part.outer_anchor_m != self.outer_anchor_m:
                raise ValueError("part outer anchor disagrees with module")
            if part.previous_seam_m != self.previous_seam_m:
                raise ValueError("part previous seam disagrees with module")
            if part.next_seam_m != self.next_seam_m:
                raise ValueError("part next seam disagrees with module")
        return self


class PerimeterClosureSummary(FrozenModel):
    module_count: Literal[8] = 8
    part_count: Literal[48] = 48
    instance_id_segment_start: Literal[219] = 219
    instance_id_segment_end: Literal[266] = 266


class PerimeterClosurePlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.perimeter-closure-module.v1"
    ] = PERIMETER_CLOSURE_SCHEMA
    plan_id: Literal["synthetic-village-perimeter-closure-v1"] = (
        "synthetic-village-perimeter-closure-v1"
    )
    recipe_version: Literal["v1"] = PERIMETER_CLOSURE_RECIPE_VERSION
    batch24_batch_id: Literal[
        "synthetic-village-design-inputs-batch24-2026-07-23"
    ] = BATCH24_BATCH_ID
    batch24_manifest_sha256: Sha256
    production_plan_sha256: Sha256
    topology_plan_sha256: Sha256
    synthetic: Literal[True] = True
    geometry_usability: Literal["preview-only"] = "preview-only"
    verification_level: Literal["L0"] = "L0"
    metric_alignment: Literal[False] = False
    real_photo_textures: Literal[False] = False
    geometry_trust: Literal["modeled-unverified"] = "modeled-unverified"
    training_use: Literal["forbidden-as-multiview"] = "forbidden-as-multiview"
    coverage_use: Literal["forbidden"] = "forbidden"
    trust_effect: Literal["none"] = "none"
    modules: tuple[PerimeterClosureModule, ...] = Field(
        min_length=8,
        max_length=8,
    )
    summary: PerimeterClosureSummary

    @model_validator(mode="after")
    def _plan_is_exact(self) -> PerimeterClosurePlan:
        if tuple(module.module_id for module in self.modules) != (
            PERIMETER_CLOSURE_MODULE_ORDER
        ):
            raise ValueError("closure modules must be exact and ordered")
        instances = tuple(
            part.instance_id for module in self.modules for part in module.parts
        )
        if instances != tuple(range(219, 267)):
            raise ValueError("closure parts must occupy ordered instances 219..266")
        part_ids = tuple(
            part.part_id for module in self.modules for part in module.parts
        )
        if len(set(part_ids)) != 48:
            raise ValueError("closure part ids must be globally unique")
        source_files = tuple(
            source
            for module in self.modules
            for source in (
                module.reciprocal_source_file,
                module.section_source_file,
            )
        )
        source_hashes = tuple(
            source
            for module in self.modules
            for source in (
                module.reciprocal_source_sha256,
                module.section_source_sha256,
            )
        )
        if len(set(source_files)) != 16 or len(set(source_hashes)) != 16:
            raise ValueError("all sixteen Batch24 sources must bind exactly once")
        for index, module in enumerate(self.modules):
            following = self.modules[(index + 1) % len(self.modules)]
            if module.next_seam_m != following.previous_seam_m:
                raise ValueError("neighbor sector seam anchors must agree")
        return self


def canonical_perimeter_closure_plan_bytes(
    plan: PerimeterClosurePlan,
) -> bytes:
    return _canonical(plan.model_dump(mode="json"))


def perimeter_closure_plan_sha256(plan: PerimeterClosurePlan) -> str:
    return hashlib.sha256(canonical_perimeter_closure_plan_bytes(plan)).hexdigest()


def _manifest_asset_index(
    batch24_manifest: Mapping[str, object],
) -> dict[str, tuple[str, str, str]]:
    if batch24_manifest.get("batch_id") != BATCH24_BATCH_ID:
        raise PerimeterClosureError("Batch24 manifest batch_id is not canonical")
    if batch24_manifest.get("asset_count") != 16:
        raise PerimeterClosureError("Batch24 manifest must declare exactly 16 assets")
    if batch24_manifest.get("prompt_count") != 16:
        raise PerimeterClosureError("Batch24 manifest must declare exactly 16 prompts")
    expected_trust: dict[str, object] = {
        "synthetic": True,
        "stage": "design-only",
        "camera_calibration": "unknown",
        "geometry_consistency": "not-verified",
        "metric_scale": "unknown",
        "real_photo_texture": False,
        "training_use": "forbidden-as-multiview",
        "coverage_use": "forbidden",
        "trust_effect": "none",
    }
    trust = batch24_manifest.get("trust")
    if not isinstance(trust, Mapping) or any(
        trust.get(key) != value for key, value in expected_trust.items()
    ):
        raise PerimeterClosureError("Batch24 manifest trust boundary disagrees")
    assets = batch24_manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 16:
        raise PerimeterClosureError("Batch24 manifest assets must contain 16 rows")
    index: dict[str, tuple[str, str, str]] = {}
    for raw_asset in assets:
        if not isinstance(raw_asset, Mapping):
            raise PerimeterClosureError("Batch24 asset row must be an object")
        file_name = raw_asset.get("file")
        kind = raw_asset.get("kind")
        sector = raw_asset.get("sector")
        sha256 = raw_asset.get("sha256")
        if not all(isinstance(value, str) for value in (file_name, kind, sector, sha256)):
            raise PerimeterClosureError("Batch24 asset identity fields must be strings")
        assert isinstance(file_name, str)
        assert isinstance(kind, str)
        assert isinstance(sector, str)
        assert isinstance(sha256, str)
        if not _SHA_RE.fullmatch(sha256):
            raise PerimeterClosureError(
                f"Batch24 asset {file_name!r} has malformed SHA-256"
            )
        if file_name in index:
            raise PerimeterClosureError(
                f"Batch24 asset filename {file_name!r} is duplicated"
            )
        index[file_name] = (kind, sector, sha256)

    expected_files = {
        file_name
        for sources in _BATCH24_SOURCES.values()
        for file_name, _sha256 in sources.values()
    }
    if set(index) != expected_files:
        raise PerimeterClosureError(
            "Batch24 manifest asset filenames disagree with accepted sources"
        )
    for sector, sources in _BATCH24_SOURCES.items():
        for kind, (file_name, sha256) in sources.items():
            if index[file_name] != (kind, sector, sha256):
                raise PerimeterClosureError(
                    f"Batch24 source binding disagrees for {file_name}"
                )
    return index


def verify_perimeter_closure_plan(
    plan: PerimeterClosurePlan,
    *,
    batch24_manifest: Mapping[str, object],
) -> None:
    """Rebind every Batch24 source and re-run canonical model validation."""

    manifest_index = _manifest_asset_index(batch24_manifest)
    for module in plan.modules:
        if manifest_index[module.reciprocal_source_file] != (
            "reciprocal-perimeter",
            module.sector,
            module.reciprocal_source_sha256,
        ):
            raise PerimeterClosureError(
                f"reciprocal source disagrees for {module.module_id}"
            )
        if manifest_index[module.section_source_file] != (
            "section-closure",
            module.sector,
            module.section_source_sha256,
        ):
            raise PerimeterClosureError(
                f"section source disagrees for {module.module_id}"
            )
    try:
        revalidated = PerimeterClosurePlan.model_validate_json(
            canonical_perimeter_closure_plan_bytes(plan)
        )
    except ValidationError as exc:
        raise PerimeterClosureError(
            "perimeter-closure plan failed canonical validation"
        ) from exc
    if revalidated != plan:
        raise PerimeterClosureError("perimeter-closure plan is not canonical JSON")


def _sample(
    terrain_height_at: Callable[[float, float], float],
    xy: tuple[float, float],
) -> Vec3:
    x_m, y_m = xy
    z_m = float(terrain_height_at(x_m, y_m))
    if not all(math.isfinite(value) for value in (x_m, y_m, z_m)):
        raise PerimeterClosureError("terrain sampler returned a non-finite coordinate")
    return (x_m, y_m, round(z_m, 3))


def _midpoint(
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _build_part(
    *,
    instance_id: int,
    module_id: ModuleId,
    role: SemanticRole,
    inner_xy: tuple[float, float],
    outer_xy: tuple[float, float],
    previous_seam_xy: tuple[float, float],
    next_seam_xy: tuple[float, float],
    terrain_height_at: Callable[[float, float], float],
) -> PerimeterClosurePart:
    dx = outer_xy[0] - inner_xy[0]
    dy = outer_xy[1] - inner_xy[1]
    length_m = math.hypot(dx, dy)
    ux, uy = dx / length_m, dy / length_m
    px, py = -uy, ux
    midpoint_xy = _midpoint(inner_xy, outer_xy)
    role_offset = {
        "terrain-contact": (0.0, 0.0),
        "bidirectional-corridor": (0.0, 0.0),
        "support-retaining": (px * 4.0, py * 4.0),
        "drainage-water": (-px * 4.0, -py * 4.0),
        "boundary-seam": (
            outer_xy[0] - midpoint_xy[0],
            outer_xy[1] - midpoint_xy[1],
        ),
        "vegetation-enclosure": (ux * (length_m / 2.0 + 12.0), uy * (length_m / 2.0 + 12.0)),
    }[role]
    center_xy = (
        midpoint_xy[0] + role_offset[0],
        midpoint_xy[1] + role_offset[1],
    )
    seam_span_m = math.dist(previous_seam_xy, next_seam_xy)
    extent_m = {
        "terrain-contact": (length_m + 12.0, 20.0, 3.0),
        "bidirectional-corridor": (length_m, 4.0, 0.6),
        "support-retaining": (max(length_m * 0.65, 10.0), 3.0, 5.0),
        "drainage-water": (length_m, 2.0, 0.5),
        "boundary-seam": (max(seam_span_m, 8.0), 6.0, 3.0),
        "vegetation-enclosure": (24.0, 18.0, 16.0),
    }[role]
    return PerimeterClosurePart(
        instance_id=instance_id,
        module_id=module_id,
        part_id=f"{module_id}-{role}",
        semantic_role=role,
        geometry_family=_ROLE_GEOMETRY[role],
        material_slot_id=_ROLE_MATERIALS[role],
        center_m=_sample(terrain_height_at, center_xy),
        extent_m=tuple(round(value, 3) for value in extent_m),
        orientation_deg=round(math.degrees(math.atan2(dy, dx)), 6),
        inner_anchor_m=_sample(terrain_height_at, inner_xy),
        outer_anchor_m=_sample(terrain_height_at, outer_xy),
        previous_seam_m=_sample(terrain_height_at, previous_seam_xy),
        next_seam_m=_sample(terrain_height_at, next_seam_xy),
    )


def build_default_perimeter_closure_plan(
    *,
    batch24_manifest: Mapping[str, object],
    batch24_manifest_sha256: str,
    production_plan_sha256: str,
    topology_plan_sha256: str,
    terrain_height_at: Callable[[float, float], float],
) -> PerimeterClosurePlan:
    """Build the literal 8-sector/48-root plan from explicit coordinates."""

    _manifest_asset_index(batch24_manifest)
    outer_anchors = tuple(
        _MODULE_ANCHORS_XY[module_id][1]
        for module_id in PERIMETER_CLOSURE_MODULE_ORDER
    )
    modules: list[PerimeterClosureModule] = []
    for index, module_id in enumerate(PERIMETER_CLOSURE_MODULE_ORDER):
        sector = _MODULE_SECTORS[module_id]
        inner_xy, outer_xy = _MODULE_ANCHORS_XY[module_id]
        previous_seam_xy = _midpoint(outer_anchors[index - 1], outer_xy)
        next_seam_xy = _midpoint(
            outer_xy,
            outer_anchors[(index + 1) % len(outer_anchors)],
        )
        reciprocal_source = _BATCH24_SOURCES[sector]["reciprocal-perimeter"]
        section_source = _BATCH24_SOURCES[sector]["section-closure"]
        parts = tuple(
            _build_part(
                instance_id=instance_id,
                module_id=module_id,
                role=role,
                inner_xy=inner_xy,
                outer_xy=outer_xy,
                previous_seam_xy=previous_seam_xy,
                next_seam_xy=next_seam_xy,
                terrain_height_at=terrain_height_at,
            )
            for instance_id, role in zip(
                PERIMETER_CLOSURE_INSTANCE_RANGES[module_id],
                PERIMETER_CLOSURE_ROLE_ORDER,
                strict=True,
            )
        )
        modules.append(
            PerimeterClosureModule(
                module_id=module_id,
                sector=sector,
                reciprocal_source_file=reciprocal_source[0],
                reciprocal_source_sha256=reciprocal_source[1],
                section_source_file=section_source[0],
                section_source_sha256=section_source[1],
                inner_anchor_m=_sample(terrain_height_at, inner_xy),
                outer_anchor_m=_sample(terrain_height_at, outer_xy),
                previous_seam_m=_sample(terrain_height_at, previous_seam_xy),
                next_seam_m=_sample(terrain_height_at, next_seam_xy),
                parts=parts,
            )
        )
    try:
        plan = PerimeterClosurePlan(
            batch24_manifest_sha256=batch24_manifest_sha256,
            production_plan_sha256=production_plan_sha256,
            topology_plan_sha256=topology_plan_sha256,
            modules=tuple(modules),
            summary=PerimeterClosureSummary(),
        )
    except ValidationError as exc:
        raise PerimeterClosureError(
            "default perimeter-closure plan failed validation"
        ) from exc
    verify_perimeter_closure_plan(plan, batch24_manifest=batch24_manifest)
    return plan


__all__ = [
    "BATCH24_BATCH_ID",
    "PERIMETER_CLOSURE_INSTANCE_RANGES",
    "PERIMETER_CLOSURE_MODULE_ORDER",
    "PERIMETER_CLOSURE_RECIPE_VERSION",
    "PERIMETER_CLOSURE_ROLE_ORDER",
    "PERIMETER_CLOSURE_SCHEMA",
    "PerimeterClosureError",
    "PerimeterClosureModule",
    "PerimeterClosurePart",
    "PerimeterClosurePlan",
    "PerimeterClosureSummary",
    "build_default_perimeter_closure_plan",
    "canonical_perimeter_closure_plan_bytes",
    "perimeter_closure_plan_sha256",
    "verify_perimeter_closure_plan",
]
