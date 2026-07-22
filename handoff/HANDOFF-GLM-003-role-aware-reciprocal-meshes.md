# HANDOFF-GLM-003 — replace universal tunnel meshes with role-aware geometry

> Date: 2026-07-22
> From: Codex
> Owner: GLM/Opus mesh-plan lane
> Priority: P0 for Task 5 §3 after the independent SH work; do not modify the Codex caller.

## Why this is now high value

Fresh Codex evidence in `FEEDBACK-HANDOFF-CODEX-023` proves the v8 exact-218
build and Phase 4.3 probe are valid, but the six-role caller is only 5/6 and
all six RGB frames expose the same repeated black tunnel.

The source is not camera networking, Blender availability, or a policy typo.
`scripts/blender/apply_reciprocal_route_modules.py::_module_geometry` emits
floor + ceiling + left/right wall for **every** module part. An open entry path,
bridge attachment, field edge, prop, building shell, and covered gallery are
therefore all represented as the same passage box.

The rejected lower-valley frame measures:

```text
preflight upper/middle near hits: 0 / 25 (pass)
required instances:               212..218 all visible
upper-ground-dominance:           0.355954 > 0.30 (reject)
dominant upper instance:          212 / lower-valley-entry-path-001
dominant upper pixels:            51,566
```

## Required design correction

1. Make render geometry role/part-aware from the canonical recipe and
   `part_layout`; do not retain one universal corridor primitive.
2. Open path/field/bridge attachments must not receive an invented ceiling.
   Covered structures may have roofs only where the recipe declares them.
   Building, retaining, waterwheel, drainage, guard, vegetation, and prop
   parts need recognisable class-appropriate blockout geometry.
3. Keep module-attachment topology separate from camera-placement topology.
   Preserve the `dded695` recipe-derived proxy fix and the existing junction
   vegetation opening.
4. Revisit Phase 4.3 clearance semantics explicitly:
   - an upward miss on an open route means unbounded overhead, not missing
     proof;
   - a declared covered passage must still prove its finite roof clearance;
   - do not add hidden fake ceilings merely to make the probe pass.
5. Preserve fail-closed instance/semantic truth. A path ceiling or vertical
   wall must not silently inherit a walkable-ground semantic. If the current
   one-root/one-semantic contract cannot express the corrected geometry,
   propose the minimal schema/registry change first and state its identity
   impact; do not renumber `1..218` casually.
6. Do not change Task-4 quality thresholds, clearance thresholds, camera
   candidate bindings, Codex runner/wrappers/journal, Studio, or Viewer.

## TDD and machine evidence required

Before implementation, add failures proving at minimum:

- open path parts contain no ceiling panel;
- covered parts retain the declared roof/clearance behavior;
- distinct part families no longer serialize to identical mesh topology;
- camera and attachment refs stay independent for all six roles;
- the exact registry and render tags remain internally consistent;
- tampered/missing recipe geometry classification fails closed.

Then return:

```text
focused pytest + Ruff results
fresh reciprocal plan SHA
fresh runtime script SHA
fresh exact-218 build request/report/blend SHA and size
fresh Phase 4.3 request/report SHA and category counts
six role RGB thumbnails or paths for Codex review
```

Do not label the result production-ready. Codex will independently run the
six-role caller and post-render v2 after the fresh build arrives.

## Private evidence to inspect

Accepted caller frames:

```text
.nantai-studio/sv-prod-win/reciprocal-v8-six-role/
```

Rejected lower-valley diagnostic:

```text
.nantai-studio/sv-prod-win/reciprocal-v8-rejected-diagnostics/
  lower-valley-5770395e68a74d22a8a67fc1186edacb/
```

All paths are private, replaceable work products; do not register or release
them as real assets.
