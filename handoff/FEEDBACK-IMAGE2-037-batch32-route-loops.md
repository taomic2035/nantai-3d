# FEEDBACK-IMAGE2-037 — Batch32 route loops

Date: 2026-07-25
Producer: GPT image2
Status: design-only candidate input

## Delivery

- [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch32-2026-07-25)
- Archive: `synthetic-village-route-loops-batch32-2026-07-25.zip`
- Bytes: `26,182,500`
- Archive SHA-256:
  `737d0dc502ad67eb586d7f4565427f34d94763f81c1385f9afbda2edf06905bd`
- Manifest SHA-256:
  `b1b69fc4a6258eb9399275f45d51cb281aca965b40a2a124ed65c7deea42b43f`
- Payload checksum SHA-256:
  `06006bc58ca91fb1b0bed14002a37192e706f97b9e0b04cbe978bf1d3839dfea`
- External checksum SHA-256:
  `ddc8f94293e5c77efd2c2ff3c42fab47afe61494efa4e368f7b75fbff7eb9856`

The deterministic archive has 19 sorted entries: 8 RGB24 PNGs at
`1536 x 1024`, 8 exact prompt/prompt-chain files, manifest, usage and payload
checksums. All 18 payload hashes were recomputed from archive bytes. No
intermediate image, queue, source record or rebuild proof is published.

## Assets

| Asset | Authoring role | SHA-256 |
|---|---|---|
| `design-loop-alley-arcade-courtyard-01.png` | alley/arcade split and upper rejoin | `b3ead4dbd4f8a35e1745ef7d94c6ac3c61e0c83af4396f4c0ca6cd1ee1104c26` |
| `design-loop-bridge-watermill-service-01.png` | bridge and single-waterwheel service loop | `66a08721cb0f095f4dd2c8edcea2c9dd47643f084812a6e3a1c300e0c64dea28` |
| `design-loop-communal-hall-01.png` | multi-entry hall and reciprocal egress | `586b57a42d3d64ccb5853ea1394281740e051f3b3af47425db72f5d1d18c81ec` |
| `design-loop-four-portal-courtyard-01.png` | four-portal courtyard and upper return | `2fc5b47555f4877a69175e51095439b4be003848b18044b51232c31960cfc77f` |
| `design-loop-split-level-residence-01.png` | interior/exterior routes between two levels | `800c55a54742456038fcd20f30efd74a46189eda274782e926d1b3e31b94aa5a` |
| `design-loop-terraced-residence-cluster-01.png` | three-building vertical circulation loop | `631fe66f1539e03256ec5e8eecac59a29fa9e4379d4d4fb815409c3cb2126652` |
| `design-loop-village-edge-storehouse-01.png` | lane/storehouse/orchard/forest loop | `e2e822c57b5d574d1ef0d4de8257e25b3b9fe083fcf4df8e85c5b8e73d2a5509` |
| `design-loop-workshop-compound-01.png` | workshop yard ring and side return | `b08003d3af301df9ef5c3c98390b8cc7ed432fbff7d0a810dba47b5af04d554c` |

Two accepted images use reference edits solely to remove plaque-like objects.
Their prompt-chain files bind private source-image SHAs; those source
intermediates are intentionally excluded from the Release.

## Trust and consumption

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
route_loop_topology=authoring-guidance-not-measured
portal_graph=authoring-guidance-not-measured
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

After the held P7 parser/transaction/source-report items pass Codex review,
GLM may consume these in one isolated geometry lane:

1. define stable route, room and portal IDs with two directed edges per
   traversable opening;
2. reject dangling endpoints, missing reverse edges and duplicate IDs;
3. bind transforms, collision proxies, materials and source SHAs;
4. build one content-addressed exact scene;
5. rerun graph reachability, reciprocal clearance/visibility, six layers,
   seam/target visibility and post-render v2.

The boards guide loop construction; they do not prove a loop exists in 3D.
Real completion still requires real capture, accepted SfM, non-mock GPU 3DGS,
measured alignment and real Viewer QA.
