# Production CUDA OCI Runtime Design

Date: 2026-07-29

Status: approach A confirmed by the user; written specification awaiting approval

## Problem

Nantai already has a fail-closed remote caller, fresh-container runtime
clearance, prepared real-scene bundles, Nerfstudio Splatfacto training,
held-out rendering and verified result fetch. The remaining repository-owned
runtime blocker is that there is no Nantai-published CUDA image whose exact
dependency bytes, build origin and operator-facing policy values are known.

The upstream Nerfstudio `1.1.5` image is useful evidence, but it uses Python
3.10 while this repository requires Python 3.11. It also does not publish the
Nantai-specific runtime probe needed to fill
`nantai.production-runtime-policy-input.v1`. An operator therefore has no
single trusted artifact from which to obtain the immutable image digest,
Python and CLI executable hashes, CLI schema hash, or the exact Torch/gsplat
versions.

Publishing an image does not satisfy the external production gates. A
published image is still `modeled-unverified` until the same digest passes
fresh GPU clearance and produces an accepted non-mock training result.

## Selected architecture

Build and publish one repository-owned, Linux AMD64 CUDA runtime:

```text
locked source inputs
  -> multi-stage CUDA image build
  -> no-GPU image contract probe
  -> GHCR image@sha256 digest
  -> SBOM + build-provenance attestations
  -> detached canonical image receipt
  -> operator runtime policy
  -> existing fresh-container GPU clearance
  -> existing training / held-out render / verified fetch
```

The default registry name is:

```text
ghcr.io/taomic2035/nantai-3d-production-cuda
```

Consumers must use `image@sha256:<64-hex>`. Tags are navigation aids only and
are never accepted as runtime identity.

The image contains the dependency runtime, not a mutable copy of Nantai source
or private scene data. The existing remote worker continues to mount the exact
clean repository at `/workspace` read-only and the job boundary at `/job`
read-write. Runtime networking remains disabled.

## Locked runtime

The first runtime contract is intentionally narrow:

| Component | Required identity |
|---|---|
| Platform | `linux/amd64` |
| Build base | `nvidia/cuda:11.8.0-devel-ubuntu22.04@sha256:94fd755736cb58979173d491504f0b573247b1745250249415b07fefc738e41f` |
| Runtime base | `nvidia/cuda:11.8.0-runtime-ubuntu22.04@sha256:eaaccb3528ceca110601131434ab467e41d694a41e8c9bf280fb27ac18fcb29b` |
| Python | CPython `3.11.9` source tarball, SHA-256 `9b1e896523fc510691126c864406d9360a3d1e986acbda59cda57b5abda45b87` |
| Torch | `2.1.2+cu118`, CPython 3.11 Linux AMD64 wheel, SHA-256 `051833f6174e672eb313ee1c70dbcaf97e558dc46237215407933d28f40bca85` |
| Torchvision | `0.16.2+cu118`, CPython 3.11 Linux AMD64 wheel, SHA-256 `9a784073e801c04066a5e4453306010b67bacfbff12bd57e5d65c1a638584a89` |
| Nerfstudio | `1.1.5` wheel, SHA-256 `ee6d3d360a1e363ad2f1703b602da5a8987485bff812d0ae8aa4a6e672b994c4`; upstream tag commit `6b60855003011b2ca23c2fe3f8e2ca6314c69924` |
| gsplat | `1.4.0` sdist, SHA-256 `8aa81a785e0daf3ed60d0b9930a56c0f337280e6989351d1f1b74e21cf190160`; upstream tag commit `4d3a3b69db4de0326f983ccf7b7b255271a17b01` |
| NumPy | `<2.0`, exact resolved version and wheel hash in the Python lock |

These Torch and gsplat versions follow the upstream Nerfstudio `1.1.5`
Dockerfile and package metadata. Python differs deliberately because Nantai
requires Python 3.11.

All transitive Python artifacts must appear in a generated hash-locked file.
Installation uses `pip --require-hashes` with no unbounded secondary resolver.
The gsplat build runs only after the pinned Torch wheel is installed and uses a
fixed CUDA architecture set:

```text
7.5;8.0;8.6;8.9;9.0+PTX
```

The set covers the intended T4, A100, RTX 30/A10, RTX 40/L4 and H100 families.
Supporting a GPU outside that set requires a new image receipt and digest; it
is not a runtime override.

The exact base manifests, source archives, direct wheels, Python lock, CUDA
architectures and runtime OS packages live in one canonical
`containers/production-cuda/runtime-lock.json`. Docker build arguments may
select only values already present in that lock. The build fails if a
downloaded artifact or resolved dependency differs.

The two listed base digests identify multi-platform manifests. The workflow
fixes `linux/amd64` and records the selected AMD64 child-manifest digest in the
probe and detached receipt. Apt sources use a fixed Ubuntu snapshot timestamp,
and every installed package uses the version recorded in the lock; a moving
Ubuntu mirror is not an accepted dependency resolver.

