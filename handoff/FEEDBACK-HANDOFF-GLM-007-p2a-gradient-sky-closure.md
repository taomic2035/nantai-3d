# FEEDBACK-HANDOFF-GLM-007-p2a — Gradient sky closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md` §5 P2a

## 1. What was delivered

P2a (base-scene world/sky/haze) is closed as a path-limited commit. The
synthetic village base Blender builder now consumes a deterministic
gradient-sky contract instead of a flat Background colour.

### Owned paths (this commit only)

```text
pipeline/synthetic_village/weather_profile.py
pipeline/synthetic_village/render_quality_metrics.py  (new)
scripts/blender/build_synthetic_village.py
tests/test_synthetic_village_weather.py
tests/test_render_quality_metrics.py  (new)
handoff/FEEDBACK-HANDOFF-GLM-007-p2a-gradient-sky-closure.md  (this file)
```

No Codex-owned path was touched.

## 2. What changed

### 2.1 Sky model

- **Before**: `SKY_MODEL = "gradient-sky-volume-haze-approximation"` — a
  flat `Background.Color` node driven directly by `world_color`.
- **After**: `SKY_MODEL = "gradient-sky-approximation"` — a
  `ShaderNodeTexGradient` (LINEAR) → `ShaderNodeValToRGB` (3-stop ColorRamp)
  → `ShaderNodeBackground` chain. The World `node_tree` now varies the
  background vertically: zenith darker and bluer, horizon = `world_rgb`
  unchanged, below-horizon warmer and earthier.

### 2.2 Volume Scatter removed

A `ShaderNodeVolumeScatter` node was initially added for synthetic distance
haze. In EEVEE_NEXT it caused **100% black pixels** (measured 2026-07-24).
Diagnosis:

- Links remain valid after save/reload (4/4 valid).
- Plain Background renders correctly (0% black).
- Gradient+ColorRamp→Background renders correctly (0% black).
- Adding Volume Scatter → World Output.Volume causes EEVEE_NEXT to emit
  all-black frames.

The Volume Scatter node and its constants (`SKY_VOLUME_HAZE_DENSITY`,
`SKY_VOLUME_HAZE_COLOR`) were removed. `sky_model` no longer claims
"volume-haze". This is an honest retraction: the approximation does not
include distance fog.

### 2.3 Render quality metrics module (new)

`pipeline/synthetic_village/render_quality_metrics.py` is a pure-function
PNG measurement gate. It computes:

- `pixel_count`, `avg_rgb`
- `lum_p10`, `lum_p50`, `lum_p90` (Rec.601 luminance percentiles)
- `clipped_black_ratio` (all channels < 7)
- `clipped_white_ratio` (all channels > 248)
- `background_pixel_ratio` (pixels matching a target colour within tolerance)

Fail-closed: invalid PNG bytes → `ValueError("decode: ...")`. Uses
`img.tobytes()` (not deprecated `getdata()`). 14 TDD tests in
`tests/test_render_quality_metrics.py`.

## 3. Real Blender build evidence

Two canary builds were run from the same `build-canary` entry point, same
camera IDs, same resolution (1024×576), same colour management (AgX),
same weather request. The only difference is the builder script bytes
(gradient sky vs flat background).

### Before (flat Background, stashed sky changes)

```text
build_id:       e6d0c4eb6e0faae903e37075967aec198cd7353c7d214dde4bd60e62cb78d40f
blend_sha256:   6a1a86fe9028db07bef140b1ccec42b9efffae2fffccf60effcf49ba1e4a40a1
report_sha256:  d0b39602386bbf8bd071f0284e55db8105e4b457863c7a7de6cce9f55ec25fa5
schema_version: nantai.synthetic-village.blender-build-report.v1
synthetic:      true
```

### After (Gradient+ColorRamp sky)

