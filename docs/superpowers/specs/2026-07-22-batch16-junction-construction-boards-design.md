# Batch 16 Junction Construction Boards Design

## Purpose

Batch 16 provides eight replaceable, synthetic construction-reference boards for
Blender modeling. The pack focuses on small transition details that are missing
from the current mountain-village blockout, especially the confirmed covered-
gallery side-entry versus roadside-vegetation conflict.

The boards are generic component references, not a pixel-level repair recipe for
one scene. Each board shows one coherent component family in four text-free views:
perspective, front, side, and top or cutaway. The views communicate form and
assembly intent while remaining explicitly uncalibrated.

## Asset Set

| File stem | Modeling purpose |
|---|---|
| `design-construction-gallery-side-entry-clearance-01` | Covered-gallery side entrance with an unobstructed pedestrian opening |
| `design-construction-vegetation-junction-opening-01` | Deterministic vegetation-edge termination around a path junction |
| `design-construction-stone-plinth-foundation-01` | Stone plinth, timber sill, footing, and terrain contact |
| `design-construction-path-drainage-transition-01` | Walkable path edge, drain, culvert, and runoff transition |
| `design-construction-ramp-stair-transition-01` | Ramp/stair landing and sloped-path connection |
| `design-construction-timber-post-base-01` | Timber post base, stone shoe, bracing, and damp separation |
| `design-construction-railing-termination-01` | Railing end, return, opening protection, and post attachment |
| `design-construction-eave-gutter-closure-01` | Eave end, gutter/downspout, fascia, and wall junction |

## Visual Language

- Text-free architectural component sheet on a warm neutral studio background.
- Weathered stone, dark timber, gray clay tile, packed earth, and restrained
  vegetation consistent with the existing synthetic mountain-village language.
- Four clearly separated views of the same plausible assembly; no people, logos,
  watermarks, dimension strings, callouts, or readable labels.
- Enough surrounding context to explain the connection, but no complete village
  scene and no scene-specific coordinates.

## Consumption Boundary

The PNGs and prompts live in the private, replaceable candidate area:

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch16/
```

They do not enter the asset registry, production geometry, Git, or Release. A
tracked handoff records only filenames, prompts, byte identity, intended use, and
limitations. Blender authors must create metric dimensions, connection anchors,
collision volumes, walkability, drainage semantics, LOD, and material slots
independently.

All eight assets remain:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
orthographic_projection=not-verified
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

## Acceptance Checks

1. Exactly eight non-empty PNG files and eight exact prompt files exist.
2. Every PNG is visually inspected for subject, four-view composition, forbidden
   text/watermarks, and useful assembly detail.
3. SHA-256, byte count, dimensions, and color mode are recorded from local files.
4. No generated view is described as measured, orthographic, topology-correct,
   structurally safe, or 360-degree coverage evidence.
5. Existing collaborator work, especially `web/data/`, remains untouched.
