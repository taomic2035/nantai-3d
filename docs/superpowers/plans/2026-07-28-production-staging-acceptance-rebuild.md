# Production Staging Acceptance Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Production public staging reopen private acceptance and publish a candidate archive only when a fresh exact-HEAD rebuild is byte-for-byte identical.

**Architecture:** Extract one shared Git source-identity resolver into the existing Production builder, then make the staging layer call the authoritative builder directly inside its private staging directory. The copied candidate and fresh rebuild are compared as stable regular files before the existing offline verification, privacy audit and four-file no-replace publication continue.

**Tech Stack:** Python 3.11+ standard library, pytest, Ruff, existing durable I/O and Production release contracts, PowerShell-compatible `make.py`, GitHub Actions.

---

## File structure

- Modify `pipeline/production_release_builder.py`: own the reusable exact-HEAD
  source identity and keep the authoritative acceptance-bound builder.
- Modify `scripts/build_production_release.py`: consume the shared source
  identity instead of maintaining a second Git implementation.
- Modify `pipeline/production_release_assets.py`: perform the acceptance
  rebuild, stable byte comparison, cleanup and existing four-file publication.
- Modify `scripts/stage_production_release_assets.py`: require acceptance root
  and version and bind the fixed repository root.
- Modify `make.py`: require and pass all five staging environment inputs.
- Modify `tests/test_production_release_builder.py`: cover source-identity
  resolution and retain builder determinism/second-pass coverage.
- Modify `tests/test_production_release_cli.py`: prove the build CLI uses the
  shared identity resolver.
- Modify `tests/test_production_release_assets.py`: cover A1 through A10 and
  migrate every staging caller to the acceptance-bound API.
- Modify `tests/test_make_runner.py`: prove task-runner argument wiring and
  missing-input failure.
- Modify `tests/test_production_release_docs.py`: freeze the three distinct
  build/stage/download-verifier trust statements.
- Modify `docs/manual/production-runtime-release.md`: document the new staging
  inputs and same-environment rebuild requirement.
- Modify `docs/production-v1-status.md`: remove the ZIP-only staging overclaim.
- Modify `release/production-verify-and-run.md`: state exactly what downloaded
  verification does and does not prove.

No new production module is needed. The existing builder and staging modules
already own the correct responsibilities.

## Global execution rules

- Work only on `main` in the shared worktree.
- Before each commit, inspect `git status --short` and stage only the paths
  listed by that task.
- Never use `git add -A`, `git commit -a`, reset, rebase or stash.
- Every Codex commit ends with:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

- Push each green task immediately:

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- If push fails, wait five seconds and retry with the same one-shot proxy. Do
  not alter persistent Git configuration.

### Task 1: Shared exact-HEAD source identity

**Files:**
- Modify: `pipeline/production_release_builder.py:78-121`
- Modify: `scripts/build_production_release.py:6-65`
- Modify: `tests/test_production_release_builder.py`
- Modify: `tests/test_production_release_cli.py:63-123`

- [ ] **Step 1: Write failing resolver and CLI tests**

Add this import and test to `tests/test_production_release_builder.py`:

```python
from types import SimpleNamespace


def test_source_identity_resolves_exact_head_and_tracked_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], Path]] = []

    def run(command, *, cwd, **_kwargs):
        calls.append((command, cwd))
        if command[1:] == ["rev-parse", "--verify", "HEAD"]:
            return SimpleNamespace(
                returncode=0,
                stdout="a" * 40 + "\n",
                stderr="",
            )
        if command[1:] == ["ls-files", "-z", "--"]:
            return SimpleNamespace(
                returncode=0,
                stdout="web/z.js\0LICENSE\0web/a.js\0",
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    identity = builder_module.resolve_production_release_source_identity(
        tmp_path
    )

    assert identity.source_commit == "a" * 40
    assert identity.tracked_files == (
        "LICENSE",
        "web/a.js",
        "web/z.js",
    )
    assert calls == [
        (["git", "rev-parse", "--verify", "HEAD"], tmp_path.absolute()),
        (["git", "ls-files", "-z", "--"], tmp_path.absolute()),
    ]
```

Replace the subprocess patch in
`test_build_cli_uses_exact_git_head_and_tracked_allowlist` with a shared
resolver patch:

```python
identity = builder_module.ProductionReleaseSourceIdentity(
    source_commit="a" * 40,
    tracked_files=("LICENSE", "pipeline/runtime.py"),
)
monkeypatch.setattr(
    build_cli,
    "resolve_production_release_source_identity",
    lambda root: identity,
)
```

Keep the assertions that the builder receives exactly those two fields.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
python -m pytest -q `
  tests/test_production_release_builder.py::test_source_identity_resolves_exact_head_and_tracked_files `
  tests/test_production_release_cli.py::test_build_cli_uses_exact_git_head_and_tracked_allowlist
