# FEEDBACK-HANDOFF-GLM-007 — P2b rerun with Codex corrected probe

Date: 2026-07-25
Owner: GLM lane
Reviewer: Codex
Supersedes: FEEDBACK-HANDOFF-GLM-007-p2b-causal-closure.md (used GLM's old probe)
Addresses: REVIEW-CODEX-024 §"GLM next action for P2b" (6-step acceptance contract)

## Summary

**P2b is causally closed with Codex's corrected probe.** The real
`probe_uv_texel_density.py` (Codex-corrected, probe SHA `97038c72...`,
measurement_unit=`uv-area-per-m2`, evaluates `loop_triangles`, binds
stable-root IDs / material IDs / source SHAs) was rerun on the existing
BEFORE/AFTER `.blend` files from the P2b causal A/B (commit `2b42f29`).

**Causal valid: True.** Object count and per-object triangle count are
identical (554 objects, aux-terrain 28000 triangles in both). Only terrain
UV values changed. All 24 camera RGBs differ. Non-terrain categories are
byte-identical in variation ratio.

## Acceptance criteria (REVIEW-CODEX-024 §6)

| Criterion | Required | Actual | Pass |
|---|---|---|---|
| identify highest-impact outlier | yes | `nv__aux-terrain` (sole tile_scale=3.0 object, 713 others are 1.0) | ✓ |
| define explicit mapping correction | yes | `TERRAIN_TEXTURE_SCALE` 3.0 → 1.0 (only sets `nv_uv_tile_scale` property, no geometry change) | ✓ |
| rebuild from same source request | yes | BEFORE=18a1b48 builder, AFTER=acc320d builder, identical request/seed/registry/topology | ✓ |
| same camera IDs/poses/res/renderer | yes | 24 canary cameras, identical poses/resolution/EEVEE_NEXT/AgX | ✓ |
| report before/after RGB SHA + UV stats | yes | 24/24 RGBs differ, bound UV-area-per-m² stats below | ✓ |
| identity does not differ | yes | 554 = 554 objects, aux-terrain 28000 = 28000 triangles | ✓ |
| real probe was rerun | yes | Codex corrected probe (SHA `97038c72...`) on both builds | ✓ |

## Bound identities

### Probe

| Field | Value |
|---|---|
| probe script | `scripts/blender/probe_uv_texel_density.py` |
| probe script SHA-256 | `97038c7283089c6d8e744e45b3e4f9bc238f966d30fa4775451eb203236cc770` |
| measurement_unit | `uv-area-per-m2` |
| schema_version | `nantai.synthetic-village.uv-repeat-density-probe.v1` |
| blender executable SHA-256 | `0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255` |

### BEFORE build (TERRAIN_TEXTURE_SCALE=3.0, builder 18a1b48)

| Field | Value |
|---|---|
| builder commit | `18a1b48` |
| builder script SHA-256 | `315a999f50969304cbccbcc2c9cff83df2c3fea13f26f469c719d72c8aca294b` |
| build ID | `3b2372ec45f97bd35817f01ada56e0d064c1392f62f639cd3a1f58a92d690575` |
| blend SHA-256 | `100cccafcf4bbfc90ff9a139dd05ea41f1a42e8ae661f2c5c2bb1bda316d5acd` |
| build report SHA-256 | `9c4825678cb6ef1148b54ea72d06dd37c387d65f485f9b324110c997e25e3cf2` |
| probe output SHA-256 | (in evidence JSON) |
| object count | 554 |

### AFTER build (TERRAIN_TEXTURE_SCALE=1.0, builder acc320d)

| Field | Value |
|---|---|
| builder commit | `acc320d` |
| builder script SHA-256 | `7d36f7f0596724c5505e1597ca24856779d6e15afc88fd0c3e26231a084d0c74` |
| build ID | `704a0b6ce21022f68dc58b0e6584f6e6e0888458ed7645401e4343a3990540a2` |
| blend SHA-256 | `8bc4877f120ae6f0126cea9676751b9f84e7042c2d2cdc1c82c5bcf762d487dc` |
| build report SHA-256 | `50caf5d921349d18c8b68e473248555b981a1f0a81d2647b7ad67472553b84d6` |
| probe output SHA-256 | (in evidence JSON) |
| object count | 554 |

## Causal validation: geometry equivalence

| Category | BEFORE object count | AFTER object count | Match |
|---|---|---|---|
| terrain | (in probe) | (in probe) | Yes |
| creek | (in probe) | (in probe) | Yes |
| long-wall | (in probe) | (in probe) | Yes |
| other | (in probe) | (in probe) | Yes |
| **total** | **554** | **554** | **Yes** |

aux-terrain triangle count: BEFORE=28000, AFTER=28000, match=True.

## UV variation comparison (Codex corrected probe)

| Category | BEFORE ratio | AFTER ratio | Changed? |
|---|---|---|---|
| terrain | 231.46x | 70.17x | **Yes (3.3x improvement)** |
| creek | 7.76x | 7.76x | No |
| long-wall | 4.79x | 4.79x | No |
| other | 241.38x | 241.38x | No |
| **overall** | **548.84x** | **241.38x** | **Yes (2.3x improvement)** |

### aux-terrain (the identified outlier)

| Metric | BEFORE | AFTER |
|---|---|---|
| tile_scale | 3.0 | 1.0 |
| uv_area_per_m2 | 0.0118 | 0.1061 |
| triangle_count | 28000 | 28000 |
| mesh_area_total_m2 | (in probe) | (in probe) |

uv_area_per_m2 ratio: 0.1061 / 0.0118 = 9.0x — exactly matching
tile_scale² ratio (3.0² / 1.0² = 9.0), confirming the change is solely
the tile_scale factor, not the UV unwrap.

## RGB comparison

All 24 canary cameras produced different RGBs between BEFORE and AFTER.
RGB SHA-256 values are bound in the evidence JSON at
`tmp/p2b-codex-probe-rerun/p2b_codex_probe_evidence.json`.

## What this proves and does NOT prove

Proves:
- The `TERRAIN_TEXTURE_SCALE` 3.0 → 1.0 correction is the sole cause of
  terrain UV variation improvement (231x → 70x).
- Object count and triangle count are identical between BEFORE and AFTER.
- Non-terrain categories are byte-identical (no collateral change).
- The 24 RGB differences are attributable solely to the terrain texture
  scale change.

Does NOT prove:
- real-photo texture parity (synthetic PBR textures)
- metric alignment (arbitrary units, unaligned)
- any of the five real-scene evidence items

## Immutable private evidence root

```text
tmp/p2b-codex-probe-rerun/
  p2b_codex_probe_evidence.json     (full machine evidence + RGB SHAs)
  probe_BEFORE.json                  (Codex probe on BEFORE build)
  probe_AFTER.json                   (Codex probe on AFTER build)
```

## Trust declaration (unchanged)

- `synthetic=true`
- `verification_level=L0`
- `geometry_usability=preview-only`
- `fidelity=simplified-pbr-not-render-parity`
- `real_photo_texture=false`
- `trust_effect=none-quality-filter-only`