## Image construction

Add:

```text
containers/production-cuda/Dockerfile
containers/production-cuda/runtime-lock.json
containers/production-cuda/requirements.lock
containers/production-cuda/README.md
```

The Dockerfile has two stages.

### Builder stage

The builder:

1. starts from the digest-pinned CUDA devel base;
2. installs only the compiler and CPython build prerequisites;
3. verifies the CPython tarball before extraction and builds Python 3.11.9;
4. installs direct and transitive Python dependencies from the hash lock;
5. compiles gsplat for the fixed architecture set;
6. verifies package versions and imports;
7. removes pip caches, build trees and credentials.

No `curl | sh`, floating Git branch, mutable `latest`, unpinned `pip install`,
runtime package installation or secret build argument is allowed.
PEP 517 build dependencies are present in the same lock, and gsplat is built
with isolation disabled so it cannot open an untracked secondary resolver.

### Runtime stage

The runtime:

1. starts from the digest-pinned CUDA runtime base;
2. installs the small fixed set of shared libraries needed by Torch,
   Nerfstudio and held-out rendering;
3. copies the built CPython runtime and installed packages;
4. exposes `python` as a regular executable file, not a symlink, because the
   production clearance hashes and reopens that exact executable;
5. retains regular-file `ns-train` and `ns-export` console entrypoints;
6. embeds a read-only copy of `runtime-lock.json` and its SHA-256 under
   `/opt/nantai/`;
7. sets OCI source, revision, version, license, base-image and lock labels;
8. has no baked credentials, private host data, capture data or trained model.

The image does not need COLMAP for the formal remote path. The accepted SfM
model is converted into the prepared bundle before submission; the production
container consumes that bundle. The old ad-hoc mode that runs
`ns-process-data` is not an image acceptance path.

## Image probe and detached receipt

Add a repository-owned no-GPU probe and a strict receipt model:

```text
cloud/probe_production_cuda_image.py
pipeline/production_cuda_image_release.py
```

The probe runs inside the just-built image with `/workspace` mounted
read-only. It must derive, not accept as success flags:

- Python, Torch, compiled Torch CUDA, Nerfstudio and gsplat versions;
- SHA-256 and byte length of the regular `python`, `ns-train` and `ns-export`
  executables;
- the full `ns-train splatfacto -h` option set and its existing canonical
  schema SHA;
- the embedded runtime-lock bytes and SHA;
- import success for the modules used by training and held-out evaluation.

It explicitly does not require `torch.cuda.is_available()` on the GitHub-hosted
builder. That observation belongs to the existing fresh GPU clearance.

After GHCR returns the pushed manifest digest, a host-side producer combines
the digest, exact source commit, Dockerfile and lock hashes, probe output and
workflow identity into canonical:

```text
nantai.production-cuda-image-release.v1
```

The receipt includes the image-derived values needed by
`nantai.production-runtime-policy-input.v1`:

- immutable `expected_container_identity`;
- expected CUDA, Python and Nerfstudio versions;
- expected Python and `ns-train` executable hashes;
- expected training CLI schema SHA and required options.

Host-specific facts remain external: GPU UUID, minimum memory policy,
container-runtime executable hash and `nvidia-smi` executable hash. The receipt
must not invent them.

The receipt is detached because an image cannot contain its own final manifest
digest without a circular identity. Both probe and receipt loaders reject
duplicate keys, noncanonical JSON, placeholders, unknown fields, unsafe text
and content-SHA disagreement.

## GitHub publication workflow

Add a manually dispatched workflow:

```text
.github/workflows/production-cuda-image.yml
```

It runs only from an exact `main` commit and:

1. checks out that commit with persistent credentials disabled;
2. validates the lock and Dockerfile before login;
3. logs into GHCR with the short-lived `GITHUB_TOKEN`;
4. builds only `linux/amd64` with BuildKit;
5. pushes a source-commit tag and records the returned manifest digest;
6. pulls and probes the exact digest with runtime networking disabled;
7. publishes BuildKit SPDX SBOM and `mode=max` provenance attestations;
8. publishes a GitHub artifact attestation for the image digest;
9. emits and attests the canonical detached image receipt;
10. uploads only the final receipt and verification summary as workflow
    artifacts.

Required workflow permissions are minimal and job-scoped:

```text
contents: read
packages: write
id-token: write
attestations: write
```

Actions are pinned to full commit SHAs. Build cache may improve speed, but no
cache hit is trusted: all downloaded artifact hashes, the final image probe
and the published digest are rechecked.

No `latest` tag is created. A later convenience tag may point to an accepted
digest, but production policy always stores the digest and detached receipt
SHA. Re-running the workflow does not imply bit-for-bit reproducibility; each
output is judged by its measured digest, lock, probe and attestations.

## Operator integration

The documented operator flow becomes:

1. download the detached receipt from the successful workflow;
2. verify its GitHub attestation against this repository;
3. verify the GHCR image attestation and SBOM for the same digest;
4. combine receipt-derived image facts with approved host/GPU facts;
5. generate the existing private production runtime policy;
6. run the existing remote readiness checker;
7. submit the existing remote job using the receipt's exact image digest.

The existing worker still:

- resolves the immutable image reference to a content image ID;
- creates a fresh container with `--gpus all`, `--network none` and
  `no-new-privileges`;
- verifies `.Image` and `.Config.Image`;
- runs production clearance before training;
- binds executable hashes, GPU identity, CUDA/Python/Nerfstudio versions and
  CLI schema;
- stops before training on any rejected or ambiguous observation.

There is no default Production image hidden in source configuration. An
operator must explicitly select and verify one detached receipt.

## Verification levels

### Level 0: repository contract

Tests prove:

- every source, wheel and base image is digest/hash pinned;
- the Dockerfile consumes only lock values;
- runtime `python`, `ns-train` and `ns-export` must be regular files;
- probe and receipt canonicalization fail closed;
- receipt fields map exactly to runtime-policy fields;
- no tag-only image can enter a policy;
- no credentials, private paths or scene data are in the build context.

### Level 1: published image contract

The manual workflow proves:

- the image builds for Linux AMD64;
- the exact pushed digest can be pulled;
- package versions, executable hashes and CLI schema match the lock;
- the container works with network disabled for no-GPU imports and help
  probes;
- SBOM and provenance attestations exist for the same digest;
- the detached receipt is canonical and separately attested.

This level remains `modeled-unverified` for real training.

### Level 2: fresh GPU runtime

On the approved external host, the existing clearance must accept the exact
digest and expected GPU. This proves usable NVIDIA scheduling,
`torch.cuda.is_available()`, GPU memory, driver/CUDA compatibility, executable
identity and CLI compatibility for that run.

Failure is `rejected` or `unknown`; it never falls back to host Python,
uncontained training or an alternate image.

### Level 3: non-mock training

The same accepted runtime must train the rights-approved scene or the internal
canary, export a valid PLY, render held-out cameras and return a verified result
bundle. Only this level closes the repository/runtime part of the non-mock
CUDA gate. It still does not prove measured alignment or Viewer/human quality.

## Documentation changes

Implementation updates:

- `docs/manual/reconstruction-setup.md` with receipt verification and policy
  population;
- a focused `docs/manual/production-cuda-image.md`;
- `docs/production-v1-status.md` with the precise verification level reached;
- `docs/README.md` with one link;
- `README.md` only if the top-level runtime status changes.

The examples must use CUDA `11.8` for this receipt rather than the current
illustrative `12.8`. Test fixtures may continue to use synthetic version
values when they are explicitly fixtures.

## Failure and cleanup semantics

- A build or probe failure publishes no accepted receipt.
- A pushed but unprobed digest remains an unaccepted registry artifact.
- A missing SBOM or provenance attestation blocks receipt publication.
- A receipt/image digest mismatch blocks policy creation.
- Workflow artifacts contain no intermediate wheels, caches, logs with
  environment dumps or credentials.
- Existing remote residue and audit-retention behavior is unchanged.
- Registry cleanup is a separate maintenance action; the build workflow never
  deletes prior evidence to hide a failed attempt.

## Alternatives rejected

### Use the upstream Nerfstudio image directly

Rejected as the formal default because it uses Python 3.10 and does not emit
the Nantai policy values or detached receipt. It remains a useful comparison
artifact.

### Install Nerfstudio on each GPU host

Rejected because dependency resolution can drift, training requires network
access, and host state cannot be bound to one immutable image digest.

### Bake Nantai source and scene data into the image

Rejected because source is already independently bound and mounted read-only,
while scene data is private and job-specific. Baking either would couple every
job to a large image and expand the release/privacy boundary.

### Publish automatically on every `main` commit

Rejected because the image is large and expensive to build, most commits do
not change the runtime lock, and publication is a material external mutation.
Fast static checks remain in ordinary CI; full image publication is explicit.

## Completion boundary

Implementation is complete when the repository can publish one attested GHCR
digest and detached receipt, and the no-GPU image contract passes from pulled
bytes.

The CUDA blocker is closed only after that same digest passes Level 2 and
Level 3 on a real external NVIDIA GPU. Production V1 remains blocked until the
same scene also has rights-cleared capture, accepted real-photo SfM, measured
alignment and real Viewer/human acceptance.

## Primary upstream references

- [Nerfstudio v1.1.5 release](https://github.com/nerfstudio-project/nerfstudio/releases/tag/v1.1.5)
- [Nerfstudio v1.1.5 Dockerfile](https://github.com/nerfstudio-project/nerfstudio/blob/6b60855003011b2ca23c2fe3f8e2ca6314c69924/Dockerfile)
- [Python 3.11.9 release](https://www.python.org/downloads/release/python-3119/)
- [GitHub container publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
