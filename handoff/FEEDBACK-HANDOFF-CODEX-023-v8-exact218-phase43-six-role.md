# FEEDBACK-HANDOFF-CODEX-023 — v8 exact-218 / Phase 4.3 / six-role caller

> Date: 2026-07-22
> Owner: Codex
> Baseline: `main@dded695b341236da06bf15fb8c595c73b040964c`
> Result: exact-218 and Phase 4.3 pass; six-role production caller is **5 accepted / 1 rejected**.

## Trust boundary

Every artifact in this report remains synthetic L0, `preview-only`,
`modeled-unverified`, and `trust_effect=none` (quality reports use
`none-quality-filter-only`). Nothing here proves real-photo texture, metric
geometry, SfM/3DGS coverage, or arbitrary-coordinate 360-degree completeness.

## Runtime regression fixed

REVIEW-CODEX-021 separated camera-placement topology from module-attachment
topology. The Blender builder still named topology proxies from the camera
candidate ref, while the Phase 4.3 probe searched by the module attachment ref.
The three cross-path modules therefore fell back to non-mesh EMPTY roots.

Commit `dded695` makes the builder read attachment refs from the bound module
recipes. The camera refs remain unchanged. TDD reproduced the exact three-row
misbinding before the fix; focused verification is `190 passed`, Ruff clean.

## Fresh exact-218 identity

| field | value |
|---|---|
| build ID | `4b971ae6354025d57caff5d807e8c0716982ac9c651711b5237c7376a452a2b9` |
| build request SHA | `899ad9152dd3ccf75d226323f477e753bfe9a7b08772b7bac8fd7f0dd964213d` |
| build report SHA | `bec6973287437fdc44267cb111e2c6e47977764de5362bbed3b2e724ff92cb79` |
| blend SHA | `8779792b2d175b1aff6df1b20b48e71596eec6bd6ecf2da2884d177a5fb55b07` |
| blend bytes | `150363405` |
| reciprocal plan SHA | `5501cbb202e017870c345991cfc72c39a8be0bb6cab2d065874ed8dd457fb2e5` |
| runtime script SHA | `f4040c1835a1b444f504b4a12b5aa360a7d273ec9ac517f214e8aced9eae0885` |
| registry | exact instances `1..218` |

Private reproducible directory:

```text
.nantai-studio/synthetic-village/hybrid-v4/work/reciprocal-route-modules/
  4b971ae6354025d57caff5d807e8c0716982ac9c651711b5237c7376a452a2b9/
```

## Fresh Phase 4.3

| field | value |
|---|---|
| probe request SHA | `3b4fe8093aa363f88f917f6bf8c86ca8fb512a276830056d9506341e9819fcb6` |
| probe report SHA | `aed183c9deddb3adece5c6228c11bc2d31396041778563b6431de61c827d66a8` |
| probe script SHA | `4017110f45d90edfc5721a00fc1482b12a89093e3b485d4cd8bba570d23c7d23` |
| overall | `passed=true` |

The report has 6/6 route passes, 15/15 module-pair intersection passes,
6/6 topology-attachment passes, and 6/6 module-environment passes. Minimum
measured route width/height remain approximately `1.4m / 2.501m`; no threshold
was relaxed.

## Six-role caller outcome

Frozen policies are clearance `<2m / 5-of-15`, legacy valid-pixel floor `0.05`,
and Task-4 post-render v2 policy SHA
`b60eabd0c9cf069b23982bf2cfb9149ea25add8c6d76df39541d5642cf880b17`.

| role | target | result | render ID | frame report SHA | quality report SHA | RGB SHA |
|---|---|---|---|---|---|---|
| central-courtyard-downhill | 010 | accepted | `4de7ba8ec7d6a349ed16d342223ebdb68ae78f7ce63d603cb8910835cfc3de2b` | `4e4074f562d05a529c7e7ec8f45e24b1b589cfa6541c5e1584fd47c6eb362d3d` | `965e808d358d425b28a66b0850260c09dc43b2291d0274289bca74b6b52796b0` | `bbd446a2c8863c5c492c70671207098579a85cb8304ae4a7c5f31bf476592dc7` |
| bridge-deck-crossing | 039 | accepted | `0f3729b02e31a6803eeca3eec68bf52eeaea1729ef649c50666598c76d0f3284` | `8c0f2d24a9e1b62908ba9ca60442394cfb90c4554e955f373ff8dbec4f417293` | `53f7183d085016e83ada6a1ab6faa138c44b4c1b0311b4148f318f727775a4d9` | `085f5c50d8b610b6cd35f6054323414f08fadfffb990a65c9a124cfbaa37208d` |
| watermill-tailrace | 010 | accepted | `78a735d0fc21a1e54a4ec619bd854cdbc9586ddabe66c99340e7181235f5f9f4` | `5f02c9bcd954ed496536adfffb2dad6c4dab22ba824e35e4aa2743e91e3c3fd3` | `533eebb43bf2bdcf320c2bccfc2984049ad94fe14947d1250f4fa57337b031db` | `01224c390670e06cb3af91f5de453bc2ffaf4fbd82f5522bf2c463b3d7b947fe` |
| covered-gallery-underpass | 039 | accepted | `cc7f6cea8e0d26e18e5864cf70a736675bbe318aee7f29a809e56aed09e635c5` | `e96b3710fde25c12157ec40df46f66e239f503c9f9b8d5a0d6184143753921d0` | `a9eafc8e010a2eb49ae9b39c27d06dadc00649c3eacad69dbd3591f864a23873` | `c85b9c2d1b8210213fd4fa1448f8121366d25d307d4006a9c0f4e158ecdbf7ac` |
| forest-orchard-boundary | 010 | accepted | `ab5d3eefe05a23454a313b09b7c7954da45db89d581c7a2b37a273ff03c37d61` | `1ca1b3e6540fa0a9ac2f5cb8cdafc2b7056e332edb4a686b11b3c26513c5732e` | `532e030d1ebb281dac8a3334401f45b8cb226ec1d5da9fdef8659b881afa5b61` | `a4bf755cb053ef0ccbebc8ead1f5052f67b6c6da566f3073949713d8086203a8` |
| lower-valley-uphill | 039 | **rejected** | unpublished | `064224245e58dfea010f557bbb1317507b6286f7a5a848e9dbc458ff5b296c5c` | diagnostic recomputation only | `8ca6b40cccd71b1e5c115e7d1430021cf6cdd46bbfc6f29e61c9457156fd39f6` |

