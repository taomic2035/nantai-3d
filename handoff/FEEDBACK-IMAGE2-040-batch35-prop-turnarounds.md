# FEEDBACK-IMAGE2-040 — Batch35 prop turnarounds

Date: 2026-07-25
Producer: Codex + OpenAI built-in imagegen
Consumer: GLM canonical prop geometry; Codex rendered QA

## Delivery

- Release:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch35-2026-07-25>
- Archive:
  `synthetic-village-prop-turnarounds-batch35-2026-07-25.zip`
- Archive bytes: `18,183,084`
- Archive SHA-256:
  `00261044d27f7d7d4889bd23255f1e6a17e9450e15d6f7b725bbb026aff2a28a`
- Manifest SHA-256:
  `46417576012a29a70addbd74d63dae0b28261143cceb2afc2156cfc1b236755f`
- Payload-checksum SHA-256:
  `d8387f02c93bfb8e8d4b74933ae93232f8bb2e288b442d6621043e43a4f3b604`

The clean archive has 19 sorted entries: eight accepted PNGs, eight exact
prompts, `manifest.json`, `USAGE.md` and `PAYLOAD-SHA256SUMS.txt`. Two fixed-
timestamp builds produced identical bytes. A fresh GitHub download reproduced
the archive SHA and all 18 declared payload hashes. The private contact sheet,
generation paths, queues and caches are absent.

## Accepted modeling inputs

All eight images are `1536 × 1024`, RGB24 and contain six unobstructed panels.

| Existing slot | Design board | SHA-256 |
|---|---|---|
| `prop-water-jar-01` | `design-turnaround-water-jar-01.png` | `30caa127934742e64889ea2dc5055b4c34a72174736ea999cc26e057d60149c6` |
| `prop-firewood-stack-01` | `design-turnaround-firewood-stack-01.png` | `1106dd3682b944e8806174a7b277eccf598daccf7b562c8cb3ec3e468ec98b71` |
| `prop-bamboo-basket-01` | `design-turnaround-bamboo-basket-01.png` | `92a09118cb5d33e703979fa998940b2dc4a23f9fea0140a86dc7b98a96c2dd8b` |
| `prop-wooden-bench-01` | `design-turnaround-wooden-bench-01.png` | `328748cf38d12abc6aedab557162233e8e0e006e5f8ec2907be71ba634153a3a` |
| `prop-farming-tools-01` | `design-turnaround-farming-tools-01.png` | `075cf4d252e39d374c012e13b24d8e365a2bedfbc16abaca18f0ba0a62d1e6ad` |
| `prop-grain-rack-01` | `design-turnaround-grain-rack-01.png` | `e5bebc0502bacdc74ef2b1941bce6a829342554479c9a0db895867f6c0345745` |
| `prop-stone-trough-01` | `design-turnaround-stone-trough-01.png` | `0634f7d9ae8287dfecb00f61064f250c4ff6585e09a4f0eea56c1581577422b1` |
| `prop-handcart-01` | `design-turnaround-handcart-01.png` | `3f5e83b734cb707e2011804ff33733752f07210e6c05c421dacfb0e232af0e39` |

Original-resolution review found no visible text, logo, watermark, people,
animals or modern vehicles. Jar, basket, bench, trough and handcart provide
the strongest shape continuity. Firewood framing, tool-rest presentation and
grain-rack rail/slat counts vary across panels; exact differences are recorded
in the manifest and must be resolved into one canonical part graph.

## Why this is actionable

The eight slot ids already exist in the visual catalog and are consumed by
`scripts/blender/build_synthetic_village.py::_build_prop`. The current code is
still a coarse primitive proxy: for example the jar is two cylinders, the
firewood stack is twelve cylinders, the bench has a back-like slab despite its
backless slot description, the farming-tools cluster has only two handles and
two boxes, and the handcart wheels are solid cylinders without spokes.

Batch35 therefore supplies direct reference input for an existing runtime
path; it does not introduce eight orphan concepts.

## Exact GLM task

Do this after the held P7 transaction slice and current roaming-graph candidate
reach their Codex review point. Use a new GLM-owned pure-model path first; do
not mix it into transaction WIP or Codex exact-266 caller/overlay files.

1. Add a pure canonical prop-geometry v2 plan for exactly the eight slot ids
   above. Each stable part records `part_id`, local transform, material slot,
   render bounds, collision/support role and Batch35 source SHA.
2. Keep ScenePlan `width_m/depth_m/height_m` and instance transforms
   authoritative. Image proportions are guidance only and must never overwrite
   metric contracts.
3. Freeze one explicit part graph where panels disagree. Do not average
   generated views or switch components by camera.
4. Cover at minimum:
   - jar body/rim/opening/foot;
   - stack frame plus varied logs;
   - basket body/rim/loops/open interior/base;
   - backless bench seat/legs/braces/pegs;
   - four distinct tool heads/handles plus rest;
   - rack frames/rails/braces/slatted shelf;
   - open trough basin/walls/notch/feet;
   - cart bed/two spoked wheels/axle/handles/braces/rests.
5. Fail closed on duplicate/unknown part ids, non-finite transforms,
   zero-volume parts, dimension-envelope overflow, floating required supports,
   forbidden interpenetration, missing collision proxies, wrong tool/wheel
   counts and source-SHA mismatch.
6. Emit through the existing Blender builder only after pure-model RED tests
   pass. Keep material slots registered independently; never project these
   boards as textures or use opaque camera-facing billboards.
7. Produce a private exact build and five-direction probe per prop
   (front/side/rear/top/underside-contact), then rerun the six production
   roles, clearance/visibility and post-render v2.
8. Return content/report SHAs and private RGB paths to Codex as
   `candidate / Reviewer: pending Codex`. Do not edit `web/data/`, register
   defaults or claim acceptance.

Minimum new test target:

```text
tests/test_prop_geometry_v2.py
```

Existing Blender-builder, exact-build and material-bundle tests must remain
green unchanged.

## Trust boundary

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
metric_scale=unknown
real_photo_texture=false
training_use=forbidden-as-multiview
coverage_use=forbidden
clearance_use=forbidden-as-evidence
trust_effect=none
```

Batch35 improves the synthetic proxy's near-field geometry references and can
increase readable parallax from multiple viewing directions. It does not
provide real photographs, real imported geometry/texture, SfM/3DGS evidence,
measured alignment, 360-degree coverage, arbitrary-coordinate reachability or
real Viewer QA.
