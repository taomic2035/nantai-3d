"""Source-bound deterministic multiscale surface contracts."""

from __future__ import annotations

import hashlib
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from PIL import Image
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .material_bundle import DerivedMaterialRecord, load_material_bundle
from .scene_plan import DEFAULT_SEED, SceneObject, ScenePlan

LEGACY_SURFACE_PROFILE_ID = "single-scale-derived-pbr-v0"
SURFACE_PROFILE_V1 = "source-consistent-multiscale-surface-v1"
SURFACE_ALGORITHM_V1 = "source-palette-world-macro-path-detail-v1"
ACTIVE_MACRO_SLOTS = (
    "material-moss-stone-01",
    "material-packed-earth-01",
    "material-terrace-soil-01",
    "material-wet-stone-paving-01",
)
TERRAIN_SPACING_M = 4.0
TERRAIN_PERIOD_M = 20.0
GROUND_PERIOD_M = 10.0
PATH_STEP_M = 1.0
PATH_LATERAL_RAILS = 6
MAX_DETAIL_COUNTS = {
    "stone-fragment": 128,
    "leaf-card": 384,
    "damp-patch": 72,
    "rut-run": 96,
}
RUNTIME_MODULE = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "blender"
    / "surface_realism_runtime.py"
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MultiplierQ = Annotated[int, Field(ge=3604, le=4506)]
DetailClass = Literal["stone-fragment", "leaf-card", "damp-patch"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    text = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


class SurfaceMacroPalette(FrozenModel):
    slot_id: Literal[
        "material-moss-stone-01",
        "material-packed-earth-01",
        "material-terrace-soil-01",
        "material-wet-stone-paving-01",
    ]
    source_sha256: Sha256
    quantization_denominator: Literal[4096] = 4096
    multipliers_q: tuple[
        tuple[MultiplierQ, MultiplierQ, MultiplierQ],
        ...,
    ] = Field(min_length=256, max_length=256)
    palette_sha256: Sha256

    @model_validator(mode="after")
    def _digest_matches_palette(self) -> Self:
        actual = hashlib.sha256(
            _canonical_json_bytes(self.multipliers_q),
        ).hexdigest()
        if actual != self.palette_sha256:
            raise ValueError("surface macro palette digest does not match")
        return self


class PathDetailRecord(FrozenModel):
    detail_id: str = Field(
        pattern=r"^path-network-\d{3}:(?:stone|leaf|damp):\d{3}$",
    )
    detail_class: DetailClass
    arc_length_m: float = Field(ge=0, allow_inf_nan=False)
    side_fraction: float = Field(ge=-0.78, le=0.78, allow_inf_nan=False)
    scale: float = Field(ge=0.65, le=0.90, allow_inf_nan=False)
    yaw_deg: float = Field(ge=0, lt=360, allow_inf_nan=False)

    @model_validator(mode="after")
    def _outside_clear_corridor(self) -> Self:
        if abs(self.side_fraction) < 0.68:
            raise ValueError("surface detail violates the clear path corridor")
        return self


class PathRutRun(FrozenModel):
    rut_id: str = Field(pattern=r"^path-network-\d{3}:rut:\d{3}$")
    start_arc_length_m: float = Field(ge=0, allow_inf_nan=False)
    length_m: float = Field(ge=6, le=18, allow_inf_nan=False)
    depth_m: float = Field(ge=0.015, le=0.035, allow_inf_nan=False)


class PathSurfacePlan(FrozenModel):
    object_id: str = Field(pattern=r"^path-network-\d{3}$")
    path_length_m: float = Field(gt=0, allow_inf_nan=False)
    longitudinal_step_m: Literal[1.0] = 1.0
    lateral_rail_count: Literal[6] = 6
    details: tuple[PathDetailRecord, ...]
    rut_runs: tuple[PathRutRun, ...]

    @model_validator(mode="after")
    def _bounded_complete_path_details(self) -> Self:
        detail_ids = [detail.detail_id for detail in self.details]
        rut_ids = [run.rut_id for run in self.rut_runs]
        if (
            detail_ids != sorted(set(detail_ids))
            or rut_ids != sorted(set(rut_ids))
            or {detail.detail_class for detail in self.details}
            != {"stone-fragment", "leaf-card", "damp-patch"}
            or not self.rut_runs
            or any(
                detail.arc_length_m > self.path_length_m
                for detail in self.details
            )
            or any(
                run.start_arc_length_m + run.length_m
                > self.path_length_m + 1e-9
                for run in self.rut_runs
            )
        ):
            raise ValueError("surface path detail plan is incomplete or unbounded")
        return self


class SurfaceRealismPlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.surface-realism-plan.v1"
    ] = "nantai.synthetic-village.surface-realism-plan.v1"
    plan_sha256: Sha256
    profile_id: Literal["source-consistent-multiscale-surface-v1"]
    algorithm_id: Literal["source-palette-world-macro-path-detail-v1"]
    scene_seed: Literal[20260715]
    runtime_module_sha256: Sha256
    terrain_spacing_m: Literal[4.0] = TERRAIN_SPACING_M
    terrain_period_m: Literal[20.0] = TERRAIN_PERIOD_M
    ground_period_m: Literal[10.0] = GROUND_PERIOD_M
    macro_palettes: tuple[SurfaceMacroPalette, ...] = Field(
        min_length=4,
        max_length=4,
    )
    path_plans: tuple[PathSurfacePlan, ...] = Field(
        min_length=6,
        max_length=6,
    )

    @model_validator(mode="after")
    def _complete_content_identity(self) -> Self:
        if tuple(row.slot_id for row in self.macro_palettes) != ACTIVE_MACRO_SLOTS:
            raise ValueError("surface macro material slots are incomplete")
        expected_paths = tuple(
            f"path-network-{index:03d}"
            for index in range(1, 7)
        )
        if tuple(row.object_id for row in self.path_plans) != expected_paths:
            raise ValueError("surface path identities are incomplete")
        actual = hashlib.sha256(
            canonical_surface_realism_plan_bytes(self),
        ).hexdigest()
        if actual != self.plan_sha256:
            raise ValueError("surface realism plan digest does not match")
        return self


