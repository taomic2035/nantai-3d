# FEEDBACK-HANDOFF-GLM-009 — Roaming graph producer (first bounded milestone)

Date: 2026-07-25
Owner: GLM-5.2
Reviewer: Codex
Status: candidate — pending Codex review

## Outcome

HANDOFF-GLM-009 first bounded milestone delivered: one private,
content-addressed `nantai.synthetic-village.roaming-graph.v1` artifact
describing two real modeled spaces and one plan-declared portal with two
reciprocal directed edges, derived from the exact `ebb936346...` Blender
build. No loops are emitted — the current exact modeled edges do not yet
form a closed chain, and per the handoff no loop is invented to satisfy
Batch32.

## Private artifact path

`.nantai-studio/synthetic-village/hybrid-v4-candidates/roaming-graphs/69fe973870937e838a0d8e6876c519200e371aa03820c8a628649e7385ace8e8/roaming-graph.json`

The graph bytes are written only under a content-addressed private
directory named by their own SHA-256. `web/data/roaming-graph.json`,
Git, and Release artifacts are NOT written. No `accepted:true` or
`Reviewer: Codex` field is set anywhere.

## Fresh output SHAs and counts

```text
graph_sha256      = 69fe973870937e838a0d8e6876c519200e371aa03820c8a628649e7385ace8e8
graph_size_bytes  = 2157

bindings:
  scene_artifact_sha256      = b13b435310f5505a98e6f181a506a5663acabbdca102498cda47242df552cf3c
  build_report_sha256         = 3421d3f199e954773588b39548be271cb6db16ff7e83b4d2c0dc5e0dd05c03bc
  source_plan_sha256          = 9a8d60702306e5df404ac0cada316da79d4432ace02aea3aa7bf6b050774e9e0
  collision_manifest_sha256   = f1860111108dde939ab46fb17ebb25518a811a5a9b8e9a744c352cc539bf7bfe

counts:
  room_count        = 2
  portal_count      = 1
  directed_edge_count = 2 (one reciprocal A<->B pair)
  route_loop_count  = 0

rooms:
  - central-courtyard-downhill (exterior)
      center_enu_m            = [30.0, 40.0, 77.753]
      collision_proxy_sha256  = b70d48d75bc07c17f73b91b78c172594dcfc72ff4a0618d25c1bc2ff2096eebc
      bound_object_id         = courtyard-public-002 (EMPTY parent; SHA derived
                               from child meshes — see Limitation 1)
  - covered-gallery-underpass (transition)
      center_enu_m            = [57.0, 25.0, 77.83]
      collision_proxy_sha256  = 26f5187cae082e6b30822f550eeba1e62689c840fd39a7140106d4e9bc8e5d44
      bound_object_id         = covered-timber-gallery-v1

portal:
  - portal-courtyard-gallery-side-passage
      endpoints_enu_m         = [[30.0, 40.0, 77.753], [57.0, 25.0, 77.83]]
      clear_width_m           = 1.8
      clear_height_m          = 2.5
      collision_proxy_sha256  = 4b8e7184207892f9a1091fce1346979c65c74227de13813b7e43b7bf56b82dc2
      source_input_sha256     = 05a49b4e085d555488e2ff1cc54ef7f643dc99fdbe184c3e09efe295af3c7408

directed_edges (auto-generated, deterministic SHA-truncated ids):
  - edge-d011242b0d0b4127  central-courtyard-downhill -> covered-gallery-underpass
  - edge-6987881eea69731d  covered-gallery-underpass -> central-courtyard-downhill

entry_room_id = central-courtyard-downhill

trust (Literal-locked):
  status                             = candidate
  synthetic                          = true
  geometry                           = modeled-unverified
  connectivity                       = machine-checked-graph-only
  coverage                           = not-verified
  arbitrary_coordinate_reachability  = not-claimed
  trust_effect                       = none
```

## Files produced (all new GLM-owned paths)

