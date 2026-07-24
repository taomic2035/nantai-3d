# Batch24 Perimeter-Closure Overlay Design

Date: 2026-07-23
Status: approved (option A)
Owner: Codex
Input: Batch24 reciprocal-perimeter and section-closure design pack

## 1. Purpose

Batch24 adds useful reverse-side and construction references, but the current
Blender production scene remains an exact 218-root artifact. This design turns
the sixteen Batch24 references into an additive, content-addressed Blender
overlay without changing any historical exact-218 contract or claiming that
image generation recovered real geometry.

The overlay adds eight perimeter sectors with six canonical parts each:

1. terrain contact or bench;
2. bidirectional route or creek corridor;
3. retaining, bank or structural support;
4. drainage or water path;
5. cross-sector boundary seam;
6. vegetation or far-field enclosure.

The result is an exact 266-root scene:

```text
exact-218 verified base + 48 Batch24 roots = exact-266 closure build
```

All new geometry remains `synthetic / L0 / preview-only /
modeled-unverified`. Batch24 source binding has `trust_effect=none`.

## 2. Chosen architecture

The approved option is an additive overlay with new schemas and paths.

Rejected alternatives:

- modifying `EnvironmentModulePlan v1` or `ReciprocalRouteModulePlan v1`
  would invalidate historical plan digests and exact-218 evidence;
- generating only Viewer-side procedural scenery would not create Blender
  geometry or support the required render gates.

The new implementation must not modify the canonical bytes, instance ranges,
root counts or report semantics of either existing v1 plan.

## 3. Canonical instance partition

The module order and instance ranges are literal-locked:

| Module ID | Sector | Instances |
|---|---|---:|
| `closure-upstream` | upstream creek valley | 219–224 |
| `closure-northeast` | northeast forest terrace | 225–230 |
| `closure-east` | east orchard route | 231–236 |
| `closure-southeast` | southeast service edge | 237–242 |
| `closure-downstream` | downstream creek basin | 243–248 |
| `closure-southwest` | southwest stone bank | 249–254 |
| `closure-west` | west uphill forest | 255–260 |
| `closure-northwest` | northwest flume ridge | 261–266 |

Each range contains exactly six parts in the semantic order from section 1.
Part IDs, material slots, geometry families, layout, reciprocal source SHA and
section source SHA are part of canonical plan bytes.

## 4. Source and base bindings

The plan binds the private Batch24 candidate manifest and every accepted PNG by
lowercase SHA-256. Those accepted payloads also exist in the clean public
Release, but the build consumes the explicit private manifest path. It uses the
image bytes only as recorded design provenance; Blender does not infer
dimensions, camera positions or geometry from pixels.

The runtime request additionally binds:

- exact-218 build ID;
- exact-218 `.blend` SHA-256;
- exact-218 build-report SHA-256;
- exact-218 object-registry SHA-256;
- Batch24 closure-plan SHA-256;
- Batch24 private candidate manifest SHA-256;
- Blender runtime script SHA-256;
- the verified exact-218 Blender material-binding-table SHA-256.

Unknown, missing, uppercase, malformed or mismatched hashes fail closed.

## 5. Plan model

Add `pipeline.synthetic_village.perimeter_closure_module` with:

- frozen Pydantic models;
- schema `nantai.synthetic-village.perimeter-closure-module.v1`;
- `PerimeterClosurePart`;
- `PerimeterClosureModule`;
- `PerimeterClosurePlan`;
- deterministic canonical JSON bytes and SHA-256 helpers;
- default plan builder;
- explicit verifier.

Every part stores:

- `instance_id`;
- `module_id` and `part_id`;
- `semantic_role`;
- `geometry_family`;
- `material_slot_id`;
- `center_m`, `extent_m` and `orientation_deg`;
- `inner_anchor_m` and `outer_anchor_m`;
- `previous_seam_m` and `next_seam_m`.

Placement is derived from explicit scene/topology coordinates and terrain
sampling, never from Batch24 pixels. Each sector must expose an inward and
outward route/water endpoint and two neighbor seam endpoints.

## 6. Geometry contract

The first implementation is a physically connected synthetic construction
model, not a flat image card:

- terrain parts are watertight wedges or benches with visible underside/contact;
- route/creek parts connect the inner and outer anchors continuously;
- retaining/support parts touch both terrain and the supported corridor;
- drainage/water parts have an open, continuous discharge path;
- seam parts overlap neither neighbor and terminate within the seam tolerance;
- vegetation/enclosure parts use sparse explicit proxy geometry and never seal
  the walking corridor or camera sky.

