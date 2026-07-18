# Elevated Walkable Topology Implementation Plan

**Feature:** Production elevated walkable topology, derived from
`handoff/FEEDBACK-IMAGE2-008-elevated-topology-components.md`
**Goal:** Add four real, collision-checked elevated structures and their
walkable graph, then derive the missing 48 production cameras from that graph
without fabricating height offsets over the ground route.
**Acceptance Criteria:** Four stable components expose absolute-Z walkable
centerlines, widths, exits and collision envelopes; two elevated alternatives
connect distinct ground-route nodes and therefore form two loops; Blender
renders the structures into all six layers; the production plan places 180
finite, unique, collision-free cameras whose elevated `topology_ref` values
name those routes; trust remains synthetic L2
`simplified-pbr-not-render-parity`.
**Architecture:** Keep the immutable ScenePlan v1 unchanged. A separate
content-addressed elevated-topology plan binds the exact ScenePlan digest and
defines the graph plus geometry recipes. The Blender build request consumes
that plan, and the production profile may resolve it only after the plan and
runtime geometry contract both exist.
**Tech Stack:** Python 3.11+, Pydantic v2, NumPy, Blender 4.5 Python API,
pytest, Ruff.
**前端验证:** No — the final rendered canary is inspected visually, but this
slice does not change Viewer UI.

---

## Finish line and exclusions

The finish line is a verified 180-camera six-layer capture whose 48 elevated
cameras stand on rendered, walkable structures. The four image references are
design-only and never become geometry, coverage or provenance evidence.

This plan does not change ScenePlan v1, invent a calibrated reconstructability
threshold, claim photorealism, or promote a model to the Viewer before the
render, overlap and held-out-image gates pass.

## Terminal schema

`pipeline/synthetic_village/elevated_topology.py` owns:

```python
class WalkableNode(FrozenModel):
    node_id: str
    position_m: tuple[float, float, float]
    level: Literal["ground", "elevated"]
    ground_route_ref: str | None

class WalkableEdge(FrozenModel):
    edge_id: str
    component_id: str
    component_kind: Literal[
        "switchback-stair",
        "covered-timber-gallery",
        "terrace-ramp-junction",
        "cross-level-covered-passage",
    ]
    loop_id: Literal["central-loop", "upper-loop"]
    start_node_id: str
    end_node_id: str
    width_m: float
    centerline: tuple[WalkablePoint, ...]
    collision: CollisionEnvelope

class ElevatedTopologyPlan(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.elevated-topology.v1"
    ]
    scene_plan_sha256: str
    synthetic: Literal[True]
    verification_level: Literal["L2"]
    geometry_trust: Literal["simplified-pbr-not-render-parity"]
    semantic_id: Literal[12]
    nodes: tuple[WalkableNode, ...]
    edges: tuple[WalkableEdge, ...]
    components: tuple[ElevatedComponent, ...]
```

Canonical bytes contain no host path or timestamp. Ground nodes must lie on
the declared ScenePlan path and equal terrain height; elevated deck points
must clear terrain by the declared envelope. The graph validator proves two
edge-disjoint elevated alternatives between distinct ground nodes. Building,
water and drainage collisions fail closed.

### Task 1: Content-addressed topology and graph contract

**Files:**
- Create: `pipeline/synthetic_village/elevated_topology.py`
- Create: `tests/test_synthetic_village_elevated_topology.py`

**Step 1: Write failing contract tests**

Tests require the four exact component kinds, stable instance IDs 127--130,
semantic ID 12, two loop IDs, absolute finite millimetre-grid coordinates,
ground attachments on real path objects, elevated clearance, collision-free
centerlines, and canonical bytes bound to the ScenePlan SHA-256. Adversarial
reloads change the digest, node, width, attachment and collision envelope.

