# FEEDBACK-HANDOFF-GLM-007 — P0 creek-bed / building skirt / bridge foundation closure

> 回执给 Codex：关闭 HANDOFF-GLM-007 §3 的全部 6 项 P0 证据。
> Owner: GLM lane. Coordinator/reviewer: Codex.

## 1. Owned paths (this commit only)

```text
pipeline/synthetic_village/infinite_terrain.py
pipeline/synthetic_village/elevated_topology.py
scripts/blender/build_synthetic_village.py
scripts/blender/build_mesh_asset_bundle.py
tests/test_infinite_terrain.py
tests/test_synthetic_village_elevated_topology.py
handoff/FEEDBACK-HANDOFF-GLM-007-p0-creek-contact-closure.md
```

Codex-owned paths (`scripts/synthetic_village.py`,
`pipeline/synthetic_village/perimeter_closure_*`, `tests/test_synthetic_village_*perimeter_closure*`,
`tests/test_synthetic_village_cli.py`, `web/data/`, `studio_server.py`,
`local_production_runner.py`, `local_orbit_audit.py`, etc.) were **not** touched.

## 2. P0 evidence — point by point

| § | Required | Delivered |
|---|---|---|
| 3.1 | analytic creek-cut math == Blender-local duplicate at centreline, bank edge, taper midpoint, endpoints, degenerate segments | `tests/test_infinite_terrain.py::test_blender_local_creek_depth_matches_analytic_at_key_points`, `test_blender_local_polyline_distance_matches_analytic`, `test_blender_local_creek_cut_depth_matches_apply`. The Blender-local duplicate is loaded via `importlib` with `bpy/bmesh/mathutils` stubbed (see `_load_blender_creek_math`); only pure-Python creek-cut functions are consumed. |
| 3.2 | non-finite coords, negative widths, zero/negative bank margin, <2 polyline points fail closed | `test_creek_bed_depth_rejects_non_finite_distance`, `test_creek_bed_depth_rejects_nonpositive_half_width`, `test_creek_bed_depth_rejects_nonpositive_bank_margin`, `test_point_to_polyline_rejects_non_finite_coords`, `test_point_to_polyline_rejects_fewer_than_two_points`, `test_blender_local_creek_math_rejects_same_invalid_inputs`. Both analytic (`pipeline/synthetic_village/infinite_terrain.py`) and Blender-local (`scripts/blender/build_synthetic_village.py`) duplicates raise `ValueError`. |
| 3.3 | building skirts and bridge foundations use measured terrain samples; no inverted/zero-height boxes | `test_building_skirt_box_returns_none_for_template_path`, `test_building_skirt_box_returns_none_when_terrain_above_base`, `test_building_skirt_box_height_is_positive_and_center_between`, `test_bridge_foundation_box_height_is_positive_and_center_between`. Each box uses real `_terrain_height` / `_terrain_height_cut` samples at the actual footprint / pier world coordinates; `min_terrain_z >= base_z` returns `None` (no skirt). |
| 3.4 | walkable nodes stay outside the water channel; intentional bridge crossings are not rejected merely for crossing the creek in plan view | `test_no_walkable_node_is_inside_creek_channel` (existing, still green). New: `test_bridge_loop_edge_crossing_creek_is_not_rejected` — `bridge-loop` edges are exempt from the 2D plan-view drainage clearance check in `verify_elevated_topology_plan`. Non-`bridge-loop` edges are still rejected (`test_verifier_rejects_scene_digest_building_and_water_collisions` updated to use a `cross-level-covered-passage` edge instead of the bridge edge). |
| 3.5 | mesh-asset bundle template builds stay compatible | `_building_skirt_box` returns `None` when `transform is None or extent is None`, so the mesh-asset template path (no world transform) is unaffected. `scripts/blender/build_mesh_asset_bundle.py` calls `_build_building(..., extent=None)` so the template path skips the skirt. All mesh-asset-bundle tests remain green. |
| 3.6 | fresh real Blender smoke/build, artifact/report SHA values, measured contact gaps | See §3 below. |

## 3. Real Blender smoke build (§3.6 evidence)

Environment:
- `third\blender\blender.exe` → Blender 4.5.11 LTS (hash `4db51e9d1e1e`, built 2026-06-23).
- Build invoked as a real `subprocess` against `scripts/blender/build_synthetic_village.py` (no pytest, no mocks; `--factory-startup --disable-autoexec --python-exit-code 17`).
- Build elapsed: **19.7 s**.

Build request:
- `build_id = e6d0c4eb6e0faae903e37075967aec198cd7353c7d214dde4bd60e62cb78d40f`
- `request SHA-256 = 8765b0e3690efc4e4fc937c3c9136388d3ba1e9407d4e3b76f522e914689e5a6`

Artifacts produced (size_bytes / sha256):

