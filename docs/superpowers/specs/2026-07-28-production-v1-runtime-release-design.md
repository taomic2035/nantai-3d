# Nantai 3D Production V1 Runtime Release Design

Date: 2026-07-28

Status: distribution shape selected; written specification awaiting review

Chosen shape: one cross-platform runtime ZIP that can be verified offline from
downloaded bytes.

## 1. Purpose

This subproject turns one already accepted real reconstruction into the first
Production V1 distribution. It is the fifth subproject identified by
`2026-07-26-production-v1-real-golden-path-design.md`; it does not replace or
weaken any capture, SfM, CUDA training, metric-alignment, render, Viewer or
human-review gate.

The release must provide:

1. a final scene that opens from a clean Windows, macOS or Linux extraction;
2. one deterministic, content-addressed package receipt;
3. a standard-library offline verifier for both the ZIP and extracted tree;
4. a minimal public evidence projection bound to the full private acceptance;
5. a clear separation between package integrity and reconstruction trust;
6. a release process that verifies bytes downloaded back from GitHub.

The chosen deliverable is a runtime ZIP, not a Windows installer or hosted-only
Viewer. Native installers, automatic updates and hosted access remain later
distribution layers over the same verified runtime contract.

## 2. Non-goals and hard boundaries

This work must not:

- turn Preview2 synthetic content into a production scene;
- allow `internal-canary`, `preview-only`, `arbitrary` or `unaligned` input;
- make package verification create or upgrade geometry trust;
- include raw photos, raw video, EXIF/GPS, control-point coordinates, cloud
  credentials, SSH configuration, private host addresses, COLMAP workspaces,
  training caches, raw trainer logs or Blender work directories;
- publish a release when the full private evidence cannot be freshly reopened;
- claim that a public verifier can independently prove physical reality,
  copyright ownership or survey correctness from hashes alone;
- silently fall back to a Preview scene when the production scene fails.

The release remains blocked until one scene identity has all of:

- rights-cleared production capture;
- accepted real-photo COLMAP SfM;
- non-mock CUDA 3DGS;
- measured metre alignment;
- accepted held-out render evaluation;
- accepted Viewer v2 machine evidence;
- accepted human visual review.

## 3. Relationship to Preview2

Preview2 supplies reusable packaging mechanics only:

- safe POSIX member paths;
- declared allowlists;
- deterministic metadata;
- canonical JSON and checksum files;
- symlink, traversal and duplicate-path rejection;
- archive and extracted-tree verification;
- clean-room server and browser QA.

Production uses a separate schema and implementation boundary:

```text
nantai.preview-release.v1              synthetic / preview-only
nantai.production-runtime-release.v1   accepted real reconstruction only
```

The Production builder must not call `build_preview_release.py`, import a
Preview receipt as its trust source, or accept a Preview manifest as a
production fallback. Shared low-level archive helpers may be extracted only
when their semantics are neutral and covered by both suites.

## 4. Two evidence closures

Production distribution has two deliberately different closures.

### 4.1 Private acceptance closure

The private workspace contains the complete evidence needed by
`validate_real_scene_acceptance`, including source media, the capture bundle,
the production training bundle, held-out source pixels, measured controls and
all runtime evidence.

Immediately before packaging, the builder must:

1. resolve the content-addressed acceptance report through its pointer;
2. reopen every report reference and directory;
3. run the existing aggregate validator from original bytes;
4. require `source_role=production-acceptance`;
5. require `production_release_allowed=true`;
6. require all ten aggregate gates to be exactly `accepted`;
7. retain the canonical report SHA and freshly derived decision bytes;
8. reopen the accepted import and every final scene artifact again while
   assembling the package.

There is no `--force`, `--allow-preview`, `--ignore-rights` or equivalent
override. A caller-supplied decision boolean is never accepted.

### 4.2 Public distribution closure

Raw/private evidence is not copied into the ZIP. Instead the builder derives a
canonical `nantai.production-public-evidence.v1` projection from the freshly
validated in-memory models.

The projection contains:

- acceptance report SHA-256 and byte length;
- canonical acceptance decision and decision SHA-256;
- source role and opaque dataset/capture content identities;
- public rights scope and the fact that release inclusion was allowed, without
  private operator details or source paths;
- registration engine and accepted quality-report identity;
- immutable trainer/runtime/container identities and production quality role;
- import receipt identity, scene identity and complete final-artifact closure;
- target units, alignment status and alignment RMS, without measured point
  coordinates;
- render policy/report identities and derived gate result;
- Viewer v2 policy/report identities, runtime identities and derived gate
  result;
- human-review policy/review identities, categories and final dispositions;
- explicit lists of private evidence classes that were validated but omitted.

The projection is not accepted as input to the private validator. It is output
only, and its values are derived rather than copied from a user-authored
summary.

The public verifier can independently prove that the downloaded package,
public projection, final scene and runtime are the exact bytes named by the
receipt. It can re-derive the public decision contract. It cannot re-run the
private photo/control-point gates after those bytes have intentionally been
excluded. The authenticated release commit and published hashes therefore bind
the public projection to the build-time full validation; they do not turn legal
or physical-world claims into cryptographic facts.

## 5. Release receipt

The archive root contains `PRODUCTION-RELEASE.json` with schema
`nantai.production-runtime-release.v1`.

Required top-level fields are:

```json
{
  "schema": "nantai.production-runtime-release.v1",
  "version": "v1.0.0",
  "source": {
    "git_commit": "<40 lowercase hex>",
    "tag": "v1.0.0"
  },
  "package": {
    "layout": "nantai.production-runtime.v1",
    "immutable": true,
    "content_id": "<sha256>"
  },
  "scene": {
    "scene_identity": "<content identity>",
    "source_role": "production-acceptance",
    "quality_role": "production",
    "geometry_usability": "metric-aligned",
    "units": "meters",
    "alignment_status": "aligned",
    "trust_effect": "none"
  },
  "acceptance": {
    "report_sha256": "<sha256>",
    "decision_sha256": "<sha256>",
    "production_release_allowed": true,
    "public_evidence_path": "evidence/public-evidence.json"
  },
  "artifacts": [],
  "protected_roots": [],
  "entrypoints": {},
  "exclusions": []
}
```

`scene.trust_effect=none` means the package receipt does not promote scene
trust. The production fields are copied from the already accepted scene and
cross-checked against the public projection and final scene manifest.

The content ID is the SHA-256 of the canonical receipt with the content-ID slot
unset. Artifact entries contain canonical POSIX path, role, byte length and
SHA-256. Unknown fields, duplicate JSON keys, noncanonical JSON, non-finite
numbers and inconsistent derived fields are rejected.

Versions must be final semantic release tags such as `v1.0.0`; Preview suffixes
are rejected. No `v1.0.0` tag or Python `1.0.0` version is created until a real
candidate passes all private and public gates.

## 6. Package layout and allowlist

The package root is:

```text
nantai-3d-v1.0.0/
  PRODUCTION-RELEASE.json
  SHA256SUMS.txt
  VERIFY-AND-RUN.md
  LICENSE
  pyproject.toml
  make.py
  pipeline/
  scripts/
    verify_production_release.py
  web/
    studio/
    viewer/
    data/
      recon/
        recon_manifest.json
        chunks/
        <receipt-bound scene payloads>
  evidence/
    public-evidence.json
    acceptance-decision.json
    viewer/
      policy.json
      report.json
      screenshots/
    human-review/
      policy.json
      receipt.json
```

The final scene allowlist is derived from the revalidated production import,
not from a broad filesystem walk. It includes only:

- the final scene manifest;
- every chunk, LOD, mesh, texture, material or renderer payload directly
  referenced by that manifest or its validated import receipt;
- any scene-bound roaming/navigation graph accepted for the same identity;
- the runtime assets needed to render those formats;
- the minimal public evidence artifacts listed above.

The validated import's `web/` subtree is mapped into the existing
`web/data/recon/` runtime mount. This preserves the already tested
`/web/data/recon/recon_manifest.json` Studio/Viewer ABI; the builder records
both the import-relative source binding and packaged destination binding, so
the path mapping cannot change scene identity or hide a byte substitution.

