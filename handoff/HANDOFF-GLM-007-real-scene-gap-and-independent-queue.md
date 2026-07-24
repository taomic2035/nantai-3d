# HANDOFF-GLM-007 — Real-scene gap and independent high-value queue

Date: 2026-07-24  
Last coordinated update: 2026-07-24, after commits `c1ca38b` and `9b8c0d7`
Owner: GLM lane  
Coordinator/reviewer: Codex

## 1. Current truth

The project is not finished.

- The currently accepted Blender production artifact is exact-218 and remains
  `synthetic / L0 / preview-only / modeled-unverified`.
- Codex has produced the first machine-verified additive Batch24 exact-266
  candidate:
  - build ID
    `db52d46befc727e2d4c923b4922743a1db2818d266a523ddf976651d37abcf89`;
  - `.blend` SHA-256
    `ed119c0e8147dc7cee1466576b6c79da3e71e20a1e76237654a538b1cedae211`;
  - perimeter plan SHA-256
    `ea6438b1dbb0628def1fc2fe31d02ac94db66f022175f9b022db519610e8bb96`;
  - exact roots `1..266`, with 48 non-empty, material-bound overlay meshes.
- Sixteen private reciprocal RGB views were rendered from that exact byte
  artifact. They are audit-only, not the formal six-layer/post-render
  acceptance run. A first candidate was rejected because terrain, retaining
  structures and vegetation blocked the route; the current candidate leaves
  the centerline open.
- Visual inspection still shows blocky vegetation, repeated/stretched
  materials, flat grey world/sky, terrain seams and sparse/distant proxy
  geometry. Therefore exact-266 is not accepted as a realistic scene and must
  not replace the exact-218 production baseline yet.
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

### Queue status and automatic continuation rule

- **Completed and pushed:** P0 creek/contact closure, commit `c1ca38b`.
- **Completed and pushed:** P1 reconstruction artifact integrity, commit
  `9b8c0d7`; Codex reran the focused suite (`26 passed, 3 skipped`) and Ruff.
- **In progress:** P2 base-scene world/sky/material audit. The current uncommitted
  `weather_profile.py` and weather tests are only the first RED/contract slice;
  pure-function tests alone do not complete P2.
- **After each pushed item, start the next unblocked item in sections 6 and 7
  without waiting for Codex's exact-266 work.** Codex review may interrupt with
  corrections, but a pending review is not a reason to report that no work
  remains.
- Do not say “all high-value tasks are complete” while either:
  - any item in sections 5–7 is unfinished; or
  - any of the five real-scene evidence items above is absent.

## 2. Codex-owned paths — do not edit

Codex currently owns:

```text
pipeline/synthetic_village/perimeter_closure_module.py
pipeline/synthetic_village/perimeter_closure_runtime.py
scripts/blender/apply_perimeter_closure_modules.py
pipeline/synthetic_village/perimeter_closure_audit.py
scripts/blender/render_perimeter_closure_audit.py
scripts/synthetic_village.py
tests/test_synthetic_village_perimeter_closure_*.py
tests/test_synthetic_village_cli.py
docs/superpowers/specs/2026-07-23-batch24-perimeter-closure-overlay-design.md
docs/superpowers/plans/2026-07-24-batch24-perimeter-closure-overlay.md
```

Do not modify these paths or their schemas without a new coordination note.

## 3. Completed P0 — creek/contact work (`c1ca38b`)

This item is closed and must not be reopened without a measured regression:

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

Completion feedback:
`handoff/FEEDBACK-HANDOFF-GLM-007-p0-creek-contact-closure.md`.

## 4. Completed P1 — real reconstruction artifact integrity (`9b8c0d7`)

The additive verifier now exists in:

```text
pipeline/reconstruction_artifact_integrity.py
scripts/verify_recon_artifacts.py
tests/test_reconstruction_artifact_integrity.py
```

Completion feedback:
`handoff/FEEDBACK-HANDOFF-GLM-007-p1-artifact-integrity-closure.md`.

Known remaining limitation: legacy `chunks.json` has no per-payload SHA/size,
so the verifier can prove path and structural closure but cannot prove chunk
PLY bytes. Section 6 is the queued closure for that limitation.

## 5. Active P2 — base-scene world, haze and material audit

Current owned paths:

```text
pipeline/synthetic_village/weather_profile.py
tests/test_synthetic_village_weather.py
scripts/blender/build_synthetic_village.py
tests/test_synthetic_village_blender_script.py
```

Finish it as two path-limited commits:

1. **P2a world/sky/haze**
   - keep the current pure deterministic gradient/haze contract;
   - make the base Blender builder actually consume that contract;
   - retain `synthetic=true`, `L0`, `preview-only` and a name that says
     `approximation`; never label the generated background as HDRI, physical
     atmosphere or real lighting;
   - fail closed on non-finite/out-of-range node inputs;
   - run a real headless Blender build and render before/after RGB from the
     identical blend input, camera ID, pose, resolution, color management and
     weather request;
   - report request/build/blend/RGB SHA-256 values and measured luminance
     percentiles, clipped-black ratio, clipped-white ratio and background pixel
     ratio. A prettier screenshot without these bindings is not completion.
