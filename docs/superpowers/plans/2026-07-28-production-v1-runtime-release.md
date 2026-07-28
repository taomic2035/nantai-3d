# Nantai 3D Production V1 Runtime Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-platform Production V1 runtime ZIP whose final real
scene, public evidence projection and runtime bytes can be independently
verified offline without publishing private capture or training inputs.

**Architecture:** Keep the complete `validate_real_scene_acceptance` closure in
the private workspace and derive a separate public evidence projection only
after a fresh accepted decision. Standard-library contract and verifier modules
validate the downloaded ZIP/tree; a dependency-bearing builder reopens the
private acceptance, resolves only the final runtime scene closure, maps the
validated import `web/` subtree into `web/data/recon/`, then publishes a
deterministic no-replace archive. Studio recognizes Preview and Production
receipts separately and never lets package integrity promote scene trust.

**Tech Stack:** Python 3.11+ standard library for public receipt/archive
verification; existing Pydantic acceptance/import models for private build-time
validation; pytest and Ruff; browser ES modules and Node test runner; current
Studio HTTP server and Viewer; GitHub Actions on Windows, Ubuntu and macOS.

**Design:** `docs/superpowers/specs/2026-07-28-production-v1-runtime-release-design.md`

---

## File structure

- Create `pipeline/release_archive.py`: standard-library canonical JSON, safe
  paths, stable file digests, ZIP member limits and deterministic metadata.
- Modify `pipeline/preview_release.py`: re-export/use the neutral primitives
  without changing Preview receipt bytes or behavior.
- Create `pipeline/production_release_contract.py`: standard-library-only
  Production receipt and public-evidence schema validation.
- Create `pipeline/production_release_verifier.py`: independent tree/archive
  verifier and bounded safe extraction.
- Create `pipeline/production_release_builder.py`: private acceptance
  revalidation, redacted projection, scene closure mapping and deterministic
  builder.
- Create `scripts/build_production_release.py`: thin build CLI.
- Create `scripts/verify_production_release.py`: clean-extraction verifier CLI.
- Create `tests/production_release_fixtures.py`: modeled contract fixtures that
  are impossible to mistake for a real release.
- Create `release/production-verify-and-run.md`: concise tracked source for the
  packaged `VERIFY-AND-RUN.md`.
- Create `tests/test_release_archive.py`,
  `tests/test_production_release_contract.py`,
  `tests/test_production_release_verifier.py`,
  `tests/test_production_release_builder.py` and
  `tests/test_production_release_cli.py`.
- Modify `make.py` and `tests/test_make_runner.py`: `build-production` and
  `verify-production` entry points with explicit acceptance/version inputs.
- Modify `pipeline/studio_server.py` and `tests/test_studio_server.py`: detect
  either Preview or Production receipt, project the verified public decision,
  and reject ambiguous/mixed packages.
- Modify `web/studio/app.js`, `web/studio/model.mjs`,
  `web/studio/index-contract.test.mjs`, `web/studio/model.test.mjs`: distinct
  Production package/evidence labels and fail-closed normalization.
- Modify `web/viewer/main.js`, `web/viewer/startup-state.test.mjs` and
  `web/viewer/model-preview.test.mjs`: disable synthetic model fallback in a
  verified Production package.
- Create `docs/manual/production-runtime-release.md`; modify `README.md`,
  `docs/README.md` and `docs/production-v1-status.md`.
- Modify `.github/workflows/ci.yml`: focused cross-platform verifier/content-ID
  matrix including macOS.
- Create `tests/probe_production_release_content_id.py`: CI-only modeled
  content-ID producer; it is never packaged.
- Do not create `docs/releases/1.0.md`, change Python to `1.0.0`, create
  `v1.0.0`, or publish a GitHub Release until Task 10 has real external
  acceptance evidence.

## Global execution rules

- Work only on `main`; one shared worktree remains authoritative.
- Before every task run `git status --short --branch` and do not touch GLM
  H1/I1/J1/K1 paths while they contain work.
- Start each implementation task with `superpowers:test-driven-development`.
- Before each completion claim or commit use
  `superpowers:verification-before-completion`.
- Stage only the listed task paths. Never use `git add -A`, `git commit -a`,
  reset, checkout, stash or rebase.
- Every Codex commit ends with:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

- Push every green checkpoint with:

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 1: Neutral standard-library archive safety primitives

**Files:**

- Create: `pipeline/release_archive.py`
- Create: `tests/test_release_archive.py`
- Modify: `pipeline/preview_release.py`
- Test: `tests/test_preview_release.py`
- Test: `tests/test_preview_release_cli.py`

- [ ] **Step 1: Write RED canonical path, digest and ZIP-member tests**

Create tests with these exact public contracts:

```python
from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    canonical_json_bytes,
    inspect_zip_members,
    safe_posix_member_path,
    stable_regular_file_digest,
)


def test_stable_digest_streams_in_one_mib_chunks(tmp_path, monkeypatch):
    target = tmp_path / "scene.ply"
    target.write_bytes(b"x" * (3 * 1024 * 1024 + 17))
    observed = []
    result = stable_regular_file_digest(
        target,
        on_read=lambda size: observed.append(size),
    )
    assert result.byte_length == target.stat().st_size
    assert len(result.sha256) == 64
    assert observed
    assert max(observed) <= 1024 * 1024


def test_zip_inspection_rejects_casefold_collision(tmp_path):
    archive_path = tmp_path / "collision.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("nantai/web/A.js", b"a")
        archive.writestr("nantai/web/a.js", b"b")
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ReleaseArchiveError, match="case-fold"):
            inspect_zip_members(archive, ArchiveLimits())
```