The Blender builder may create auxiliary mesh children, but only the 48
canonical roots carry `nv_root=True`. Image planes, hidden black boxes and
unregistered root objects are forbidden.

## 7. Runtime and artifact contract

Add `pipeline.synthetic_village.perimeter_closure_runtime` and
`scripts/blender/apply_perimeter_closure_modules.py`.

The runtime:

1. validates the exact-218 request/report/artifact triple;
2. snapshots all immutable input files;
3. verifies the plan and source bindings;
4. invokes the measured Blender executable with a private staging directory;
5. loads the exact-218 artifact;
6. verifies canonical roots `1..218`;
7. creates roots `219..266`;
8. validates counts, identities, materials, bounds, contacts and seams;
9. saves the exact-266 `.blend`;
10. writes a canonical report and re-verifies it outside Blender;
11. content-addresses the final directory by request bytes.

Required build entries:

```text
perimeter-closure-build-request.json
perimeter-closure-build-report.json
village-perimeter-closure.blend
```

The report is schema
`nantai.synthetic-village.perimeter-closure-build-report.v1` and literal-locks:

```text
base_canonical_roots=218
overlay_canonical_roots=48
canonical_roots=266
verification_level=L0
geometry_usability=preview-only
trust_effect=none-quality-filter-only
```

## 8. Caller and CLI

Add an additive command to `scripts/synthetic_village.py`:

```text
build-perimeter-closure
```

The caller accepts explicit paths for the exact-218 build directory and
Batch24 manifest. It never searches by filename or silently selects the newest
artifact. A successful command prints only bounded truth: IDs, hashes, counts,
paths and the unchanged trust boundary.

The Studio job/ledger integration is a later additive consumer of the same
request/report schemas. The CLI is the first caller so Blender evidence can be
produced and audited before UX integration.

## 9. Acceptance gates

### 9.1 Static and model gates

- canonical determinism across processes;
- exact module order and instances `219..266`;
- all sixteen Batch24 source hashes bound exactly once;
- exact six semantic roles per sector;
- no mutation of v1 canonical bytes or SHAs;
- all invalid/missing base or source bindings fail closed.

### 9.2 Fresh Blender build gates

- exact roots `1..266`, no gaps or duplicates;
- base roots `1..218` retain object IDs and base registry SHA;
- overlay roots `219..266` match the plan registry;
- all 48 roots have non-empty meshes/materials and finite bounds;
- terrain/support contact and route/drainage continuity checks pass;
- eight previous/next sector seams satisfy the configured tolerance.

### 9.3 Render and roaming gates

Materialize sixteen audit cameras:

- eight outer reciprocal cameras looking inward;
- eight inner cameras looking outward.

Then run:

- fresh camera clearance;
- reciprocal-pair visibility;
- cross-sector seam visibility;
- six-layer render identity;
- target visibility;
- post-render v2;
- RGB original-resolution review.

Passing geometric tests alone does not establish image quality. Passing all
synthetic gates still does not establish real geometry, real texture, metric
alignment or real-photo reconstruction.

## 10. Failure handling

The build must fail without publishing a final directory when:

- any base or source hash disagrees;
- the base artifact is not the bound exact-218 build;
- Blender version/executable/script identity disagrees;
- any instance, module, semantic role or material binding is missing;
- a route/water endpoint is disconnected;
- a support is floating;
- a drainage path is sealed;
- a seam exceeds tolerance;
- report bytes disagree with the artifact or request.

Private staging is removed only after its resolved path is proven to be under
the configured build root. Existing content-addressed builds are immutable and
may be reused only after full re-verification.

## 11. Out of scope

- deriving geometry or camera calibration from synthetic images;
- changing exact-218 v1 artifacts or historical evidence;
- claiming that exact-266 is a real reconstruction;
- registering Batch24 PNGs as PBR texture payloads;
- training SfM, NeRF or 3DGS from Batch24;
- replacing the external real-capture, COLMAP and cloud-GPU path.

## 12. Definition of done

This phase is complete only when:

1. plan/runtime/caller tests pass;
2. existing v1 tests prove canonical compatibility;
3. a fresh real Blender exact-266 artifact and canonical report exist;
4. sixteen reciprocal cameras have fresh render evidence;
5. seam, visibility, six-layer and post-render gates have machine reports;
6. RGB review records remaining visual defects honestly;
7. documentation records all content SHA values and the unchanged trust limit.
