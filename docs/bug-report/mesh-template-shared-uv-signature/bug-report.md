# Mesh template shared UV signature drift

## Bug diagnosis capsule

| Field | Evidence |
|---|---|
| Symptom | The real macOS Blender gate exited with code 17 instead of producing the exact 33 verified v1 templates. |
| Evidence | Blender 4.5.11 reported `TypeError: _apply_textured_uvs_and_tangents() missing 2 required positional arguments: 'request' and 'surface_runtime'` at `build_mesh_asset_bundle.py:700`. |
| Root cause | The shared textured UV helper gained request and surface-runtime inputs in `502e3ff`, but the v1 mesh-template consumer retained its one-argument call. |
| Diagnostic strategy | Capture bounded Blender stdout/stderr, trace the failed call, then compare every repository caller with the current helper signature. |
| Timeout strategy | If one instrumented real build did not locate the boundary, reduce it to a direct Blender import probe before changing code. |
| Warning strategy | Do not weaken the real Blender gate or copy the shared UV implementation into the template builder. |
| User-visible correction | Verified v1 template builds work again; their geometry and material algorithms are unchanged. |
| Acceptance | The AST contract test requires the complete shared call signature, then the real Blender gate must produce all 33 templates. |

## Five-part bug report

1. **Reporter:** Codex, during the near-mesh v2 related regression gate.
2. **Reproduction:** Run
   `tests/test_mesh_asset_blender_runtime.py::test_real_blender_builds_exact_33_verified_templates`
   with Blender 4.5.11. Expected: 33 audited templates. Actual: deterministic
   Python exit code 17 before the first artifact.
3. **Root-cause analysis:** Material publication and Blender startup succeeded.
   The failure occurred at the shared UV/tangent API boundary after the surface
   realism change expanded that function from one to three parameters.
4. **Fix:** Build the compatibility material request once and pass it, together
   with an explicit `None` surface runtime, through `_build_asset`. The v1
   request has no surface profile, so shared surface-colour behavior remains a
   deliberate no-op.
5. **Verification:** The new AST regression test fails on the stale call and
   passes on the three-argument call. The complete real Blender file passes
   `3 passed` and rebuilds the exact 11 × 3 artifact matrix.