Parameterize unsafe member names over `/x`, `C:/x`, `../x`, `a\\b`, `a//b`,
`a/./b`, `CON`, `con.txt`, trailing dot/space and NUL. Add real symlink,
non-regular file, mid-read size/mtime replacement, encrypted-bit, duplicate
member, device-mode, total-size and compression-ratio cases.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest -q tests/test_release_archive.py
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'pipeline.release_archive'`.

- [ ] **Step 3: Implement the exact neutral API**

`pipeline/release_archive.py` must export:

```python
@dataclass(frozen=True)
class FileDigest:
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class ArchiveLimits:
    maximum_members: int = 20_000
    maximum_member_bytes: int = 8 * 1024 * 1024 * 1024
    maximum_total_bytes: int = 32 * 1024 * 1024 * 1024
    maximum_compression_ratio: int = 1_000


@dataclass(frozen=True)
class InspectedZipMember:
    path: PurePosixPath
    byte_length: int
    compressed_length: int
    unix_mode: int


def canonical_json_bytes(value: object) -> bytes
def safe_posix_member_path(value: str) -> PurePosixPath
def stable_regular_file_digest(
    path: Path,
    *,
    maximum_bytes: int | None = None,
    on_read: Callable[[int], None] | None = None,
) -> FileDigest
def deterministic_zip_info(path: str, *, executable: bool = False) -> zipfile.ZipInfo
def inspect_zip_members(
    archive: zipfile.ZipFile,
    limits: ArchiveLimits,
) -> tuple[InspectedZipMember, ...]
```

`stable_regular_file_digest` opens once, compares `lstat/open/fstat` identity,
mode, size and nanosecond mtime before and after, reads at most 1 MiB per call
and rejects links/non-regular files. `inspect_zip_members` requires one common
root, canonical case-insensitive-unique paths, regular files/directories only,
no encryption, bounded expansion and a finite compression ratio.

- [ ] **Step 4: Re-export neutral primitives from Preview without receipt drift**

Replace Preview-local implementations of `canonical_json_bytes`,
`safe_posix_member_path`, `sha256_file` and `_zip_info` with imports/wrappers
from `pipeline.release_archive`. Keep Preview public names and exact output:

```python
from pipeline.release_archive import (
    canonical_json_bytes,
    deterministic_zip_info,
    safe_posix_member_path,
    stable_regular_file_digest,
)


def sha256_file(path: Path) -> str:
    return stable_regular_file_digest(path).sha256


def _zip_info(path: str) -> zipfile.ZipInfo:
    return deterministic_zip_info(path)
```

- [ ] **Step 5: Run focused and Preview regression gates**

```powershell
python -m pytest -q tests/test_release_archive.py tests/test_preview_release.py tests/test_preview_release_cli.py
python -m ruff check pipeline/release_archive.py pipeline/preview_release.py tests/test_release_archive.py
git diff --check -- pipeline/release_archive.py pipeline/preview_release.py tests/test_release_archive.py
```

Expected: all tests pass and a previously built Preview2 fixture retains the
same receipt bytes and package content ID.

- [ ] **Step 6: Commit and push checkpoint 1**

```powershell
git add -- pipeline/release_archive.py pipeline/preview_release.py tests/test_release_archive.py
git commit -m "refactor: share fail-closed release archive primitives" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/release_archive.py pipeline/preview_release.py tests/test_release_archive.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 2: Production public receipt and evidence contracts

**Files:**

- Create: `pipeline/production_release_contract.py`
- Create: `tests/production_release_fixtures.py`
- Create: `tests/test_production_release_contract.py`

- [ ] **Step 1: Create one explicit modeled public-evidence fixture**

The fixture must contain no private paths and must say it is modeled:

```python
MODELED_ACCEPTANCE_SHA = "a" * 64
MODELED_DECISION_SHA = "b" * 64
MODELED_SCENE_ID = "scene-" + "c" * 64
GATES = (
    "dataset",
    "capture",
    "sfm",
    "production-training",
    "import-integrity",
    "render-quality",
    "viewer-performance",
    "human-review",
    "release-rights",
    "metric-alignment",
)


def modeled_public_evidence() -> dict[str, object]:
    return {
        "schema": "nantai.production-public-evidence.v1",
        "fixture_kind": "modeled-contract-not-real-release",
        "acceptance": {
            "report_sha256": MODELED_ACCEPTANCE_SHA,
            "decision_sha256": MODELED_DECISION_SHA,
            "source_role": "production-acceptance",
            "production_release_allowed": True,
            "gates": [{"id": gate, "state": "accepted"} for gate in GATES],
        },
        "source": {
            "dataset_id_sha256": "d" * 64,
            "capture_manifest_sha256": "e" * 64,
            "rights": {
                "redistribution_allowed": True,
                "release_inclusion_allowed": True,
                "processing_purposes": ["3d-reconstruction"],
            },
        },
        "scene": {
            "scene_identity": MODELED_SCENE_ID,
            "import_receipt_sha256": "f" * 64,
            "manifest_sha256": "1" * 64,
            "quality_role": "production",
            "geometry_usability": "metric-aligned",
            "units": "meters",
            "alignment_rms_m": 0.1,
            "gaussian_count": 100_000,
        },
        "training": {
            "closure_sha256": "2" * 64,
            "runtime_decision_sha256": "3" * 64,
            "container_identity_sha256": "4" * 64,
        },
        "render": {"policy_sha256": "5" * 64, "report_sha256": "6" * 64, "accepted": True},
        "viewer": {
            "schema": "nantai.viewer-performance-report.v2",
            "policy_sha256": "7" * 64,
            "report_sha256": "8" * 64,
            "accepted": True,
            "screenshot_count": 3,
        },
        "human_review": {
            "policy_sha256": "9" * 64,
            "review_sha256": "0" * 64,
            "accepted": True,
            "categories": [
                "scene-envelope",
                "floaters",
                "view-dependent-colour",
                "exposure-seams",
                "transparent-surfaces",
                "navigable-holes",
                "fidelity-label",
            ],
        },
        "private_evidence_omitted": [
            "capture-media",
            "control-point-coordinates",
            "private-operator-identity",
            "remote-host-configuration",
            "training-input-pixels",
        ],
    }
```

