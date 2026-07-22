# Batch 21 Watermill Construction Consumption Design

## Goal

Turn the existing `watermill-tailrace` role from a generic dark blockout into a
coherent watermill service scene that visibly connects the already-registered
wheel, axle, millrace and tailwater to a millhouse, platform, stair, guard and
retaining structure. The result remains synthetic L0, `modeled-unverified` and
`preview-only`; it does not become measured geometry or a real-photo texture.

## Current evidence

The accepted Batch 20 RGB for `watermill-tailrace` frames only seven generic
reciprocal-route meshes. The independently registered environment objects
`waterwheel-wheel-001` through `waterwheel-tailwater-001` exist in the same
exact-218 build, but their authored center is outside the role camera envelope.
The quality gate therefore passes while the product view still fails to read as
a watermill.

Batch 21 contributes two independent construction references for the service
side and tailrace side. They are design-only inputs with unknown camera
calibration and no shared geometry. Their value is the component relationship,
not pixel-derived coordinates.

## Considered approaches

1. **Duplicate a wheel inside the reciprocal module.** Visually immediate, but
   it creates a second wheel identity and mixes wheel semantics into the access
   panel root. Rejected.
2. **Retarget only the camera.** This can reveal the existing wheel but leaves
   the millhouse, stair and tailrace as generic passage boxes. Rejected as too
   shallow.
3. **Plan-bound shared anchor plus role-specific construction geometry.** Add
   one authored waterwheel assembly anchor to `LowerBridgeRecipe`, derive the
   reciprocal service layout and camera envelope from it, and retain one mesh
   root per existing reciprocal part. Recommended.

## Architecture

### Plan-bound assembly anchor

`LowerBridgeRecipe` gains `waterwheel_assembly_anchor_m`, initially equal to the
existing modeled center `(-185.2, -115.0, 43.15)`. The value is part of the
canonical EnvironmentModulePlan bytes. `apply_environment_modules.py` consumes
the anchor instead of inventing the wheel center locally; all current wheel,
axle, bracket, millrace, spill and tailwater offsets stay relative to it.

The anchor is authored synthetic placement, not survey evidence. Changing it
changes the environment plan SHA, base 175 build identity, reciprocal plan SHA,
exact-218 build identity and all downstream render identities.

### Anchor-relative reciprocal service layout

`build_default_reciprocal_route_module_plan` passes the bound environment plan
into the watermill recipe and layout builder. The seven existing instances
`189..195` move to anchor-relative positions forming a non-collinear approach,
service platform, axle access, creek-bank return and tailrace edge. Their stable
IDs, instance IDs, semantic IDs and material aliases remain unchanged.

The watermill role camera keeps its standing-eye position derived from the first
two route-bearing parts. Its look envelope additionally includes the plan-bound
wheel anchor, so the existing wheel becomes part of the intended composition.
Other five role cameras are byte-for-byte unaffected by this extra point.

### Role-specific meshes without registry expansion

`apply_reciprocal_route_modules.py` dispatches the seven watermill part IDs to
specialized deterministic builders:

- open timber millhouse frame with roof/eaves and a view-through wheel bay;
- supported plank maintenance platform;
- five-tread service stair with stringers;
- axle-access panel with bearing housing, not a duplicate wheel;
- stone creek-bank path;
- post-and-rail platform guard;
- retaining wall with a visible tailrace opening and channel floor.

Each part remains one canonical root and one semantic/material mesh. Compound
subcomponents live inside that mesh, so exact roots stay `1..218`. Declared
part envelopes include all subcomponents and roof overhangs; builders remain
finite/non-empty and keep the route clearance contract.

## Fail-closed behavior

- The environment runtime rejects a missing, non-finite or mismatched anchor.
- The reciprocal plan fails if the bound lower-bridge module or wheel identity
  is absent, or if the derived role route degenerates.
- Existing module-pair, module/environment, route width/clearance and topology
  attachment probes are unchanged and must pass against the fresh build.
- No quality threshold, terrain clearance, visibility requirement or
  post-render policy is relaxed.
- Batch 21 PNGs do not enter Git, the Blender request, the object registry or
  geometry trust. Their Release SHA remains documentation provenance only.

## Verification

1. TDD proves the EnvironmentModulePlan carries the anchor and the Blender-side
   wheel vertices move when only the plan anchor changes.
2. TDD proves the seven service parts are anchor-relative, non-collinear and the
   watermill camera envelope includes the wheel anchor while other roles do not.
3. Runtime geometry tests prove every specialized part is finite, non-empty,
   semantically compatible and structurally distinct from the generic family.
4. Rebuild fresh 175 and exact-218 artifacts; run unchanged Phase 4.3.
5. Run the six-role caller with frozen policies. Inspect the watermill RGB plus
   depth/normal/instance/semantic layers and compare it with the Batch 20 RGB.

## Success criteria

- exact canonical roots remain 218 with the same object/instance/semantic IDs;
- Phase 4.3 remains fully green without allowlists or threshold changes;
- all six formal role renders pass the existing policy;
- watermill RGB visibly includes the existing wheel and reads as a connected
  millhouse/platform/stair/tailrace system rather than repeated black portals;
- documentation explicitly retains synthetic L0 and preview-only boundaries.
