# FEEDBACK — REVIEW-CODEX-003 world bounds UX

Date: 2026-07-17
Owner: Codex (Studio / Viewer lane)
Source: `handoff/REVIEW-CODEX-003-render-on-demand-integration.md`, item 4

## Outcome

The low-priority coordinate-envelope ambiguity is resolved without changing
pipeline geometry or provenance:

- malformed coordinates and invalid LOD remain HTTP 400
  `invalid_world_chunk_request`;
- valid integer coordinates outside the mock world's renderable WGS84 envelope
  return HTTP 422 `world_bounds_exceeded`;
- other internal layout validation failures return HTTP 500
  `world_chunk_render_failed` instead of being blamed on the client;
- Viewer retains the original five-second retry policy for network, 5xx, and
  unrelated validation failures;
- Viewer permanently suppresses retries only for the explicit 422
  `world_bounds_exceeded` response.

## Root cause

`MockLayoutGenerator` derives `geo_origin` linearly from chunk coordinates.
Large but syntactically valid integer coordinates can therefore push latitude
or longitude beyond the bounds enforced by `ChunkLayout.geo_origin`. Pydantic's
`ValidationError` is a `ValueError`, and Studio's previous broad handler
collapsed this model-envelope failure into the same HTTP 400 used for malformed
route input.

Studio now recognizes only latitude/longitude upper- or lower-bound validation
issues as world-envelope failures. It does not classify unrelated Pydantic
issues as bounds errors.

## Evidence

- Real generator reproduction:
  - `(30500, 0)` and `(0, 32000)` validate;
  - `(30501, 0)` fails `geo_origin.lon <= 180`;
  - `(0, 32001)` fails `geo_origin.lat <= 90`.
- HTTP contract test covers both overflowing axes.
- Viewer policy test proves that only the exact structured 422 response is
  terminal.
- Viewer source contract proves that HTTP status and API code reach the retry
  policy and terminal set.

## Remaining honest limitation

The runtime manifest does not yet publish the complete generator coordinate
envelope. Viewer therefore stops after the first rejected request for each
individual out-of-envelope chunk; it cannot pre-clamp navigation before that
request. Publishing an authoritative envelope belongs in the pipeline grid
contract so Studio does not duplicate generator constants.
