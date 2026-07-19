from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from pipeline.synthetic_village.material_bundle import publish_material_bundle
from pipeline.synthetic_village.scene_plan import build_scene_plan
from pipeline.synthetic_village.surface_realism import (
    ACTIVE_MACRO_SLOTS,
    MAX_DETAIL_COUNTS,
    SURFACE_PROFILE_V1,
    SurfaceMacroPalette,
    build_surface_realism_plan,
    canonical_surface_realism_plan_bytes,
)
from pipeline.synthetic_village.visual_sources import (
    VisualSourceManifest,
    canonical_manifest_bytes,
    load_visual_source_manifest,
)
from scripts.blender.surface_realism_runtime import sample_macro_color
from tests.synthetic_material_fixtures import (
    publish_material_fixture,
    write_material_visual_pack,
)


def _palette() -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (3604 + index % 320, 3650 + index % 280, 3700 + index % 240)
        for index in range(256)
    )


def test_macro_sampler_is_stable_across_negative_lattice_boundaries() -> None:
    palette = _palette()
    first = sample_macro_color(
        palette,
        x_m=-20.0001,
        y_m=-0.0001,
        period_m=20.0,
        scene_seed=20260715,
        source_sha256="a" * 64,
    )
    second = sample_macro_color(
        palette,
        x_m=-20.0001,
        y_m=-0.0001,
        period_m=20.0,
        scene_seed=20260715,
        source_sha256="a" * 64,
    )
    assert first == second
    assert first[3] == 1.0
    assert all(0.88 <= value <= 1.10 for value in first[:3])
    assert first != sample_macro_color(
        palette,
        x_m=20.0001,
        y_m=-0.0001,
        period_m=20.0,
        scene_seed=20260715,
        source_sha256="a" * 64,
    )


@pytest.mark.parametrize(
    ("palette", "period_m", "source_sha256"),
    [
        (((4096, 4096, 4096),), 20.0, "a" * 64),
        (_palette(), 0.0, "a" * 64),
        (_palette(), 20.0, "not-a-sha"),
        (
            tuple(
                (4507, green, blue)
                if index == 0
                else (red, green, blue)
                for index, (red, green, blue) in enumerate(_palette())
            ),
            20.0,
            "a" * 64,
        ),
    ],
)
def test_macro_sampler_rejects_untrusted_inputs(
    palette: tuple[tuple[int, int, int], ...],
    period_m: float,
    source_sha256: str,
) -> None:
    with pytest.raises(ValueError, match="macro sampler"):
        sample_macro_color(
            palette,
            x_m=0.0,
            y_m=0.0,
            period_m=period_m,
            scene_seed=20260715,
            source_sha256=source_sha256,
        )


def test_surface_plan_is_complete_content_addressed_and_path_free(
    tmp_path: Path,
) -> None:
    _visual_root, bundle = publish_material_fixture(tmp_path)
    plan = build_surface_realism_plan(
        build_scene_plan(),
        bundle.final_directory,
    )
    assert plan.profile_id == SURFACE_PROFILE_V1
    assert tuple(row.slot_id for row in plan.macro_palettes) == ACTIVE_MACRO_SLOTS
    assert len(plan.path_plans) == 6
    assert all(row.lateral_rail_count == 6 for row in plan.path_plans)
    assert all(row.longitudinal_step_m == 1.0 for row in plan.path_plans)
    assert all(
        {detail.detail_class for detail in row.details}
        == {"damp-patch", "leaf-card", "stone-fragment"}
        for row in plan.path_plans
    )
    assert all(row.rut_runs for row in plan.path_plans)
    raw = canonical_surface_realism_plan_bytes(plan)
    assert hashlib.sha256(raw).hexdigest() == plan.plan_sha256
    assert str(tmp_path).encode() not in raw
    assert str(Path.home()).encode() not in raw


