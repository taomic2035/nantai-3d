# FEEDBACK-HANDOFF-CODEX-011 — Phase 4.4 central caller canary

> Date: 2026-07-22  
> Scope: `HANDOFF-CODEX-011` P1-3 / `FEEDBACK-HANDOFF-OPUS-009` caller checklist  
> Result: one central role canary passed the measured caller gates  
> Trust: unchanged — `synthetic=true`, `verification_level=L0`,
> `geometry_usability=preview-only`, `simplified-pbr-not-render-parity`,
> `trust_effect=none`

## Outcome

`central-courtyard-downhill` is the first reciprocal role with a complete
Windows Blender caller chain:

1. fresh exact-218 build;
2. fresh Phase 4.3 mesh probe;
3. canonical ground-node binding;
4. replacement of one slot in the 180-camera production plan;
5. fresh 25-ray clearance preflight;
6. six-layer Blender render;
7. post-render v2 quality evaluation;
8. explicit target-instance visibility check.

All machine gates above passed for `camera-ground-route-028`. This is a
quality acceptance of one modeled canary only. It is not evidence of measured
geometry, real-photo texture, render parity, SfM/3DGS coverage, or 360-degree
walkability.

## Fixes required before the canary could run

- Restored `topology_proxy_count=6` to the reciprocal preflight/render lineage
  contract. A wrong count now fails closed.
- Kept the six topology proxies visible to the dedicated mesh probe but hid
  them from production clearance rays, RGB and masks. The generic renderer
  now validates their exact low-trust identity instead of treating them as
  canonical or auxiliary render meshes.
- Derived role-camera placement from the module's ordered part layouts. A
  module move can no longer leave its role camera behind or vertically stale.
- Moved the central canary passage to a free contour near
  `central-ground-east`, rather than duplicating the existing courtyard
  environment module.
- Fixed rotated passage geometry: yaw now rotates the left/right wall centre
  offsets as well as the wall boxes. The previous behaviour made a 270-degree
  corridor report false 0.05 m clearances.
- Added `require_reciprocal_visible_instances`. A role caller must declare a
  non-empty sorted instance set and every declared ID must be present in the
  measured instance-mask statistics. This closes the observed case where all
  generic quality rules passed while the target module had zero pixels.

## Fresh build and mesh evidence

| Identity | Value |
|---|---|
| build ID | `84bf97e35e309fc6ddff30b31f9514c8a3ffa6c203f09ed4c13c52e4203e3cc9` |
| reciprocal plan SHA-256 | `916a66ce0a952bb4f3c3c55c9e4b998630bb2c1d65a7d68c058e6df76597df1b` |
| runtime script SHA-256 | `d7a786c6f2228b3faf448c6ead6c5b87c8f11c37da8129e7f50ac8f4a94fa690` |
| `.blend` SHA-256 | `4e7e88158589535c2385558a6669410f7611b120688bd1381574478e3c1fa9e2` |
| build-report SHA-256 | `71a21aa47feb9039e649eab5d652bc7564cad240284e24ca340f72f6d423ac50` |
| persisted probe-report SHA-256 | `3366b4b48101bd4fb539b93870c953046d09ee9c273273e1d9f6641013a9e232` |

The fresh probe reported:

- module routes: `6/6` pass, each minimum upward clearance approximately
  `2.501 m`;
- module-module intersections: `15/15` pass;
- module-environment intersections: `6/6` pass;
- topology attachment: `6/6` pass, each measured distance `1.75 m`;
- `overall_passed=true`.

The central floor is flat at `z=77.753 m`, starts approximately 0.1 m above
the actual Blender terrain mesh, and follows world `+x` at `y=40 m`. The
standing-eye candidate is `(25.0, 40.0, 79.353)`, 14.836 m from canonical
ground node `central-ground-east` on `path-network-003`.

## Caller evidence

| Identity | Value |
|---|---|
| target production slot | `camera-ground-route-028` |
| production plan SHA-256 | `7e53db6beb3eeaf6bd4fc5ebf6e8f884485e6666baac0740113a73f0db31ef6d` |
| camera registry SHA-256 | `0a06f9767f22741f98800c76ebd3045199e5f36e226d555099e78f733806330d` |
| preflight ID | `2bf1b5df42e26a262e800871ad07b04c807f14c49bc30eae715be0210a67d3be` |
| preflight-report SHA-256 | `db6e56f540ad690cd730db763c39d88cb624724b0efc83e7c97616f2975f912d` |
| render ID | `d40afde1bc3f3972eab96a571cb9dc951404863919acdcc32abe5f78b471e89d` |
| frame-report SHA-256 | `e071947d94e26254f2b72f4d970357768f541bb7861a27deee7f455cf6be823e` |
| journal SHA-256 | `40b93ee6cb46bd5aed3248d2e2b9e2b6af0ce8ab5a13876b6c60e06ad2b05e6b` |
| quality-request SHA-256 | `fcb1daaeeb32a13666c5c09047818d8f15035c1455e4b9a4cd7798ad6eaa276b` |
| quality-report SHA-256 | `3878ea3b8d56e243e4ba901953f68e42cc937bc5ff476199153eda9ae447f1f2` |

Clearance preflight measured `0` upper/middle hits under 2 m. Post-render v2
passed all eight rules:

| Measurement | Value | Gate |
|---|---:|---:|
| valid depth / normal / semantic ratio | `0.780750` | minimum `0.30` |
| sky ratio | `0.219250` | maximum `0.55` |
| upper-ground ratio | `0.017181` | maximum `0.30` |
| near-depth ratio | `0.000000` | maximum `0.35` |
| near-instance dominance | `0.000000` | maximum `0.70` |
| upper-instance dominance | `0.355625` | maximum `0.70` |

Measured instance-mask counts for the required central segment were:

```text
176=114396  177=27834  178=12230  179=3697
180=1416    181=820    182=300    total=160693
```

Thus every required central instance is present. The prior canary with zero
pixels for 176-182 would now be rejected by the caller.

## Visual review

The RGB proves that the camera sees and enters the intended passage. It also
shows the current fidelity ceiling clearly: the reciprocal passage is a dark,
box-like procedural tunnel with coarse material aliases, and nearby terrain
and buildings remain synthetic. This frame is suitable for caller/quality
contract validation, not for claiming a real village model or real texture.

## Remaining blockers

The other five default role placements cannot be accepted yet. Their nearest
canonical ground-node distances are:

| Role | Nearest ground-node distance |
|---|---:|
| bridge deck | `125.783 m` |
| watermill tailrace | `159.017 m` |
| covered gallery | `31.268 m` |
| forest/orchard boundary | `51.384 m` |
| lower valley uphill | `162.389 m` |

The binding maximum is 30 m. Therefore `camera-ground-route-010` and `039`
must not be replaced with the bridge candidate yet: there is no canonical
ground `WalkableNode` near that module. The 1.75 m topology proxy measurement
is auxiliary probe geometry and cannot be substituted for a canonical node.

Next work must either add measured/canonical topology nodes near those modules
or relocate each module onto existing canonical topology and terrain, then
repeat fresh build, mesh probe, preflight, six layers, target visibility and
post-render v2. No filename or role-name inference may bypass this blocker.