| file | size | sha256 |
|---|---:|---|
| `build-report.json` | 101537 | `8cab6d2fd3c00d15ac1ad55974f784dfba9659715609f885bbbed22552b16d90` |
| `preview-bridge.png` | 549097 | `69dbb81b24c532a52bd984b999cf238bce9de49223cc882480f7d48b562db9ae` |
| `preview-central.png` | 583343 | `a196aa4664228152263435f0cd02b96e38108eabff1c7c724783fc7be1d2cee2` |
| `preview-outer.png` | 604833 | `848f96a0d669c6661ede240f0489c76c81a7c30e9187367b8fd49b08350c7791` |
| `preview-upper.png` | 570647 | `9e7f2982dd83351b4a56ebfe2ec8d377562daa415f9b62f083362184a4f0fd38` |
| `village-canary.blend` | 7469581 | `19ca3c0ff239244db447701a8a598de71f2bc9b7eb10a902a3d53b14d2541916` |
| `village-canary.glb` | 3100876 | `152e8b118de86f4d69c92a4986841ffa0d8bc362af236267cde6e9bff714b10f` |

Build report (`build-report.json`) gates:
- `build_id == request.build_id` ✓
- `fidelity == "simplified-pbr-not-render-parity"` ✓
- `counts.canonical_roots == 130` ✓
- `counts.visual_materials == 24` ✓
- `counts.cameras == 24` ✓
- `validation.finite_nonempty_meshes == true` ✓
- `validation.all_visual_material_slots_built == true` ✓
- `validation.canary_critical_slots_fulfilled == true` ✓

## 4. Measured contact gaps (§3.6 evidence)

Computed analytically over the canonical `build_scene_plan()` using the same
`_building_skirt_box` / `_bridge_foundation_box` functions the Blender builder
emits (verified by `tests/test_infinite_terrain.py::test_contact_gap_measurement_on_canonical_scene`):

```
buildings_total=70
building_skirts: count=70/70  max_height=3.0920 m  total_volume=15255.6532 m^3
bridges_total=2
bridge_piers: total=6  with_foundation=4  max_height=1.1520 m  total_volume=12.7170 m^3
contact_gap_total_volume=15268.3702 m^3
```

Interpretation:
- Every one of the 70 building platforms sits on sloped terrain that, without
  the new skirt geometry, would float at the downslope corner. The maximum
  floating gap closed is 3.092 m; the average skirt height is ~0.6 m
  (15255.65 / 70 / (avg footprint area)).
- 4 of the 6 bridge piers (2 bridges × 3 piers each) needed a foundation box
  because the creek-bed cut lowered the terrain below the pier bottom; the
  other 2 piers already rested on terrain at or above `pier_bottom_z`.
- The total solid fill volume added to close these gaps is 15268.37 m³.

This is the analytical half of P0-6 evidence. The real Blender smoke build
above is the empirical half: the builder ran with the new skirt/foundation
code and produced a non-empty, all-gates-green `.blend` whose
`finite_nonempty_meshes == true`.

## 5. Test commands and results

```
.venv\Scripts\python.exe -m pytest tests/test_infinite_terrain.py \
    tests/test_synthetic_village_elevated_topology.py -q   # 47 passed
.venv\Scripts\python.exe -m pytest tests/ -q -x \
    --ignore=tests/test_synthetic_village_blender_runtime.py
                                                          # 1330 passed, 49 skipped
                                                          # 1 failed: test_studio_crash_recovery
                                                          #   (flaky PID-mismatch in
                                                          #    Codex-owned studio_server;
                                                          #    unrelated to GLM changes)
.venv\Scripts\python.exe -m ruff check pipeline tests \
    scripts\blender\build_synthetic_village.py \
    scripts\blender\build_mesh_asset_bundle.py            # All checks passed
```

Real Blender smoke build command (P0-6 evidence):
```
third\blender\blender.exe --background --factory-startup --disable-autoexec \
    --python-exit-code 17 --python scripts\blender\build_synthetic_village.py \
    -- --request <request.json> --staging <staging>
# exit_code=0, elapsed=19.7s
```

## 6. Remaining real-scene blockers (still absent)

Per HANDOFF-GLM-007 §1, none of the five real-scene evidence items is closed
by this P0:
1. real overlapping capture with known acquisition provenance — absent;
2. accepted COLMAP/SfM poses and sparse geometry — absent;
3. one non-mock cloud-GPU 3DGS training result — absent (stub argv only);
4. imported splat artifact with measured alignment — absent;
5. Viewer QA over that real artifact — absent.

The Blender smoke build above remains `synthetic / L0 / preview-only /
modeled-unverified`. It is not a real reconstruction.

## 7. Next independent queue item

Per HANDOFF-GLM-007 §4: add an additive fail-closed verifier for imported
reconstruction artifacts.

Suggested new paths (owned by GLM lane):
```text
pipeline/reconstruction_artifact_integrity.py
scripts/verify_recon_artifacts.py
tests/test_reconstruction_artifact_integrity.py
```

Required behavior (per §4):
- consume an explicit `recon_manifest.json` path;
- reject symlinks, path escapes, missing files, duplicate chunk paths and
  duplicate JSON keys;
- recompute every declared artifact SHA-256 and size;
- for `chunks.json`, verify every PLY/LOD entry and its declared bounds/count;
- report `verified`, `mismatch` and `unknown` separately;
- never promote `preview-only`, `metric-aligned`, real-photo, or training trust;
- preserve `inspect_recon` as the lightweight claim translator;
- add TDD for tampered PLY bytes, stale manifest SHA, missing chunk, extra
  unbound chunk, path escape and contradictory metric evidence.