def test_surface_plan_detail_counts_corridor_and_envelope_are_bounded(
    tmp_path: Path,
) -> None:
    _visual_root, bundle = publish_material_fixture(tmp_path)
    plan = build_surface_realism_plan(
        build_scene_plan(),
        bundle.final_directory,
    )
    counts = Counter(
        detail.detail_class
        for path in plan.path_plans
        for detail in path.details
    )
    counts["rut-run"] = sum(len(path.rut_runs) for path in plan.path_plans)
    assert set(counts) == set(MAX_DETAIL_COUNTS)
    assert all(counts[key] <= maximum for key, maximum in MAX_DETAIL_COUNTS.items())
    assert all(
        0.68 <= abs(detail.side_fraction) <= 0.78
        for path in plan.path_plans
        for detail in path.details
    )
    assert all(
        0.65 <= detail.scale <= 0.90
        for path in plan.path_plans
        for detail in path.details
    )
    assert all(
        run.start_arc_length_m + run.length_m <= path.path_length_m + 1e-9
        for path in plan.path_plans
        for run in path.rut_runs
    )


def test_surface_palette_rejects_out_of_range_multiplier(
    tmp_path: Path,
) -> None:
    _visual_root, bundle = publish_material_fixture(tmp_path)
    palette = build_surface_realism_plan(
        build_scene_plan(),
        bundle.final_directory,
    ).macro_palettes[0]
    payload = palette.model_dump(mode="json")
    payload["multipliers_q"][0][0] = 4507
    with pytest.raises(ValidationError):
        SurfaceMacroPalette.model_validate(payload)


def test_surface_plan_changes_when_verified_source_changes(tmp_path: Path) -> None:
    _first_visual, first_bundle = publish_material_fixture(tmp_path / "first")
    first = build_surface_realism_plan(
        build_scene_plan(),
        first_bundle.final_directory,
    )

    second_root = tmp_path / "second"
    second_visual = write_material_visual_pack(second_root / "visual")
    manifest_path = second_visual / "visual-sources.json"
    manifest = load_visual_source_manifest(manifest_path)
    records = list(manifest.records)
    index = next(
        position
        for position, record in enumerate(records)
        if record.slot_id == "material-packed-earth-01"
    )
    record = records[index]
    with Image.open(second_visual / record.object_path) as source:
        changed = source.convert("RGB")
    changed.putpixel((0, 0), (255, 255, 255))
    stream = io.BytesIO()
    changed.save(stream, format="PNG", compress_level=9, optimize=False)
    payload = stream.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    (second_visual / "objects" / f"{digest}.png").write_bytes(payload)
    records[index] = record.model_copy(
        update={
            "object_path": f"objects/{digest}.png",
            "sha256": digest,
            "bytes": len(payload),
        },
    )
    manifest_path.write_bytes(
        canonical_manifest_bytes(
            VisualSourceManifest(
                pack_id=manifest.pack_id,
                records=tuple(records),
            ),
        ),
    )
    second_bundle = publish_material_bundle(
        visual_pack_root=second_visual,
        publication_root=second_root / "material-bundles",
        work_root=second_root / "material-work",
    )
    second = build_surface_realism_plan(
        build_scene_plan(),
        second_bundle.final_directory,
    )
    assert second.plan_sha256 != first.plan_sha256
    assert second.macro_palettes != first.macro_palettes


def test_surface_plan_is_process_independent(tmp_path: Path) -> None:
    _visual_root, bundle = publish_material_fixture(tmp_path)
    script = "\n".join(
        (
            "from pathlib import Path",
            "from pipeline.synthetic_village.scene_plan import build_scene_plan",
            "from pipeline.synthetic_village.surface_realism import build_surface_realism_plan",
            f"root = Path({str(bundle.final_directory)!r})",
            "print(build_surface_realism_plan(build_scene_plan(), root).plan_sha256)",
        ),
    )
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = "random"
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
        ).stdout.strip()
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]
    assert len(outputs[0]) == 64