The five accepted directories were reloaded independently. Canonical request,
preflight report, render report, journal, quality report, and all six artifact
SHA/size records match the final bytes.

### Rejected lower-valley evidence

The runner correctly did not publish a final bundle. A diagnostic-only copy is
kept at:

```text
.nantai-studio/sv-prod-win/reciprocal-v8-rejected-diagnostics/
  lower-valley-5770395e68a74d22a8a67fc1186edacb/
```

Clearance passed with `0/25` upper/middle near hits. Required instances
`212..218` were all visible. Seven post-render rules passed; only
`upper-ground-dominance=0.355954 > 0.30` failed. Dominant upper instance `212`
(`lower-valley-entry-path-001`) alone owns `51,566` upper pixels. The rejected
frame remains diagnostic evidence and is not a quality report or acceptance.

## RGB review: caller pass is not visual-product acceptance

All six RGB frames visibly contain a large repeated dark rectangular tunnel.
The cause is `apply_reciprocal_route_modules.py::_module_geometry`: every part,
including open paths, is emitted as floor + ceiling + two walls and the whole
mesh inherits the part semantic. For lower valley, the artificial ceiling and
walls of a path object therefore count as upper-frame ground/path pixels.

This is a modeled-geometry defect, not a reason to weaken the policy or pitch
an unbound camera. The five machine-accepted frames prove caller plumbing and
fail-closed bindings only. They do not meet a real-scene visual bar.

## Caller candidate identity hardening

The verified-build handle now retains the exact six role camera candidates
from the content-addressed reciprocal runtime request. Before deriving a
camera plan or starting Blender, the caller requires the canonical six-role
ordering and compares the complete candidate SHA with the candidate embedded
in that verified build. A changed disclosure, pose, topology binding, or any
other candidate field therefore fails closed before a subprocess or publish.

TDD first reproduced the gap with a candidate that remained valid against the
source production plan but was absent from the verified build. The focused
caller/runtime/Blender contract suite is `104 passed`; Ruff is clean. This is
identity hardening only and adds no geometry or quality trust.

## Durable six-role batch and Studio projection

The caller now has a writer-locked, resumable six-role batch journal. Its batch
identity binds the verified build/report/blend, exact-218 registry, reciprocal
plan, source production plan, Blender executable, all three policies, the six
complete candidate SHAs, and the explicit `010/039` target assignment. A rerun
reuses only accepted entries; failed entries are retried and never acquire
quality-report fields unless the per-camera caller actually publishes them.

Studio can discover the latest journal below:

```text
.nantai-studio/sv-prod-win/reciprocal-production-batches/<batch>/
```

Accepted entries are not trusted from the batch row alone: Studio reopens the
camera journal, quality request/report and bound files, verifies canonical
bytes, every recorded SHA, build/plan/journal lineage and the v2 report before
showing per-rule PASS. A failed entry with no canonical quality report remains
visibly failed with no fabricated rules. Backend Studio contracts are
`107 passed, 9 skipped`; all `87` Studio JavaScript tests pass.

The earlier private `reciprocal-v8-six-role` evidence predates this batch
journal and was not silently migrated. Commit `e2fc0f1` adds the explicit
`render-reciprocal-production` CLI and a canonical runtime-request loader. Its
caller/runtime/batch regression suite is `91 passed`; Ruff and direct CLI help
are clean.

A fresh pre-mesh-fix baseline was then rendered through that CLI into:

```text
.nantai-studio/sv-prod-win/reciprocal-production-batches/v8-pre-mesh-fix/
```

| field | value |
|---|---|
| batch ID | `554443087078c2003b7342ec563b0391fabd85e8fedb084599b773cf8ed2d949` |
| journal self SHA | `e94c83658c450edd908f578abf9e81ca591593911a627211618e7e748476515a` |
| journal file / Studio evidence SHA | `984fcf0a01446bb0750680592e165bcb3c4f7c2312b6b529daf149bae0b68e18` |
| result | `5 accepted / 1 failed` |
| failed role | `lower-valley-uphill` / `post-render-quality-rejected` |

Independent journal loading and the Studio projection agree on all six role
states. Studio reports rendering completed and post-render quality rejected;
it does not fabricate a quality report for the failed role. This baseline is
machine-readable evidence of the known defect, not a waiver. The post-fix run
must use a fresh plan/build/batch identity.

## Next ownership

GLM receives the role-aware mesh task in
`handoff/HANDOFF-GLM-003-role-aware-reciprocal-meshes.md`. Codex retains the
caller. After a fresh mesh/plan/build return, Codex must rerun exact-218,
Phase 4.3, all six preflights/layers/visibility/post-render v2 checks, and RGB
review. Task 5 §3 and `req-5-pose-quality-fail-closed` remain blocked.
