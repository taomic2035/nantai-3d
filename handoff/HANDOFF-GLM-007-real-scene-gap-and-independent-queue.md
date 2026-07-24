# HANDOFF-GLM-007 — Real-scene gap and independent high-value queue

Date: 2026-07-24  
Owner: GLM lane  
Coordinator/reviewer: Codex

## 1. Current truth

The project is not finished.

- The current verified Blender production artifact is exact-218 and remains
  `synthetic / L0 / preview-only / modeled-unverified`.
- Codex is building a separate additive Batch24 exact-266 perimeter overlay.
  Its first real Blender RGB evidence does not exist yet.
- A visually better exact-266 scene will still not be a real reconstruction.
- The decisive real-scene evidence is still absent:
  1. real overlapping capture with known acquisition provenance;
  2. accepted COLMAP/SfM poses and sparse geometry;
  3. one non-mock cloud-GPU 3DGS training result;
  4. imported splat artifact with measured alignment;
  5. Viewer QA over that real artifact.

Image2 design references cannot satisfy any of these five items. They are
replaceable modeling inputs only and remain forbidden as multiview training
evidence.

## 2. Codex-owned paths — do not edit

Codex currently owns:

```text
pipeline/synthetic_village/perimeter_closure_module.py
pipeline/synthetic_village/perimeter_closure_runtime.py
scripts/blender/apply_perimeter_closure_modules.py
pipeline/synthetic_village/perimeter_closure_audit.py
scripts/blender/render_perimeter_closure_audit.py
tests/test_synthetic_village_perimeter_closure_*.py
docs/superpowers/specs/2026-07-23-batch24-perimeter-closure-overlay-design.md
docs/superpowers/plans/2026-07-24-batch24-perimeter-closure-overlay.md
```

Do not modify these paths or their schemas without a new coordination note.

## 3. Immediate P0 — finish the current creek/contact work

Continue the already-started GLM work in:

```text
pipeline/synthetic_village/infinite_terrain.py
pipeline/synthetic_village/elevated_topology.py
scripts/blender/build_synthetic_village.py
scripts/blender/build_mesh_asset_bundle.py
tests/test_infinite_terrain.py
tests/test_synthetic_village_elevated_topology.py
```

Required completion evidence:

1. analytic creek-cut math and Blender-local duplicate stay numerically equal
   at centreline, bank edge, taper midpoint, endpoints and degenerate segments;
2. non-finite coordinates, negative widths, zero/negative bank margin and
   fewer-than-two polyline points fail closed;
3. building skirts and bridge foundations use measured terrain samples and do
   not create inverted/zero-height boxes;
4. walkable nodes remain outside the water channel, while intentional bridge
   crossings are not rejected merely for crossing the creek in plan view;
5. mesh-asset bundle template builds remain compatible;
6. run a fresh real Blender smoke/build, record artifact/report SHA values and
   measured contact gaps; screenshots alone are not acceptance evidence.

Commit and push this P0 as a path-limited change. Do not include Codex files or
unowned working-tree paths.

## 4. Next independent P1 — real reconstruction artifact integrity

After P0, add an additive fail-closed verifier for imported reconstruction
artifacts. Prefer a new module/command rather than changing Viewer or Studio
code.

Required behavior:

- consume an explicit `recon_manifest.json` path;
- reject symlinks, path escapes, missing files, duplicate chunk paths and
  duplicate JSON keys;
- recompute every declared artifact SHA-256 and size;
- for `chunks.json`, verify every PLY/LOD entry and its declared bounds/count;
- report `verified`, `mismatch` and `unknown` separately;
- never promote `preview-only`, `metric-aligned`, real-photo, or training trust;
- preserve `inspect_recon` as the lightweight claim translator unless a
  separate reviewed design explicitly changes it;
- add TDD for tampered PLY bytes, stale manifest SHA, missing chunk, extra
  unbound chunk, path escape and contradictory metric evidence.

Suggested new paths:

```text
pipeline/reconstruction_artifact_integrity.py
scripts/verify_recon_artifacts.py
tests/test_reconstruction_artifact_integrity.py
```

This closes a known real-data gap: `inspect_recon` currently checks manifest
claims and consistency but deliberately does not rehash PLY/chunk bytes.

## 5. Next independent P1 — base-scene world and material audit

If P1 artifact integrity is already owned elsewhere, take this task instead:

- add a deterministic synthetic world/sky and distance haze to the base
  Blender builder;
- keep it explicitly synthetic and do not call it HDRI or real lighting;
- add measured render exposure/background-validity gates;
- audit repeated/stretched materials on terrain, creek banks and long walls;
- produce before/after RGB with identical camera/frame identity and report
  content SHA values.

This work may touch the base Blender builder only after the creek/contact P0 is
committed. It must not edit the exact-266 overlay paths.

## 6. Reporting rule

Do not report “all high-value work is complete” while any of the five real
evidence items in section 1 is absent. At the end of each task, report:

- exact owned paths;
- commit and push status;
- test/lint/real-Blender commands and results;
- artifact/report SHA values;
- remaining real-scene blockers;
- the next independent queue item from this handoff.

