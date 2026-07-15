# Studio B2 readiness audit

Date: 2026-07-15

Scope: current `main` after the B1 ingest job kernel. This is a read-only
architecture/UX readiness audit for the next end-state-aligned increment:
immutable capture and scene revisions plus transactional reconstruction. It is
not an implementation specification and does not approve new write capability.

## Executive conclusion

B1 supplies a credible single-writer ledger, fenced process runner, publication
journal, recovery path, and secure loopback API. Those are reusable foundations.
The current project does **not** yet have a scene revision model, immutable
revision storage, transactional reconstruction publication, process-tree
cancellation, or a Studio surface for revision lineage. Connecting the existing
reconstruction CLI directly to the B1 job endpoint would therefore create
mutable, mixed-generation scene state and would work against the infinite-scene
objective.

The next technical/experience boundary should be B2 `CaptureRevision +
SceneRevision + transactional reconstruct`. Each revision must publish under an
immutable revision-owned location; a separately fenced compare-and-swap active
pointer selects the visible revision. The first implementation may still publish
one whole reconstruction artifact, but its contract must already model a list of
spatial artifacts so later chunking is an additive change rather than a schema
replacement.

## Evidence matrix

| Requirement | Current evidence | Verdict |
|---|---|---|
| Immutable capture revision | B1 replaces one mutable formal `photos/` target and removes the previous formal payload. The run stores an ingest session ID, but no immutable capture payload store, `CaptureRevision`, or revision pointer exists. | Missing |
| Immutable scene revision | No `SceneRevision`, `parent_revision`, capture-revision list, or scene-revision collection appears in pipeline, API, schema, or tests. | Missing |
| Revision lineage | Reconstruction manifest records coordinate transform ancestry and input artifact hashes, which is useful trust evidence, but it does not identify parent/child scene revisions. | Partial, not equivalent |
| Extensible spatial artifacts | Reconstruction manifest has one full 3DGS descriptor and LOD descriptors. Studio schema exposes one `reconstruction.artifact`; neither contract exposes a revision-owned spatial artifact list. | Missing end-state boundary |
| Staging-only reconstruction | `reconstruct()` accepts configurable output directories, but creates and writes them immediately. Its CLI defaults to `recon/` and `web/data/recon/`; registration defaults its COLMAP workspace to `recon/colmap_ws`. | Unsafe until registry fixes every path inside run staging |
| Atomic revision publication and activation | The ledger can journal multiple publication targets, but the B1 publisher and recovery path are hard-gated to `photos/`. There is no immutable scene-revision store, committed-candidate state, or fenced/CAS active-revision pointer. Fixed `recon/` and `web/data/recon/` paths cannot themselves be revision identity. | Foundation only |
| Stable old scene during work/failure | Studio reads the live `web/data/recon/recon_manifest.json` and its referenced bytes. Direct reconstruction overwrites those locations and there is no active-revision pointer. | Not guaranteed |
| Fixed reconstruct command | `CommandRegistry` explicitly accepts only `ingest`; server advertises only ingest as enabled. | Correctly fail-closed |
| Cancel process tree | SQLite schema and UI vocabulary reserve `canceled`, and UI action derivation is capability-gated. The local ledger state machine has no legal canceled transition; JobService explicitly has no cancel or Windows Job Object/process-tree ownership. | Reserved schema only |
| Retry without rewriting history | SQLite schema reserves `retry_of`, but local `RunRecord`, row mapping, `create_run`, and API do not read or write it. Browser mock retry is not SQLite lineage evidence. | Reserved schema only |
| Retention and crash-safe GC | Neither capture nor scene payloads have pin-active, candidate, ancestor, reference-count, capacity, or GC-eligibility rules. | Missing |
| Freshness/TOCTOU fencing | Ingest double-hashes its fixed source and target. There is no reconstruct snapshot covering capture revision, import descriptor, base scene revision, registration evidence, and both formal target generations. | Missing |
| Trust preservation | Reconstruction already records requested/actual engines, synthetic status, coordinate contract, transform catalog, applied transform paths, fidelity, and content hashes. | Strong reusable evidence |
| Studio revision UX | Studio shows step freshness/trust, active-run state, engine and artifact evidence. It has no scene/capture revision identity, lineage, compare/switch, or “old scene remains active” state. | Missing |

## Authoritative code evidence

- `pipeline/studio_jobs.py:740-795` defines an ingest-only registry with fixed
  `input/`, run staging, and formal `photos/` paths.
- `pipeline/studio_jobs.py:1001-1021` and `:1308-1324` restrict publication and
  recovery to the B1 `photos/` target despite the generic journal table shape.