@dataclass(frozen=True)
class _Candidate:
    digest: bytes
    record: PathDetailRecord | PathRutRun


def canonical_surface_realism_plan_bytes(
    plan: SurfaceRealismPlan,
) -> bytes:
    """Return canonical unsigned plan bytes used by ``plan_sha256``."""

    return _canonical_json_bytes(
        plan.model_dump(mode="json", exclude={"plan_sha256"}),
    )


def _read_verified_base_color(
    root: Path,
    record: DerivedMaterialRecord,
) -> Image.Image:
    path = root / record.base_color.object_path
    raw = path.read_bytes()
    if (
        len(raw) != record.base_color.bytes
        or hashlib.sha256(raw).hexdigest() != record.base_color.sha256
    ):
        raise ValueError("surface palette base colour failed SHA-256 verification")
    with Image.open(io.BytesIO(raw)) as source:
        source.load()
        if source.size != (1024, 1024):
            raise ValueError("surface palette base colour dimensions are invalid")
        return source.convert("RGB")


def _srgb_to_linear(value: int) -> float:
    normalized = value / 255.0
    if normalized <= 0.04045:
        return normalized / 12.92
    return ((normalized + 0.055) / 1.055) ** 2.4


def _macro_palette(
    root: Path,
    record: DerivedMaterialRecord,
) -> SurfaceMacroPalette:
    image = _read_verified_base_color(root, record)
    channels = []
    for channel_index in range(3):
        linear = Image.new("F", image.size)
        linear.putdata(
            [
                _srgb_to_linear(pixel[channel_index])
                for pixel in image.get_flattened_data()
            ],
        )
        channels.append(
            tuple(
                float(value)
                for value in linear.resize(
                    (16, 16),
                    Image.Resampling.BOX,
                ).get_flattened_data()
            ),
        )
    means = tuple(sum(channel) / len(channel) for channel in channels)
    if any(not math.isfinite(value) or value <= 0 for value in means):
        raise ValueError("surface palette linear mean is invalid")
    multipliers = tuple(
        tuple(
            round(
                min(1.10, max(0.88, channels[channel][index] / means[channel]))
                * 4096,
            )
            for channel in range(3)
        )
        for index in range(256)
    )
    digest = hashlib.sha256(_canonical_json_bytes(multipliers)).hexdigest()
    return SurfaceMacroPalette(
        slot_id=record.slot_id,
        source_sha256=record.source_sha256,
        multipliers_q=multipliers,
        palette_sha256=digest,
    )