Viewer screenshots are included because they are part of the public visual
acceptance. Their inclusion is permitted only when the source rights contract
allows release inclusion. Screenshots must not expose unrelated UI, local
paths, credentials or private metadata.

The builder rejects any final artifact that is missing, unbound, a symlink, a
special file, outside the accepted import root, changed during read, or changed
after the aggregate validation.

## 7. Builder architecture

New public entry points:

```text
python scripts/build_production_release.py \
  --acceptance-root <private-root> \
  --output <runtime.zip> \
  --version v1.0.0

python scripts/verify_production_release.py <runtime.zip> --json
python scripts/verify_production_release.py <extracted-root> --json
```

The builder is an orchestrator over four boundaries:

1. **Acceptance revalidator** — invokes the existing authoritative aggregate
   validator and returns immutable validated models, never an authored boolean.
2. **Public evidence projector** — emits the bounded redacted evidence
   contract from validated models.
3. **Scene closure resolver** — reopens the production import and enumerates
   only its content-bound runtime files.
4. **Archive assembler** — copies into a fresh staging directory, generates
   receipt/checksums, verifies the tree, writes a temporary ZIP, verifies the
   ZIP, then publishes with no-replace semantics.

All reads are streaming and check file identity before and after reading.
Output is written outside the acceptance root. Existing destinations,
pre-existing staging members and publication collisions fail closed.

Failure before final publication removes the temporary staging/archive.
Failure after a no-replace publication reports the exact published state and
never overwrites it on retry.

## 8. Determinism and cross-platform behavior

Cross-platform means the extracted runtime and verifier work on supported
Windows, macOS and Linux. It does not mean platform-specific Python/zlib
versions are assumed to emit identical compressed bytes.

Two identities are therefore explicit:

- `package.content_id` is derived from canonical paths and uncompressed file
  bytes and must be identical across supported operating systems;
- archive SHA-256 identifies one concrete compressed ZIP.

The canonical GitHub archive is built in a pinned release-builder environment
with exact Python and compression-runtime identities. Rebuilding in that
environment from the same commit and accepted scene must reproduce the archive
SHA. Windows and macOS candidate builds must reproduce the package content ID
and extracted tree; archive-byte equality is required only when they use the
same pinned builder image.

Archive members are sorted and use fixed timestamps, POSIX path separators,
normalized permissions, no data descriptors with unknown sizes, and one fixed
compression policy. The verifier does not trust ZIP metadata as a substitute
for extracted-byte hashes.

## 9. Offline verifier and archive safety

The verifier uses the Python standard library and does not import Pydantic,
Studio dependencies or optional reconstruction packages.

For a ZIP it validates the central directory before extraction:

- one canonical root directory;
- no absolute, drive-qualified, traversal, backslash or ambiguous Windows
  names;
- no duplicate or case-fold-colliding member paths;
- no symlink, device or encrypted member;
- bounded member count, per-file size, total expanded size and compression
  ratio;
- exact declared uncompressed sizes;
- no destination collision.

It then extracts into a new temporary directory and verifies:

- canonical receipt and public-evidence schemas;
- exact receipt content ID;
- every declared artifact byte length and SHA;
- no missing or unexpected file under protected roots;
- checksum-file agreement;
- production-only scene fields;
- acceptance decision canonical form and all ten accepted gates;
- cross-binding among acceptance, public projection, scene manifest, runtime
  entrypoint and Viewer/human-review evidence;
- `scene.trust_effect=none`.

Success reports both:

```text
package_integrity = verified
release_contract = production-accepted-at-build
```

It must not report `physical_reality_verified`, `rights_ownership_proved` or
any equivalent overclaim.

## 10. Runtime and user experience

The runtime remains a local HTTP application:

```text
python scripts/verify_production_release.py . --json
python make.py serve
```

Studio opens the accepted production scene by default. It must:

- display `real capture`, `production 3DGS`, `metres` and `metric aligned`
  only from the verified receipt/scene pair;
