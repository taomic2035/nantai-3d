# Nantai 3D Production Hybrid Scene Overlays Design

Date: 2026-08-02

Status: selected direction and written specification approved by the user on
2026-08-02

Program objective: complete the original replaceable-assets promise without
rewriting or misrepresenting a real Gaussian reconstruction. A verified real
3DGS remains the authoritative captured base. Content-addressed mesh assets
may be added, replaced, hidden or rolled back as separately identified overlay
layers.

This specification is subproject 4 from
`2026-07-26-production-v1-real-golden-path-design.md`. It does not replace the
five real-scene gates: rights-cleared capture, accepted real-photo SfM,
non-mock CUDA 3DGS, measured metric alignment and real Viewer acceptance.

## 1. Product decision

The selected route is a receipt-bound hybrid composition:

```text
verified real 3DGS base (immutable identity)
  + verified mesh asset bundles (immutable identities)
  + immutable slot transforms and selection
  = one content-addressed scene-composition revision
```

The base reconstruction is never edited by an asset replacement. Replacing an
asset creates a new composition revision that references the same base scene
and a different overlay selection. Disabling all overlays reproduces the
original base-scene bytes and presentation.

### 1.1 Rejected alternatives

1. **Rewrite the real-scene import manifest for every replacement.** This
   couples operator-authored objects to captured evidence, invalidates the
   import receipt and makes rollback require rebuilding the real scene.
2. **Load arbitrary overlay URLs only from Viewer query parameters.** This is
   convenient but has no package closure, rights binding, stable coordinates
   or acceptance identity. It cannot satisfy Production.
3. **Edit the Gaussian appearance itself.** Local splat deletion, inpainting
   and retraining are valuable future work, but they are a different trust and
   training problem. They are not smuggled into the replaceable-mesh feature.

## 2. Scope

### 2.1 In scope

- one verified real 3DGS base plus zero or more mesh overlay instances;
- stable replaceable slots with immutable revision history;
- GLB mesh LOD and material-profile selection through the existing verified
  mesh resource path;
- explicit ENU-metre placement transforms;
- base-only, hybrid and overlay-diagnostic Viewer modes;
- Studio candidate validation, comparison, commit and rollback UX;
- Production package closure, machine QA and human visual review for the exact
  composition revision;
- Preview operation on non-production bases, with the resulting composition
  remaining Preview.

### 2.2 Not in scope

- modifying colour, opacity, covariance or SH coefficients in the base 3DGS;
- claiming that modeled overlays were reconstructed from the source capture;
- automatic semantic segmentation or object removal from the captured scene;
- arbitrary external URLs, camera-facing billboards or unbound texture files;
- collision-safe arbitrary-coordinate navigation;
- using image2 design boards as textures, calibrated views or geometry
  evidence.

## 3. Trust model

Base truth and composition truth are independent.

| Claim | Authoritative evidence |
|---|---|
| Captured geometry/appearance | existing production import receipt and base scene manifest |
| Base metric alignment | existing measurement, policy and decision chain |
| Overlay bytes/materials | verified mesh asset bundle and reachable resource digests |
| Overlay placement | composition manifest transform and placement provenance |
| Active replacement selection | composition revision and parent revision identity |
| Hybrid visual quality | composition-bound machine report and human review |
| Public package integrity | Production receipt and download verifier |

An overlay may be positioned in a measured ENU frame without becoming measured
captured geometry. The UI and evidence use separate fields:

```text
base_scene_role=production-acceptance
composition_kind=real-base-with-modeled-overlays
overlay_geometry=modeled-unverified
placement_frame=world-enu
placement_evidence=operator-authored
trust_effect=none
```

The combined presentation must never be labelled `fully-real`,
`reconstructed-overlay` or `measured-overlay` from those facts. If the base is
Preview, arbitrary or unaligned, the composition cannot be promoted above the
base. If any active overlay lacks accepted rights, resource closure, transform
validity or hybrid QA, the composition is not Production-accepted even when
the base remains independently accepted.

## 4. Artifacts and identities

The overlay root is separate from the verified real-scene import root. Adding
an overlay file below the import root would correctly invalidate the current
`RealSceneImportReceipt` exact file-set check.

### 4.1 Base scene binding

Every composition binds all of:

- real-scene import receipt canonical SHA-256;
- base `scene_identity` from public evidence;
- base reconstruction manifest SHA-256 and byte length;
- target frame ID, units and axis convention;
- production acceptance report and decision SHA-256 when Production is
  claimed.

A composition cannot be replayed onto another import merely because both use
ENU metres.

### 4.2 Overlay asset bundle binding

The first Production version consumes the existing
`nantai.synthetic-village.mesh-asset-bundle.v3` closure through a strict
adapter. The adapter reopens the manifest and every reachable GLB/KTX2/PNG
byte, verifies H3/H2 profile identity, LOD geometry identity, local bounds and
the existing trust fields. It does not convert `synthetic=true` or
`real_photo_textures=false` into another claim.

