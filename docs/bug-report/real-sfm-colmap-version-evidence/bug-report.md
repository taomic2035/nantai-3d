# Real SfM COLMAP version evidence

| Field | Evidence |
|---|---|
| Reporter | Codex, during the fresh `production-v1-canary-20260726-b` real-scene canary on macOS arm64 |
| Symptom | The real COLMAP stage accepted 96 of 100 images and allowed training, but `registration-quality-report.json` recorded `engine_version: null`. |
| Reproduction | Run `python make.py real-canary RUN_ID=production-v1-canary-20260726-b sfm`, then inspect the immutable quality report at stage receipt `110d7dad51e1c2d8ada11c758ca5e6e1f1228f6659ffcbad8356559a076ce335`. |
| Root cause | `run_real_sfm()` hard-coded `engine_version=None` even though the active registration binary was already resolved by `pipeline.registration`. |
| Risk | A successful real-photo SfM decision did not bind the exact tool version that produced its sparse model. That breaks the formal provenance contract even though the geometry result itself remains valid. |
| Fix | Probe the exact active COLMAP executable with `feature_extractor -h`, parse its version banner, bind it into the quality report, and fail closed when the executable or exact version cannot be proven. |
| Warning strategy | Do not infer a version from filenames, package metadata, an unrelated doctor probe, or old receipts. A completed stage remains immutable; corrected evidence requires a new stage identity. |
| Acceptance | Focused RED/GREEN coverage proves successful binding and unidentified-binary rejection; the affected registration/quality/runner suite and Ruff pass; the live active-binary probe returns `COLMAP 4.1.0`. |

## Debug capsule

```text
Hypothesis:
  The production caller discards tool identity by passing a literal None.

Expected:
  A real COLMAP quality report contains the version banner measured from the
  same executable selected for registration.

Actual before fix:
  stage receipt:
    110d7dad51e1c2d8ada11c758ca5e6e1f1228f6659ffcbad8356559a076ce335
  registration report:
    fe6bafa1ba63fbfcd0da266b03007c8c1560a054bc7b8e4621503dfb938381ac
  quality report:
    f620461e5c6119ea5c6e388304818d16f5a0f5b16c181af80ae2619498ac6c31
  registered ratio:
    0.96
  engine version:
    null

Discriminating probe:
  .venv/bin/python - <<'PY'
  from pipeline.registration import colmap_version
  print(colmap_version())
  PY

Measured result after fix:
  COLMAP 4.1.0

Regression commands:
  .venv/bin/python -m pytest \
    tests/test_registration.py \
    tests/test_registration_quality.py \
    tests/test_real_scene_capture.py \
    tests/test_real_scene_operations.py \
    tests/test_real_scene_runner.py -q
  -> 119 passed

  .venv/bin/python -m ruff check \
    pipeline/registration.py pipeline/real_scene_capture.py \
    tests/test_registration.py tests/test_real_scene_capture.py
  -> All checks passed
```

`--retry` intentionally does not overwrite a completed stage: invoking it on
the affected run returned the same immutable receipt and did not create false
replacement evidence. A new canary run is required to record the corrected
version-bearing report.
