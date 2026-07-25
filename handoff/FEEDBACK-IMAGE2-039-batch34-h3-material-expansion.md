# FEEDBACK-IMAGE2-039 — Batch34 H3 material expansion candidates

Date: 2026-07-25
Producer: Codex + OpenAI built-in imagegen
Consumer: GLM H3 material contracts/build; Codex rendered QA

## Delivery

Private candidate root:

```text
.nantai-studio/synthetic-village/hybrid-v4-candidates/batch34/
```

Batch34 contains four complete appearance-selection sets:

- 4 scene material slots;
- 3 candidates per slot;
- 12 RGB PNGs and 12 exact prompts;
- candidate 1 is an exact byte/prompt reuse from Batch33;
- candidates 2 and 3 are eight new built-in imagegen outputs.

Machine identities:

| Artifact | SHA-256 |
|---|---|
| `manifest.json` | `14c7cb87aa522245d50c007df1f603c1408b5a7d751809802fd12f0c8027bf25` |
| `PAYLOAD-SHA256SUMS.txt` | `7d418fa36525b1f34a4f859ff474d88cdf027b415428203c2b859f913552a2d8` |
| private contact sheet | `1d934d3d6509af365e0f03b119d657e52f815f4dc030020637cb9d570b8ba803` |
| private 2x2 repeat sheet | `473fb5a8ec30daf20abd5d5346740f94a472a2ed07a1cca4c17252dc5c93f33b` |

All 26 declared payload hashes pass. All 12 candidates are `1254 × 1254`
RGB and pass the already frozen `h3-ai-candidate-audit-policy-v1`
(`12/12`); this is only a source hygiene gate, not PBR or scene acceptance.

## Human visual selection

| Scene slot | Selected SHA | Reason |
|---|---|---|
| `material-creek-rock-01` | `17ac51105e9eaeb4c4d66f76bc3608f134314dd1b216dada9eadefbd8ca43d8a` | damp substrate balance and readable size hierarchy without baked water |
| `material-wet-stone-paving-01` | `6d1812da97c431f1305214ffb0f3c0f0ccb0d7a5dfa75489b6c85a8aebeec738` | dense irregular slate, restrained edge mismatch, no baked wet reflection |
| `material-aged-metal-01` | `9f166e27bdcd6380cdde12be20e2e2b4d95c603f45ddadef5fc8bce188a23454` | broad forge facets, restrained oxidation and no hardware motif |
| `material-pale-plaster-01` | `8e16a771606f1a1c43cab0b4777f7bff59751713e062c9dfa85065b247a81452` | neutral pale lime with fine trowel/straw detail and cleanest edge continuity |

Selection is `human-visual-review / trust_effect=none-appearance-only`.
The 2x2 sheet still shows boundaries and repetition, especially for creek and
paving. None is mathematically seamless or production PBR.

## Exact GLM task

Work only on new GLM-owned paths after the P7 transaction hold is cleared, or
while awaiting Codex review without touching transaction WIP:

1. Add an **additive** H3 source-pack extension schema for exactly the four
   existing scene slots above. Keep
   `nantai.h3-ai-material-source-pack.v1` and `H3_HERO_SLOTS` immutable.
2. Require exactly three unique candidates and one selected candidate per
   extension slot. Reuse the current candidate audit, safe relative-path,
   exact-byte/prompt SHA, atomic publication and reload verification behavior.
3. Model source authorization explicitly because candidate 1 came from an
   already public Batch33 Release while candidates 2/3 remain private. Do not
   falsify all records as `private-project-use-only`, and do not interpret
   publication as measured/real provenance.
4. Feed only the four selected source bytes into the existing
   `sha-quilt-seam-pbr-v1` 4096 authoring chain. Do not fork a second seam/PBR
   algorithm merely to consume this batch.
5. Preserve the current scene replacement contracts: slot id, UV policy,
   nominal tile metres, alpha mode, normal strength, roughness centre and
   metallic policy. A source change creates new source/material/bundle ids.
6. Fail closed on unknown/duplicate slots, wrong candidate count, duplicate
   bytes, selected SHA outside candidates, rights mismatch, path escape,
   source/prompt tamper, incomplete selected-source closure, noncanonical
   manifest or mixed old/new bundle identity.
7. Produce private authored-master, base/normal/ORM and KTX2/fallback bundles.
   Run current source preservation, exact seam, complete mip, KTX validation,
   decoded-quality and H2/H3 geometry-fingerprint gates.
8. Render 2x2 and 8x8 repetition plus neutral near/mid/far crops and six-role
   scene probes. Keep creek substrate below separate water geometry/shader;
   paving appearance never proves collision/clearance.
9. Return only content SHAs, machine reports and private RGB paths to Codex.
   Do not edit `web/data/`, register defaults, publish a Release or report
   `accepted:true` before Codex visual review.

Minimum tests:

```text
tests/test_h3_material_source_extensions.py
tests/test_h3_material_authoring.py
tests/test_material_bundle_v2.py
```

The new test file must be RED before implementation and cover all failures in
item 6. The existing H3 tests must remain green unchanged.

## Trust boundary

```text
synthetic=true
stage=material-source-only
real_photo_texture=false
pbr_channels=not-derived
tileability=not-verified
metric_scale=unknown
training_use=forbidden-as-multiview
coverage_use=forbidden
clearance_use=forbidden-as-evidence
trust_effect=none
```

Batch34 improves the synthetic mesh proxy's material-source coverage. It does
not provide real photographs, real imported texture/geometry, camera views,
SfM/3DGS evidence, 360-degree coverage, arbitrary-coordinate walkability,
measured alignment or real Viewer QA.
