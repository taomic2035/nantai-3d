# GLB KTX2 mixed-material closure

Reporter: Codex, found while integrating Mesh Bundle v3 on 2026-07-20.

## Diagnostic capsule

| Field | Finding |
|---|---|
| Symptom | `rewrite_glb_for_ktx2` required every PNG image in one GLB to have a KTX2 replacement. Real H2 assets mix H3 hero slots with unchanged H2 slots, so a valid mixed profile could not be constructed. |
| Evidence | Accepted H2 bundle `866c4c1c...` contains mixed assets such as `house_thatch_01` (`dark-timber`, `rammed-earth`, `woven-bamboo`) and `stone_lamp_01` (`fieldstone`, `aged-metal`). Only the first two hero examples have H3 KTX2 replacements. |
| Root cause | The global requirement “replace the exact eight H3 hero slots” was incorrectly implemented as “replace the complete image closure of every GLB.” |
| Diagnostic strategy | Compare the accepted H2 material-slot distribution, Material Bundle v2 mixed profile, the rewrite loop, and the Khronos `KHR_texture_basisu` per-texture fallback contract. |
| Timeout strategy | If a conforming mixed per-texture representation were not supported, stop Mesh Bundle v3 and revise the approved design instead of weakening closure checks. |
| Warning strategy | A third incompatible fix attempt, geometry fingerprint drift, or a role-swapped texture passing validation means the abstraction must be redesigned. |
| User-visible correction | H3 assets can use 4K KTX2 on hero surfaces while retaining exact H2 PNG textures for other surfaces and non-KTX clients. |
| Acceptance | Mixed-subset rewrite test passes; unknown URI, unsafe URI, and swapped role remain rejected; geometry fingerprint remains identical; adjacent shared-texture and Mesh v2 suites remain green. |

## Reproduction

Expected: replace only the hero material images present in a mixed GLB.

Actual at commit `bcf2b19`: any replacement mapping smaller than the full PNG
image set raised `GLB image and KTX2 replacement closure disagree`.

## Root-cause analysis

Material Bundle v2 intentionally owns a mixed H3 profile: 24 KTX2 objects for
eight hero slots and 48 exact H2 PNG objects for the remaining slots. The GLB
rewrite imposed a stronger, incompatible per-file rule. Khronos defines
`KHR_texture_basisu` on each texture and explicitly permits retaining the core
PNG `source` as fallback while the extension points to a separate KTX2 image.

## Fix

Accept a non-empty exact subset of existing safe PNG URIs, append KTX2 image
objects, retain each core PNG source, and add the BasisU extension only to
selected textures. Keep role inference, URI safety, and geometry comparison
fail-closed. Do not declare the extension required because PNG fallbacks remain.

## Verification

Run `tests/test_glb_ktx2_variant.py`, adjacent shared-texture and Mesh Bundle v2
tests, Ruff, format, compileall, and diff hygiene before committing.
