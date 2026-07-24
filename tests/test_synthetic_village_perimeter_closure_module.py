"""TDD contract for the additive Batch24 exact-266 closure plan."""

from __future__ import annotations

import copy
import hashlib
import math
import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village.perimeter_closure_module import (
    PERIMETER_CLOSURE_MODULE_ORDER,
    PERIMETER_CLOSURE_ROLE_ORDER,
    PerimeterClosureError,
    PerimeterClosurePlan,
    build_default_perimeter_closure_plan,
    canonical_perimeter_closure_plan_bytes,
    perimeter_closure_plan_sha256,
    verify_perimeter_closure_plan,
)

BATCH24_ASSETS = (
    (
        "reciprocal-downstream-creek-basin-inbound-01.png",
        "reciprocal-perimeter",
        "downstream",
        "1099282dd6d8a4ffad94b61c989e0a7fd1bab229be916d0565c203d8712a7e9b",
    ),
    (
        "reciprocal-east-orchard-route-inbound-01.png",
        "reciprocal-perimeter",
        "east",
        "39bc303359bf1f1c4028c1dba42619dcb7b22ac21945fd8e8d0d4c5eded91a38",
    ),
    (
        "reciprocal-northeast-forest-terrace-inbound-01.png",
        "reciprocal-perimeter",
        "northeast",
        "6f056c7f5bbbcefb8b8af6b0e5980656beaf281a162c6f3e4057c9d99f35753d",
    ),
    (
        "reciprocal-northwest-flume-ridge-inbound-01.png",
        "reciprocal-perimeter",
        "northwest",
        "622a1264f7432cf29523a699bf9bc5b24031e25f6d4c8846c7ffca27fc392a18",
    ),
    (
        "reciprocal-southeast-service-edge-inbound-01.png",
        "reciprocal-perimeter",
        "southeast",
        "9ed97dd7d1cc61b4817e021b79b8dd27580818db333d67740a923564d6de1b59",
    ),
    (
        "reciprocal-southwest-stone-bank-inbound-01.png",
        "reciprocal-perimeter",
        "southwest",
        "b4a7dbe7cac6bffe6fa90ed817cee69e0e661d00914216d083adecdeb3412c44",
    ),
    (
        "reciprocal-upstream-creek-valley-inbound-01.png",
        "reciprocal-perimeter",
        "upstream",
        "8ff37aa89b68cb3c6fa63d2ae27caa938c45d251badfab248b324a4039d06526",
    ),
    (
        "reciprocal-west-uphill-forest-inbound-01.png",
        "reciprocal-perimeter",
        "west",
        "c4dd5f94fd10723a6ae6b1decde9992bd498d73997237bd326367782db4b5a77",
    ),
    (
        "section-downstream-tailwater-floodbench-01.png",
        "section-closure",
        "downstream",
        "961a01195a750190433d843cb956a7a2ed33e6a3e1b9fffd4221abeb114c0623",
    ),
    (
        "section-east-orchard-route-cutfill-01.png",
        "section-closure",
        "east",
        "4b751defe9f54e82ffa1d3fffc8ef8bf8c4b93f6e84dbd571ea90fbe98797395",
    ),
    (
        "section-northeast-terrace-drainage-01.png",
        "section-closure",
        "northeast",
        "904c1f177553368ddd46bab097129555a3e64ceba26b2e9833b945481bc75980",
    ),
    (
        "section-northwest-flume-ridge-support-01.png",
        "section-closure",
        "northwest",
        "87e615ad0108668f1b71274d7357e5c7c55cddc1faa243a1416f49c87221e5c5",
    ),
    (
        "section-southeast-service-yard-drainage-01.png",
        "section-closure",
        "southeast",
        "4b70625e0e1250749756ef9344d01af32d4fa8ce2138e51890e4d0d55d00ad45",
    ),
    (
        "section-southwest-bridge-bank-foundation-01.png",
        "section-closure",
        "southwest",
        "8c38577a47c174c8a358135651dc537946cf82e20e46868a5524090d17ecca35",
    ),
    (
        "section-upstream-flume-creek-support-01.png",
        "section-closure",
        "upstream",
        "b4a6dcd299d35286605097da4e2b5958177cc4c143bc84b3554d365fe512618a",
    ),
    (
        "section-west-forest-loop-retaining-01.png",
        "section-closure",
        "west",
        "d28577a334db61abb3c4ab5076a062a1dceb7698f1a960b6d8069112acced6bd",
    ),
)


