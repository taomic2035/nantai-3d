# REVIEW-CODEX-007 — Windows 180-camera production render gap

> Reviewer: Codex (UX / audit / production evidence lane)
> Date: 2026-07-20
> Target: `synthetic-village-coverage-180-v1` local production rendering

## Verdict

The production camera plan is now genuinely complete at `180/180`, but the
verified Windows L2 Blender build cannot yet enter the 180-frame production
runner.

This is not a Blender installation failure and not an incomplete camera plan:

- `plan-production --batch-count 6` reports `complete: true`, six stable
  30-camera batches, 48 elevated-pedestrian cameras, and two ground-connected
  route loops;
- `third/blender/blender.exe --version` reports Blender `4.5.11 LTS`;
- the current Windows build is content-addressed and binds a
  `windows-x64` `ToolIdentity`;
- `render-production-local` is intentionally coupled to the different macOS
  Apple Silicon local-preview build contract.

Removing the platform check would be unsafe. The two build contracts have
different tool-identity models and different required build-directory entries.

## Reproduction

Inputs:

```text
build:
.nantai-studio/synthetic-village/hybrid-v3/work/canary/
  4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc

material bundle:
.nantai-studio/synthetic-village/hybrid-v3/material-bundles/
  88e35afe5ed57b7d0187956d601b1470662aaf964f593a2fc08c543c7da2e2a3
```

Focused request:

```powershell
python scripts/synthetic_village.py render-production-local `
  --build-directory <build> `
  --material-bundle-root <bundle> `
  --camera camera-ground-route-001 `
  --min-valid-pixel-ratio 0.75 `
  --timeout-seconds 900
```

Observed failure before Blender invocation:

```text
pipeline.synthetic_village.local_textured_preview.LocalTexturedPreviewError:
local textured preview requires macOS Apple Silicon
```

No frame artifacts or production journal were published.

## Root cause

The call path is:

```text
render-production-local
  -> run_local_production_render()
  -> probe_local_blender_identity()
  -> macOS Apple Silicon contract gate
```

The gate is not the whole difference:

1. `LocalBlenderIdentity` only permits:
   `platform=macos-arm64`, Blender `4.5.11`,
   runtime build hash `4db51e9d1e1e`.
2. The runner reconstructs a `LocalTexturedPreviewRequest` and verifies a
   local-training build directory with `LOCAL_TRAINING_BUILD_ENTRIES`.
3. That directory must include `glb-material-audit.json` and `manifest.json`.
4. The Windows build instead carries
   `nantai.synthetic-village.blender-build-report.v2` with the locked
   `windows-x64` canary `ToolIdentity`.

Therefore changing only the platform predicate would make the next identity or
directory-contract check fail. Weakening those checks would allow one build
type to masquerade as another.

## Current immutable Windows evidence

```text
build id:
4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc

build-report sha256:
aaf3a6b9fb6f48b3336e55f44f203504d58782a95a2738d70ee773464471e065

blend sha256:
fa8cc4aabfe5049f2025e9d2ab34739c0914d87aa78a8fbda21ad86299cbebac

GLB sha256:
cdd5d998ced5c601d322dbf73460de22539c46d23017c76e0fc889c0e21c46e8

Blender executable sha256:
0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255

camera registry sha256:
9c8ad9b2bf299d51385822a2b40f071781d0c07e42aae6e1216887adb2563726

elevated topology sha256:
1eabf220e2d9e2a91c2371d3587d2f612b17d164765c0ed86059cc2ac8ddaf43
```

The `.blend` is `149,052,178` bytes and the GLB is `140,064,092` bytes.

## Required implementation boundary

Opus should choose one explicit contract rather than generalizing the Mac
preview model by omission:

1. add a Windows production runner that consumes the existing locked canary
   `ToolIdentity` and `BlenderBuildReportV2`; or
2. define a platform-neutral production-build envelope that explicitly
   discriminates the Windows canary and Mac local-preview source contracts.

The first option is the smaller, safer path for the current machine.

The Windows path must:

- accept the Blender executable explicitly or use the verified
  `third/blender/blender.exe`;
- remeasure and bind executable, build report, `.blend`, renderer script,
  production plan, camera registry, topology, object registry, material bundle,
  and visual-source identities;
- preserve the existing six-file frame contract and durable 180-frame journal;
- preserve operator-selected valid-pixel rejection, failure/timeout states,
  bounded camera subsets, and resume behavior;
- never convert Windows runtime availability into geometry-trust elevation;
- reject cross-contract substitution rather than rewriting or re-registering
  evidence.

## Acceptance evidence

At minimum:

1. a Windows fixture proves a matching canary report and executable are
   accepted;
2. platform, executable, build-report, `.blend`, material-bundle, plan, and
   renderer-script mutations each fail closed;
3. `camera-ground-route-001` produces all six verified files and one measured
   valid-pixel decision;
4. rerunning the same camera reuses verified bytes without a second render;
5. a deliberately high valid-pixel threshold retains evidence but marks the
   frame rejected;
6. the same command remains in `.nantai-studio/`, and the report continues to
   declare synthetic L0 rendering with
   `simplified-pbr-not-render-parity`.

## Separate remaining production requirements

This platform gap does not close:

- `req-3-front-back-facade-coverage`: object registry still lacks component
  orientation, so front/reverse facade identity cannot be named;
- the remainder of `req-5`: no defensible near-duplicate threshold, isolated
  camera detector, or sky/ground semantic bad-frame detector exists.

The plan's `req-5` explanation has been corrected separately so it no longer
claims that the already implemented Mac runner and valid-pixel gate do not
exist.
