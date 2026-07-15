# Nantai Studio B2 immutable reconstruction revisions

Date: 2026-07-15
Status: user-approved design, awaiting written-spec review before implementation planning

## 1. Decision summary

B2 introduces immutable `CaptureRevision` and `SceneRevision` bundles, an
append-only evidence graph, and an independently fenced compare-and-swap active
scene pointer. Reconstruction creates a committed candidate first. It never
overwrites the scene currently visible in Viewer. A user compares the candidate
with the active scene and explicitly activates it with the generation they
observed.

The selected implementation is a local control plane:

- Python 3.11 production baseline with Pydantic contracts;
- standard-library SQLite in WAL mode for metadata, lineage, leases, and the
  active pointer;
- immutable bundles on local NTFS, published with the existing B1 Win32
  durability and recovery primitives;
- CPU COLMAP locally, with one authoritative SfM bundle;
- external Linux/CUDA Nerfstudio Splatfacto training, resolved gsplat version,
  and a complete returned training capsule;
- Three.js 0.180.0 plus Spark 2.1.0 for the local Viewer;
- native ES modules for Studio and Viewer, with locally locked runtime assets.

B2 is not a machine-learning platform. It packages inputs, imports externally
computed results, verifies evidence, publishes immutable candidates, and lets a
user activate or reactivate verified revisions safely.

## 2. Goals

1. Preserve every committed capture and scene revision until an explicit,
   crash-safe retention policy makes it collectible.
2. Keep the old active scene usable throughout reconstruction, import,
   validation, failure, cancellation, and restart.
3. Make source images, SfM coordinates, cloud training, transforms, and exported
   Gaussian artifacts machine-traceable by immutable identifiers and hashes.
4. Let the first scene contain one whole-scene artifact while exposing a
   `spatial_artifacts[]` contract that can grow into chunked infinite-scene
   streaming without a schema replacement.
5. Keep real inputs, GPS, original names, PLY files, databases, logs, training
   packages, and cloud details private and outside public Git.
6. Distinguish synthetic workflow validation from real local reconstruction,
   real cloud training, and real target-device rendering.

## 3. Non-goals

B2 does not add a built-in CUDA trainer, cloud-provider account management,
payment automation, arbitrary SSH or shell commands, distributed scheduling,
PostgreSQL, Redis, Celery, object storage, or a full blob CAS. It does not rewrite
Studio in React/Vue, migrate Viewer to Unity/Unreal, or promise that unseen areas
can be reconstructed accurately.

Cancellation and retry may be exposed only where their B1/B2 process and lineage
contracts are complete. No UI control may advertise a capability that the
server has not negotiated.

## 4. Truth model and terminology

### 4.1 Immutable objects

An immutable revision is never edited in place. Its manifest contains its own
schema version and the SHA-256 of every referenced payload. A revision ID is an
opaque server-generated ID; `manifest_digest` is the content identity. Keeping
the two separate avoids treating a truncated hash as a security boundary and
permits two intentional revision records with identical bytes.

The primary immutable records are:

- `CaptureRevision`: selected images or extracted frames plus sanitized source
  descriptors, ingest parameters, and provenance;
- `SfmBundle`: the authoritative camera/image IDs, database or sparse model,
  coordinate frame, hashes, and COLMAP contract evidence;
- `TrainingHandoff`: a locally generated package and instructions suitable for
  external upload; it contains no account credentials;
- `ImportDescriptor`: a content-addressed description of returned cloud files
  in the private import inbox;
- `SceneRevision`: lineage, coordinate and transform evidence, validation
  summary, and one or more `SpatialArtifact` descriptors;
- `TrainingCapsule`: the external environment, inputs, commands, configuration,
  checkpoint, export, and returned hashes.

### 4.2 Lifecycle projection

The UI may present `staging -> committed -> verified -> candidate -> active`,
but these are not mutable states stored inside the immutable manifest:

- `staging` is a run workspace, not a revision;
- `committed` means the bundle and its database record were durably published;
- `verified` means an append-only verification record passed required gates;
- `candidate` means committed and verified but not selected by the active
  pointer;
- `active` is derived solely from the separate active pointer.

A later failed verification does not rewrite a manifest. It appends evidence and
changes the current eligibility projection.

## 5. Private storage layout

All runtime data lives under the Git-ignored `.nantai-studio/` root:

