# Production Staging Acceptance Rebuild Design

Date: 2026-07-28
Status: approved approach; written specification awaiting user review
Owner: Codex
Scope: Production V1 release staging trust boundary

## 1. Purpose

`build-production` reopens a private acceptance root and proves the real-scene
release gates, but `stage-production-assets` currently trusts fields inside an
already-built ZIP. A modeled fixture can set `fixture_kind` to `null`, recompute
its internal hashes, pass the offline verifier, and reach public staging without
reopening the private acceptance evidence.

This change closes that gap. Public staging must prove that its input archive is
the exact archive produced from:

- the caller-supplied private `ACCEPTANCE_ROOT`;
- the caller-supplied production `VERSION`;
- the current exact Git `HEAD`;
- the tracked, clean Production runtime sources at that `HEAD`.

The proof is a fresh deterministic rebuild in the staging process followed by
stable, byte-for-byte equality with the copied candidate archive.

## 2. Approved approach

The approved approach is **A: acceptance rebuild at staging**.

The alternatives remain out of scope:

- a signed builder attestation would add key custody, rotation and verification
  infrastructure before the project needs cross-machine signing;
- a GitHub workflow or tag ruleset alone can restrict publication paths but
  cannot prove that a crafted candidate ZIP came from private acceptance.

The rebuild is the authoritative trust check. Workflow and tag governance are
defense in depth added after the repository contract is closed.

## 3. Trust invariants

Staging succeeds only if all of these invariants hold:

1. the public output directory was absent and its parent is a real directory;
2. the input archive is a stable regular non-link file;
3. the selected Production runtime source paths are clean relative to exact
   `HEAD`;
4. a fresh builder run reopens and validates the private acceptance closure;
5. the requested version is the version embedded in both archives;
6. the copied candidate and the fresh rebuild are byte-for-byte equal;
7. the copied candidate independently passes tree/archive verification;
8. the copied candidate independently passes the publication privacy policy;
9. the final directory contains exactly the four public allowlisted files;
10. no failure publishes or replaces a partial output.

`fixture_kind`, `release_contract`, receipt hashes and archive checksums remain
useful internal consistency fields, but none of them substitutes for invariant
4 or 6.

Package verification still has `scene_trust_effect=none`. Rebuilding at staging
authorizes publication of already accepted bytes; it does not promote geometry,
alignment or measurement trust.

## 4. Caller and API boundary

The repository-maintainer API becomes:

```text
stage_production_release_assets(
    repo_root,
    acceptance_root,
    version,
    archive_path,
    privacy_policy_path,
    output_dir,
)
```

`repo_root`, `acceptance_root` and `version` are required. There are no unsafe
defaults and no mode that retains the legacy ZIP-only trust path.

The CLI gains required arguments:

```text
scripts/stage_production_release_assets.py
  --acceptance-root ACCEPTANCE
  --version v1.0.0
  --archive CANDIDATE
  --privacy-policy POLICY
  --output-dir RELEASE_DIR
```

The task runner requires:

```text
ACCEPTANCE_ROOT
VERSION
ARCHIVE
PRIVACY_POLICY
RELEASE_DIR
```

`repo_root` is the repository containing `make.py`; the bundled script derives
it from its own location. The task runner and CLI do not accept an override for
it.

A shared source-identity resolver returns the exact commit and tracked-file
list used by both `build-production` and staging. Staging resolves this identity
before the rebuild and again after it. A changed `HEAD`, index or tracked-file
list fails closed.

Only release-owned runtime paths must be clean. Unrelated documentation or
private workspace changes do not affect archive bytes and do not block staging.

## 5. Staging data flow

The operation uses one private, mode-`0700` staging directory beside the absent
final output:

1. validate the absent final output and real parent;
2. copy the source archive once into `.candidate.zip` while checking path and
   descriptor signatures;
3. resolve exact Git source identity;
4. call `build_production_release_archive` directly, targeting
   `.acceptance-rebuild.zip` inside the private staging directory;
5. re-resolve Git source identity and require equality with step 3;
6. compare `.candidate.zip` and `.acceptance-rebuild.zip` as stable regular
   files, in lockstep, to EOF;
7. require their sizes and SHA-256 values to agree with the builder result;
8. remove the private rebuild ZIP and its sidecar;
9. extract and independently verify the copied candidate;
10. require the verified version to equal the requested version;
11. run the existing publication privacy audit;
12. write the standalone receipt, checksums and archive SHA sidecar;
13. verify the four-file staging directory;
14. publish the directory with the existing no-replace primitive.

The original source archive is not read again after step 2. The copied candidate
is the byte identity under review and later becomes the public runtime ZIP.

The builder is called as Python code, not as a subprocess. This preserves one
error boundary, avoids parsing CLI output and allows staging to remove every
private rebuild artifact before publication.

## 6. Determinism and platform scope

The builder already normalizes:

- ZIP member order;
- the ZIP epoch;
- POSIX member paths;
- regular-file permission metadata;
- canonical JSON key order and line endings;
- compression level.

Existing tests prove two builds on one machine and runtime produce identical
bytes. The staging equality requirement therefore applies to a candidate and
rebuild made in the same pinned release-builder environment.

ZIP compression bytes can vary across zlib implementations or versions.
Consequently:

- the official candidate and staging rebuild run in the same release job and
  pinned environment;
- cross-platform CI compares `package_content_id`, artifact paths, byte lengths
  and artifact SHA-256 values;
- cross-platform CI does not require archive SHA equality;
- the downloaded verifier checks the official archive SHA and internal
  contract, but does not rebuild from private acceptance.

If future requirements demand staging on a different machine from the builder,
the project must add signed builder attestations. It must not silently weaken
byte equality to receipt-only trust.

## 7. TOCTOU and failure behavior

### 7.1 Candidate archive

The existing stable copy checks path and open-descriptor signatures before and
after streaming. Equality comparison performs the same checks on both private
files. The candidate is never compared through its original external path.

### 7.2 Acceptance closure

The builder continues to bind every observed acceptance file by size and
SHA-256 and reruns its second pass before returning. Any changed report,
import, training closure, rights evidence, metric evidence, Viewer evidence or
human review fails closed.

The accepted snapshot may change after a successful rebuild. That does not
invalidate the archive already derived from and bound to the validated
snapshot; its public receipt retains those content hashes.

### 7.3 Git source identity

Staging compares source identity before and after the builder call. The builder
also checks release-owned source cleanliness and verifies every copied source
before and after copying. Concurrent `HEAD`, index or packaged-source drift
fails closed.

### 7.4 Cleanup and publication

All rebuild artifacts remain inside the private staging directory. On any
exception, the complete staging directory is removed and the final output
remains absent. On success, rebuild-only files are removed before the existing
four-file verifier and no-replace publication run.

Errors exposed by the staging CLI are ASCII-safe and identify the failed gate
without printing private evidence contents. Builder errors are translated into
the staging error type while preserving the exception as the internal cause.

## 8. Adversarial acceptance matrix

The implementation must cover these cases:

| ID | Scenario | Required result |
|---|---|---|
| A1 | Modeled fixture changes `fixture_kind` to `null` and re-signs itself | reject by acceptance rebuild mismatch |
| A2 | Candidate rewrites or re-signs `source_commit` or public evidence | reject by byte mismatch |
| A3 | Candidate came from another acceptance root or scene | reject by byte mismatch |
| A4 | Caller `VERSION` differs from candidate | reject before publication |
| A5 | A release-owned source is dirty relative to `HEAD` | builder rejects |
| A6 | Acceptance evidence changes during rebuild | builder second pass rejects |
| A7 | Candidate changes during copy or private comparison | stable-file checks reject |
| A8 | Output, output link/junction or unsafe parent already exists | reject with no replacement |
| A9 | Exact candidate from the same acceptance, version and source | publish exactly four files |
| A10 | Downloaded four-file verifier runs without private acceptance | verify bytes and internal contract only |

Tests are concentrated in:

- `tests/test_production_release_assets.py`;
- `tests/test_production_release_builder.py`;
- `tests/test_make_runner.py`;
- CLI and documentation contract tests already covering Production release
  staging.

A real private acceptance fixture is not committed. Unit/integration tests build
a complete modeled acceptance closure and monkeypatch only external validators;
the adversarial candidate is independently well-formed so rejection proves the
new acceptance binding rather than an unrelated checksum failure.

## 9. Documentation and release workflow

The maintainer guide must distinguish three guarantees:

- `build-production` reopens private acceptance and builds a candidate;
- `stage-production-assets` independently reopens private acceptance, rebuilds
  and authorizes the exact candidate bytes for publication;
- `verify-production-assets` verifies the downloaded official bytes and
  internal contract, without reopening private acceptance or re-proving CUDA,
  metric, Viewer or human-review gates.

The future `workflow_dispatch` publisher must:

1. run on the pinned private release builder with access to `ACCEPTANCE_ROOT`;
2. build the candidate;
3. stage it in the same job and environment;
4. verify the four public files;
5. create `v1.0.0` only after all byte checks pass;
6. upload only the four allowlisted assets;
7. redownload and verify those assets;
8. mark the non-prerelease `v1.0.0` Release as Latest.

Tag rulesets and environment reviewers remain defense in depth. They cannot
replace the acceptance rebuild, and they must not be enabled until a verified
release actor can create the protected tag without guessed bypass IDs.

Synthetic design-input Releases and historical Preview tags remain intact.
Future design-input Releases should use `make_latest=false` so they do not
compete with the product release label.

## 10. Non-goals

This change does not:

- provide real photographs or close the real capture gate;
- run COLMAP or accept an SfM result;
- run non-mock CUDA 3DGS training;
- create measured Sim3/ENU alignment;
- perform human production Viewer QA;
- make Preview2 synthetic content eligible for Production;
- add cryptographic signing or remote key management;
- delete or rewrite existing Releases or tags.

## 11. Definition of done

The change is complete when:

1. the legacy ZIP-only staging caller no longer exists;
2. all five staging environment inputs are required by `make.py`;
3. A1 through A10 have executable regression coverage;
4. the exact accepted positive candidate publishes only four files;
5. every negative case leaves no final or partial publication;
6. the public verifier still works without private acceptance;
7. maintainer and public documentation state the three trust boundaries
   accurately;
8. targeted tests, Ruff, diff checks and the full CI matrix pass;
9. the committed and pushed implementation uses the required Codex co-author
   trailer.
