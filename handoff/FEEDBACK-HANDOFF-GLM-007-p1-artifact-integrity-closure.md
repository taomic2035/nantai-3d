# FEEDBACK-HANDOFF-GLM-007 — P1 reconstruction artifact integrity verifier closure

> 回执给 Codex：关闭 HANDOFF-GLM-007 §4 的全部 8 项 P1 行为合同。
> Owner: GLM lane. Coordinator/reviewer: Codex.

## 1. Owned paths (this commit only)

```text
pipeline/reconstruction_artifact_integrity.py
scripts/verify_recon_artifacts.py
tests/test_reconstruction_artifact_integrity.py
make.py                                  (only the new verify_recon_artifacts target + docstring)
handoff/FEEDBACK-HANDOFF-GLM-007-p1-artifact-integrity-closure.md
```

Codex-owned paths (`scripts/synthetic_village.py`,
`pipeline/synthetic_village/perimeter_closure_*`,
`tests/test_synthetic_village_*perimeter_closure*`,
`tests/test_synthetic_village_cli.py`, `web/data/`, `studio_server.py`,
`local_production_runner.py`, `local_orbit_audit.py`, etc.) were **not** touched.
`scripts/inspect_recon.py` is unchanged (preserved as the lightweight claim
translator, per §4 requirement).

## 2. P1 behavior contract — point by point

| §4 | Required | Delivered |
|---|---|---|
| 4.1 | consume an explicit `recon_manifest.json` path | `verify_recon_artifacts(manifest_path: Path) -> IntegrityReport` in `pipeline/reconstruction_artifact_integrity.py`; CLI `python scripts/verify_recon_artifacts.py <manifest>`; `make.py verify-recon-artifacts` (uses `MANIFEST=` env, default `web/data/recon/recon_manifest.json`). |
| 4.2 | reject symlinks, path escapes, missing files, duplicate chunk paths, duplicate JSON keys | `_is_symlink`, `_safe_resolve`, `manifest_dir.relative_to(...)` escape check, `seen_paths` dict, `_DuplicateKeyRecorder` via `json.loads(object_pairs_hook=...)`. Each is reported separately in `IntegrityReport.path_safety_violations`, `duplicate_paths`, `duplicate_json_keys`. |
| 4.3 | recompute every declared artifact SHA-256 and size | `_sha256_file` reads in 1 MiB chunks; `actual_bytes = resolved.stat().st_size`. Compared against declared `sha256` / `bytes`. Results split into `verified` (both match) / `mismatch` (either differs) / `unknown` (no SHA or invalid format). |
| 4.4 | for `chunks.json`, verify every PLY/LOD entry and its declared bounds/count | `_verify_chunks` walks every chunk's `ply_file` + `lod.0/1/2` entries, checks file existence (not symlink), `total_chunks == len(chunks)`, `total_points == sum(point_count)`, declared `bounds` contains all chunk `aabb`s, and flags `extra_unbound_chunk_files` (PLYs in chunks dir not referenced by any chunk). |
| 4.5 | report `verified`, `mismatch` and `unknown` separately | Three distinct lists on `IntegrityReport`, each with its own pydantic model (`ArtifactVerification`, `ArtifactMismatch`, `ArtifactUnknown`). All models are `frozen=True, extra="forbid"`. |
| 4.6 | never promote `preview-only`, `metric-aligned`, real-photo, or training trust | `report.trust_preserved` is always `True`. `report.geometry_usability` is the manifest's declared value, unmodified. Byte verification is a separate concern from coordinate trust — a verified `preview-proxy` stays `preview-proxy`. |
| 4.7 | preserve `inspect_recon` as the lightweight claim translator | `scripts/inspect_recon.py` and `pipeline/recon_schema.py` were not modified. The new verifier is fully additive — `inspect_recon` continues to translate manifest claims without touching PLY bytes. |
| 4.8 | TDD for tampered PLY bytes, stale manifest SHA, missing chunk, extra unbound chunk, path escape, contradictory metric evidence | All six scenarios are in `tests/test_reconstruction_artifact_integrity.py`: `test_tampered_ply_bytes_detected`, `test_stale_manifest_sha_detected`, `test_missing_chunk_ply_detected`, `test_extra_unbound_chunk_detected`, `test_path_escape_rejected`, `test_contradictory_metric_evidence_flagged`. 11 additional CLI tests lock the exit-code contract. |

## 3. Stated limitations (in plain text, not hidden)

These are documented in the module docstring and surfaced in every `ChunksReport`:

- `chunks.json` schema has **no per-chunk SHA** today (only the manifest-level
  `source.recon_manifest_sha256` attests integrity). This module verifies that
  every chunk PLY exists, is not a symlink, is inside the chunks dir, and that
  `total_chunks` / `total_points` / `bounds` are internally consistent. It
  **cannot** detect tampered chunk PLY bytes without a per-chunk SHA; that gap
  is reported in `ChunksReport.per_chunk_sha_verified = False` and printed in
  the human report.
- The verifier reads manifest *claims* plus recomputed artifact bytes. It does
  **not** recompute Sim3 residuals or re-run COLMAP; contradictions in metric
  evidence are flagged by parsing the same `sim3.alignment.v1=<json>` strings
  that `inspect_recon` parses, using the same fail-closed rule as
  `pipeline.reconstruct._alignment_evidence_consistent`.

## 4. Exit-code contract (mirrors `inspect_recon`)