```

Expected: FAIL because
`ProductionReleaseSourceIdentity` and
`resolve_production_release_source_identity` do not exist.

- [ ] **Step 3: Add the shared resolver**

In `pipeline/production_release_builder.py`, add:

```python
@dataclass(frozen=True)
class ProductionReleaseSourceIdentity:
    source_commit: str
    tracked_files: tuple[str, ...]


def _git_source_output(repo_root: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProductionReleaseBuilderError(
            "Git source identity cannot be resolved"
        )
    return completed.stdout


def resolve_production_release_source_identity(
    repo_root: str | Path,
) -> ProductionReleaseSourceIdentity:
    root = Path(repo_root).expanduser().absolute()
    source_commit = _git_source_output(
        root,
        ["rev-parse", "--verify", "HEAD"],
    ).strip()
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ProductionReleaseBuilderError(
            "Git source commit is not canonical"
        )
    tracked_files = tuple(
        sorted(
            relative
            for relative in _git_source_output(
                root,
                ["ls-files", "-z", "--"],
            ).split("\0")
            if relative
        )
    )
    if not tracked_files:
        raise ProductionReleaseBuilderError(
            "Git tracked source list is empty"
        )
    return ProductionReleaseSourceIdentity(
        source_commit=source_commit,
        tracked_files=tracked_files,
    )
```

Add `import re` beside the existing standard-library imports.

In `scripts/build_production_release.py`, remove `import subprocess`, remove
the private `_git_output`, import the shared resolver, and use:

```python
identity = resolve_production_release_source_identity(_REPO_ROOT)
result = build_production_release_archive(
    repo_root=_REPO_ROOT,
    acceptance_root=arguments.acceptance_root,
    output_path=arguments.output,
    version=arguments.version,
    source_commit=identity.source_commit,
    tracked_files=identity.tracked_files,
)
```

- [ ] **Step 4: Run focused tests and lint**

Run:

```powershell
python -m pytest -q `
  tests/test_production_release_builder.py `
  tests/test_production_release_cli.py
python -m ruff check `
  pipeline/production_release_builder.py `
  scripts/build_production_release.py `
  tests/test_production_release_builder.py `
  tests/test_production_release_cli.py
git diff --check -- `
  pipeline/production_release_builder.py `
  scripts/build_production_release.py `
  tests/test_production_release_builder.py `
  tests/test_production_release_cli.py
```

Expected: all tests pass, Ruff reports no errors and `git diff --check`
prints nothing.

- [ ] **Step 5: Commit and push Task 1**

```powershell
git status --short
git add -- `
  pipeline/production_release_builder.py `
  scripts/build_production_release.py `
  tests/test_production_release_builder.py `
  tests/test_production_release_cli.py
git commit `
  -m "refactor: share production source identity" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" `
  -- `
  pipeline/production_release_builder.py `
  scripts/build_production_release.py `
  tests/test_production_release_builder.py `
  tests/test_production_release_cli.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 2: Acceptance-bound rebuild and byte equality

**Files:**
- Modify: `pipeline/production_release_assets.py:14-38,77-214,387-487`
- Modify: `tests/test_production_release_assets.py:1-183`

- [ ] **Step 1: Add a reusable acceptance-rebuild test binder**

Import the module and builder types in
`tests/test_production_release_assets.py`:

```python
import pipeline.production_release_assets as assets_module
from pipeline.production_release_builder import (
    ProductionReleaseBuild,
    ProductionReleaseSourceIdentity,
)
from pipeline.production_release_contract import (
    verify_production_release_archive,
)
```

Extend the existing real-contract fixture so tests can build independently
valid archives with distinct source identities:

```python
def _write_real_contract_tree(
    root: Path,
    *,
    version: str = "v1.0.0",
    source_commit: str = "a" * 40,
) -> dict[str, object]:
    root.mkdir()
    payloads = modeled_payloads()
    public_evidence = modeled_public_evidence()
    public_evidence["fixture_kind"] = None
    payloads["evidence/public-evidence.json"] = (
        "public-evidence",
        canonical_json_bytes(public_evidence),
    )
    artifacts = modeled_artifact_records()
    for artifact in artifacts:
        if artifact["path"] == "evidence/public-evidence.json":
            payload = payloads["evidence/public-evidence.json"][1]
            artifact["bytes"] = len(payload)
            artifact["sha256"] = hashlib.sha256(payload).hexdigest()
    for relative, (_role, payload) in payloads.items():
        destination = root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    receipt = build_production_receipt(
        version=version,
        source_commit=source_commit,
        artifacts=artifacts,
        protected_roots=("web", "scripts", "pipeline", "evidence"),
        entrypoints=modeled_entrypoints(),
        public_evidence=public_evidence,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    (root / PRODUCTION_RELEASE_NAME).write_bytes(receipt_bytes)
    checksum_rows = [
        f"{row['sha256']}  {row['path']}\n"
        for row in receipt["artifacts"]
    ]
    checksum_rows.append(
        f"{hashlib.sha256(receipt_bytes).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    (root / CHECKSUMS_NAME).write_bytes(
        "".join(sorted(checksum_rows)).encode("ascii")
    )
    return receipt


def _write_real_contract_archive(
    root: Path,
    *,
    version: str = "v1.0.0",
    source_commit: str = "a" * 40,
) -> tuple[Path, dict[str, object]]:
    root.mkdir(parents=True)
    tree = root / "runtime"
    receipt = _write_real_contract_tree(
        tree,
        version=version,
        source_commit=source_commit,
    )
    archive = root / "runtime.zip"
    write_modeled_production_archive(
        tree,
        archive,
        wrapper=f"nantai-3d-{version}",
    )
    return archive, receipt
```

Add the acceptance-rebuild binder:

```python
def _bind_acceptance_rebuild(
    monkeypatch,
    rebuilt_source: Path,
    *,
    identities: tuple[ProductionReleaseSourceIdentity, ...] | None = None,
) -> list[dict[str, object]]:
    stable = ProductionReleaseSourceIdentity(
        source_commit="a" * 40,
        tracked_files=("LICENSE", "pipeline/runtime.py"),
    )
    queue = iter(identities or (stable, stable))
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        assets_module,
        "resolve_production_release_source_identity",
        lambda _root: next(queue),
    )

    def build(**kwargs):
        calls.append(kwargs)
        output_path = Path(kwargs["output_path"])
        payload = rebuilt_source.read_bytes()
        output_path.write_bytes(payload)
        archive_sha256 = hashlib.sha256(payload).hexdigest()
        output_path.with_suffix(".zip.sha256").write_text(
            f"{archive_sha256}  {output_path.name}\n",
            encoding="ascii",
        )
        return ProductionReleaseBuild(
            archive_path=output_path,
            archive_sha256=archive_sha256,
            package_content_id="b" * 64,
            artifact_count=9,
            total_bytes=len(payload),
            scene_identity="scene-" + "c" * 64,
            acceptance_report_sha256="d" * 64,
        )

    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        build,
    )
    return calls
```

The helper emulates only the builder publication mechanics. Candidate
verification and privacy auditing continue to use the real implementations.

- [ ] **Step 2: Write RED A9 positive and A1 forged-fixture tests**

Replace `test_stage_exports_only_four_verified_public_assets` with an
acceptance-bound version:

```python
def test_stage_exports_only_acceptance_rebuilt_public_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tree = tmp_path / "runtime"
    receipt = _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    calls = _bind_acceptance_rebuild(monkeypatch, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"

    result = stage_production_release_assets(
        repo_root=tmp_path / "repo",
        acceptance_root=tmp_path / "accepted",
        version="v1.0.0",
        archive_path=source,
        privacy_policy_path=policy,
        output_dir=output,
    )

    assert len(calls) == 1
    assert calls[0]["repo_root"] == (tmp_path / "repo").absolute()
    assert calls[0]["acceptance_root"] == (
        tmp_path / "accepted"
    ).absolute()
    assert calls[0]["version"] == "v1.0.0"
    assert calls[0]["source_commit"] == "a" * 40
    assert sorted(path.name for path in output.iterdir()) == [
        PRODUCTION_RELEASE_NAME,
        CHECKSUMS_NAME,
        "nantai-3d-v1.0.0-runtime.zip",
        "nantai-3d-v1.0.0-runtime.zip.sha256",
    ]
    assert result.package_content_id == receipt["package"]["content_id"]
```

Add the regression for the proven bypass:

```python
def test_stage_rejects_resigned_fixture_kind_null_without_acceptance_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    forged, _forged_receipt = _write_real_contract_archive(
        tmp_path / "forged",
        source_commit="a" * 40,
    )
    accepted, _accepted_receipt = _write_real_contract_archive(
        tmp_path / "accepted",
        source_commit="b" * 40,
    )
    assert verify_production_release_archive(forged).valid is True
    assert verify_production_release_archive(accepted).valid is True
    assert forged.read_bytes() != accepted.read_bytes()
    _bind_acceptance_rebuild(monkeypatch, accepted)

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild",
    ):
        stage_production_release_assets(
            repo_root=tmp_path / "repo",
            acceptance_root=tmp_path / "accepted",
            version="v1.0.0",
            archive_path=forged,
            privacy_policy_path=_privacy_policy(
                tmp_path / "privacy-policy.json"
            ),
            output_dir=tmp_path / "release-assets",
        )

    assert not (tmp_path / "release-assets").exists()
```

- [ ] **Step 3: Run the two tests and confirm RED**

Run:

```powershell
python -m pytest -q `
  tests/test_production_release_assets.py::test_stage_exports_only_acceptance_rebuilt_public_assets `
  tests/test_production_release_assets.py::test_stage_rejects_resigned_fixture_kind_null_without_acceptance_match
```

Expected: FAIL because staging does not accept the new inputs and does not call
the builder.

- [ ] **Step 4: Implement stable lockstep equality**

In `pipeline/production_release_assets.py`, import:

```python
from pipeline.production_release_builder import (
    ProductionReleaseBuilderError,
    build_production_release_archive,
    resolve_production_release_source_identity,
)
```

Add:

```python
def _stable_regular_files_equal(left: Path, right: Path) -> bool:
    try:
        left_path_before = left.lstat()
        right_path_before = right.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production acceptance rebuild is unavailable"
        ) from exc
    for observed in (left_path_before, right_path_before):
        if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(
            observed.st_mode
        ):
            raise ProductionReleaseAssetsError(
                "Production acceptance comparison requires regular files"
            )
    if left_path_before.st_size != right_path_before.st_size:
        return False

    equal = True
    try:
        with left.open("rb") as left_stream:
            with right.open("rb") as right_stream:
                left_descriptor_before = os.fstat(left_stream.fileno())
                right_descriptor_before = os.fstat(right_stream.fileno())
                while True:
                    left_chunk = left_stream.read(_COPY_CHUNK_BYTES)
                    right_chunk = right_stream.read(_COPY_CHUNK_BYTES)
                    if left_chunk != right_chunk:
                        equal = False
                    if not left_chunk and not right_chunk:
                        break
                left_descriptor_after = os.fstat(left_stream.fileno())
                right_descriptor_after = os.fstat(right_stream.fileno())
        left_path_after = left.lstat()
        right_path_after = right.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production acceptance comparison failed"
        ) from exc

    if (
        _signature(left_path_before) != _signature(left_descriptor_before)
        or _signature(left_path_before) != _signature(left_descriptor_after)
        or _signature(left_path_before) != _signature(left_path_after)
        or _signature(right_path_before) != _signature(
            right_descriptor_before
        )
        or _signature(right_path_before) != _signature(
            right_descriptor_after
        )
        or _signature(right_path_before) != _signature(right_path_after)
    ):
        raise ProductionReleaseAssetsError(
            "Production acceptance bytes changed during comparison"
        )
    return equal
```

- [ ] **Step 5: Add the rebuild to staging**

Extend the function signature:

```python
def stage_production_release_assets(
    *,
    repo_root: str | Path,
    acceptance_root: str | Path,
    version: str,
    archive_path: str | Path,
    privacy_policy_path: str | Path,
    output_dir: str | Path,
) -> ProductionReleaseAssets:
```

After copying the source candidate and before extracting it, add:

```python
root = Path(repo_root).expanduser().absolute()
acceptance = Path(acceptance_root).expanduser().absolute()
rebuilt = staging / ".acceptance-rebuild.zip"
rebuilt_sidecar = rebuilt.with_suffix(f"{rebuilt.suffix}.sha256")
try:
    source_before = resolve_production_release_source_identity(root)
    rebuilt_result = build_production_release_archive(
        repo_root=root,
        acceptance_root=acceptance,
        output_path=rebuilt,
        version=version,
        source_commit=source_before.source_commit,
        tracked_files=source_before.tracked_files,
    )
    source_after = resolve_production_release_source_identity(root)
except ProductionReleaseBuilderError as exc:
    raise ProductionReleaseAssetsError(
        "Production acceptance rebuild failed"
    ) from exc

if source_after != source_before:
    raise ProductionReleaseAssetsError(
        "Production source identity changed during acceptance rebuild"
    )
if (
    rebuilt_result.archive_path != rebuilt
    or rebuilt_result.archive_sha256
    != stable_regular_file_digest(rebuilt).sha256
    or archive_sha256 != rebuilt_result.archive_sha256
    or not _stable_regular_files_equal(candidate, rebuilt)
):
    raise ProductionReleaseAssetsError(
        "Production candidate does not match acceptance rebuild"
    )
rebuilt.unlink()
rebuilt_sidecar.unlink()
```

After `verify_production_release_tree(extracted)`, explicitly require:

```python
if verification_before.version != version:
    raise ProductionReleaseAssetsError(
        "Production candidate version disagrees with requested version"
    )
```

Keep the existing fixture-kind/release-contract check as defense in depth, not
as the acceptance authority.

- [ ] **Step 6: Add A2, A3 and A4 negative cases**

Use one verifier-valid rebuilt archive per case:

```python
@pytest.mark.parametrize(
    ("candidate_commit", "accepted_commit", "requested_version"),
    (
        ("b" * 40, "a" * 40, "v1.0.0"),  # A2 source_commit resign
        ("a" * 40, "c" * 40, "v1.0.0"),  # A3 other acceptance
    ),
)
def test_stage_rejects_candidate_not_derived_from_requested_acceptance(
    tmp_path: Path,
    monkeypatch,
    candidate_commit: str,
    accepted_commit: str,
    requested_version: str,
) -> None:
    candidate = _write_real_contract_archive(
        tmp_path / "candidate",
        source_commit=candidate_commit,
    )
    rebuilt = _write_real_contract_archive(
        tmp_path / "rebuilt",
        source_commit=accepted_commit,
    )
    _bind_acceptance_rebuild(monkeypatch, rebuilt)

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild",
    ):
        stage_production_release_assets(
            repo_root=tmp_path / "repo",
            acceptance_root=tmp_path / "accepted",
            version=requested_version,
            archive_path=candidate,
            privacy_policy_path=_privacy_policy(
                tmp_path / "privacy-policy.json"
            ),
            output_dir=tmp_path / "release-assets",
        )
```

Add the explicit version case:

```python
def test_stage_rejects_requested_version_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _write_real_contract_archive(
        tmp_path / "candidate",
        version="v1.0.0",
    )
    _bind_acceptance_rebuild(monkeypatch, candidate)

    with pytest.raises(ProductionReleaseAssetsError, match="version"):
        stage_production_release_assets(
            repo_root=tmp_path / "repo",
            acceptance_root=tmp_path / "accepted",
            version="v1.0.1",
            archive_path=candidate,
            privacy_policy_path=_privacy_policy(
                tmp_path / "privacy-policy.json"
            ),
            output_dir=tmp_path / "release-assets",
        )
```

- [ ] **Step 7: Run Task 2 tests and lint**

Run:

```powershell
python -m pytest -q tests/test_production_release_assets.py -k "stage"
python -m ruff check `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py
git diff --check -- `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py
```

Expected: all staging tests pass, Ruff reports no errors and the diff check is
empty.

- [ ] **Step 8: Commit and push Task 2**

```powershell
git status --short
git add -- `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py
git commit `
  -m "fix: bind release staging to fresh acceptance" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" `
  -- `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 3: TOCTOU, cleanup and no-replace hardening

**Files:**
- Modify: `pipeline/production_release_assets.py`
- Modify: `tests/test_production_release_assets.py:141-304`
- Test: `tests/test_production_release_builder.py:534-565,903-945`

- [ ] **Step 1: Migrate every staging test to explicit trust inputs**

Add a helper:

```python
def _stage_kwargs(
    tmp_path: Path,
    *,
    archive: Path,
    policy: Path,
    output: Path,
) -> dict[str, object]:
    return {
        "repo_root": tmp_path / "repo",
        "acceptance_root": tmp_path / "accepted",
        "version": "v1.0.0",
        "archive_path": archive,
        "privacy_policy_path": policy,
        "output_dir": output,
    }
```

For every existing test that expects to reach privacy, publication or
download-verification behavior, add `monkeypatch`, call
`_bind_acceptance_rebuild(monkeypatch, source)`, and invoke:

```python
stage_production_release_assets(
    **_stage_kwargs(
        tmp_path,
        archive=source,
        policy=policy,
        output=output,
    )
)
```

Tests that reject an existing or junction output must continue rejecting before
the resolver or builder is called.

- [ ] **Step 2: Add RED source-drift and builder-failure tests**

```python
def test_stage_rejects_source_identity_drift_without_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_real_contract_archive(tmp_path / "candidate")
    before = ProductionReleaseSourceIdentity(
        source_commit="a" * 40,
        tracked_files=("LICENSE",),
    )
    after = ProductionReleaseSourceIdentity(
        source_commit="b" * 40,
        tracked_files=("LICENSE",),
    )
    _bind_acceptance_rebuild(
        monkeypatch,
        source,
        identities=(before, after),
    )
    output = tmp_path / "release-assets"

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="source identity changed",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=_privacy_policy(tmp_path / "privacy-policy.json"),
                output=output,
            )
        )

    assert not output.exists()
    assert not tuple(tmp_path.glob(".release-assets.*.staging"))
