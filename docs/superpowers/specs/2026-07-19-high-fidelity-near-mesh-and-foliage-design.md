# High-fidelity near mesh and cutout foliage

Date: 2026-07-19
Status: user-approved H1 + H2 design

## 1. Decision summary

The infinite textured synthetic world will receive one new immutable mesh
template bundle that combines the approved H1 and H2 slices:

- **H1:** rebuild only near LOD2 geometry with materially richer silhouettes
  and construction detail;
- **H2:** replace opaque vegetation blobs at LOD2 with deterministic,
  alpha-masked foliage and move repeated LOD2 PBR images into verified shared
  content-addressed texture objects.

LOD0 and LOD1 GLB payloads remain byte-for-byte the current verified objects.
World coordinates, footprints, asset IDs, instance IDs, layout, chunk
manifests, replacement semantics, weather states, and Viewer scheduling do not
change. H3, importing externally authored or AI-generated GLBs, remains out of
scope.

The rebuilt LOD2 algorithm is named `synthetic-template-mesh-near-v2`. Reused
LOD0/1 payloads retain `synthetic-template-mesh-v1`; they are not relabelled.
The result is still a deterministic synthetic preview, not a real
reconstruction or a calibrated scan. It may be described as a
**high-fidelity near synthetic mesh** only after the gates in this document
pass. It may not be described as photoreal, measured, or
source-photo-consistent.

## 2. Evidence motivating this slice

The current infinite mesh route is functionally complete but its near geometry
is too sparse:

- all 33 GLBs total only 4,458 triangles;
- current LOD2 buildings contain about 450 triangles each;
- current LOD2 vegetation contains 172 to 476 triangles;
- the complete bundle is about 354 MiB, while LOD2 alone is about 118 MiB;
- most transferred bytes are repeated embedded 1024 px PBR maps rather than
  useful near-view geometry.

The Viewer already proves the important outer behavior: nine active mesh
chunks, arbitrary positive and negative coordinates, content-addressed
templates, and six reversible weather states. The highest-value next step is
therefore to improve what the user sees at pedestrian distance without
replacing the working world and streaming contracts.

## 3. Goals

1. Make all eleven replaceable templates hold up materially better at
   pedestrian distance.
2. Preserve exact current LOD0 and LOD1 GLB bytes and behavior.
3. Give buildings readable roof, eave, opening, frame, foundation, and
   asset-specific construction details.
4. Give vegetation visible trunks, branches, stems, nodes, and leaf
   silhouettes instead of closed ellipsoid canopies.
5. Give props bevelled, layered, irregular silhouettes instead of assemblies
   of a few boxes.
6. Reuse each unique LOD2 image payload across templates and assets in one
   Viewer session.
7. Keep all geometry, texture, alpha, and runtime claims machine-verifiable and
   fail closed.
8. Retain the current arbitrary-coordinate, replacement, LOD, and weather
   contracts.

## 4. Non-goals and honest limits

- H1 + H2 does not train 3DGS, infer geometry from photos, or align a real
  reconstruction.
- The foliage atlas is deterministically derived from current synthetic
  material sources. It is not a botanical scan and
  `real_photo_textures=false` remains mandatory.
- H2 does not add alpha blending. It uses alpha masking to avoid order-dependent
  transparency sorting.
- H2 does not add wind animation, seasonal morphing, snow accumulation, or
  per-leaf physics.
- H1 does not add interiors, collision, navigation meshes, destructibility, or
  construction-grade geometry.
- H1 + H2 does not repair terrain/road hard-edge quality. Terrain remains a
  separate algorithm revision and a later high-value slice.
- H3 external or AI-authored mesh replacement is not approved by this design.
- No macOS L0 build becomes authoritative merely because it looks better. The
  existing verification-level and platform disclosures remain unchanged.

## 5. Compatibility and content identity

### 5.1 Stable world-facing identities

The following remain exact:

- eleven `asset_id` values;
- registry footprints and synthetic ENU Z-up coordinate encoding;
- `MockLayoutGenerator` inputs and stable instance IDs;
- `world_x = cx * 200 + local_x` and
  `world_y = cy * 200 + local_y`;
