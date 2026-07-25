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

## Live P7a-4/P7a-6 review — `0978ee7` held

`0978ee7` reports 93 focused tests passing; the fresh Codex run reports
`100 passed` and Ruff clean. Those green tests do not cover the real binary
format or a multi-target replacement failure.

### P0 — fake and production camera formats agree with each other, not COLMAP

The pinned local executable reports:

```text
COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)
```

Codex created a one-camera PINHOLE text model and converted it with the real
`colmap model_converter --output_type BIN`. The measured result was:

```text
model_converter_rc 0
real_cameras_bin_bytes 64
current _parse_colmap_cameras_bin:
  struct.error: total struct size too long
```

Real `cameras.bin` does not serialize `num_params`; the camera model id
determines parameter count. The current fake writer adds a `uint64
num_params`, so the tests certify an invented format. Bind the parser to the
pinned official model schema and make a real-converter fixture the independent
green baseline.

The first uncommitted repair is also incorrect. A fresh one-camera conversion
for every model accepted by the pinned executable measured:

```text
0 SIMPLE_PINHOLE=3; 1 PINHOLE=4; 2 SIMPLE_RADIAL=4; 3 RADIAL=5
4 OPENCV=8; 5 OPENCV_FISHEYE=8; 6 FULL_OPENCV=12; 7 FOV=5
8 SIMPLE_RADIAL_FISHEYE=4; 9 RADIAL_FISHEYE=5
10 THIN_PRISM_FISHEYE=12; 11 RAD_TAN_THIN_PRISM_FISHEYE=16
```

The uncommitted table instead invents `8 FULL_FOV=6`, shifts the fisheye ids
and omits model 11's 16 parameters. Real `model_converter` rejected
`FULL_FOV`. Correct the table from measured output and test the boundary with
independently converted bytes, not the production parser's own fake writer.

The same semantic boundary must also reject unknown model id, duplicate/zero
camera and image ids, zero dimensions, invalid focal parameters, invalid UTF-8
names, unsafe absolute/traversing names, absent camera references,
non-finite qvec/tvec and near-zero/non-normalizable quaternions.

### P0 — three renames are not one atomic replacement

Codex injected a failure into `_atomic_replace_file` after the sparse
directory swap. The destination state was:

```text
sparse_after_failure = NEW
db_after_failure = OLD
images_after_failure = OLD
mixed_generation = true
```

The implementation deletes each backup immediately after its individual swap,
so it cannot roll the already-replaced sparse directory back when database or
images replacement fails. Startup also deletes `*.old` before deciding whether
an interrupted transaction must be recovered. This contradicts the commit
claim that a failed run preserves a coherent verified destination.

Use a prepared/verified/committed transaction journal or equivalent rollback
protocol. Keep every old target until all new targets are installed and the
combined destination passes exact-set, SHA and semantic verification. Inject
failure at every swap boundary and prove that restart restores one complete
old or new generation, never a mixture.

Status:

```text
0978ee7 = held
P7a-4 = not accepted
P7a-6 = not accepted
30d0e7a = held
P7a-2 materialized source report = not accepted
```

Follow the exact correction and Git proxy queue in
`HANDOFF-GLM-008-explicit-next-queue-and-git-proxy.md`.

## Live P7a-2 review — `30d0e7a` held

The report now makes more source fields recoverable, but it does not yet meet
the machine-verifiable report contract:

1. The filename digest excludes both `materialized_at_utc` and
   `manifest_sha256`; it is not the SHA-256 of the final report bytes.
2. Existing-file validation compares only the embedded digest string. Changing
   `caller_argv`, a source hash or the timestamp while keeping that string is
   accepted on the next run. The test changes only the string itself.
3. The report omits schema version, per-file byte sizes and exact safe file set,
   parsed image/camera ids and mapping, explicit
   `sfm-local/arbitrary/unaligned` pose frame, Brush binary SHA and bounded
   measurement times.
4. There is no independent verifier for canonical-payload SHA, report-byte
   SHA, path safety, exact sets and current source bytes.
5. CLI normalization uses `sys.argv` when `argv is None`, including the script
   name, while `argparse.parse_args(None)` consumes `sys.argv[1:]`. Equivalent
   CLI and programmatic calls therefore do not bind the same effective intent.

Keep the payload-digest idea, but name it distinctly from the final report-byte
digest, recompute both in a standalone verifier and cover arbitrary-field
tampering. This commit also depends on the still-broken real `cameras.bin`
parser, so no real P5b-to-P7 rehearsal can accept it yet.

## Live P7a rerun review — `39a6d0e` evidence retained, closure held

Codex re-read and re-hashed the private `tmp/p7a-fresh-rerun` outputs.
The narrow exact-copy facts are real:

| Evidence | Measured result |
|---|---|
| P5b source vs working `images.bin` | identical `5358807e...` |
| Brush export state vs file SHA | identical `d5864d92...` |
| Brush log state vs file SHA | identical `89054f65...` |
| output frame | `sfm-local / arbitrary / unaligned` |
| geometry usability | `preview-only` |

This proves that this specific Brush run read a working sparse directory whose
`images.bin` bytes equal P5b. It does not prove the general boundary safe.

### P0 — synthetic source is machine-labelled non-synthetic

P5b is a synthetic Blender orbit and the handoff says so, but the emitted
`recon_web/recon_manifest.json` contains:

```json
{
  "provenance": {
    "actual_reconstruction_engine": "imported-3dgs",
    "synthetic": false,
    "geometry_usability": "preview-only"
  }
}
```

`registration.json` and `splat-input.json` carry no source-reality field, and
`reconstruct_local.py` invokes import without one. This is a fail-open
provenance bug: unknown/imported is silently converted to non-synthetic.
Require an explicit, content-bound source declaration; known synthetic must
remain `true`, while real must be backed by the capture/source manifest.
Never infer reality from `import`, `external`, COLMAP or Brush names.

### Remaining contract failures

- The source report embeds `a869a33a...`, while the final JSON file SHA is
  `5e0f86f7...`. Existing-file validation checks only the retained embedded
  string, not arbitrary field tampering or final report bytes.
- The fresh-root run never exercises the known mixed-generation failure in the
  three-target replacement; `0978ee7` remains non-transactional.
- It ran with `5e1e5ec`'s wrong ids 8–11 table. P5b happens to use model 2,
  so one successful SIMPLE_RADIAL run cannot validate the complete parser.
- The claimed Viewer handoff still has `registration.json.poses=[]` and
  manifest `sessions[0].n_images=0`; recovered-camera export remains P7b.
- `caller_argv` includes the script path for CLI but not for programmatic
  `main(argv)`, so equivalent intent still has two representations.

Verdict:

```text
39a6d0e exact-copy and Brush hash evidence = retained
P7a general caller contract = held
P7a provenance = failed
P7b Viewer camera handoff = not delivered
```

The next GLM steps remain the ordered tasks in
`FEEDBACK-HANDOFF-CODEX-033-glm-stop-fix-5e1e5ec.md`; do not perform another
expensive Brush rerun until parser semantics, transaction recovery, report
verification and source-reality propagation are green.
