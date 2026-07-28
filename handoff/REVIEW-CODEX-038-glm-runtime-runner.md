# REVIEW-CODEX-038 — Production runtime runner review

Date: 2026-07-28
Reviewer: GLM-5.2
Reviewed commits: `59b52b4 fix: package runtime-only production runner` +
  `96d1ca8 docs: separate production runtime commands`
Design spec: `docs/superpowers/specs/2026-07-28-production-runtime-runner-design.md`
Verdict: **changes requested — one P1 missing test before runtime-runner
can close**

## What

Codex implemented the runtime-only runner template
(`release/production-runtime-runner.py`) and wired it into the builder so the
packaged `make.py` no longer leaks the 23-target repository development runner.
The release guide and runtime manual now distinguish the five repository
maintenance commands from the three packaged runtime commands. CI includes
the new tests on all three platforms.

The implementation direction matches the design spec and closes the
self-containment defect identified in FEEDBACK-HANDOFF-GLM-015 R2. The
runner contract is minimal and auditable: `help` prints ASCII usage, `verify`
delegates to the bundled offline verifier with exact arguments, `serve` binds
loopback port 8000, and private import overrides are filtered from the child
environment.

## Verification matrix (design spec §Verification)

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | builder maps runtime template to package-root `make.py` | PASS | `tests/test_production_release_builder.py::test_runtime_sources_replace_development_runner` asserts `runner.source_path == repo / "release/production-runtime-runner.py"`, `runner.role == "runtime-runner"` |
| 2 | repository `make.py` bytes and maintenance targets absent | PASS | same test asserts `not any(row.source_path == repo / "make.py" for row in payloads)`; `test_build_is_deterministic_verified_and_no_replace` asserts `b"development runner leaked" not in packaged_runner` |
| 3 | only accepted arguments are `help`, `verify`, `serve` | PASS | `tests/test_production_runtime_runner.py::test_help_is_ascii_and_lists_only_public_targets` + `test_invalid_or_combined_arguments_fail_before_subprocess` (4 parametrized invalid inputs all return exit 2) |
| 4 | `verify` invokes bundled verifier with exact arguments and propagates exit status | PASS | `test_action_dispatch_is_exact_and_propagates_status` verifies command == `[sys.executable, "scripts/verify_production_release.py", ".", "--json"]` and returncode 17 propagates |
| 5 | `serve` binds only loopback port 8000 and rejects private import overrides | PASS | same test verifies `--host 127.0.0.1 --port 8000`; `PRIVATE_OVERRIDE_NAMES` filters `REAL_SCENE_IMPORT_ROOT` from child env; test sets the env var and asserts it is absent in `observed["env"]` |
| 6 | unknown or combined target fails before any subprocess | PASS | `test_invalid_or_combined_arguments_fail_before_subprocess` monkeypatches `subprocess.run` to record calls, asserts `calls == []` |
| 7 | freshly built complete runtime can run bundled verifier from clean extracted directory | **FAIL — missing test** | no test extracts a built archive into a clean temp directory and runs `python make.py verify` end-to-end |
| 8 | tree and ZIP verifiers bind runner bytes through receipt | PASS | `test_build_is_deterministic_verified_and_no_replace` asserts `runner_artifact["role"] == "runtime-runner"` and `runner_artifact["sha256"]` matches packaged bytes |
| 9 | Preview behavior and repository `make.py` unchanged | PASS | repository `make.py::TARGETS` retains all 24 targets; `_runtime_destination` only remaps in builder context; `_ensure_release_sources_clean` tracks `release/production-runtime-runner.py` not `make.py` |
| 10 | focused Production tests pass on Ubuntu, Windows, macOS | PASS | CI `96d1ca8` `production-release-contract` green on all three platforms; `tests/test_production_runtime_runner.py` + `tests/test_production_release_builder.py` added to matrix |

Local reproduction:

```text
D:\Python313\python.exe -m pytest -q tests/test_production_runtime_runner.py tests/test_production_release_assets.py tests/test_production_release_docs.py tests/test_make_runner.py tests/test_production_release_builder.py
# 106 passed
D:\Python313\python.exe -m ruff check release/production-runtime-runner.py tests/test_production_runtime_runner.py tests/test_production_release_builder.py pipeline/production_release_builder.py
# All checks passed!
```

## [P1] Missing cold-start test (§7)

The design spec explicitly requires:

> a freshly built complete runtime can run its bundled verifier from a clean
> extracted directory without creating an unexpected-file failure

No test currently proves this. The builder tests verify that the archive
contains the correct `make.py` bytes and receipt binding, and the runner
unit tests verify dispatch logic with monkeypatched subprocess. But there is
no end-to-end test that:

1. builds a modeled archive via `build_production_release_archive`;
2. extracts it into a clean temp directory;
3. runs `python make.py verify` against the extracted package root;
4. asserts the verifier succeeds (exit 0) and does not create unexpected
   files that would fail a subsequent verification.

Without this test, a regression in archive layout, `make.py` path resolution,
`scripts/verify_production_release.py` import path, or receipt/checksums
binding could ship green on unit tests but fail in an operator's hands. The
cold-start test is the only contract evidence that the packaged runtime is
self-consistent end-to-end.

The test must use modeled data and must not be described as a real Production
scene, per the design spec completion boundary.

### Suggested test shape

```python
def test_built_runtime_runs_bundled_verifier_from_clean_extract(
    tmp_path: Path, monkeypatch
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)
    monkeypatch.setattr(builder_module, "load_latest_real_scene_acceptance",
        lambda _root: fixture["report_path"])
    monkeypatch.setattr(builder_module, "derive_production_release_context",
        lambda _path: context)
    monkeypatch.setattr(builder_module, "_ensure_release_sources_clean",
        lambda *_args: None)

    archive_path = tmp_path / "runtime.zip"
    build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=archive_path,
        version="v1.0.0",
        source_commit="a" * 40,
        tracked_files=tracked,
    )

    extract_root = tmp_path / "extracted"
    extract_root.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extract_root)
    package_root = extract_root / "nantai-3d-v1.0.0"

    completed = subprocess.run(
        [sys.executable, str(package_root / "make.py"), "verify"],
        cwd=str(package_root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    # optional: assert no unexpected files created
```

This is a suggestion only; Codex may implement differently as long as the
contract is proven with modeled data.

## Security and trust boundary

The runner correctly:

- does not import application modules in `help`;
- filters `ACCEPTANCE_ROOT`, `ARCHIVE`, `PRIVACY_POLICY`, `PRIVACY_REPORT`,
  `REAL_SCENE_IMPORT_ROOT`, `RELEASE_DIR`, `VERSION` from child env;
- sets `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`;
- rejects unknown/combined targets with exit 2 before any subprocess;
- does not expose build/audit/stage targets.

`scene_trust_effect=none` is preserved: the runner only starts the bundled
server and verifier, it does not promote scene trust.

## Verdict

The runtime-runner implementation is correct in direction and 9/10
verification requirements pass. The single P1 is missing test coverage for
the cold-start end-to-end contract (§7), not a code defect.

**Changes requested**: add the cold-start test proving a freshly built
modeled archive runs `python make.py verify` from a clean extracted
directory. Once §7 is green, the runtime-runner can close and the
self-containment defect from FEEDBACK-HANDOFF-GLM-015 R2 is fully resolved.

This review does not authorize creating a final tag or Release. The
completion boundary in the design spec remains: runtime command
self-containment only, not real-scene gates.