- [ ] **Step 2: Write RED exact-schema tests**

Test canonical bytes, fixed gate order, final semantic version
`vMAJOR.MINOR.PATCH`, exact artifact fields, sorted artifacts/protected roots,
receipt content-ID recomputation and `scene.trust_effect == "none"`. Mutate
every Production literal to Preview/internal/arbitrary/unaligned/false and
assert rejection.

```python
def test_production_receipt_rejects_preview_suffix():
    with pytest.raises(ProductionReleaseContractError, match="version"):
        build_production_receipt(
            version="v1.0.0-preview.2",
            source_commit="a" * 40,
            artifacts=(),
            protected_roots=("web",),
            entrypoints={"studio": "/web/studio/"},
            public_evidence=modeled_public_evidence(),
        )
```

- [ ] **Step 3: Run RED**

```powershell
python -m pytest -q tests/test_production_release_contract.py
```

Expected: collection fails because the contract module is absent.

- [ ] **Step 4: Implement standard-library contract validation**

Export these names:

```python
PRODUCTION_RELEASE_SCHEMA = "nantai.production-runtime-release.v1"
PRODUCTION_PUBLIC_EVIDENCE_SCHEMA = "nantai.production-public-evidence.v1"
PRODUCTION_RELEASE_NAME = "PRODUCTION-RELEASE.json"
CHECKSUMS_NAME = "SHA256SUMS.txt"
PRODUCTION_GATE_IDS = (
    "dataset",
    "capture",
    "sfm",
    "production-training",
    "import-integrity",
    "render-quality",
    "viewer-performance",
    "human-review",
    "release-rights",
    "metric-alignment",
)


def validate_public_evidence(value: Mapping[str, object]) -> dict[str, object]
def build_production_receipt(
    *,
    version: str,
    source_commit: str,
    artifacts: Iterable[Mapping[str, object]],
    protected_roots: Iterable[str],
    entrypoints: Mapping[str, str],
    public_evidence: Mapping[str, object],
) -> dict[str, object]
def load_public_evidence_bytes(payload: bytes) -> dict[str, object]
def load_production_receipt_bytes(payload: bytes) -> dict[str, object]
```

Use exact key sets throughout. The receipt embeds the public-evidence SHA and
scene facts, not the full evidence object. Rebuild and compare canonical bytes
on load. Hash opaque private identities; never accept operator, absolute path,
host or control-point coordinate fields in the public schema.

`fixture_kind` is an exact required field whose value is either
`modeled-contract-not-real-release` or `null`. The builder-derived real
projection always sets it to `null`. A verifier that sees the modeled value
returns `release_contract=modeled-contract-only`, even when the fixture's gate
shape is useful for exercising the accepted path.

- [ ] **Step 5: Verify and commit checkpoint 2**

```powershell
python -m pytest -q tests/test_production_release_contract.py
python -m ruff check pipeline/production_release_contract.py tests/production_release_fixtures.py tests/test_production_release_contract.py
git diff --check -- pipeline/production_release_contract.py tests/production_release_fixtures.py tests/test_production_release_contract.py
git add -- pipeline/production_release_contract.py tests/production_release_fixtures.py tests/test_production_release_contract.py
git commit -m "feat: define Production runtime release contracts" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_release_contract.py tests/production_release_fixtures.py tests/test_production_release_contract.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 3: Independent tree/archive verifier and clean CLI

**Files:**

- Create: `pipeline/production_release_verifier.py`
- Create: `scripts/verify_production_release.py`
- Create: `tests/test_production_release_verifier.py`
- Create: `tests/test_production_release_cli.py`

- [ ] **Step 1: Write RED tree and adversarial archive tests**

Build a tiny extracted tree using the modeled fixture. Assert:

```python
report = verify_production_release_tree(root)
assert report.valid is True
assert report.package_integrity == "verified"
assert report.release_contract == "modeled-contract-only"
assert report.scene_trust_effect == "none"
assert report.fixture_kind == "modeled-contract-not-real-release"
```

Add one test per failure class: changed/missing/extra protected file, receipt or
evidence noncanonical, checksum disagreement, scene/evidence cross-swap,
symlink, case collision, path traversal, encrypted member, unsupported type,
zip bomb, oversized member/total, existing extraction destination and archive
with two roots.

- [ ] **Step 2: Write RED isolated CLI test**

Run the copied verifier under `python -I` with an ASCII-only stdout:

```python
completed = subprocess.run(
    [
        sys.executable,
        "-I",
        "-c",
        (
            "import runpy,sys;"
            "sys.stdout.reconfigure(encoding='ascii',errors='strict');"
            "sys.argv=['verify_production_release.py',r'%s','--json'];"
            "runpy.run_path(r'%s',run_name='__main__')"
        ) % (archive_path, verifier_path),
    ],
    capture_output=True,
    text=True,
)
assert completed.returncode == 0
json.loads(completed.stdout)
```

Corrupt input must exit `2`, leave no extraction directory and write no JSON to
stdout.

- [ ] **Step 3: Run RED**

```powershell
python -m pytest -q tests/test_production_release_verifier.py tests/test_production_release_cli.py
```

- [ ] **Step 4: Implement verifier and bounded extraction**

Export:

```python
@dataclass(frozen=True)
class ProductionReleaseVerification:
    valid: bool
    version: str
    source_commit: str
    package_content_id: str
    artifact_count: int
    total_bytes: int
    package_integrity: str
    release_contract: str
    scene_trust_effect: str
    fixture_kind: str | None


