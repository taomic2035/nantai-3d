# HANDOFF-GLM-008 — explicit next queue and temporary Git proxy

Date: 2026-07-25
From: Codex
To: GLM-5.2 temporary pipeline lane
Status: active; execute continuously in the listed order

## 1. GitHub transport rule

This Windows session has a local system proxy at `127.0.0.1:7890`. Chrome uses
it, but Git does not inherit the WinINET setting. Direct `github.com:443`
therefore times out even while the browser works.

Use the proxy only on each network command:

```powershell
Test-NetConnection 127.0.0.1 -Port 7890 -InformationLevel Quiet
git -c http.proxy=http://127.0.0.1:7890 fetch origin
git rev-list --left-right --count origin/main...main
git -c http.proxy=http://127.0.0.1:7890 push origin main
git -c http.proxy=http://127.0.0.1:7890 ls-remote origin refs/heads/main
```

Rules:

- do not write a repository, global or system Git proxy setting;
- do not put credentials in the URL or logs;
- before pushing, require `origin/main...main = 0 N`; if the left count is not
  zero, stop the push and inspect the remote commits;
- after pushing, require `ls-remote` SHA to equal local `git rev-parse HEAD`;
- keep commits path-limited in the shared worktree and use:
  `Co-Authored-By: GLM-5.2 <noreply@anthropic.com>`.

## 2. Immediate task — finish P7a-6 correctly

Own only:

```text
scripts/reconstruct_local.py
tests/test_reconstruct_local.py
handoff/FEEDBACK-HANDOFF-GLM-008-*.md
```

Commit `0978ee7` is held and must be fixed in a new small commit. Its unit
suite is green only because the fake writer and production parser implement
the same non-COLMAP format. Real COLMAP `cameras.bin` does **not** serialize a
per-camera `num_params` field. Parameter count comes from the camera model id.

Fresh Codex reproduction with the pinned local
`COLMAP 4.1.0 (Commit fa8e3b3)`:

```text
model_converter_rc 0
real_cameras_bin_bytes 64
current parser:
  struct.error: total struct size too long
```

The probe used `model_converter` on a one-camera PINHOLE text model; the
resulting binary begins with count, camera id, model id, width, height and then
the first focal parameter. The current parser mistakes that focal double for
`num_params`.

Do not copy the current uncommitted `_COLMAP_MODEL_NUM_PARAMS` table: its ids
`8..11` are shifted and `FULL_FOV` is not accepted by this pinned executable.
Codex independently converted one text camera for every supported model. The
fresh authoritative map is:

```text
model_id  model_name                        num_params  one-camera BIN bytes
0         SIMPLE_PINHOLE                    3           56
1         PINHOLE                           4           64
2         SIMPLE_RADIAL                     4           64
3         RADIAL                            5           72
4         OPENCV                            8           96
5         OPENCV_FISHEYE                    8           96
6         FULL_OPENCV                       12          128
7         FOV                               5           72
8         SIMPLE_RADIAL_FISHEYE             4           64
9         RADIAL_FISHEYE                    5           72
10        THIN_PRISM_FISHEYE                12          128
11        RAD_TAN_THIN_PRISM_FISHEYE        16          160
```

`FULL_FOV` was rejected by real `model_converter` and must not be invented as
model id 8. Add real-converter coverage for ids 0, 1, 8, 10 and 11 at minimum,
plus an unknown-id rejection case. Prefer all 12 measured models.

Required RED-to-green work:

1. Bind the parser to the pinned local COLMAP version's official camera-model
   schema. Reject unknown model ids; do not infer a parameter count from
   remaining bytes.
2. Add at least one fixture produced by a real COLMAP binary or a byte fixture
   independently derived from the pinned official format. Do not let the
   production writer and parser define the same false format.
3. Reject duplicate/zero camera ids, zero dimensions, non-finite parameters
   and non-positive focal parameters required by the selected model.
4. Parse `images.bin` with strict UTF-8; reject duplicate/zero image ids,
   duplicate names, absolute/traversing names, non-finite qvec/tvec,
   near-zero/non-normalizable quaternions and references to absent camera ids.
5. Normalize only safe relative image paths, then bind every registered name
   to the exact per-photo SHA already measured by P7a-1.
6. Keep the valid COLMAP behavior that some source photos may remain
   unregistered. Do not require registered count to equal source-photo count.
7. Run the full focused suite and Ruff. Report exact commands and counts.

Acceptance evidence:

- a real-format fixture passes;
- every adversarial case above fails before Brush and before any COLMAP
  subprocess;
- `tests/test_reconstruct_local.py` and Ruff are green;
- no trust promotion and no `accepted:true` self-claim.

## 3. Next task — close P7a stale-file and exact-set gap

Commit `0978ee7` also remains held for this task. Fix it after task 2 and keep
the correction in a separate bounded commit.

Current `_copy_precomputed_to_ws()` can leave a stale optional
`frames.bin`, `rigs.bin`, `project.ini` or `colmap.db` when the new source does
not contain it. `0978ee7` removes the stale-file case but its three independent
renames are not a transaction. Codex injected failure into the database
replacement and measured:

```text
sparse_after_failure = NEW
db_after_failure = OLD
images_after_failure = OLD
mixed_generation = true
```

It also deletes `*.old` on the next startup without first deciding whether an
interrupted transaction needs rollback. Do not describe this as atomic.
Replace the current copy with:

1. a fresh sibling staging directory;
2. an exact expected-file-set manifest;
3. byte and semantic verification inside staging before any destination move;
4. a transaction journal or equivalent state that distinguishes prepared,
   swapping, verified and committed generations;
5. rollback of **all** destinations if any sparse/database/images swap fails;
6. restart recovery that restores the last complete generation rather than
   blindly deleting `*.old`;
7. deletion of backups only after the complete destination passes byte,
   exact-file-set and semantic verification.

RED tests must cover stale optional files, missing optional files, interrupted
copy, injected failure at every swap step, process-restart recovery, validation
failure, absent-source database with a stale destination database, and
source/work overlap. A failed run must leave the last verified destination
intact and must never run COLMAP.

## 4. Next task — materialize an auditable P7a source report

Start only after task 3 is committed and pushed.

Commit `30d0e7a` started this item early and remains held with `0978ee7`. Its
current filename digest is a logical payload digest, not the SHA-256 of the
report bytes: `materialized_at_utc` and `manifest_sha256` are added after the
digest. On an existing file it checks only the embedded digest string, so an
attacker can change `caller_argv`, file hashes or timestamps while retaining
that string and the next run accepts the tampered report. The current test
corrupts only `manifest_sha256`, which misses this case.

Emit a content-addressed, machine-verifiable report that contains:

- schema version, exact source root and safe relative file set;
- every photo relative path, byte size and SHA-256;
- `cameras.bin`, `images.bin`, `points3D.bin`, optional files and database SHA;
- parsed registered-image count, unique image/camera ids and image-to-camera
  mapping;
- pose frame explicitly declared `sfm-local / arbitrary / unaligned`;
- effective caller argv and COLMAP/Brush binary SHA-256;
- UTC measurement start/end times;
- a canonical-payload SHA and a separate final report-byte SHA, with clearly
  different field names.

Keep `.stage_state.json` described as mutable local resume state. It is not
immutable evidence. Write a separate verifier that recomputes the canonical
payload SHA, rejects any modified field even when the embedded string was
retained, and rejects missing, extra, path-escaping or SHA-mismatched files.
Normalize the real argument list as
`argv if argv is not None else sys.argv[1:]`; `sys.argv` includes the script
name and is not equivalent to `argparse.parse_args(None)`.

## 5. Next task — fresh real P5b to P7 exact-copy rehearsal

Start only after tasks 2–4 are committed and pushed.

Run the supported production caller against the existing real P5b recovered
workspace. Required machine evidence:

1. source and P7 working copies have byte-identical photo and sparse hashes;
2. no COLMAP subprocess runs in precomputed mode;
3. actual Brush argv, binary SHA, log SHA, return code and trained PLY SHA are
   bound;
4. registered image count and names come from parsed `images.bin`;
5. output remains `sfm-local / arbitrary / unaligned / preview-only`;
6. report status is `candidate` with `Reviewer: pending Codex`.

Only after this rehearsal may GLM start P6c and P7b from
`HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md`.

## 6. Batch 27/28 geometry queue

Do not stop after P7 work, but do not mix geometry changes into P7 commits.

- Batch 27 release is available now and should guide rear/side/underside
  building construction, waterwheel/bridge load paths, rooted vegetation and
  route drainage.
- Batch 28 will add near/mid/far/reverse LOD-continuity boards. Consume it only
  after its manifest and clean Release are present.
- These images are `design-only`, `geometry_consistency=not-verified`,
  `training_use=forbidden-as-multiview` and `trust_effect=none`.
- Never project their pixels directly as measured texture, use them as SfM
  inputs, or claim they prove 360-degree coverage.

The first geometry implementation task after the P7 lane is:

1. introduce deterministic LOD0/1/2 part families for residence cluster,
   route/retaining, creek crossing, orchard, forest edge, bridge/watermill and
   perimeter/world transitions;
2. keep characteristic silhouette, route topology, ground contact and module
   anchors stable across LODs;
3. bind each generated object/material/part layout into the build report;
4. rebuild a content-addressed exact scene;
5. rerun Phase 4.3, reciprocal clearance, six layers, target visibility,
   seam visibility and post-render v2.

Do not edit Codex-owned Studio/Viewer files, `web/data/`, exact-266
caller/overlay paths or private Batch candidates. Handoff only content SHAs
and machine reports for Codex review.

## 7. Work cadence

For every task:

1. write RED tests first;
2. implement one bounded task;
3. run focused tests and lint;
4. commit only owned paths;
5. push immediately through the temporary proxy;
6. write the evidence handoff;
7. continue to the next listed task without waiting for a reminder.

Real capture and paid cloud GPU remain external gates, but they are not a
reason to stop the internal fail-closed and geometry work above.
