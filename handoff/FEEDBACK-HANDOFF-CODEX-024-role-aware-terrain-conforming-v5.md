# FEEDBACK-HANDOFF-CODEX-024 — role-aware meshes and terrain-conforming v5

> Date: 2026-07-22
> Owner: Codex
> Baseline: `main@4f0d413`
> Formal result: exact-218 and Phase 4.3 pass; six-role caller is **3 accepted / 3 rejected**.

## Trust boundary

All outputs remain synthetic L0, `preview-only`, `modeled-unverified`, and
`trust_effect=none` (quality reports use `none-quality-filter-only`). They do
not prove real-photo textures, surveyed geometry, SfM/3DGS coverage, or
arbitrary-coordinate 360-degree completeness. Batch 19 image2 references are
design-only inputs and were not treated as calibrated multiview evidence.

## What changed

The universal floor/ceiling/two-wall tunnel was replaced by an explicit
canonical `geometry_family` per part. Missing, unknown, or semantic-
incompatible declarations fail closed in both Pydantic and Blender runtime.
The current families are open path, covered passage, bridge deck, building
shell, structural frame, drainage, retaining structure, guard, service prop,
and vegetation.

Open paths and bridge decks no longer receive invented ceilings. Declared
covered passages retain finite roofs. Building shells are view-through
portals instead of opaque rear walls; guards have bilateral rails and visible
semantic-compatible bases. Phase 4.3 samples only route-bearing parts, treats
an open upward miss as unbounded overhead, and requires a finite hit for a
covered route.

The first role-aware render exposed fixed-Z placement as a second root cause:
bridge/watermill/gallery floated 8–10 m above the analytic terrain at the
camera, while forest/lower-valley cameras were 10.36 m / 4.04 m below it.
Non-central modules now remain flat for the 12% route-slope gate but derive a
common floor from the maximum analytic terrain height across their exact part
run plus 0.5 m. This removed the underground false-green frames without
forcing a 22–27% terrain-following slope.

Relevant commits:

```text
e677db0 feat(scene): classify reciprocal mesh families
62ffe20 fix(blender): build role-aware reciprocal meshes
262edf3 fix(scene): materialize gallery roof clearance
33080b5 fix(blender): keep reciprocal role sightlines open
3dde9c9 fix(caller): expose quality failures and guard visibility
4f0d413 fix(scene): conform reciprocal routes above terrain
```

Verification at `4f0d413`:

```text
focused model/runtime: 180 passed
reciprocal module/runtime/probe/production/blender/batch: 260 passed
Ruff: clean
```

## Fresh v5 exact-218 identity

| field | value |
|---|---|
| build ID | `803880b3273073cebb71e96e94237395d8168575fdffdf7fee370e9ceaebd568` |
| reciprocal plan SHA | `35d0579a2c2c35739066532c893642a1ab5067d076234ade95dc8079bbc7e306` |
| runtime script SHA | `4f8b59a40544a275eb774fb7ed6b7de2def94f436e1f4ac010caab4139885366` |
| build request SHA | `16882122dd329a27a52294fb02785ff1695a8c2db54d39b54de51038144b7a3f` |
| build report SHA | `20e21d16dbf743dfc1b61c2266dd0b465a3360dffff5c42b375925125f10d7f7` |
| blend SHA / bytes | `67793f85ac337c0d06da6e0a30254d8cec5f0db6d17fc8612df7943982c2fd7e` / `150365933` |
| production plan binding | `54aced28d33adad63dcbb301be32ede28998e1d2996a0232b10a7df1f586cb3a` |
| camera registry binding | `ea2abab801fcff1a823276c3b5851666ec0f0a82907778d8cdaba9ae4f189d42` |
| registry | exact canonical roots `1..218` |

Private build directory:

```text
.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/
  803880b3273073cebb71e96e94237395d8168575fdffdf7fee370e9ceaebd568/
```

## Fresh Phase 4.3

| field | value |
|---|---|
| request SHA | `e2ae47f8bb72236600b531df8faadc38974206f8b81e0f5ee0dc6a1d10582a97` |
| report SHA | `7f0204ce65b73ace98bcca79a37d9ace9481f2ec7043a347db3cc30924c9275e` |
| probe script SHA | `26cf8adda6e2aa7f197b88c00b6251ecad94381dc616a8b736649810ca2b6b26` |
| outcome | `overall_passed=true` |