def verify_production_release_tree(root: Path) -> ProductionReleaseVerification
def verify_production_release_archive(path: Path) -> ProductionReleaseVerification
```

The archive verifier calls `inspect_zip_members`, extracts each member through
one bounded stream into a new temporary root, verifies written length and SHA,
then invokes the independent tree verifier. It never uses `extractall`,
overwrites a destination, trusts CRC alone or imports the builder/Pydantic.

The CLI bootstraps only its immutable release parent, sets
`sys.dont_write_bytecode=True`, selects tree/archive mode and uses
`ensure_ascii=True` for `--json`.

- [ ] **Step 5: Verify and commit checkpoint 3**

```powershell
python -m pytest -q tests/test_release_archive.py tests/test_production_release_contract.py tests/test_production_release_verifier.py tests/test_production_release_cli.py
python -m ruff check pipeline/release_archive.py pipeline/production_release_contract.py pipeline/production_release_verifier.py scripts/verify_production_release.py tests/test_production_release_verifier.py tests/test_production_release_cli.py
git diff --check -- pipeline/production_release_verifier.py scripts/verify_production_release.py tests/test_production_release_verifier.py tests/test_production_release_cli.py
git add -- pipeline/production_release_verifier.py scripts/verify_production_release.py tests/test_production_release_verifier.py tests/test_production_release_cli.py
git commit -m "feat: verify Production runtime archives offline" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_release_verifier.py scripts/verify_production_release.py tests/test_production_release_verifier.py tests/test_production_release_cli.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 4: Fresh private acceptance projection and privacy boundary

**Files:**

- Create: `pipeline/production_release_builder.py`
- Create: `tests/test_production_release_builder.py`
- Test: `tests/test_real_scene_acceptance.py`
- Test: `tests/test_real_scene_import.py`

- [ ] **Step 1: Write RED acceptance-gate tests**

Use a modeled acceptance tree and monkeypatch only at the outer validator
boundary for unit tests. Capture the exact `AcceptanceDecision` returned and
assert the builder rejects:

- internal canary;
- any gate not exactly accepted;
- `production_release_allowed=false`;
- report SHA mismatch;
- Viewer v1;
- rights inclusion false;
- import role/units/alignment/quality mismatch;
- evidence changed after validation;
- operator name, capture scope, absolute paths, controls or host appearing in
  serialized public evidence.

The positive test calls the existing validator exactly once and reopens report,
import, closure, Viewer, screenshots and human-review bytes afterward.

```python
context = derive_production_release_context(report_path)
assert context.decision.production_release_allowed is True
assert context.import_receipt.source_role == "production-acceptance"
assert context.public_evidence["scene"]["units"] == "meters"
serialized = canonical_json_bytes(context.public_evidence)
for secret in (b"Reviewer One", b"C:\\", b"/home/", b"ssh", b"control-points"):
    assert secret not in serialized
```

- [ ] **Step 2: Run RED**

```powershell
python -m pytest -q tests/test_production_release_builder.py -k "acceptance or public_evidence or private"
```

- [ ] **Step 3: Implement context derivation**

Create:

```python
@dataclass(frozen=True)
class SourcePayload:
    source_path: Path
    destination_path: str
    role: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class ProductionReleaseContext:
    acceptance_root: Path
    report_sha256: str
    decision: AcceptanceDecision
    import_root: Path
    import_receipt: RealSceneImportReceipt
    public_evidence: dict[str, object]
    public_files: tuple[SourcePayload, ...]


def derive_production_release_context(
    acceptance_report_path: Path,
) -> ProductionReleaseContext
```

The function first calls `validate_real_scene_acceptance`. It then loads the
canonical `RealSceneAcceptance`, follows only its bounded relative references,
calls `validate_real_scene_import_receipt`, loads the production training
closure, Viewer v2 report and human-review models through their existing
canonical loaders, derives their decisions again, and copies only three
reviewed screenshots. It emits the canonical acceptance decision, Viewer
policy/report and screenshots; it emits a redacted public human-review receipt
whose reviewer field is a SHA-256 identity rather than the private reviewer
text. The original human-review receipt remains private and is referenced only
by SHA. Dataset ID, container identity and other non-public identifiers are
one-way SHA-256 values.

The function performs a second stable digest pass over every retained public
file and the acceptance report before returning. A changed byte or stat
identity aborts.

