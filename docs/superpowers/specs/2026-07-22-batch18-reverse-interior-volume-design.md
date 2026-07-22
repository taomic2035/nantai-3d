# Batch 18 Reverse and Interior Volume Design

## Purpose

Batch 18 adds eight replaceable synthetic design references for two remaining
free-roaming scene gaps: reverse observation of previously designed outdoor
nodes, and continuous interior/exterior or lower/upper transitions. The pack is
for closing modeled volumes and bidirectional routes, not for producing another
set of front-facing scenic views.

## Asset Set

| File stem | Reference mode | Modeling purpose |
|---|---|---|
| `design-reverse-rear-service-alley-uphill-01` | Batch 17 rear-service alley | Opposite-direction lane, rear facades, drains, stairs, foundations and uphill skyline |
| `design-reverse-courtyard-covered-edge-01` | Batch 17 courtyard | Covered-edge viewpoint back across the courtyard, multiple exits and stair returns |
| `design-reverse-gallery-undercroft-outbound-01` | Batch 17 undercroft | Opposite-end traversal, beams, posts, cellar threshold and daylight exits |
| `design-reverse-bridge-opposite-bank-01` | Batch 17 bridge | Opposite-bank view of soffit, abutment returns, creek bed and route connections |
| `design-interior-through-workshop-01` | New independent scene | Dual-sided workshop connecting a courtyard and service alley |
| `design-interior-watermill-machinery-tailrace-01` | New independent scene | Watermill room, machinery supports, wheel connection and visible tailrace |
| `design-vertical-stair-roof-landing-01` | New independent scene | Switchback stair, intermediate landing, under-stair volume, eaves and roof access |
| `design-threshold-gatehouse-three-way-01` | New independent scene | Deep gatehouse threshold with three readable route branches and drainage continuity |

## Visual Language

- Photorealistic natural-design reference for a large, weathered mountain village.
- Neutral overcast daylight with readable shadows and no crushed-black passages.
- Stone plinths and retaining walls, dark timber frames, earth plaster, gray clay
  tiles, packed-earth or stone paving, restrained moss and vegetation.
- Eye-height wide-angle framing with near, middle and far depth layers, multiple
  connected elements, and at least two readable route continuations.
- No people, animals, vehicles, modern signage, wires, logos, captions or
  watermarks; no floating buildings, impossible stairs or abruptly cut terrain.

## Reference Boundary

The first four assets use one Batch 17 image each as a visual and layout
reference. Image generation is asked for a plausible reverse-side design, but no
pixel or camera correspondence is assumed. The result is not a calibrated
opposite camera, not a matched pair, and not usable as SfM/NeRF/3DGS multiview
training evidence.

The remaining four assets are independent scenes in the same generic material
family. They add missing interior and vertical-volume ideas without claiming to
belong to any existing coordinate system.

## Consumption and Publication Boundary

All PNGs, prompts, source bindings, queues and QA contact sheets remain in:

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/
```

They do not enter the asset registry, Release, production geometry or Git. Only
the design, plan and a concise tracked feedback document are committed. Modelers
must independently define dimensions, topology, collision, walkability,
occlusion closure, UVs, materials, LOD and production-camera visibility.

Every candidate remains:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

## Acceptance Checks

1. Exactly eight non-empty PNGs and eight exact primary prompt files exist;
   accepted correction edits additionally retain their exact correction prompts.
2. The four reference-driven jobs bind the exact source filename and SHA-256.
3. Every PNG is inspected at source resolution for requested volume, route
   continuity, forbidden text/watermarks and obvious impossible geometry.
4. SHA-256, bytes, dimensions, color mode, primary prompt identity and any
   correction-prompt identity are recorded.
5. A private contact sheet supports visual QA but is never listed as a source.
6. No candidate is described as measured, metrically consistent, calibrated,
   watertight, training-ready or proof of 360-degree coverage.
7. Existing collaborator work, including `web/data/`, remains untouched.
