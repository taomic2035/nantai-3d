# COLMAP real-binary fixtures

These fixtures are **not** produced by the production parser's sibling helpers
(`_write_fake_cameras_bin` / `_write_fake_images_bin`). They are produced by
the pinned local COLMAP 4.1.0 (`Commit fa8e3b3 on 2026-06-26 without CUDA`,
`third/colmap/bin/colmap.exe`) from independently authored text sources, so a
parser that agrees with itself but disagrees with the official binary format
will fail these tests.

## Sources

- `cameras_all_models.txt` — hand-written from the COLMAP 4.1.0
  `BaseCameraModel::CameraName` list and the parameter counts Codex measured
  with `model_converter`. Covers all 12 accepted model ids 0..11.
- `images_minimal.txt` — hand-written 3-image record list bound to the
  cameras above.

## Regeneration

```powershell
# From repo root:
$colmap = "third\colmap\bin\colmap.exe"
& $colmap model_converter \
  --input_path tests\fixtures\colmap\text \
  --output_path tests\fixtures\colmap\bin \
  --output_type BIN
```

The committed `.bin` files are the byte-exact output of that command. Do not
hand-edit them; regenerate from the text sources if the format changes.

## Provenance

- COLMAP binary SHA-256 (this executable):
  `third\colmap\bin\colmap.exe` measured at fixture-generation time.
- Generation host: Windows x64, COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26
  without CUDA).
- The text sources intentionally use synthetic, non-physical parameter values
  (positive focal, near-zero distortion). They exist to exercise the parser's
  byte layout and model-id table, not to be a usable reconstruction.
