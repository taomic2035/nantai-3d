# Mesh v3 LOD0/1 byte verification

Reporter: Codex, found during the Mesh Bundle v3 quality gate on 2026-07-20.

## Diagnostic capsule

| Field | Finding |
|---|---|
| Symptom | `load_mesh_asset_bundle_v3` verified LOD2 variant bytes and geometry but only required LOD0/1 object paths to exist. |
| Evidence | The loader populated `glb_payloads` for every object, then performed length/SHA/fingerprint checks only inside the LOD2 variant loop. |
| Root cause | Exact H2 LOD0/1 reuse was checked while composing the manifest but omitted from independent on-disk verification. |
| Diagnostic strategy | Trace every descriptor consumer from manifest parsing to file read and compare with the v1/v2 verified loaders. |
| Timeout strategy | If exact LOD0/1 descriptors cannot be verified without trusting v2 paths, stop publication and add the missing evidence to v3. |
| Warning strategy | Any GLB descriptor class that reaches a verified loader without length and SHA checks means the fix is incomplete. |
| User-visible correction | Corrupted distant/medium-detail models cannot be silently served while near-detail models remain valid. |
| Acceptance | A one-byte LOD0 mutation fails load; v1/v2/v3 adjacent tests and publication reuse remain green. |

## Reproduction and root cause

Prepare and load a valid v3 bundle, mutate the file named by
`record.lod["0"].glb_object_path`, then load again. Before the fix, the load
succeeded because only LOD2 descriptors were compared with their bytes.

## Fix and verification

Verify the length and SHA-256 of both LOD0 and LOD1 descriptors for every
record before evaluating LOD2 variants. Keep the existing exact directory
closure, so renamed or extra objects remain rejected.
