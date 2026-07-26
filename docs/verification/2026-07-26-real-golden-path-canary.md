# Production V1 real golden-path canary — 2026-07-26

## Verdict

The internal real-image canary completed verified fetch, fresh non-mock COLMAP
SfM and a local Brush plumbing run. Two isolated negative drills also proved
that corrupted completed bytes become `blocked` evidence and that a submitted
but unreachable remote job remains `unknown` without resubmission.

This is **not** Production V1 acceptance:

```text
dataset role = internal-canary
license boundary = internal-only
coordinates = sfm-local / arbitrary / unaligned
local trainer role = preview-only
production_release_allowed=false
```

Remote CUDA Splatfacto, production import, held-out evaluation, three-pose
browser measurement and human visual review were not executed. No dataset,
private job or trained PLY bytes are committed by this report.

## Code and machine identity

| Item | Measured identity |
|---|---|
| Local fetch/SfM/Brush code commit | `2c1602472eadafc6728a139515f81f62ab914c66` |
| Revalidation fix and negative-drill commit | `a9c51dea74fbe5bfdfd637d85454380cd65abe8c` |
| Platform | macOS 26.5.2, Apple Silicon arm64, 32 GB |
| Python | 3.13.13 |
| COLMAP | `COLMAP 4.1.0`, CPU SfM |
| Brush | `Brush 0.3.0`, local wgpu, `preview-only` |
| Brush binary SHA-256 | `8380ed40cce870025393e1ea0257e0752351c67a409d827b76dc75ec3a999a71` |
| Production trainer/container | not run; no operator-approved CUDA container identity was available |

The post-fix repository gate was:

```text
python -m pytest tests/ -q
3268 passed, 138 skipped, 1 existing numerical warning
```

Focused real-runner/dataset regression was 71 passed. Ruff and
`git diff --check` also passed. Viewer Node tests were 216/216 and Studio Node
tests were 103/103.

## Source and fetch evidence

Source: Nerfstudio `poster`, pinned to Hugging Face dataset revision
`461701c17e83c3f4d2481db32315aa7df703d2f8`.

The source card does not declare redistribution rights. The source record
therefore fixes `redistribution_allowed=false` and
`release_inclusion_allowed=false`; the canary is internal-only.

| Evidence | Value |
|---|---|
| Source canonical SHA-256 | `fc208c22958476b95394029f2704ba8cdec04fcd3a4a6d61339cbfe350dbb9a6` |
| Dataset lock SHA-256 | `611a153d8c5a7e3003e8a00409dd8f3c931f3a3bccd7a798581396c6d0d10f1a` |
| Dataset receipt SHA-256 | `225f001523ae56d1a03096b8406d6265e6f222be54bfe375834a1ea7d285ca62` |
| Fetch stage receipt SHA-256 | `467c2246541baaceb90fdda81e4d517baf21db86f7da1c07f45dba4fe82f59b0` |
| Verified set | 408/408 files, 379,280,986 bytes |
| Stage result | `completed` |

The run id was `production-v1-canary-20260726-c` and its workspace was absent
when created. The first portion of the payload was downloaded from the pinned
remote source; after an interrupted transfer, remaining bytes were populated
from a prior same-revision verified local run. The new runner then rehashed and
revalidated all 408 files against the fresh lock and receipt. This proves byte
closure of this workspace, but it is not represented as a single uninterrupted
379 MB network-download measurement.

## Fresh COLMAP SfM

Only the 100 original `poster/images/` files were ingested. Dataset-provided
`database.db`, sparse models and `transforms.json` were retained as source
diagnostics but were not consumed as evidence for the fresh run.

| Evidence | Value |
|---|---|
| SfM stage receipt SHA-256 | `c58e8a32e61a7eecc98147c7e150d529ffc55704125fdaff61c33610df64aa25` |
| Registration SHA-256 | `47d0b616d8a0aef4c2b683775d73f46e40c2d8d4cc4f69976b4b00d410622568` |
| Registration-quality report SHA-256 | `25d26c2a64651282c04181b4b3c58315073391084a0e3070988cc41b95450da5` |
| Engine | `COLMAP 4.1.0`, non-mock |
| Registered | 96/100, ratio 0.96 |
| Longest unregistered run | 2 |
| Quality decision | `quality_accepted=true`, `training_allowed=true` |
| Rejection reasons | none |

The accepted SfM still has `sfm-local / arbitrary / unaligned` coordinates. SfM
coverage does not create metric scale or commercial rights.

## Local Brush preview

