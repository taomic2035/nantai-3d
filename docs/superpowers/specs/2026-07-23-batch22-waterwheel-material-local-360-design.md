# Batch 22 Waterwheel Material and Local 360 Design

> Date: 2026-07-23
> Owner: Codex / image2
> Status: approved design; implementation requires a separate written plan

## Goal

Make the Batch 21 watermill readable as a textured mechanical assembly from a
local eight-direction orbit while preserving every existing identity and trust
boundary. Batch 22 fixes the proven material-input defect, replaces the wheel's
faceted filled-disc proxy with an open spoked wheel, and adds content-addressed
audit views around the watermill.

The output remains synthetic L0, `modeled-unverified`, `preview-only` and
`simplified-pbr-not-render-parity`. It is not measured geometry, a calibrated
multiview capture, a real-photo texture set or evidence that a real scene can
be reconstructed outside photographed volume.

## Current evidence

The accepted Batch 21 exact-218 build is structurally valid and its six formal
role renders pass the existing gates. Its watermill frame contains wheel
instance `155` and reciprocal role instances `189..195`, but the later module
meshes render nearly black and the wheel reads as a faceted disc.

The dark response has a reproduced root cause:

- the 572 base materialized meshes carry a CORNER/FLOAT_COLOR
  `nv_surface_color` attribute;
- the 94 meshes added by the environment and reciprocal runtimes do not;
- reused PBR materials multiply their base-colour texture by
  `ShaderNodeVertexColor(layer_name="nv_surface_color")`;
- a temporary in-memory render that added white `nv_surface_color` values made
  the existing textures visible without changing lights or texture files.

The reciprocal runtime also writes `(0, 0)` to every UV corner. The environment
runtime writes a non-zero ad-hoc UV, but ignores the material's verified
`uv_policy` and `nv_nominal_tile_m`. The material bundle already carries valid
base-colour, normal and ORM textures plus nominal tile scales; replacing those
textures or increasing light intensity would not correct the contract defect.

Local camera coverage is also incomplete. Around the plan-bound waterwheel
anchor `(-185.2, -115.0, 43.15)`, the current production plan covers only two
of eight azimuth bins within 15 m, four within 30 m and six within 60 m. The one
accepted reciprocal role frame therefore proves one useful composition, not a
local 360-degree view.

## Considered approaches

1. **Increase exposure and replace textures.** This can brighten the frame but
   leaves the missing colour attribute and degenerate UVs intact. Rejected.
2. **Patch only the wheel material.** This improves one object while the other
   93 later meshes keep violating the shared material contract. Rejected.
3. **Repair both runtime material contracts, upgrade the existing wheel root,
   and add an audit-only local orbit.** This fixes the shared cause, preserves
   exact-218 identity, and produces honest 360-degree inspection evidence.
   Adopted as方案 A.

## Architecture

### 1. Blender material-input contract

Both `scripts/blender/apply_environment_modules.py` and
`scripts/blender/apply_reciprocal_route_modules.py` will apply the same bounded
contract before tangent generation:

1. inspect the assigned material's `uv_policy`, `nv_nominal_tile_m` and
   `nv_surface_color_input` metadata;
2. reject absent, non-finite or unsupported UV metadata;
3. project every polygon corner into a deterministic UV layer using the same
   policies already implemented by `build_synthetic_village.py`:
   `world-xy`, `dominant-axis-box`, `roof-slope`, `object-long-axis` and
   `leaf-card`;
4. divide world/object coordinates by the bound `nv_nominal_tile_m`, without
   modulo wrapping, so tiling frequency is metric within the synthetic scene;
5. create or reuse `nv_surface_color` as CORNER/FLOAT_COLOR, set every later
   mesh corner to linear white `(1, 1, 1, 1)`, and set it as active/render
   colour;
6. calculate tangents against the named UV layer and reject any degenerate UV,
   missing colour layer, wrong domain/type, wrong element count or tangent
   failure.

White is a neutral texture multiplier, not a visual override. Macro surface
variation remains owned by the base builder; Batch 22 does not invent unbound
palette data for later modules.

The helper remains self-contained in each content-addressed Blender runtime.
Moving it into an imported third file would add an unbound executable
dependency unless every request/report schema also bound that file's SHA. A
source-parity test will keep the two small implementations behaviourally
identical without weakening runtime provenance.

### 2. Open waterwheel geometry within instance 155

`waterwheel-wheel-001` keeps stable object identity, instance `155`, semantic
ID, material alias, anchor and canonical root. Only its deterministic mesh is
upgraded. The single mesh contains:

- a watertight open annular rim in the XZ wheel plane;
- one Y-axis hub aligned with the existing axle;
- 12 oriented radial spokes from hub to inner rim;
- 12 tangential paddles/buckets around the outside circumference;
- finite, consistently wound faces with no duplicate canonical roots.

The current axis-aligned overlapping spoke boxes are removed. The existing
axle, bracket, millrace, spill and tailwater roots `156..160` remain separate
and unchanged. The wheel envelope may grow only within the already declared
waterwheel assembly volume; if Phase 4.3 detects a new route or module
intersection, geometry must be corrected rather than allowlisted.

### 3. Audit-only local eight-direction orbit

