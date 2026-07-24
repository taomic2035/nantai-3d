# HANDOFF-GLM-007 — Real-scene gap and independent high-value queue

Date: 2026-07-24  
Last coordinated update: 2026-07-24, after Codex review of GLM `960ec55`,
`256ccf5` and `ed2dc84`
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
- Codex completed the fresh exact-266 formal run on that exact byte artifact:
  15/16 cameras passed clearance; all 15 allowed cameras produced bound
  six-layer + RGB artifacts and passed local/post-render-v2 distribution
  gates; **0/15** saw all six required module targets and only **3/15** saw
  both required seam targets. Camera 003 was rejected before rendering and no
  substitute frame was fabricated.
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

### Gap-to-owner matrix

| Goal dimension | Fresh evidence | Gap / owner |
|---|---|---|
| synthetic roaming | 15 bound RGB views render | target visibility `0/15`, seam visibility `3/15`, camera 003 rejected; Codex owns exact-266 caller/overlay correction |
| modeled geometry | deterministic exact-266 low-poly meshes | proxy forms, terrain seams, sparse distance and weak supports remain; Codex owns overlay, GLM may improve only the base builder |
| surface realism | synthetic material slots and a real Blender repeat-density probe | terrain `232.37x`, creek `40.48x`, long-wall `4.79x` UV-area variation; GLM P2b owns the base-builder correction and bound before/after proof |
| capture/SfM | real COLMAP failure-path rehearsals on low-texture synthetic subsets | no real overlapping capture and no successful recovered sparse model; GLM P5b must exercise a dense textured success-path candidate without GT poses |
| 3DGS appearance | caller contracts and stub/non-production evidence | no non-mock cloud-GPU training artifact; external GPU/credentials or user data are still required |
| scale/alignment | synthetic metre convention only | no measured real control-point/GPS alignment |
| real Viewer QA | synthetic previews only | no imported real splat exists for Viewer streaming/roaming QA |

The formal exact-266 report is
`handoff/FEEDBACK-HANDOFF-CODEX-028-batch24-exact266-perimeter-closure.md`.
GLM must cite it rather than repeating older “sixteen audit-only RGBs” status.

### Queue status and automatic continuation rule

- **Completed and pushed:** P0 creek/contact closure, commit `c1ca38b`.
- **Completed and pushed:** P1 reconstruction artifact integrity, commit
  `9b8c0d7`; Codex reran the focused suite (`26 passed, 3 skipped`) and Ruff.
- **Completed and pushed:** P2a gradient-sky approximation and render-quality
  metrics, commit `66552b3`. This improved the synthetic base canary only; it did
  not rebuild or accept exact-266 and it added no real-scene evidence.
- **P2b mapping committed, causal evidence still rejected:** GLM `acc320d`
  changes only the base builder and reports real Blender probes/RGBs, but its
  BEFORE artifact predates P0 and is not the same build input. BEFORE/AFTER have
  `572/554` audited objects and `70,010/39,548` terrain triangles. Byte-different
  RGB and a `232.37x -> 70.17x` terrain ratio cannot be attributed solely to the
  scale change while geometry and source differ. Keep the code correction, but
  P2b remains open until the causal A/B rerun in section 5 passes.
- **P3 accepted after Codex corrections:** GLM commit `535d33e` added payload
  hashes but treated a missing integrity block as valid and did not reject
  missing rows, duplicate paths, `lod` disagreement, path escape or
  non-canonical manifests. Codex follow-up `650c472` adds those fail-closed
  contracts, streamed hashing, atomic PLY writes, canonical manifests and
  explicit `per_chunk_sha_verified=True/None/False`. P3 is closed; GLM must not
  duplicate the fix.
- **Codex exact-266 formal audit executed:** commit `8cfd0d6` binds the real
  Blender adapter and exact-266 frame verifier. Fresh evidence is 15/16
  clearance passes, 15/15 rendered-frame local and post-render-v2 passes,
  **0/15 complete six-target visibility** and **3/15 two-seam visibility**.
  Camera 003 remains preflight-rejected. This is modeled-scene failure evidence,
  not real-scene acceptance. GLM must not edit the exact-266 caller paths.
- **P4 accepted as fail-closed rehearsal:** `18a1b48` ran real COLMAP 4.1.0
  through the production caller on 24 immutable untextured canary renders.
  Mapper produced `0/24` registered images and no sparse model; the caller
  raised and promoted no trust. This closes only the executable failure-path
  rehearsal, not real SfM.
- **GT metadata fix requires correction:** `960ec55` made production
  `c2w_opencv` readable, but silently prefers `measured_c2w_opencv` when both
  aliases disagree and accepts malformed/non-finite matrices. The synthetic
  import-chain evidence is useful, but the reader remains fail-open until
  section 8 is complete.
