# H3-A · AI 模拟 4K 材质与 KTX2 运行时

Date: 2026-07-19
Status: user approved H3-A and confirmed this design direction
Scope: AI-generated material sources, deterministic 4K authoring masters,
PBR derivation, KTX2 delivery, verified Viewer fallback, and release payload

## 1. Decision

H3-A improves the highest-impact near-view materials without changing H2
geometry, coordinates, layout, weather semantics, or trust.

The first slice covers exactly eight opaque material slots:

1. `material-weathered-timber-01`
2. `material-dark-timber-01`
3. `material-gray-roof-tile-01`
4. `material-fieldstone-01`
5. `material-dry-stone-wall-01`
6. `material-rammed-earth-01`
7. `material-packed-earth-01`
8. `material-terrace-soil-01`

For each slot, the pipeline retains an AI-generated native source, creates a
deterministic seamless 4096 × 4096 authoring master, derives base colour,
normal, and ORM maps, builds complete mip chains, and compiles a KTX2 runtime
variant. The exact H2 1K PNG material remains the fallback variant.

The Viewer selects one material profile for the whole session:

```text
h3-ai-ktx2-4k
    or
h2-png-1k-fallback
```

It never mixes profiles silently inside one world or chunk set.

## 2. Truth and provenance boundary

Every H3-A source and artifact remains:

```text
synthetic=true
ai_generated=true
real_photo_textures=false
geometry_usability=preview-only
metric_alignment=false
verification_level=L0
```

AI-generated texture appearance is not:

- a photograph of the represented place;
- a calibrated material scan;
- measured reflectance, roughness, displacement, or scale;
- evidence that the geometry matches a real object;
- evidence that the source prompt can reproduce identical bytes.

Trust begins only after the generated output bytes exist. Prompt identity and
generation metadata describe how a candidate was requested, but the captured
output SHA-256 is the source identity.

The full prompt is retained. Generator product/version fields are recorded only
when the generation response exposes them. Missing generator metadata remains
explicitly `unknown`; it is never inferred from a filename, tool name, or date.

## 3. Measured baseline

The accepted H2 runtime currently has:

- exact mesh bundle
  `866c4c1cb8219c12ae0c20f176e65ac39311bfc69e36b360b03eaa6fa5977ee6`;
- exact material bundle
  `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13`;
- 11 assets × 3 LODs;
- 45 content-addressed 1024 × 1024 PNG dependencies;
- 39 decoded bitmap / semantic GPU texture objects in the near-view browser
  acceptance state;
- 67 WebGL geometries after static primitive compaction;
- 16.7 ms median and 18.6 ms p95 frame intervals in the accepted test viewport.

Five files under `input/photo_*.jpg` are only 320 × 240 soil-colour test
frames. They are insufficient as H3 photo sources and must not be upscaled and
relabeled as high-resolution texture evidence.

The only downloaded release is the 2026-07-16 synthetic canary. It predates H2
and is not an H3 payload.

## 4. Goals

1. Improve near-view material structure, colour variation, edge wear, and
   perceived scale while retaining exact material slot identities.
2. Preserve H2 geometry and prove KTX2/PNG variants use identical topology and
   UVs.
3. Deliver true 4096 × 4096 authored map payloads without loading uncompressed
   4K RGBA PNGs into every active material.
4. Keep AI source lineage and every derived byte content-addressed.
5. Make Viewer capability selection, fallback reason, compressed memory, and
   active material profile machine-readable.
6. Preserve arbitrary-coordinate chunk loading and six reversible weather
   modes.
7. Produce a self-contained, checksum-verifiable release archive that can be
   downloaded to a fresh machine.
8. Activate H3 only when visual, integrity, memory, and frame-time gates all
   pass.

## 5. Non-goals

- H3-A does not replace building, roof, prop, or vegetation topology. That is
  H3-B.
- It does not add interiors, collision, animation, physical mud, water flow,
  snow accumulation, or destructible surfaces.
- It does not change 3DGS colour, relight splats, train a reconstruction, or
  promote any geometry to measured/metric-aligned.
- It does not cover foliage alpha atlases in the first slice. Opaque hero
  materials must pass before a separately reviewed foliage source pack.
- It does not publish a public GitHub release without explicit external
  publication authorization.
- It does not claim that a 4096 master has 4096 pixels of native model output.

## 6. Considered approaches

### 6.1 Approved: 4K authoring master + KTX2 primary + H2 PNG fallback