Each bundle reference records:

- bundle schema and bundle ID;
- canonical manifest SHA-256 and byte length;
- source material-bundle IDs and profile IDs;
- sorted asset closure;
- rights/source receipt identities required for public distribution;
- `trust_effect=none`.

Supporting another mesh-bundle schema later requires an explicit adapter and
does not weaken the v3 verifier.

### 4.3 Scene composition manifest v1

`nantai.scene-composition-manifest.v1` is canonical LF JSON with:

- `composition_id`, derived from canonical bytes excluding the ID itself;
- `parent_composition_sha256`, null only for the first revision;
- exact base-scene binding;
- sorted unique overlay-bundle bindings;
- sorted unique replaceable slots;
- derived disclosure and acceptance state;
- `trust_effect=none`.

Each slot contains:

- stable lowercase `slot_id` and monotonic integer `slot_version`;
- exact `asset_id`, bundle ID and selected material profile;
- a finite right-handed local-to-world transform;
- target frame ID and units copied from the base binding;
- declared local and transformed world bounds;
- placement provenance and optional semantic label;
- enabled/hidden state;
- optional predecessor slot identity for replacement audit.

Transforms are translation, unit quaternion and positive uniform scale. Shear,
reflection, non-uniform scale, NaN/Inf and zero scale fail closed. Geometry
bounds are remeasured from verified GLB bytes, transformed independently and
checked against the declared world bounds. Phase 1 placement provenance is
`operator-authored`; a future measured-placement role needs a separate
evidence schema.

### 4.4 Composition publication receipt v1

`nantai.scene-composition-receipt.v1` binds:

- the canonical composition manifest;
- every reachable overlay asset byte;
- the exact base binding without copying private base evidence;
- candidate validation policy and decision;
- source/rights receipts;
- complete sorted file whitelist;
- publisher source commit and clean-tree identity.

Publication is append-only and content-addressed below:

```text
.nantai-studio/scene-compositions/<scene-identity>/<composition-id>/
```

Candidate staging, file and directory sync, and no-replace publication use
the existing durability primitives. Name collision, partial sync or ambiguous
publication retains evidence and never overwrites an older revision. Rollback
selects a prior verified receipt; it does not mutate or delete the latest one.

## 5. Data flow

```text
production import + acceptance
  -> reopen exact base binding
verified mesh bundle candidate
  -> reopen resource closure and rights
slot selection + ENU transform
  -> derive canonical composition manifest
  -> validate bounds, identities and replacement lineage
  -> durable no-replace publication
  -> Studio mounts base and composition independently
  -> Viewer fetches the verified base first, then exact overlay resources
  -> hybrid machine capture and human review
  -> Production release includes only reachable accepted bytes
```

No composition stage writes into the production import root. No Viewer result
is allowed to backfill trust into the import receipt.

## 6. Studio behavior

Studio exposes separate cards for:

- **Base reconstruction:** existing engine, Gaussian count, coordinate and
  acceptance evidence;
- **Active composition:** composition ID, parent revision, slot count and
  acceptance state;
- **Overlay slots:** asset/version/profile, placement source, bounds and
  validation result.

The replacement workflow is:

1. choose an existing stable slot;
2. select a verified asset candidate allowed by the slot contract;
3. preview it as `candidate / uncommitted`;
4. compare current and candidate at receipt-bound camera poses;
5. validate bundle, transform, bounds, rights and performance budget;
6. explicitly commit a new immutable composition revision;
7. keep both revisions available for audited rollback.

Studio must not expose a commit button when write durability is unavailable.
Read-only macOS, Windows or packaged runtimes still load and compare verified
revisions. Enabling cross-platform mutation requires the corresponding local
filesystem self-test; a generic in-memory success flag is insufficient.

## 7. Viewer behavior

Viewer adds three explicit presentation modes:

- `hybrid`: base 3DGS plus active overlays;
- `base`: verified 3DGS with all overlays disabled;
- `overlays`: diagnostic overlay-only view, never the default Production
  representation.

The HUD always shows both identities and disclosure, for example:

```text
Base: real 3DGS / metric-aligned / production-accepted
Overlay: modeled-unverified / operator-authored / trust effect none
Composition: hybrid / review pending
```

The base scene is loaded and verified before overlay fetch begins. Overlay
resources use same-origin exact-byte fetch, bounded caching and existing
verified mesh resource loaders. Mesh and splat passes share the base world
frame and camera. Opaque mesh depth, transparent material behavior and splat
occlusion require actual-browser evidence; render order is not accepted from a
unit-test-only assertion.

If an active overlay fails:

- the composition enters a visible failed state;
- Studio/Viewer may offer an explicit `view verified base only` action;
- the UI cannot silently hide the overlay while retaining a hybrid-success
  label;
- the failure cannot alter base-scene acceptance.