```
0  = 全部产物 SHA+字节匹配, 无路径安全问题, 无矛盾, 无 chunks 异常
2  = 发现任何 mismatch / 路径安全 / chunks 异常 / 矛盾 (可当 CI 门)
   (文件不存在/不是合法 JSON/symlink manifest 等致命错误经 SystemExit 抛出,
    shell 看到 exit code 1)
```

This makes the new script usable as a CI gate identical to `inspect_recon`,
but on a different concern: `inspect_recon` gates on manifest self-contradiction;
`verify_recon_artifacts` gates on byte-level tampering and path safety.

## 5. Real manifest verification (web/data/recon/recon_manifest.json)

The shipped synthetic manifest was verified end-to-end:

```
manifest SHA-256 = 44db09e3949739151a8774d94aff695c3ea2b5fe18762d748adf1f4b378d19ca
verified  = 4   (full_3dgs + lod.0 + lod.1 + lod.2)
mismatch  = 0
unknown   = 0
path_safety_violations = 0
chunks_report = None  (this manifest has no chunks.json)
contradictions = 0
trust_preserved = True
geometry_usability = preview-proxy  (unchanged — byte verification did not promote)
```

Verified artifact SHAs (all match manifest declared values):

| artifact_key | kind | fidelity | actual_bytes | actual_sha256 |
|---|---|---|---:|---|
| `full_3dgs` | 3dgs-ply | full-3dgs | 16973072 (16.19 MiB) | `62dd7f8e50f58fe925f0bdc45a8219c8b5acb2bcecc7ca8fa853b83f9117d96d` |
| `lod.0` | simple-ply | dc-point-preview | 104602 (102.15 KiB) | `e5565cd5e6905ecc963b88042ec82c97fc812fff72c9c2d6cf6927419e88179e` |
| `lod.1` | simple-ply | dc-point-preview | 390648 (381.49 KiB) | `d45fe40c7dbd93082d9e60105da30a4cf11d1d34258a5a9525e710f5eb184189` |
| `lod.2` | simple-ply | dc-point-preview | 1300805 (1.24 MiB) | `d75ec5975e774981d9112543605d094936aba8a2ae97897a59830c27df5bce09` |

CLI usage:

```
python scripts/verify_recon_artifacts.py web/data/recon/recon_manifest.json
python scripts/verify_recon_artifacts.py web/data/recon/recon_manifest.json --json
python make.py verify-recon-artifacts
MANIFEST=path/to/recon_manifest.json python make.py verify-recon-artifacts
```

The manifest remains `synthetic / L0 / preview-proxy / modeled-unverified`.
Byte verification does **not** promote it to metric-aligned or to "verified
real reconstruction."

## 6. Test commands and results

```
.venv\Scripts\python.exe -m pytest \
    tests/test_reconstruction_artifact_integrity.py -q
# 26 passed, 3 skipped in 0.30s
#   (3 skipped = symlink tests on Windows without admin permission;
#    the corresponding ValueError path is still covered by the
#    non-symlink tests via path-escape / missing-file scenarios)

.venv\Scripts\python.exe -m pytest tests/test_inspect_recon.py -q
# 38 passed in 0.12s  (inspect_recon unchanged; still the claim translator)

.venv\Scripts\python.exe -m ruff check \
    pipeline/reconstruction_artifact_integrity.py \
    scripts/verify_recon_artifacts.py \
    tests/test_reconstruction_artifact_integrity.py \
    make.py
# All checks passed!

# Real manifest end-to-end:
.venv\Scripts\python.exe scripts\verify_recon_artifacts.py \
    web\data\recon\recon_manifest.json
# exit_code=0, prints 4 verified artifacts, trust_preserved=True
```

The 11 new CLI tests in `tests/test_reconstruction_artifact_integrity.py`
lock the exit-code contract:

- `test_cli_clean_manifest_exits_zero`
- `test_cli_json_flag_emits_parseable_json`
- `test_cli_tampered_ply_exits_two`
- `test_cli_stale_sha_exits_two`
- `test_cli_missing_chunk_exits_two`
- `test_cli_chunks_total_mismatch_exits_two`
- `test_cli_path_escape_exits_two`
- `test_cli_contradiction_exits_two`
- `test_cli_missing_manifest_file_raises_systemexit`
- `test_cli_symlink_manifest_raises_systemexit`
- `test_cli_json_on_problems_still_exits_two`

## 7. Remaining real-scene blockers (still absent)

Per HANDOFF-GLM-007 §1, none of the five real-scene evidence items is closed
by this P1:

1. real overlapping capture with known acquisition provenance — absent;
2. accepted COLMAP/SfM poses and sparse geometry — absent;
3. one non-mock cloud-GPU 3DGS training result — absent (stub argv only);
4. imported splat artifact with measured alignment — absent;
5. Viewer QA over that real artifact — absent.

The new verifier only confirms that *whatever* is declared in a manifest is
byte-identical to what is on disk. It does not produce real photos, does not
run SfM, does not train on a cloud GPU, and does not perform metric alignment.
A `preview-only` manifest whose every byte verifies is still `preview-only`.

## 8. Next independent queue item

Per HANDOFF-GLM-007 §5: base-scene world and material audit.

Suggested scope (owned by GLM lane, only after this P1 is committed):

- add a deterministic synthetic world/sky and distance haze to the base
  Blender builder (`scripts/blender/build_synthetic_village.py` world block);
- keep it explicitly synthetic — do not call it HDRI or real lighting;
- add measured render exposure / background-validity gates;
- audit repeated/stretched materials on terrain, creek banks and long walls;
- produce before/after RGB with identical camera/frame identity and report
  content SHA values.

This work may touch the base Blender builder only (no exact-266 overlay paths,
no Codex-owned perimeter-closure paths).
