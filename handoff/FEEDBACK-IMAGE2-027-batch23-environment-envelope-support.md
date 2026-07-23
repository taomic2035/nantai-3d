# FEEDBACK IMAGE2 027 — Batch23 environment envelope and support inputs

Date: 2026-07-23
Status: published, design-only

## Delivery

- Release: <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch23-2026-07-23>
- Tag: `synthetic-village-design-inputs-batch23-2026-07-23`
- Archive: `synthetic-village-environment-envelope-support-pack-batch23-2026-07-23.zip`
- Archive bytes: `57,708,511`
- Archive SHA-256: `549dc14d59feeab29771fce8addbf599adebe6d1f6e5ba301de63397b7cf3e1b`
- Generator: OpenAI built-in imagegen
- Accepted images: `16/16`
- Rejected variants: `0`

The clean archive contains sixteen final PNGs, sixteen exact per-image prompts,
`manifest.json`, `USAGE.md` and `PAYLOAD-SHA256SUMS.txt`. It contains no
rejected variants, contact sheet, queue state or generation intermediates.
The archive was extracted into an independent private directory and all 34
payload checksums were recomputed successfully.

## Asset inventory

| File | Role | Dimensions | Bytes | SHA-256 |
|---|---|---:|---:|---|
| `construction-bridge-watermill-longitudinal-support-01.png` | bridge and single-waterwheel mill longitudinal support and load path | 1536×1024 | 3,603,666 | `a2d81a36dc08e4e6ce47215ce902c893f6e66350fdfd14821a0cc8e442d4ff14` |
| `construction-cross-bank-foundation-01.png` | opposing creek-bank abutments, foundations and scour protection | 1536×1024 | 3,656,936 | `80e7c2c0b07c515f6d7fa316f00dc6988804dfcf7d35d40b745f9e7b112e91c2` |
| `construction-retaining-stair-orbit-support-01.png` | retaining wall, stair and supported maintenance platform | 1536×1024 | 3,746,001 | `7fb1432dae9d54e1c438aabe55d9d47fa1590efc6c6057bde4f8f3b0f62f9f22` |
| `construction-tailrace-creek-junction-01.png` | tailrace outlet, retaining returns and natural creek junction | 1536×1024 | 3,907,437 | `eb88dfb959fdedc42f51ddc860b0c00904640e6f8aecff794c42a726f561433f` |
| `envelope-downstream-creek-basin-01.png` | downstream basin, flood bench, tailwater and return paths | 1536×1024 | 3,882,228 | `35836fa82d42942bdd5393c224e3beec7da12b2ce140b7ee2a31f5420f960ce3` |
| `envelope-east-orchard-route-01.png` | orchard slope, service route and mountain enclosure | 1536×1024 | 3,537,661 | `e194896e2c2fedcac4d9f7b0665a58938b07bd86df02edc12b3bd6e6b0e081b5` |
| `envelope-northeast-forest-terrace-01.png` | forest-to-terrace switchback, retaining and drainage | 1536×1024 | 3,601,004 | `f8273831cabdd611226f806fec63cf210ab1ab122b043fe80bca333ad3211c98` |
| `envelope-northwest-flume-ridge-01.png` | supported elevated flume, lower route and ridge | 1536×1024 | 3,938,852 | `a76097052687970b945a8326427923f457055b710cf218d96de63437f4633a4f` |
| `envelope-southeast-village-service-edge-01.png` | service courtyards, drainage and terraced forest edge | 1536×1024 | 3,715,497 | `49db1b5eaf41ae2c1c5c28efc96ec548eeb09155cbf686f5657885b56a477547` |
| `envelope-southwest-stone-bank-return-01.png` | stone bank, supported landing and alternate pedestrian loop | 1536×1024 | 3,901,055 | `6be0758b5ed1af4452924a9e7f1df06fe8484f32eef187bed44d402a444a1f76` |
| `envelope-upstream-creek-valley-01.png` | upstream valley, supported flume, crossing and village approach | 1536×1024 | 3,754,825 | `4162f58ae98581d609785376c835d9dc858e54634ae63b8d11ab5b969b524a59` |
| `envelope-west-uphill-forest-loop-01.png` | uphill forest loop, supported landings and village return | 1536×1024 | 3,903,334 | `0491b126697f0713c6ec675749d97a20291a23bc7cb1f0eb5f507aa434b79e49` |
| `transition-creek-bed-wet-dry-01.png` | clear water through wet gravel and moss to dry bank | 1254×1254 | 3,358,298 | `698b1417cbd742deb3a132f30bf4d4e283c5987073af9ec4546e02dd429dec8d` |
| `transition-moss-stone-drainage-wall-01.png` | dry-stone drainage joints, cap, moss, roots and soil contact | 1254×1254 | 3,427,726 | `6da06a697e89d8138597773c89d105cc8c1488fec1b47778f7a13a5966a0137c` |
| `transition-route-soil-vegetation-01.png` | paving through compacted soil and gravel to vegetated slope | 1254×1254 | 3,598,018 | `53a2a683b85e1b89cad78fbf5417a84e1681588bc231e9968b7b8e569bd5eaa9` |
| `transition-timber-stone-bearing-joint-01.png` | timber bearing seat, metal strap and stone-pier contact | 1254×1254 | 3,460,555 | `dfe50d3e2679b893d507900cb9437e03574705d8eeada5e2949c39dd17d0e972` |

## Original-resolution visual QA

Every image was inspected at original resolution. The eight envelope images
passed role match, foreground/middle/far layering, route or terrain continuity
and readable primary support. The four construction images passed role match,
surrounding connection context and visible load-path/contact checks. The four
transition images passed role match and showed a broad physical transition
rather than a seamless swatch.

All sixteen passed the negative checks: no visible text or watermark; no
people, animals or vehicles; no floating primary structure; and no duplicate
waterwheel. One dry-stone reference expresses drainage through open joints
rather than a discrete pipe, which is acceptable for its intended role.

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

The images are independent compositions, not a calibrated multiview capture.
They do not establish pixel correspondence, pose, metric scale, 360-degree
coverage, arbitrary-coordinate navigation or real-world texture fidelity.
They must not be used as SfM, NeRF or 3DGS training evidence.

## Blender and chunk consumption guidance

1. Use the eight envelope images to design replaceable perimeter terrain,
   forest, route, creek and far-field modules around the current exact scene.
   Do not back-solve a shared camera rig from them.
2. Use the four construction images to replace floating or weakly supported
   bridge, flume, watermill, platform and retaining-wall geometry with explicit
   foundations and load paths.
3. Use the four transition studies to author geometry-aware material masks and
   wet/dry zones. They are not seamless PBR maps and do not supply normal,
   roughness, metallic or displacement channels.
4. Bind consumed modules through content SHA and rebuild the exact scene and
   chunk registry. Image filenames alone confer no provenance or coverage.
5. After every topology or placement change, rerun fresh camera clearance,
   Phase 4.3 visibility probes, six-role/six-layer renders and post-render v2.
   Only those machine reports may support navigation acceptance.

The highest-value next modeling pass is the far-field terrain/forest envelope,
followed by bridge-watermill foundations and then creek/route transition masks.
Real 3D geometry plus real textures still requires real overlapping capture,
SfM poses and an external GPU reconstruction/training result.
