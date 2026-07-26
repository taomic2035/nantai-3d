# REVIEW-CODEX-037 — Batch35 prop geometry v2 pure-model review

Date: 2026-07-26
Reviewer: Codex
Reviewed commit: `381f243 feat: define Batch35 prop geometry contracts`
Verdict: **changes requested — Blender emission remains blocked**

## What

The eight-slot canonical graph, 79-part coverage, source-image identities,
semantic counts, registered material-slot references and declared AABB
intersections are internally coherent. The focused suite remains green.

The candidate cannot be signed off because its advertised frozen/fail-closed
boundary has two P1 escapes and one lower-severity type-boundary defect.

### [P1] `model_copy(update=...)` bypasses every trust-field validator

`_Frozen` prevents attribute assignment, but Pydantic's
`model_copy(update=...)` does not validate the update. Consequently the public
plan can be silently promoted after validation:

```python
plan = build_prop_geometry_v2_plan()
promoted = plan.model_copy(update={"stage": "accepted"})
assert promoted.stage == "accepted"  # accepted, despite Literal["design-only"]
```

This directly contradicts the handoff claim that `model_copy(update=...)`
re-validates through `model_validate`. The same unsafe method is used by
`PropSlotPlan.finalize_for_build()` even though that particular caller
currently regex-checks its one update first.

Affected boundary:

- `pipeline/synthetic_village/prop_geometry_v2.py:276`
- `pipeline/synthetic_village/prop_geometry_v2.py:525`
- `pipeline/synthetic_village/prop_geometry_v2.py:544`
- `handoff/FEEDBACK-HANDOFF-GLM-010-prop-geometry-v2-pure-model.md:90`

### [P1] The frozen plan and locked contract tables remain mutable

`frozen=True` is shallow. `PropGeometryV2Plan.slot_plans` is a mutable `dict`,
and every exported contract table is also a mutable `dict`. A validated plan
can lose required slots without an exception:

```python
plan = build_prop_geometry_v2_plan()
plan.slot_plans.pop("prop-water-jar-01")
assert len(plan.slot_plans) == 7
```

More seriously, the supposedly locked source identity and canonical plan can
be changed together and are then accepted by the builder:

```python
slot_id = "prop-water-jar-01"
BATCH35_SOURCE_SHAS[slot_id] = "0" * 64
PROP_SLOTS_V2[slot_id] = PROP_SLOTS_V2[slot_id].model_copy(
    update={"source_image_sha256": "0" * 64},
)
rebuilt = build_prop_geometry_v2_plan()
assert rebuilt.slot_plans[slot_id].source_image_sha256 == "0" * 64
```

The same root cause applies to `REQUIRED_SEMANTIC_KINDS`,
`EXACT_COUNT_KINDS`, `MIN_COUNT_KINDS` and
`CANONICAL_ALLOWED_INTERSECTIONS`. Consumer discipline is not an adequate
replacement for structural immutability at this provenance boundary.

Affected boundary:

- `pipeline/synthetic_village/prop_geometry_v2.py:79`
- `pipeline/synthetic_village/prop_geometry_v2.py:100`
- `pipeline/synthetic_village/prop_geometry_v2.py:122`
- `pipeline/synthetic_village/prop_geometry_v2.py:128`
- `pipeline/synthetic_village/prop_geometry_v2.py:160`
- `pipeline/synthetic_village/prop_geometry_v2.py:558`
- `pipeline/synthetic_village/prop_geometry_v2.py:1245`

### [P2] Boolean coordinates are silently coerced to metres

The numeric tuple fields are not strict. For example,
`local_transform_m=(True, 0, 0)` validates and becomes `(1.0, 0.0, 0.0)`.
This is an ambiguous input/type error that should fail closed before geometry
emission. Reject `bool` explicitly on metric and rotation tuple components;
do not apply a global strict mode unless JSON round-trip compatibility is
verified.

## Why

The project requires provenance and trust claims to be derived only from
machine-verifiable evidence. A design-only plan that can be promoted with one
unchecked copy call, or whose locked source SHA can be rewritten in place,
does not satisfy that rule even if current callers behave correctly.

Blender emission would turn this pure-model defect into persisted geometry and
collision evidence, so the correct stop point is before emission rather than
after private renders are produced.

## Tradeoff

The smallest safe repair is:

1. replace `model_copy(update=...)` at trust-bearing boundaries with an
   explicitly reconstructed payload followed by `model_validate`;
2. expose immutable mappings, including immutable nested count tables;
3. return a deeply immutable plan view, or a fresh fully revalidated plan whose
   public container cannot be mutated;
4. reject boolean components before numeric coercion.

`MappingProxyType` is suitable for module-level tables, but it may need an
explicit serializer adapter. A tuple-of-key/value entries is more naturally
deeply immutable but changes the model shape. Because this candidate is not
accepted or emitted yet, correctness is more valuable than preserving its
current content SHA.

Do not solve this by merely hiding names behind underscores: Python callers can
still reach the objects, and the returned plan itself would remain mutable.

## Open Questions

1. Should `slot_plans` remain a JSON object externally while using an immutable
   runtime representation, or may the pre-acceptance schema change to a tuple?
2. Is `model_copy` intended to remain part of the supported public API? If so,
   `_Frozen` needs an explicitly validated replacement/override; otherwise all
   project callers must avoid it and adversarial tests must lock that boundary.
3. Should boolean rejection apply only to metric/rotation tuples, or to all
   numeric contract fields in the synthetic-village schemas?

These questions do not change the stop condition: silent trust promotion and
post-validation contract mutation must both be impossible before sign-off.

## Next Action

GLM should add RED tests that prove:

- `plan.model_copy(update={"stage": "accepted"})` cannot produce a promoted
  plan;
- removing or replacing a `slot_plans` entry fails;
- source SHA and semantic-count/intersection contract tables cannot be mutated;
- `finalize_for_build()` reconstructs and re-validates the complete slot;
- booleans are rejected in metric and rotation tuples;
- canonical serialization remains deterministic after the repair.

After the implementation is green, return the new plan SHA/size, fresh focused
test and Ruff outputs, and this review can be repeated. Until then:

```text
prop-geometry-v2 pure model: changes requested
Blender emission: blocked
web/data / registry / Release promotion: blocked
```