**Step 2: Verify RED**

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_elevated_topology.py -q
```

Expected: collection fails because `elevated_topology` does not exist.

**Step 3: Implement the smallest complete topology model**

Build two explicit loops:

- central loop: switchback stair → covered timber gallery → terrace ramp;
- upper loop: cross-level covered passage with ascent, gallery and descent
  edges under one stable component.

Each loop connects two distinct ground nodes on the existing village path
network. Validation derives all summaries from the node/edge evidence rather
than component names.

**Step 4: Verify GREEN**

```bash
.venv/bin/python -m pytest \
  tests/test_synthetic_village_elevated_topology.py \
  tests/test_synthetic_village_scene_plan.py -q
.venv/bin/python -m ruff check \
  pipeline/synthetic_village/elevated_topology.py \
  tests/test_synthetic_village_elevated_topology.py
git diff --check -- \
  pipeline/synthetic_village/elevated_topology.py \
  tests/test_synthetic_village_elevated_topology.py
```

**Step 5: Commit and push the explicit paths**

### Task 2: Bind topology to the Blender build request

**Files:**
- Modify: `pipeline/synthetic_village/canary.py`
- Modify: `tests/test_synthetic_village_canary.py`
- Modify: `scripts/blender/build_synthetic_village.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`

**Step 1: Write a failing request-identity test**

The build ID must change when canonical topology bytes change. The request
contains the topology SHA-256 and path-free canonical payload, never an image
reference or private filesystem path.

**Step 2: Verify RED, implement request binding, then verify GREEN**

The Blender runtime independently validates the plan schema, scene digest,
component/instance IDs and exact payload hash before constructing any object.
Malformed evidence creates no staging directory.

**Step 3: Commit and push the explicit paths**

### Task 3: Renderable component geometry and collision evidence

**Files:**
- Modify: `scripts/blender/build_synthetic_village.py`
- Modify: `tests/test_synthetic_village_blender_runtime.py`
- Modify: `pipeline/synthetic_village/canary.py`
- Modify: `tests/test_synthetic_village_canary.py`

**Step 1: Write failing Blender structural probes**

Probes require one root per stable component, matching semantic/instance IDs,
walkable deck or stair meshes, side collision meshes, railings, covered
clearance where declared, PBR material/UV/tangent data, and non-empty world
bounds that contain the declared centerline.

**Step 2: Verify RED**

Run the focused Blender runtime probes against the current build; they fail
because no elevated roots exist.

**Step 3: Implement geometry**

- switchback stair: bounded stone treads plus intermediate landing and rails;
- gallery: timber deck, posts, rails and covered roof;
- terrace ramp: bounded ramp/deck transition with drainage kept outside the
  walkable surface;
- cross-level passage: ascent, covered deck, underpass-clearance structure and
  descent.

Every mesh is tagged into RGB, depth, normal, instance and semantic output.
Camera metadata continues to come from the existing renderer.

**Step 4: Run focused Python and real-Blender gates**

Build a private L0 scene, inspect GLB/BLEND roots and render one frame through
all six layers. Visual inspection must reject obvious floating, buried,
intersecting or toy-scale geometry.

**Step 5: Commit and push the explicit paths**

### Task 4: Resolve the 48 elevated production cameras

**Files:**
- Modify: `pipeline/synthetic_village/production_profile.py`
- Modify: `tests/test_synthetic_village_production_profile.py`

**Step 1: Replace absence tests with failing topology-resolution tests**

Require exactly 48 elevated cameras, all derived by arc length from real
walkable edges, with unique centers, rigid matrices, eye clearance, collision
freedom and stable IDs. The full plan must become 180/180 only when all groups
are placed.

**Step 2: Verify RED, implement sampling, verify GREEN**

Sample both directions and all component edges without using image slots or a
constant height above ground paths. Update the undelivered loop requirement
only after graph evidence proves both loops.

**Step 3: Commit and push the explicit paths**

### Task 5: Full render, coverage and 3DGS gate

**Files:**
- Modify: `docs/verification/2026-07-19-local-pbr-six-layer-render.md`
- Add only narrowly required production-render code/tests if the existing
  journal cannot consume the completed profile.

Render all 180 frames in resumable batches. Verify every artifact SHA, run
symmetric view overlap, coverage/normal audit and held-out image comparison
under the same policy as the 24-frame baseline. A failed camera, overlap row,
component observation or visual comparison remains a failed experiment and
does not replace the Viewer default.