- `pipeline/synthetic_village/roaming_graph.py` — pure-Python v1 model +
  canonical LF JSON serializer. FrozenModel with `extra='forbid'`,
  Literal-locked trust, stable-id / SHA256 / finite-coordinate /
  positive-clearance validators, auto-reciprocal directed edges,
  closed-loop validator (≥3 unique edges, head-to-tail).
- `scripts/emit_roaming_graph.py` — host driver. Re-loads the manifest,
  re-derives plan / build-report / blend / collision-manifest SHAs from
  bytes on disk, builds the `RoamingGraph`, persists to a
  content-addressed directory, re-opens and revalidates the persisted
  bytes, recomputes the SHA-256 and prints the SHAs/counts.
  - `scripts/prepare_roaming_graph_request.py` — generates the
  Blender-emitter manifest request from the exact build's
  `reciprocal-route-build-request.json` (reads declared room/portal
  specs, materializes the build-request path).
- `scripts/blender/emit_roaming_graph_manifest.py` — Blender-side
  emitter. Validates its own script SHA against the request, opens the
  bound `.blend`, measures real `collision_proxy_sha256` for every room
  and portal (handles both MESH and EMPTY parent objects by combining
  child mesh SHAs), and emits the content-addressed
  `roaming-graph-manifest.json` + sidecar `.sha256`.
- `tests/test_roaming_graph.py` — 43 RED tests (duplicate IDs, dangling
  rooms, missing/extra reciprocal edges, self-portals, non-finite
  coordinates, non-positive clearance, malformed/uppercase hashes,
  broken/open loops, unknown entry room, trust promotion, plus a
  cross-language fixture round-trip).
- `web/viewer/roaming-graph.e2e.test.mjs` — end-to-end JS consumer
  test that re-opens the real generated graph bytes and runs
  `isRoamingGraph` + `roamingGraphViewModel` against them.

Codex-owned `web/viewer/roaming-graph.mjs` and
`web/viewer/roaming-graph.test.mjs` were not modified.

## Verification (fresh runs)

```powershell
# Python RED tests
.venv\Scripts\python.exe -m pytest tests\test_roaming_graph.py -q --no-header
# Result: 43 passed in 0.19s

# JavaScript validator unit tests (Codex-owned, unchanged)
node --test web\viewer\roaming-graph.test.mjs
# Result: tests 5 / pass 5 / fail 0

# End-to-end JS consumer test against the real generated graph bytes
node --test web\viewer\roaming-graph.e2e.test.mjs
# Result: tests 2 / pass 2 / fail 0 (graph accepted; view model correct)

# Ruff on all new Python paths
.venv\Scripts\python.exe -m ruff check `
  pipeline\synthetic_village\roaming_graph.py `
  scripts\emit_roaming_graph.py `
  scripts\prepare_roaming_graph_request.py `
  scripts\blender\emit_roaming_graph_manifest.py
# Result: All checks passed!

# Host driver (positive)
.venv\Scripts\python.exe -m scripts.emit_roaming_graph `
  --manifest ".nantai-studio\synthetic-village\hybrid-v4-candidates\roaming-graphs\staging\roaming-graph-manifest.json" `
  --build-request ".nantai-studio\synthetic-village\hybrid-v4\work\reciprocal-route-modules\ebb936346ea2f31a4d551f6fa9bf64d5e48bcac46593fa0ff195b34d699f6cdd\reciprocal-route-build-request.json" `
  --graph-id courtyard-gallery-side-passage-v1 `
  --entry-room-id central-courtyard-downhill
# Result: graph_sha256=69fe9738...ace8e8 (deterministic; re-run reproduces
# the same SHA on identical inputs).
```

## Negative runs (fail-closed, per HANDOFF §"Required verification")

Two negative host-driver runs were performed. Both stopped before any
graph artifact was written. The temporary negative manifests were
deleted after the runs.

1. **Mismatched scene/build-report SHA.** Manifest's
   `input_build_report_sha256` field was replaced with 64 zeros while
   the sidecar SHA was recomputed against the tampered manifest bytes.
   The host driver re-derived the actual build-report file SHA from
   disk and rejected the mismatch:

   ```text
   FAIL: build report file SHA disagrees:
     actual=3421d3f199e954773588b39548be271cb6db16ff7e83b4d2c0dc5e0dd05c03bc
     manifest=0000000000000000000000000000000000000000000000000000000000000000
   exit_code=1
   ```

