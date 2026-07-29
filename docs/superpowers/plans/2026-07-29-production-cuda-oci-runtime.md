# Production CUDA OCI Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a digest-addressed Nantai CUDA 11.8 runtime with locked
Python/Nerfstudio/gsplat dependencies, a machine-derived policy receipt, SBOM
and build provenance, then connect that exact digest to the existing fresh GPU
clearance and non-mock training path.

**Architecture:** Keep Nantai source and private scene data outside the image.
Build a dependency-only Linux AMD64 image from locked artifacts, probe the
pulled digest without pretending a GitHub runner has a GPU, and emit a detached
canonical receipt containing the image facts needed by the existing runtime
policy. Continue to use the current read-only `/workspace`, writable `/job`,
network-disabled container and fresh GPU clearance before any training.

**Tech Stack:** OCI/Docker BuildKit, NVIDIA CUDA 11.8, CPython 3.11.9, PyTorch
2.1.2+cu118, Nerfstudio 1.1.5, gsplat 1.4.0, Pydantic v2, pytest, Ruff, GHCR,
SPDX SBOM, GitHub artifact attestations and GitHub Actions.

**Design:** `docs/superpowers/specs/2026-07-29-production-cuda-oci-runtime-design.md`

---

## File structure

- Create `pipeline/production_cuda_runtime_lock.py`: strict canonical schema
  and validator for all base-image, source, apt and Python dependency locks.
- Create `pipeline/production_cuda_image_release.py`: canonical no-GPU probe,
  detached image receipt and runtime-policy image-fact projection.
- Create `cloud/probe_production_cuda_image.py`: image-internal observation of
  versions, CLI schema, imports, lock identity and regular executable bytes.
- Create `scripts/emit_production_cuda_image_release.py`: host-side producer
  that binds the pushed digest and workflow/attestation identities to a
  verified probe.
- Create `containers/production-cuda/Dockerfile`: two-stage locked CUDA image.
- Create `containers/production-cuda/runtime-lock.json`: canonical top-level
  lock and content identity.
- Create `containers/production-cuda/requirements.in`: human-reviewed direct
  Python requirements.
- Create `containers/production-cuda/requirements.lock`: generated transitive
  Linux AMD64 Python lock with hashes.
- Create `containers/production-cuda/apt-build.lock`: sorted exact Ubuntu
  builder packages.
- Create `containers/production-cuda/apt-runtime.lock`: sorted exact Ubuntu
  runtime packages.
- Create `containers/production-cuda/README.md`: maintainer-only lock refresh
  and local build contract.
- Create `.github/workflows/production-cuda-image.yml`: manually dispatched
  build, probe, publish and attestation workflow.
- Create `docs/manual/production-cuda-image.md`: operator verification and
  private runtime-policy population.
- Create focused tests:
  `tests/test_production_cuda_runtime_lock.py`,
  `tests/test_production_cuda_image_release.py`,
  `tests/test_production_cuda_image_probe.py`,
  `tests/test_production_cuda_image_contract.py`, and
  `tests/test_production_cuda_image_workflow.py`.
- Modify `.dockerignore`: exclude private, generated, test and release
  material while retaining the exact probe/contract sources needed by the
  build.
- Modify `.github/workflows/ci.yml`: add fast cross-platform static/schema
  tests; do not build the large CUDA image in ordinary CI.
- Modify `docs/manual/reconstruction-setup.md`, `docs/production-v1-status.md`,
  `docs/README.md` and `README.md` only where the actual runtime status changes.
- Do not modify Preview releases, synthetic trust, final `v1.0.0` tagging,
  external host credentials, private runtime policy files or scene data.

## Global execution rules

- Work on shared `main`; inspect `git status --short --branch` before each
  task and preserve unrelated paths.
- Use TDD for every Python/workflow contract: observe RED, implement the
  smallest behavior, then rerun the focused suite.
- Stage only the paths named in the current task. Never use `git add -A`,
  `git commit -a`, reset, checkout, stash or rebase.
- Every Codex commit ends with:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

- Push each green checkpoint with:

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- A built or published image stays `modeled-unverified`. Only the existing
  fresh GPU decision and non-mock result bundle can raise runtime evidence.

### Task 1: Canonical runtime lock

**Files:**

- Create: `tests/test_production_cuda_runtime_lock.py`
- Create: `pipeline/production_cuda_runtime_lock.py`

- [ ] **Step 1: Write RED tests for the exact lock schema**

Create fixture builders and assertions for:

```python
def test_runtime_lock_is_canonical_content_addressed() -> None:
    lock = _valid_lock()
    payload = canonical_production_cuda_runtime_lock_bytes(lock)
    loaded = load_production_cuda_runtime_lock_bytes(payload)

    assert loaded == lock
    assert loaded.platform == "linux/amd64"
    assert {image.role for image in loaded.base_images} == {
        "builder",
        "runtime",
    }
    assert loaded.content_sha256 == hashlib.sha256(
        canonical_production_cuda_runtime_lock_signing_bytes(loaded)
    ).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    (
        _tag_only_base_image,
        _non_https_artifact,
        _uniform_dummy_digest,
        _duplicate_artifact_role,
        _unsorted_cuda_architectures,
        _unhashed_auxiliary_lock,
    ),
)
def test_runtime_lock_rejects_unbound_or_ambiguous_input(mutation) -> None:
    payload = mutation(canonical_production_cuda_runtime_lock_bytes(
        _valid_lock()
    ))
    with pytest.raises(ProductionCudaRuntimeLockError):
        load_production_cuda_runtime_lock_bytes(payload)
```

