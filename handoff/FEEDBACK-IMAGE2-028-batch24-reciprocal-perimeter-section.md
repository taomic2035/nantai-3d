# FEEDBACK IMAGE2 028 — Batch24 reciprocal perimeter and section inputs

Date: 2026-07-23
Status: published, design-only

## Delivery

- Release: <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch24-2026-07-23>
- Tag: `synthetic-village-design-inputs-batch24-2026-07-23`
- Archive: `synthetic-village-reciprocal-perimeter-section-pack-batch24-2026-07-23.zip`
- Archive bytes: `58,813,112`
- Archive SHA-256: `1318656f2019889470bcf47d2765f6cfee335194e735995c104405936edc1723`
- Generator: OpenAI built-in imagegen
- Accepted images: `16/16`
- Rejected variants: `0`

The clean archive contains sixteen final PNGs, sixteen exact per-image prompts,
`manifest.json`, `USAGE.md` and `PAYLOAD-SHA256SUMS.txt`. It excludes the
private `source-bindings.json`, `qa-results.json`, rejected variants, Batch23
source PNGs and generation intermediates. An independent extraction reproduced
all 34 public payload checksums.

## Source bindings

Each sector binds one accepted Batch23 environment-envelope image to one
reciprocal view and one section study:

| Sector | Batch23 source | Source SHA-256 |
|---|---|---|
| upstream | `envelope-upstream-creek-valley-01.png` | `4162f58ae98581d609785376c835d9dc858e54634ae63b8d11ab5b969b524a59` |
| northeast | `envelope-northeast-forest-terrace-01.png` | `f8273831cabdd611226f806fec63cf210ab1ab122b043fe80bca333ad3211c98` |
| east | `envelope-east-orchard-route-01.png` | `e194896e2c2fedcac4d9f7b0665a58938b07bd86df02edc12b3bd6e6b0e081b5` |
| southeast | `envelope-southeast-village-service-edge-01.png` | `49db1b5eaf41ae2c1c5c28efc96ec548eeb09155cbf686f5657885b56a477547` |
| downstream | `envelope-downstream-creek-basin-01.png` | `35836fa82d42942bdd5393c224e3beec7da12b2ce140b7ee2a31f5420f960ce3` |
| southwest | `envelope-southwest-stone-bank-return-01.png` | `6be0758b5ed1af4452924a9e7f1df06fe8484f32eef187bed44d402a444a1f76` |
| west | `envelope-west-uphill-forest-loop-01.png` | `0491b126697f0713c6ec675749d97a20291a23bc7cb1f0eb5f507aa434b79e49` |
| northwest | `envelope-northwest-flume-ridge-01.png` | `a76097052687970b945a8326427923f457055b710cf218d96de63437f4633a4f` |

The binding records visual context, not a recovered physical scene, reciprocal
camera pose, pixel correspondence or shared metric geometry.

## Asset inventory

