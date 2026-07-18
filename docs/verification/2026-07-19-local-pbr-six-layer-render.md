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

## 3DGS visual-quality gate: initial 24-view failure

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

## Dense local source-consistency canary

Two follow-up controls separated camera coverage from depth-initialization
quality:

1. A 132-camera broad RGB plan still placed neighboring ground-route cameras
   approximately 20 metres apart (with wider environment and perimeter gaps).
   Its 3,000-step Brush result measured only `19.684405 dB` mean PSNR and
   `0.602874` mean SSIM in an independent comparison and was visibly smeared.
2. A private L0 72-camera local route reduced the maximum longitudinal gap to
   `1.976944 m` and used two opposite-direction passes with approximately
   `1.33 m` cross-pass baselines. Reusing initialization points from the
   unrelated 24-view depth set improved the result only to `22.274280 dB` mean
   PSNR and `0.633629` mean SSIM. This isolated mismatched depth initialization
   as a second major failure mode.

The same 72 local RGB frames were then paired with depth rendered from their
exact camera poses. Conversion produced 181,726 initialization points. The
maximum Euclidean depth/Position disagreement across the 72 renders was
`0.004148787 m`.

The symmetric overlap gate still failed closed:

- target: at least `0.65` best symmetric overlap per camera;
- passing cameras: 51/72;
- minimum / median / maximum best overlap:
  `0.566945607` / `0.659099766` / `0.694220922`.

This remains a private L0 experiment, not an approved production camera
profile. Nevertheless, it is a useful controlled source-consistency result.
Brush trained the matching dataset for 3,000 steps in 8 minutes 32 seconds and
exported 357,794 Gaussians. The PLY SHA-256 is
`93825e898d42cdb8e36e7e40114f81b837b217a0aea5924009b66fd867177a98`.

An independent comparison of all eight held-out renders measured:

| Held-out frame | PSNR (dB) | SSIM |
|---|---:|---:|
| `camera-ground-route-001` | 27.432391 | 0.816055 |
| `camera-ground-route-010` | 29.515946 | 0.882763 |
| `camera-ground-route-019` | 29.678477 | 0.878062 |
| `camera-ground-route-028` | 28.419967 | 0.870173 |
| `camera-ground-route-037` | 28.079328 | 0.820112 |
| `camera-ground-route-046` | 30.403554 | 0.884789 |
| `camera-ground-route-055` | 30.093098 | 0.896088 |
| `camera-ground-route-064` | 29.367371 | 0.875883 |
| mean | 29.123767 | 0.865491 |

The controlled slice therefore passes the numerical source-consistency target
of 23 dB, and visual comparison confirms that geometry, colour, and large
texture regions now correspond to the source frames. It does **not** pass the
photorealism gate: high-frequency ground and foliage detail remains soft,
distant edges retain some trailing, and the source scene itself contains
low-polygon roofs and trees plus visibly repeated terrain textures.

The result was not promoted to the Viewer default. The next high-value gate is
a formal six-layer dense profile in which every camera passes the overlap
threshold, followed by higher-fidelity source geometry/materials and a
production cloud-GPU 3DGS run. The broad production plan is also still only
132/180 cameras and lacks its 48 elevated views.