- canonical mesh chunk manifest schema and same-origin asset projection;
- LOD distance selection, hysteresis, 3 x 3 active window, and LRU behavior;
- weather state IDs and their provenance-neutral behavior.

No asset is moved, renamed, rescaled to fit a new model, or silently replaced
with a different semantic object.

### 5.2 Versioned bundle rather than an in-place schema mutation

H2 requires explicit texture dependencies that the current
`nantai.synthetic-village.mesh-asset-bundle.v1` cannot represent. The new
publication therefore uses:

- bundle schema `nantai.synthetic-village.mesh-asset-bundle.v2`;
- build schema `nantai.synthetic-village.mesh-asset-build.v2`;
- per-LOD mesh algorithm identity;
- LOD2 per-asset recipe IDs ending in `-near-v2`.

The v1 parser and canonical bytes remain untouched. The loader dispatches on
the explicit schema value and accepts v1 and v2 independently. A v2 record
must never be coerced into v1, and an unknown schema fails closed.

Every v2 LOD descriptor records its own algorithm and recipe identity:

- reused LOD0/1: `synthetic-template-mesh-v1` plus the exact current v1 recipe;
- rebuilt LOD2: `synthetic-template-mesh-near-v2` plus its exact `-near-v2`
  recipe.

The v2 build request binds the source v1 bundle ID and reused object
descriptors instead of pretending to rebuild them. The publisher verifies
their bytes against that source bundle before copying them into the new
content-addressed closure.

The canonical `MeshChunkManifest` remains unchanged except for its new bundle
ID. Its path-bearing runtime projection is explicitly versioned to
`nantai.synthetic-village.mesh-chunk-runtime.v2`; each LOD2 asset URL record
includes the exact texture dependency descriptors required by that GLB. The
Viewer continues to accept runtime v1 only for bundle v1 and accepts runtime v2
only for bundle v2. It never derives dependency URLs from asset or material
names.

The new bundle ID binds:

- exact v1 LOD0 and LOD1 GLB object hashes;
- exact rebuilt LOD2 GLB hashes;
- every shared texture object hash and byte count;
- original material-bundle identity;
- deterministic foliage derivation records;
- builder script hash, Blender identity, recipes, budgets, and audit profile.

Changing geometry, an alpha cutoff, a texture byte, a recipe, or a builder
byte necessarily changes the build and bundle identities.

## 6. H1 near-geometry contract

### 6.1 Triangle bands

LOD0 and LOD1 retain their exact current objects, so their current triangle
counts and limits remain unchanged. Every new LOD2 independently measured
triangle count must fall inside these inclusive bands:

| Kind | LOD2 minimum | LOD2 maximum |
|---|---:|---:|
| Building | 8,000 | 15,000 |
| Vegetation | 6,000 | 12,000 |
| Prop | 1,000 | 4,000 |

The minimum is not permission to pad a model with hidden, duplicate,
degenerate, zero-area, or microscopic geometry. Independent audit rejects
non-finite vertices, degenerate indexed triangles, duplicate faces, unused
meshes, invalid normals/tangents, footprint overflow, and disconnected hidden
triangle payloads. LOD triangle counts must remain strictly increasing.

### 6.2 Buildings

All five LOD2 buildings keep their registered footprint and current
architectural type. Their visible geometry includes:

- roof surfaces with individual tile or layered-thatch rows, ridge pieces,
  verge/bargeboards, fascia, soffit, and visible eave thickness;
- walls with a foundation or plinth, restrained edge bevels, and
  construction-scale surface breaks;
- recessed door and window openings with four-sided frames, sill, lintel,
  reveal depth, and glass or dark recess planes;
- asset-specific features:
  - timber houses: posts, beams, braces, board seams, and distinct door/window
    arrangements;
  - stone house: irregular quoin and foundation stones plus deep openings;
  - thatch house: layered eave fringe and uneven bundled roof edges;
  - barn: large framed doors, braces, ventilated upper opening, and heavier
    structural members.

Back faces, opposite elevations, and roof ends remain intentionally authored;
the result must not regress to a facade-only shell. Geometry stays inside the
registered footprint tolerance and rests on local Z = 0.

### 6.3 Vegetation structure

