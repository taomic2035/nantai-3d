# FEEDBACK-IMAGE2-038 — Batch33 material source plates

Date: 2026-07-25  
Producer: Codex + OpenAI built-in imagegen  
Consumer: GLM Blender material derivation and Codex rendered QA

## Delivery

- Release:
  <https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch33-2026-07-25>
- Archive:
  `synthetic-village-material-source-plates-batch33-2026-07-25.zip`
- Archive bytes: `26,927,309`
- Archive SHA-256:
  `85ccfc05569f6139dc4d81e851c4de9147088cdf58a4e163d43c5690a0109a0b`
- Manifest SHA-256:
  `3d0128e285fdfd021c24cc37470a864766d5e3f54ef45c582c3f6cff16a7228f`
- Payload-checksum SHA-256:
  `79fe80290d45414f6001441d95679773bf63c2a45dbb77ab9c8f9571e85942c0`
- External sidecar SHA-256:
  `b56b725e541859d1fc13090004039427e6d3fbb0f08547d041e3cee1872fd15a`

The remote ZIP was downloaded again and its SHA recomputed. It contains 19
sorted entries: eight final RGB PNGs, eight exact prompts, `manifest.json`,
`USAGE.md` and `PAYLOAD-SHA256SUMS.txt`. All 18 declared payload hashes pass.
Queue, candidate record, contact sheets, two rejected flagstone variants and
archive-rebuild proof are private.

## Accepted source plates

All PNGs are `1254 x 1254`, RGB.

| Source | Authoring role | SHA-256 |
|---|---|---|
| `material-source-charcoal-roof-tile-01.png` | aged roof-tile appearance and overlap | `714033b608eaf23a36d20b79f6cac2b4c3783ac32dc2adf4486ac2fc5f834ffc` |
| `material-source-damp-creek-bed-01.png` | substrate below separately modeled water | `17ac51105e9eaeb4c4d66f76bc3608f134314dd1b216dada9eadefbd8ca43d8a` |
| `material-source-dry-stack-granite-01.png` | wall stone, joints and macrovariation | `5502c5a8b5a3a6204d4dab22cc6812b6262f398089ebd7b4e073c7ffb18a1252` |
| `material-source-flush-flagstone-01.png` | flush walking-surface appearance | `540aa72cec1aee8b40586546abe3e019e46de22a72c62308c3eedf65c23857ac` |
| `material-source-forged-iron-01.png` | hardware, guard and machinery appearance | `bc5b89bae37c83a3c0dacb9da76f072cbbc838a19dfffec33e2f91b7bb576951` |
| `material-source-lime-earth-plaster-01.png` | plaster grain and macrovariation | `f0f768a0abfcd1e94b61a968ff07dd9652869d793c0f23f139d31a10cf052a37` |
| `material-source-moss-soil-blend-01.png` | terrain/contact blend appearance | `3a708b0b873b2d162f3cdf6a930b63e6f0a8cce03807b9ec8574c4907bfb34fa` |
| `material-source-weathered-timber-01.png` | timber grain and board variation | `4f606771769148045bf07bde458b46ed9bbd9356a037e2b1e2033e6ef1d1daf8` |

## Visual QA and trust boundary

Original-resolution and 2x2 repeated-tile sheets were inspected. No accepted
image contains visible text, logo, watermark, scene boundary or unrelated
object. Two flagstone variants were rejected for protruding rocks or embossed
surface artifacts.

The repetition sheet confirms an important limitation: these are
**tile-friendly source plates, not mathematically seamless textures**.
Stone-wall, creek-bed and flagstone boundaries are especially visible without
seam synthesis and macrovariation.

```text
synthetic=true
stage=material-source-only
real_photo_texture=false
pbr_channels=not-derived
albedo_calibration=not-measured
tileability=not-verified
metric_scale=unknown
training_use=forbidden-as-multiview
coverage_use=forbidden
clearance_use=forbidden-as-evidence
trust_effect=none
```

They are not real photographs, calibrated albedo scans, measured PBR maps,
geometry, collision evidence, camera views or 360/arbitrary-coordinate
coverage proof.

## Explicit GLM task

Do this on new GLM-owned paths after the held P7 transaction work is accepted,
or while it is waiting for Codex review without touching its WIP:

**Correction:** do not add another material-source derivation tool. Reuse the
existing, tested H3 chain:

- `pipeline/synthetic_village/h3_material_sources.py`;
- `pipeline/synthetic_village/h3_material_authoring.py`;
- `pipeline/synthetic_village/material_bundle_v2.py`;
- `import-h3-material-sources`, `author-h3-materials` and `build-h3-ktx2`
  in `scripts/synthetic_village.py`.

The first H3 source pack already has eight slots × three candidates and a
deterministic 4096 authored-master/PBR/KTX2 path. Batch33 must not bypass or
weaken its selection, rights, seam, source-preservation or content-addressing
contracts.

Exact consumption split:

1. `weathered-timber` and `dry-stack-granite` may be reviewed as alternative
   sources for the existing H3 hero slots
   `material-weathered-timber-01` and `material-dry-stone-wall-01`; never
   overwrite the accepted v1 source pack.
2. `damp-creek-bed`, `flush-flagstone`, `forged-iron` and
   `lime-earth-plaster` map to existing scene slots
   `material-creek-rock-01`, `material-wet-stone-paving-01`,
   `material-aged-metal-01` and `material-pale-plaster-01`. These four are
   outside H3 v1 and require an additive extension contract.
3. `charcoal-roof-tile` is not a byte-compatible replacement for the current
   curved gray roof-tile source without a separate shape/UV review.
   `moss-soil-blend` is not the same material as `material-moss-stone-01`.
   Do not force either mapping.
4. Batch34 now supplies a complete three-candidate visual set for the four
   additive slots above. Its exact selection and consumer task are in
   `handoff/FEEDBACK-IMAGE2-039-batch34-h3-material-expansion.md`.
5. Apply only accepted authored outputs to a private exact build; render 2x2
   and 8x8 repetition, neutral near/mid/far probes and the existing six
   production roles. Bind source, authored map, replacement contract, bundle,
   build and frame-report SHAs.
6. Hand Codex the machine report/content SHAs and private RGB paths. Do not
   register, copy into `web/data/`, publish a new Release, or write
   `accepted:true` before Codex visual review.

This batch improves the synthetic proxy's appearance path. It does not replace
real overlapping capture, accepted real-photo SfM, non-mock cloud-GPU 3DGS,
measured alignment, real imported texture/geometry or real Viewer QA.
