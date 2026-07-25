# COLMAP real-binary fixtures

These fixtures are **not** produced by the production parser's sibling helpers
(`_write_fake_cameras_bin` / `_write_fake_images_bin`). They are produced by
the pinned local COLMAP 4.1.0 (`Commit fa8e3b3 on 2026-06-26 without CUDA`,
`third/colmap/bin/colmap.exe`) from independently authored text sources, so a
parser that agrees with itself but disagrees with the official binary format
will fail these tests.

## Sources

- `cameras.txt` — hand-written from the COLMAP 4.1.0
  `BaseCameraModel::CameraName` list and the parameter counts Codex measured
  with `model_converter`. Covers all 12 accepted model ids 0..11.
- `images.txt` — hand-written 12-image record list bound to the
  cameras above.

## Regeneration

```powershell
# From repo root:
$colmap = "third\colmap\bin\colmap.exe"
& $colmap model_converter `
  --input_path tests\fixtures\colmap\text `
  --output_path tests\fixtures\colmap\bin `
  --output_type BIN
```

The committed `.bin` files are the byte-exact output of that command. Do not
hand-edit them; regenerate from the text sources if the format changes.

## Provenance

- COLMAP binary SHA-256:
  `15cd3da19e4b8712dd86296c370b0d75dfb9f5a9185be031299f9e23a534e5ed`
  (`third\colmap\bin\colmap.exe` at fixture-generation time).
- Fixture SHA-256:
  - `cameras.bin`: `9430ac0ac227017a4fffa944b4d875c97687e87f57e635a8647ce03746c1ae0c`
  - `images.bin`: `d57d3b2b3e94b3152f0df293c3e6be7e9e7a35c9e5a07628e73f33ad3b2d62f0`
  - `points3D.bin`: `af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc`
  - `frames.bin`: `e4c63339fe0ac293a8279dec05bc5c0900c4eef03b6e727ca92076e6097aa925`
  - `rigs.bin`: `4eb76af977d0daf9d145c6b12e0ab789d66b9ae10b88691949a6ae69b6473f6f`
- Generation host: Windows x64, COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26
  without CUDA).
- The text sources intentionally use synthetic, non-physical parameter values
  (positive focal, near-zero distortion). They exist to exercise the parser's
  byte layout and model-id table, not to be a usable reconstruction.