The valid fixture uses the exact base/source hashes in the design and these
auxiliary roles:

```text
apt-build-lock
apt-runtime-lock
python-requirements-lock
```

- [ ] **Step 2: Run the lock tests and observe RED**

```powershell
python -m pytest -q tests/test_production_cuda_runtime_lock.py
```

Expected: import fails because
`pipeline.production_cuda_runtime_lock` does not exist.

- [ ] **Step 3: Implement the strict lock types and loaders**

Create these frozen strict Pydantic models:

```python
class LockedBaseImage(FrozenModel):
    role: Literal["builder", "runtime"]
    identity: str
    platform: Literal["linux/amd64"]
    platform_manifest_digest: str


class LockedSourceArtifact(FrozenModel):
    role: Literal[
        "cpython-source",
        "torch-wheel",
        "torchvision-wheel",
        "nerfstudio-wheel",
        "gsplat-sdist",
    ]
    version: str
    filename: str
    url: str
    byte_length: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_commit: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{40}$",
    )


class LockedAuxiliaryFile(FrozenModel):
    role: Literal[
        "apt-build-lock",
        "apt-runtime-lock",
        "python-requirements-lock",
    ]
    path: str
    byte_length: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProductionCudaRuntimeLock(FrozenModel):
    schema_id: Literal["nantai.production-cuda-runtime-lock.v1"] = Field(
        default="nantai.production-cuda-runtime-lock.v1",
        alias="schema",
        serialization_alias="schema",
    )
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    platform: Literal["linux/amd64"]
    ubuntu_snapshot: str
    cuda_architectures: tuple[str, ...]
    base_images: tuple[LockedBaseImage, ...]
    source_artifacts: tuple[LockedSourceArtifact, ...]
    auxiliary_files: tuple[LockedAuxiliaryFile, ...]
    required_imports: tuple[str, ...]
```

Implement duplicate-key rejection, exact ASCII canonical JSON, HTTPS-only
artifact URLs, base identities matching
`^[a-z0-9._/:+-]+@sha256:[0-9a-f]{64}$`, exact role sets, sorted unique
collections, portable relative auxiliary paths and a content SHA that excludes
only `content_sha256`.

- [ ] **Step 4: Run focused tests and lint**

```powershell
python -m pytest -q tests/test_production_cuda_runtime_lock.py
python -m ruff check pipeline/production_cuda_runtime_lock.py tests/test_production_cuda_runtime_lock.py
```

Expected: all lock tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit and push the lock contract**

```powershell
git add -- pipeline/production_cuda_runtime_lock.py tests/test_production_cuda_runtime_lock.py
git commit -m "feat: define production CUDA runtime lock" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_cuda_runtime_lock.py tests/test_production_cuda_runtime_lock.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 2: No-GPU probe and detached receipt schemas

**Files:**

- Create: `tests/test_production_cuda_image_release.py`
- Create: `pipeline/production_cuda_image_release.py`

- [ ] **Step 1: Write RED model and projection tests**

The tests construct a probe with these exact observed identities:

```python
versions = {
    "python": "3.11.9",
    "torch": "2.1.2+cu118",
    "torch_cuda": "11.8",
    "torchvision": "0.16.2+cu118",
    "nerfstudio": "1.1.5",
    "gsplat": "1.4.0",
}
```

Assert:

```python
def test_release_projects_exact_runtime_policy_image_facts() -> None:
    probe = _valid_probe()
    image_digest = hashlib.sha256(b"published-image").hexdigest()
    release = ProductionCudaImageRelease.create(
        source_commit="0123456789abcdef0123456789abcdef01234567",
        image_name="ghcr.io/taomic2035/nantai-3d-production-cuda",
        image_digest=f"sha256:{image_digest}",
        platform_manifest_digest=(
            "sha256:" + hashlib.sha256(b"amd64-manifest").hexdigest()
        ),
        dockerfile_sha256=hashlib.sha256(b"dockerfile").hexdigest(),
        requirements_lock_sha256=hashlib.sha256(
            b"requirements-lock"
        ).hexdigest(),
        probe=probe,
        workflow_repository="taomic2035/nantai-3d",
        workflow_run_id=30413151667,
        workflow_run_attempt=1,
        attestations=_valid_attestations(),
    )

    facts = release.runtime_policy_image_facts()
    assert facts.expected_container_identity == (
        "ghcr.io/taomic2035/nantai-3d-production-cuda@sha256:"
        + image_digest
    )
    assert facts.expected_cuda_runtime_version == "11.8"
    assert facts.expected_python_version == "3.11.9"
    assert facts.expected_nerfstudio_version == "1.1.5"
    assert facts.expected_python_sha256 == _sha_for(probe, "python")
    assert facts.expected_training_cli_sha256 == _sha_for(
        probe,
        "ns-train",
    )
    assert facts.expected_training_cli_schema_sha256 == (
        probe.training_cli_schema_sha256
    )
    assert facts.required_training_cli_options == (
        "--data",
        "--machine.seed",
        "--max-num-iterations",
        "--output-dir",
        "--viewer.quit-on-train-completion",
    )