```

```python
def test_stage_translates_acceptance_rebuild_failure_and_cleans_up(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _write_real_contract_archive(tmp_path / "candidate")
    identity = ProductionReleaseSourceIdentity(
        source_commit="a" * 40,
        tracked_files=("LICENSE",),
    )
    monkeypatch.setattr(
        assets_module,
        "resolve_production_release_source_identity",
        lambda _root: identity,
    )
    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        lambda **_kwargs: (_ for _ in ()).throw(
            ProductionReleaseBuilderError(
                "acceptance evidence changed during validation"
            )
        ),
    )
    output = tmp_path / "release-assets"

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild failed",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=_privacy_policy(tmp_path / "privacy-policy.json"),
                output=output,
            )
        )

    assert not output.exists()
    assert not tuple(tmp_path.glob(".release-assets.*.staging"))
```

The second test represents A5/A6 at the staging boundary. Keep the existing
builder tests that separately prove dirty source and acceptance second-pass
rejection.

- [ ] **Step 3: Add candidate/rebuild comparison and private-artifact tests**

```python
def test_stage_rejects_candidate_or_rebuild_byte_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = _write_real_contract_archive(tmp_path / "candidate")
    rebuilt = _write_real_contract_archive(
        tmp_path / "rebuilt",
        source_commit="b" * 40,
    )
    _bind_acceptance_rebuild(monkeypatch, rebuilt)
    output = tmp_path / "release-assets"

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="does not match acceptance rebuild",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=candidate,
                policy=_privacy_policy(tmp_path / "privacy-policy.json"),
                output=output,
            )
        )

    assert not output.exists()
    assert not tuple(tmp_path.glob(".release-assets.*.staging"))