```text
build_id:       69bb8bb03b9ba03f9e302e0d4d589499a39d2607269a4b6fb90f3339a846f8a8
blend_sha256:   b82b6c83a3f38c2017c7b9544a68f60ad94ba661eec88d0a745ec27d83eb7b16
report_sha256:  9d9e4aace19e30d79c95c6481bf65897b12f8e22ef607b479f6e25f79d023b63
schema_version: nantai.synthetic-village.blender-build-report.v1
synthetic:      true
```

### RGB measurement delta (after − before)

| Camera | avg_rgb Δ | lum_p50 Δ | lum_p10 | lum_p90 | clipped_black | clipped_white |
|---|---|---|---|---|---|---|
| preview-bridge | (−3.3, −4.5, −6.1) | −4.05 | 123→119 | 140→136 | 0.0→0.0 | 0.0→0.0 |
| preview-central | (−3.3, −4.5, −6.3) | −4.06 | 103→97 | 151→147 | 0.0→0.0 | 0.0→0.0 |
| preview-outer | (−4.5, −6.2, −8.5) | −4.04 | 117→111 | 155→146 | 0.0→0.0 | 0.0→0.0 |
| preview-upper | (−3.0, −4.2, −5.9) | −3.70 | 119→114 | 153→149 | 0.0→0.0 | 0.0→0.0 |

All four preview PNGs have different SHA-256 (the sky change is measurable).
The gradient sky is slightly darker overall (~4 luminance points), with
the B channel decreasing more than R — consistent with the derivation
(zenith bluer but darker, below-horizon warmer but darker). No black or
white clipping was introduced.

### Per-image SHA-256 (after)

| Image | SHA-256 |
|---|---|
| preview-bridge.png | `cc862bf3400a157eddae5ec778771043a1fa86a2a95dc2cf1f1e5e50e457b2c3` |
| preview-central.png | `6df03962b7a124286802187196159671eaf39aa108d4b5c5e5e10513ecea8d0a` |
| preview-outer.png | `78c66ad92ab3ba012428a6cbbf901b4192f91e023228070c98390314afa55a7a` |
| preview-upper.png | `ec37c0eaa2497ac7dadeb6f1cb1c66a92d4fd589ef5b5e110069ec09363803a1` |

## 4. Tests and lint

```powershell
.venv\Scripts\python.exe -m pytest `
  tests/test_synthetic_village_weather.py `
  tests/test_render_quality_metrics.py -q
# 38 passed

.venv\Scripts\python.exe -m ruff check `
  pipeline/synthetic_village/weather_profile.py `
  pipeline/synthetic_village/render_quality_metrics.py `
  scripts/blender/build_synthetic_village.py `
  tests/test_synthetic_village_weather.py `
  tests/test_render_quality_metrics.py
# All checks passed!

third\blender\blender.exe --background --version
# Blender 4.x (real headless build used for both canary builds above)
```

## 5. Honest limits (not promoted)

- **synthetic = true** unchanged. **L0 / preview-only** unchanged.
  `fidelity = simplified-pbr-not-render-parity` unchanged.
- The gradient sky is **not** Nishita, HDRI, physical atmosphere or volume
  fog. It is a vertical ColorRamp derived from `world_color`.
- Volume Scatter was attempted and **retracted** because it produced
  all-black EEVEE_NEXT renders. Distance haze is **not** implemented.
- The 23 pre-existing failures in
  `tests/test_synthetic_village_blender_runtime.py` are caused by the P0
  creek/contact build_id drift (commit `c1ca38b`), **not** by this sky
  change. They fail identically with sky changes stashed. They need
  runtime-fixture maintenance outside this commit's scope.
- No geometry trust, metric alignment, real-photo or training evidence
  was added. The five real-scene evidence items in §1 of the handoff
  remain absent.

## 6. Next queue item

P2b: material distortion audit — measure texel/UV scale variation on
terrain, creek banks and long walls; report per-object min/max or
percentile ratio; correct only the base builder's material mapping.