KTX2 can carry mipmapped universal compressed textures, while Three.js
`KTX2Loader` transcodes Basis Universal data to a GPU-supported format. The
Viewer can therefore retain 4K authored detail without creating a multi-GB
uncompressed PNG working set.

This adds a versioned bundle/runtime contract and vendored transcoder, but it
is the only approach that preserves the requested 4K direction and a bounded
browser memory budget.

### 6.2 Rejected: direct 4K PNG replacement

Thirty-nine 4096 × 4096 RGBA textures require roughly 2.44 GiB before mip
overhead, renderer duplication, geometry, or frame buffers. This would risk
browser instability and make arbitrary-coordinate streaming less credible.

### 6.3 Fallback only: 2K PNG

2K PNG is simpler and may be used for diagnosis, but it does not satisfy the
approved 4K runtime direction. It remains a recovery option if the KTX2 tool
chain cannot be verified, not the H3-A success state.

## 7. AI material source pack

### 7.1 Candidate generation

Each slot requests three candidates with a slot-specific prompt derived from a
tracked prompt template. Prompts require:

- orthographic or near-orthographic material coverage;
- flat, diffuse illumination with no hard cast shadows;
- no text, logos, objects, borders, perspective corners, or scene horizon;
- material scale cues appropriate to the slot;
- sufficient stochastic variation for later seamless quilting;
- no claim of being a real Nantai photograph.

Generation is intentionally nondeterministic. Reproducibility begins at the
captured bytes, not at the prompt.

### 7.2 Candidate review

A deterministic source audit measures dimensions, colour mode, alpha, clipping,
contrast, edge energy, dominant perspective, and opposite-edge disagreement.
The audit can reject candidates but cannot select the visually best one alone.

A fixed contact sheet presents all three candidates per slot under identical
scale and labels. Codex records a visual choice and reason. Rejected bytes stay
outside the published source pack.

### 7.3 Source pack schema

The strict canonical manifest is:

```json
{
  "schema_version": "nantai.h3-ai-material-source-pack.v1",
  "source_pack_id": "<sha256>",
  "synthetic": true,
  "ai_generated": true,
  "real_photo_textures": false,
  "generation_policy_id": "h3-ai-material-candidates-v1",
  "records": [
    {
      "slot_id": "material-weathered-timber-01",
      "prompt": "<full prompt>",
      "prompt_sha256": "<sha256>",
      "generator_product": "openai-image-generation",
      "generator_version": null,
      "generator_version_evidence": "not-exposed-by-generation-response",
      "native_source": {
        "object_path": "sources/<sha256>.<ext>",
        "sha256": "<sha256>",
        "bytes": 1,
        "width": 1,
        "height": 1,
        "media_type": "image/png"
      },
      "selection": {
        "candidate_count": 3,
        "selected_candidate_sha256": "<sha256>",
        "review_kind": "human-visual-review",
        "trust_effect": "none-appearance-only"
      },
      "rights_review": {
        "status": "private-project-use-only",
        "evidence": "user-approved-ai-generation-workflow",
        "public_release_authorized": false
      }
    }
  ]
}
```

Canonical bytes exclude filesystem paths, timestamps, machine names, temporary
URLs, and generation request IDs. Payload paths are relative, slash-normalized,
and content-addressed.

`generator_version` is either the exact response value or JSON `null`; the
evidence field explains why it is absent. `rights_review` records workflow
authorization, not a legal conclusion. It cannot be changed to public-release
authorization without explicit user approval and a separate release review.
Angle-bracket values above are typed schema examples, not unresolved build
inputs.

## 8. Deterministic 4K authoring master

The native generated image is retained unchanged. A separate deterministic
algorithm creates the 4096 × 4096 seamless master:

1. validate and convert the selected candidate to an explicit colour space;
2. remove only bounded low-frequency illumination gradients;
3. select content patches using SHA-seeded quilting;
4. minimize seam energy in overlap bands;
5. enforce opposite-edge continuity;
6. add bounded, source-derived macro variation;
7. write canonical RGB8 PNG with nonsemantic metadata removed.

The master manifest records:

- native source SHA and dimensions;
- master SHA and exact 4096 × 4096 dimensions;
- derivation algorithm/module SHA;
- colour-space conversion;
- quilting patch and overlap sizes;
- edge-band width;
- measured edge discontinuity;
- full/interior SSIM against the selected native source at matched scale.

The UI and reports call this a `4096 authored master`, never a `native 4K AI
output` unless the generation response actually supplied 4096 × 4096 bytes.