def _candidate_digest(
    object_id: str,
    detail_class: str,
    candidate_index: int,
) -> bytes:
    return hashlib.sha256(
        "\0".join(
            (
                SURFACE_PROFILE_V1,
                str(DEFAULT_SEED),
                object_id,
                detail_class,
                str(candidate_index),
            ),
        ).encode("utf-8"),
    ).digest()


def _path_length(path: SceneObject) -> float:
    if path.polyline is None:
        raise ValueError("surface path lacks a polyline")
    return sum(
        math.dist(
            (first.x_m, first.y_m, first.z_m),
            (second.x_m, second.y_m, second.z_m),
        )
        for first, second in zip(
            path.polyline.points,
            path.polyline.points[1:],
            strict=False,
        )
    )


def _detail_candidates(
    path: SceneObject,
    detail_class: DetailClass,
    path_length_m: float,
) -> tuple[_Candidate, ...]:
    parameters = {
        "stone-fragment": (10.0, 96, "stone"),
        "leaf-card": (3.0, 144, "leaf"),
        "damp-patch": (15.0, 112, "damp"),
    }
    spacing_m, acceptance, token = parameters[detail_class]
    result = []
    for candidate_index in range(max(1, math.ceil(path_length_m / spacing_m))):
        digest = _candidate_digest(
            path.object_id,
            detail_class,
            candidate_index,
        )
        arc_length_m = min(
            path_length_m,
            (candidate_index + 0.5) * spacing_m,
        )
        magnitude = 0.68 + digest[2] / 255.0 * 0.10
        side_fraction = magnitude if digest[1] & 1 else -magnitude
        scale = 0.65 + digest[3] / 255.0 * 0.25
        yaw_deg = int.from_bytes(digest[4:6], "big") / 65536.0 * 360.0
        record = PathDetailRecord(
            detail_id=f"{path.object_id}:{token}:{candidate_index:03d}",
            detail_class=detail_class,
            arc_length_m=round(arc_length_m, 6),
            side_fraction=round(side_fraction, 6),
            scale=round(scale, 6),
            yaw_deg=round(yaw_deg, 6),
        )
        result.append(_Candidate(digest=digest, record=record))
    accepted = tuple(candidate for candidate in result if candidate.digest[0] < acceptance)
    if accepted:
        return accepted
    return (min(result, key=lambda candidate: candidate.digest),)


def _rut_candidates(
    path: SceneObject,
    path_length_m: float,
) -> tuple[_Candidate, ...]:
    candidates = []
    for candidate_index in range(max(1, math.ceil(path_length_m / 12.0))):
        digest = _candidate_digest(path.object_id, "rut-run", candidate_index)
        start = candidate_index * 12.0
        available = path_length_m - start
        if available < 6.0:
            continue
        length = min(
            available,
            6.0 + int.from_bytes(digest[1:3], "big") / 65535.0 * 12.0,
        )
        record = PathRutRun(
            rut_id=f"{path.object_id}:rut:{candidate_index:03d}",
            start_arc_length_m=round(start, 6),
            length_m=round(length, 6),
            depth_m=round(0.015 + digest[3] / 255.0 * 0.020, 6),
        )
        candidates.append(_Candidate(digest=digest, record=record))
    if not candidates:
        raise ValueError(f"surface path is too short for a rut: {path.object_id}")
    accepted = tuple(
        candidate
        for candidate in candidates
        if candidate.digest[0] < 112
    )
    if accepted:
        return accepted
    return (min(candidates, key=lambda candidate: candidate.digest),)