All three LOD2 vegetation assets include solid structural geometry beneath H2
foliage:

- tapered, multi-segment trunks or culms;
- deterministic major branches with non-repeating angles, length, and taper;
- pine branch whorls and layered branch hierarchy;
- broadleaf trunk forks and primary/secondary branches;
- bamboo clusters with at least twelve culms, visible nodes, and branching
  stems.

Random-looking variation comes only from a documented hash of
`asset_id`, LOD, and component index. Blender noise, timestamps, process
randomness, and platform-dependent unordered iteration are forbidden.

### 6.4 Props

LOD2 props gain silhouette-bearing detail:

- dry-stone wall: irregular bevelled blocks, staggered courses, varied depth,
  cap stones, and visible gaps without floating blocks;
- stone lamp: stepped base, bevelled shaft, recessed cage, cap, and distinct
  stone/metal construction;
- timber fence: bevelled posts, rails, braces, joinery depth, and controlled
  edge irregularity.

## 7. H2 cutout-foliage contract

### 7.1 Deterministic leaf atlases

Each current foliage material slot produces one 1024 x 1024 RGBA cutout atlas:

- `material-bamboo-leaf-01`;
- `material-broadleaf-canopy-01`;
- `material-orchard-leaf-01`.

The atlas algorithm is `deterministic-foliage-cutout-v1`. It binds the exact
existing 1024 px base-color, normal, and ORM map hashes, an exact procedural
mask recipe, atlas layout, and output PNG hashes. Colour detail comes from the
existing material source; the alpha silhouette is explicitly synthetic and
procedural.

The alpha channel must contain both covered and discarded pixels, must not be
uniform, and must meet documented coverage bands per atlas. Transparent pixels
have colour dilation from the nearest covered texel to prevent bright fringes.
Normal and ORM atlas regions use the exact same layout as base colour.

Atlas generation is path-free and byte deterministic on the supported
publication platform. Cross-platform byte disagreement is reported and fails
the authoritative publication gate; it is never repaired by re-registering
unreviewed local bytes.

### 7.2 Leaf geometry and material mode

LOD2 foliage uses branch-attached leaf or needle cluster cards with atlas
rectangles. Cards have deterministic position, scale, roll, and bend, avoid
large intersecting crosses at one common origin, and follow branch direction.

Every foliage material must declare:

- glTF `alphaMode="MASK"`;
- exact `alphaCutoff=0.45`;
- `doubleSided=true`;
- base-colour RGBA, normal, and ORM textures;
- `TEXCOORD_0` and `TANGENT` on every primitive;
- material extras binding source hashes, atlas algorithm, bundle identity,
  synthetic status, and `uv_policy="leaf-card"`.

`BLEND`, omitted alpha mode, another cutoff, a missing alpha channel, a
single-sided card, or a material without exact source/derivation evidence
fails closed. Buildings, trunks, branches, and props remain opaque.

### 7.3 Shared LOD2 texture objects

LOD0 and LOD1 retain their current self-contained GLBs. LOD2 uses shared,
content-addressed PNG objects so repeated 1024 px maps are transferred and
decoded once per Viewer session.

Each v2 LOD descriptor declares a sorted exact dependency closure:

```text
textures/<sha256>.png
sha256
byte_count
mime_type = image/png
roles = base-color | normal | orm
source_material_slot
derivation_algorithm_id
```

LOD2 GLBs may reference only relative URIs matching
`../textures/<sha256>.png`. Geometry buffers remain embedded in the GLB.
External HTTP URLs, absolute paths, query strings, fragments, redirects,
unregistered relative paths, non-PNG images, and external geometry buffers
are forbidden.

The Studio server exposes only manifest-listed objects under the immutable
bundle route, with strong ETag, HEAD, range behavior where already supported,
and `nosniff`. A texture object is never resolved by filename or material
slot; the SHA-256 path and verified manifest record are authoritative.

The only texture route shape is:

`/api/world/mesh-assets/{bundle_id}/textures/{sha256}.png`

The runtime projection supplies that exact path, SHA-256, byte count, role,
colour space, sampler state, and derivation identity. The Viewer does not
construct it by string replacement.

## 8. Independent audit and fail-closed behavior

