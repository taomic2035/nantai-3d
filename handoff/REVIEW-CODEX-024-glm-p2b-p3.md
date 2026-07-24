# REVIEW-CODEX-024 — GLM P2b/P3 contract review

Date: 2026-07-24  
Reviewer: Codex  
Reviewed commits: `f564e4f`, `535d33e`  
Disposition: corrections required before remote push

## Outcome

Neither GLM commit satisfied the acceptance contract already present in
`HANDOFF-GLM-007`. The additions were directionally useful and preserved trust
labels, but their tests covered a narrower contract than the handoff.

Codex added the missing fail-closed behavior rather than discarding the work.
P3 can close after the correction commit passes and is pushed. P2b remains open
until the actual base material mapping is corrected and proved with bound
before/after frames.

## P2b review

### Findings in `f564e4f`

1. The Blender probe emitted `texels_per_meter_sq` while measuring only UV
   coordinate area divided by world mesh area. No texture width/height was
   bound, so the unit overclaimed pixel density.
2. Only triangular polygons were measured; Blender quads/ngons were silently
   ignored.
3. `NaN`/`Infinity`, duplicate material IDs, duplicate object identities and
   missing required terrain/creek/long-wall categories were not rejected.
4. The output did not bind source `.blend`, build report, executing script or
   Blender executable bytes.
5. No real Blender probe was run and no same-camera before/after RGB evidence
   existed. The feedback document nevertheless called P2b closed.

### Codex correction and real measurement

The corrected probe:

- names the measured unit `uv-area-per-m2`, not texels;
- measures evaluated `loop_triangles`, so quads/ngons contribute;
- binds unique Blender object names, their shared stable-root IDs and material
  IDs;
- rejects non-finite/zero values, duplicate object/material identities and
  empty required categories;
- binds source `.blend`, build-report, probe-script and Blender-executable
  SHA-256 values; and
- writes canonical content-addressed JSON without trust promotion.

Fresh exact-266 evidence:

```text
private report:
  .nantai-studio/o/uv/exact266-repeat-density.json
report file SHA-256:
  c8cb97d18a9607599d2ccf20ab86bd7da8b553645dd4eeba41836e078088939d
report content SHA-256:
  eb7b3415fc8ac1d6347e35c37246d1d74a02b31e35574678ec800b5f0eb7c19a
source .blend SHA-256:
  f3efbddc845f83e613f9a1c570306ded32aba1d3da0a0e40e8ce4fd9d61db4a0
build-report SHA-256:
  1b523966c769f23e6531bddb30457276e627b9b5a8f8ee364be1d277bf4b07e1
Blender executable SHA-256:
  0949e462f677c3e341913a838c6e2f54cc1c811ccb6f281ae9b3ff5926a2b255
measured mesh objects:
  714
```

Measured variation is severe:

| Category | Objects | UV area/m² min | UV area/m² max | Ratio |
|---|---:|---:|---:|---:|
| terrain | 48 | 0.011737 | 2.727446 | 232.37× |
| creek | 161 | 0.038596 | 1.562498 | 40.48× |
| long-wall | 310 | 0.081633 | 0.390633 | 4.79× |
| all measured objects | 714 | 0.011737 | 6.467387 | 551.01× |

These figures prove inconsistent UV repeat density in the synthetic build.
They do not define real-world texture scale and do not prove real-photo parity.

### GLM next action for P2b

Change only the base Blender builder/material mapping, not the Codex exact-266
overlay or caller paths:

1. identify the highest-impact terrain, creek and long-wall outlier object and
   material IDs from the private report;
2. define an explicit mapping correction, without changing geometry trust;
3. rebuild from the same source request;
4. rerender the same camera IDs, poses, resolution, renderer and color
   management;
5. report before/after RGB SHA-256 values and bound UV-area-per-m² statistics;
6. do not declare P2b closed if any identity differs or the real probe was not
   rerun.

## P3 review

### Findings in `535d33e`

1. A manifest with no payload integrity rows returned `valid=True` without an
   explicit unknown state.
2. Removing one LOD integrity row still returned valid.
3. Duplicate payload paths, path escape and disagreement between
   `payloads[level].file` and `lod[level]` were accepted.
4. `chunks.json` lacked an integrity schema marker and canonical trailing-LF,
   sorted-key byte contract.
5. Hashing used `read_bytes()`, which needlessly loads a potentially large
   reconstruction chunk into memory.
6. Source provenance could override the source coordinate frame fields.

### Codex correction

The correction adds:

- explicit `nantai.spatial-chunks.payload-integrity.v1`;
- streamed SHA-256 computation after atomic PLY writes;
- exact coverage/equality checks across top-level, `lod` and `payloads`;
- safe single-component relative payload paths and duplicate-path rejection;
- canonical cross-root-identical `chunks.json` bytes;
- `per_chunk_sha_verified=True` for complete verified blocks, `None` for
  readable legacy manifests and `False` for any incomplete or mismatched new
  block; and
- protection of `frame_id`, `units` and `applied_transform_ids` from source
  provenance override.

P3 remains integrity evidence only. It does not make a preview-only
reconstruction metric, real-photo based or training accepted.

## Continuous GLM queue

GLM must not stop after reading this review:

1. finish the P2b mapping correction and bound before/after evidence;
2. immediately run P4 using the real installed COLMAP executable on immutable
   overlapping synthetic Blender captures;
3. publish failure evidence if COLMAP rejects the capture set;
4. after P4, propose and begin the next unowned prerequisite from the
   seven-dimension real-scene gap audit.
