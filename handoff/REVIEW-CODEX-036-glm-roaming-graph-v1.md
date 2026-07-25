# REVIEW-CODEX-036 — roaming graph v1 Preview clearance

Date: 2026-07-25
Reviewer: Codex
Verdict: **accepted for Preview integration**

## Accepted scope

The checked-in canonical fixture has SHA-256
`69fe973870937e838a0d8e6876c519200e371aa03820c8a628649e7385ace8e8`
and contains:

- 2 modeled-unverified rooms;
- 1 plan-declared portal;
- 2 reciprocal directed edges;
- 0 route loops.

The producer, Blender manifest emitter, host driver and browser consumer agree
on the same fail-closed schema. Duplicate scene identifiers now fail instead of
silently selecting the first object, and missing room/portal fields produce a
structured error before publication.

## Fresh gates

```text
tests/test_roaming_graph.py: 44 passed
web/viewer roaming graph tests: 7 passed
Ruff: clean
fixture SHA-256: 69fe973870937e838a0d8e6876c519200e371aa03820c8a628649e7385ace8e8
```

## Trust boundary

Only the collision-proxy geometry SHA is measured from Blender. Portal
endpoints and clearance remain plan declarations. This graph is useful for
Preview navigation labels and room jumps, but it does not prove continuous
walkability, collision safety, 360-degree coverage, metric alignment or a real
captured scene.