```

Extend the A9 positive test:

```python
assert not any(
    "acceptance-rebuild" in path.name
    for path in output.iterdir()
)
assert len(tuple(output.iterdir())) == 4
```

Keep the existing existing-output and junction tests as A8 and ensure they
still prove the sentinel is untouched.

- [ ] **Step 4: Run hardening tests**

Run:

```powershell
python -m pytest -q `
  tests/test_production_release_assets.py `
  tests/test_production_release_builder.py
python -m ruff check `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py `
  tests/test_production_release_builder.py
git diff --check -- `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py `
  tests/test_production_release_builder.py
```

Expected: all tests pass; negative cases leave no output or staging directory.

- [ ] **Step 5: Commit and push Task 3**

```powershell
git status --short
git add -- `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py `
  tests/test_production_release_builder.py
git commit `
  -m "test: harden acceptance staging lifecycle" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" `
  -- `
  pipeline/production_release_assets.py `
  tests/test_production_release_assets.py `
  tests/test_production_release_builder.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 4: CLI and task-runner caller closure

**Files:**
- Modify: `scripts/stage_production_release_assets.py:22-46`
- Modify: `make.py:310-325`
- Modify: `tests/test_production_release_assets.py:307-382`
- Modify: `tests/test_make_runner.py:631-686`

- [ ] **Step 1: Write RED CLI binding assertions**

Update `test_cli_stages_exact_inputs_and_emits_ascii_json`:

```python
acceptance = tmp_path / "accepted"
exit_code = assets_cli.main(
    [
        "--acceptance-root",
        str(acceptance),
        "--version",
        "v1.0.0",
        "--archive",
        str(archive),
        "--privacy-policy",
        str(policy),
        "--output-dir",
        str(output),
    ]
)

