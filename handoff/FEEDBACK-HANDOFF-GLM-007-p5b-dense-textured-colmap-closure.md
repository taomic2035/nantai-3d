# FEEDBACK-HANDOFF-GLM-007 — P5b dense textured COLMAP success-path closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex

## Summary

**P5b is accepted as a success-path rehearsal.** Real COLMAP 4.1.0 recovered
a complete sparse model from a 60-frame densely overlapping textured synthetic
Blender sequence. This is the first successful SfM evidence in the project —
all previous P4/P5/P6 runs registered 0 images on untextured or low-texture
captures.

**Registration: 60/60 (100%).** Sparse points: 44,426. Verified match pairs:
1,770/1,770. No GT poses, fake executables or relaxed trust gates were used.
Provenance remains `synthetic-capture / sfm-local / arbitrary-units / unaligned`.

This closes P5b and unblocks P6b (sampled video) and P7 (recovered-pose
training).

## Source build

| Field | Value |
|---|---|
| blend SHA-256 | `8bc4877f120ae6f0126cea9676751b9f84e7042c2d2cdc1c82c5bcf762d487dc` |
| build ID | `704a0b6ce21022f68dc58b0e6584f6e6e0888458ed7645401e4343a3990540a2` |
| build report SHA | `50caf5d921349d18c8b68e473248555b981a1f0a81d2647b7ad67472553b84d6` |
| builder script SHA | `7d36f7f0596724c5505e1597ca24856779d6e15afc88fd0c3e26231a084d0c74` |
| scene plan SHA | `1a05b678a61ca15228ac3be219864699d0ad333e9a2210cb16277147a32283d4` |
| Blender version | 4.5.11 (EEVEE_NEXT, AgX view transform) |
| Blender exe SHA | `0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255` |
| synthetic | true |

This is the P2b AFTER textured canary build with terrain texture scale = 1.0
(correction from 3.0). It is not the exact-266 overlay build.

## Dense orbital render

| Parameter | Value |
|---|---|
| camera count | 60 |
| orbit radius | 20.0 m |
| eye height | 1.6 m above terrain (ray-cast) |
| angular step | 6° |
| resolution | 1280 × 720 (0.92 MP) |
| FOV | 65° horizontal |
| render engine | BLENDER_EEVEE_NEXT |
| color management | AgX |
| elapsed | 253.24 s |
| scene center (Blender) | (22.5, 86.36, 115.5) |
| scene bbox (Blender) | (-635, -250, -8) → (680, 423, 239) |

Camera positions were ray-cast to terrain and recorded in `render_manifest.json`
for binding only. They were **not** passed to COLMAP as GT poses. COLMAP
recovered all 60 poses from image features alone.

Every rendered PNG SHA-256 is bound in the evidence JSON at
`tmp/p5b-dense-textured-colmap/p5b_evidence.json`.

## check_capture preflight

| Metric | Value |
|---|---|
| verdict | `unlikely` |
| reason | 60 images at 0.92 MP (below 1.0 MP threshold) |
| blur | 0/60 below threshold (median Laplacian variance 303.91) |
| EXIF GPS | 0/60 (synthetic, no GPS) |
| recommended matcher | sequential (600 pairs) |

**Important finding**: `check_capture` verdict was `unlikely` due to sub-1.0 MP
resolution, but COLMAP succeeded with 100% registration. This confirms that
`check_capture` is an **advisory preflight, not a gate**: the `unlikely` verdict
means "no obvious hard injury found" or "some hard injury found", but it does not
predict SfM success or failure. The decisive factor was texture density and
overlap, not resolution.

## COLMAP production caller

| Field | Value |
|---|---|
| COLMAP version | 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA) |
| COLMAP exe SHA | `15cd3da19e4b8712dd86296c370b0d75dfb9f5a9185be031299f9e23a534e5ed` |
| caller | `pipeline.registration.register(engine='colmap', use_gpu=False)` |
| GPU | disabled (CPU-only, Intel UHD 770) |
| matcher | exhaustive (60 ≤ 400 threshold) |
| stage timeout | 21600 s (6 h backstop) |
| total elapsed | 256.99 s |
| argv | standard 4-stage: feature_extractor → exhaustive_matcher → mapper → model_converter |

## COLMAP results

| Metric | Value |
|---|---|
| registered images | 60 / 60 (100%) |
| sparse point count | 44,426 |
| sparse model | yes (sparse/0/) |
| pose frame | sfm-local |
| metric status | arbitrary |
| geo aligned | unaligned |

