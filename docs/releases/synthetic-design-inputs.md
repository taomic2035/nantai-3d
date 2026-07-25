# Synthetic design input Releases

This catalog is the single index for replaceable synthetic visual inputs.
README links here instead of repeating one download block per batch.

## Trust boundary

All listed design batches are independent synthetic authoring references:

```text
synthetic=true
stage=design-only
camera_calibration=unknown
geometry_consistency=not-verified
real_photo_texture=false
training_use=forbidden-as-multiview
coverage_use=forbidden
trust_effect=none
```

They may guide canonical Blender geometry, material families, route/portal
graphs and camera placement. They are not calibrated photographs, real
textures, SfM/NeRF/3DGS input, measured clearance, or proof of 360-degree
coverage and arbitrary-coordinate reachability.

## Base visual pack

The [Synthetic Mountain Village Canary Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-canary-2026-07-16)
contains the 68-slot hybrid-v3 visual pack. Its final archive is
`synthetic-mountain-village-visual-pack-hybrid-v3-2026-07-17.zip`, SHA-256
`e4f6226a5253fec02db8f30d996c7d4663483022b7a7dbdb1df508024194e559`.

Install it under
`.nantai-studio/synthetic-village/hybrid-v3/`. The included manifest remains
synthetic and may be replaced only through a new pack revision; never overwrite
registered source records in place.

## Design batch catalog

The 20 batches below contain 164 final design boards. Archive SHA values are
the downloadable ZIP digests; per-image and prompt hashes are in each archive
manifest and linked evidence.

