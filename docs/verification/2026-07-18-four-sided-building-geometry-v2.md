# Four-sided building geometry v2 verification

Date: 2026-07-18
Workspace: `/Users/taomic/vibecoding/nantai-3d`
Runtime URL: `http://127.0.0.1:8767/web/viewer/`
Profile: `four-sided-rural-building-v2`

## Result and truth boundary

The local macOS preview now publishes all 70 canonical buildings with deliberate
front, left, rear, and right elevation details. The result remains
`synthetic=true`, `verification_level=L0`, `authoritative=false`, and
`geometry_usability=preview-only`. It is not measured geometry, a real-place
reconstruction, or photo-derived texture evidence.

The upgrade reuses the existing 421 building mesh objects. Openings are inset
visual assemblies, not enterable interiors or boolean-cut doors and windows.

## Immutable publication

| Field | Measured value |
|---|---|
| Preview ID | `000e48f209e108f5a127b980f1c08b36dd869371c06bc52cb5ed8b14a923eeb9` |
| Material bundle ID | `b5f49d93c4dd29e1c29d5e5dc24cb7a836c4c9cbfcfce346e05db3524291ab13` |
| GLB SHA-256 | `34382cabd94b9de79f3766a07b0bbdd5dc08739a3deaf0cb3ff73f83be82f19b` |
| GLB bytes | `133877204` |
| Build report SHA-256 | `71279d81d5c89be8208510f00fabd6e8bb8fecddd78a50e5e495dd29cc56b375` |
| Combined audit SHA-256 | `377c322b9016154244675e8c428ea9ac7f94dd450198808f3c35e5aee2bbf12d` |
| Materials / primitives | `24 / 544` |
| External URI count | `0` |

The first standard publication returned `reused=false`. A later call to
`verify_local_textured_preview_directory()` re-read the four published files,
rehashed the GLB, decoded the embedded images, and recomputed the geometry audit
from the GLB node tree and indexed accessors.

## Geometry evidence

| Check | Result | Budget |
|---|---:|---:|
| Canonical building roots | `70` | exactly `70` |
| Covered elevations | `front / left / rear / right` | exact |
| Variants | `21 balanced / 20 rear-service / 29 side-entry` | exact stable SHA-256 mapping |
| Builder-measured added faces | `8659` | at most `15400` |
| Maximum added faces per building | `124` | at most `220` |
| New building mesh objects | `0` | exactly `0` |
| Total GLB triangles | `81718` | at most `100000` |
| Maximum GLB triangles in one building subtree | `512` | at most `720` |

The polygon delta is builder-measured and cross-checked against every building
root extra. Triangle counts are independently derived from the published GLB
index accessors. Neither value promotes the preview's trust level.

## Browser evidence

The in-app browser loaded the exact preview URL:

```text
http://127.0.0.1:8767/web/viewer/?modelPreview=%2Fapi%2Flocal-textured-preview%2F000e48f209e108f5a127b980f1c08b36dd869371c06bc52cb5ed8b14a923eeb9%2Fmanifest.json
```

Observed behavior:

- the private manifest and 134 MB GLB loaded with the honest local L0 badge;
- drag-orbit changed the camera and exposed authored rear/side doors, windows,
  stone plinths, gable trim, and roof-edge depth;
- 3× optical zoom remained interactive;
- clear → rain → night → clear changed mesh lighting and atmosphere while
  retaining the non-reconstruction disclosure;
- browser console warnings/errors after the interaction sequence: `0`.

Private screenshot evidence is under
`.nantai-studio/verification/2026-07-18-building-geometry-v2/`:

| Evidence | SHA-256 | Observation |
|---|---|---|
| `04-clear-orbit-side-rear.png` | `5c1caff878a87d62a28b31af75ec5569211e181870806594eb19f3f0d3db8f0c` | clear side/rear orbit; openings and plinths visible |
| `05-rain-orbit-side-rear.png` | `ea1f8b32cd87fac297499a81191b779c9f9cc76dba3ad2a56de8f1b2fe777c8d` | rain relighting and precipitation |
| `06-night-orbit-side-rear.png` | `a4ad0598cc1334ecd7d27a6d09e46f0f983c227ee4dfcb35b862022ed26e199a` | night state; geometry remains visible but dim |

No pixel-perfect same-building front/side/rear composite is claimed. The manual
orbit proves the interaction and selected side/rear views; the exact four-side
coverage for all 70 buildings comes from the independently audited GLB extras.

## Quality gate

```text
.venv/bin/python -m pytest tests/ -q
1157 passed, 124 skipped, 1 warning in 401.48s

node --test web/viewer/*.test.mjs
130 passed

node --test web/studio/*.test.mjs
75 passed

.venv/bin/python -m ruff check pipeline scripts tests
All checks passed!

.venv/bin/python -m compileall -q pipeline scripts
passed

git diff --check
passed

find designs -type f -name '*.pen' -print
no matching design file

git status --short | rg '^\?\? [^/]+\.(png|jpe?g|webm|mp4)$'
no untracked root-level media
```

The single Python warning is the existing intentional non-finite coordinate
fail-closed test exercising a NumPy overflow path. The test passed.

Focused geometry/audit evidence from this run:

```text
tests/test_building_geometry.py +
tests/test_synthetic_village_building_geometry_contract.py
5 passed

tests/test_glb_material_audit.py
22 passed

tests/test_glb_material_audit.py +
tests/test_local_textured_preview.py
30 passed

tests/test_glb_material_audit.py +
tests/test_local_textured_preview.py +
tests/test_synthetic_village_canary.py
102 passed, 1 skipped
```

The plan's `scripts/synthetic_village.py build-textured-preview` command was
not present in the current CLI and Git history showed it had never been
implemented. The rejected command exited before Blender started. This
publication therefore called the tested `run_local_textured_preview()` entry
point directly with the exact verified material bundle and Blender executable;
no CLI capability is claimed.

## Remaining high-value limits

1. Geometry and textures are still synthetic. Terrain tiling, vegetation
   silhouettes, repeated house proportions, and distant composition remain
   visibly simplified.
2. Rain and especially night are readable but darker than a polished product
   target; this receipt does not claim final art direction.
3. There is no enterable interior, collision model, mesh LOD, or arbitrary
   coordinate textured-chunk continuation.
4. Weather relights this synthetic mesh only. It does not relight 3DGS splats.
5. Real photo/video reconstruction still depends on external SfM and 3DGS
   training; this preview does not replace that path.

The unrelated modified
`tests/test_synthetic_village_weather.py` remained unstaged and was not changed
or included by this work.