@pytest.fixture
def batch24_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "batch_id": "synthetic-village-design-inputs-batch24-2026-07-23",
        "asset_count": 16,
        "prompt_count": 16,
        "trust": {
            "synthetic": True,
            "stage": "design-only",
            "camera_calibration": "unknown",
            "geometry_consistency": "not-verified",
            "metric_scale": "unknown",
            "real_photo_texture": False,
            "training_use": "forbidden-as-multiview",
            "coverage_use": "forbidden",
            "trust_effect": "none",
        },
        "assets": [
            {"file": file, "kind": kind, "sector": sector, "sha256": sha256}
            for file, kind, sector, sha256 in BATCH24_ASSETS
        ],
    }


@pytest.fixture
def plan(batch24_manifest: dict[str, Any]) -> PerimeterClosurePlan:
    return build_default_perimeter_closure_plan(
        batch24_manifest=batch24_manifest,
        batch24_manifest_sha256="a" * 64,
        production_plan_sha256="b" * 64,
        topology_plan_sha256="c" * 64,
        terrain_height_at=lambda x, y: round(0.015 * x - 0.01 * y, 3),
    )


def _payload(plan: PerimeterClosurePlan) -> dict[str, Any]:
    return copy.deepcopy(plan.model_dump(mode="json"))


def test_default_plan_locks_order_roles_instances_and_canonical_hash(
    plan: PerimeterClosurePlan,
) -> None:
    assert PERIMETER_CLOSURE_MODULE_ORDER == (
        "closure-upstream",
        "closure-northeast",
        "closure-east",
        "closure-southeast",
        "closure-downstream",
        "closure-southwest",
        "closure-west",
        "closure-northwest",
    )
    assert PERIMETER_CLOSURE_ROLE_ORDER == (
        "terrain-contact",
        "bidirectional-corridor",
        "support-retaining",
        "drainage-water",
        "boundary-seam",
        "vegetation-enclosure",
    )
    assert tuple(module.module_id for module in plan.modules) == (
        PERIMETER_CLOSURE_MODULE_ORDER
    )
    assert [part.instance_id for module in plan.modules for part in module.parts] == (
        list(range(219, 267))
    )
    assert all(
        tuple(part.semantic_role for part in module.parts)
        == PERIMETER_CLOSURE_ROLE_ORDER
        for module in plan.modules
    )
    assert canonical_perimeter_closure_plan_bytes(plan).endswith(b"\n")
    assert perimeter_closure_plan_sha256(plan) == hashlib.sha256(
        canonical_perimeter_closure_plan_bytes(plan)
    ).hexdigest()


def test_default_plan_is_cross_process_deterministic(
    batch24_manifest: dict[str, Any],
) -> None:
    code = f"""
from pipeline.synthetic_village.perimeter_closure_module import (
    build_default_perimeter_closure_plan,
    perimeter_closure_plan_sha256,
)
manifest = {batch24_manifest!r}
plan = build_default_perimeter_closure_plan(
    batch24_manifest=manifest,
    batch24_manifest_sha256="a" * 64,
    production_plan_sha256="b" * 64,
    topology_plan_sha256="c" * 64,
    terrain_height_at=lambda x, y: round(0.015 * x - 0.01 * y, 3),
)
print(perimeter_closure_plan_sha256(plan))
"""

    def run() -> str:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    assert run() == run() == perimeter_closure_plan_sha256(
        build_default_perimeter_closure_plan(
            batch24_manifest=batch24_manifest,
            batch24_manifest_sha256="a" * 64,
            production_plan_sha256="b" * 64,
            topology_plan_sha256="c" * 64,
            terrain_height_at=lambda x, y: round(0.015 * x - 0.01 * y, 3),
        )
    )


def test_default_plan_binds_all_sixteen_sources_exactly_once(
    plan: PerimeterClosurePlan,
) -> None:
    actual = [
        (module.reciprocal_source_file, module.reciprocal_source_sha256)
        for module in plan.modules
    ] + [
        (module.section_source_file, module.section_source_sha256)
        for module in plan.modules
    ]
    expected = [(file, sha256) for file, _kind, _sector, sha256 in BATCH24_ASSETS]
    assert sorted(actual) == sorted(expected)