## 9. PBR derivation

Each 4K master produces:

- base colour: sRGB RGB8;
- tangent-space normal: linear RGB8;
- ORM: linear RGB8, with occlusion/roughness/metallic channel semantics;
- a complete 4096 → 1 mip chain.

Normal and ORM are heuristic derivatives. Their manifest declares:

```text
material_measurement=none
normal_derivation=synthetic-image-gradient
roughness_derivation=synthetic-luminance-statistics
metalness_policy=slot-constant-or-zero
```

No value is described as physically calibrated.

The replacement contract retains existing:

- `slot_id`;
- nominal tile metres;
- UV policy;
- alpha mode;
- normal strength;
- roughness centre;
- metallic policy.

Changing any source, derivation parameter, encoder option, or map byte changes
the material bundle ID.

## 10. KTX2 compilation and validation

The acceptance toolchain is the official Khronos KTX-Software `v4.4.2`
release. On the current Apple Silicon development machine, the expected
installer artifact is `KTX-Software-4.4.2-Darwin-arm64.pkg`. Its downloaded
bytes and SHA-256 must be measured and recorded before installation; the
specification does not guess a digest that has not yet been observed.

The Viewer transcoder is a separate lock: `KTX2Loader.js` and its Basis
JS/WASM payload come from the exact same Three.js revision already vendored by
the project. A latest-version Khronos web transcoder cannot be substituted
silently for that matching Three.js dependency closure.

- base colour and normal use high-quality UASTC;
- ORM may use ETC1S only if decoded channel-error gates pass, otherwise UASTC;
- base-colour transfer function is sRGB;
- normal and ORM are linear;
- all files contain the full mip chain;
- output media type is `image/ktx2`.

Every output must pass:

- official KTX validation;
- 12-byte KTX2 identifier check;
- dimensions and level-count audit;
- Data Format Descriptor colour/transfer audit;
- decode-to-reference comparison;
- exact bytes and SHA-256 validation.

The encoder version, binary SHA, command options, source-map SHA, and decoded
quality measurements are recorded. Platform-specific byte drift fails
cross-platform publication; it is not fixed by re-registering the drifted
bytes.

## 11. Bundle contracts

### 11.1 Material bundle v2

`nantai.synthetic-village.derived-material-bundle.v2` binds:

- the H3 AI source pack ID;
- all 4K master/base/normal/ORM PNG identities;
- all KTX2 identities and encoder evidence;
- exact H2 fallback material bundle ID;
- all replacement contracts;
- `synthetic=true`, `ai_generated=true`,
  `real_photo_textures=false`.

### 11.2 Mesh bundle v3

`nantai.synthetic-village.mesh-asset-bundle.v3` keeps H2 LOD0/LOD1 unchanged
and gives each affected LOD2 asset two complete variants:

```json
{
  "variants": {
    "h3-ai-ktx2-4k": {
      "glb_sha256": "<sha256>",
      "texture_dependencies": ["<ktx2 descriptors>"]
    },
    "h2-png-1k-fallback": {
      "glb_sha256": "<sha256>",
      "texture_dependencies": ["<exact H2 descriptors>"]
    }
  },
  "geometry_fingerprint": "<sha256>"
}
```

The geometry fingerprint covers positions, indices, normals, tangents, UVs,
primitive/material-slot assignments, bounds, and node transforms. Both
variants must have the same fingerprint. A texture-profile build cannot change
topology.

Unaffected slots may reuse exact H2 texture SHAs, but the closure remains
self-contained for release packaging.

## 12. Chunk runtime v3

`nantai.synthetic-village.mesh-chunk-runtime.v3` exposes:

- primary and fallback material profile IDs;
- exact primary/fallback asset variant descriptors;
- predicted compressed texture bytes;
- source/material/mesh bundle IDs;
- unchanged chunk coordinates, world offsets, instance transforms, and
  provenance.

The endpoint returns both variant descriptors but no texture bytes. The Viewer
selects exactly one profile and fetches only that profile's closure.

Unknown profiles, missing counterparts, geometry fingerprint disagreement,
unsafe paths, wrong bundle IDs, or incomplete closures are terminal structured
errors.

## 13. Viewer runtime

### 13.1 Vendored dependencies

Vendor and hash-lock:

- Three.js `KTX2Loader.js` matching the existing vendored Three revision;
- Basis transcoder JS/WASM assets required by that loader;
- upstream license text.

No CDN is allowed.