Counts are 6/6 route, 15/15 module-pair, 6/6 environment, and 6/6
attachment. Minimum measured width is about 1.32 m for the bridge and 1.40 m
elsewhere. Central and covered-gallery finite roof clearance is about 2.501 m;
open bridge/watermill/lower-valley overhead remains `None` by design.

## Formal six-role v5

Frozen post-render policy SHA:
`b60eabd0c9cf069b23982bf2cfb9149ea25add8c6d76df39541d5642cf880b17`.

```text
.nantai-studio/sv-prod-win/reciprocal-production-batches/
  role-aware-v5-terrain-conforming/
```

| role | result | evidence |
|---|---|---|
| central-courtyard-downhill | accepted | render `e9bb43dc…`, quality `573307ac…` |
| bridge-deck-crossing | rejected | upper-ground `0.382911 > 0.300000` |
| watermill-tailrace | rejected | upper-ground `0.344143 > 0.300000` |
| covered-gallery-underpass | accepted | render `04360eb4…`, quality `840858be…` |
| forest-orchard-boundary | rejected | upper-ground `0.438843 > 0.300000` |
| lower-valley-uphill | accepted | render `c6e6720d…`, quality `0311fcef…` |

Batch ID is
`de0410b7e1ced10c68228eeefff5e7b09620e3fffc8e4a81803f28458de07395`;
journal self SHA is
`558b9141fa31c50724254d7fa5ba42f00d6f2972458db71f0dc01787eb02cb35`;
journal file SHA is
`5e71ac6ae68fecacc58142df725bc176e61a40a036cd3ee8680e2dd86d05a1f0`.

All 43 role instances now pass the visibility gate. The three failures are
post-render quality failures only; no threshold was relaxed.

## Diagnostic RGB only

To inspect rejected frames, a separate policy changed only upper-ground max
from 0.30 to 1.0. Its distinct SHA is
`6a7d7f293a1507aaf988a44fa4c5066e3c813c9793e26d0b7794cb6e22ed9448`.
It is diagnostic-only and has no effect on the formal v5 decision.

```text
.nantai-studio/sv-prod-win/reciprocal-production-batches/
  role-aware-v5-diagnostic-ug1/
```

The six RGB frames confirm forest/lower-valley are no longer underground.
They also show the remaining product defect: bridge, watermill, and forest
parts are still arranged as small straight sequences in broad empty terrain,
so upper-frame ground dominates. Blockout forms are visibly class-aware but
remain dark, sparse, repeated primitives rather than final architecture.

## Next high-value task for GLM/Opus plan lane

Do not tune the `0.30` quality threshold, inject a generic camera pitch, or
raise modules back into the air. Instead:

1. Replace the arbitrary straight part runs for bridge, watermill, and forest
   with explicit non-collinear `part_layout` placements derived from their
   declared topology attachment, recipe relationships, and nearby built
   environment objects.
2. Keep each walkable segment flat or explicitly ramped within the existing
   12% gate; keep every part above analytic terrain and prove it again in the
   Blender environment-intersection probe.
3. Recompute each role camera from the actual route tangent plus the spatial
   envelope of all role parts. The camera must retain visibility of the full
   instance segment; do not optimize only the upper-ground metric.
4. Return only canonical plan/registry SHAs and machine reports. Codex will
   rebuild exact-218, run fresh Phase 4.3, six preflights/layers/visibility,
   formal post-render v2, and RGB review.

Batch 19 references may guide recognizable bridge-undercroft, watermill rear,
raised-house undercroft, stacked-route, and route-boundary composition, but
they remain replaceable design inputs with no geometry/training trust effect.

## Distance to real model + real texture target

This closes a real caller/runtime correctness gap and produces genuine
Blender meshes with PBR materials, but it is still a blockout milestone. The
remaining major gaps are: topology-authored final geometry, architectural
detail/UV quality, real-photo calibrated texture capture or reconstruction,
and external SfM + CUDA 3DGS training for actual scene reconstruction. A
six-role green result would prove these synthetic gates only; it would still
not turn the blockout into a real reconstructed village.