- [ ] **Step 4: Verify privacy and acceptance regressions**

```powershell
python -m pytest -q tests/test_production_release_builder.py tests/test_real_scene_acceptance.py tests/test_real_scene_import.py
python -m ruff check pipeline/production_release_builder.py tests/test_production_release_builder.py
git diff --check -- pipeline/production_release_builder.py tests/test_production_release_builder.py
```

- [ ] **Step 5: Commit and push checkpoint 4**

```powershell
git add -- pipeline/production_release_builder.py tests/test_production_release_builder.py
git commit -m "feat: derive redacted Production release evidence" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_release_builder.py tests/test_production_release_builder.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 5: Runtime scene closure and deterministic no-replace builder

**Files:**

- Modify: `pipeline/production_release_builder.py`
- Modify: `tests/test_production_release_builder.py`
- Create: `scripts/build_production_release.py`
- Modify: `tests/test_production_release_cli.py`
- Create: `release/production-verify-and-run.md`

- [ ] **Step 1: Write RED scene-closure tests**

From a validated import fixture, assert the builder maps:

```text
$IMPORT_ROOT/web/recon_manifest.json
  -> web/data/recon/recon_manifest.json
$IMPORT_ROOT/web/chunks/chunks.json
  -> web/data/recon/chunks/chunks.json
$IMPORT_ROOT/web/{manifest-bound-relative-path}
  -> web/data/recon/{manifest-bound-relative-path}
```

Assert source/normalized PLY, control points, private registration source,
training bundle and unreferenced import files do not enter the package. Reject
manifest path escape, binding not present in the import receipt, manifest/file
SHA drift, duplicate mapped destination, symlink, late mutation, dirty
release-owned runtime source, pre-existing output and pre-existing staging.

- [ ] **Step 2: Write RED determinism and publication tests**

Build twice with the same modeled context and assert equal:

- package content ID;
- ZIP SHA when using the same runtime;
- member order, timestamp, mode and compression;
- sidecar contents.

Inject failure after staging, during ZIP close, during archive verification and
during no-replace publication. Assert the final archive and sidecar are absent
and unrelated existing destinations remain byte-identical.

- [ ] **Step 3: Run RED**

```powershell
python -m pytest -q tests/test_production_release_builder.py tests/test_production_release_cli.py -k "scene or build or deterministic or publication"
```

- [ ] **Step 4: Implement resolver, assembler and CLI**

Export:

```python
@dataclass(frozen=True)
class ProductionReleaseBuild:
    archive_path: Path
    archive_sha256: str
    package_content_id: str
    artifact_count: int
    total_bytes: int
    scene_identity: str
    acceptance_report_sha256: str


def resolve_runtime_scene_payloads(
    context: ProductionReleaseContext,
) -> tuple[SourcePayload, ...]


def build_production_release_archive(
    *,
    repo_root: Path,
    acceptance_root: Path,
    output_path: Path,
    version: str,
    source_commit: str,
    tracked_files: Iterable[str],
) -> ProductionReleaseBuild
```

Runtime code uses an explicit tracked-file allowlist for `pipeline/**/*.py`,
`scripts/verify_production_release.py`, `web/studio/`, `web/viewer/`,
`make.py`, `pyproject.toml`, `LICENSE` and
`release/production-verify-and-run.md`. The tracked release template is copied
to package root as `VERIFY-AND-RUN.md`. Build/probe scripts, test files, Preview
scene data, caches, handoff, Git metadata and `.nantai-studio` are excluded.
Scene bytes come only from the resolver.

Write staging and temporary ZIP beside the requested output, verify staged
tree, close and verify ZIP, then publish archive and SHA sidecar with no-replace
semantics. The CLI requires `--acceptance-root`, `--version`, `--output`;
resolves the content-addressed report through
`load_latest_real_scene_acceptance`, derives exact Git HEAD by default, rejects
dirty release-owned files and prints ASCII-safe JSON.

- [ ] **Step 5: Run all release-core gates**

```powershell
python -m pytest -q tests/test_release_archive.py tests/test_preview_release.py tests/test_preview_release_cli.py tests/test_production_release_contract.py tests/test_production_release_verifier.py tests/test_production_release_builder.py tests/test_production_release_cli.py
python -m ruff check pipeline/release_archive.py pipeline/preview_release.py pipeline/production_release_contract.py pipeline/production_release_verifier.py pipeline/production_release_builder.py scripts/build_production_release.py scripts/verify_production_release.py tests/test_release_archive.py tests/test_production_release_contract.py tests/test_production_release_verifier.py tests/test_production_release_builder.py tests/test_production_release_cli.py
git diff --check -- pipeline/production_release_builder.py scripts/build_production_release.py tests/test_production_release_builder.py tests/test_production_release_cli.py release/production-verify-and-run.md
```

- [ ] **Step 6: Commit and push checkpoint 5**

```powershell
git add -- pipeline/production_release_builder.py scripts/build_production_release.py tests/test_production_release_builder.py tests/test_production_release_cli.py release/production-verify-and-run.md
git commit -m "feat: build deterministic Production runtime archives" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_release_builder.py scripts/build_production_release.py tests/test_production_release_builder.py tests/test_production_release_cli.py release/production-verify-and-run.md
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 6: Cross-platform task-runner entry points

**Files:**

- Modify: `make.py`
- Modify: `tests/test_make_runner.py`

- [ ] **Step 1: Write RED command-contract tests**

Add:

```python
def test_build_production_requires_acceptance_version_and_archive(make, monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_ROOT", "private/real-scene")
    monkeypatch.setenv("VERSION", "v1.0.0")
    monkeypatch.setenv("ARCHIVE", "dist/nantai-3d-v1.0.0-runtime.zip")
    make.build_production()
    assert make.commands[-1] == [
        make.PY,
        "scripts/build_production_release.py",
        "--acceptance-root",
        "private/real-scene",
        "--version",
        "v1.0.0",
        "--output",
        "dist/nantai-3d-v1.0.0-runtime.zip",
    ]


def test_verify_production_uses_exact_archive(make, monkeypatch):
    monkeypatch.setenv("ARCHIVE", "dist/nantai-3d-v1.0.0-runtime.zip")
    make.verify_production()
    assert make.commands[-1] == [
        make.PY,
        "scripts/verify_production_release.py",
        "dist/nantai-3d-v1.0.0-runtime.zip",
    ]
```

Missing `ACCEPTANCE_ROOT`, `VERSION` or `ARCHIVE` raises a clear error before
any subprocess. Preview targets remain unchanged.

- [ ] **Step 2: Run RED, implement, and verify**

```powershell
python -m pytest -q tests/test_make_runner.py -k "production"
python make.py help
python -m ruff check make.py tests/test_make_runner.py
git diff --check -- make.py tests/test_make_runner.py
```

Implement `build-production` and `verify-production` in `TARGETS`; document
their three environment variables in the module docstring. Do not add a default
`v1.0.0` path that could package a modeled fixture accidentally.

- [ ] **Step 3: Commit and push checkpoint 6**

```powershell
git add -- make.py tests/test_make_runner.py
git commit -m "feat: add explicit Production release runner targets" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- make.py tests/test_make_runner.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 7: Studio recognizes verified Production packages

**Files:**

- Modify: `pipeline/studio_server.py`
- Modify: `tests/test_studio_server.py`

- [ ] **Step 1: Write RED package-kind and evidence tests**

Cover:

- neither receipt: current `not-packaged`;
- valid Preview receipt: unchanged Preview projection;
- valid Production receipt: `package_kind=production`,
  `package_status=verified`, accepted-production real-scene envelope and exact
  report/scene identity;
- either receipt invalid: invalid without fallback;
- both receipt names present: ambiguous invalid package;
- valid Production package plus `.nantai-studio/real-scene` disagreement:
  invalid, not whichever claim is more permissive;
- public receipt verification never changes reconstruction trust itself.

Expected Production snapshot fields:

```python
assert snapshot["release"]["package_kind"] == "production"
assert snapshot["release"]["release_contract"] == "production-accepted-at-build"
assert snapshot["release"]["scene_trust_effect"] == "none"
assert snapshot["real_scene"]["decision"] == "accepted-production"
assert snapshot["real_scene"]["production_release_allowed"] is True
assert all(row["state"] == "succeeded" for row in snapshot["real_scene"]["stages"])
```

- [ ] **Step 2: Run RED**

```powershell
python -m pytest -q tests/test_studio_server.py -k "release or production_package"
```

- [ ] **Step 3: Implement dual receipt dispatch**

`_release_snapshot` checks `RELEASE-MANIFEST.json` and
`PRODUCTION-RELEASE.json` as mutually exclusive formats. It invokes only the
matching independent verifier and returns package kind, release contract,
acceptance report SHA and scene identity. `_real_scene_snapshot` continues to
validate private evidence in a development workspace; for a verified
Production runtime it projects the already verified public gate list. If both
sources exist, their report SHA and decision must agree exactly.

- [ ] **Step 4: Run server and release regressions**

```powershell
python -m pytest -q tests/test_studio_server.py tests/test_preview_release.py tests/test_production_release_verifier.py
python -m ruff check pipeline/studio_server.py tests/test_studio_server.py
git diff --check -- pipeline/studio_server.py tests/test_studio_server.py
```

- [ ] **Step 5: Commit and push checkpoint 7**

```powershell
git add -- pipeline/studio_server.py tests/test_studio_server.py
git commit -m "feat: project verified Production release evidence" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/studio_server.py tests/test_studio_server.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 8: Studio/Viewer Production UX and no synthetic fallback

**Files:**

- Modify: `web/studio/model.mjs`
- Modify: `web/studio/model.test.mjs`
- Modify: `web/studio/app.js`
- Modify: `web/studio/index-contract.test.mjs`
- Modify: `web/viewer/main.js`
- Modify: `web/viewer/startup-state.test.mjs`
- Modify: `web/viewer/model-preview.test.mjs`

- [ ] **Step 1: Write RED normalization and rendering tests**

Assert a Production badge appears only when all are true:

```javascript
release.package_kind === 'production'
release.package_status === 'verified'
release.release_contract === 'production-accepted-at-build'
release.scene_trust_effect === 'none'
real_scene.decision === 'accepted-production'
real_scene.production_release_allowed === true
```

Any mismatch renders `发布包 · 校验失败` and normalizes production permission to
false. Package integrity remains a separate chip from real capture, production
3DGS and metric alignment.

Add a Viewer test that a verified Production runtime failing to load its
required scene surfaces an actionable error and never requests
`model-preview/manifest.json` or repository synthetic PLYs.

- [ ] **Step 2: Run RED**

```powershell
node --test web/studio/model.test.mjs web/studio/index-contract.test.mjs web/viewer/startup-state.test.mjs web/viewer/model-preview.test.mjs
```

