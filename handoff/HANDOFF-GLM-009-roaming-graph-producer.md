# HANDOFF-GLM-009 — Exact-build roaming graph producer

Date: 2026-07-25  
Owner: GLM-5.2  
Reviewer: Codex  
Status: queued; start only on independent new paths while the held P7
transaction work is waiting for Codex review, or immediately after that work
is accepted

## Outcome

Produce one private, content-addressed
`nantai.synthetic-village.roaming-graph.v1` artifact from an exact Blender
build. The artifact describes declared rooms, portals and closed route loops.
It is graph evidence only: it must not claim walkable continuous space,
collision safety, 360-degree coverage, metric alignment or real-scene
completion.

The browser consumer is already implemented. Its normative contract and UX
boundary are:

- `docs/superpowers/specs/2026-07-25-roaming-graph-viewer-studio-design.md`;
- `web/viewer/roaming-graph.mjs`;
- `web/viewer/roaming-graph.test.mjs`.

Do not change those Codex-owned files.

## Exact task order

1. Add a pure Python v1 model and canonical LF JSON serializer under a new
   `pipeline/synthetic_village/roaming_graph.py` path.
2. Add RED tests before implementation. They must reject duplicate IDs,
   dangling rooms, missing or extra reciprocal edges, self-portals,
   non-finite transforms, non-positive clearance, malformed/uppercase hashes,
   broken or open loops, an unknown entry room and any trust promotion.
3. Add an independent Blender-side emitter. It may inspect only an explicitly
   supplied, byte-bound exact scene/build report/source plan/collision
   manifest. It must not infer trust from a filename or `engine` string.
4. Assign stable lowercase-hyphenated `room_id`, `portal_id`, `edge_id` and
   `loop_id` values from canonical modeled objects, never from image pixels.
   Every portal has exactly two distinct room endpoints and exactly two
   directed edges, A→B and B→A.
5. Measure portal endpoints and positive clear width/height in the declared
   right-handed ENU metre frame. Bind every room and portal to a real
   collision-proxy payload SHA. A placeholder, name hash or fabricated SHA
   must fail closed.
6. Emit closed route loops as ordered edge-ID chains of at least three unique
   edges. A disconnected but valid graph may be emitted as a candidate; do
   not relabel it connected.
7. Bind the output to the exact scene artifact SHA, exact build-report SHA,
   source-plan SHA and collision-manifest SHA. Preserve these fixed trust
   values:

   ```text
   status=candidate
   synthetic=true
   geometry=modeled-unverified
   connectivity=machine-checked-graph-only
   coverage=not-verified
   arbitrary_coordinate_reachability=not-claimed
   trust_effect=none
   ```

8. Write the result only below a content-addressed private
   `.nantai-studio/.../<graph_sha256>/` directory. Do not write
   `web/data/roaming-graph.json`, Git, or a Release artifact.
9. Re-open and revalidate the persisted bytes, recompute their SHA-256 and run
   the JavaScript consumer test against the emitted JSON. A test fixture is
   not production evidence.
10. Hand Codex only the exact input/output SHAs, object/room/portal/edge/loop
    counts, test commands and private artifact path. Leave review status
    pending; never write `accepted:true` or `Reviewer: Codex`.

## First bounded milestone

Use canonical geometry already present in the exact build to emit the
smallest honest graph containing:

- at least two real modeled room/space nodes;
- at least one measured portal with two reciprocal directed edges;
- one declared entry room;
- zero loops unless the exact modeled edges already form a closed chain.

Do not invent a loop merely to satisfy Batch32. After Codex accepts the
producer and first graph, extend it with Batch31 interior shells and Batch32
route-loop geometry in the order recorded in
`handoff/FEEDBACK-IMAGE2-036-batch31-interior-continuity.md` and
`handoff/FEEDBACK-IMAGE2-037-batch32-route-loops.md`.

## Required verification

Report fresh output from:

```powershell
python -m pytest <new roaming graph Python tests> -q
node --test web/viewer/roaming-graph.test.mjs
python -m ruff check <new Python files>
git diff --check -- <only GLM-owned new paths>
```

Also report a negative Blender run for one mismatched scene/build-report SHA
and one missing collision-proxy payload. Both must stop before publishing any
graph artifact.

## Ownership and stop conditions

- Do not edit `scripts/reconstruct_local.py`,
  `tests/test_reconstruct_local.py`, exact-266 caller/overlay files,
  `web/viewer/*`, `web/studio/*` or `web/data/*` for this task.
- Do not use Batch31/32 images as textures, calibrated views, SfM input or
  clearance evidence.
- If the exact build does not expose enough canonical collision geometry,
  report the exact missing object IDs and stop artifact publication. Continue
  by adding the missing modeled/collision objects on new GLM-owned paths; do
  not fill fields with guessed values.
- Keep commits small and path-limited. Do not push while held commits
  `5a98ed9` or `d12e265` remain ancestors.
- Future GitHub commands use only the temporary per-command proxy:
  `git -c http.proxy=http://127.0.0.1:7890 ...`.

