# FEEDBACK-HANDOFF-GLM-007-p2b — Material UV texel density audit closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md` §5 P2b

## 1. What was delivered

P2b (material distortion audit) is closed as a path-limited commit. A
pure-function texel-density audit module and a Blender UV probe script were
added. No Codex-owned path was touched.

### Owned paths (this commit only)

```text
pipeline/synthetic_village/material_uv_audit.py        (new)
scripts/blender/probe_uv_texel_density.py               (new)
tests/test_material_uv_audit.py                          (new)
handoff/FEEDBACK-HANDOFF-GLM-007-p2b-material-uv-audit-closure.md  (this file)
```

## 2. What changed

### 2.1 Texel density audit module (new)

`pipeline/synthetic_village/material_uv_audit.py` is a pure-function module
that computes the **effective tile size** (texture repeat distance in metres)
for each material/object-category combination:

```
effective_tile_m = nominal_tile_m * tile_scale
```

Where `nominal_tile_m` comes from the material registry
(`pipeline/synthetic_village/material_bundle.py::MATERIAL_PARAMETERS`) and
`tile_scale` comes from the object's `nv_uv_tile_scale` Blender property
(default 1.0).

The report includes:
- `per_object`: per-object effective tile sizes with material/category metadata
- `by_category`: min/max/median/material_count per object category
- `overall_min_tile_m`, `overall_max_tile_m`: global extremes
- `variation_ratio`: max/min across all audited objects
- `extreme_variation`: True when variation_ratio > 3.0

Fail-closed: empty inputs, non-positive nominal_tile_m, non-positive
tile_scale, unsupported uv_policy, and missing material_id all raise
ValueError. Uses FrozenModel (extra=forbid, frozen=True, strict=True) for
all record types.

### 2.2 Blender UV probe script (new)

`scripts/blender/probe_uv_texel_density.py` is a headless Blender script that
opens a `.blend` file, measures UV area vs mesh face area for each mesh
object, and reports per-object texel density ratios. Pure measurement: does
not modify the scene. Categorises objects as terrain/creek/wall-building/other
by name pattern.

Output is JSON delimited by `UV_TEXEL_DENSITY_JSON_START` /
`UV_TEXEL_DENSITY_JSON_END` markers for easy parsing.

### 2.3 Key finding: extreme variation is inherent, not just terrain scaling

Using the real `MATERIAL_PARAMETERS` from `material_bundle.py` and the known
terrain `tile_scale=3.0`:

| Category | Material | nominal_tile_m | tile_scale | effective_tile_m |
|---|---|---|---|---|
| terrain | packed-earth-01 | 3.0 | 3.0 | 9.0 |
| terrain | terrace-soil-01 | 4.0 | 3.0 | 12.0 |
| creek | shallow-water-01 | 5.0 | 1.0 | 5.0 |
| creek | creek-rock-01 | 2.5 | 1.0 | 2.5 |
| wall | pale-plaster-01 | 3.5 | 1.0 | 3.5 |
| wall | dark-timber-01 | 1.6 | 1.0 | 1.6 |

- **Default variation_ratio = 7.5** (12.0 / 1.6) — extreme.
- **After normalising terrain tile_scale to 1.0**: variation_ratio = 3.125
  (5.0 / 1.6) — still above 3.0 because the inherent material nominal tile
  size range (1.6 to 5.0) is itself wide.

This is a real finding: reducing terrain texture stretching alone does not
bring variation below the extreme threshold. The `dark-timber-01` material
at `nominal_tile_m=1.6` is the primary outlier — its texture repeats every
1.6 m, while `shallow-water-01` repeats every 5.0 m.

### 2.4 Full registry variation (for context)

Across all 24 registered materials, `nominal_tile_m` ranges from 0.35
(`bamboo-leaf-01`, leaf-card policy) to 6.0 (`rice-paddy-water-01`,
world-xy policy) — a ratio of 17.1x. However, leaf-card materials use a
different UV policy (billboard cards, not tiled surfaces) and should not be
compared directly with world-xy/dominant-axis-box tiled surfaces.

## 3. Tests

```text
.venv\Scripts\python.exe -m pytest tests/test_material_uv_audit.py --noconftest -v
# 15 passed in 0.10s
```

Test coverage:
- Effective tile size calculation (nominal × scale)
- Category aggregation (min/max/median/material_count)
- Overall min/max correctness
- Variation ratio and extreme flag
- Improvement when terrain scale reduced (ratio decreases, max shifts)
- Fail-closed: empty materials/objects, invalid nominal_tile_m, invalid
  tile_scale, unsupported uv_policy, missing material_id
- Frozen model enforcement

## 4. Honest limits (not promoted)

- **Pure measurement, no trust promotion**: this module measures UV
  projection parameters only. It does not promote geometry trust, does not
  claim render parity, and does not validate against real-world texel
  density targets.
- **No real Blender measurement yet**: the probe script
  (`probe_uv_texel_density.py`) is provided but has not been run against
  a real `.blend` file in this commit. The audit module's test data uses
  representative values from `MATERIAL_PARAMETERS`, not measured UV areas.
- **Variation ratio > 3.0 is not a gate**: it is a reporting threshold.
  The audit does not fail a build; it only reports the variation so that
  texture stretching can be assessed objectively.
- **nominal_tile_m values are design constants**, not measured from real
  reference photos. They define the intended texture repeat distance; the
  actual visual repeat may differ due to UV unwrapping, object scale, and
  material mapping.
- No geometry trust, metric alignment, real-photo or training evidence was
  added. The five real-scene evidence items in §1 of the handoff remain
  absent.

## 5. Next queue item

P3: bind every streamed chunk and LOD payload SHA — content-address each
chunk's PLY bytes so that cross-worker caching and provenance verification
can operate on real asset version/SHA keys rather than deterministic
synthetic proxies.
