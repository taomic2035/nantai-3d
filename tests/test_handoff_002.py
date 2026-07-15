"""Cross-platform canonicalization contract for HANDOFF-002 assets."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = (
    ROOT / "handoff" / "deliverables" / "HANDOFF-002" / "scripts" / "generate.py"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location("handoff_002_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_float32_absorbs_sub_grid_platform_drift():
    generator = _load_generator()
    values = np.array([-4.1254321, 0.3141592, 8.8754321], dtype=np.float64)
    platform_drift = np.array([2e-12, -3e-12, 4e-12], dtype=np.float64)

    baseline = generator.canonical_float32(values)
    alternate = generator.canonical_float32(values + platform_drift)

    assert baseline.dtype == np.float32
    assert np.array_equal(baseline.view(np.uint32), alternate.view(np.uint32))


def test_generator_emits_handoff_002_manifest_and_reproducible_payloads(tmp_path):
    generator = _load_generator()
    first = tmp_path / "first"
    second = tmp_path / "second"

    generator.main(first)
    generator.main(second)

    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    repeated = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["handoff_id"] == "HANDOFF-002"
    assert len(manifest["items"]) == 11
    assert {
        item["asset_id"]: item["sha256"] for item in manifest["items"]
    } == {
        item["asset_id"]: item["sha256"] for item in repeated["items"]
    }