def _cap_candidates(
    by_path: dict[str, tuple[_Candidate, ...]],
    *,
    maximum: int,
) -> dict[str, tuple[_Candidate, ...]]:
    required = {
        object_id: min(candidates, key=lambda candidate: candidate.digest)
        for object_id, candidates in by_path.items()
    }
    selected_keys = {
        (object_id, required[object_id].record)
        for object_id in required
    }
    optional = sorted(
        (
            (candidate.digest, object_id, candidate)
            for object_id, candidates in by_path.items()
            for candidate in candidates
            if candidate != required[object_id]
        ),
        key=lambda row: (row[0], row[1]),
    )
    for _digest, object_id, candidate in optional[: maximum - len(required)]:
        selected_keys.add((object_id, candidate.record))
    return {
        object_id: tuple(
            candidate
            for candidate in candidates
            if (object_id, candidate.record) in selected_keys
        )
        for object_id, candidates in by_path.items()
    }


def _path_plans(scene_plan: ScenePlan) -> tuple[PathSurfacePlan, ...]:
    paths = tuple(
        sorted(
            (
                item
                for item in scene_plan.objects
                if item.semantic_class == "path"
            ),
            key=lambda item: item.object_id,
        ),
    )
    expected = tuple(f"path-network-{index:03d}" for index in range(1, 7))
    if tuple(path.object_id for path in paths) != expected:
        raise ValueError("surface realism requires the complete stable path network")
    lengths = {path.object_id: _path_length(path) for path in paths}
    details_by_class: dict[
        DetailClass,
        dict[str, tuple[_Candidate, ...]],
    ] = {}
    for detail_class in ("stone-fragment", "leaf-card", "damp-patch"):
        details_by_class[detail_class] = _cap_candidates(
            {
                path.object_id: _detail_candidates(
                    path,
                    detail_class,
                    lengths[path.object_id],
                )
                for path in paths
            },
            maximum=MAX_DETAIL_COUNTS[detail_class],
        )
    ruts = _cap_candidates(
        {
            path.object_id: _rut_candidates(
                path,
                lengths[path.object_id],
            )
            for path in paths
        },
        maximum=MAX_DETAIL_COUNTS["rut-run"],
    )
    result = []
    for path in paths:
        detail_records = tuple(
            sorted(
                (
                    candidate.record
                    for detail_class in (
                        "stone-fragment",
                        "leaf-card",
                        "damp-patch",
                    )
                    for candidate in details_by_class[detail_class][path.object_id]
                ),
                key=lambda record: record.detail_id,
            ),
        )
        rut_records = tuple(
            sorted(
                (
                    candidate.record
                    for candidate in ruts[path.object_id]
                ),
                key=lambda record: record.rut_id,
            ),
        )
        result.append(
            PathSurfacePlan(
                object_id=path.object_id,
                path_length_m=round(lengths[path.object_id], 6),
                details=detail_records,
                rut_runs=rut_records,
            ),
        )
    return tuple(result)


def build_surface_realism_plan(
    scene_plan: ScenePlan,
    material_bundle_root: Path,
) -> SurfaceRealismPlan:
    """Build the v1 path-free, content-addressed finite surface plan."""

    if scene_plan.seed != DEFAULT_SEED:
        raise ValueError("surface realism requires the stable scene seed")
    material_bundle_root = Path(material_bundle_root)
    bundle = load_material_bundle(material_bundle_root)
    records = {record.slot_id: record for record in bundle.records}
    if any(slot_id not in records for slot_id in ACTIVE_MACRO_SLOTS):
        raise ValueError("surface macro material inputs are incomplete")
    runtime_bytes = RUNTIME_MODULE.read_bytes()
    payload = {
        "schema_version": "nantai.synthetic-village.surface-realism-plan.v1",
        "profile_id": SURFACE_PROFILE_V1,
        "algorithm_id": SURFACE_ALGORITHM_V1,
        "scene_seed": DEFAULT_SEED,
        "runtime_module_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
        "terrain_spacing_m": TERRAIN_SPACING_M,
        "terrain_period_m": TERRAIN_PERIOD_M,
        "ground_period_m": GROUND_PERIOD_M,
        "macro_palettes": tuple(
            _macro_palette(material_bundle_root, records[slot_id])
            for slot_id in ACTIVE_MACRO_SLOTS
        ),
        "path_plans": _path_plans(scene_plan),
    }
    plan_sha256 = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return SurfaceRealismPlan(
        plan_sha256=plan_sha256,
        **payload,
    )
