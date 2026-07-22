# FEEDBACK-HANDOFF-CODEX-026 — Batch 21 Watermill Construction

Date: 2026-07-23

## Outcome

Batch 21 now has a plan-bound, exact-218 Blender consumption path for the
existing environment waterwheel. The watermill role no longer renders as the
Batch 20 generic portal boxes: it uses an open timber mill frame, supported
maintenance platform, five-level split stair, faceted bearing, creek-bank
drainage path, reinforced guard and open tailrace portal.

No canonical root, existing instance ID, semantic ID or material alias was
replaced. The environment waterwheel remains instances `155..160`; the
reciprocal watermill role remains instances `189..195`.

The accepted Batch 21 design inputs remain replaceable, design-only references
in Release
`synthetic-village-design-inputs-batch21-2026-07-23`. They do not form a
multiview reconstruction set and do not increase geometry trust.

## Plan and runtime changes

- `LowerBridgeRecipe.waterwheel_assembly_anchor_m` is a strict finite canonical
  anchor. The six existing waterwheel meshes are derived from it.
- `WatermillTailraceRecipe` copies that anchor; all seven reciprocal role parts
  are deterministic offsets from it.
- The standing-eye role camera composition includes the bound wheel anchor.
- Exact part-ID dispatch adds deterministic construction geometry while generic
  family builders remain the fallback for all other parts.
- Open-route geometry keeps the unchanged Phase 4.3 `1.2 m` width and `2.4 m`
  clearance contracts. Stair treads and creek-bank stones use a central
  maintenance/drainage slot so they do not occupy the probe origin.
- Legacy v1 serialized artifacts remain byte-compatible when the new anchor was
  absent; newly authored plans bind the anchor in canonical bytes.

## Fresh machine identities

### 175-root environment build

- environment module plan SHA-256:
  `8217b1be455480f2c42f3135bee8cff70921aa7ef0c007fa0457afb9cc8c9bfc`
- build ID:
  `0722380bda077e7711cb1ffc1873f249f62c64789d9619313f9ef01481a5abd1`
- request SHA-256:
  `3216dac9058e9ac66fa691209f6ddaa81cf8c1cb3bdd0223055fd7e70bfa0930`
- report SHA-256:
  `ca9c387fd1caddc3be04fb59d8a2a324a5cdfaa2793c32f30d42c14614d1c9fa`
- `.blend` SHA-256:
  `83981bbc03447bdee02b733c27ebf8286b9ec1f4c9ad621005e691a920cd4f0b`
- measured counts: `130` base roots + `45` environment module roots =
  exact `175`; `45` module mesh objects.

### Production-bound exact-218 build

- reciprocal plan SHA-256:
  `9a8d60702306e5df404ac0cada316da79d4432ace02aea3aa7bf6b050774e9e0`
- runtime script SHA-256:
  `0cd51266868e4ee050ffce6963bf91abc9b45440a0f9a014c4e17bf019ca6c2f`
- build ID:
  `38318f5fc60840f5eed4ecb0f219e8b300f2c453f61504c613f7ec7e0a9a9e23`
- request SHA-256:
  `f3cfd3c40838b52e012887cbe4d3996fbafd249077494b6e7823559419b3ad4e`
- report SHA-256:
  `16802c25363360aa8a0118c290014c07998960ccff39e90438656f79477a5dc2`
- `.blend` SHA-256:
  `6d33955b883e0ae289eb5c8d81ba18fd1536df8502c73ce79e2377763e6f9736`
- measured counts: `175` base roots + `43` reciprocal roots = exact `218`;
  `43` reciprocal mesh objects.

The first private caller attempt correctly failed before rendering because its
reciprocal candidates had placeholder all-zero production bindings. The harness
was corrected to build the reciprocal plan with the current 180-camera
production plan. The accepted exact-218 build above is the rebuilt,
production-bound artifact; the rejected unbound artifact is not acceptance
evidence.

## Fresh Phase 4.3 evidence

- canonical report SHA-256:
  `379cc45c1c768b6f23ea3a1c7340b73d3d7428ad0b677cb049b4a0a864c4d240`
- persisted report file SHA-256:
  `2bbe8055f943d12f3598fb68d8c617b40b6566d7dcca9b07db5036a292d52873`