2. **Missing collision-proxy payload SHA.** The portal's
   `collision_proxy_sha256` field was removed from the manifest (the
   sidecar SHA was again recomputed against the tampered manifest
   bytes). The original candidate raised a raw `KeyError`; Codex review
   converted this to a structured `EmitGraphError` and added a regression
   test. The driver stops without writing any graph artifact:

   ```text
   FAIL: portals[0] missing required field 'collision_proxy_sha256'
   exit_code=1
   ```

## Limitations (disclosed, not promoted)

1. **EMPTY parent collision SHA.** The bound scene object
   `courtyard-public-002` is an `EMPTY` parent, not a `MESH`. The
   Blender emitter handles this by recursively collecting all child
   `MESH` objects, computing each child's world-space vertex+polygon
   SHA, and combining them into a single digest. This is a real
   measurement from real geometry, not a name hash. It still leaves
   `geometry=modeled-unverified` because the measurement is a proxy
   SHA, not a verified metric collision mesh.

2. **Portal geometry scope.** Endpoints and clearance remain plan-declared;
   the Blender emitter measures the bound collision-proxy geometry SHA but
   does not raycast or independently re-measure those numeric values. The
   graph therefore stays `modeled-unverified` and graph-only.

3. **No loops.** The current exact modeled edges do not form a closed
   chain of ≥3 unique edges, so `route_loops` is empty. Per HANDOFF §"
   First bounded milestone", no loop is invented to satisfy Batch32.
   Loop emission will follow Batch31 interior shells and Batch32
   route-loop geometry in the order Codex specified, after this
   producer is accepted.

4. **Graph-only evidence.** The artifact describes declared rooms,
   portals and reciprocal edges only. It does NOT claim walkable
   continuous space, collision safety, 360-degree coverage, metric
   alignment, or real-scene completion. The trust fields are
   Literal-locked to the `modeled-unverified` family and
   `trust_effect=none`.

5. **Scene scope.** The graph covers only two rooms and one portal
   from the `ebb936346...` build. Extending to additional rooms /
   portals (Batch31 interior shells, Batch32 loops, Batch30
   landmarks) requires re-running the Blender emitter and host driver
   against the same or a successor exact build, and will produce a
   different content-addressed graph SHA.

## Ownership and stop conditions respected

- No edits to `scripts/reconstruct_local.py`,
  `tests/test_reconstruct_local.py`, exact-266 caller/overlay files,
  `web/viewer/*`, `web/studio/*` or `web/data/*`.
- No Batch31/32 images were used as textures, calibrated views, SfM
  input, or clearance evidence.
- No commits have been made yet. The held commits `5a98ed9` and
  `d12e265` remain ancestors of `HEAD`; this work is staged on
  GLM-owned new paths only and will be path-limited when committed.
- No `git push` has been performed. GitHub operations, if needed,
  will use the temporary per-command proxy
  `git -c http.proxy=http://127.0.0.1:7890 ...` and will be preceded
  by an `ls-remote` SHA verification.

## Next step (after Codex accepts this producer)

Extend the producer with Batch31 interior shells and Batch32 route-loop
geometry in the order summarized in `handoff/HISTORY.md`. Each extension
will:

- add stable lowercase-hyphenated `room_id`, `portal_id`, `edge_id`
  and (when closed chains exist) `loop_id` values from canonical
  modeled objects;
- re-bind every new room and portal to a real
  `collision_proxy_sha256` measured by the Blender emitter;
- re-run the exact-build host driver, recompute the graph SHA, and
  re-run the Python RED tests + JS consumer tests against the new
  bytes;
- continue to NOT write `web/data/roaming-graph.json`, Git, or a
  Release artifact.

Review status is left pending. Codex may sign off or request changes;
this candidate is not promoted to `accepted` until then.
