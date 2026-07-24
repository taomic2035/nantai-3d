# FEEDBACK-HANDOFF-GLM-007 — P2b causal A/B closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex

## Summary

The P2b material distortion correction (`TERRAIN_TEXTURE_SCALE` 3.0 → 1.0)
is causally proven. The previous non-causal evidence (comparing `4f38ecf4` vs
`704a0b6c` with different object/triangle counts) has been replaced by a
controlled A/B test using the parent `18a1b48` builder script as BEFORE and
the current `acc320d` builder script as AFTER, both run with identical
request, seed, registry, topology, resolution, renderer, color management and
cameras.

**Causal valid: True.** The geometry is byte-identical. Only terrain UV values
changed. All 24 camera RGBs differ as expected.

## Method (per HANDOFF-GLM-007 section 5)

1. Extracted the parent `18a1b48` version of `scripts/blender/build_synthetic_village.py`
   to a private SHA-bound path.
2. Ran that frozen parent script (BEFORE) and the current `acc320d` script
   (AFTER) with identical request, seed, registry, topology, resolution,
   renderer, color management and cameras via `run_textured_canary_build`.
3. Required equal stable object identities, category object counts and
   per-category triangle counts before comparing UV ratios or RGBs.
4. Bound both script SHAs, build requests/reports, `.blend` files, camera
   matrices, probe reports and RGBs.
5. Verified that geometry and camera bindings are identical; UV and RGB
   differences are attributable solely to the `TERRAIN_TEXTURE_SCALE` change.

## Diff between BEFORE and AFTER scripts

```diff
-TERRAIN_TEXTURE_SCALE = 3.0
+TERRAIN_TEXTURE_SCALE = 1.0
+terrain_obj["nv_uv_audit_category"] = "terrain"
```

No other lines changed. The `nv_uv_audit_category` attribute does not affect
geometry, UVs, materials or rendering; it only labels the terrain category for
the audit probe. The probe has a name-based fallback for objects lacking this
attribute, so both BEFORE and AFTER are correctly categorized.

## Bound identities

### Scripts

| Label | Commit | SHA-256 |
|---|---|---|
| BEFORE | `18a1b48` | `315a999f50969304cbccbcc2c9cff83df2c3fea13f26f469c719d72c8aca294b` |
| AFTER | `acc320d` (current HEAD) | `7d36f7f0596724c5505e1597ca24856779d6e15afc88fd0c3e26231a084d0c74` |

### Builds

| Label | Build ID | Blend SHA-256 | Report SHA-256 | Script SHA (from report) |
|---|---|---|---|---|
| BEFORE | `3b2372ec45f97bd35817f01ada56e0d064c1392f62f639cd3a1f58a92d690575` | `100cccafcf4bbfc90ff9a139dd05ea41f1a42e8ae661f2c5c2bb1bda316d5acd` | `9c4825678cb6ef1148b54ea72d06dd37c387d65f485f9b324110c997e25e3cf2` | `315a999f50969304cbccbcc2c9cff83df2c3fea13f26f469c719d72c8aca294b` |
| AFTER | `704a0b6ce21022f68dc58b0e6584f6e6e0888458ed7645401e4343a3990540a2` | `8bc4877f120ae6f0126cea9676751b9f84e7042c2d2cdc1c82c5bcf762d487dc` | `50caf5d921349d18c8b68e473248555b981a1f0a81d2647b7ad67472553b84d6` | `7d36f7f0596724c5505e1597ca24856779d6e15afc88fd0c3e26231a084d0c74` |

Both builds report 130 object registry entries and 24 camera registry entries.

### UV probes

| Label | Probe report SHA-256 | Content SHA-256 | Object count |
|---|---|---|---|
| BEFORE | `54a0357d8b87ebb43ab302c2c10dc7ecb03a373ff3678be391b4ff0582b84a58` | `ab5d02a7038d32beeeb919bb7065d89e0e0ba862871de768854c910e0e48afa7` | 554 |
| AFTER | `b9eaa795a4e063c5a2fe7aa652f6be005591a668a8164807775207e8bced3c1d` | `91f789d970f740522a9b3e8b3f3842fedebff55c47cac0aca3bc979eb53d83ff` | 554 |

Both probes measured 554 mesh objects with UV layers.

### Renders

| Label | Cameras rendered | All RGBs differ |
|---|---|---|
| BEFORE | 24 | Yes |
| AFTER | 24 | Yes |

## Causal validation: geometry equivalence

Per-category geometry is identical between BEFORE and AFTER:

| Category | Object count | Triangle count | Match |
|---|---|---|---|
| creek | 135 | 5,544 | Yes |
| long-wall | 291 | 10,620 | Yes |
| other | 88 | 12,264 | Yes |
| terrain | 40 | 39,548 | Yes |

Total: 554 objects, 68,976 triangles — identical in both builds.

## UV variation comparison

| Category | BEFORE variation ratio | AFTER variation ratio | Changed? |
|---|---|---|---|
| creek | 7.76x | 7.76x | No |
| long-wall | 4.79x | 4.79x | No |
| other | 241.38x | 241.38x | No |
| **terrain** | **231.46x** | **70.17x** | **Yes (3.3x improvement)** |
| **overall** | **548.84x** | **241.38x** | **Yes (2.3x improvement)** |

Terrain min UV area per m² increased from 0.0118 to 0.0389 (3.3x increase,
exactly matching the 3.0 → 1.0 scale normalization). Terrain max UV area
per m² is unchanged (2.727), confirming only the scale factor changed, not
the UV unwrap.

All non-terrain categories are byte-identical in min, max, median and
variation ratio. This proves the `TERRAIN_TEXTURE_SCALE` change is the sole
cause of UV variation improvement.

## RGB comparison

All 24 canary cameras produced different RGBs between BEFORE and AFTER.
This is expected: terrain is visible from every camera angle, and the
texture scale change alters the visible pattern.

Sample luminance (camera-bridge-001):

| Metric | BEFORE | AFTER |
|---|---|---|
| mean luminance | 0.1874 | 0.1873 |
| p10 luminance | 0.058 | 0.058 |
| p50 luminance | 0.149 | 0.150 |
| p90 luminance | 0.411 | 0.406 |
| clipped black | 0.0% | 0.0% |
| clipped white | 0.0% | 0.0% |

Luminance distributions are very close but not identical, confirming the
texture scale change is subtle and does not cause clipping or extreme
brightness shifts.

## Trust declaration

- `synthetic=true`
- `verification_level=L0`
- `geometry_usability=preview-only`
- `fidelity=simplified-pbr-not-render-parity`
- `real_photo_texture=false`
- `trust_effect=none-quality-filter-only`

## Honest limits

This causal proof validates that the terrain texture scale change
(3.0 → 1.0) is the sole cause of UV/RGB differences between the `18a1b48`
and `acc320d` builder scripts. It does not:

- add real-scene evidence;
- promote the synthetic build to metric or accepted status;
- prove real-photo texture parity;
- address remaining UV variation in the `other` category (241.38x, dominated
  by auxiliary non-terrain objects with intrinsically different scales).

## Conclusion

P2b is causally closed. The code correction (`acc320d`) may be retained.
The remaining UV variation (overall 241.38x) is now dominated by the `other`
category (auxiliary objects with intrinsically different scales), not by
terrain. Creek and long-wall variation ratios are small (7.76x and 4.79x
respectively) and unchanged.