- `pipeline/studio_jobs.py:1951` explicitly states that B1 has no cancellation.
- `pipeline/studio_ledger.py:155-172` reserves `canceled` and `retry_of` columns
  beside process identity, lease generation, and artifact IDs; the public ledger
  API/state machine does not yet implement cancel or retry lineage.
- `pipeline/studio_ledger.py:226-231` can represent multiple ordered publication
  targets, which is a useful B2 primitive but not proof of multi-target recovery.
- `pipeline/reconstruct.py:500-535` creates output locations and writes
  registration evidence directly to the caller-selected directory.
- `pipeline/reconstruct.py:666-670` writes the audit PLY and copies the full
  artifact into the web output before the manifest is complete.
- `pipeline/reconstruct.py:736-842` builds strong coordinate/transform/artifact
  provenance, then writes one mutable `recon_manifest.json`.
- `pipeline/registration.py:478-516` runs COLMAP in a caller-provided workspace;
  `pipeline/registration.py:619-628` defaults that workspace to
  `recon/colmap_ws`.
- `pipeline/studio_server.py:554-723` reduces the live web manifest to a single
  current reconstruction artifact; `:1011-1053` resolves and invokes that read.
- `docs/contracts/studio-adapter-v2.schema.json:38-82` models a single
  reconstruction artifact and contains no revision collection or lineage.
- `pipeline/studio_server.py:97-109` advertises only ingest and keeps cancel/retry
  disabled; `web/studio/job-actions.mjs:143-147` correctly hides those actions
  unless the server advertises them.

## UX consequences

The next Studio change should not start with a larger reconstruct form. Users
first need a stable answer to four questions:

1. Which capture revision is this scene based on?
2. Which scene revision is currently being viewed and which remains active?
3. Is a newer revision building, stale, failed, or safe to activate?
4. Which engine, coordinate evidence, transform lineage, and renderer fidelity
   justify the trust label?

The UI must distinguish an active revision from a committed-but-not-activated
candidate. A crash after revision commit but before activation leaves the
candidate available for inspection while the active pointer remains unchanged.

Only after those facts exist should Studio expose capability-gated cancel,
schema-approved retry, and fixed `mock`/`import` reconstruction confirmation.
The successful terminal transition should offer revision comparison and explicit
activation; a failed or canceled run must leave the old active scene visibly
unchanged.

## Required B2 acceptance evidence

- Two consecutive capture/reconstruct cycles create immutable revision IDs,
  preserve the parent chain, and retain old bytes under revision-owned locations
  such as `.nantai-studio/artifacts/capture/<revision-id>/` and
  `.nantai-studio/artifacts/scene/<revision-id>/`.
- A fake COLMAP executable proves no write occurs outside the run workspace.
- Reconstruct snapshots include capture manifest, import descriptor, base scene,
  registration evidence, and current target generation; any change yields a
  stable pre-publication stale error.
- Revision bytes commit before activation. A separate fenced/CAS active-revision
  pointer selects one committed revision. Real kills at every publication and
  activation edge followed by a fresh process reveal either the old active
  revision or the complete new active revision, never a mixed generation; a
  committed but unactivated candidate may remain for later inspection.
- Compatibility paths such as `recon/` and `web/data/recon/` are projections or
  pointer views of the active revision, never revision identity or mutable source
  truth.
- Retention rules pin the active revision, candidates referenced by live runs,
  and required ancestors. Capacity limits, reference accounting, GC eligibility,
  and crash-safe collection prevent both dangling revisions and unbounded disk
  growth.
- Windows Job Object tests prove cancel and parent death leave no child process;
  the writer slot becomes recoverable after a fresh startup.
- Mock/import manifest evidence is machine-checked along separate axes: geometry
  provenance/usability, artifact attribute fidelity, and renderer runtime
  fidelity. Synthetic geometry cannot become measured or metric, but a synthetic
  artifact may honestly be full-3DGS when its SH, opacity, scale, rotation, and
  descriptors prove that file fidelity.
- During a running, failed, or canceled new revision, Viewer continues loading
  the previous active revision by immutable URL and verified hash.
- Studio tests cover revision identity, lineage, freshness, active-vs-candidate
  distinction, cancel/retry capability gating, failure preservation, and
  explicit activation after success.

## Deliberate non-scope for B2

B2 should not claim or expose a built-in GPU trainer, arbitrary paths or shell
commands, world/asset mutation, distributed execution, or one-click “infinite
world” generation. It advances the infinite-scene objective by making scene
state immutable, appendable, spatially extensible, and recoverable; it does not
pretend the final spatial streaming system already exists.