```

Add rejection tests for duplicate JSON keys, tag-only identity, missing SBOM,
missing provenance, probe/release lock mismatch, uniform dummy digests,
unsorted CLI options, unknown executable roles and content-SHA drift.

- [ ] **Step 2: Run and observe RED**

```powershell
python -m pytest -q tests/test_production_cuda_image_release.py
```

Expected: import fails because the release module does not exist.

- [ ] **Step 3: Implement the probe, attestation, receipt and projection**

Create:

```python
class ImageExecutableObservation(FrozenModel):
    role: Literal["python", "ns-train", "ns-export"]
    resolved_path: str
    byte_length: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    mode: int = Field(ge=0, le=0o177777)


class ProductionCudaImageProbe(FrozenModel):
    schema_id: Literal["nantai.production-cuda-image-probe.v1"] = Field(
        default="nantai.production-cuda-image-probe.v1",
        alias="schema",
        serialization_alias="schema",
    )
    probe_id: str
    content_sha256: str
    platform: Literal["linux/amd64"]
    runtime_lock_sha256: str
    python_version: str
    torch_version: str
    torch_cuda_version: str
    torchvision_version: str
    nerfstudio_version: Literal["1.1.5"]
    gsplat_version: Literal["1.4.0"]
    executables: tuple[ImageExecutableObservation, ...]
    training_cli_options: tuple[str, ...]
    training_cli_schema_sha256: str
    imported_modules: tuple[str, ...]


class OciAttestationBinding(FrozenModel):
    role: Literal[
        "buildkit-sbom",
        "buildkit-provenance",
        "github-build-provenance",
    ]
    predicate_type: Literal[
        "https://spdx.dev/Document",
        "https://slsa.dev/provenance/v1",
    ]
    manifest_digest: str


class RuntimePolicyImageFacts(FrozenModel):
    expected_container_identity: str
    expected_cuda_runtime_version: str
    expected_python_version: str
    expected_nerfstudio_version: str
    expected_training_cli_schema_sha256: str
    required_training_cli_options: tuple[str, ...]
    expected_python_sha256: str
    expected_training_cli_sha256: str


class ProductionCudaImageRelease(FrozenModel):
    schema_id: Literal["nantai.production-cuda-image-release.v1"] = Field(
        default="nantai.production-cuda-image-release.v1",
        alias="schema",
        serialization_alias="schema",
    )
    release_id: str
    content_sha256: str
    source_commit: str
    image_name: str
    image_digest: str
    platform: Literal["linux/amd64"]
    platform_manifest_digest: str
    dockerfile_sha256: str
    runtime_lock_sha256: str
    requirements_lock_sha256: str
    image_probe_sha256: str
    image_probe: ProductionCudaImageProbe
    workflow_repository: str
    workflow_run_id: int = Field(ge=1)
    workflow_run_attempt: int = Field(ge=1)
    attestations: tuple[OciAttestationBinding, ...]
```

Use the repository's existing CLI schema projection:

```python
training_cli_schema_sha256(
    trainer_name="nerfstudio-splatfacto",
    observed_options=probe.training_cli_options,
)
```

Both probe and release must have `create`, canonical bytes, signing bytes,
content SHA and strict load functions. `ProductionCudaImageRelease.create`
derives `runtime_lock_sha256` and `image_probe_sha256` from the verified probe;
callers cannot supply them separately. `runtime_policy_image_facts()` is a pure
projection and cannot accept overrides.

- [ ] **Step 4: Run tests and lint**

```powershell
python -m pytest -q tests/test_production_cuda_image_release.py
python -m ruff check pipeline/production_cuda_image_release.py tests/test_production_cuda_image_release.py
```

Expected: all tests pass and Ruff is clean.

- [ ] **Step 5: Commit and push the receipt contract**

```powershell
git add -- pipeline/production_cuda_image_release.py tests/test_production_cuda_image_release.py
git commit -m "feat: define production CUDA image receipt" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_cuda_image_release.py tests/test_production_cuda_image_release.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 3: Image-internal machine probe

**Files:**

- Create: `tests/test_production_cuda_image_probe.py`
- Create: `cloud/probe_production_cuda_image.py`

- [ ] **Step 1: Write RED tests with injected observations**

Use temporary regular executables and injected `which`, `run_command`,
`package_version` and `module_importer`. Test:

```python
def test_probe_derives_versions_executables_and_cli_schema(tmp_path) -> None:
    fixture = _probe_fixture(tmp_path)
    probe = probe_module.collect_image_probe(
        fixture.lock_path,
        which=fixture.which,
        run_command=fixture.run,
        package_version=fixture.package_version,
        module_importer=fixture.import_module,
    )

    assert probe.python_version == "3.11.9"
    assert probe.torch_version == "2.1.2+cu118"
    assert probe.torch_cuda_version == "11.8"
    assert probe.nerfstudio_version == "1.1.5"
    assert probe.gsplat_version == "1.4.0"
    assert [item.role for item in probe.executables] == [
        "ns-export",
        "ns-train",
        "python",
    ]
    assert probe.training_cli_schema_sha256 == training_cli_schema_sha256(
        trainer_name="nerfstudio-splatfacto",
        observed_options=probe.training_cli_options,
    )
```

Add fail-closed cases for symlink executable, executable replacement while
hashing, non-ASCII or oversized output, wrong version, missing CLI option,
import failure, lock drift, unexpected platform and attempted GPU-success
input.

- [ ] **Step 2: Run and observe RED**

```powershell
python -m pytest -q tests/test_production_cuda_image_probe.py
```

Expected: module import fails.

- [ ] **Step 3: Implement the bounded no-GPU probe**

The public function is:

```python
def collect_image_probe(
    runtime_lock_path: Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    run_command: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    package_version: Callable[[str], str] = metadata.version,
    module_importer: Callable[[str], object] = importlib.import_module,
) -> ProductionCudaImageProbe:
    ...
```

It must:

1. open and hash a canonical runtime lock;
2. resolve `python`, `ns-train`, `ns-export` exactly once;
3. require absolute, non-symlink, executable regular files;
4. stream-hash each file before and after all observations;
5. run bounded `python -c` version probes and `ns-train splatfacto -h`;
6. parse options with the same `--[a-z0-9][a-z0-9.-]*` rule used by fresh
   runtime clearance;
7. import exactly the lock's sorted `required_imports`;
8. construct a content-addressed probe;
9. never call `torch.cuda.is_available()` or claim GPU readiness.

The CLI accepts exactly:

```text
--runtime-lock /opt/nantai/runtime-lock.json
--output /job/image-probe.json
```

The output must not exist and is published no-replace.

- [ ] **Step 4: Run focused tests, direct help and lint**

```powershell
python -m pytest -q tests/test_production_cuda_image_probe.py
python -I cloud/probe_production_cuda_image.py --help
python -m ruff check cloud/probe_production_cuda_image.py tests/test_production_cuda_image_probe.py
```

Expected: tests pass, help exits zero, Ruff is clean.

- [ ] **Step 5: Commit and push the probe**

```powershell
git add -- cloud/probe_production_cuda_image.py tests/test_production_cuda_image_probe.py
git commit -m "feat: probe production CUDA image contract" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- cloud/probe_production_cuda_image.py tests/test_production_cuda_image_probe.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 4: Locked dependency inputs and Dockerfile

**Files:**

- Create: `containers/production-cuda/requirements.in`
- Create: `containers/production-cuda/requirements.lock`
- Create: `containers/production-cuda/apt-build.lock`
- Create: `containers/production-cuda/apt-runtime.lock`
- Create: `containers/production-cuda/runtime-lock.json`
- Create: `containers/production-cuda/Dockerfile`
- Create: `containers/production-cuda/README.md`
- Modify: `.dockerignore`
- Create: `tests/test_production_cuda_image_contract.py`

- [ ] **Step 1: Write RED static build-context tests**

Tests load the lock through Task 1 and assert:

```python
def test_dockerfile_uses_only_digest_and_hash_locked_inputs() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    lock = load_production_cuda_runtime_lock_bytes(LOCK.read_bytes())

    for image in lock.base_images:
        assert f"FROM {image.identity}" in dockerfile
    for artifact in lock.source_artifacts:
        assert f"sha256:{artifact.sha256}" in dockerfile
    assert "pip install --require-hashes" in dockerfile
    assert "--no-build-isolation" in dockerfile
    assert "curl |" not in dockerfile
    assert ":latest" not in dockerfile
    assert "pip install nerfstudio==1.1.5" not in dockerfile


def test_auxiliary_lock_hashes_match_actual_bytes() -> None:
    lock = load_production_cuda_runtime_lock_bytes(LOCK.read_bytes())
    for item in lock.auxiliary_files:
        payload = (ROOT / item.path).read_bytes()
        assert len(payload) == item.byte_length
        assert hashlib.sha256(payload).hexdigest() == item.sha256


def test_build_context_excludes_private_and_release_material() -> None:
    ignored = DOCKERIGNORE.read_text(encoding="utf-8")
    for required in (
        ".nantai-studio/",
        "input/",
        "output/",
        "releases/",
        "trained/",
        "handoff/",
        ".git/",
    ):
        assert required in ignored