| Evidence | Value |
|---|---|
| Train-preview stage receipt SHA-256 | `85d7d85c7f046f2c7ce402206d5547c716bf73466247e5791e235a57baa405cb` |
| Training request file SHA-256 | `1d8458b9f03dd53ca27fae1df2ffc99fe98921835b91064718275bb1ca758366` |
| Training result SHA-256 | `4bd7771d8321756eb79888d1fbd4d24b83751bb8f35b8b6471976fd6891781d8` |
| Brush execution receipt SHA-256 | `e6b38ef1e1a3f254b76f11d2dbad7c03d60031ab49b5b13532acddaf942639f5` |
| Training bundle SHA-256 | `607b7ddf8c855a49151b47aa2e09e282a698229bfbce66ce927cfd7d12287f64` |
| Exported PLY SHA-256 | `af4cc9f7d075759d0e48e179ab4e1f2fab69019e5c444006df2f60d7abe637a4` |
| Exported PLY bytes | 3,180,942 |
| Execution | `Brush 0.3.0`, return code 0, 1,000 steps |
| Quality role | `preview-only` |

This PLY validates local plumbing only. It is not the required
Nerfstudio Splatfacto production artifact and cannot satisfy the ≥100,000
semantically valid Gaussian gate or the held-out visual-quality gate.

## Negative drill 1: completed-byte corruption

An APFS copy-on-write clone of the canary workspace was created at the ignored
run id `corruption-canary-20260726`. Source and clone had different inodes. One
byte of `poster/base_cam.json` was flipped without changing its 846-byte length:

```text
receipt-bound SHA = cc5c71224746139919328b86c581c6746796a24759a7a38cd2a3248331bfc4de
tampered clone SHA = c6eb0c2c7a4fb87565606974f8453c0d6c02c9d2cf742b72d8c80566501f87ee
```

`fetch --resume` rejected the clone and retained:

| Evidence | Value |
|---|---|
| Original completed receipt | `467c2246541baaceb90fdda81e4d517baf21db86f7da1c07f45dba4fe82f59b0` |
| New blocked receipt | `5c5cf5219fb5cb4b53a16a5c3019d4e9f3c44e1ae21457526568f3b0b0a53834` |
| Revalidation evidence | `66f544f5b4d0470711bcbbef908cccdfd3b96aa0a2162eee06ce6cc9f532830d` |
| Result | `blocked`; zero outputs; explicit retry required |

A second resume produced no new execution or receipt. The original canonical
workspace file remained at the receipt-bound SHA.

## Negative drill 2: unreachable submitted remote job

An isolated runner used real `RealScenePipelineOperations` with a fake remote
executor only at the transport boundary. Bootstrap prerequisite bytes and the
fake bundle are drill-only and confer no scene trust. The executor submitted one
job, then raised a simulated unreachable-host observation during poll.

| Evidence | Value |
|---|---|
| Unknown stage receipt | `3e9536cc0481c8b8c86a85f73309f5117fa542da1353f001e7210b06331955fc` |
| Remote job evidence | `b803da1cab3e151390b6690e7d598742fa05f0596ae2b5e1a06e38847087ab0d` |
| Public executor config evidence | `9f85140629dd736c4d842fddbd5e2befd4dfeebb4fc1909005876a1eaf134bee` |
| Result | `unknown`; zero outputs |
| First attempt | `submit_calls=1`, `poll_calls=1` |
| Resume | explicit retry required; receipt count stayed 1; `submit_calls=1` |

The test deliberately does not infer failure or success from loss of contact.

## Required work not executed

The real internal canary still requires operator-owned remote inputs:

- a safe SSH alias and private key;
- a strict known-hosts file plus pinned host-key fingerprint;
- an absolute remote root and remote repository root;
- an immutable production CUDA container digest with Nerfstudio 1.1.5;
- enough cloud GPU capacity to train, evaluate and export the result.

Until those inputs exist, `train-production`, production `import`, held-out
PSNR/SSIM/LPIPS, three-pose Viewer performance and human review remain
not-started. That is an external execution blocker, not a passed or failed
production result.

Commercial acceptance separately requires a rights-cleared
`production-acceptance` capture and at least **four non-coplanar** measured
control points spanning the scene. It must produce a content-addressed Sim3,
metre units and alignment RMS ≤ 0.25 m. The internal poster dataset cannot
substitute for those inputs.

Finally, the formal product remains incomplete until subprojects 2–5 pass:
cross-platform Studio writes/recovery; mixed-session fusion and revisions;
production presentation/material replacement; and production distribution,
upgrade, privacy and security gates.