assert observed == {
    "repo_root": assets_cli._REPO_ROOT,
    "acceptance_root": acceptance,
    "version": "v1.0.0",
    "archive_path": archive,
    "privacy_policy_path": policy,
    "output_dir": output,
}
```

Update the failure test with the same two required arguments. Add:

```python
@pytest.mark.parametrize(
    "missing_flag",
    ("--acceptance-root", "--version"),
)
def test_stage_cli_requires_acceptance_binding(
    tmp_path: Path,
    missing_flag: str,
) -> None:
    arguments = {
        "--acceptance-root": str(tmp_path / "accepted"),
        "--version": "v1.0.0",
        "--archive": str(tmp_path / "candidate.zip"),
        "--privacy-policy": str(tmp_path / "policy.json"),
        "--output-dir": str(tmp_path / "release-assets"),
    }
    argv = [
        token
        for item in arguments.items()
        if item[0] != missing_flag
        for token in item
    ]
    with pytest.raises(SystemExit) as raised:
        assets_cli.main(argv)
    assert raised.value.code == 2
```

- [ ] **Step 2: Write RED make.py caller tests**

Update `test_stage_production_assets_uses_exact_inputs` to set
`ACCEPTANCE_ROOT` and `VERSION` and expect:

```python
assert calls == [
    [
        make.PY,
        "scripts/stage_production_release_assets.py",
        "--acceptance-root",
        "private/real-scene",
        "--version",
        "v1.0.0",
        "--archive",
        "dist/build-a.zip",
        "--privacy-policy",
        "private/privacy-policy.json",
        "--output-dir",
        "dist/v1.0.0-release-assets",
    ]
]
```

Change the missing-input parameterization to:

```python
@pytest.mark.parametrize(
    "missing",
    (
        "ACCEPTANCE_ROOT",
        "VERSION",
        "ARCHIVE",
        "PRIVACY_POLICY",
        "RELEASE_DIR",
    ),
)
```

- [ ] **Step 3: Run caller tests and confirm RED**

Run:

```powershell
python -m pytest -q `
  tests/test_production_release_assets.py -k "cli" `
  tests/test_make_runner.py::TestProductionReleaseTargets
