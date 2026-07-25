# Roaming Graph Viewer / Studio Design

Date: 2026-07-25  
Status: approved by the existing Batch31/32 portal-graph handoff  
Owner: Codex  
Inputs:

- Batch31 interior continuity 与 Batch32 route loops，摘要见
  `handoff/HISTORY.md`

## 1. Purpose

The current production camera plan proves four fixed outdoor route loops, but
it does not describe rooms, portals or indoor/outdoor reachability. Studio can
also move a camera to arbitrary numeric coordinates without knowing whether
that coordinate is walkable.

This design adds an independent, optional roaming-graph artifact and a
fail-closed Viewer/Studio presentation. It makes declared room/portal
connectivity inspectable without treating a graph, a camera move or a
design-only image as proof of geometry, collision safety, 360-degree coverage
or arbitrary-coordinate reachability.

## 2. Chosen architecture

Use a new additive artifact:

```text
nantai.synthetic-village.roaming-graph.v1
```

The alternatives were rejected:

- Extending `production-camera-plan.v1` would change a stable, content-bound
  camera contract and mix pose planning with navigation topology.
- Deriving topology from Batch31/32 pixels would invent calibration,
  dimensions and connectivity that the independent design boards do not
  contain.
- Showing only a Studio mock would demonstrate controls but create no
  reusable machine contract for a future exact Blender build.

The roaming graph is therefore optional and separately loaded. Existing
camera, coverage and reconstruction provenance remain unchanged.

## 3. Artifact contract

The v1 document contains:

- `schema_version=1`;
- `graph_schema=nantai.synthetic-village.roaming-graph.v1`;
- a stable `graph_id`;
- `status=candidate`;
- an explicit right-handed ENU metre coordinate frame;
- `synthetic=true`;
- trust declarations fixed to:
  - `geometry=modeled-unverified`;
  - `connectivity=machine-checked-graph-only`;
  - `coverage=not-verified`;
  - `arbitrary_coordinate_reachability=not-claimed`;
  - `trust_effect=none`;
- lowercase SHA-256 bindings for the exact scene artifact, build report,
  source plan and collision manifest;
- one `entry_room_id`;
- rooms, portals, directed edges and declared route loops.

Each room contains a stable `room_id`, display label, semantic kind,
`center_enu_m` and collision-proxy SHA.

Each portal contains a stable `portal_id`, exactly two distinct room IDs,
one endpoint in each room, finite positive clear width/height, collision-proxy
SHA and source-input SHA.

Each traversable portal has exactly two directed edges: A→B and B→A. A route
loop contains an ordered list of at least three edge IDs whose endpoints form
one closed chain.

## 4. Fail-closed validation

Viewer owns runtime validation and recomputes graph facts. It does not trust
claimed counts or status words.

Validation rejects:

- malformed schema, trust or coordinate-frame declarations;
- missing, uppercase or malformed content hashes;
- duplicate room, portal, edge or loop IDs;
- non-finite coordinates or non-positive clearance;
- self-portals, missing rooms or a room with no incident portal;
- missing, extra or non-reciprocal directed portal edges;
- dangling edge references;
- loops with missing edges, broken ordering or an open final endpoint;
- an entry room that does not exist.

A structurally valid graph may still contain multiple connected components.
That is represented as `fragmented`, not rejected or promoted.

## 5. Derived presentation model

The browser derives:

- total and entry-reachable room counts;
- portal and declared-loop counts;
- connected-component count;
- reachable room navigation nodes;
- one of `unknown`, `fragmented` or `graph-connected`.

Even a fully connected graph is displayed as:

```text
graph-connected · candidate · not 360 evidence
```

The model never emits `360-ready`, `coverage-complete`,
`arbitrary-coordinate-ready`, `metric-aligned` or equivalent trust language.

## 6. Viewer and Studio UX

Viewer:

- advertises `roaming-graph` as a dynamic, non-rendering artifact kind;
- loads only same-origin JSON;
- validates it before replacing current state;
- exposes the derived model through `getState`;
- shows room reachability, portal/loop counts and the graph-only trust label in
  the existing HUD.

Studio:

- probes `/web/data/roaming-graph.json` only after the Viewer advertises the
  capability;
- treats 404 as a quiet optional absence;
- shows a compact roaming summary;
- lists only entry-reachable room nodes;
- lets the operator move the camera to a declared room center through the
  existing `setCameraPose` command;
- keeps the free-coordinate control, but labels camera movement as distinct
  from walkability or collision clearance.

No default roaming-graph JSON is committed. GLM must first emit one from an
exact Blender build with real collision-proxy bindings; until then the UX
honestly stays unknown/absent.

## 7. Ownership and data flow

```text
GLM exact Blender build
  -> content-bound roaming-graph.json
  -> Studio optional loader
  -> Viewer same-origin loader + validator
  -> derived graph-only view model
  -> HUD / reachable-room camera jump
```

Codex owns the browser validator, optional loader, view model and presentation.
GLM owns future exact scene geometry, portal transforms, collision proxies and
machine build report. Neither lane edits the other lane's active P7
reconstruction transaction paths.

## 8. Verification

TDD must cover:

- one valid connected fixture;
- a valid fragmented fixture;
- duplicate IDs;
- dangling rooms and references;
- missing reverse edges and extra portal edges;
- non-finite positions and invalid clearance;
- broken/open loops;
- malformed hashes, frame and trust declarations;
- same-origin loading and invalid-artifact rejection;
- capability-gated Studio probe, quiet 404 and visible non-404 failure;
- Viewer and Studio HTML contract IDs;
- absence of readiness or trust-promotion language.

Run all affected Node tests plus `git diff --check`. No test fixture is
published as production evidence.

## 9. Out of scope

- generating portal geometry from image pixels;
- collision or navmesh computation in the browser;
- claiming all continuous coordinates are walkable;
- changing production-camera-plan or coverage-audit v1;
- committing private Blender builds or `web/data`;
- replacing real capture, SfM, 3DGS training, measured alignment or real
  Viewer QA.

## 10. Definition of done

This Codex phase is complete when:

1. the independent validator and derived model pass adversarial tests;
2. Viewer loads and reports the optional graph without trust promotion;
3. Studio shows graph status and can jump to a validated reachable room node;
4. absence and malformed artifacts remain fail-closed;
5. existing Viewer/Studio bridge and index-contract tests remain green;
6. GLM receives a short exact producer checklist for the future Blender
   artifact.