def test_default_plan_is_finite_terrain_sampled_and_honest(
    plan: PerimeterClosurePlan,
) -> None:
    assert plan.synthetic is True
    assert plan.verification_level == "L0"
    assert plan.geometry_usability == "preview-only"
    assert plan.geometry_trust == "modeled-unverified"
    assert plan.trust_effect == "none"
    for module in plan.modules:
        assert module.inner_anchor_m != module.outer_anchor_m
        assert module.previous_seam_m != module.next_seam_m
        for part in module.parts:
            assert all(math.isfinite(value) for value in part.center_m)
            assert all(value > 0.0 for value in part.extent_m)
            assert part.inner_anchor_m[2] == pytest.approx(
                0.015 * part.inner_anchor_m[0] - 0.01 * part.inner_anchor_m[1],
                abs=1e-3,
            )
            assert part.outer_anchor_m[2] == pytest.approx(
                0.015 * part.outer_anchor_m[0] - 0.01 * part.outer_anchor_m[1],
                abs=1e-3,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("batch24_manifest_sha256", "A" * 64),
        ("production_plan_sha256", "not-a-sha"),
        ("topology_plan_sha256", "f" * 63),
        ("synthetic", False),
        ("verification_level", "L1"),
        ("geometry_usability", "metric-aligned"),
        ("trust_effect", "coverage"),
    ),
)
def test_plan_rejects_promoted_trust_or_malformed_hashes(
    plan: PerimeterClosurePlan,
    field: str,
    value: object,
) -> None:
    payload = _payload(plan)
    payload[field] = value
    with pytest.raises(ValidationError):
        PerimeterClosurePlan.model_validate(payload)


def test_plan_rejects_missing_extra_or_out_of_order_modules(
    plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(plan)
    for modules in (
        payload["modules"][:-1],
        payload["modules"] + [payload["modules"][-1]],
        list(reversed(payload["modules"])),
    ):
        mutated = copy.deepcopy(payload)
        mutated["modules"] = modules
        with pytest.raises(ValidationError):
            PerimeterClosurePlan.model_validate(mutated)


def test_plan_rejects_instance_gaps_and_role_reordering(
    plan: PerimeterClosurePlan,
) -> None:
    for field, value in (
        ("instance_id", 220),
        ("semantic_role", "drainage-water"),
    ):
        payload = _payload(plan)
        payload["modules"][0]["parts"][0][field] = value
        with pytest.raises(ValidationError):
            PerimeterClosurePlan.model_validate(payload)


def test_plan_rejects_nonfinite_or_nonpositive_geometry(
    plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(plan)
    payload["modules"][0]["parts"][0]["center_m"][0] = float("nan")
    with pytest.raises(ValidationError):
        PerimeterClosurePlan.model_validate(payload)

    payload = _payload(plan)
    payload["modules"][0]["parts"][0]["extent_m"][0] = 0.0
    with pytest.raises(ValidationError):
        PerimeterClosurePlan.model_validate(payload)


def test_plan_rejects_equal_route_or_seam_anchors(
    plan: PerimeterClosurePlan,
) -> None:
    payload = _payload(plan)
    payload["modules"][0]["outer_anchor_m"] = payload["modules"][0]["inner_anchor_m"]
    with pytest.raises(ValidationError):
        PerimeterClosurePlan.model_validate(payload)

    payload = _payload(plan)
    payload["modules"][0]["next_seam_m"] = payload["modules"][0]["previous_seam_m"]
    with pytest.raises(ValidationError):
        PerimeterClosurePlan.model_validate(payload)


def test_builder_fails_closed_on_manifest_missing_hash_or_swapped_kind(
    batch24_manifest: dict[str, Any],
) -> None:
    def build(manifest: dict[str, Any]) -> None:
        build_default_perimeter_closure_plan(
            batch24_manifest=manifest,
            batch24_manifest_sha256="a" * 64,
            production_plan_sha256="b" * 64,
            topology_plan_sha256="c" * 64,
            terrain_height_at=lambda _x, _y: 0.0,
        )

    missing = copy.deepcopy(batch24_manifest)
    missing["assets"].pop()
    with pytest.raises(PerimeterClosureError):
        build(missing)

    wrong_hash = copy.deepcopy(batch24_manifest)
    wrong_hash["assets"][0]["sha256"] = "0" * 64
    with pytest.raises(PerimeterClosureError):
        build(wrong_hash)

    swapped = copy.deepcopy(batch24_manifest)
    swapped["assets"][0]["kind"] = "section-closure"
    with pytest.raises(PerimeterClosureError):
        build(swapped)


def test_verifier_rebinds_manifest_sources(
    plan: PerimeterClosurePlan,
    batch24_manifest: dict[str, Any],
) -> None:
    verify_perimeter_closure_plan(plan, batch24_manifest=batch24_manifest)
    mutated = copy.deepcopy(batch24_manifest)
    mutated["assets"][0]["sha256"] = "0" * 64
    with pytest.raises(PerimeterClosureError):
        verify_perimeter_closure_plan(plan, batch24_manifest=mutated)