```

- [ ] **Step 2: Run and observe RED**

```powershell
python -m pytest -q tests/test_production_cuda_image_contract.py
```

Expected: required files are absent.

- [ ] **Step 3: Create direct requirements and generate the hash lock**

`requirements.in` contains the Nantai runtime dependencies from
`pyproject.toml` plus direct immutable CUDA requirements:

```text
torch @ https://download-r2.pytorch.org/whl/cu118/torch-2.1.2%2Bcu118-cp311-cp311-linux_x86_64.whl
torchvision @ https://download-r2.pytorch.org/whl/cu118/torchvision-0.16.2%2Bcu118-cp311-cp311-linux_x86_64.whl
nerfstudio==1.1.5
gsplat==1.4.0
numpy<2.0
pydantic>=2.7
plyfile>=1.0
trimesh>=4.4
Pillow>=10.4
opencv-python-headless>=4.10
scikit-image>=0.26
psutil>=5.9
rich>=13.7
```

Generate and review:

```powershell
python -m pip install "uv==0.8.13"
uv pip compile containers/production-cuda/requirements.in `
  --python-version 3.11 `
  --python-platform x86_64-manylinux_2_31 `
  --generate-hashes `
  --output-file containers/production-cuda/requirements.lock
```

Expected: every resolved distribution has one or more SHA-256 hashes; Torch is
`2.1.2+cu118`, Torchvision is `0.16.2+cu118`, Nerfstudio is `1.1.5`, gsplat is
`1.4.0`, and NumPy is below 2.0. `manylinux_2_31` is required because
Nerfstudio's Open3D dependency has no CPython 3.11 wheel for
`manylinux_2_17`; it remains compatible with the Ubuntu 22.04 / glibc 2.35
runtime base.

- [ ] **Step 4: Resolve exact snapshot apt locks**

Use snapshot `20260701T000000Z` and the two digest-pinned CUDA bases. Generate
sorted `name=version` lines with `apt-cache policy`, once for builder packages
and once for runtime packages. The builder list includes CPython build
requirements; the runtime list includes the exact shared libraries used by
CPython, Torch, OpenCV and held-out rendering. Reject duplicates and any
package without one candidate in that snapshot.

Validate each list using:

```bash
apt-get update
xargs --no-run-if-empty apt-get install -y --no-install-recommends < apt-build.lock
dpkg-query -W -f='${binary:Package}=${Version}\n' | sort
```

The same command with `apt-runtime.lock` runs in the runtime base.

- [ ] **Step 5: Create the canonical runtime lock**

Populate the exact design identities, selected AMD64 child manifests, source
artifact sizes/hashes, CUDA architectures:

```json
["7.5", "8.0", "8.6", "8.9", "9.0+PTX"]
```

and required imports:

```json
[
  "gsplat",
  "nerfstudio",
  "pipeline.production_runtime_evidence",
  "torch",
  "torchmetrics",
  "torchvision"
]
```

Compute auxiliary file hashes from actual bytes, then compute the canonical
runtime lock content SHA through Task 1's model.

- [ ] **Step 6: Implement the two-stage Dockerfile**

The builder stage must:

```dockerfile
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04@sha256:94fd755736cb58979173d491504f0b573247b1745250249415b07fefc738e41f AS builder
ENV DEBIAN_FRONTEND=noninteractive
ENV SOURCE_DATE_EPOCH=1712056281
COPY containers/production-cuda/apt-build.lock /tmp/apt-build.lock
RUN printf '%s\n' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy main universe multiverse restricted' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy-updates main universe multiverse restricted' \
      'deb [check-valid-until=no] https://snapshot.ubuntu.com/ubuntu/20260701T000000Z jammy-security main universe multiverse restricted' \
      > /etc/apt/sources.list \
    && apt-get update \
    && xargs -r apt-get install -y --no-install-recommends < /tmp/apt-build.lock \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:9b1e896523fc510691126c864406d9360a3d1e986acbda59cda57b5abda45b87 \
    https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tar.xz /tmp/Python.tar.xz
```

Build CPython to `/opt/python`, install the hash lock with
`--require-hashes`, build gsplat with no isolation and fixed
`TORCH_CUDA_ARCH_LIST`, then run exact package/import checks.

The runtime stage starts from the pinned runtime base, installs only
`apt-runtime.lock`, copies `/opt/python`, sets:

```dockerfile
ENV PYTHONHOME=/opt/python
ENV PATH=/opt/python/bin:/usr/local/bin:/usr/bin:/bin
ENV LD_LIBRARY_PATH=/opt/python/lib
```

and uses `install -m 0755 /opt/python/bin/python3.11 /usr/local/bin/python`
so `python` is a regular file. Copy the canonical runtime lock to
`/opt/nantai/runtime-lock.json`; include source/revision/base/lock OCI labels.
The default command is `["/bin/bash"]`.

- [ ] **Step 7: Run static tests and a remote BuildKit smoke**

```powershell
python -m pytest -q tests/test_production_cuda_runtime_lock.py tests/test_production_cuda_image_contract.py
python -m ruff check pipeline/production_cuda_runtime_lock.py tests/test_production_cuda_runtime_lock.py tests/test_production_cuda_image_contract.py
```

On a Linux Docker runner:

```bash
docker buildx build \
  --platform linux/amd64 \
  --load \
  --tag nantai-production-cuda:contract \
  --file containers/production-cuda/Dockerfile .
docker run --rm --network none \
  -v "$PWD:/workspace:ro" \
  -w /workspace \
  nantai-production-cuda:contract \
  python cloud/probe_production_cuda_image.py \
    --runtime-lock /opt/nantai/runtime-lock.json \
    --output /tmp/image-probe.json
