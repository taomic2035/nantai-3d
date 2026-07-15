#!/usr/bin/env python3
"""Generate canonical, cross-platform HANDOFF-002 proxy assets.

Geometry and seeded visual design come from HANDOFF-001.  This handoff changes
only the serialization boundary: every encoded floating-point PLY property is
rounded to a fixed decimal grid before conversion to little-endian float32.
That absorbs platform libm drift while preserving the existing asset contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parents[1]
SOURCE_GENERATOR = (
    ROOT / "handoff" / "deliverables" / "HANDOFF-001" / "scripts" / "generate.py"
)
GENERATOR_VERSION = "2.0.0"
CANONICAL_DECIMALS = 6
sys.path.insert(0, str(ROOT))

_source = runpy.run_path(str(SOURCE_GENERATOR))
Builder = _source["Builder"]
SPECS = _source["SPECS"]


def canonical_float32(values: np.ndarray) -> np.ndarray:
    """Map finite values to the canonical 1e-6 grid and float32 bytes."""
    array = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError("canonical PLY properties must be finite")
    return np.round(array, decimals=CANONICAL_DECIMALS).astype(np.float32)


def _canonical_vertex_array(scene) -> np.ndarray:
    props = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
    ]
    props.extend((f"f_rest_{index}", "f4") for index in range(scene.sh_rest.shape[1]))
    props.extend(
        [
            ("opacity", "f4"),
            ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
            ("rot_0", "f4"), ("rot_1", "f4"),
            ("rot_2", "f4"), ("rot_3", "f4"),
        ]
    )
    if scene.extra_properties:
        raise ValueError("HANDOFF-002 fixtures do not permit undeclared PLY properties")

    vertices = np.zeros(len(scene), dtype=props)
    vertices["x"], vertices["y"], vertices["z"] = canonical_float32(scene.xyz).T
    vertices["nx"], vertices["ny"], vertices["nz"] = canonical_float32(scene.normals).T
    vertices["f_dc_0"], vertices["f_dc_1"], vertices["f_dc_2"] = canonical_float32(
        scene.sh_dc
    ).T
    for index in range(scene.sh_rest.shape[1]):
        vertices[f"f_rest_{index}"] = canonical_float32(scene.sh_rest[:, index])

    opacity = np.clip(scene.opacity, 1e-6, 1.0 - 1e-6)
    vertices["opacity"] = canonical_float32(np.log(opacity / (1.0 - opacity)))
    log_scale = np.log(np.clip(scene.scale, 1e-9, None))
    vertices["scale_0"], vertices["scale_1"], vertices["scale_2"] = (
        canonical_float32(log_scale).T
    )
    rotations = scene.rot / np.linalg.norm(scene.rot, axis=1, keepdims=True)
    vertices["rot_0"], vertices["rot_1"], vertices["rot_2"], vertices["rot_3"] = (
        canonical_float32(rotations).T
    )
    return vertices


def save_canonical_3dgs(scene, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": 2,
        "frame_id": scene.frame_id,
        "units": scene.units,
        "applied_transform_ids": scene.applied_transform_ids,
        "applied_transform_paths": scene.applied_transform_paths,
        "provenance_frames": scene.provenance_frames,
    }
    comment = "nantai_meta=" + json.dumps(
        metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    element = PlyElement.describe(_canonical_vertex_array(scene), "vertex")
    PlyData([element], byte_order="<", comments=[comment]).write(str(path))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(output: Path = OUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    items = []
    for spec in SPECS:
        builder = Builder(spec.asset_id)
        spec.build(builder)
        scene = builder.finish()
        path = output / f"{spec.asset_id}.ply"
        save_canonical_3dgs(scene, path)
        items.append(
            {
                "asset_id": spec.asset_id,
                "kind": spec.kind,
                "ply": path.name,
                "footprint_m": list(spec.footprint_m),
                "sha256": _sha256_file(path),
            }
        )

    manifest = {
        "schema_version": 2,
        "handoff_id": "HANDOFF-002",
        "coordinate_system": {"units": "meters", "axes": "local-z-up"},
        "generator": {
            "name": "nantai-handoff-002-canonical-proxies",
            "version": GENERATOR_VERSION,
            "script_sha256": _sha256_file(Path(__file__)),
            "source_handoff": "HANDOFF-001",
            "canonical_decimals": CANONICAL_DECIMALS,
        },
        "items": items,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    main(args.output)