The existing embedded-only GLB audit remains the default and is not relaxed.
Bundle v2 explicitly selects a new
`verified-relative-content-addressed` texture audit profile.

Before publication, the independent Python auditor:

1. reads each GLB and texture with bounded, changed-during-read checks;
2. rejects all external buffers and all image URIs outside the exact
   manifest-listed closure;
3. hashes every dependency and verifies byte count, PNG MIME, dimensions, and
   colour mode;
4. inspects alpha coverage and exact material mode for the three foliage
   slots;
5. verifies material closure, UVs, tangents, indexed triangles, transformed
   ENU bounds, footprint tolerance, and triangle bands; geometry-bound
   measurement uses only the embedded geometry buffer plus a strict in-memory
   resolver for the already verified texture closure, never a filesystem or
   network resolver;
6. confirms v1 LOD0/1 object hashes are exactly reused;
7. reloads the staged canonical bundle and repeats verification before an
   absent-only atomic publication.

Blender reports are cross-check evidence, not self-verification. A missing,
malformed, changed, redirected, over-budget, under-detail, alpha-invalid, or
identity-mismatched object blocks publication. There is no box, opaque canopy,
untextured material, or stale-bundle fallback after v2 has been selected.

If v2 cannot load at runtime, the Viewer reports a structured failed mesh
chunk and keeps provenance visible. It does not silently claim that a v1
template is the requested v2 asset.

## 9. Viewer loading and cache behavior

The canonical mesh chunk payload remains unchanged except for its new bundle
ID. The Studio projects runtime v2 dependency descriptors from the verified
bundle. When that bundle resolves to v2, the Viewer:

1. validates the bundle and selected LOD dependency closure;
2. fetches GLB and texture objects from strict same-origin projected routes;
3. rejects redirects, changed final URLs, wrong content types, wrong byte
   counts, and wrong hashes before parsing;
4. caches immutable response bytes and decoded `ImageBitmap` objects by
   texture SHA-256;
5. maps only the declared relative image URIs to verified in-memory object
   URLs through a template-local `LoadingManager`;
6. invokes GLTFLoader, then rebinds each material map to the shared GPU texture
   whose key is
   `(sha256, role, colour_space, sampler, wrap, flip_y, alpha_mode)`;
7. independently checks the parsed material/texture closure after GLTFLoader
   returns, because GLTFLoader can otherwise continue after an image error;
8. disposes transient loader-created texture wrappers before first render;
9. caches templates by
   `(glb_sha256, ordered_dependency_hashes)`;
10. reference-counts object URLs, decoded bitmaps, and GPU textures across
   chunks;
11. disposes them only after no active or cached template refers to them.

A byte payload is shared solely by SHA-256. A GPU texture is deliberately not
keyed by SHA alone because the same bytes used with different colour space,
sampler, or alpha semantics are different rendering resources. One corrupt
dependency fails only the templates that require it, but its failure is never
hidden. Cache keys contain content identity and rendering semantics, not asset
names or runtime URLs.

Weather continues to operate on reversible Viewer-owned material clones.
Alpha mode, alpha cutoff, texture hashes, and double-sided state are immutable
base properties and may not be changed by rain, fog, night, snow, or clear.

## 10. Performance and activation gates

H1 adds geometry while H2 removes repeated near-texture transfer and decode
cost. Activation depends on measurements rather than an assumed tradeoff.

The real-browser evidence run uses the documented Apple Silicon development
machine and records browser, renderer, viewport, device-pixel ratio, bundle
ID, and commit. After a 10-second warm-up it must prove:

- exactly nine active chunks at the default camera and after a far
  positive/negative coordinate jump;
- no duplicate network fetch or `ImageBitmap` decode for one texture SHA in a
  session;
- no duplicate GPU texture upload for one exact
  `(sha256, role, colour_space, sampler, wrap, flip_y, alpha_mode)` key;
- no failed or permanently pending chunks;
- no console warning or error;
- no parsed material with a missing, substituted, or unexpected map;
- stable geometry/texture counts after a 60-second stationary period;
- median frame interval no worse than 33.3 ms and 95th percentile no worse
  than 50 ms during a documented 60-second pedestrian orbit;
