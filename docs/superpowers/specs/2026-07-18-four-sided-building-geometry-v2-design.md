# Four-sided rural building geometry v2

Date: 2026-07-18
Status: user approved approach 1; written-spec review pending
Scope: synthetic mountain-village Blender builder and local textured preview

## 1. Problem

The current synthetic village is navigable and textured, but its buildings are
not convincing from a 360-degree orbit. Every building has a gabled mass and a
detailed front elevation, while most side and rear elevations remain plain wall
boxes. The current roof also has nearly zero visible edge thickness. This makes
the same building read differently depending on which side the viewer reaches
and leaves the model short of the requested realistic appearance.

This design improves the existing deterministic geometry. It does not claim
that synthetic geometry is measured, reconstructed from photographs, or equal
to a production modular asset library.

## 2. Decision

Introduce an explicit geometry profile:

```text
four-sided-rural-building-v2
```

The v2 profile enriches all 70 canonical buildings without changing their
stable object IDs, transforms, footprints, terrain platforms, semantic IDs,
material families, camera plans, or world coordinate frame.

The old behavior remains identifiable as:

```text
front-facade-box-v1
```

Old requests and reports that do not contain a geometry-profile field continue
to parse as v1 and retain their original canonical bytes. A new build uses v2
only when its content-addressed request explicitly selects v2. The local
textured preview creator selects v2; tracked L2 publication remains blocked
until the locked Windows x64 Blender build is run and verified.

## 3. Geometry contract

### 3.1 Keep the stable scene

The following values are unchanged:

- 70 building roots and the 126-entry object registry;
- the `22 / 28 / 20` cluster budget and all 12 spatial cells;
- every building transform, yaw, footprint, height, base elevation, and
  material family;
- the existing stone platform, wall, roof, timber, door, and window material
  bindings;
- the `synthetic`, verification-level, geometry-usability, and coordinate
  disclosures.

The geometry upgrade must therefore be an extension of the present scene, not
a second scene layout or a silent replacement of world identity.

### 3.2 Four elevations

Each building must expose deliberate detail on all four local elevations:

| Elevation | Required visible geometry |
|---|---|
| front (`-Y`) | entry assembly, two framed windows, corner posts, top beam |
| rear (`+Y`) | at least one framed opening, corner posts, top beam |
| left (`-X`) | at least one framed opening or side entry, corner posts, top beam |
| right (`+X`) | at least one framed opening or side entry, corner posts, top beam |

An opening assembly consists of:

1. a dark inset panel;
2. a proud perimeter frame;
3. one vertical and one horizontal muntin for windows, or two vertical battens
   and one cross rail for doors.

The inset is a visible depth cue, not a boolean wall opening. Reports and UX
must not describe it as an interior, enterable building, or physically open
window.

All four elevations also receive a continuous exposed stone plinth. Timber
corner posts are shared by adjacent elevations and must not be duplicated into
z-fighting surfaces.

### 3.3 Roof edge depth

The existing gabled roof keeps its pitch and material, but gains:

- a soffit/fascia band along both eaves;
- bargeboards on both gable ends;
- a visible roof-edge thickness of `0.14 m`;
- the existing ridge cap, extended only as needed to cover the bargeboards.

Tile-scale detail remains the responsibility of the verified PBR texture.
Individual roof tiles are deliberately excluded because they would multiply
geometry without improving the current overview and mid-distance use cases.

### 3.4 Deterministic variants

Every building receives one of three façade variants:

```text
balanced-residence
side-entry-workshop
rear-service-house
```

The variant is derived only from:

```text
sha256("four-sided-rural-building-v2\0" + object_id).digest()[0] % 3
```

The mapping is therefore stable across runs and platforms and does not use
Python's process-randomized `hash()`. Remainders map as follows:

```text
0 -> balanced-residence
1 -> side-entry-workshop
2 -> rear-service-house
```

- `balanced-residence`: front entry, rear window, one window on each side;
- `side-entry-workshop`: front entry/windows, left side service entry, right
  side window, rear service window;
- `rear-service-house`: front entry/windows, rear door, one window on each
  side.

The community hall keeps its porch and also satisfies the four-elevation
contract. Its existing role does not create a fourth variant.

### 3.5 Mesh and material budget

The upgrade reuses the existing mesh objects:

- stone-plinth geometry is appended to `stone-platform`;
- roof edge geometry is appended to `tiled-gabled-roof-ridge-eaves`;
- four-sided posts and beams are appended to `timber-frame`;
- all doors are appended to `timber-door`;
- all window frames and inset panels are appended to
  `two-latticed-windows`.

No new material or image is added. No building gains an extra Blender mesh
object solely for v2. The textured GLB must therefore keep the existing
primitive count and all 24 material identities while increasing only vertex
and face counts.

The local preview acceptance budget is:

- exactly 70 v2 building roots;
- all three variants present;
- no more than 220 added polygon faces per building;
- no more than 15,400 added polygon faces across the village;
- no more than 720 exported triangles under any building root;
- no more than 100,000 exported triangles across the complete GLB;
- exactly 544 textured GLB primitives, matching the current v1 preview;
- embedded GLB smaller than 150,000,000 bytes;
- zero external texture URIs;
- no material, UV, tangent, weather, camera, or Viewer-console regression.