### 13.2 Session-wide selection

After `WebGLRenderer` exists, the Viewer:

1. initializes one `KTX2Loader`;
2. detects renderer support;
3. decodes one verified canary texture;
4. checks predicted compressed memory against the 512 MiB budget;
5. freezes the session profile before loading world meshes.

Capability absence selects `h2-png-1k-fallback` normally. Integrity,
transcoder, or decode failure selects H2 with an explicit structured
`fallback_reason`; it never claims H3 active.

If a primary texture fails after H3 chunks begin loading, the Viewer disposes
all H3 chunks, templates, decoded resources, and KTX workers before reloading
the visible set entirely with H2. Mixed H2/H3 chunks are forbidden.

### 13.3 Verified resource store

The store verifies final URL, redirect state, MIME, byte count, and SHA before
KTX2 parsing. It shares one decoded/GPU texture per exact semantic key:

```text
sha + role + colour space + sampler + alpha mode + flipY + material profile
```

It reports bounded counters only:

- network fetches;
- KTX transcodes;
- PNG bitmap decodes;
- GPU texture creations;
- compressed mip bytes;
- active/idle templates;
- active profile and fallback reason.

Raw URLs, texture bytes, prompts, and hashes do not enter the public bridge
diagnostics.

## 14. Weather and appearance

H3 material clones retain the exact selected texture objects. Clear, rain,
overcast, fog, night, and snow may change renderer light, material scalar
response, sky, and atmospheric overlays, but they do not substitute a
different material profile or mutate texture identity.

Returning to clear must restore the exact baseline material scalars.

Weather remains dynamic mesh relighting plus atmosphere, not weather-specific
3DGS retraining.

## 15. Studio UX

Studio and Viewer expose:

- `AI 合成 4K · KTX2` when H3 is active;
- `H2 1K 回退` plus a human-readable reason when fallback is active;
- `synthetic / preview-only / not real-photo` disclosure in both states;
- active source/material/mesh bundle identity in the evidence inspector;
- predicted/observed compressed texture memory and profile selection evidence.

No green success badge implies real-photo, measured, or reconstructed truth.

## 16. Release payload and download

The local release archive contains:

- canonical AI source pack manifest and selected native source objects;
- 4K masters and derived PBR maps;
- KTX2 primary and PNG fallback objects;
- material and mesh bundle manifests;
- Viewer transcoder assets and license inventory, or an exact reference to the
  tracked vendored runtime revision;
- contact sheets, machine-readable audits, and build report;
- `SHA256SUMS`.

Archive construction is deterministic from published objects and uses
normalized paths, permissions, ordering, timestamps, and LF metadata.

A download command:

1. downloads to a staging directory;
2. verifies archive SHA;
3. extracts without path traversal or symlinks;
4. verifies every internal manifest/object SHA;
5. atomically publishes the bundle to `.nantai-studio`;
6. runs one server asset and one Viewer smoke probe.

No public release is uploaded without explicit authorization. A local archive
and download/verify workflow can be completed first.

## 17. Failure and rollback behavior

| Failure | Required behavior |
|---|---|
| AI candidate missing metadata | reject candidate before source publication |
| source/master/PBR hash mismatch | reject whole H3 build |
| KTX validator or decoded-quality failure | reject affected material and whole bundle |
| primary/fallback topology disagreement | reject mesh bundle |
| missing vendored transcoder/hash/license | H3 capability unavailable |
| device lacks supported KTX2 path | select explicit H2 fallback |
| integrity or decode failure | dispose H3, show reason, reload whole visible set as H2 |
| predicted memory >512 MiB | refuse H3 activation |
| browser frame/visual gate fails | keep H2 default |
| release payload incomplete | do not publish or activate |

Rollback is one exact material-profile/bundle identity change. H2 payloads and
their existing browser evidence remain immutable.

## 18. Acceptance gates

### 18.1 Source and derivation

- exactly eight required slots, three candidates each, one recorded selection;
- selected source bytes and prompts content-addressed;
- every master exactly 4096 × 4096 RGB8;
- opposite edges pass the fixed seam threshold;
- interior structure remains within the frozen source-preservation threshold;
- base/normal/ORM semantics and replacement contracts complete;
- no alpha, text, logo, horizon, or gross perspective contamination.

Numerical source thresholds are calibrated on the first generated candidate
set and frozen before choosing winners. Thresholds cannot be relaxed after
seeing H3 browser results.

### 18.2 KTX2