| Batch | Images | Focus | Release | Archive SHA-256 | Evidence |
|---:|---:|---|---|---|---|
| 8 | 6 | reciprocal routes and near occlusion | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch8-2026-07-20) | `6bdafc92b9eb2df3a943c4e5df3466e9609c22db89844dc940db3dab6ca921eb` | [detail](../../handoff/FEEDBACK-IMAGE2-012-batch8-reciprocal-route-pack.md) |
| 9 | 6 | lateral routes and hidden structure | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch9-2026-07-20) | `6f7cc48e40e3d323a98e5ca91633cb6a6a7f623d7544efe44317102b3e5648f8` | [detail](../../handoff/FEEDBACK-IMAGE2-013-batch9-lateral-route-pack.md) |
| 10 | 6 | vertical envelope and contact surfaces | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch10-2026-07-21) | `affe92b238f442b765f495b75cb80c612ead193781f265f340c85fb141722fbf` | [detail](../../handoff/FEEDBACK-IMAGE2-014-batch10-vertical-enclosure-pack.md) |
| 11 | 6 | cross-chunk boundary transitions | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch11-2026-07-21) | `7796df6549b46d525e698a8abfa9708d449ab718153645f458100995247095a4` | [detail](../../handoff/FEEDBACK-IMAGE2-015-batch11-boundary-transition-pack.md) |
| 12 | 6 | directional family references | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch12-2026-07-21) | `8b5fae794df167078e559a1dd1f2029e99b9decc4ef1cee96a370d6c1c2b77d5` | [detail](../../handoff/FEEDBACK-IMAGE2-016-batch12-directional-reference-pack.md) |
| 13 | 6 | modular asset boards | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch13-2026-07-21) | `97e1f9d84ef0b42b8294c49cc74e27a2b9b3e4e5566cc8be29687ac19bf1a7f4` | [detail](../../handoff/FEEDBACK-IMAGE2-017-batch13-modular-asset-boards.md) |
| 14 | 6 | diagonal routes and translated checkpoints | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch14-2026-07-21) | `1470096e9f33cccd94c43be3bab8aa1e4592c4d305d835d3d112a4bc5150be27` | [detail](../../handoff/FEEDBACK-IMAGE2-018-batch14-diagonal-navigation-pack.md) |
| 20 | 8 | role topology and camera envelope | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch20-2026-07-23) | `55251c47fd4b25fa1bca9a2a5b5ee1cc98a567ce98131fdb0d628f00ce8cb360` | [detail](../../handoff/FEEDBACK-IMAGE2-024-batch20-role-topology-camera-envelope.md) |
| 21 | 8 | construction and simulated materials | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch21-2026-07-23) | `cabfe3f7f080e15030d2400a4b1a976f4c739b149716262a0a3e88bf78721d84` | [detail](../../handoff/FEEDBACK-IMAGE2-025-batch21-role-construction-materials.md) |
| 22 | 12 | watermill local-360 roles | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch22-2026-07-23) | `1f842f8ce5eb52bafb5bb6d8a581816e1c7571187537e45ace6af669365fb07f` | [detail](../../handoff/FEEDBACK-IMAGE2-026-batch22-watermill-local360.md) |
| 23 | 16 | environment envelope and support | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch23-2026-07-23) | `549dc14d59feeab29771fce8addbf599adebe6d1f6e5ba301de63397b7cf3e1b` | [detail](../../handoff/FEEDBACK-IMAGE2-027-batch23-environment-envelope-support.md) |
| 24 | 16 | reciprocal perimeter and section closure | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch24-2026-07-23) | `1318656f2019889470bcf47d2765f6cfee335194e735995c104405936edc1723` | [detail](../../handoff/FEEDBACK-IMAGE2-028-batch24-reciprocal-perimeter-section.md) |
| 25 | 8 | environmental realism and walkable boundary | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch25-2026-07-24) | `6673d94c7651a21d73706b1810626d0a9559668eb229dd13a7aa028599906575` | [detail](../../handoff/FEEDBACK-IMAGE2-029-batch25-environment-realism.md) |
| 26 | 6 | 360-degree modeling construction boards | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch26-2026-07-25) | `91f75d265357f9ff25785c466aafe5dd6a1e104b0608ffc0b40e0972a76dcb39` | [detail](../../handoff/FEEDBACK-IMAGE2-030-batch26-360-modeling-boards.md) |
| 27 | 8 | near-field turnarounds and contact geometry | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch27-2026-07-25) | `79d9555e24f7f37c02fb7e10aabe1a99277d7c79ef7f9e693e8dd66545916a09` | [detail](../../handoff/FEEDBACK-IMAGE2-031-batch27-nearfield-turnarounds.md) |
| 28 | 8 | near/mid/far LOD continuity | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch28-2026-07-25) | `3f83d4a588d75471b98ee6b4bbf93d264c8a5851f9a4637afd83e66e4fc19f3c` | [detail](../../handoff/FEEDBACK-IMAGE2-032-batch28-lod-continuity.md) |
| 29 | 8 | material macrovariation and contacts | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch29-2026-07-25) | `c3ff4cd08c7f2a2bf115f71e79d86afae2775d7f6c4a73efa00166d93f83469a` | [detail](../../handoff/FEEDBACK-IMAGE2-034-batch29-material-macrovariation.md) |
| 30 | 8 | spatial landmarks and occluded surfaces | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch30-2026-07-25) | `e7c0417d5f61f6063388264677fbd635adfcfaf16e7e400cdeef9d58dbad20a1` | [detail](../../handoff/FEEDBACK-IMAGE2-035-batch30-spatial-landmarks.md) |
| 31 | 8 | interiors, portals and thresholds | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch31-2026-07-25) | `a29c4032449367fe4efa376b2158b1fed807049fa2ac2bf535185153cdcf9805` | [detail](../../handoff/FEEDBACK-IMAGE2-036-batch31-interior-continuity.md) |
| 32 | 8 | multi-scale route and portal loops | [Release](https://github.com/taomic2035/nantai-3d/releases/tag/synthetic-village-design-inputs-batch32-2026-07-25) | `737d0dc502ad67eb586d7f4565427f34d94763f81c1385f9afbda2edf06905bd` | [detail](../../handoff/FEEDBACK-IMAGE2-037-batch32-route-loops.md) |

## Download and verify one batch

Set `$tag` to a catalog entry. The proxy is process-local and is removed after
the GitHub command; no Git or system proxy is persisted.

```powershell
$tag = "synthetic-village-design-inputs-batch32-2026-07-25"
$releaseDir = ".nantai-studio\release-inputs\$tag"
New-Item -ItemType Directory -Force $releaseDir | Out-Null

$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:HTTP_PROXY = "http://127.0.0.1:7890"
gh release download $tag --repo taomic2035/nantai-3d --dir $releaseDir
Remove-Item Env:HTTPS_PROXY,Env:HTTP_PROXY

$archive = Get-ChildItem $releaseDir -Filter "*.zip" -File
$sumFile = Get-ChildItem $releaseDir -Filter "*.SHA256SUMS.txt" -File
if ($archive.Count -ne 1 -or $sumFile.Count -ne 1) {
  throw "Expected exactly one archive and one checksum file"
}
$expected = ((Get-Content $sumFile.FullName) -split '\s+')[0]
$actual = (Get-FileHash $archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "Release archive SHA-256 mismatch" }
Expand-Archive $archive.FullName -DestinationPath "$releaseDir\unpacked" -Force
```

## Clean package contract

Current batches publish only final PNGs, exact prompt or prompt-chain files,
`manifest.json`, `USAGE.md` and `PAYLOAD-SHA256SUMS.txt`. Queue state,
candidate-source records, contact sheets, rejected variants, generation cache
and deterministic rebuild proofs remain private.

After modeling from any batch, acceptance still requires a content-addressed
scene build, exact source/transform/material/collision bindings, reciprocal
camera checks, six layers, seam/target visibility and post-render policy.
Real-scene completion additionally requires real overlapping capture,
accepted real-photo SfM, non-mock GPU 3DGS, measured alignment and real Viewer
QA.
