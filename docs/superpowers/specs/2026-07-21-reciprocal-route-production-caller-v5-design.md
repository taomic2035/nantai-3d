# Reciprocal-route 218-root production caller v5 design

> Date: 2026-07-21
> Decision: additive scheme A, already approved for the Windows production lane
> Inputs: `HANDOFF-OPUS-008`, `HANDOFF-OPUS-009`, `REVIEW-CODEX-019`

## Goal

Render and quality-check a verified 218-root reciprocal-route Blender build without
changing, reinterpreting, or silently widening the existing 130-root v4 caller.
The first independently testable slice produces a real one-camera preflight and
six-layer frame bundle; full 180-camera journal and Studio projection follow as a
separate slice on the same contracts.

## Non-goals

- Do not promote `modeled-unverified`, `preview-only`, or `L0` trust.
- Do not claim the current simplified boxes satisfy route topology or visual quality.
- Do not make the v4 request accept 175 or 218 roots.
- Do not drop roots 131..218 to reuse the old renderer.
- Do not replace the existing v4 renderer script or mutate old journal bytes.

## Chosen architecture

Create an additive v5 host contract and two small Blender entrypoint wrappers. The
wrappers load the frozen v4 Blender implementation as an internal rendering engine,
but independently validate v5 identity, exact instances 1..218, the reciprocal build
scene property, and their own script SHA. The old script remains byte-for-byte
unchanged, so old render identities and cached evidence remain valid.

The v5 host module owns these responsibilities:

1. verify the reciprocal-route three-file build directory using the existing
   `load_reciprocal_route_build_report` and `verify_reciprocal_route_build_report`;
2. measure the final build-report file SHA, `.blend` SHA, Blender executable SHA,
   runtime script SHA, plan SHA, and transitive 175-root report SHA;
3. build exact-218 preflight and frame requests;
4. compute a render ID that binds the final report through `build_report_sha256` and
   the transitive 175-root report through
   `environment_module_build_report_sha256`;
5. invoke the dedicated v5 Blender scripts with immutable file snapshots;
6. verify canonical reports and all six artifact SHA values before publication.

## Versioned contracts

The first slice introduces:

```text
nantai.synthetic-village.reciprocal-production-clearance-request.v1
nantai.synthetic-village.reciprocal-production-clearance-report.v1
nantai.synthetic-village.local-production-render-frame-request.v5
nantai.synthetic-village.local-production-render-frame-report.v4
nantai.synthetic-village.local-production-camera-metadata.v4
```

`LocalProductionRenderFrameRequestV5` carries the same production plan, camera,
settings, policy and registries as v4, with these differences:

```text
build_adapter = windows-reciprocal-route-v1
object_registry = exact instances 1..218
build_id = reciprocal-route build_id
build_report_sha256 = measured reciprocal-route report-file SHA
blend_sha256 = measured village-reciprocal-route.blend SHA
environment_module_build_report_sha256 = report.base_build_report_sha256
```

The final reciprocal-route report already binds the plan SHA, runtime script SHA,
base blend SHA and full registry. The host verifier checks that transitive chain
before constructing v5 requests; no identity is inferred from paths or filenames.

## Blender wrapper boundary

The new scripts are:

```text
scripts/blender/preflight_reciprocal_route_cameras.py
scripts/blender/render_reciprocal_route_production.py
```

Each wrapper:

- hashes its own bytes and rejects a request bound to another script;
- requires the pinned Blender 4.5.11 Windows executable;
- validates exact root IDs, instance IDs and semantic/material registry values;
- parses `bpy.context.scene["nv_reciprocal_route_module_build"]` and requires its
  `build_id` and plan SHA to match the verified host request;
- temporarily adapts only internal calls to the frozen rendering engine;
- never rewrites scene provenance or truncates the registry;
- writes a new schema version and canonical JSON.

The wrapper may reuse the v4 pixel/render implementation, because the RGB/depth/
normal/instance/semantic encodings are unchanged and uint16 instance PNGs safely
represent IDs through 218. It must not reuse the v4 object-count validator or scene
adapter decision.

## Data flow

```text
verified 218-root directory
  -> verify request/report/blend identities
  -> build exact-218 clearance request
  -> fresh Blender ray preflight
  -> reject or build v5 frame request
  -> render RGB/depth/normal/instance/semantic/camera metadata
  -> verify v4 frame report + six artifact SHA values
  -> build ProductionFrameQualityRequestV2/ReportV2
  -> publish one immutable content-addressed frame directory
```

The one-camera slice uses an explicitly selected existing production camera. It is
plumbing evidence only; it does not prove the six reciprocal-route roles have camera
coverage. Opus must still provide new topology-bound standing-eye cameras.

## Failure behavior

The caller fails closed when any of these occur:

- registry is not exactly 1..218 or stable IDs are duplicated;
- build request/report/blend/runtime/executable SHA values disagree;
- the embedded 175-root report SHA or reciprocal plan SHA disagrees;
- Blender scene properties do not bind the final build ID;
- preflight rejects the selected camera;
- Blender exits nonzero, times out, or changes an immutable input;
- output layout differs from the exact request/report/artifact set;
- camera pose, layer statistics, artifact SHA, or v2 quality decision disagrees.

Every failure leaves the build previewable but produces no accepted production
quality result. Private staging directories are removed only after path verification.

## Compatibility

No existing schema constant, class, runtime script, report, journal or render root is
modified. The v5 adapter uses a separate content-addressed root. Existing v4 tests
must prove unchanged request serialization and rejection of 218-root registries.

## Verification

The first slice is accepted only with all of the following fresh evidence:

1. unit tests for exact identities, tampering, 130/175/217/219-root rejection and
   deterministic request bytes;
2. direct Python loading of both Blender wrappers with a fake `bpy` module;
3. adjacent v4 production tests still green;
4. ruff and `git diff --check` clean;
5. real pinned-Blender preflight and one-camera six-layer render from a freshly built
   Phase 4.1 reciprocal-route scene;
6. measured request/report/artifact SHA values recorded in a Codex handoff.

## Follow-up slice

After the one-camera caller proves the contract, extend it to resumable 180-camera
journal execution and add Studio/ledger/HUD projection. That slice must reuse the
v5 evidence instead of defining another quality decision path.