- route probes: `6/6`
- module-pair intersections: `15/15`
- module/environment intersections: `6/6`
- topology attachments: `6/6`
- `overall_passed=true`

Two intermediate fresh builds were rejected while preserving the thresholds:
one exposed a stair/stone upward-ray hit, and one exposed a creek-stone
coplanar side-ray hit. The final build uses geometry corrections rather than a
policy relaxation.

## Six-role render and watermill review

- batch ID:
  `6c5b46cadfcf37862439c662a8d6dfb99a32b55380d9ac860284553e239def9c`
- declared journal SHA-256:
  `7a5e9b064845ac80ef88a21348772d0eea97079d9bee8d12ee8e9cf1909f48e3`
- accepted / failed / reused: `6 / 0 / 0`
- source production plan SHA-256:
  `54aced28d33adad63dcbb301be32ede28998e1d2996a0232b10a7df1f586cb3a`
- source camera registry SHA-256:
  `647d6bdb6df2f5d02445348e6f81efe7c3b893cd37e0548dfbcf465977bb22fa`
- object registry SHA-256:
  `c02c70d73860eac74267da52d9d1e0413c15d02a2e358eba3c1e237b48ca2edc`
- post-render v2 policy SHA-256:
  `b60eabd0c9cf069b23982bf2cfb9149ea25add8c6d76df39541d5642cf880b17`

Watermill accepted render:

- render ID:
  `e29615a3894a46b7053a9849286844fcff45142cf26f4a467bb45927a1d1ce78`
- role candidate SHA-256:
  `131768b5046bceebac7a11f3da53bb9748f4d8d3ebfe3d33352ef8b7aba01581`
- preflight report SHA-256:
  `e723e563a612ee4e9ae52aa9850ff0697824ac975b6a639612c25fa8d5ca69d5`
- render report SHA-256:
  `33c047089fe20dd4fbb9b859d2e127dd37a7e2fb8a1a3472032f09032929f2ba`
- post-render quality report SHA-256:
  `edbd6c0cfc6a289093294391b00ebcb9dd271671a77acd683fd22b869ff91a4a`
- RGB SHA-256:
  `cd655737a500e1f745ea3bb2cca773e2a2e652a4750d095ac5af233c1030cbc3`
- depth / normal / instance / semantic SHA-256:
  `134f90a21f13e8ece41e885d87fee50457c3bfff7a18ea1ef2f356fea715c459`,
  `761a37b374802efd666b77617d0c51d8a50a5d2e6336b048a9b794d752ebec0f`,
  `142e428e263915f940a6a785aea400ed943264a0f1ec57d622940b3bb31c07e3`,
  `ed74afe73e71f15c28d6efa2044583b60c362e70339583664a17afcfd9c51f10`.

The instance layer contains all reciprocal role IDs `189..195`. It also
contains the existing wheel assembly IDs `155..159`; the wheel itself
(`waterwheel-wheel-001`, instance `155`) occupies `3,801` pixels. Therefore the
wheel is materially present in the accepted frame rather than inferred from
camera intent. Instance `160` tailwater is outside this single frame.

Compared with Batch 20, the frame now exposes the mill frame, stepped access,
maintenance deck, guard, bearing/wheel silhouette and tailrace relationship.
The remaining visual defects are explicit: materials are still very dark,
surfaces are simplified and the wheel lacks production-detail readability.
This is a materially better construction blockout, not photoreal geometry or
photo-derived texture parity.

## Verification

Before the fresh builds:

```text
321 passed
Ruff: all checks passed
```

After the Phase 4.3 geometry correction:

```text
81 reciprocal runtime tests passed
Ruff: all checks passed
```

## Trust boundary and next work

All outputs remain `synthetic=true`, `verification_level=L0`,
`geometry_usability=preview-only`, `fidelity=simplified-pbr-not-render-parity`
and `trust_effect=none` or `none-quality-filter-only`. Phase 4.3 and post-render
v2 prove contract compliance for this modeled scene; they do not turn design
references into measured reconstruction, real photo texture, SfM coverage or
metric-aligned real geometry.

The next high-value step is visual fidelity rather than another caller rewrite:
improve waterwheel spokes/paddles and PBR material response, then repeat the
same content-addressed exact-218 → Phase 4.3 → six-role caller path. Real-scene
parity still requires actual capture, external SfM and external 3DGS/mesh
reconstruction inputs.
