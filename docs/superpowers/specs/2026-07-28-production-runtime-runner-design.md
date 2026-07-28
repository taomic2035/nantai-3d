# Production Runtime Runner Design

Date: 2026-07-28  
Status: user-approved direction A; written specification pending final review

## Problem

The Production builder currently copies the repository development `make.py`
into the public runtime while its script allowlist includes only
`scripts/verify_production_release.py`. The packaged help therefore advertises
23 repository targets, including build, privacy-audit and asset-staging commands
whose scripts and private inputs are intentionally absent from the runtime.

This is a self-containment defect, not a reason to expand the public package.
The formal runtime needs only an integrity check and a local server. Repository
maintenance commands must remain in the source workspace.

## Selected approach

Add one tracked runtime-only runner template:

```text
release/production-runtime-runner.py
```

During Production archive construction, map that file to package-root
`make.py`. Do not copy repository-root `make.py` into the runtime. Keep the
repository runner unchanged for development, building, privacy audits and final
asset staging.

The packaged runner exposes exactly:

```text
python make.py help
python make.py verify
python make.py serve
```

No other target is accepted or displayed.

## Runtime command contract

### `help`

Print concise ASCII-safe usage and the exact two actionable targets, `verify`
and `serve`. Return zero without importing application modules or inspecting
private state.

### `verify`

Run the bundled standard-library verifier against the extracted package root:

```text
<current-python> scripts/verify_production_release.py . --json
```

Use the interpreter executing `make.py`, the package root as the working
directory and a UTF-8-safe child environment. Propagate a nonzero verifier exit
status unchanged. Do not accept an archive override, acceptance root, privacy
policy or external scene path.

### `serve`

Start only the bundled Studio server:

```text
<current-python> -m pipeline.studio_server --host 127.0.0.1 --port 8000
```

Use the package root as the working directory and propagate failures. Do not
accept `REAL_SCENE_IMPORT_ROOT` or any repository development override. The
Studio/Viewer continue to consume the package-bound scene and independently
project invalid package state fail closed; the runner does not promote scene
trust.

The documented operator order remains `verify` before dependency installation
or serving. `serve` is not a substitute for the explicit downloaded-byte
verification gate.

## Builder integration

Update the runtime source mapping in
`pipeline/production_release_builder.py`:

- repository `make.py` is not a runtime source;
- `release/production-runtime-runner.py` maps to destination `make.py` with
  role `runtime-runner`;
- the destination allowlist still requires exactly one root `make.py`;
- source cleanliness covers the runtime template;
- portable destination identity and duplicate checks remain unchanged;
- the runtime continues to include only
  `scripts/verify_production_release.py` from `scripts/`.

No receipt schema change is required. The runner bytes remain an ordinary
content-addressed artifact in `PRODUCTION-RELEASE.json` and
`SHA256SUMS.txt`.

## Security and trust boundary

The public runtime must not acquire:

- the Production builder CLI;
- privacy policy parsing or private needles;
- final four-asset staging tools;
- acceptance workspaces, control points or remote configuration;
- Preview build/verify commands;
- tests, lint, asset generation or repository cleanup targets.

Unknown targets return exit code 2 and an ASCII-safe error. The runner has no
default that can build or promote `v1.0.0`, and its operation keeps
`scene_trust_effect=none`.

## Documentation

Update:

- `release/production-verify-and-run.md` to use `python make.py verify`;
- `docs/manual/production-runtime-release.md` to distinguish repository
  maintenance commands from the three packaged runtime commands;
- documentation contract tests so the distinction cannot regress.

The repository-side commands remain:

```text
build-production
verify-production
audit-production-privacy
stage-production-assets
verify-production-assets
```

They are not advertised as commands inside an extracted runtime.

## Verification

Tests must prove:

1. the builder maps the runtime template to package-root `make.py`;
2. repository `make.py` bytes and maintenance targets are absent from the
   packaged runner;
3. the only accepted arguments are `help`, `verify` and `serve`;
4. `verify` invokes the bundled verifier with exact arguments and propagates
   its exit status;
5. `serve` binds only loopback port 8000 and rejects private import overrides;
6. an unknown or combined target fails before any subprocess;
7. a freshly built complete runtime can run its bundled verifier from a clean
   extracted directory without creating an unexpected-file failure;
8. tree and ZIP verifiers still bind the runner bytes through the receipt;
9. Preview behavior and repository `make.py` remain unchanged;
10. focused Production tests pass on Ubuntu, Windows and macOS.

The cold-start test is contract evidence only when it uses modeled data; it
must not be described as a real Production scene.

## Alternatives rejected

### Include all maintenance scripts

Rejected because it expands the public attack surface, contradicts the release
whitelist and still cannot provide private acceptance or privacy inputs.

### Keep the development runner and hide targets dynamically

Rejected because the runtime would continue to ship unrelated implementation,
environment-variable parsing and repository-only behavior. A dedicated runner
has a smaller, auditable contract.

## Completion boundary

This change closes runtime command self-containment only. It does not satisfy
the remaining real-scene gates: rights-cleared capture, accepted real-photo
SfM, non-mock CUDA 3DGS, measured alignment, or real Viewer/human acceptance.
It must not create the final tag or Release.
