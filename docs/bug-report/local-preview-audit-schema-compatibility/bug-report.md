# Local preview audit schema compatibility

Date: 2026-07-19

## Reporter

Codex found the failure while revalidating the existing `000e48...` local
textured preview before preparing a source-consistent PBR training dataset.

## Reproduction

1. Build and publish a local textured preview with the audit shape that existed
   before commit `e2d68f4`.
2. Run `verify_local_textured_preview_directory(...)` with the current code.
3. The stored audit fails Pydantic validation because `triangle_count` is
   absent, even though the GLB and the audit hash still match the immutable
   historical publication.

Expected: revalidate historical evidence without inventing the new field.

Actual: reject the publication before the verifier can remeasure the GLB.

## Root cause

Commit `e2d68f4` correctly made `triangle_count` mandatory for newly generated
`GlbMaterialAudit` evidence, but the local-preview reader had no historical
schema path. All three existing local preview audit files predated that field.
The GLB bytes were intact; only the stored audit shape was older.

## Fix

The local-preview verifier now accepts exactly two stored shapes:

- current canonical audits containing `triangle_count`;
- canonical historical audits without it.

Both paths rerun `audit_textured_glb(...)` from the current GLB bytes. A
historical audit is accepted only when every field it did record matches the
fresh measurement. The missing triangle count comes only from the measured GLB
and is never guessed, defaulted, or derived from a filename.

## Verification

- Regression test covers a valid historical audit and rejects a changed
  primitive count.
- Existing `000e48...` publication revalidates and reports its freshly measured
  triangle count.
- The complete local-preview test file and Ruff checks pass before delivery.
