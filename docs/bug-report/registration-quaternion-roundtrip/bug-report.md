# Registration Quaternion Round-Trip Bug

## Bug diagnosis capsule

| Field | Evidence |
|---|---|
| Reporter | Codex, discovered while running the pinned 100-image poster canary on 2026-07-26. |
| Symptom | Real COLMAP registered 96/100 images and wrote `registration.json`, but `run_real_sfm()` rejected it with `registration object differs from registration.json bytes`. The expected behavior is exact object equality after writing and reparsing the trust-root JSON. |
| Evidence | The failure occurs at `pipeline/real_scene_capture.py:285`. Reparse diagnostics isolate the first drift to `frame_00088.png`: quaternion component `0.5836519348461495` becomes `0.5836519348461496` after a second validation pass. Sessions and coordinate frames remain equal. |
| Root cause | `CameraPose._unit_quat()` unconditionally divides an already unit-length quaternion by its floating-point norm on every validation. The operation is not bit-idempotent, so model construction and JSON reparsing can differ by one ULP even though the pose is semantically unchanged. |
| Diagnostic strategy | Reparse the exact failed artifact, serialize and parse it a second time, then compare each pose field. Contrast this with immutable models whose validators preserve already-canonical values. |
| Timeout strategy | If the focused model test does not reproduce within five minutes, retain the real artifact and instrument the registration writer/reader boundary rather than rerunning COLMAP. |
| Warning strategy | If more than one independent field drifts, or three focused fixes fail, stop and reassess the canonicalization boundary instead of widening equality tolerances. |
| User-visible correction | A valid real COLMAP result will proceed to the frozen registration-quality gate instead of being rejected because of validator-induced last-bit drift. Geometry trust and thresholds do not change. |
| Acceptance | A focused test using the observed quaternion must fail before the fix and pass after it; registration, registration-quality and real-scene-capture suites must pass; the preserved real canary artifact must resume through quality derivation without rerunning or relaxing policy. |

## Fix decision

Preserve an already unit-length finite quaternion verbatim within a strict
absolute tolerance. Normalize only materially non-unit inputs. This keeps the
existing safety property while making the validator idempotent. Comparing
models approximately was rejected because it would weaken an intentional
byte/object trust boundary and could mask drift in unrelated fields.

## Verification

- The focused regression reproduced the original code failure, then passed
  after the idempotence fix.
- The registration, registration-quality, real-scene-capture,
  registration-quality CLI and Studio capture-revision suites passed:
  `119 passed`.
- Ruff and `git diff --check` passed.
- The preserved real canary artifacts revalidated without rerunning COLMAP:
  96/100 registered, ratio 0.96, largest connected-model share 0.96,
  `quality_accepted=true`, `training_allowed=true`, and no rejection reasons.