- official validation clean;
- exact mip chain 4096 → 1;
- correct colour/linear transfer semantics;
- decoded base-colour SSIM, normal cosine similarity, and ORM channel error
  meet frozen per-role thresholds;
- repeat compilation is byte-identical on the acceptance machine;
- cross-platform byte equality is required before claiming portable release
  identity.

### 18.3 Geometry and bundle

- H2/H3 topology fingerprint exact for every asset variant;
- 11-asset layout closure unchanged;
- LOD0/LOD1 exact H2 SHA equality;
- no coordinate, AABB, transform, or provenance drift;
- primary and fallback dependency closures independently verified.

### 18.4 Visual review

Fixed Blender and Viewer comparisons show H2 and H3 under:

- three pedestrian close cameras;
- clear, rain, and night;
- roof, timber, stone, and ground crops;
- identical camera matrices, lighting presets, geometry, and exposure.

Review rejects visible wrap seams, perspective-baked lighting, repeated motifs,
normal inversion, mip shimmer, compression blocks, colour-space errors, or
weather material identity loss.

### 18.5 Browser

In the same viewport and machine:

- 9/9 default chunks with zero pending/failed;
- 9/9 at `(123456,-98765,12)` and `(-123456,98765,12)`;
- all six weather IDs apply and return to clear;
- median frame interval `<=33.3 ms`;
- p95 frame interval `<=50 ms`;
- median regression `<=30%` against accepted H2;
- compressed H3 mip bytes `<=512 MiB`;
- stable resource counts at 60 and 62 seconds;
- one fetch/transcode/GPU creation per exact semantic key;
- zero application warning/error;
- H2 fallback independently passes the same coordinate/weather smoke.

H3 becomes default only after every gate passes. A better contact sheet alone
cannot override runtime failure.

## 19. Test strategy

### Python unit/TDD

- strict source/material/mesh/chunk schema dispatch;
- canonical identity and path-free bytes;
- source audit and deterministic 4K derivation;
- PBR role semantics and replacement closure;
- KTX header/DFD/mip/quality parsing;
- topology fingerprint equality;
- atomic release extraction and SHA verification;
- negative tests for every fail-closed condition.

### Opt-in real tools

- image generation output capture and source audit;
- pinned KTX encoder repeat build and official validator;
- Blender variant build and contact sheet;
- release archive download/extract smoke.

### Viewer

- vendored module graph, hashes, and licenses;
- profile negotiation and canary decode;
- verified KTX byte closure;
- session-wide atomic fallback;
- cache/refcount/worker disposal;
- weather clone texture identity;
- bridge/HUD evidence;
- browser visual/performance acceptance.

Full Python, Node, ruff, compileall, and diff-check gates run before each
activation commit.

## 20. Activation sequence

1. Freeze the tracked prompt set, source pack schema, and numerical source
   thresholds.
2. Generate candidates and publish only selected content-addressed sources.
3. Build/audit 4K masters and PBR maps.
4. Download, hash, install, and pin official KTX-Software `v4.4.2`; compile
   and independently validate KTX2.
5. Build material bundle v2 and dual-variant mesh bundle v3.
6. Add strict Studio routes and chunk runtime v3.
7. Vendor KTX2 loader/transcoder and implement atomic Viewer selection.
8. Build local deterministic release archive and verify a clean download.
9. Render contact sheets and run H2/H3 browser A/B.
10. Activate exact H3 identity only if all gates pass.

Each green implementation slice is path-limited, committed with the required
Codex attribution, and pushed to `origin/main` before the next slice grows.
The collaborator weather WIP remains untouched.

## 21. H3-B boundary

H3-A does not complete the full model-realism objective. After H3-A passes,
H3-B may replace selected hero geometry:

- roof/eaves;
- timber/stone building shells;
- tree trunk/branch/canopy structure;
- fences and walls.

H3-B requires its own licensing, coordinate, topology, LOD, collision,
replacement, performance, and visual specification. It consumes H3-A material
slots but cannot weaken their source or runtime contracts.

## 22. References

- Three.js KTX2Loader:
  https://threejs.org/docs/pages/KTX2Loader.html
- Khronos KTX 2.0 specification:
  https://registry.khronos.org/KTX/specs/2.0/ktxspec.v2.html
- Khronos KTX tools:
  https://github.khronos.org/KTX-Software/ktxtools/
- Existing H2 verification:
  `docs/verification/2026-07-19-high-fidelity-near-mesh-and-foliage.md`
