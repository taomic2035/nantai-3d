# REVIEW-CODEX-022 — GLM Registration and Training Trust Contracts

> Date: 2026-07-23
> Reviewer: Codex
> Scope: commits `039da69`, `05f554e`, `1e28ba3`, `9376979`, plus read-only
> pre-review of the uncommitted `prepare_import` consumer

## Decision

**Changes requested. Do not integrate the uncommitted `prepare_import` consumer
yet.** The two committed modules are useful schema prototypes and their own 74
tests plus Ruff pass, but three adversarial checks prove that the current
validators can promote self-reported or misparsed data. They are not yet safe
as provenance gates.

This review does not revert the four already-pushed commits. GLM should harden
the contracts in-place with TDD, then request a fresh Codex review before any
Studio, cloud runner, `registration.py`, `reconstruct_local.py` or import-path
integration.

## Findings

### P0 — Registration quality accepts unmeasured coverage claims

`validate_registration_quality()` hashes `registration_json_bytes` but never
parses those bytes or derives the measured fields from them. A report can bind
bytes that say `2/20`, claim `registered_count=100`, `registered_ratio=1.0`, omit
sessions/model enumeration, and pass validation. `capture_manifest_sha256` also
has no 64-hex constraint and its bytes are never supplied; the literal string
`not-a-sha` is enough to unlock the capture-presence check.

Additional fail-open paths follow from the same cause:

- `registered_ratio` is not required to equal `registered_count / total`;
- session registered/total counts, unregistered names and longest run are not
  re-derived from ordered capture inputs plus registered poses;
- session sums need not match global totals;
- COLMAP `model_enumeration` is optional, so the largest-component threshold is
  silently skipped;
- enumeration totals and selected counts need not match report/registration
  totals;
- rejection reasons need only be empty/non-empty, not equal derived reasons.

Adversarial result:

```text
REGISTRATION_FALSE_ACCEPT: validator accepted claimed 100/100 over bytes
saying 2/20; malformed capture SHA accepted
```

### P0 — COLMAP `images.txt` parser counts POINTS2D rows as images

`_parse_colmap_images_txt()` treats every non-comment row with at least ten
tokens as an image header. A POINTS2D row contains triples and commonly exceeds
ten tokens. A file with one image header plus four observed 2D points is
therefore reported as two registered images, with a coordinate token used as a
false image name.

Adversarial result:

```text
COLMAP_PARSE_FALSE_COUNT: one image line measured as image_count=2;
parsed_names=('photo.jpg', '40')
```

The current enumerator also handles only converted TXT while the normal mapper
first produces binary models and may create multiple `sparse/*` components.

### P0 — Training provenance can call a drifted failed command trustworthy

`validate_training_provenance()` does not bind actual trainer name/version to
the request, does not bind `actual_config_sha256` to request or actual config
bytes, accepts `state="completed"` with non-zero exit code, ignores the PLY
binding's declared size, and does not bind the declared log SHA to a log output
or actual log bytes. Timestamp fields accept arbitrary strings and ordering.

`derive_training_trust()` then treats valid-looking SHA strings and non-empty
identity/environment claims as verified facts. With registration quality passed,
an unrequested trainer, arbitrary config SHA, exit code `99`, false output size,
invented log SHA and invalid timestamps still produces `is_trustworthy=True`.

Adversarial result:

```text
TRAINING_FALSE_TRUST: validator accepted trainer/config drift + exit_code=99
+ fake size/log/timestamps; is_trustworthy=True
```

Input closure currently compares SHA sets only. This loses duplicate/kind/path
identity and never verifies the actual input artifact bytes, so
`inputs_verified=True` means only “strings look like SHA-256,” not “inputs were
verified.”

### P1 — Uncommitted `prepare_import` consumer contradicts its approved spec

The design reserves `training_provenance.v1=<result_sha>` for
`is_trustworthy=True`, including an accepted registration-quality report. The
uncommitted consumer always calls `derive_training_trust(...,
registration_quality_passed=False)` but appends the same evidence prefix when
only `content_closed=True`. That gives the prefix two incompatible meanings.

It also accepts `--training-request` without `--training-result` silently;
paired arguments must be symmetric. The explicit
`--allow-unverified-training` escape correctly adds no evidence and may remain
development-only after the underlying contract is fixed.

## Required GLM work, in order

### GLM-P0.1 — Make registration quality measured, not report-authored

Add red tests for every adversarial case above, then change the API so the
builder/validator consumes authoritative artifacts:

1. parse and validate the bound `RegistrationResult` bytes;
2. accept capture-manifest bytes, validate their SHA, and derive ordered global
   and per-session totals from them;
3. derive registered names from unique `RegistrationResult.poses` and reject
   unknown/duplicate pose identities;
4. compute ratio, unregistered lists and consecutive runs internally;
5. require a sparse enumeration for `engine="colmap"`, support real binary/TXT
   models without POINTS2D misclassification, and bind selected model counts to
   the registration result;
6. require every stored boolean/reason to equal the complete derived decision.

Do not add default quality thresholds and do not infer acceptance from the old
free-form `colmap.registration.coverage.v1` evidence string.

### GLM-P0.2 — Close the training request/result artifact chain

Add red tests, then require:

1. exact ordered/kinded input bindings, unique artifact identities, and actual
   input byte SHA/size verification;
2. requested trainer name/version equality unless an explicit structured drift
   record is present and separately rejected/approved by policy;
3. canonical requested-config SHA plus actual config artifact bytes/size/SHA;
4. `completed` iff exit code is zero and exactly one non-empty primary PLY
   binding matches actual SHA and size;
5. a training-log output binding matching actual log SHA and size;
6. strict UTC timestamps with `started <= finished`;
7. trust booleans derived only from completed verifications, never schema field
   presence.

Keep the honest boundary: even a closed training chain does not imply real
photos, visual quality, metric scale or geographic alignment.

### GLM-P0.3 — Rebase the `prepare_import` consumer on the hardened contract

After P0.1/P0.2:

- require request/result/registration-quality artifacts together for the
  trusted prefix, or rename a weaker content-only receipt so meanings cannot be
  confused;
- append `training_provenance.v1=<result_sha>` only when the hardened trust
  derivation is true;
- reject either half of every argument pair;
- keep unverified bypass explicit, development-only and evidence-free;
- add CLI integration tests for tampered inputs, config, log, PLY, quality report
  and asymmetric arguments.

### GLM-P1 — Only then wire real callers

Once Codex signs off P0.1–P0.3:

1. integrate the quality builder with the real COLMAP wrapper and bundled
   executable resolver;
2. make an explicit COLMAP request fail if unavailable; never silently promote a
   mock fallback;
3. update the cloud trainer to emit request/result/config/log receipts from
   actual files and command exit state;
4. preserve a reproducible textured canary with input SHA and machine reports;
5. hand the three-state status to Codex/Studio without changing trust.

## Verification performed by Codex

```text
python -m pytest tests/test_registration_quality.py \
  tests/test_training_provenance.py -q
74 passed

python -m ruff check pipeline/registration_quality.py \
  pipeline/training_provenance.py \
  tests/test_registration_quality.py \
  tests/test_training_provenance.py
All checks passed
```

These green checks are retained as baseline evidence. The three adversarial
checks above are the missing acceptance tests GLM must add before claiming the
contracts fail closed.