Batch 22 adds a reusable, deterministic local-orbit audit plan around the
plan-bound waterwheel anchor. It emits eight candidates at azimuths
`0, 45, 90, 135, 180, 225, 270, 315` degrees, with explicit 12 m radius,
`anchor_z + 1.6 m` camera height, 65-degree horizontal field of view and look
target `anchor_z + 0.4 m`. IDs are `audit-waterwheel-az000` through
`audit-waterwheel-az315`.

The orbit plan binds the environment plan SHA, exact-218 build ID/blend SHA,
anchor coordinates, radius, height, FOV and ordered azimuth list. It is not
inserted into the canonical 180-camera production registry and never changes
its coverage claims. Every orbit frame uses the existing fresh preflight,
six-layer render and post-render v2 machinery.

Acceptance requires all eight azimuth bins to produce valid RGB, depth, normal,
instance and semantic layers. Every frame must contain at least one registered
watermill assembly instance from `155..160`; at least six of eight frames must
contain wheel instance `155`. These are local modeled-scene inspection gates,
not SfM/3DGS capture coverage or free-space proof.

### 4. Replaceable image2 design inputs

image2 will generate twelve independent, design-only references after the
implementation plan is approved:

- eight watermill context views corresponding to the eight audit azimuths;
- upstream flume/axle and underside bracket close-ups;
- weathered timber and aged metal material studies.

They provide component, weathering and silhouette ideas only. Each file has its
own prompt and SHA. All carry `camera_calibration=unknown`,
`geometry_consistency=not-verified`,
`training_use=forbidden-as-multiview` and `trust_effect=none`. They are not
claimed to be mutually consistent and cannot be used as SfM, NeRF or 3DGS
training evidence. The model and orbit validation—not the generated images—are
what make arbitrary local camera rotation possible in the synthetic scene.

Only the final twelve PNGs, twelve prompts, manifest, checksums and usage note
enter a clean Release archive. Contact sheets, failed attempts, browser
downloads and generation intermediates stay out of Git and Release.

## Data flow and identities

```text
Batch 21 plan-bound anchor
        |
        +--> environment runtime --> upgraded instance 155 mesh
        |                         --> material/UV contract on instances 131..175
        |
        +--> reciprocal runtime  --> material/UV contract on instances 176..218
        |
        +--> exact-218 build SHA
                 |
                 +--> unchanged Phase 4.3
                 +--> unchanged six-role caller
                 +--> bound local eight-direction orbit plan
                              |
                              +--> 8 x six-layer frames + machine report
```

Any geometry or material-runtime change produces fresh runtime-script SHA,
build request/report SHA, `.blend` SHA, build ID, render ID and audit-plan SHA.
No prior Batch 21 render is reused as Batch 22 acceptance evidence.

## Fail-closed behaviour

- A material that declares textured UV or `nv_surface_color` input cannot be
  assigned to a mesh lacking valid corresponding data.
- A UV policy or nominal tile scale missing from a reused PBR material rejects
  the build; no guessed scale or `(0, 0)` fallback is allowed.
- The material audit reopens the saved `.blend`; in-memory pre-save state is
  insufficient evidence.
- The exact canonical root count remains 218 and registry IDs remain unchanged.
- The orbit runner rejects a changed anchor, build identity, missing azimuth,
  duplicate camera ID, invalid layer, unregistered mask ID or insufficient
  assembly visibility.
- Existing Phase 4.3, preflight, visibility and post-render thresholds are not
  relaxed. A blocked view requires camera/geometry correction and a fresh
  content-addressed run.
- image2 inputs never promote geometry, metric, alignment or reconstruction
  trust.

## Verification

1. TDD reproduces the black-material cause: a material requiring
   `nv_surface_color` rejects a mesh without it; the fixed runtime creates a
   white CORNER/FLOAT_COLOR layer with one value per loop.
2. TDD proves reciprocal UVs are non-degenerate and both runtimes consume the
   material's policy and nominal tile scale. Missing/invalid metadata rejects.
3. Blender runtime tests prove instance `155` has one open rim, one hub,
   12 spokes and 12 paddles, finite vertices/faces and unchanged registry
   identity.
4. Rebuild fresh 175-root environment and production-bound exact-218 artifacts;
   reopen the exact `.blend` and emit a material-contract machine report for all
   reused textured meshes.
5. Run unchanged Phase 4.3 and the six-role caller with frozen policies.
6. Render all eight bound local-orbit candidates through the same preflight and
   six-layer/post-render path; audit exact azimuth coverage and assembly
   instance visibility.
7. Visually compare the accepted Batch 21 watermill RGB, the temporary
   hypothesis probe and the fresh Batch 22 RGB/orbit contact sheet. Visual
   review supplements but never replaces machine gates.

## Success criteria

- exact roots remain `1..218`; instances `155..160` and `189..195` retain their
  current identities;
- every later textured mesh reopens with a non-degenerate UV layer and required
  white `nv_surface_color`; no reused texture is multiplied by an absent black
  attribute;
- the wheel reads as an open rim with hub, 12 spokes and 12 paddles rather than
  a filled faceted disc;
- unchanged Phase 4.3 remains fully green and all six formal role renders pass;
- the local orbit contains exactly eight unique 45-degree azimuth bins, all
  eight frames pass six-layer/post-render validation, all see the assembly and
  at least six see wheel instance `155`;
- the Batch 22 Release is clean and the twelve references remain explicitly
  replaceable, independent and forbidden as multiview training evidence;
- documentation continues to distinguish finite exact-218 modeled content,
  arbitrary-coordinate synthetic render-on-demand expansion, and external real
  reconstruction.
