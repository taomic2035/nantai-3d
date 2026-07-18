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

The complete 24-frame render and coverage audit remain subsequent gates. This
canary proves the local L0 execution path, not completion of the dataset or
photo-realistic source quality.