- [ ] **Step 3: Implement fail-closed labels and startup mode**

Normalize `release.package_kind` as `none | preview | production | invalid`.
Render:

```text
Production 包 · 已校验
真实采集 · Production 3DGS · 米制对齐
package integrity: verified · scene trust effect: none
```

The Viewer derives `requiredProductionScene` from the verified Studio snapshot.
When true, disable model-preview and point-demo fallbacks. Keep current Preview
behavior unchanged.

- [ ] **Step 4: Run full Studio/Viewer Node gates**

```powershell
node --test web/studio/*.test.mjs
node --test web/viewer/*.test.mjs
git diff --check -- web/studio/model.mjs web/studio/model.test.mjs web/studio/app.js web/studio/index-contract.test.mjs web/viewer/main.js web/viewer/startup-state.test.mjs web/viewer/model-preview.test.mjs
```

- [ ] **Step 5: Commit and push checkpoint 8**

```powershell
git add -- web/studio/model.mjs web/studio/model.test.mjs web/studio/app.js web/studio/index-contract.test.mjs web/viewer/main.js web/viewer/startup-state.test.mjs web/viewer/model-preview.test.mjs
git commit -m "feat: present verified Production runtime state" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- web/studio/model.mjs web/studio/model.test.mjs web/studio/app.js web/studio/index-contract.test.mjs web/viewer/main.js web/viewer/startup-state.test.mjs web/viewer/model-preview.test.mjs
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 9: Documentation, privacy audit and cross-platform CI

**Files:**

- Create: `docs/manual/production-runtime-release.md`
- Create: `tests/test_production_release_docs.py`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/production-v1-status.md`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/probe_production_release_content_id.py`

- [ ] **Step 1: Write RED documentation assertions**

Tests require:

- final vs Preview version distinction;
- exact build/verify/run commands;
- private/public closure distinction;
- no claim that hashes prove rights or physical reality;
- no default `v1.0.0` release before real evidence;
- raw photo/video, controls, credentials and workspaces excluded;
- downloaded-byte verification and real browser QA required.

- [ ] **Step 2: Write RED CI contract test**

Add a repository test that parses `.github/workflows/ci.yml` and requires one
focused `production-release-contract` job with:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest, macos-latest]
```

The job runs the standard-library contract/verifier/CLI tests and uploads a
modeled package content-ID text artifact produced by
`tests/probe_production_release_content_id.py`. The probe accepts one output
path, builds the same modeled tree through public contract/verifier APIs, and
writes exactly `<content-id>\n` as ASCII. A Linux compare job asserts all three
content IDs match. It must label the fixture
`modeled-contract-not-real-release`.

- [ ] **Step 3: Implement concise docs and CI**

README keeps one Production status paragraph and links the manual. The manual
owns detailed commands. Status marks release tooling separately from missing
real dataset/GPU/control/Viewer evidence.

- [ ] **Step 4: Run docs, CI-contract and full local gates**

```powershell
python -m pytest -q tests/test_production_release_docs.py tests/test_release_archive.py tests/test_production_release_contract.py tests/test_production_release_verifier.py tests/test_production_release_builder.py tests/test_production_release_cli.py tests/test_make_runner.py tests/test_studio_server.py
python -m pytest -q
python -m ruff check
node --test web/studio/*.test.mjs
node --test web/viewer/*.test.mjs
git diff --check
```

- [ ] **Step 5: Commit and push checkpoint 9**