```

Expected: the new argument expectations fail because neither caller passes
acceptance root or version.

- [ ] **Step 4: Implement CLI and make.py wiring**

In `scripts/stage_production_release_assets.py`, add:

```python
parser.add_argument("--acceptance-root", type=Path, required=True)
parser.add_argument("--version", required=True)
```

Call:

```python
result = stage_production_release_assets(
    repo_root=_REPO_ROOT,
    acceptance_root=arguments.acceptance_root,
    version=arguments.version,
    archive_path=arguments.archive,
    privacy_policy_path=arguments.privacy_policy,
    output_dir=arguments.output_dir,
)
```

In `make.py`, replace `stage_production_assets` with:

```python
def stage_production_assets() -> None:
    acceptance_root = _required_environment("ACCEPTANCE_ROOT")
    version = _required_environment("VERSION")
    archive = _required_environment("ARCHIVE")
    policy = _required_environment("PRIVACY_POLICY")
    output_dir = _required_environment("RELEASE_DIR")
    run(
        [
            PY,
            "scripts/stage_production_release_assets.py",
            "--acceptance-root",
            acceptance_root,
            "--version",
            version,
            "--archive",
            archive,
            "--privacy-policy",
            policy,
            "--output-dir",
            output_dir,
        ]
    )
```

Do not change `verify_production_assets`; A10 requires the downloaded verifier
to remain independent of private acceptance.

- [ ] **Step 5: Run caller and A10 tests**

Run:

```powershell
python -m pytest -q `
  tests/test_production_release_assets.py `
  tests/test_make_runner.py::TestProductionReleaseTargets