```

Expected: build exits zero and the probe is canonical. It does not claim GPU
availability.

- [ ] **Step 8: Commit and push the locked image inputs**

```powershell
git add -- .dockerignore containers/production-cuda/Dockerfile containers/production-cuda/runtime-lock.json containers/production-cuda/requirements.in containers/production-cuda/requirements.lock containers/production-cuda/apt-build.lock containers/production-cuda/apt-runtime.lock containers/production-cuda/README.md tests/test_production_cuda_image_contract.py
git commit -m "build: lock production CUDA image" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- .dockerignore containers/production-cuda/Dockerfile containers/production-cuda/runtime-lock.json containers/production-cuda/requirements.in containers/production-cuda/requirements.lock containers/production-cuda/apt-build.lock containers/production-cuda/apt-runtime.lock containers/production-cuda/README.md tests/test_production_cuda_image_contract.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 5: Detached receipt producer

**Files:**

- Create: `scripts/emit_production_cuda_image_release.py`
- Modify: `tests/test_production_cuda_image_release.py`

- [ ] **Step 1: Write RED CLI and no-replace tests**

Test exact arguments:

```text
--runtime-lock
--probe
--source-commit
--image-name
--image-digest
--platform-manifest-digest
--dockerfile
--requirements-lock
--workflow-repository
--workflow-run-id
--workflow-run-attempt
--attestation
--output
```

Assert the producer reopens and hashes every local input, derives the policy
projection, rejects an existing/symlink output and requires exactly the three
attestation roles with their allowed predicate types.

- [ ] **Step 2: Run and observe RED**

```powershell
python -m pytest -q tests/test_production_cuda_image_release.py
```

Expected: CLI file is missing or the new assertions fail.

- [ ] **Step 3: Implement the producer**

The script parses each `--attestation` as
`role,predicate-type,sha256:` followed by exactly 64 lowercase hex characters,
loads the canonical runtime lock and probe, checks their SHA equality, hashes
Dockerfile/requirements from stable regular files, creates
`ProductionCudaImageRelease`, writes canonical bytes no-replace, reopens them
and prints one canonical JSON object containing exactly `image_identity` and
`receipt_sha256`.

No environment dump, token or private path is printed.

- [ ] **Step 4: Run tests, direct help and lint**

```powershell
python -m pytest -q tests/test_production_cuda_image_release.py
python -I scripts/emit_production_cuda_image_release.py --help
python -m ruff check pipeline/production_cuda_image_release.py scripts/emit_production_cuda_image_release.py tests/test_production_cuda_image_release.py
```

Expected: all commands exit zero.

- [ ] **Step 5: Commit and push**

```powershell
git add -- scripts/emit_production_cuda_image_release.py tests/test_production_cuda_image_release.py
git commit -m "feat: emit production CUDA image receipt" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- scripts/emit_production_cuda_image_release.py tests/test_production_cuda_image_release.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 6: Manual GHCR publication and attestations

**Files:**

- Create: `tests/test_production_cuda_image_workflow.py`
- Create: `.github/workflows/production-cuda-image.yml`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write RED workflow contract tests**

Parse YAML with the existing loader convention and assert:

- trigger is only `workflow_dispatch`;
- branch/ref check requires exact `refs/heads/main`;
- `linux/amd64`, no `latest`, no pull request publish;
- job-scoped permissions are exactly `contents: read`, `packages: write`,
  `id-token: write`, `attestations: write`;
- every action uses the full SHA listed below;
- BuildKit uses `sbom: true`, `provenance: mode=max`;
- digest output, not a tag, feeds probe and attest steps;
- runtime probe uses `--network none`;
- receipt and summary are the only uploaded workflow artifacts.

Pinned actions:

```text
actions/checkout@11d5960a326750d5838078e36cf38b85af677262
docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9
docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f
docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8
actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6
actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
```

- [ ] **Step 2: Run and observe RED**

```powershell
python -m pytest -q tests/test_production_cuda_image_workflow.py
```

Expected: workflow file is absent.

- [ ] **Step 3: Implement the manual workflow**

The workflow:

1. verifies `github.ref == 'refs/heads/main'`;
2. validates clean source SHA, runtime lock and static tests;
3. logs in to `ghcr.io`;
4. builds/pushes
   `ghcr.io/${{ github.repository_owner }}/nantai-3d-production-cuda:sha-${{ github.sha }}`;
5. captures `${{ steps.build.outputs.digest }}`;
6. pulls and runs the exact digest with `/workspace` read-only, `/evidence`
   writable and `--network none`;
7. retrieves the AMD64 child manifest and BuildKit SBOM/provenance manifest
   digests with bounded JSON parsing;
8. creates the GitHub image attestation using the digest;
9. emits the detached receipt;
10. attests the receipt as a file;
11. runs `gh attestation verify` for both OCI digest and receipt;
12. uploads `production-cuda-image-release.json` and
    `production-cuda-image-verification.json`.

The workflow uses `set -euo pipefail`, never prints `GITHUB_TOKEN`, and does
not delete an earlier failed registry artifact.

- [ ] **Step 4: Add fast contracts to ordinary CI**

Add these tests to the existing three-OS read-only Production job:

```text
tests/test_production_cuda_runtime_lock.py
tests/test_production_cuda_image_release.py
tests/test_production_cuda_image_probe.py
tests/test_production_cuda_image_contract.py
tests/test_production_cuda_image_workflow.py
```

Ordinary CI does not build or push the CUDA image.

- [ ] **Step 5: Run focused tests and lint**

```powershell
python -m pytest -q tests/test_production_cuda_runtime_lock.py tests/test_production_cuda_image_release.py tests/test_production_cuda_image_probe.py tests/test_production_cuda_image_contract.py tests/test_production_cuda_image_workflow.py
python -m ruff check pipeline/production_cuda_runtime_lock.py pipeline/production_cuda_image_release.py cloud/probe_production_cuda_image.py scripts/emit_production_cuda_image_release.py tests/test_production_cuda_runtime_lock.py tests/test_production_cuda_image_release.py tests/test_production_cuda_image_probe.py tests/test_production_cuda_image_contract.py tests/test_production_cuda_image_workflow.py
git diff --check
```

Expected: tests and lint pass with no whitespace errors.

- [ ] **Step 6: Commit and push**

```powershell
git add -- .github/workflows/production-cuda-image.yml .github/workflows/ci.yml tests/test_production_cuda_image_workflow.py
git commit -m "ci: publish attested production CUDA image" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- .github/workflows/production-cuda-image.yml .github/workflows/ci.yml tests/test_production_cuda_image_workflow.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 7: Operator documentation and honest status

