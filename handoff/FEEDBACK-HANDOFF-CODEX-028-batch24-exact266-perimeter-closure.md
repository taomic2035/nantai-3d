# FEEDBACK-HANDOFF-CODEX-028 — Batch24 exact-266 formal audit

Date: 2026-07-24
Owner/reviewer: Codex
Status: rendered and machine-verified, acceptance rejected

## Result

The exact-266 Blender candidate can render navigable-looking reciprocal route
views, but it does not yet prove complete perimeter/target visibility and is
not a realistic or real reconstructed scene.

Fresh formal results:

| Gate | Result |
|---|---:|
| exact object registry | 1..266 |
| fresh clearance | 15/16 pass |
| six-layer + RGB artifacts for clearance-passing cameras | 15/15 verified |
| camera metadata/requested-measured pose binding | 15/15 verified |
| local valid-pixel policy | 15/15 pass |
| post-render v2 eight-rule policy | 15/15 pass |
| all six module targets visible | **0/15 pass** |
| both current/neighbor seam targets visible | **3/15 pass** |

`camera-audit-overview-003` was rejected before rendering because its upper
middle clearance probe measured 5/15 near hits. No render was fabricated for
that camera.

The result is intentionally not collapsed to one misleading green/red bit.
Basic image validity and post-render distribution pass, while task-specific
module and seam visibility fail.

## Bound identities

```text
exact build ID:
  937afaca82fcb12f841318f4ebc0bbcdd5388f3a45d6ca57243fb1154d825a66
exact .blend SHA-256:
  f3efbddc845f83e613f9a1c570306ded32aba1d3da0a0e40e8ce4fd9d61db4a0
build report SHA-256:
  1b523966c769f23e6531bddb30457276e627b9b5a8f8ee364be1d277bf4b07e1
object registry SHA-256:
  03370ed43bae25c13968339ee93cb4aa9102b40aada193cb2baca3d07ea4031a
audit plan SHA-256:
  5c74171ecdb11a336c427cb67a6193c2a7d66e445621d7b2ffaa7142c6053216
clearance request SHA-256:
  d2599e689a1d38072e55b004e3e797e8cc4f6f27015d6597b0e89500b5f894b8
clearance report file SHA-256:
  4608ab2d76bae97e38f136a071aeb0e47ebe8973b09cfd491ae127de81443764
preflight ID:
  9fbc18138afce47f80a879611e87cbbf56f25d87f25d021a72e4af818899e95c
clearance policy SHA-256:
  520e72ee9b0b62c8540ecf8866ab2a1c1cf3f6638f4ed86d52de1bcaac0bdf40
audit adapter SHA-256:
  2b20a17782533a2fab919312fddc4aad3c9a435ee89cafad793cc09a19452dd6
frozen render engine SHA-256:
  b684f0ff81f14df2b368a5e9c3e242463af6724653a56564cf016dc3dc42affc
Blender executable SHA-256:
  0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255
renderer capability SHA-256:
  19a2175621434e883a65c98d09c8a4c0804838d52646a45d6a68ffc972dddafb
local quality policy SHA-256:
  4c57374c118cf771c59062ce11e754eb6549da3e1c8f3c6c478c055ec5966a8a
post-render v2 policy SHA-256:
  b60eabd0c9cf069b23982bf2cfb9149ea25add8c6d76df39541d5642cf880b17
```

Private evidence root:

```text
.nantai-studio/o/b24c/9fbc18138afce47f80a879611e87cbbf56f25d87f25d021a72e4af818899e95c/
```

The diagnostic contact sheet is
`rgb-contact-sheet.png`, SHA-256
`8d6ef4adb0757388932924dd8284050e25bd20930040ce81e2c9644a0172d054`.
It is private diagnostic output, not a Release asset.

## Camera matrix

`valid` is the measured non-background depth ratio. Every rendered frame
passed local and post-render-v2 policies.