- no more than 30% regression in median frame interval against the current v1
  bundle measured in the same run and viewport.

If the visual gates pass but the runtime gates fail, v2 remains available as
an explicit preview and does not replace the default bundle. The evidence
report states the limiting metric and actual values.

## 11. Visual and functional acceptance

### 11.1 Machine gates

- all 11 assets x 3 LODs are present;
- all LOD0/1 object hashes exactly match the current bundle;
- each LOD2 is inside its kind's triangle band and has no audit defect;
- every GLB bound matches independently measured synthetic ENU bounds;
- every material and external texture dependency matches its exact closure;
- all three foliage atlases pass RGBA, coverage, `MASK`, cutoff, and
  double-sided checks;
- every template remains within its registry footprint tolerance;
- repeated builds on the same verified runtime produce identical request,
  GLB, texture, report, and bundle bytes.

### 11.2 Visual gates

A fixed-camera contact sheet renders all eleven assets at pedestrian distance
with the v1 and v2 LOD2 versions side by side. The review must be able to see:

- roof/eave/opening depth on all five buildings;
- distinct structural character among the five building asset IDs;
- branch and leaf silhouettes on all three vegetation assets;
- absence of opaque ellipsoid canopies in v2;
- bevel, layering, and irregular silhouette on all three props;
- no alpha halos, obvious card rectangles, black fringes, floating parts,
  facade-only back sides, texture stretching, or footprint clipping.

The browser acceptance run then proves:

- plain `/web/viewer/` activates the approved v2 bundle only after all gates
  pass;
- teleporting to at least one large positive and one large negative ENU
  coordinate still loads nine coherent chunks;
- clear, rain, overcast, fog, night, and snow remain reversible;
- close-up views in clear, rain, and night retain leaf masks and material
  identity;
- disclosure still says synthetic, preview-only, and not real-photo textured.

### 11.3 Completion boundary

Passing H1 + H2 means the infinite synthetic world has a substantially better
near-view asset layer with efficient, verified foliage textures. It does not
complete the broader realism goal. Remaining visible limits, especially
terrain transitions, procedural repetition, lack of interiors, and absence of
real capture-derived geometry, must be listed in the evidence report.

## 12. Implementation sequence

After written-spec approval, implementation planning will decompose the work
into TDD-sized commits in this order:

1. v2 bundle/build models and backward-compatible loader dispatch;
2. shared texture dependency audit and deterministic foliage atlas builder;
3. LOD2 geometry recipes and independent geometry gates;
4. real Blender build, publication, repeatability audit, and contact sheet;
5. strict Studio texture-object routes;
6. Viewer verified dependency loader, cache, and disposal;
7. browser visual/performance evidence;
8. default-bundle activation only if every gate passes.

Each green step is path-limited, committed with the required attribution, and
pushed to `origin/main` before the next step accumulates.

## 13. Rejected alternatives

### 13.1 Raise triangle budgets without semantic geometry gates

Subdivision can increase a counter without improving silhouette or
construction detail. The chosen design combines triangle bands with topology,
footprint, component, contact-sheet, and close-view gates.

### 13.2 Use alpha blending

Dense overlapping foliage produces draw-order artifacts and unstable
appearance. `MASK` with an exact cutoff is deterministic, supported by the
current GLTFLoader, and compatible with depth writing.

### 13.3 Keep opaque ellipsoid canopies and improve only their textures

No texture resolves the current solid-blob silhouette at pedestrian distance.
H2 requires actual leaf/needle card silhouettes and visible branch structure.

### 13.4 Embed every PBR image in every new LOD2 GLB

The current bundle already proves that this spends most bytes on repeated
textures. Verified shared objects preserve content identity while making
texture cache reuse observable and enforceable.

### 13.5 Allow arbitrary external image URLs

URL identity, redirects, mutable bytes, CORS, and offline behavior would weaken
the fail-closed bundle contract. Only exact same-bundle,
content-addressed relative PNG dependencies are accepted.

### 13.6 Replace the templates with downloaded or AI-generated GLBs

That is H3. It requires separate licensing, coordinate, topology, material,
provenance, and replacement review and is intentionally not smuggled into
H1 + H2.