```text
.nantai-studio/
  studio.db
  artifacts/
    capture/<capture-revision-id>/
    sfm/<sfm-bundle-id>/
    handoff/<training-handoff-id>/
    import/<import-descriptor-id>/
    scene/<scene-revision-id>/
  inbox/
  work/<run-id>/
  quarantine/
  tombstones/
  logs/
  cache/
```

Each artifact directory contains `manifest.json` and its revision-owned payload.
Published directories are never used as mutable workspaces. Compatibility paths
such as `recon/` or `web/data/recon/` may be read-only projections of the active
revision, but are never revision identity or source truth.

The Git ignore contract must cover the complete `.nantai-studio/` root before
the first B2 runtime write. Existing ignores for `input/`, `photos/`, PLY files,
weights, logs, and generated outputs remain defense in depth.

## 6. SQLite v2 model

SQLite remains the only metadata and active-pointer truth. Payload bytes remain
in immutable NTFS bundles. B2 adds explicit v1-to-v2 migration and refuses an
unknown newer schema.

Required logical tables are:

| Table | Purpose |
|---|---|
| `capture_revisions` | committed capture identity, manifest digest, timestamps |
| `sfm_bundles` | authoritative SfM identity, capture ID, frame and evidence digest |
| `training_handoffs` | package identity, capture/SfM references and digest |
| `import_descriptors` | private inbox import identity and returned-file hashes |
| `scene_revisions` | parent, requested/actual engine, manifest digest and commit facts |
| `scene_inputs` | ordered capture, SfM, import, alignment, and base-revision references |
| `spatial_artifacts` | artifact ID, scene ID, bounds, LOD, format, bytes and hash |
| `verification_records` | append-only gate results and verifier/toolchain identity |
| `active_scene` | project ID, scene revision ID, generation and update event |
| `revision_pins` | explicit retention pins with reason |
| `revision_leases` | live compare/verify/run leases with expiry and owner |
| `publication_intents` | crash recovery facts for bundle publication |
| `gc_plans` | immutable mark set, observed generation and lifecycle status |

Foreign keys are enabled. Short mutation transactions use `BEGIN IMMEDIATE`.
WAL permits observers, while critical publication and activation use
`synchronous=FULL` and the B1 cross-process fencing rules. Startup verifies local
NTFS and Win32 write-through support; an inconclusive durability check makes
write capability fail closed while read-only inspection stays available.

## 7. CaptureRevision and the authoritative SfM boundary

Ingest publishes a CaptureRevision rather than replacing a single mutable
`photos/` target. Its manifest fixes:

- the ordered selected images or frames;
- sanitized logical names and source hashes;
- extraction, deduplication, blur, resize, orientation, and timestamp policy;
- synthetic/real provenance and known tool versions;
- private EXIF/GPS presence flags without exposing private values through API;
- the canonical image hash tree consumed downstream.

The real end-to-end path must recognize exactly one authoritative SfM bundle.
Running COLMAP locally and then allowing a cloud `ns-process-data` step to run a
different COLMAP model does not produce interchangeable frames, even with the
same images.

B2 supports two honest paths:

1. local COLMAP creates the authoritative `SfmBundle`, and cloud training
   consumes that exact model; or
2. cloud processing creates the authoritative model and returns its actual
   COLMAP model, `transforms.json`, image/camera mapping, configuration, and
   hashes before alignment is derived.

The `SceneRevision.source_frame` and every registration transform must be
derived from the selected authoritative bundle. Matching filenames or a shared
label such as `sfm-local` is never evidence of coordinate equivalence.

## 8. External training handoff and import

`TrainingHandoff` creates a deterministic, checksummed package from a fixed
CaptureRevision and SfmBundle. It does not upload, create an account, choose a
paid instance, or persist secrets. Provider-specific automation is outside B2;
a future narrow `CloudRunner` may expose only upload, status, cancel, and
download operations.

The returned training capsule must include at least:

- CaptureRevision and SfmBundle digests;
- exact argv, preset, seed, iteration count, image scale and camera options;
- OCI image digest and resolved package inventory;
- GPU, driver, CUDA, Python, PyTorch, Nerfstudio and gsplat identities;
- checkpoint and export hashes;
- exported Gaussian PLY hash;
- the actual coordinate model and mapping used during training.

Returned files first enter a fixed private inbox. The server imports them by
predeclared artifact kind and hash into an immutable `ImportDescriptor`; HTTP
never accepts an arbitrary local path. A missing, changing, malformed, or
unmapped file fails closed.