**Files:**

- Create: `docs/manual/production-cuda-image.md`
- Modify: `docs/manual/reconstruction-setup.md`
- Modify: `docs/production-v1-status.md`
- Modify: `docs/README.md`
- Modify: `README.md`
- Modify: `tests/test_real_golden_path_docs.py`

- [ ] **Step 1: Write RED documentation assertions**

Require:

```python
def test_cuda_image_manual_keeps_publish_runtime_and_training_gates_separate():
    manual = CUDA_IMAGE_MANUAL.read_text(encoding="utf-8")
    assert "image@sha256:" in manual
    assert "gh attestation verify oci://" in manual
    assert "modeled-unverified" in manual
    assert "fresh GPU clearance" in manual
    assert "non-mock training" in manual
    assert "不等于 Production V1" in manual
    assert ":latest" not in manual
```

Also require one README link, one docs index link, CUDA `11.8` in the formal
receipt example, and no claim that GitHub-hosted no-GPU smoke proves CUDA.

- [ ] **Step 2: Run and observe RED**

```powershell
python -m pytest -q tests/test_real_golden_path_docs.py
```

Expected: new manual/link/status assertions fail.

- [ ] **Step 3: Write the focused CUDA image manual**

Document:

```powershell
$head = git rev-parse HEAD
$run = gh run list --workflow production-cuda-image.yml --limit 20 `
  --json databaseId,headSha,conclusion | ConvertFrom-Json |
  Where-Object { $_.headSha -eq $head -and $_.conclusion -eq "success" } |
  Select-Object -First 1
if ($null -eq $run) { throw "successful exact-head image run missing" }
$runId = $run.databaseId
$receiptRoot = ".nantai-studio\cuda-image\$runId"
gh run download $runId `
  --name production-cuda-image-release `
  --dir $receiptRoot
gh attestation verify `
  "$receiptRoot\production-cuda-image-release.json" `
  -R taomic2035/nantai-3d
$release = Get-Content `
  "$receiptRoot\production-cuda-image-release.json" `
  -Raw | ConvertFrom-Json
$imageIdentity = "$($release.image_name)@$($release.image_digest)"
gh attestation verify "oci://$imageIdentity" -R taomic2035/nantai-3d
```

The manual says to derive the image identity from the verified receipt, never
type or guess it. Show the exact receipt-to-policy field mapping and list
host-specific facts separately.

- [ ] **Step 4: Update reconstruction and status surfaces**

In `reconstruction-setup.md`, replace the illustrative formal CUDA `12.8`
example with values derived from a verified receipt. In
`production-v1-status.md`, report the highest level actually achieved:

- repository contract;
- published image contract;
- fresh GPU runtime;
- non-mock training.

Only mark a level complete after its machine evidence exists. Keep capture,
SfM, control points and Viewer/human gates unchanged.

Keep the top-level README concise: add at most one link under the cloud GPU
runtime entry and do not paste workflow internals.

- [ ] **Step 5: Run documentation and focused runtime suites**

```powershell
python -m pytest -q tests/test_real_golden_path_docs.py tests/test_production_runtime_policy.py tests/test_production_runtime_entrypoint.py tests/test_remote_readiness_checker.py tests/test_production_cuda_runtime_lock.py tests/test_production_cuda_image_release.py tests/test_production_cuda_image_probe.py tests/test_production_cuda_image_contract.py tests/test_production_cuda_image_workflow.py
python -m ruff check pipeline cloud scripts tests
git diff --check
```

Expected: all focused tests pass, Ruff and diff-check are clean.

- [ ] **Step 6: Commit and push**

```powershell
git add -- README.md docs/README.md docs/manual/production-cuda-image.md docs/manual/reconstruction-setup.md docs/production-v1-status.md tests/test_real_golden_path_docs.py
git commit -m "docs: add production CUDA image operations" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- README.md docs/README.md docs/manual/production-cuda-image.md docs/manual/reconstruction-setup.md docs/production-v1-status.md tests/test_real_golden_path_docs.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 8: Publish and verify the first OCI candidate