If a budget is exceeded, the build fails closed. The limit may be revised only
with measured evidence and a new design decision; it must not be silently
relaxed to make a build pass.

## 4. Identity and evidence

### 4.1 Request identity

Build requests gain:

```json
{
  "building_geometry_profile_id": "four-sided-rural-building-v2"
}
```

The field participates in the canonical request bytes and therefore in the
preview/build content address. Historical requests missing the field parse as
`front-facade-box-v1`; their canonical serializer omits the default when it was
not present in the original bytes.

### 4.2 Build report

New v2 reports contain a strict `building_geometry` evidence block:

| Field | Required value |
|---|---|
| `profile_id` | `four-sided-rural-building-v2` |
| `building_count` | `70` |
| `covered_elevations` | `["front", "left", "rear", "right"]` |
| `variant_counts.balanced-residence` | `21` |
| `variant_counts.rear-service-house` | `20` |
| `variant_counts.side-entry-workshop` | `29` |
| `added_face_count` | integer in `1..15400` |
| `maximum_added_faces_per_building` | integer in `1..220` |
| `new_mesh_object_count` | `0` |

The variant counts above are the measured result of applying the specified
SHA-256 mapping to the current 70 stable building IDs; they are not adjustable
targets.

Each building root also exports these GLB node extras:

```text
nv_building_geometry_profile
nv_building_variant
nv_facade_elevations
nv_added_face_count
```

`nv_facade_elevations` is the exact sorted JSON array:

```json
["front", "left", "rear", "right"]
```

### 4.3 Independent publication audit

The published GLB audit must read the GLB JSON and binary accessors rather than
trust the build report alone. It verifies:

- exactly 70 nodes with `nv_root=true` identify canonical building roots;
- every root declares the v2 profile, one allowed variant, all four
  elevations, and a positive bounded added-face count;
- recomputing the variant from the stable object ID matches the exported
  value;
- all three variants occur and their counts match the report;
- every building subtree has at most 720 indexed triangles;
- the complete GLB has at most 100,000 indexed triangles and exactly 544
  textured primitives;
- root extras and report agree on the Blender polygon delta; this delta is
  labelled builder-measured rather than independently recomputed from
  triangulated GLB accessors;
- the 24-material, embedded-image, UV, tangent, and primitive-coverage
  material audit remains green.

These fields prove which deterministic geometry recipe was emitted. They do
not by themselves prove photorealism; the visual checks below remain required.

## 5. Visual acceptance

Generate a new immutable local preview and compare it with the current v1
geometry from matched camera states.

Required private evidence:

1. a clear-weather overview showing the village silhouette;
2. a close orbit composite showing front, side, and rear elevations of the
   same residence;
3. a close orbit composite showing the other two variants, including one view
   in rain and one at night.

Acceptance requires:

- the same building remains visually authored on front, side, and rear;
- window/door frames stand proud of inset panels without z-fighting;
- stone plinth and roof edge thickness are visible at close range;
- the three variants are distinguishable without changing scene layout;
- clear, rain, and night preserve readable materials;
- 360-degree orbit and zoom remain responsive;
- browser console contains no new error;
- the disclosure remains `synthetic / L0 / preview-only`.

Visual review must record remaining defects. A successful v2 preview must not
be labelled "near-real final quality" if terrain zoning, vegetation, interiors,
or asset repetition remain visibly synthetic.

## 6. Failure behavior

The builder fails before publication when:

- the profile ID is unknown;
- a building lacks any required elevation;
- a variant does not match the stable hash mapping;
- variant counts do not total 70 or omit a variant;
- face or GLB byte budgets are exceeded;
- v2 creates extra mesh objects;
- report evidence differs from emitted GLB extras;
- the existing material audit regresses.

Historical v1 artifacts remain loadable and auditable. They are never silently
relabeled as v2.

## 7. Test strategy

Implementation follows red-green-refactor:

1. request/report compatibility tests prove old canonical bytes remain
   unchanged and new v2 identity changes the content address;
2. standalone Blender-script contract tests prove the four elevations,
   deterministic variants, roof depth, component reuse, and face budgets;
3. report-verifier tests reject missing elevations, wrong variants, and
   over-budget geometry;
4. GLB audit tests mutate node extras and accessor evidence to prove the
   independent audit fails closed;
5. a real local Blender build publishes a new immutable preview;
6. existing Python, Viewer, Studio, lint, compile, and browser gates run
   unchanged.

## 8. Explicitly deferred

This slice does not implement:

- enterable interiors, collision, doors that open, or simulation;
- individual roof-tile geometry;
- a production modular asset library or mesh LOD chain;
- arbitrary-coordinate textured chunk streaming;
- real-photo, SfM, or 3DGS geometry reconstruction;
- promotion of synthetic or local evidence to measured/metric truth.

The next high-value geometry slice after v2 is terrain/path/terrace relief.
The modular asset and streamed-LOD design follows only after the separate
arbitrary-coordinate textured-world scheme is approved.