| File | Sector / kind | Batch23 source | Dimensions | Bytes | SHA-256 |
|---|---|---|---:|---:|---|
| `reciprocal-downstream-creek-basin-inbound-01.png` | downstream / reciprocal | `envelope-downstream-creek-basin-01.png` | 1536×1024 | 3,866,422 | `1099282dd6d8a4ffad94b61c989e0a7fd1bab229be916d0565c203d8712a7e9b` |
| `reciprocal-east-orchard-route-inbound-01.png` | east / reciprocal | `envelope-east-orchard-route-01.png` | 1536×1024 | 3,513,807 | `39bc303359bf1f1c4028c1dba42619dcb7b22ac21945fd8e8d0d4c5eded91a38` |
| `reciprocal-northeast-forest-terrace-inbound-01.png` | northeast / reciprocal | `envelope-northeast-forest-terrace-01.png` | 1536×1024 | 3,434,357 | `6f056c7f5bbbcefb8b8af6b0e5980656beaf281a162c6f3e4057c9d99f35753d` |
| `reciprocal-northwest-flume-ridge-inbound-01.png` | northwest / reciprocal | `envelope-northwest-flume-ridge-01.png` | 1536×1024 | 3,528,791 | `622a1264f7432cf29523a699bf9bc5b24031e25f6d4c8846c7ffca27fc392a18` |
| `reciprocal-southeast-service-edge-inbound-01.png` | southeast / reciprocal | `envelope-southeast-village-service-edge-01.png` | 1536×1024 | 3,849,790 | `9ed97dd7d1cc61b4817e021b79b8dd27580818db333d67740a923564d6de1b59` |
| `reciprocal-southwest-stone-bank-inbound-01.png` | southwest / reciprocal | `envelope-southwest-stone-bank-return-01.png` | 1536×1024 | 3,728,460 | `b4a7dbe7cac6bffe6fa90ed817cee69e0e661d00914216d083adecdeb3412c44` |
| `reciprocal-upstream-creek-valley-inbound-01.png` | upstream / reciprocal | `envelope-upstream-creek-valley-01.png` | 1536×1024 | 3,674,059 | `8ff37aa89b68cb3c6fa63d2ae27caa938c45d251badfab248b324a4039d06526` |
| `reciprocal-west-uphill-forest-inbound-01.png` | west / reciprocal | `envelope-west-uphill-forest-loop-01.png` | 1536×1024 | 4,072,247 | `c4dd5f94fd10723a6ae6b1decde9992bd498d73997237bd326367782db4b5a77` |
| `section-downstream-tailwater-floodbench-01.png` | downstream / section | `envelope-downstream-creek-basin-01.png` | 1536×1024 | 3,776,307 | `961a01195a750190433d843cb956a7a2ed33e6a3e1b9fffd4221abeb114c0623` |
| `section-east-orchard-route-cutfill-01.png` | east / section | `envelope-east-orchard-route-01.png` | 1536×1024 | 3,673,608 | `4b751defe9f54e82ffa1d3fffc8ef8bf8c4b93f6e84dbd571ea90fbe98797395` |
| `section-northeast-terrace-drainage-01.png` | northeast / section | `envelope-northeast-forest-terrace-01.png` | 1536×1024 | 3,812,044 | `904c1f177553368ddd46bab097129555a3e64ceba26b2e9833b945481bc75980` |
| `section-northwest-flume-ridge-support-01.png` | northwest / section | `envelope-northwest-flume-ridge-01.png` | 1536×1024 | 3,955,971 | `87e615ad0108668f1b71274d7357e5c7c55cddc1faa243a1416f49c87221e5c5` |
| `section-southeast-service-yard-drainage-01.png` | southeast / section | `envelope-southeast-village-service-edge-01.png` | 1536×1024 | 3,766,895 | `4b70625e0e1250749756ef9344d01af32d4fa8ce2138e51890e4d0d55d00ad45` |
| `section-southwest-bridge-bank-foundation-01.png` | southwest / section | `envelope-southwest-stone-bank-return-01.png` | 1536×1024 | 4,167,819 | `8c38577a47c174c8a358135651dc537946cf82e20e46868a5524090d17ecca35` |
| `section-upstream-flume-creek-support-01.png` | upstream / section | `envelope-upstream-creek-valley-01.png` | 1536×1024 | 3,813,683 | `b4a6dcd299d35286605097da4e2b5958177cc4c143bc84b3554d365fe512618a` |
| `section-west-forest-loop-retaining-01.png` | west / section | `envelope-west-uphill-forest-loop-01.png` | 1536×1024 | 3,913,364 | `d28577a334db61abb3c4ab5076a062a1dceb7698f1a960b6d8069112acced6bd` |

## Original-resolution visual QA

All sixteen PNGs decoded at `1536×1024` and have distinct SHA-256 values. Every
accepted reciprocal view has a changed foreground and translated human-eye
viewpoint relative to its bound source, with readable near/middle/far layers
and an inward route or creek relation. Every section study keeps the relevant
terrain contact, support or foundation and drainage/water path visible in
surrounding context.

All sixteen passed the negative checks: no visible text or watermark; no
people, animals or vehicles; no floating primary structure; and no mirrored or
duplicate source composition. No replacement generation was required.

## Trust boundary

The complete batch is:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

These are synthetic design references, not calibrated multiview photographs.
They do not prove camera poses, reciprocal geometry, 360-degree reconstruction,
arbitrary-coordinate navigation, real geometry or real texture fidelity.

## Blender consumption order

1. Map each reciprocal image to an inward-facing perimeter role and list the
   missing reverse surfaces and bidirectional route/creek/flume connections.
   Do not infer camera coordinates from image pixels.
2. Convert the paired section study into explicit terrain, footing, support,
   drainage and water-level components with stable module IDs.
3. Rebuild the eight perimeter modules, exact scene and chunk registry using
   content-addressed source bindings.
4. Run fresh reciprocal-pair and cross-sector seam checks, then camera
   clearance, target visibility, six-layer rendering and post-render v2.
5. Keep the result `modeled-unverified` until those machine reports pass.

The next useful step is Blender consumption and a fresh exact-build/render
cycle. Real 3D models and real textures still require overlapping real capture,
SfM poses and an external GPU reconstruction or training result.