python -m ruff check `
  scripts/stage_production_release_assets.py `
  make.py `
  tests/test_production_release_assets.py `
  tests/test_make_runner.py
git diff --check -- `
  scripts/stage_production_release_assets.py `
  make.py `
  tests/test_production_release_assets.py `
  tests/test_make_runner.py
```

Expected: all tests pass. In particular,
`test_verify_four_asset_bundle_rejects_mixed_or_extra_bytes` and the verify CLI
tests still run without `ACCEPTANCE_ROOT`.

- [ ] **Step 6: Commit and push Task 4**

```powershell
git status --short
git add -- `
  scripts/stage_production_release_assets.py `
  make.py `
  tests/test_production_release_assets.py `
  tests/test_make_runner.py
git commit `
  -m "fix: require acceptance-bound release staging" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" `
  -- `
  scripts/stage_production_release_assets.py `
  make.py `
  tests/test_production_release_assets.py `
  tests/test_make_runner.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 5: Truthful documentation and final verification

**Files:**
- Modify: `docs/manual/production-runtime-release.md:28-29,79-116`
- Modify: `docs/production-v1-status.md:26-30,48-66`
- Modify: `release/production-verify-and-run.md:3-6`
- Modify: `tests/test_production_release_docs.py:27-114`
- Verify: `.github/workflows/ci.yml:231-317`

- [ ] **Step 1: Write RED documentation contract tests**

In `tests/test_production_release_docs.py`, require the staging section to say:

```python
assert re.search(
    r"stage-production-assets.*重新打开.*acceptance.*逐字节",
    manual,
    re.DOTALL | re.IGNORECASE,
)
assert re.search(
    r"ACCEPTANCE_ROOT.*VERSION.*stage-production-assets",
    manual,
    re.DOTALL,
)
assert re.search(
    r"verify-production-assets.*不.*重新打开.*acceptance",
    manual,
    re.DOTALL | re.IGNORECASE,
)
```

Add:

```python
def test_downloaded_runtime_guide_does_not_claim_to_reopen_acceptance() -> None:
    guide = _read(ROOT / "release/production-verify-and-run.md")
    assert "byte-integrity-verified" in guide
    assert "does not reopen" in guide
    assert "ACCEPTANCE_ROOT" in guide
    assert "re-prove real CUDA" in guide
```

Extend the status test:

```python
assert "acceptance rebuild" in status
assert "staging" in status
assert "download verifier" in status
```

- [ ] **Step 2: Run documentation tests and confirm RED**

Run:

```powershell
python -m pytest -q tests/test_production_release_docs.py
```

Expected: FAIL because current text still describes staging as a ZIP-only
verification and privacy step.

- [ ] **Step 3: Update the maintainer manual**

In the staging command block, add:

```powershell
$env:ACCEPTANCE_ROOT = (Resolve-Path .nantai-studio\real-scene\accepted).Path
$env:VERSION = "v1.0.0"
```

Replace the staging guarantee with exactly:

```text
stage-production-assets 从 ACCEPTANCE_ROOT 与 VERSION 在当前 exact HEAD
重新打开真实 acceptance，使用同一固定 builder 环境确定性重建 ZIP，并与输入
candidate 逐字节比较。完全相同后才重新执行隐私审计并发布四件套。
```

State that the candidate build and staging rebuild must run in the same pinned
environment because cross-zlib archive bytes are not a portable identity.
Cross-platform identity is the package content ID and artifact SHA/length set.

Replace the download-verifier claim with:

```text
verify-production-assets 只证明下载的四件套与已经授权发布的字节和内部合同一致；
它不重新打开 ACCEPTANCE_ROOT，也不重新证明真实 CUDA、metric alignment、
Viewer QA 或 human review。
```

- [ ] **Step 4: Update status and bundled runtime guide**

In `docs/production-v1-status.md`, change the release-tooling row to state that
staging now performs an `acceptance rebuild` and exact candidate-byte match.
Keep the five real external gates explicitly open.

In `release/production-verify-and-run.md`, replace lines 3-6 with:

```text
This archive is byte-integrity-verified when the bundled offline verifier
succeeds. Its receipt and public evidence bind the runtime bytes to an
acceptance report that was reopened by the pre-release staging step from
ACCEPTANCE_ROOT at the exact source commit. This downloaded verifier does not
reopen that private root or re-prove real CUDA, metric alignment, Viewer QA or
human review.
```

- [ ] **Step 5: Run focused Production verification**

Run:

```powershell
python -m pytest -q `
  tests/test_release_archive.py `
  tests/test_production_release_contract.py `
  tests/test_production_release_verifier.py `
  tests/test_production_release_cli.py `
  tests/test_production_release_privacy.py `
  tests/test_production_release_assets.py `
  tests/test_production_release_docs.py `
  tests/test_production_runtime_runner.py `
  tests/test_production_release_builder.py `
  tests/test_make_runner.py
python -m ruff check `
  pipeline/production_release_builder.py `
  pipeline/production_release_assets.py `
  scripts/build_production_release.py `
  scripts/stage_production_release_assets.py `
  make.py `
  tests/test_production_release_builder.py `
  tests/test_production_release_cli.py `
  tests/test_production_release_assets.py `
  tests/test_production_release_docs.py `
  tests/test_make_runner.py
git diff --check
```

Expected: all focused tests pass, Ruff reports no errors and the diff check is
empty.

- [ ] **Step 6: Run the repository-wide verification**

Run:

```powershell
python make.py lint
python make.py test
```

Expected: both commands exit 0. Do not describe the change as complete if
either command is interrupted, times out or reports failures.

- [ ] **Step 7: Commit and push Task 5**

```powershell
git status --short
git add -- `
  docs/manual/production-runtime-release.md `
  docs/production-v1-status.md `
  release/production-verify-and-run.md `
  tests/test_production_release_docs.py
git commit `
  -m "docs: explain acceptance-bound release staging" `
  -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" `
  -- `
  docs/manual/production-runtime-release.md `
  docs/production-v1-status.md `
  release/production-verify-and-run.md `
  tests/test_production_release_docs.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

- [ ] **Step 8: Verify the pushed exact HEAD**

Run:

```powershell
$head = (git rev-parse HEAD).Trim()
$origin = (git rev-parse origin/main).Trim()
if ($head -ne $origin) { throw "origin/main is not exact HEAD" }
git status --short --branch
gh run list --commit $head --limit 3 `
  --json databaseId,status,conclusion,url,headSha
```

Expected: local and remote SHA are equal, the worktree is clean and the exact
HEAD CI run exists. Monitor that run to terminal success before closing the
repo-local P1.

## Completion audit

Before reporting this plan complete, verify each specification item against
machine evidence:

| Requirement | Evidence |
|---|---|
| Legacy ZIP-only staging removed | API/CLI/make tests require acceptance and version |
| Exact source identity | resolver tests plus before/after equality test |
| Fresh private acceptance reopened | builder call observation and builder acceptance tests |
| Stable byte equality | A1-A4/A7/A9 tests |
| Dirty source and acceptance drift fail closed | builder tests plus staging error/cleanup tests |
| Existing output/junction remains no-replace | A8 tests and unchanged sentinel |
| Exactly four public assets | A9 directory allowlist assertion |
| Download verifier remains offline | A10 function/CLI/docs tests |
| Trust wording is honest | documentation contract tests |
| Cross-platform identity remains portable | existing three-OS package-content-ID comparison |
| Small commits and remote sync | commit log, origin SHA and clean status |
| Full regression safety | `python make.py lint`, `python make.py test`, exact-HEAD CI |

Passing this audit closes only the repo-local staging trust gap. Production V1
still requires real capture, accepted real-photo SfM, non-mock CUDA 3DGS,
measured alignment and real Viewer/human QA for one scene identity.
