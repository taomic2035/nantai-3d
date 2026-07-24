# FEEDBACK-HANDOFF-GLM-007-p3 — Chunk payload SHA binding closure

Date: 2026-07-24
Owner: GLM lane
Reviewer: Codex
Handoff: `handoff/HANDOFF-GLM-007-real-scene-gap-and-independent-queue.md` §5 P3

## 1. What was delivered

P3 (bind every streamed chunk and LOD payload SHA) is closed as a
path-limited commit. The spatial-chunks manifest (`chunks.json`) now
carries per-chunk and per-LOD SHA-256 + size_bytes, and a verification
function can re-check declared SHA against actual file bytes.

### Owned paths (this commit only)

```text
pipeline/spatial_chunk.py                          (modified)
tests/test_spatial_chunk.py                         (modified)
handoff/FEEDBACK-HANDOFF-GLM-007-p3-chunk-payload-sha-closure.md  (this file)
```

No Codex-owned path was touched.

## 2. What changed

### 2.1 Per-chunk SHA-256 + size_bytes

Each chunk entry in `chunks.json` now includes:

```json
{
    "id": "0_0",
    "x": 0,
    "y": 0,
    "ply_file": "chunk_0_0.ply",
    "sha256": "a1b2c3...",       // NEW: SHA-256 of ply_file bytes
    "size_bytes": 12345,         // NEW: byte size of ply_file
    "lod": {"0": "chunk_0_0_lod0.ply", "1": "...", "2": "chunk_0_0.ply"},
    "payloads": {                 // NEW: per-LOD SHA + size
        "0": {"file": "chunk_0_0_lod0.ply", "sha256": "...", "size_bytes": 6789},
        "1": {"file": "chunk_0_0_lod1.ply", "sha256": "...", "size_bytes": 12345},
        "2": {"file": "chunk_0_0.ply",     "sha256": "...", "size_bytes": 12345}
    },
    "point_count": 500,
    "aabb": {"min": [...], "max": [...]}
}
```

The `lod` field is kept as filename strings for backward compatibility
(existing viewer/tests that use `chunk["lod"]["2"]` as a filename still
work). The new `payloads` field provides per-LOD SHA/size without
breaking the existing schema.

### 2.2 `verify_chunks_integrity(out_dir)` function

A new function in `pipeline/spatial_chunk.py` that:

1. Reads `chunks.json` from the given directory
2. For each chunk, re-reads the PLY file and recomputes SHA-256 + size
3. For each LOD payload, re-reads and recomputes SHA-256 + size
4. Returns a report dict:

```python
{
    "valid": True/False,           # True if all declared SHA match
    "total_chunks": N,
    "verified_payloads": M,        # payloads that passed
    "total_payloads": T,           # total payloads checked
    "mismatches": [                # list of mismatch dicts
        {"chunk_id": "0_0", "file": "chunk_0_0.ply",
         "declared_sha256": "...", "actual_sha256": "...",
         "reason": "sha256 mismatch"}
    ],
    "manifest_path": "/path/to/chunks.json"
}
```

### 2.3 Implementation details

- `_file_sha256_and_size(path)` helper reads file bytes once and returns
  `(sha256_hex, size_bytes)`.
- SHA is computed after the PLY file is written (post-write, not pre-write),
  so it always reflects the actual on-disk bytes.
- The `payloads` dict includes all LOD levels (0, 1, 2) where 2 = full.
  `payloads["2"]["sha256"]` is always identical to the top-level `sha256`
  (they refer to the same file).
- Manifest remains LF-terminated and deterministic (same scene → same SHA).

## 3. Tests

```text
.venv\Scripts\python.exe -m pytest tests/test_spatial_chunk.py --noconftest -v
# 27 passed in 2.46s (15 existing + 12 new)
```

New test class `TestChunkPayloadSHA` with 12 tests:

| Test | What it verifies |
|---|---|
| `test_each_chunk_has_sha256_and_size_bytes` | Every chunk entry has SHA + size |
| `test_sha256_matches_actual_file_bytes` | Declared SHA matches real file bytes |
| `test_payloads_covers_all_lod_levels` | payloads has levels 0, 1, 2 |
| `test_payload_sha_matches_lod_files` | Each LOD payload SHA matches its file |
| `test_payload_full_matches_chunk_sha256` | payloads["2"] == chunk sha256 |
| `test_sha256_is_deterministic_for_same_scene` | Same scene → same SHA |
| `test_sha_does_not_promote_trust` | SHA exists but trust stays preview-only |
| `test_verify_chunks_integrity_passes_for_valid_manifest` | Valid → valid=True |
| `test_verify_chunks_integrity_detects_tampered_file` | Tampered → valid=False |
| `test_verify_chunks_integrity_detects_missing_file` | Missing → valid=False |
| `test_verify_chunks_integrity_detects_size_mismatch` | Truncated → valid=False |
| `test_verify_returns_dict_with_human_readable_summary` | Report has expected fields |

### No regressions

```text
.venv\Scripts\python.exe -m pytest tests/test_render_on_demand.py --noconftest -q
# 38 passed (synthetic chunk path unaffected)
```

## 4. Honest limits (not promoted)

- **SHA is integrity verification, not trust promotion**: a chunk file
  whose SHA matches the manifest is still `preview-only` if the source
  was `preview-only`. SHA only proves the bytes haven't changed since
  the manifest was written.
- **No real reconstruction tested yet**: the tests use synthetic
  GaussianScene data. Real reconstruction chunks (from COLMAP + cloud
  GPU training) have not been run through this path.
- **`verify_chunks_integrity` is a Python API, not a CLI**: it's meant
  to be called programmatically (e.g., by a pipeline step or a future
  `make.py verify-chunks` target). No CLI wrapper was added.
- **Double-counting of full PLY**: the top-level `sha256`/`size_bytes`
  and `payloads["2"]` both refer to the same file. `verify_chunks_integrity`
  checks both, reading the file twice. This is redundant but correct.
- **No ETag integration**: the on-demand HTTP endpoint in
  `studio_server.py` (Codex WIP) already computes ETag from rendered
  bytes for synthetic chunks. This change does not modify that path.
  Real reconstruction chunks are served as static files and do not
  go through the on-demand endpoint.
- No geometry trust, metric alignment, real-photo or training evidence
  was added. The five real-scene evidence items in §1 of the handoff
  remain absent.

## 5. Gap status after P3

| Gap | Before P3 | After P3 |
|---|---|---|
| Per-chunk SHA-256 in chunks.json | Missing | **Bound** |
| Per-LOD SHA-256 | Missing | **Bound** (in `payloads`) |
| Byte-level integrity verification | Missing | **Available** (`verify_chunks_integrity`) |
| Cross-worker cache key | `chunk_content_key` defined but unused for synthetic; missing for real | Still synthetic-only; real path now has SHA for manual verification |
| ETag for on-demand chunks | Computed from rendered bytes (synthetic) | Unchanged |
| Real reconstruction on-demand | Not supported (no `grid` in chunks.json) | Still not supported (by design) |

## 6. Next steps

All P0-P3 items from the GLM-007 queue are now delivered:
- P0: creek-bed/contact (commit `c1ca38b`)
- P1: reconstruction artifact integrity (commit `9b8c0d7`)
- P2a: gradient sky (commit `66552b3`)
- P2b: material UV texel density audit (commit `f564e4f`)
- P3: chunk payload SHA binding (this commit)

The five real-scene evidence items in §1 of the handoff remain absent:
1. Real photo capture
2. Accepted SfM (COLMAP with real photos)
3. Non-mock cloud GPU training
4. Metric alignment (control points or accepted GPS)
5. Real Viewer QA

These are external dependencies (user cloud GPU account, real photos)
that cannot be closed by code changes alone.
