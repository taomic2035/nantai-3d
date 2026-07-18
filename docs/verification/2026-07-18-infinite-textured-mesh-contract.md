# Infinite textured mesh chunk contract verification

Date: 2026-07-18

## Scope

This receipt covers Slice A of
`docs/superpowers/specs/2026-07-18-infinite-textured-mesh-chunks-design.md`:

- immutable, content-addressed mesh-template bundle verification;
- independent GLB material, primitive, and indexed-triangle measurement;
- deterministic signed-coordinate mesh chunk manifests;
- exact same-origin Studio mesh-manifest and immutable-template routes;
- fail-closed behavior for absent, redirected, malformed, or changed evidence.

It does not claim that textured mesh chunks are already rendered by the Viewer.

## Delivered commits

- `cb3e52a` — infinite textured mesh chunk design;
- `62eeb5d` — Slice A implementation plan;
- `e2d68f4` — immutable audited mesh-template bundles;
- `2c004a7` — deterministic signed-coordinate chunk manifests;
- `fed2bf0` — verified Studio mesh chunk and template routes;
- `44215cc` — pre-existing full-repository Ruff gate cleanup.

All commits were pushed to `origin/main`.

## Bundle and manifest evidence

The bundle verifier requires:

- canonical manifest bytes and canonical bundle identity;
- content-addressed `objects/<sha256>.glb` paths;
- exact current byte count and SHA-256;
- embedded PBR images with no external GLB resource;
- exact material source and material-bundle identities;
- UV and tangent evidence on every primitive;
- independently measured primitive and indexed-triangle counts;
- sorted unique asset/material closure;
- request-time re-verification before any GLB byte is returned.

The chunk builder consumes only
`MockLayoutGenerator(world_seed).generate_chunk(cx, cy)`. It records:

- signed safe-integer chunk coordinates;
- exact `world_offset = (cx * 200, cy * 200, 0)`;
- layout, terrain, mesh-bundle, and material-bundle identities;
- world-anchored shared-edge terrain vertices;
- deterministic road, water, building, vegetation, and prop records;
- AABB derived from transformed template bounds rather than registry filename
  or engine-name inference;
- fixed truth fields:
  `synthetic=true`, `geometry_usability=preview-only`,
  `coordinate_confidence=synthetic-layout`, `metric_alignment=false`, and
  `real_photo_textures=false`.

The canonical chunk has no `/api/` path. Runtime same-origin template URLs are
an outer projection and do not alter the chunk content key.

## Cross-process determinism probe

Two fresh Python 3.13 processes independently built chunk `(-2, 3)`, seed 42,
LOD 1 from the same hermetic eleven-asset bundle. Both produced:

- canonical manifest SHA-256:
  `021e81f98ea435cbf46f6ef09e1a024c908f79ab4e83db36e74716a106ee405f`;
- content key:
  `508c0ff0f41fc54e0f040f7d5e7fd59a509ecb71d20f62858b46a66d750c4905`;
- canonical byte count: `11735`;
- instances: `21`;
- terrain vertices: `25`;
- projected template URLs: `10`, identical and sorted in both processes.

This proves byte identity for the tested process/runtime pair. It does not yet
claim cross-operating-system identity for production template GLB bytes.

## Loopback HTTP probe

A real `ThreadingHTTPServer` instance served a hermetic complete bundle without
writing any project file.

Chunk request:

- route: `/api/world/mesh-chunk/-2/3.json?lod=1`;
- GET: `200`;
- HEAD: `200`, same ETag and content length, empty body;
- conditional GET: `304`, same ETag, empty body;
- content type: `application/json; charset=utf-8`;
- cache control: `no-store`;
- runtime payload bytes: `16020`;
- runtime payload SHA-256:
  `33a4f8bc2620a0590a6ae91c0debad2e8f020065e15e8573852dee556a46ff5f`.

Template request:

- GET: `200`;
- content type: `model/gltf-binary`;
- cache control: `public, max-age=31536000, immutable`;
- bytes: `2136`;
- response and descriptor SHA-256:
  `b8949c6e93f304b03dccd3d310d18c66b26dece18466d0865c26c028313ea885`;
- response bytes matched the independently prepared GLB exactly.

A before/after recursive project-file digest map was identical. No chunk JSON,
template copy, registry mutation, or trust-root write occurred.

## Automated gates

Commands used the repository virtual environment because the shell-level
`/Users/taomic/bin/python3` does not contain pytest.

- `.venv/bin/python -m pytest -q`
  - `1186 passed, 124 skipped, 1 warning in 377.35s`;
  - the warning is the existing non-finite alignment adversarial test reaching
    a NumPy overflow path before fail-closed rejection.
- `node --test web/viewer/*.test.mjs`
  - `130 passed, 0 failed`.
- `node --test web/studio/*.test.mjs`
  - `75 passed, 0 failed`.
- `.venv/bin/python -m ruff check .`
  - passed.
- `.venv/bin/python -m compileall -q pipeline scripts tests`
  - passed.
- `git diff --check`
  - passed.

The focused gates additionally reported:

- mesh bundle, GLB audit, and local preview: `37 passed`;
- mesh chunk plus layout/render-on-demand regressions: `94 passed`;
- Studio server plus mesh bundle/chunk: `120 passed`;
- new mesh HTTP cases: `9 passed`.

## Proven

- Immutable mesh template evidence is independently audited and hash verified.
- Signed-coordinate chunk manifests are deterministic and path-free.
- The manifest records an explicit flat synthetic terrain recipe rather than
  claiming consumption of the layout's nonexistent heightmap file.
- Same-origin runtime routes support GET, HEAD, strong ETag, and 304.
- Template GLBs use immutable content-addressed caching.
- Invalid or missing opt-in, bundle, material identity, coordinates, bounds,
  or template bytes fail closed without a mesh placeholder.
- The existing PLY route, local textured preview route, Studio tests, and
  Viewer tests remain green.

## Not yet proven

- Production-quality near/far geometry for all eleven template asset IDs.
- A published production mesh-asset bundle below the real project root.
- Viewer GLB template loading, instancing, terrain/ribbon construction, LOD
  hysteresis, and GPU LRU disposal.
- Arbitrary-coordinate textured browser roaming.
- Visual quality across all six weather states in streamed mesh mode.
- Alignment or compositing with real 3DGS.
- Real reconstruction quality, metric alignment, or real photo textures.

The next high-value slice is the complete eleven-template production bundle,
followed by Viewer streaming. Until both exist, the current visible Viewer
continues to offer either the finite textured canary or the arbitrary-coordinate
Gaussian world, not an infinite textured mesh world.

## Worktree state

After the implementation pushes, `main` matched `origin/main`. The pre-existing
collaborator-owned modification in `tests/test_synthetic_village_weather.py`
remained unstaged and unmodified by this slice.