### Feature statistics

| Metric | Value |
|---|---|
| images in DB | 60 |
| images with features | 60 |
| feature count (min) | 3,938 |
| feature count (max) | 17,755 |
| feature count (mean) | 10,271.6 |

**Comparison with P4**: P4's untextured canary renders produced ~345 features
per image and 37 verified matches per pair (max), leading to 0/24 registration.
P5b's textured renders produce 10,271.6 features per image on average — a **30x
improvement** — demonstrating that texture density is the decisive factor for
SIFT-based SfM on synthetic captures.

### Match statistics

| Metric | Value |
|---|---|
| match pairs | 1,770 (= 60 × 59 / 2, all exhaustive pairs) |
| total matches | 642,110 |
| verified match pairs | 1,770 (100%) |
| verified matches | 517,444 (81% verification rate) |

### Sparse model outputs (SHA-256 bound)

| File | SHA-256 | Size |
|---|---|---|
| cameras.txt | `472a0c8b0e058d8cb0821ead80ed7064d9ef5f13935e9959a4eb254f9d85e7c9` | 4,639 B |
| images.txt | `08af8af17fd93796b1b2ff63f9f2c56c0e8f11f8c5abe38f1e5740e40f956b24` | 24,786,991 B |
| points3D.txt | `94714430d05e62bfb81ca642c7f3b65ad124d2e97c5e8951bc6cc2915b9824ac` | 5,345,172 B |
| frames.txt | `4d79f67a254387506da8ee43162640c2ccb864a0e2e1c5aadf2444993acccdb0` | 10,034 B |
| rigs.txt | `0a0f410792016d6dc9eed8cb5fcba5feb2968a27177ad3a966fe9f8442ece754` | 1,151 B |

### COLMAP database

| Field | Value |
|---|---|
| DB SHA-256 | `cf9e073246a1aac7cd3f185b0a013a2bd86f48cf915b9a9c53d82407d0e572e0` |
| DB size | 104,902,656 B (100 MB) |

## Success-path acceptance criteria

Per HANDOFF-GLM-007 section 9, point 5:

| Criterion | Required | Actual | Pass |
|---|---|---|---|
| at least one sparse model | yes | yes (sparse/0/) | ✓ |
| nonzero sparse points | yes | 44,426 | ✓ |
| ≥ 80% registered images | yes | 100% | ✓ |

**Accepted: all three criteria met.**

## Provenance (not promoted)

This success remains:

```text
synthetic-capture / sfm-local / arbitrary-units / unaligned
```

It does **not** close any of the five real-scene evidence items:
1. real overlapping capture — still absent (this is synthetic Blender render)
2. accepted COLMAP/SfM on real photos — still absent (this is synthetic)
3. non-mock cloud-GPU 3DGS training — still absent
4. imported splat with measured alignment — still absent
5. Viewer QA over real artifact — still absent

## What P5b proves

1. The production COLMAP caller (`pipeline.registration.register`) works
   correctly on a success path: it runs all 4 stages, writes `registration.json`
   with 60 poses, and correctly reports `sfm-local / arbitrary / unaligned`.
2. Textured synthetic captures with dense orbital overlap (60 cameras, 6° step,
   20 m radius, 65° FOV) produce sufficient SIFT features (mean 10,272/image)
   and verified matches (517,444 total) for complete SfM registration.
3. `check_capture`'s `unlikely` verdict on sub-1.0 MP resolution does not
   predict SfM failure; it is advisory only.
4. The P2b terrain texture scale correction (3.0 → 1.0) contributed to the
   feature density improvement, though this P5b run does not isolate that
   variable causally.

## Next steps

Per HANDOFF-GLM-007 section 10:

- **P6b**: encode this 60-frame dense ordered sequence into a video with ≥ 120
  source frames at higher source FPS, set `max_frames` below the sampled count,
  bind OpenCV backend, and prove sequential matching from machine evidence.
- **P7**: consume the real COLMAP-recovered sparse model from P5b (not GT
  poses) for Brush training and private import.

The immutable private evidence root is:
```text
tmp/p5b-dense-textured-colmap/
  p5b_evidence.json          (full machine evidence)
  registration.json           (60 COLMAP-recovered poses)
  colmap_ws/colmap.db         (feature/match database)
  colmap_ws/sparse/0/         (cameras.txt, images.txt, points3D.txt)
  images/frame_0000.png ... frame_0059.png  (60 source RGBs)
  images/render_manifest.json (camera positions for binding, NOT GT input)
```
