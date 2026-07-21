# H3 authored 4K material verification

Date: 2026-07-21  
Scope: H3-A eight-slot deterministic 4096 authoring and derived PBR maps

## Result

The verified H3 source pack was authored into one private, content-addressed
pack:

```text
source pack:
  be92da7d0c2d1956b7775d3422b914f32e5dc29bb233e394c1f33e346eff26b4
authored pack:
  b27eb142bc23c79c5cdd52bc8215604634f115ab12c2f9240fb98ecfc9af1789
manifest SHA-256:
  b39be627307acae14eb0f30dec37b29b9bf3dc43d49b814cda86db5acb0cd7cd
private file closure:
  25 files = manifest + 8 x (base/master object + normal + ORM)
```

Every map is RGB8 PNG at 4096 x 4096. Base colour is declared sRGB; normal
and ORM are declared linear data maps. The master and base-colour descriptors
share the same content-addressed bytes. All eight opposite-edge seam scores
are exactly `0.0`.

## Rejected intermediates

Two earlier private packs remain rejected and are not Release inputs:

| Pack ID | Rejection reason |
|---|---|
| `9a882895b20dfb7c7c017a32e4fc958e72adfaafdb7b703cdb0f91c373db4a4c` | The 1254 source pixels were copied at one-to-one pixel scale into a 4096 canvas, shrinking material features by about 3.27x. |
| `5466d689d6a0fa27a028e729873b6254709b06ea8eff01bf77a41e2ec7c7d98a` | Feature scale was corrected, but 4096-square `float32` channel-mean accumulation drifted by up to about 16.4 RGB levels and introduced visible green/yellow colour casts. |

The accepted implementation first scales the selected source to the authored
physical tile extent, then accumulates low-frequency RGB means in `float64`.
The manifest now freezes three source-preservation gates:

```text
full source SSIM       >= 0.90
interior source SSIM   >= 0.94
mean RGB delta         <= 0.01
```

## Per-slot evidence

| Slot | Full SSIM | Interior SSIM | Mean RGB delta | Seam |
|---|---:|---:|---:|---:|
| `material-weathered-timber-01` | 0.98801177 | 0.99780010 | 0.00026649 | 0.0 |
| `material-dark-timber-01` | 0.99168298 | 0.99782849 | 0.00032812 | 0.0 |
| `material-gray-roof-tile-01` | 0.99025824 | 0.99829048 | 0.00038491 | 0.0 |
| `material-fieldstone-01` | 0.98641313 | 0.99897982 | 0.00029493 | 0.0 |
| `material-dry-stone-wall-01` | 0.98359577 | 0.99882287 | 0.00050341 | 0.0 |
| `material-rammed-earth-01` | 0.98944094 | 0.99778776 | 0.00008394 | 0.0 |
| `material-packed-earth-01` | 0.98817161 | 0.99820983 | 0.00065189 | 0.0 |
| `material-terrace-soil-01` | 0.98692886 | 0.99912686 | 0.00016001 | 0.0 |

The fixed private source/master/normal/ORM comparison sheet is:

```text
.nantai-studio/h3/authored-contact-sheet-v3.png
SHA-256: 8387aaf82d3d6fe8dfaf6dfa9d6746bebbba3528de10a387b725762df34d29e1
dimensions: 1866 x 3672
```

Human inspection confirmed that roof, timber, stone, rammed earth, packed
earth, and terrace-soil feature scales remain readable at the same physical
tile scale as their selected sources. The visible colour casts from the second
intermediate are absent. This is an appearance review, not geometry, camera,
coverage, metric, or reconstruction evidence.

## Fresh gates

```text
pytest:
  38 passed, 2 skipped in 140.50s
ruff:
  all checks passed
compileall:
  passed
git diff --check:
  passed
real private build:
  8 records, 25-file closed directory, loader and SHA verification passed
```

The two skipped tests are existing environment-dependent cases in the focused
suite; they are not counted as authored-material evidence.

## Truth and publication boundary

These maps remain:

```text
synthetic=true
ai_generated=true
real_photo_textures=false
geometry_usability=preview-only
metric_alignment=false
verification_level=L0
material_measurement=none
```

The source rights receipt is still `private-project-use-only`; therefore the
PNG pack and contact sheet stay below `.nantai-studio` and are not uploaded to
Release. A public H3 Release must wait for an explicitly public-authorized
source receipt, verified KTX2 outputs, Blender contact renders, and Viewer
fallback/rollback evidence.

## Next gate

Consume the accepted authored pack through the pinned KTX2 toolchain. Verify
container identity, colour-transfer metadata, decoded base-colour SSIM,
normal cosine similarity, ORM channel error, mip completeness, then run the
Blender H2/H3 contact render and Viewer material-profile rollback tests before
changing any default material profile.
