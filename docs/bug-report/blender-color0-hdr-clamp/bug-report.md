# Blender `COLOR_0` HDR clamp

## Bug diagnosis capsule

| Field | Evidence |
|---|---|
| Symptom | `field-terrace-007__terrace-levees` had 16 macro colours in Blender but exported as constant white `COLOR_0`. |
| Evidence | macOS arm64, Blender 4.5.11 LTS. The captured `.blend` measured `1.001709–1.051758`; mesh 475 in the final GLB measured only `(1,1,1)`. |
| Root cause | Blender's glTF exporter clamps the standard vertex-colour semantic to `[0,1]`. A region whose multipliers were all above one therefore collapsed to white. |
| Diagnostic strategy | Capture one failed staging directory, decode the GLB accessor, then inspect the same named mesh in the saved `.blend`. Compare with a minimal generic-attribute export. |
| Timeout strategy | After one captured build, stop full-scene parameter experiments and reduce the boundary to a three-vertex exporter probe. |
| Warning strategy | Never relax the final GLB audit or accept a report-only colour claim. Three unsuccessful exporter-setting changes require an architectural correction. |
| User-visible correction | Bright and dark source-derived macro variation survives into the Viewer instead of flattening bright regions to white. |
| Acceptance | `test_local_blender_authors_float_corner_surface_color`, `test_local_blender_normalizes_exported_surface_colors_to_float_vec4`, the complete GLB audit suite, and one real immutable preview build must pass. |

## Five-part bug report

1. **Reporter:** Codex, while running the real macOS local-preview acceptance.
2. **Reproduction:** Build the v1 surface profile with the verified
   `packed-earth-v2-be7dcd29` source revision and inspect mesh 475. Expected:
   non-constant float multipliers. Actual: constant white final `COLOR_0`.
3. **Root-cause analysis:** The Blender mesh and build report were correct.
   The value changed at the Blender glTF-export boundary. A minimal
   `_NV_SURFACE_COLOR` `FLOAT_VECTOR` probe preserved values above one, proving
   that the clamp belongs to the standard colour-semantic export path.
4. **Fix:** Keep `nv_surface_color` for Blender rendering and add a private
   `_NV_SURFACE_COLOR` float transport attribute. Export generic attributes,
   then replace the exporter-produced colour accessor with a bounded float
   `VEC4` `COLOR_0` accessor and remove the transport semantic. The independent
   audit still validates only the final GLB bytes.
5. **Verification:** The focused probes must fail without the transport path,
   pass with it, preserve alpha one and values above one, remove the private
   semantic, and remain compatible with all legacy material-audit fixtures.