**Files:**

- Verify externally generated GHCR image, attestations and workflow artifacts.
- Do not add generated image receipts to Git unless the specification is
  deliberately revised.

- [ ] **Step 1: Verify exact-head ordinary CI**

```powershell
$head = git rev-parse HEAD
$remote = git rev-parse origin/main
if ($head -ne $remote) { throw "local and remote main differ" }
$runs = gh run list --workflow ci.yml --branch main --limit 10 `
  --json databaseId,headSha,status,conclusion,url | ConvertFrom-Json
$run = $runs | Where-Object headSha -eq $head | Select-Object -First 1
if ($null -eq $run) { throw "exact-head CI run missing" }
gh run view $run.databaseId --json headSha,status,conclusion,jobs,url
```

Expected: every exact-head job concludes `success`.

- [ ] **Step 2: Dispatch the manual image workflow**

```powershell
gh workflow run production-cuda-image.yml --ref main
Start-Sleep -Seconds 5
gh run list --workflow production-cuda-image.yml --branch main --limit 5
```

Use bounded run polling. If the build fails, inspect the failed step and fix
the lock/build contract through a new TDD checkpoint; do not bypass hashes or
attestations.

- [ ] **Step 3: Download and verify final artifacts**

```powershell
gh run download $runId `
  --name production-cuda-image-release `
  --dir .nantai-studio\cuda-image\$runId
gh attestation verify `
  .nantai-studio\cuda-image\$runId\production-cuda-image-release.json `
  -R taomic2035/nantai-3d
```

Load the canonical receipt with
`pipeline.production_cuda_image_release`, then verify the OCI digest
attestation. Confirm image, probe, SBOM, provenance and receipt all bind the
same digest and source commit.

- [ ] **Step 4: Record the achieved level honestly**

If the workflow and downloaded-byte verification pass, update only the
published image contract level. The state is still `modeled-unverified`; do
not claim usable CUDA or non-mock training.

### Task 9: Fresh external GPU clearance and non-mock training

**Files:**

- Private inputs and outputs only under `.nantai-studio/`.
- Modify tracked status/docs only after evidence is verified.

- [ ] **Step 1: Build a private policy input from verified facts**

Copy image-derived fields from the canonical detached receipt. Add the approved
host's measured GPU UUID, memory floor, container-runtime executable SHA and
`nvidia-smi` executable SHA. Keep the private host, key and credentials out of
Git.

Generate the policy with the existing command:

```powershell
python -m pipeline.production_runtime_policy `
  --repo-root . `
  --operator-input .nantai-studio\private\production-runtime-policy-input.json `
  --output .nantai-studio\private\production-runtime-policy.json
```

Expected: canonical policy binds the exact OCI digest and current clean source
commit.

- [ ] **Step 2: Run host readiness and fresh container clearance**

Use the existing remote readiness checker and `train-production` caller. The
same exact digest must produce an accepted
`nantai.production-runtime-decision.v1` with matching GPU, CUDA `11.8`,
Python `3.11.9`, Nerfstudio `1.1.5`, executable identities and CLI schema.

Any host mismatch, inability to schedule the GPU, missing driver support,
timeout or connection ambiguity remains rejected/unknown and stops before
training.

- [ ] **Step 3: Run one non-mock training and held-out evaluation**

Submit the prepared internal canary or rights-approved scene through the
existing remote worker. Require:

- accepted runtime decision for the same digest;
- completed Splatfacto training and exported PLY;
- identity dataparser transform;
- held-out render report and decision;
- verified v2 result bundle and local closure.

The PLY, reports and receipt remain private unless their rights allow
redistribution.

- [ ] **Step 4: Verify import and update status**

Run the existing production import and validators from downloaded bytes.
Only after the runtime decision, training closure, held-out decision and import
all verify may `docs/production-v1-status.md` mark the non-mock CUDA gate
closed. Measured alignment and real Viewer/human acceptance remain separate
gates for the same scene.

## Final regression and completion audit

- [ ] Run the complete Python, Node and Ruff suites:

```powershell
python -m pytest -q tests
python -m ruff check pipeline tests cloud scripts make.py
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
git diff --check
```

- [ ] Confirm local/remote exact HEAD and all GitHub CI jobs are green.
- [ ] Verify the published OCI image and detached receipt attestations from
  downloaded bytes.
- [ ] Verify the runtime status document reports the highest observed level,
  not the intended level.
- [ ] Confirm Release/README surfaces contain no private inputs, build caches,
  intermediate wheels, failed requests, raw logs or unaccepted receipts.
- [ ] Keep the overall Production V1 goal open until one scene has all five
  external gates: rights-cleared capture, accepted real-photo SfM, non-mock
  CUDA 3DGS, measured alignment and real Viewer/human acceptance.
