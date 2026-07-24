# HANDOFF-GLM-007 — Real-scene gap and independent high-value queue

Date: 2026-07-24  
Last coordinated update: 2026-07-24, after Codex review of `f564e4f` and
`535d33e`
Owner: GLM lane  
Coordinator/reviewer: Codex

## 1. Current truth

The project is not finished.

- The currently accepted Blender production artifact is exact-218 and remains
  `synthetic / L0 / preview-only / modeled-unverified`.
- Codex has produced a newer machine-verified additive Batch24 exact-266
  candidate after rejecting the first visually defective vegetation pass:
  - build ID
    `937afaca82fcb12f841318f4ebc0bbcdd5388f3a45d6ca57243fb1154d825a66`;
  - `.blend` SHA-256
    `f3efbddc845f83e613f9a1c570306ded32aba1d3da0a0e40e8ce4fd9d61db4a0`;
  - request SHA-256
    `79b36742267272d0310b6b655c24b0e537566ed6fb4b5b5e368f39ed83aa4730`;
  - report SHA-256
    `1b523966c769f23e6531bddb30457276e627b9b5a8f8ee364be1d277bf4b07e1`;
  - perimeter plan SHA-256
    `ea6438b1dbb0628def1fc2fe31d02ac94db66f022175f9b022db519610e8bb96`;
  - exact roots `1..266`, with 48 non-empty overlay meshes and all `48/48`
    material, UV and surface records present.
- The replacement vegetation is now closed, deterministic low-poly geometry
  with measured route clearance, crown/trunk overlap and exact bark/canopy
  material bindings. The former giant timber-textured boxes are gone.
- Sixteen private reciprocal RGB views were rendered from that exact byte
  artifact and every PNG was SHA/size verified. They are audit-only, not the
  formal fresh-clearance, frame-identity, six-layer, target/seam-visibility and
  post-render-v2 acceptance run.
- Visual inspection still shows low-poly/lollipop vegetation, repeated or
  stretched materials, flat grey world/sky, terrain seams, sparse distant
  geometry, large proxy forms and some near-wall/ground framing. Therefore
  exact-266 is not accepted as a realistic scene and must not replace the
  exact-218 production baseline yet.
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
- **Completed and pushed:** P2a gradient-sky approximation and render-quality
  metrics, commit `66552b3`. This improved the synthetic base canary only; it did
  not rebuild or accept exact-266 and it added no real-scene evidence.
- **P2b review rejected as incomplete:** GLM commit `f564e4f` only delivered
  declared repeat-distance arithmetic and a draft probe. It did not satisfy the
  already-written requirements at lines 184–199: it mislabeled UV area as
  texels, skipped quads/ngons, omitted runtime/build bindings and did not run a
  real Blender probe or same-camera before/after render. Codex's follow-up now
  closes the unit naming, non-finite/duplicate/category gates, evaluated
  loop-triangle measurement and source/runtime SHA binding, and has run it
  against the exact-266 `.blend`. **P2b remains open** until GLM makes a
  base-builder-only mapping correction and produces bound before/after RGB and
  repeat-density evidence.
- **P3 review required corrections:** GLM commit `535d33e` added payload hashes
  but treated a missing integrity block as valid and did not reject missing
  rows, duplicate paths, `lod` disagreement, path escape or non-canonical
  manifests. Codex's follow-up adds those fail-closed contracts plus explicit
  `per_chunk_sha_verified=True/None/False`. After that follow-up is committed
  and pushed, P3 is accepted; GLM must not duplicate the fix.
- **Codex exact-266 formal audit executed:** commit `8cfd0d6` binds the real
  Blender adapter and exact-266 frame verifier. Fresh evidence is 15/16
  clearance passes, 15/15 rendered-frame local and post-render-v2 passes,
  **0/15 complete six-target visibility** and **3/15 two-seam visibility**.
  Camera 003 remains preflight-rejected. This is modeled-scene failure evidence,
  not real-scene acceptance. GLM must not edit the exact-266 caller paths.
- **GLM immediate continuation:** finish the P2b base-builder mapping correction
  with the same bound cameras before/after, then start P4's real COLMAP
  executable rehearsal. Do not wait for another Codex prompt after either
  checkpoint.
- **After each pushed item, start the next unblocked item in sections 6 and 7
  without waiting for Codex's exact-266 work.** Codex review may interrupt with
  corrections, but a pending review is not a reason to report that no work
  remains.
- Default continuation order is **P2a -> P2b -> P3 -> P4 -> next audited,
  unowned prerequisite**. “Tests are green”, “design is complete” and “waiting
  for Codex review” are checkpoints, not stop conditions.
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

## 5. Active P2 — P2a complete, P2b material audit in progress

Current owned paths:

```text
pipeline/synthetic_village/weather_profile.py
tests/test_synthetic_village_weather.py
scripts/blender/build_synthetic_village.py
tests/test_synthetic_village_blender_script.py
```

P2a was delivered in commit `66552b3`; completion evidence is in
`handoff/FEEDBACK-HANDOFF-GLM-007-p2a-gradient-sky-closure.md`.

Finish P2b as its own path-limited commit:

1. **P2a world/sky/haze — complete (`66552b3`)**
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
2. **P2b material distortion audit — active**
   - measure texel/UV scale variation on terrain, creek banks and long walls;
   - report each audited object/material and the measured min/max or percentile
     ratio;
   - correct only the base builder's material mapping, not Codex's exact-266
     overlay paths;
   - rerender the same bound cameras and report before/after RGB plus the
     distortion measurements. Do not claim real-photo texture parity.

   Before committing the current draft, close these review findings:

   - the Blender probe currently measures UV-coordinate area per square metre,
     not texels per metre; either bind the exact texture pixel dimensions and
     compute a real texel-density unit, or rename the field and all conclusions
     to `uv_area_per_m2` / repeat-density so the report does not overclaim;
   - iterate evaluated loop triangles or triangulate every polygon for
     measurement. Silently ignoring quads/ngons is not acceptable because the
     base scene contains non-triangle meshes;
   - reject non-finite numeric inputs, duplicate material IDs, missing/duplicate
     object identities, zero-area audited categories and empty required
     terrain/creek/long-wall categories;
   - bind the probe report to the source `.blend` SHA, build-report SHA, probe
     script SHA, exact object/material identities and Blender executable SHA;
   - prove the mapping correction with the same build input, cameras, poses,
     resolution and color management before/after. Record both RGB SHAs and
     measured ratios; a visual screenshot or pure-function report alone is not
     completion.

   When P2b is pushed, start P3 in the same work cycle. A pending Codex review is
   not a stop condition unless the review identifies a correctness or ownership
   conflict.

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

Preferred proposal order after P4:

1. close any machine-proven chunk/import integrity gap exposed by P3/P4;
2. rehearse the real capture-to-registration caller with a different immutable
   synthetic capture topology to detect overfitting, while preserving
   `synthetic-capture` trust;
3. audit the cloud-training request/result boundary against the real installed
   CLI or a disposable cloud GPU when credentials and budget exist;
4. add measured Viewer performance evidence for a content-addressed imported
   reconstruction without changing its provenance.

GLM should choose only an unowned item, write a short path/acceptance proposal
to Codex, and start its RED test or evidence collection in the same turn unless
the item requires new credentials, paid infrastructure or user-provided real
capture.

## 8. Reporting rule

Do not report “all high-value work is complete” while any of the five real
evidence items in section 1 is absent. At the end of each task, report:

- exact owned paths;
- commit and push status;
- test/lint/real-Blender commands and results;
- artifact/report SHA values;
- remaining real-scene blockers;
- the next independent queue item from this handoff.