2. **P2b material distortion audit**
   - measure texel/UV scale variation on terrain, creek banks and long walls;
   - report each audited object/material and the measured min/max or percentile
     ratio;
   - correct only the base builder's material mapping, not Codex's exact-266
     overlay paths;
   - rerender the same bound cameras and report before/after RGB plus the
     distortion measurements. Do not claim real-photo texture parity.

This work may touch the base Blender builder only after the creek/contact P0 is
committed. It must not edit the exact-266 overlay paths.

Codex's next exact-266 work is the formal sixteen-camera preflight, six-layer,
target/seam visibility and post-render-v2 chain. GLM must not wait on that work:
finish P2a, push it, then finish P2b and continue with section 6.

Focused minimum verification for P2:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_synthetic_village_weather.py `
  tests/test_synthetic_village_blender_script.py -q
.venv\Scripts\python.exe -m ruff check `
  pipeline/synthetic_village/weather_profile.py `
  scripts/blender/build_synthetic_village.py `
  tests/test_synthetic_village_weather.py `
  tests/test_synthetic_village_blender_script.py
third\blender\blender.exe --background --version
```

The final evidence must also include the actual build/render commands used;
`blender.exe --version` alone is not a real-build gate.

## 6. Queued P3 — bind every streamed chunk and LOD payload

Start immediately after P2b. This is additive and does not require real photos,
cloud GPU or Codex's exact-266 acceptance.

Owned paths:

```text
pipeline/spatial_chunk.py
pipeline/reconstruction_artifact_integrity.py
scripts/chunk_reconstruction.py
tests/test_spatial_chunk.py
tests/test_reconstruction_artifact_integrity.py
```

Required behavior:

1. Preserve the existing `ply_file` and `lod` filename fields so the current
   Viewer remains compatible.
2. Add a deterministic per-chunk integrity block that binds every full and LOD
   payload to its existing relative path, exact byte size and SHA-256.
3. Hash the bytes after each atomic write; reject path aliases, duplicate
   payload paths, missing files and any disagreement between the integrity
   block and `ply_file`/`lod`.
4. Extend the verifier so complete integrity blocks produce
   `per_chunk_sha_verified=true`; old manifests remain readable but explicitly
   report `unknown`, never `verified`.
5. Add TDD for one-byte tampering in full and LOD PLYs, swapped hashes, stale
   sizes, duplicate paths, missing integrity rows and cross-platform canonical
   `chunks.json` bytes.
6. Do not promote geometry, metric alignment, real-photo or training trust.

Minimum verification:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_spatial_chunk.py `
  tests/test_reconstruction_artifact_integrity.py -q
.venv\Scripts\python.exe -m ruff check `
  pipeline/spatial_chunk.py `
  pipeline/reconstruction_artifact_integrity.py `
  scripts/chunk_reconstruction.py `
  tests/test_spatial_chunk.py `
  tests/test_reconstruction_artifact_integrity.py
```

Publish this as its own path-limited commit and then continue with section 7.

## 7. Queued P4 — real COLMAP executable rehearsal on synthetic captures

This item tests the actual local executable/caller boundary while real photos
are still absent. It is **not** real-scene evidence.

1. Select one immutable private Blender RGB capture set with enough overlap;
   record every input image SHA, camera/frame identity and the source build
   SHA. Do not use independent image2 design references.
2. Run `scripts/check_capture.py`, then the real
   `third/colmap/bin/colmap.exe` through the repository's production
   registration path. Do not use a fake executable, GT-pose injection or mock
   registration.
3. Keep a bounded timeout and record COLMAP version, exact argv, exit status,
   registered/total images, sparse point count and output/report SHA values.
4. If registration fails, publish the machine failure evidence instead of
   relaxing gates until it says success.
5. Any success remains
   `synthetic-capture / sfm-local / arbitrary-units / unaligned`; it does not
   close real capture, cloud-GPU training, metric alignment or Viewer QA.
6. Add code only when the rehearsal exposes a reproducible caller or
   fail-closed defect; use TDD and a separate path-limited commit for that
   defect. Otherwise deliver an evidence-only feedback document.

After P4, reread
`handoff/AUDIT-2026-07-22-real-3d-scene-gap-assessment.md`. If real input is
still absent, propose and start the highest-value unowned prerequisite that
reduces one of its seven dimensions. Do not self-declare the queue exhausted;
send Codex a concrete next-item proposal with paths and acceptance evidence.

## 8. Reporting rule

Do not report “all high-value work is complete” while any of the five real
evidence items in section 1 is absent. At the end of each task, report:

- exact owned paths;
- commit and push status;
- test/lint/real-Blender commands and results;
- artifact/report SHA values;
- remaining real-scene blockers;
- the next independent queue item from this handoff.