`normalize_ply_quats.py` and similar conversions may not modify a formal PLY in
place. They run in staging, create a new artifact, and record input hash, output
hash, transform ID, argv, and tool version.

## 9. SceneRevision creation and publication

The fixed reconstruct request consumes IDs, not paths:

- one or more CaptureRevision IDs;
- one authoritative SfmBundle ID;
- one ImportDescriptor ID for `engine=import`, or synthetic fixture identity for
  `engine=mock`;
- alignment evidence ID;
- optional parent/base SceneRevision ID;
- a bounded reconstruction preset.

The idempotency digest includes every input manifest digest, authoritative SfM
digest, toolchain capsule digest, preset, transform chain, and parent revision.

Publication follows this order:

1. validate IDs and take a command-specific concurrency snapshot;
2. reserve a revision ID and durable publication intent;
3. build every payload under `.nantai-studio/work/<run-id>/`;
4. produce canonical manifests and hash all bytes;
5. run format, coordinate, bounds, Gaussian, LOD and Viewer-load gates;
6. under the publication fence, revalidate inputs and target absence;
7. publish the complete directory to its absent revision-owned destination with
   the existing write-through/flush semantics;
8. commit the revision rows and publication result in a short SQLite
   transaction;
9. expose it as a verified candidate without changing the active pointer.

A crash between steps is recovered from the intent, manifest and hashes. A
complete, verified absent-to-present publish may roll forward. Anything that
cannot be proven complete is quarantined. Recovery never infers success from a
directory name or a process exit code.

The first SceneRevision may contain one whole reconstruction artifact. Its
manifest still uses `spatial_artifacts[]`, with artifact ID, content hash,
format, local-space and world-space bounds, transform reference, LOD descriptors,
byte size, Gaussian count, fidelity and trust evidence.

## 10. Activation and rollback

Activation is a separate operation from reconstruction and publication. It
requires a committed revision, currently passing verification, a successful
Viewer load check, and the active generation observed by the caller.

Conceptually:

```text
BEGIN IMMEDIATE
  verify scene is committed and currently eligible
  UPDATE active_scene
     SET scene_revision_id = candidate,
         generation = generation + 1
   WHERE project_id = project
     AND generation = expected_generation
  require exactly one row changed
  append activation event
COMMIT
```

A generation conflict returns the current active identity and requires refresh
and re-comparison. It never performs last-write-wins activation.

Rollback is the same CAS operation targeting a previously verified revision. It
does not copy files, mutate the old revision, or delete the newer revision. A
failed rollback leaves the current active scene unchanged.

Viewer requests the active descriptor, then loads immutable artifact URLs with
verified hashes. During reconstruction, failure, cancellation, or recovery it
continues to render the old active URL. A committed candidate survives a crash
between publication and activation and remains available for later comparison.

## 11. Studio experience

The revision workspace answers four questions before exposing activation:

1. Which CaptureRevision and authoritative SfM evidence produced this scene?
2. Which SceneRevision is active and which candidate is being inspected?
3. Is the candidate building, committed, verified, stale, failed, or blocked?
4. Which actual engine, transforms, artifact fidelity, and runtime evidence
   justify its trust labels?

After a successful candidate build, Studio opens a synchronized split comparison:

- active and candidate share camera pose, field of view, movement and LOD
  policy;
- each side shows revision ID, capture identity, parent, requested/actual engine,
  provenance, Gaussian count, bounds, bytes, warnings and verification level;
- activation remains explicit and states that no old revision will be deleted;
- a CAS conflict refreshes the active side and requires a new comparison;
- keyboard navigation, focus, progress announcements and reduced-motion behavior
  remain part of acceptance.

Studio remains native ES modules. B2 splits revision store, comparison view,
activation actions and derived UI state into focused modules rather than adding
a framework migration.

## 12. API boundary

The loopback origin, rotating request token, strict Host/Origin checks, body
budgets, no-store responses, single-writer lease, and path-containment rules from
B1 remain mandatory.

