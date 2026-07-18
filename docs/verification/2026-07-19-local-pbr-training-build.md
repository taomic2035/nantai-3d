# Local PBR training-build snapshot

Date: 2026-07-19

## Outcome

The current Mac L0 textured scene can now be retained as a complete private
training source without changing the Viewer publication contract.

The verified snapshot is:

```text
.nantai-studio/synthetic-village/hybrid-v3/local-training-builds/
  6313cb5c7f1dae363c8928e8a952fdfc68eccec5f782739221991d1caba62ac5/
```

Its directory name is the measured SHA-256 of `build-report.json`. The snapshot
contains exactly nine regular files:

```text
build-report.json
glb-material-audit.json
manifest.json
preview-bridge.png
preview-central.png
preview-outer.png
preview-upper.png
village-canary.blend
village-canary.glb
```

Measured evidence from a fresh disk revalidation:

- preview request ID:
  `000e48f209e108f5a127b980f1c08b36dd869371c06bc52cb5ed8b14a923eeb9`
- build-report SHA-256:
  `6313cb5c7f1dae363c8928e8a952fdfc68eccec5f782739221991d1caba62ac5`
- Blender scene SHA-256:
  `88f6d832830d7f7495d577c1a9469af85bed043f1920a1fe7c4a9208d7c49825`
- Blender scene bytes: `138652069`
- GLB bytes: `133877204`
- GLB triangles: `81718`
- snapshot disk size: approximately `264 MiB`

The existing four-file Viewer publication remained unchanged and was reused.

## Why the build report is the content key

Repeated Blender builds from the same request produced different `.blend`
SHA-256 values. This agrees with the existing
`blend_bytes: measured-not-guaranteed` determinism declaration. The request
`preview_id` therefore cannot safely identify a retained Blender file.

The build report records the measured SHA and byte count of every generated
artifact. Hashing that canonical report gives each non-deterministic output set
its own immutable identity while preserving the stable preview request ID as
input provenance.

## Verification boundary

Revalidation checks:

1. the exact nine-file set and direct regular-file paths;
2. directory name equals the current build-report SHA-256;
3. all six reported build artifacts against current bytes;
4. the GLB material, UV, tangent, embedded texture, triangle, and four-sided
   building geometry evidence from the current GLB;
5. the manifest and stored audit against freshly measured bytes.

This is still `synthetic=true`, `verification_level=L0`,
`geometry_usability=preview-only`, and `real_photo_textures=false`. Retaining a
verified source scene does not claim that the 24-view dataset has been rendered
or that a source-consistent 3DGS has been trained. Those are subsequent gates.
