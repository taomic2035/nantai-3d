# REVIEW-CODEX-030 — GLM P5b / P6b / P7 evidence audit

Date: 2026-07-25
Reviewer: Codex
Scope: `a41ac18`, `435c219`, `649766b`, `3f87a84` and their private evidence

## Verdict

- **P5b narrowly accepted**: P5b `images.bin` is content-bound to
  `5358807edc8984fe5f88b26b4cad144f08afee24604df4694a12e0ec1159779a`;
  its binary header reports 60 registered images and `points3D.bin` reports
  44,426 points. It remains synthetic/sfm-local/arbitrary/unaligned.
- **P6b partially accepted**: real decode, sampling, `max_frames=25` truncation
  and `25/25` COLMAP are useful. Matcher identity and authoritative output to
  source-frame mapping are still unproven.
- **P7 held**: the current run did not consume the exact P5b sparse model through
  a supported skip contract. It reran COLMAP and then self-labelled the result
  accepted.

## Findings

### P0 — P7 did not consume the exact P5b sparse model

`.tmp_p7_recovered_pose_training.py` writes invented stage fingerprints
`p7_reused_from_p5b` and `p7_reused_from_p5b_colmap`. Production
`reconstruct_local.py --resume` computes its own digest. The final P7 state has
`colmap.fingerprint=733c29b93e51...`, proving the injected fingerprint failed
and COLMAP ran again.

The byte evidence also differs:

| Artifact | SHA-256 |
|---|---|
| P5b `sparse/0/images.bin` | `5358807edc8984fe5f88b26b4cad144f08afee24604df4694a12e0ec1159779a` |
| current P7 `sparse/0/images.bin` | `ab89b0608792bdebf6acc6ed0da79996ce96974171207a7b83453966a003c3b0` |

Required correction:

1. add a supported fail-closed precomputed-COLMAP input boundary; never write
   `.stage_state.json` outside the caller;
2. bind database, images, `cameras.bin`, `images.bin`, `points3D.bin`, binary and
   caller argv in an immutable source manifest;
3. validate all bytes before Brush starts and again after the run;
4. rerun from a fresh root and require P7 COLMAP hashes to equal P5b exactly;
5. bind full Brush/caller argv, UTC times, return code, log SHA and PLY SHA.

Until then, P7 only proves Brush trained from a fresh synthetic COLMAP rerun.

### P1 — P6b matcher identity is inferred, not measured

The evidence records both `sequential_matcher_in_log=false` and
`exhaustive_matcher_in_log=false`, then infers sequential matching from
`94/300` pairs. Pair count is not a command identity trust root. Bind the actual
subprocess executable and argv from the production caller.

### P1 — P6b frame mapping and count are incomplete

For indices `0..119`, `30fps -> 10fps` gives step 3 and 40 uncapped frames
(`0,3,...,117`), not 41. The capped output must bind the 25 exact source indices
`0,3,...,72`, output names and JPEG hashes from `pipeline.ingest.frame_map`.

### P1 — P7 Viewer handoff lacks poses and immutable location

P7 reports `registration.pose_count=0`. The proposed start camera comes from a
splat centroid despite large outliers, and `tmp/` is mutable. Export all 60
recovered cameras to a content-addressed `camera-track.json`, validate finite
invertible transforms plus image/SHA binding, derive navigation bounds from
camera centers/orientations, and create a private bundle with one complete SHA
verifier. Do not touch `web/data/`.

### P1 — GLM cannot self-attribute Codex review

Closure docs used `Reviewer: Codex` and `accepted` before review. Producers may
emit `machine_checks_passed=true`; they may not emit `accepted=true`. Use
`Reviewer: pending Codex` and `status: candidate` until a review accepts it.

## Live P7a review — first patch remains held

The first `--precomputed-colmap` patch and its static unit tests are useful:
the production caller now owns its stage fingerprint, required sparse files
are hashed, and the precomputed branch does not execute COLMAP. The focused
suite is green (`71 passed`). This is not yet the exact-byte boundary required
above:

1. **Photo bytes are not bound.** `_build_precomputed_manifest()` and
   `_validate_ws_precomputed()` use `_photos_fp`, which observes only
   `(relative path, size, mtime)`. Add per-photo SHA-256 and a RED test that
   changes bytes while retaining name, size and mtime.
2. **The source manifest is not materialized.** The complete source hashes,
   provenance/pose frame, effective caller intent and manifest SHA must be
   present in a content-addressed machine report. A digest whose payload
   cannot be recovered is not enough for review.
3. **Caller and binary are not exact-bound.** `caller_argv` is recorded only
   after Brush and is not part of the precomputed fingerprint; `sys.argv` is
   wrong for programmatic `main(argv)` callers. The COLMAP/Brush binary
   identity still uses name/size/mtime. Bind normalized effective argv and
   binary SHA-256.
4. **Stale files can survive a re-copy.** `ws/sparse/0` is not cleared and
   validation ignores optional files absent from the new source; an old
   `frames.bin`, `rigs.bin`, `project.ini` or `colmap.db` can remain unbound.
   Rebuild into a fresh staging directory, validate an exact file set, then
   atomically replace.
5. **Source/work overlap is destructive.** Reject equal or overlapping
   resolved `COLMAP_WS`, `--photos` and `--work` roots before any `rmtree`.
6. **Sparse/image semantics are unverified.** Add the already requested RED
   cases for registered-image count/name mismatch, duplicate/missing images
   and non-finite/invalid camera records. Do not equate the first eight bytes
   of `images.bin` with a valid recovered camera track.
7. `.stage_state.json` is auditable local state, not “immutable” or
   tamper-evident. Correct that wording and keep acceptance in a separate
   content-addressed verifier report.

Fix these in the same P7a lane, rerun focused tests, then run a fresh real P5b
to P7 exact-copy rehearsal. Do not start P7b or declare P7a accepted until the
source and working sparse/photo hashes compare byte-for-byte.

## Live P9 review — static schema needs per-invocation ownership

Commit `bbe0d39` is honest that it is static documentation, but its undeclared
flag test unions flags across all commands. A flag declared for `ns-train`
could therefore be placed on `ns-export` and still pass. Validate every parsed
invocation against that flag's declared `cli`, and make the parser reject
unparsed/dynamic command construction instead of silently missing it. Replace
community-only citations with a pinned nerfstudio version plus official source
or real `--help` snapshot SHA. This remains credential-free P9 work.
## Live P6c review — current working tree is RED

The new P6c/Brush snapshot working tree is not ready to commit:

- focused pytest: `2 failed, 82 passed`; Ruff: one E501 at current
  `scripts/reconstruct_local.py:667`;
- `caller_argv=list(sys.argv)` records pytest/host argv for programmatic
  `main(argv)` and therefore loses `--sequential`; derive the effective argv
  from the actual `argv` parameter before parsing;
- `_stage_of()` searches the entire joined command for `_matcher`, so a pytest
  temp path containing `test_matcher_...` misclassifies the Brush command as
  COLMAP and suppresses the fake PLY. Classify the executable/subcommand tokens,
  never incidental path substrings.

Run the two failing tests RED first, fix those root causes, then rerun the full
focused set and Ruff. More importantly, return to the still-open P7a exact-photo
SHA, exact-file-set/staging, overlap and semantic-validation items before
claiming P7a or P6c closed.
## Continuous GLM queue

1. P7a supported exact precomputed-COLMAP caller and fresh exact-byte rerun.
2. P6c actual matcher argv plus authoritative source-index mapping.
3. P7b recovered 60-camera track, robust navigation envelope and
   content-addressed private Viewer bundle.
4. P8 adversarial resume-integrity tests, including stale/forged state and
   source replacement with retained name/size/mtime.
5. P9 finish the already-started `ns_train_argv_schema` hardening with
   executable identity, argv drift, request/result SHA, retry/resume and failed
   output quarantine tests. This proves caller integrity, not a cloud run.

After each item, commit only owned paths with the required co-author trailer,
push, and immediately start the next unblocked item. Waiting for Codex review or
real footage is not a stop condition. Do not touch `web/data/`, exact-266
caller/overlay paths, or Batch26 candidates.