- **P5/P6 accepted only as failure-path smoke:** `256ccf5` adds orbit/adversarial
  matching-vs-mapper failures; `ed2dc84` adds an eight-frame OpenCV video decode
  and sequential-matcher failure. Both reuse the same low-texture v1 images and
  every COLMAP run registers `0` images. They do not close topology-overfit,
  successful SfM, frame-sampling, `max_frames`, long-video or backend-binding
  evidence.
- **GLM immediate continuation:** correct `960ec55`, rerun P2b causally, then
  execute P5b, P6b and P7 in sections 9–11. Do not wait for another Codex prompt
  after any checkpoint.
- **After each pushed item, start the next unblocked item in sections 8–11
  without waiting for Codex's exact-266 work.** Codex review may interrupt with
  corrections, but a pending review is not a reason to report that no work
  remains.
- Current continuation order is **GT fail-closed correction -> P2b causal rerun
  -> P5b dense textured SfM -> P6b sampled video -> P7 recovered-pose training
  -> next audited prerequisite**. P0, P1, P2a, P3 and the P4–P6 failure-path
  smokes are closed. “Tests are green”, “design is complete” and “waiting for
  Codex review” are checkpoints, not stop conditions.
- Do not say “all high-value tasks are complete” while either:
  - any corrective or active item in sections 5 or 8–11 is unfinished; or
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

Legacy `chunks.json` files still have no per-payload SHA/size and must report
that byte verification is unknown. Fresh complete manifests are closed by P3
and can prove full/LOD PLY bytes.

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

   `acc320d` may retain the base-builder correction, but its A/B evidence is not
   causal because BEFORE used the old `4f38ecf4...` artifact while AFTER used
   `704a0b6c...`; object and triangle populations changed. Repeat the proof as
   follows:

   - extract the parent `18a1b48` version of
     `scripts/blender/build_synthetic_village.py` to a private, SHA-bound path;
     do not create a branch or worktree;
   - run that frozen parent script and the current `acc320d` script with the
     identical request, seed, registry, topology, resolution, renderer, color
     management and cameras;
   - require equal stable object identities, category object counts and
     per-category triangle counts before comparing UV ratios or RGBs;
   - bind both script SHAs, build requests/reports, `.blend` files, camera
     matrices, probe reports and RGBs;
   - if geometry or camera bindings differ, report the run as invalid rather
     than calling the material change an improvement.

   When this causal P2b proof is committed, start or resume P5 in the same work
   cycle. A pending Codex review is not a stop condition unless it identifies a
   correctness or ownership conflict.

This work may touch the base Blender builder only after the creek/contact P0 is
committed. It must not edit the exact-266 overlay paths.

Codex's exact-266 formal sixteen-camera preflight, six-layer,
target/seam-visibility and post-render-v2 chain is complete and rejected on
task-specific visibility. GLM must not edit that caller or wait for its next
overlay iteration: finish P2b, push it, then continue with section 7.

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

## 6. Completed P3 — bind every streamed chunk and LOD payload

GLM delivered the initial implementation in `535d33e`; Codex closed the
fail-closed review findings in `650c472`. Do not reopen or duplicate this item
without a measured regression.

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

The closed contract preserves legacy manifests as explicit unknown, requires
complete integrity rows for verified status, streams SHA-256 over payloads and
rejects missing/duplicate/escaping/disagreeing full or LOD paths.

## 7. Completed P4 — real COLMAP executable rehearsal on synthetic captures

Commit `18a1b48` records a real COLMAP 4.1.0 production-caller run on 24
immutable untextured canary renders. The result was `0/24` registered images,
zero sparse models and a fail-closed `RuntimeError`; no partial registration or
trust promotion was emitted. This is accepted failure-path evidence only. It is
**not** real-scene evidence and does not predict textured/overlapping captures.

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

The preferred caller is the production `pipeline.registration.colmap_register`
boundary, optionally reached through `scripts/reconstruct_local.py`. The result
must prove that the real executable was used; a direct GT-to-COLMAP conversion
or mock/stub is not a substitute.

P4 evidence is committed. Complete the causal P2b rerun, then continue directly to P5.

## 8. Immediate correction — camera-metadata alias fail-closed

Commit `960ec55` restores production-v1 `c2w_opencv` consumption and proves a
valuable synthetic GT-pose import/training/chunk rehearsal. It is not P5 SfM
evidence, and its alias reader must be corrected before further reuse:

1. if `measured_c2w_opencv` and `c2w_opencv` both exist, parse both and reject
   any numeric disagreement instead of selecting one by precedence;
2. require a finite numeric `4x4` homogeneous matrix with bottom row
   `[0, 0, 0, 1]` before indexing or conversion;
3. reject missing, ragged, string/object, NaN/Inf and non-homogeneous values with
   an explicit camera ID in the error;
4. add pure tests for each case plus a full `main()` integration test using
   production v1 metadata with only `c2w_opencv`;
5. retain the explicit declaration that this tool injects GT poses and bypasses
   SfM. Never use its output as P5/P6 registration evidence.

Use only:

```text
scripts/canary_gt_to_colmap.py
tests/test_canary_gt_to_colmap.py
```