- separately display package verification state;
- expose the accepted scene identity and evidence summary;
- provide reset, orbit, zoom and allowed roaming controls;
- show actionable failure when any required renderer or scene artifact fails;
- never fall back to the synthetic Preview2 scene;
- avoid writing into protected package roots.

Mutable runtime state, browser cache, logs and support bundles are written to a
user-selected or platform-local state directory outside the extraction.

## 11. Testing strategy

Implementation is test-driven.

### 11.1 Contract and negative tests

- reject Preview, internal-canary and `production_release_allowed=false`;
- reject missing/rejected/unknown gates;
- reject rights, metric, render, Viewer v2 or human-review mismatch;
- reject acceptance report or evidence changed after initial validation;
- reject scene A acceptance combined with scene B artifacts;
- reject user-authored public projection fields that disagree with derivation;
- ensure private paths, operator details, source pixels and controls are absent;
- reject traversal, symlink, case collision, duplicate JSON keys, backslash,
  reserved Windows names and special files;
- reject archive bombs, excessive compression ratio and declared-size drift;
- reject existing destination and partial publication;
- prove that package verification never changes scene trust.

The positive repository fixture is an explicitly modeled contract fixture. It
can prove packaging logic but must never be labeled or published as a real
Production V1 candidate.

### 11.2 Determinism and portability

- two canonical builds produce identical archive SHA;
- Windows, macOS and Linux produce identical package content IDs and trees;
- verifier runs before dependency installation;
- non-ASCII evidence remains JSON-safe under Windows legacy code pages;
- large scene files are hashed and copied with bounded memory.

### 11.3 Clean-room runtime

For a real candidate on every supported OS:

1. download the ZIP and SHA sidecar;
2. verify the archive before extraction;
3. extract into an empty directory;
4. verify the extracted tree;
5. install only declared runtime dependencies;
6. start the local server;
7. open Studio in a fresh browser profile;
8. wait for the production representation to become interactive;
9. exercise reset, orbit, zoom and allowed roaming;
10. inspect three accepted camera poses and one transition between chunks/LOD;
11. confirm no console errors, indefinite loading or silent fallback;
12. compare displayed identity/trust fields with the receipt;
13. corrupt one scene artifact and prove verifier and UI fail closed.

Browser QA artifacts remain private release evidence unless explicitly
allowlisted into the final public projection.

## 12. Release process

1. land builder, verifier and runtime changes in small path-limited commits;
2. keep `main` synchronized after each green logical checkpoint;
3. run all repository tests, Ruff, Node suites and `git diff --check`;
4. obtain the first complete real-scene acceptance;
5. build and verify a release candidate from a clean exact commit;
6. run clean-room runtime QA on Windows, macOS and Linux;
7. create the final `v1.0.0` tag at the verified source commit;
8. rebuild in the pinned canonical builder environment;
9. publish only ZIP, archive SHA sidecar, public receipt/checksum and concise
   release notes;
10. download every GitHub asset back;
11. compare downloaded SHA and rerun the offline verifier;
12. publish the final acceptance report only after downloaded-byte QA passes.

Intermediate candidates, failed requests, contact sheets, caches, private
Blender roots and build logs are never Release assets.

## 13. Definition of done

Production V1 distribution is complete only when:

1. the private aggregate validator freshly derives
   `production_release_allowed=true`;
2. the ZIP contains one real, metric-aligned, accepted production scene;
3. no raw/private evidence or credentials are present;
4. receipt, public projection, final scene and runtime identities close;
5. archive and extracted-tree verification pass offline;
6. the package content ID is stable across supported platforms;
7. the canonical archive is reproducible in its pinned builder environment;
8. clean-room runtime and real-browser QA pass on Windows, macOS and Linux;
9. no synthetic fallback or trust promotion is possible;
10. full repository quality gates pass at the release commit;
11. GitHub-downloaded bytes match and verify;
12. documentation, Python version, tag and release title agree on Production
    V1.

Until all twelve conditions have machine and human evidence, the project
remains Preview even if the release tooling itself is complete.