| Camera | Direction | State / valid | Missing module targets | Missing seams |
|---|---|---:|---|---|
| 001 | upstream inward | 0.697673 | 221, 222 | — |
| 002 | upstream outward | 0.668910 | 221, 222 | — |
| 003 | northeast inward | preflight rejected | not rendered | not rendered |
| 004 | northeast outward | 0.686810 | 227, 228, 229 | 229, 235 |
| 005 | east inward | 0.670359 | 233, 234 | 241 |
| 006 | east outward | 0.606857 | 233, 234 | 241 |
| 007 | southeast inward | 0.636003 | 239, 240, 241 | 241, 247 |
| 008 | southeast outward | 0.512280 | 239, 240 | 247 |
| 009 | downstream inward | 0.628645 | 246, 247 | 247 |
| 010 | downstream outward | 0.505783 | 246 | — |
| 011 | southwest inward | 0.627530 | 252, 253 | 253, 259 |
| 012 | southwest outward | 0.807197 | 252 | 259 |
| 013 | west inward | 0.594291 | 258, 259 | 259, 265 |
| 014 | west outward | 0.524033 | 258 | 265 |
| 015 | northwest inward | 0.584056 | 264 | 223 |
| 016 | northwest outward | 0.622918 | 264 | 223 |

## Visual review

The views are readable as a route and are materially better than an empty
placeholder, but the following defects are obvious at the delivered
resolution:

- low-poly “lollipop” vegetation dominates several views;
- the world background is a flat grey field with no convincing sky,
  atmosphere or distant terrain;
- terrain, road and bank surfaces contain hard triangular seams;
- stone, dirt and vegetation textures repeat or stretch at visibly different
  scales;
- multiple buildings/roofs remain oversized proxy-like forms;
- camera 006 is framed beneath/against a nearby structure and camera 012 has a
  large near-wall obstruction;
- most boundary/seam objects are outside the intended reciprocal framing;
- drainage, retaining/support and vegetation-enclosure targets are commonly
  absent even though generic pixel-distribution gates pass.

This demonstrates why generic valid-pixel and sky/ground thresholds cannot
replace task-specific visibility.

## Fresh UV repeat-density evidence

The corrected real Blender probe measured all evaluated loop triangles in the
same exact-266 `.blend`:

```text
private report:
  .nantai-studio/o/uv/exact266-repeat-density.json
report file SHA-256:
  c8cb97d18a9607599d2ccf20ab86bd7da8b553645dd4eeba41836e078088939d
report content SHA-256:
  eb7b3415fc8ac1d6347e35c37246d1d74a02b31e35574678ec800b5f0eb7c19a
probe script SHA-256:
  97038c7283089c6d8e744e45b3e4f9bc238f966d30fa4775451eb203236cc770
objects:
  714
```

| Category | Objects | UV area/m² min | UV area/m² max | Ratio |
|---|---:|---:|---:|---:|
| terrain | 48 | 0.011737 | 2.727446 | 232.37× |
| creek | 161 | 0.038596 | 1.562498 | 40.48× |
| long-wall | 310 | 0.081633 | 0.390633 | 4.79× |
| all measured objects | 714 | 0.011737 | 6.467387 | 551.01× |

The unit is UV-coordinate area per square metre, not texels per metre. Texture
pixel dimensions are not bound. The report proves severe internal variation,
not a real-world texel-density target.

## Distance to the real 3D goal

| Dimension | Current evidence | Still required |
|---|---|---|
| arbitrary-coordinate synthetic navigation | route views render | fix target/seam visibility and collision continuity |
| synthetic geometry | exact-266 low-poly Blender meshes | detailed, repaired geometry or real reconstruction |
| texture | simulated PBR/material slots | real-photo texture or trained radiance/splat appearance |
| capture | image2 design references only | real overlapping photos/video frames |
| camera recovery | modeled cameras | accepted real COLMAP/SfM |
| 3DGS | external caller contracts | one non-mock cloud-GPU training result |
| scale/alignment | synthetic metre convention | measured control-point/GPS alignment |
| real Viewer QA | not run on a real artifact | imported real splat, streaming and roaming QA |

Therefore:

```text
exact-266 = synthetic modeled-unverified Blender geometry
not a real mesh reconstructed from capture
not real-photo texture
not calibrated multiview
not metric-aligned real-world evidence
```

## Next iteration

1. Keep camera 003 rejected and relocate/rebuild the northeast corridor rather
   than weakening clearance.
2. Reframe or reposition module targets so each bidirectional pair measures the
   intended support, drainage, seam and enclosure geometry.
3. Apply the base-builder UV mapping correction using the same camera/render
   identities before and after.
4. Consume Batch25 only as replaceable modeling guidance for sky, creek-bed,
   terrain transitions and vegetation; it does not add reconstruction trust.
5. Rebuild exact-266+ and rerun the same fresh clearance/six-layer/visibility
   audit.
6. In parallel, run the real COLMAP executable rehearsal; real photos and cloud
   GPU training remain external prerequisites for the actual real-scene path.