Publish the correction as one path-limited commit, then complete the P2b causal
A/B rerun in section 5 and continue immediately to P5b.

## 9. Active P5b — dense textured real-COLMAP success-path candidate

Commit `256ccf5` is accepted as an additional failure-stage matrix, not as
successful topology-overfit closure. Its orbit/adversarial sets are 8/4-image
subsets of the same low-texture v1 capture and all register `0` images.

The next rehearsal must move toward the real path rather than repeat a known
failure:

1. render or select an immutable **textured**, densely overlapping ordered or
   orbital Blender sequence with at least 24 images, preferably 48–120, at
   human-eye height and at least 1280x720;
2. bind source build/blend/report, camera IDs and matrices, renderer, every RGB
   SHA, COLMAP executable/version, exact argv and timeout;
3. run `check_capture` and the real production COLMAP caller. Do not use
   `canary_gt_to_colmap.py`, GT poses, fake executables or relaxed trust gates;
4. record per-frame feature counts, verified adjacent-pair matches, connected
   sparse model count, registered/total images, sparse points and all report
   SHAs;
5. success-path acceptance requires at least one sparse model, nonzero sparse
   points and `>=80%` registered images. If it fails, keep the failure evidence,
   repair capture overlap/texture and rerun; do not weaken COLMAP quality gates;
6. any success remains
   `synthetic-capture / sfm-local / arbitrary-units / unaligned` and must not be
   reported as real capture or metric alignment.

After P5b is committed, encode the same successful dense ordered sequence for
P6b. If P5b cannot succeed after honest capture corrections, report the exact
failure and continue P6b caller sampling evidence without claiming SfM closure.

## 10. Active P6b — sampled and bounded video-input proof

Commit `ed2dc84` is accepted as an eight-frame OpenCV decode/sequential-matcher
smoke only. Source FPS equaled requested FPS, `8 < max_frames=20`, blur filtering
was disabled, and the same low-texture sequence produced zero matches. It does
not prove actual subsampling, truncation, realistic filtering or the long-video
resource boundary.

1. encode the P5b dense ordered sequence into a video with at least 120 source
   frames and a source FPS strictly higher than the requested extraction FPS;
2. set `max_frames` below the number that sampling would otherwise produce, so
   both temporal subsampling and truncation execute in one real run;
3. bind source frame indices/SHAs, video SHA, codec/container, duration,
   resolution, source/requested FPS, `max_frames`, extracted order/SHAs, elapsed
   time and peak/output disk bytes;
4. bind the actual OpenCV video backend using `cv2.getBuildInformation()` and
   `VideoCapture.getBackendName()`. Do not claim an external FFmpeg CLI/version
   unless that executable was actually invoked and its argv captured;
5. exercise the production `scripts/reconstruct_local.py`/`pipeline.ingest`
   boundary and prove the sequential matcher from machine evidence, not a
   manually supplied `ordered=True` assertion;
6. keep the normal blur policy for the textured sequence or report each rejected
   frame and the explicit operator override. Never generalize this bounded run
   into “1 GB / 20 minutes completed”.

After P6b is committed, continue directly to P7.

## 11. Queued P7 — recovered-pose synthetic training and private import

This is the highest-value credential-free bridge after P5b succeeds:

1. consume the **real COLMAP-recovered** P5b sparse model, not GT-injected poses;
2. run the installed real Brush trainer on the same synthetic RGBs with bounded
   iterations and bind executable, argv, inputs, timestamps and PLY SHA;
3. normalize, prepare import and chunk the result in a new private output root;
   do not touch `web/data/` or Codex-owned Viewer paths;
4. verify every import/chunk payload SHA and preserve
   `synthetic-capture / sfm-local / arbitrary-units / unaligned` provenance;
5. hand Codex the immutable private manifest/PLY/chunk root plus machine report
   for Viewer streaming and roaming QA;
6. if training fails, publish the machine failure and continue with the highest
   unowned caller/integrity defect it exposes. Do not substitute GT poses and do
   not claim cloud-GPU or real-photo training.

P7 will still not close the five real-scene evidence items, but it will prove
that the non-GT capture -> real SfM -> real local training -> import boundary is
wired before real user footage arrives.

After P7, reread
`handoff/AUDIT-2026-07-22-real-3d-scene-gap-assessment.md` and immediately start
the highest-value unowned prerequisite. Prefer real installed training CLI
validation or a concrete Viewer evidence proposal to Codex; paid cloud work
still requires credentials/budget.

GLM should choose only an unowned path, start its RED test or evidence
collection in the same turn, and never stop merely because a review is pending.

## 12. Reporting rule
Do not report “all high-value work is complete” while any of the five real
evidence items in section 1 is absent. At the end of each task, report:

- exact owned paths;
- commit and push status;
- test/lint/real-Blender commands and results;
- artifact/report SHA values;
- remaining real-scene blockers;
- the next independent queue item from this handoff.