```powershell
git add -- docs/manual/production-runtime-release.md tests/test_production_release_docs.py tests/probe_production_release_content_id.py README.md docs/README.md docs/production-v1-status.md .github/workflows/ci.yml
git commit -m "docs: add Production runtime release path" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- docs/manual/production-runtime-release.md tests/test_production_release_docs.py tests/probe_production_release_content_id.py README.md docs/README.md docs/production-v1-status.md .github/workflows/ci.yml
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- [ ] **Step 6: Wait for and inspect authoritative CI**

```powershell
$runId = gh run list --workflow ci.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --exit-status
gh run view $runId --json status,conclusion,url,headSha,jobs
```

Do not claim cross-platform completion until Ubuntu, Windows and macOS focused
jobs and the content-ID compare job all pass on the exact commit.

### Task 10: Real candidate, clean-room QA and final `v1.0.0`

**Files:**

- Create only after a real accepted candidate:
  `docs/releases/1.0.md`
- Modify only after all gates pass: `pyproject.toml`, `README.md`,
  `docs/README.md`, `docs/production-v1-status.md`
- Create ignored outputs below:
  `.nantai-studio/releases/v1.0.0/`

- [ ] **Step 1: Prove the private acceptance is real and complete**

Run against the actual private report:

```powershell
python scripts/real_scene.py accept --source $env:NANTAI_PRODUCTION_SOURCE --workspace $env:NANTAI_PRIVATE_WORKSPACE --run-id $env:NANTAI_PRODUCTION_RUN_ID --media-root $env:NANTAI_MEDIA_ROOT --rights $env:NANTAI_RIGHTS_RECEIPT --policy $env:NANTAI_REGISTRATION_POLICY --control-points $env:NANTAI_CONTROL_POINTS --geo-origin $env:NANTAI_GEO_ORIGIN --viewer-policy $env:NANTAI_VIEWER_POLICY --viewer-report $env:NANTAI_VIEWER_REPORT_V2 --human-review-policy $env:NANTAI_HUMAN_REVIEW_POLICY --human-visual-review $env:NANTAI_HUMAN_VISUAL_REVIEW
```

Expected: canonical decision has source role `production-acceptance`, all ten
gates accepted and `production_release_allowed=true`. Record the content SHA,
not private paths, in the release audit.

- [ ] **Step 2: Build twice from the clean exact candidate commit**

```powershell
python scripts/build_production_release.py --acceptance-root $env:NANTAI_ACCEPTANCE_ROOT --version v1.0.0 --output .nantai-studio/releases/v1.0.0/build-a.zip --json
python scripts/build_production_release.py --acceptance-root $env:NANTAI_ACCEPTANCE_ROOT --version v1.0.0 --output .nantai-studio/releases/v1.0.0/build-b.zip --json
python scripts/verify_production_release.py .nantai-studio/releases/v1.0.0/build-a.zip --json
python scripts/verify_production_release.py .nantai-studio/releases/v1.0.0/build-b.zip --json
```

Expected: equal package content IDs and equal archive SHA in the pinned builder
environment.

- [ ] **Step 3: Run clean-room Windows, macOS and Linux browser QA**

On each OS:

1. verify downloaded archive before extraction;
2. extract into a new directory;
3. verify the tree before dependency installation;
4. install declared runtime dependencies;
5. start `python make.py serve`;
6. open a fresh browser profile at `/web/studio/`;
7. exercise reset, orbit, zoom, allowed roam, three accepted poses and one
   chunk/LOD transition;
8. assert no console errors, indefinite loading, synthetic fallback or trust
   mismatch;
9. corrupt one copied scene artifact and prove verifier/UI fail closed.

Store screenshots, console logs and machine reports privately. Public Release
contains only the three acceptance screenshots already allowlisted.

- [ ] **Step 4: Perform final privacy and package whitelist audit**

```powershell
python scripts/verify_production_release.py .nantai-studio/releases/v1.0.0/build-a.zip --json
$env:ARCHIVE = (Resolve-Path .nantai-studio/releases/v1.0.0/build-a.zip).Path
$env:PRIVACY_POLICY = (Resolve-Path .nantai-studio/private/privacy-policy.json).Path
$env:PRIVACY_REPORT = (Join-Path $PWD ".nantai-studio/verification/privacy-v1.0.0.json")
python make.py audit-production-privacy
```

The canonical privacy report must have `valid=true`, zero findings and the same
package content ID as the independent verifier. Any private needle, absolute
filesystem path, credential/private-key marker, unverified file, symlink,
non-regular file or read drift blocks release. Never delete evidence from the
private acceptance to make the public scan pass; fix the projector/allowlist.

After the three clean-room browser QA reports and human review pass, stage the
exact four public Release assets from the verified candidate. This re-verifies
the archive, reruns the privacy audit and rejects modeled fixtures:

```powershell
$env:RELEASE_DIR = (Join-Path $PWD ".nantai-studio/releases/v1.0.0/public-assets")
python make.py stage-production-assets
```

The new directory must contain only the normalized runtime ZIP, its SHA sidecar,
`PRODUCTION-RELEASE.json` and `SHA256SUMS.txt`. This staging step does not
replace the two-build equality or clean-room QA gates.

- [ ] **Step 5: Promote version/docs only after real QA**

Set Python version to `1.0.0`, write concise `docs/releases/1.0.md`, and update
README/docs index. Run the complete gates again and commit:

```powershell
git add -- pyproject.toml docs/releases/1.0.md README.md docs/README.md docs/production-v1-status.md
git commit -m "release: prepare Nantai 3D 1.0" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pyproject.toml docs/releases/1.0.md README.md docs/README.md docs/production-v1-status.md
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- [ ] **Step 6: Tag, publish, download back and reverify**

Create `v1.0.0` only at the exact all-green commit. Publish only:

```text
nantai-3d-v1.0.0-runtime.zip
nantai-3d-v1.0.0-runtime.zip.sha256
PRODUCTION-RELEASE.json
SHA256SUMS.txt
```

Download all four GitHub assets into a new directory, then verify the bundle:

```powershell
$env:RELEASE_DIR = (Resolve-Path .\downloaded-v1.0.0-assets).Path
python make.py verify-production-assets
```

This compares the archive SHA to its sidecar, reruns the offline verifier
against downloaded bytes and proves the standalone receipt/checksum are
byte-identical to the verified archive copies. The release is incomplete until
this succeeds and the GitHub Actions run for the tag is green.

## Completion audit

Before declaring Production V1 complete, map each of the 12 design-definition
items to current evidence:

| Requirement | Required evidence |
|---|---|
| Fresh private acceptance | accepted decision bytes + report SHA |
| Real production scene | import receipt, manifest/chunk closure |
| No private payload | ZIP whitelist and privacy scan |
| Receipt/evidence/scene identity | offline verifier report |
| ZIP/tree offline verification | downloaded archive verifier output |
| Cross-platform content ID | Windows/macOS/Linux CI artifacts |
| Canonical archive reproducibility | two pinned-builder SHA values |
| Browser usability | three clean-room QA reports |
| No synthetic fallback | browser network/console assertions |
| Repository gates | exact commit CI URL and job conclusions |
| Downloaded-byte equality | release download SHA/verifier report |
| Version agreement | tag, Python, docs and receipt values |

Any missing, indirect or modeled-only row keeps the project at Preview.