## 8. Replacement and revision semantics

- Slot IDs are stable across revisions; asset IDs are not used as slot IDs.
- A replacement increments exactly one slot version unless the operator
  submits an explicit multi-slot transaction.
- The parent composition SHA must match the currently selected revision.
- Duplicate submission of identical canonical input returns the same content
  identity; it never creates a different revision with the same meaning.
- Conflicting parent, asset, transform or resource identities fail before
  publication.
- Removing an overlay creates a new revision with that slot disabled. It does
  not delete the asset bundle or prior composition.
- Candidate bytes, contact sheets, rejected assets and private source paths do
  not enter the public package.

## 9. Production release and acceptance

Production distribution gains an optional composition closure alongside the
existing base-scene closure. When a composition is active, it is mandatory,
not advisory.

The builder must:

1. revalidate the production base import and acceptance;
2. revalidate the composition receipt and its exact base binding;
3. include only manifest-reachable overlay resources;
4. bind the composition ID into public evidence, package content ID, Viewer
   capture and cover identity;
5. rerun privacy and rights checks on the same copied bytes;
6. reject extra, missing, mixed-revision or unbound overlay files.

The download verifier recomputes the same closure. A package with overlay
bytes but no composition receipt, or a receipt referencing absent bytes, is
invalid.

### 9.1 Machine quality gates

At minimum the exact hybrid revision must prove:

- base and overlay frame/units agree;
- every active asset, material and texture byte is closed;
- transformed bounds are finite and within the accepted scene envelope;
- slot replacements do not duplicate IDs or violate size/placement policy;
- cold load and bounded memory/performance budgets in `hybrid` mode;
- mesh/3DGS occlusion at fixed camera poses;
- LOD transition does not change asset identity or placement;
- failed overlay fetch never produces a hybrid-success state;
- base-only mode still matches the original base manifest and screenshots.

### 9.2 Human visual gates

Receipt-bound reviewers check:

- floating, sinking and contact-shadow errors;
- incorrect splat/mesh occlusion;
- obvious material-scale, exposure or colour mismatch;
- replacement popping and LOD discontinuity;
- residual captured object conflicts where an overlay is intended to augment,
  not erase, the base;
- truthful base/overlay disclosure in every mode.

An accepted base with a rejected overlay remains an accepted base and a
rejected hybrid composition.

## 10. Error handling and privacy

- Unknown, missing or contradictory overlay evidence is rejected, never
  guessed.
- Errors use bounded portable labels and do not echo private source paths,
  tokens, raw environment variables or rights text.
- Every authoritative JSON input is duplicate-key-safe, canonical and read
  through the existing stable bounded file primitives.
- GLB and texture resources reject links, redirects, external URIs, size
  drift, path-identity ambiguity and unsupported media types.
- A failed publication keeps an honest residue/receipt state and cannot be
  mistaken for the active composition.
- Public Releases contain only accepted active assets, public receipts,
  checksums and required runtime code. Private candidates and review working
  files remain ignored.

## 11. Implementation boundaries

The implementation should add a generic composition contract instead of
changing synthetic source identities:

```text
pipeline/scene_composition.py
pipeline/scene_composition_publication.py
web/viewer/scene-composition.mjs
```

It may integrate with, but must not weaken:

```text
pipeline/real_scene_import.py
pipeline/synthetic_village/mesh_asset_bundle_v3.py
pipeline/production_release_builder.py
pipeline/production_release_verifier.py
pipeline/studio_server.py
web/viewer/verified-mesh-resources.mjs
web/studio/
```

The work is delivered in four independently reviewable phases:

1. canonical composition model, verifier and negative matrix;
2. append-only publication and replacement lineage;
3. Studio mount plus hybrid Viewer rendering and truthful UX;
4. Production release closure, browser QA and human-review binding.

Each phase uses RED -> minimal GREEN -> full related gates -> Ruff ->
`git diff --check`, followed by a path-limited commit and push to `main`.

## 12. Acceptance criteria

The replaceable-assets promise is complete only when all of the following are
proved on one accepted real-scene base:

1. a verified overlay asset occupies a stable slot in ENU metres;
2. replacing it produces a different composition ID while preserving the exact
   base import and scene identities;
3. both revisions reopen from clean storage and rollback selects the previous
   bytes without mutation;
4. Viewer loads full 3DGS and overlay mesh simultaneously with correct
   disclosure and observable occlusion;
5. base-only mode reproduces the verified base;
6. corrupted bundle, transform, parent revision, rights or resource bytes fail
   closed before acceptance;
7. hybrid machine and human QA accept the exact revision;
8. a clean Production build and downloaded-package verifier bind only the
   active composition's reachable bytes;
9. the UI never calls the overlay real reconstructed geometry;
10. the five original real-scene gates remain independently satisfied.

Modeled fixtures and synthetic bases exercise contracts in CI but cannot
satisfy criteria 1-8 for Production V1.