The B2 surface may add the following bounded operations:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/revisions/captures` | list sanitized capture summaries |
| `GET` | `/api/revisions/scenes` | list scene identity, lineage and projection |
| `GET` | `/api/revisions/scenes/{id}` | return sanitized evidence and artifacts |
| `GET` | `/api/active-scene` | return active ID, generation and immutable URLs |
| `POST` | `/api/jobs` | fixed ingest/handoff/import/reconstruct command schemas |
| `POST` | `/api/revisions/scenes/{id}/activate` | activate with `expected_generation` |
| `POST` | `/api/revisions/{id}/pins` | create or remove an explicit retention pin |
| `GET` | `/api/artifacts/{id}` | serve a known immutable artifact with safe range support |

Responses expose revision and artifact IDs, sanitized project-relative
identifiers, enums, metrics, and hashes. They never expose absolute paths,
original filenames, GPS values, environment dumps, credentials, cookies, SSH
keys, payment information, or unrestricted file contents.

## 13. Privacy and public-repository rules

All real image/video bytes, extracted frames, EXIF/GPS, original names, SfM
models, PLY files, training packages, checkpoints, databases, logs and local
paths are private runtime data. They must remain under ignored roots and pass a
pre-commit privacy scan.

Logs use stable error codes and sanitized logical identifiers. Authorization
headers, known secret values, full environments and user-directory paths are
redacted. TrainingHandoff records only a provider-neutral external boundary;
credentials remain in the user's chosen provider or session.

The public repository may contain schemas, scripts, documentation, checksum
locks, synthetic-source manifests, and tiny explicitly reviewed test fixtures.
It may not contain the generated mountain-village input pack produced for local
L2 testing unless a later, separate review approves a deliberately public
fixture.

## 14. Retention and garbage collection

The following are marked reachable and cannot be collected:

- the active SceneRevision;
- every explicitly pinned revision;
- all dependencies and parents required by the active or pinned graph;
- revisions held by a live run, verification, comparison, or lease;
- the most recent verified rollback point;
- any artifact referenced by an incomplete publication or recovery decision.

GC is mark-and-sweep with a two-phase delete:

1. in a short transaction, capture the active generation, leases and exact
   reachable set in an immutable GC plan;
2. before every mutation, recheck generation, pins and leases;
3. move eligible bundles to a tombstone/quarantine location with a durable
   journal;
4. retain them for a configurable cooling period;
5. physically remove only a still-eligible tombstone and append completion
   evidence.

GC does not delete user originals. Removing original images or video is a
separate, explicit data-management operation that previews impact and recovery
options.

## 15. Error and recovery behavior

| Failure | Required result |
|---|---|
| capture interrupted | no CaptureRevision is published; staging is quarantined |
| cloud training interrupted | local handoff remains; no false local recovery claim |
| import interrupted | inbox bytes remain private; no incomplete descriptor is visible |
| reconstruction interrupted | old active remains; incomplete staging is quarantined |
| candidate verification fails | candidate is ineligible; old active remains |
| Viewer load fails | activation is rejected |
| activation process dies | transaction selects exactly old or new active generation |
| CAS conflict | refresh and compare again; never overwrite automatically |
| committed candidate not activated | candidate remains inspectable after restart |
| evidence/hash mismatch | fail closed and quarantine affected bytes |

Startup recovery is observer-only while another live owner holds the writer
fence. Parent death, child survival, stale leases, incomplete publication and GC
journals are covered by real external-process tests, not only monkeypatched
exceptions.

## 16. Technology and reproducibility policy

### 16.1 Local runtime

Production pins a Python 3.11 patch release and a hash-locked dependency graph;
Python 3.13 remains a forward-compatibility CI lane. SQLite uses the standard
library so transaction and lock behavior stays explicit. No ORM or separate
database service is introduced.

COLMAP remains an external `shell=False` CLI, not PyCOLMAP. The selected Windows
CPU binary is accepted only after a canary and records source URL, archive
SHA-256, version/commit, binary identity and supported-option help digest. GPU
flags are selected from the pinned binary contract, not guessed between version-
specific names. Ordered video defaults to sequential matching; small unordered
sets may use exhaustive matching; large unordered sets require a bounded
vocabulary-tree policy.

FFmpeg/ffprobe will become the authoritative video container, codec, duration,
timestamp and frame-extraction backend after its own pinned Windows canary.
OpenCV remains for pixel-level blur, resize and color analysis. Until that
migration, CaptureRevision records the actual OpenCV decoder backend and sampling
parameters.

### 16.2 Cloud training

The recommended lane is a pinned Linux Nerfstudio Splatfacto OCI image. The
exact Nerfstudio, PyTorch, CUDA and resolved gsplat combination is frozen only
after a real canary, and the image is referenced by digest rather than tag.
Splatfacto is the default quality tier; higher-memory presets are explicit.

CUDA training is statistically reproducible, not assumed byte-identical. Inputs,
environment, seed and configuration are fixed, while held-out views and quality
metrics determine regression. Deterministic conversions, manifests and CPU
transformations remain byte-exact and are tested separately.

### 16.3 Web runtime

Spark 2.1.0 and Three.js 0.180.0 remain the B2 renderer pair. Exact npm versions
and a committed lockfile produce a local vendor/bundle artifact with its own
hash and self-only CSP. Runtime CDN availability is not accepted as a
reproducibility guarantee. Spark failure uses an explicit lower-fidelity
fallback and never raises the trust label.

### 16.4 Toolchain capsule

Local dependency locks, native `tools.lock.json`, cloud OCI digest and package
inventory, and exact web artifact hash form the `toolchain_capsule_digest`
recorded by every SceneRevision. Request deduplication includes this digest.

## 17. Computer-use boundary

Computer use is appropriate for user-session and visual tasks: account login,
2FA, terms, payment confirmation, cloud console authorization, GUI-only
installers, Windows prompts, and final browser rendering inspection. The user
must confirm identity, payment, account changes and external uploads at the point
of action.

Scriptable downloads, extraction, hashes, dependency probing, training
supervision, artifact movement, SQLite mutations, publication, activation, GC and
crash testing use controlled CLI/API/subprocess paths with durable logs. A click
record is never reproducibility evidence. Long cloud training should use a
provider API or SSH/tmux/log polling where available, with computer use limited
to unsupported interactive edges.

## 18. Synthetic mountain-village input contract

Before real material exists, GPT image generation supplies a generic, replaceable
mountain-village mock pack under ignored `input/`. It is a visual workflow fixture,
not a photogrammetric truth claim.

The pack uses stable logical slots rather than a named real village:

- elevated establishing view;
- entrance path view;
- central courtyard view;
- terrace and rear-building view;
- opposite-slope view;
- close material/detail view.

Every generated image is labeled `synthetic=true` and records its final prompt,
source SHA-256, known generator identity, generation time, and any reference
image hashes. If the built-in tool does not expose an exact model identifier, the
manifest records `unknown` rather than inferring a marketing name.

Real input replaces the entire pack through the same CaptureRevision contract.
No layout, reconstruction, Viewer, or activation code may depend on a fictional
village name, exact facade, image count, or generated pixel dimensions.

Image-generation views are not guaranteed to share exact camera geometry. They
are valid for L2 UI and pipeline simulation, but they cannot pass L3 SfM truth or
be described as a successful real 3D reconstruction without measured evidence.

## 19. Verification levels and acceptance

Every result displays its highest completed verification level:

| Level | Meaning |
|---|---|
| L0 | schema, manifest, hash, CAS, migration, path and redaction contracts |
| L1 | real-process crash, restart, publication, activation and GC fault injection |
| L2 | full workflow with clearly marked synthetic inputs |
| L3 | real local input and authoritative COLMAP/SfM evidence |
| L4 | real external GPU training with returned capsule and verified export |
| L5 | real target-device Spark comparison, activation and navigation |

### 19.1 Hard data-safety gates

- A kill at any publication or activation edge never corrupts the old active
  scene.
- Restart preserves active ID, generation and all committed hashes.
- Concurrent activation against one generation has at most one winner.
- Missing manifest, hash damage, unknown coordinates, unsafe paths and malformed
  artifacts cannot activate.
- GC cannot collect active, pinned, leased or reachable dependencies.
- The API rejects absolute paths, traversal, arbitrary file reads and
  over-budget bodies.
- A repository privacy scan has zero hits for private runtime payloads and
  secrets.

### 19.2 Reconstruction evidence

Real runs report selected/deduplicated/rejected image counts, COLMAP registration
ratio, failed images, camera trajectory, sparse model, reprojection-error
distribution, authoritative SfM digest, training environment, transform chain,
Gaussian count, bounds, units, held-out PSNR/SSIM/LPIPS, and known uncovered or
low-confidence regions.

There is no universal visual threshold that is honest for every scene. Hard
contract failures block publication or activation. Visual metrics compare
against a predeclared quality preset and the same-dataset baseline; a regression
blocks automatic recommendation and requires the synchronized human comparison.

### 19.3 Viewer acceptance

On the target Intel UHD 770 machine, a fixed browser, 1080p viewport, fixed test
route and explicit splat budget record first-frame time, stable-load time,
average and low-percentile frame rate, memory peak, input latency, LOD stability,
and active/candidate camera synchronization. A canary establishes the honest
`smooth` and `usable` bands; B2 does not preclaim that integrated graphics can
render an arbitrary PLY at full density.

Browser automation covers reproducible interaction and screenshots. Human visual
inspection covers holes, trailing artifacts, flicker, motion comfort and control
feel. The two forms of evidence remain separate.

## 20. Capability boundary for 100 images or a 20-minute video

Either input can produce an interactive Gaussian scene, but file count, duration
and gigabytes do not guarantee a perfect result. One hundred sharp,
well-overlapped views with height and occlusion coverage may be better than a
long, repetitive, blurred video. Video is filtered for duplicate, blurred and
exposure-damaged frames before reconstruction.

The product promises navigation only inside sufficiently observed and validated
space. It may support 360-degree viewing of reconstructed local structure, but
does not claim accurate unseen backs, closed interiors, sky, glass, water,
textureless surfaces or moving objects. Results are labeled as observed
reconstruction, low-confidence reconstruction, generated completion, or
uncovered region. Generated completion is never presented as measured reality.

## 21. Required test matrix

### 21.1 Contract and migration

- canonical manifest hashing and immutable-ID validation;
- SQLite v1-to-v2 migration, fingerprint, downgrade refusal and foreign keys;
- activation CAS success, stale generation, concurrent winner and idempotency;
- path, identifier, JSON, range, body-budget and log-redaction boundaries;
- projection of committed/verified/candidate/active without mutating manifests.

### 21.2 Publication and recovery

- real external publisher kills before and after every file and database edge;
- parent death while a child survives, followed by observer-only recovery;
- complete orphan roll-forward and incomplete/mismatched quarantine;
- active scene remains byte-identical during failed capture/import/reconstruct;
- deterministic transformation creates new bytes and never edits input in place;
- GC crash at plan, tombstone, cooling and physical-delete edges.

### 21.3 Coordinate and external-capsule tests

- local authoritative SfM consumed unchanged by the training handoff;
- cloud-authoritative model import requires actual model and camera mapping;
- a deliberately mismatched local/cloud frame is rejected;
- missing capsule fields, hashes, transforms, tool identities and export bytes
  fail closed;
- fake COLMAP proves no writes occur outside the run workspace;
- pinned COLMAP option contract is tested against the actual binary help.

### 21.4 Studio and Viewer

- active remains visible while candidate builds, fails, is canceled or recovers;
- synchronized split comparison, metadata, warnings and fidelity labels;
- Viewer-load failure blocks activation;
- activation conflict refreshes and requires comparison again;
- rollback uses CAS and preserves both revisions;
- keyboard, focus, live-region and reduced-motion acceptance;
- immutable URLs and hashes prevent mixed-generation scene bytes.

### 21.5 Real canary

B2 is not complete with synthetic tests alone. At least one small real dataset
must complete L3, external L4 training, and L5 target-device inspection. Account,
GPU rental, payment, uploads and provider terms remain user-confirmed external
actions.

## 22. Delivery slices

Implementation planning will split B2 into independently reviewable slices:

1. SQLite v2 migration, immutable bundle primitives, CaptureRevision and
   read-only revision queries;
2. authoritative SfmBundle, deterministic TrainingHandoff and private
   ImportDescriptor;
3. transactional SceneRevision publication and spatial artifact contracts;
4. verification projection, immutable Viewer resolution and CAS activation;
5. Studio comparison, rollback, pins and crash-safe retention planning;
6. pinned native/cloud/web canaries and one real L3/L4/L5 acceptance run.

Each slice begins with failing tests, preserves sole-`main` repository hygiene,
uses path-scoped staging in the shared worktree, and receives fresh verification
before any completion claim.

## 23. B2 completion criteria

B2 is complete only when all of the following are true:

- CaptureRevision, authoritative SfmBundle, TrainingHandoff, ImportDescriptor
  and SceneRevision are immutable and traceable;
- local/cloud SfM frame ambiguity is eliminated by machine evidence;
- candidate comparison, explicit CAS activation and rollback work after restart;
- private data remains outside public Git and outside sanitized APIs/logs;
- all L0, L1 and L2 gates pass on current `main`;
- at least one real small dataset reaches L3, L4 and L5;
- Viewer remains on the old active revision during every failure scenario;
- reports clearly separate synthetic, local, cloud and real-device evidence;
- the system makes no claim of perfect or unlimited reconstruction outside the
  captured and validated volume.
