"""Shared deterministic terrain contract for mesh and Gaussian world paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pipeline.synthetic_village.infinite_terrain import (
    TERRAIN_ALGORITHM_ID,
    terrain_height_m,
    terrain_macro_tint,
)


def test_terrain_identity_is_explicit_and_height_is_meaningful() -> None:
    assert TERRAIN_ALGORITHM_ID == "synthetic-multiscale-relief-v1"

    heights = [
        terrain_height_m(x, y, world_seed=42)
        for x in range(-600, 601, 40)
        for y in range(-600, 601, 40)
    ]

    assert max(heights) - min(heights) > 4.0
    assert min(heights) >= -6.0
    assert max(heights) <= 6.0


def test_same_world_coordinate_is_exact_across_chunk_boundaries() -> None:
    shared_edge = [
        (200.0, float(y), terrain_height_m(200.0, y, world_seed=42))
        for y in range(0, 201, 25)
    ]
    east_chunk_view = [
        (200.0, float(y), terrain_height_m(200.0, y, world_seed=42))
        for y in range(0, 201, 25)
    ]

    assert east_chunk_view == shared_edge
    assert len({row[2] for row in shared_edge}) > 1


def test_macro_tint_is_gentle_deterministic_and_nonconstant() -> None:
    first = [
        terrain_macro_tint(x, y, world_seed=42)
        for x in range(-800, 801, 100)
        for y in range(-800, 801, 100)
    ]
    second = [
        terrain_macro_tint(x, y, world_seed=42)
        for x in range(-800, 801, 100)
        for y in range(-800, 801, 100)
    ]

    assert first == second
    assert min(first) >= 0.90
    assert max(first) <= 1.10
    assert max(first) - min(first) > 0.08


def test_terrain_samples_are_stable_across_processes() -> None:
    root = Path(__file__).resolve().parent.parent
    code = (
        "import hashlib,json;"
        "from pipeline.synthetic_village.infinite_terrain import "
        "terrain_height_m,terrain_macro_tint;"
        "samples=[(terrain_height_m(x,y,world_seed=7),"
        "terrain_macro_tint(x,y,world_seed=7)) "
        "for x,y in [(-200.0,-25.0),(0.0,0.0),(200.0,425.0)]];"
        "print(hashlib.sha256(json.dumps(samples,separators=(',',':'))"
        ".encode()).hexdigest())"
    )

    def run() -> str:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    assert run() == run()
