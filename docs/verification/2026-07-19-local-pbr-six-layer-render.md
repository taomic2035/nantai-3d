# Local PBR six-layer render canary

Date: 2026-07-19

## Contract

The strict synthetic-village frame renderer now accepts two non-interchangeable
provenance pairs:

- formal canary: `render-frame-request.v1` + `L2`;
- Mac local textured build:
  `local-textured-render-frame-request.v1` + `L0`.

Request, frame-report, camera-metadata, and render-journal schemas all enforce
the same pairing. A local request additionally requires the loaded Blender
scene to contain the measured `nv_preview_id`, `nv_authoritative=false`, and
`nv_release_channel=local-preview-only` properties. Cross-pairing L0 with the
formal schema or L2 with the local schema fails validation.

Render output lives outside the immutable nine-file training-build snapshot:

```text
.nantai-studio/synthetic-village/hybrid-v3/local-training-renders/
  <build-report-sha256>/<render-id>/
```

The runner is resumable. It revalidates each previously verified frame from
current bytes before counting it as reused and quarantines inconsistent partial
outputs.

## Real Mac canary

Inputs:

- build-report SHA-256:
  `6313cb5c7f1dae363c8928e8a952fdfc68eccec5f782739221991d1caba62ac5`
- Blender scene SHA-256:
  `88f6d832830d7f7495d577c1a9469af85bed043f1920a1fe7c4a9208d7c49825`
- render ID:
  `217e1cac5c76a9d5644ce7c0ec46408d285d09cbef30312739100d855e77afae`
- camera: `camera-outer-001`
- Blender: macOS Apple Silicon 4.5.11 LTS

Measured result:

- first run: one newly rendered and verified frame, no renderer stderr;
- resume run: zero new frames and one byte-revalidated reuse;
- approximate first-frame wall time: 56 seconds;
- six artifacts: RGB, Euclidean camera-range depth, world normal, instance
  mask, semantic mask, and calibrated camera metadata;
- maximum depth/Position disagreement: `0.004359108 m`;
- maximum normal unit-length error: `0.000000049`;
- 13 observed semantic IDs and 93 observed canonical/noncanonical instance
  values including background.

## Complete local render

The same content-addressed build subsequently completed all 24 planned local
frames:

- 24/24 frames verified;
- 23 new frames plus the byte-revalidated first-frame reuse;
- no renderer stderr;
- subsequent frames took approximately 10--15 seconds each on this Mac;
- output size: approximately 212 MiB across the six layers and journals.

Ground-truth conversion to the COLMAP text layout retained all 24 cameras and
three intrinsic groups. The conversion sampled 49,308 initialization points.
Two adjacent-camera depth consistency probes measured median relative errors of
`0.0011` and `0.0008`. These checks validate the local camera/depth conversion;
they do not validate scene realism or guarantee enough views for 3DGS training.

The subsequent symmetric depth-visible-surface overlap audit measured every
camera against every possible neighbor. It takes the smaller of the two
directional overlap ratios so that a narrow view contained by a distant wide
view cannot report a false 100% pair:

- target: at least `0.65` best symmetric overlap per camera;
- passing cameras: 12/24;
- minimum / median / maximum best overlap:
  `0.002047782` / `0.640190972` / `0.841929002`;
- all eight outer cameras passed;
- four bridge, four courtyard, and four ground cameras failed;
- the command exited `2` and published a private canonical audit report.

This is depth-visible rendered-surface evidence only. It does not prove feature
matches, SfM registration, or reconstructability, and it has no trust effect.

## 3DGS visual-quality gate: failed

Brush trained the converted dataset for 2,000 steps and exported 106,016
degree-3 SH Gaussians. An independent comparison of three held-out renders
against their source RGB frames measured:

| Held-out frame | PSNR (dB) | SSIM |
|---|---:|---:|
| `camera-bridge-001` | 6.824113 | 0.258619 |
| `camera-ground-001` | 20.442931 | 0.415808 |
| `camera-outer-001` | 22.841708 | 0.729803 |
| mean | 16.702917 | 0.468077 |

Visual inspection also found severe smearing, holes, and stretched splats.
`camera-bridge-001` is close to or inside scene geometry, while 24 images are
too sparse to constrain a scene approximately 700 by 500 metres. The source
RGB itself remains visibly procedural and tiled, so matching it more closely
would still not by itself make the result photorealistic.

This experiment therefore failed the visual-quality gate and was not promoted.
The Viewer default was restored byte-for-byte to the previous manifest
SHA-256
`c292fc762ace57050f7249ef2ed5c2b247f58893cb37c25b0249ff2e2ccbf650`.
Increasing Brush steps against the same 24 views is not an approved next step.
The next useful gate is a denser, collision-free camera plan followed by a
small-area 3DGS canary; source-material realism is a separate upstream gate.

This result proves the local L0 six-layer execution path and records a failed
training experiment. It does not claim completion of the dataset,
source-image consistency, or photorealistic quality.
