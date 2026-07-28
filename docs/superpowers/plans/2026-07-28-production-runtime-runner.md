# Production Runtime Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the repository development runner inside a Production runtime
with a three-command, package-only runner that verifies or serves only the
content-addressed extracted package.

**Architecture:** Keep repository maintenance commands in root `make.py`, add a
tracked runtime-only template at `release/production-runtime-runner.py`, and
map that template to package-root `make.py` during deterministic archive
construction. The runner accepts exactly one of `help`, `verify`, or `serve`,
uses the current interpreter and package root, and cannot accept private scene
or repository build inputs.

**Tech Stack:** Python 3.11+ standard library, existing deterministic Production
release builder/verifier, pytest, Ruff, and the existing three-OS GitHub Actions
Production contract matrix.

**Design:** `docs/superpowers/specs/2026-07-28-production-runtime-runner-design.md`

---

## File structure

- Create `release/production-runtime-runner.py`: the only `make.py` bytes
  shipped at package root; owns help, exact verifier dispatch, exact loopback
  server dispatch, UTF-8 child environment and single-target validation.
- Create `tests/test_production_runtime_runner.py`: isolated runner contract
  tests with subprocess calls replaced by an observer.
- Modify `pipeline/production_release_builder.py`: map the runtime template to
  destination `make.py`, exclude repository `make.py`, and include the template
  in the clean-source gate.
- Modify `tests/test_production_release_builder.py`: distinguish development
  and runtime runner bytes, verify the artifact role/source mapping, and execute
  the bundled verifier twice from a freshly extracted modeled archive.
- Modify `tests/test_production_release_assets.py`: validate packaged guide
  targets against the runtime template rather than repository `make.py`.
- Modify `release/production-verify-and-run.md`: make `python make.py verify`
  the documented first command while naming the delegated offline verifier.
- Modify `docs/manual/production-runtime-release.md`: separate repository
  maintainer commands from the three extracted-runtime commands.
- Modify `tests/test_production_release_docs.py`: lock the command boundary and
  require the new runner test in cross-platform CI.
- Modify `.github/workflows/ci.yml`: run the runtime runner and builder tests in
  the existing Ubuntu/Windows/macOS Production matrix.
- Do not modify repository `make.py`, receipt schemas, Production entrypoints,
  Preview behavior, versions, tags or Release assets.

## Global execution rules

- Work on the shared `main` tree and inspect `git status --short --branch`
  before every task.
- Use TDD: add the focused assertion, observe the expected failure, implement
  only enough to pass it, then rerun the focused suite.
- Stage only the paths named by the current task. Never use `git add -A`,
  `git commit -a`, reset, checkout, stash or rebase.
- Every Codex commit ends with:

```text
Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>
```

- Push each green checkpoint with:

```powershell
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

### Task 1: Runtime-only command contract

**Files:**

- Create: `tests/test_production_runtime_runner.py`
- Create: `release/production-runtime-runner.py`

- [ ] **Step 1: Write RED tests for the exact public command surface**

Create `tests/test_production_runtime_runner.py` with a fresh module load for
every test and an observer that replaces `subprocess.run`:

```python
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "release" / "production-runtime-runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "production_runtime_runner",
        RUNNER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner():
    return _load_runner()


def test_help_is_ascii_and_lists_only_public_targets(runner, capsys) -> None:
    assert runner.main(["make.py", "help"]) == 0
    output = capsys.readouterr().out
    output.encode("ascii")
    assert "python make.py verify" in output
    assert "python make.py serve" in output
    for forbidden in (
        "build-production",
        "verify-production",
        "audit-production-privacy",
        "stage-production-assets",
        "REAL_SCENE_IMPORT_ROOT",
    ):
        assert forbidden not in output


@pytest.mark.parametrize(
    "arguments",
    (
        ["make.py"],
        ["make.py", "bogus"],
        ["make.py", "verify", "serve"],
        ["make.py", "serve", "REAL_SCENE_IMPORT_ROOT=C:/private"],
    ),
)
def test_invalid_or_combined_arguments_fail_before_subprocess(
    runner,
    arguments,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert runner.main(arguments) == 2
    assert calls == []


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        (
            "verify",
            [
                sys.executable,
                "scripts/verify_production_release.py",
                ".",
                "--json",
            ],
        ),
        (
            "serve",
            [
                sys.executable,
                "-m",
                "pipeline.studio_server",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
        ),
    ),
)
def test_action_dispatch_is_exact_and_propagates_status(
    target,
    expected,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REAL_SCENE_IMPORT_ROOT", "C:/private")
    runner = _load_runner()
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.main(["make.py", target]) == 17
    assert observed["command"] == expected
    assert observed["cwd"] == str(runner.ROOT)
    assert observed["check"] is False
    assert observed["env"]["PYTHONUTF8"] == "1"
    assert observed["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "REAL_SCENE_IMPORT_ROOT" not in observed["env"]
```

- [ ] **Step 2: Run the runner tests and observe RED**

```powershell
python -m pytest -q tests/test_production_runtime_runner.py
```

Expected: collection or module loading fails because
`release/production-runtime-runner.py` does not exist.

- [ ] **Step 3: Implement the minimal standalone runner**

Create `release/production-runtime-runner.py`:

```python
#!/usr/bin/env python3
"""Run the two safe actions exposed by an extracted Production runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
PRIVATE_OVERRIDE_NAMES = frozenset(
    {
        "ACCEPTANCE_ROOT",
        "ARCHIVE",
        "PRIVACY_POLICY",
        "PRIVACY_REPORT",
        "REAL_SCENE_IMPORT_ROOT",
        "RELEASE_DIR",
        "VERSION",
    }
)
ENV = {
    key: value
    for key, value in os.environ.items()
    if key not in PRIVATE_OVERRIDE_NAMES
}
ENV.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})


def _run(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=ENV,
        check=False,
    )
    return completed.returncode


def verify() -> int:
    return _run(
        [
            PYTHON,
            "scripts/verify_production_release.py",
            ".",
            "--json",
        ]
    )


def serve() -> int:
    return _run(
        [
            PYTHON,
            "-m",
            "pipeline.studio_server",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
    )


TARGETS: dict[str, Callable[[], int]] = {
    "verify": verify,
    "serve": serve,
}


def _print_help() -> None:
    print("Nantai 3D Production runtime")
    print("  python make.py help")
    print("  python make.py verify")
    print("  python make.py serve")


def main(argv: list[str]) -> int:
    arguments = argv[1:]
    if arguments == ["help"]:
        _print_help()
        return 0
    if len(arguments) != 1 or arguments[0] not in TARGETS:
        print(
            "expected exactly one target: help, verify, or serve",
            file=sys.stderr,
        )
        return 2
    return TARGETS[arguments[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run focused tests and lint**

```powershell
python -m pytest -q tests/test_production_runtime_runner.py
python -m ruff check release/production-runtime-runner.py tests/test_production_runtime_runner.py
```

Expected: all runner tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit and push the standalone contract**

```powershell
git add -- release/production-runtime-runner.py tests/test_production_runtime_runner.py
git commit -m "feat: add production runtime runner" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- release/production-runtime-runner.py tests/test_production_runtime_runner.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

Expected: `main` advances by one green, pushed commit.

### Task 2: Builder source mapping and clean extracted verification

**Files:**

- Modify: `tests/test_production_release_builder.py`
- Modify: `pipeline/production_release_builder.py`

- [ ] **Step 1: Write RED builder mapping assertions**

In `_runtime_repo`, give the two runner sources unmistakably different bytes:

```python
"make.py": b"raise SystemExit('development runner leaked')\n",
"release/production-runtime-runner.py": (
    Path(__file__).parents[1]
    .joinpath("release/production-runtime-runner.py")
    .read_bytes()
),
```

Add a direct source-mapping test:

```python
def test_runtime_sources_replace_development_runner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)

    payloads = builder_module._runtime_source_payloads(repo, tracked)
    runner = next(row for row in payloads if row.destination_path == "make.py")

    assert runner.role == "runtime-runner"
    assert runner.source_path == (
        repo / "release/production-runtime-runner.py"
    )
    assert not any(row.source_path == repo / "make.py" for row in payloads)
```

Extend the deterministic archive test after opening its ZIP:

```python
with zipfile.ZipFile(first_path) as archive:
    packaged_runner = archive.read("make.py")
    receipt = json.loads(archive.read("PRODUCTION-RELEASE.json"))
assert packaged_runner == (
    repo / "release/production-runtime-runner.py"
).read_bytes()
assert b"development runner leaked" not in packaged_runner
runner_artifact = next(
    row for row in receipt["artifacts"] if row["path"] == "make.py"
)
assert runner_artifact["role"] == "runtime-runner"
assert runner_artifact["sha256"] == hashlib.sha256(
    packaged_runner
).hexdigest()
```

- [ ] **Step 2: Run the mapping tests and observe RED**

```powershell
python -m pytest -q \
  tests/test_production_release_builder.py::test_runtime_sources_replace_development_runner \
  tests/test_production_release_builder.py::test_build_is_deterministic_verified_and_no_replace
```

Expected: the runtime source resolver still maps repository `make.py` with role
`runtime-root`, so the new assertions fail.

- [ ] **Step 3: Replace the builder mapping**

Change `_runtime_destination` so the template owns package-root `make.py`:

```python
def _runtime_destination(relative: str) -> tuple[str, str] | None:
    if relative == "release/production-verify-and-run.md":
        return "VERIFY-AND-RUN.md", "release-guide"
    if relative == "release/production-runtime-runner.py":
        return "make.py", "runtime-runner"
    if relative in {"LICENSE", "pyproject.toml"}:
        return relative, "runtime-root"
    if relative == "scripts/verify_production_release.py":
        return relative, "offline-verifier"
    if relative.startswith("pipeline/") and relative.endswith(".py"):
        return relative, "runtime-code"
    if relative.startswith(("web/studio/", "web/viewer/")):
        return relative, "web-runtime"
    return None
```

In `_ensure_release_sources_clean`, replace the root `make.py` entry with:

```python
"release/production-runtime-runner.py",
```

Keep destination `make.py` in the required runtime set.

- [ ] **Step 4: Lock the clean-source boundary**

Add a subprocess observer test:

```python
def test_clean_source_gate_tracks_template_not_development_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(builder_module.subprocess, "run", fake_run)
    builder_module._ensure_release_sources_clean(tmp_path, ())

    command = observed["command"]
    assert "release/production-runtime-runner.py" in command
    assert "make.py" not in command
    assert observed["cwd"] == tmp_path
```

- [ ] **Step 5: Add a modeled cold-start verifier regression**

Append a test that builds, extracts and verifies the same package twice:

```python
def test_fresh_runtime_verifier_is_repeatable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)
    monkeypatch.setattr(
        builder_module,
        "load_latest_real_scene_acceptance",
        lambda _root: fixture["report_path"],
    )
    monkeypatch.setattr(
        builder_module,
        "derive_production_release_context",
        lambda _path: context,
    )
    monkeypatch.setattr(
        builder_module,
        "_ensure_release_sources_clean",
        lambda *_args: None,
    )
    archive_path = tmp_path / "runtime.zip"
    build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=archive_path,
        version="v1.0.0",
        source_commit="a" * 40,
        tracked_files=tracked,
    )
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)

    for _attempt in range(2):
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/verify_production_release.py",
                ".",
                "--json",
            ],
            cwd=extracted,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["valid"] is True
    assert not tuple(extracted.rglob("__pycache__"))
```

Add `subprocess` and `sys` imports to the builder test module.

- [ ] **Step 6: Run focused and complete builder suites**

```powershell
python -m pytest -q tests/test_production_release_builder.py
python -m ruff check pipeline/production_release_builder.py tests/test_production_release_builder.py
```

Expected: builder tests pass, the extracted verifier returns zero twice, and
Ruff reports `All checks passed!`.

- [ ] **Step 7: Commit and push the builder integration**

```powershell
git add -- pipeline/production_release_builder.py tests/test_production_release_builder.py
git commit -m "fix: package runtime-only production runner" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/production_release_builder.py tests/test_production_release_builder.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

Expected: `main` advances with the content-addressed template mapping.

### Task 3: Operator documentation and cross-platform contract

**Files:**

- Modify: `tests/test_production_release_assets.py`
- Modify: `tests/test_production_release_docs.py`
- Modify: `release/production-verify-and-run.md`
- Modify: `docs/manual/production-runtime-release.md`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write RED documentation-boundary assertions**

Change `_load_make_targets` in `tests/test_production_release_assets.py` to
load `release/production-runtime-runner.py` and include `help` explicitly:

```python
def _load_runtime_targets() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "production_runtime_runner",
        _REPO_ROOT / "release" / "production-runtime-runner.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {"help", *module.TARGETS}
```

Use that helper in `test_release_guide_make_py_targets_exist`, and replace the
offline-verifier assertion with:

```python
assert "python make.py verify" in guide
assert "scripts/verify_production_release.py" in guide
```

Extend `tests/test_production_release_docs.py`:

```python
def test_manual_separates_repository_and_extracted_runtime_commands() -> None:
    manual = _read(MANUAL)
    assert "仓库维护命令" in manual
    assert "解压包命令" in manual
    for target in ("help", "verify", "serve"):
        assert f"python make.py {target}" in manual
    assert re.search(
        r"解压包命令.*build-production",
        manual,
        re.DOTALL,
    ) is None
```

Add `tests/test_production_runtime_runner.py` and
`tests/test_production_release_builder.py` to the list of paths required in
the three-OS Production job.

- [ ] **Step 2: Run documentation tests and observe RED**

```powershell
python -m pytest -q \
  tests/test_production_release_assets.py::test_release_guide_make_py_targets_exist \
  tests/test_production_release_assets.py::test_release_guide_references_bundled_offline_verifier \
  tests/test_production_release_docs.py
```

Expected: guide/manual/CI assertions fail until all operator surfaces are
updated together.

- [ ] **Step 3: Update the packaged quick guide**

Use this command block in `release/production-verify-and-run.md`:

```powershell
python make.py verify
python make.py serve
```

State immediately below it that `verify` delegates to the bundled
`scripts/verify_production_release.py . --json`, that verification precedes
dependency installation or serving, and that no private scene override is
accepted.

- [ ] **Step 4: Separate repository maintenance from runtime use in the manual**

Add a `仓库维护命令` paragraph before build instructions that lists exactly:

```text
build-production
verify-production
audit-production-privacy
stage-production-assets
verify-production-assets
```

Add a `解压包命令` paragraph before runtime setup that lists exactly:

```text
python make.py help
python make.py verify
python make.py serve
```

Change the extracted-runtime sequence to run `python make.py verify` before
creating the environment or serving. State that package-root `make.py` is not
the repository runner and rejects build, privacy, staging, combined-target and
private-import inputs.

- [ ] **Step 5: Extend the existing three-OS CI test command**

In `.github/workflows/ci.yml`, add:

```yaml
            tests/test_production_runtime_runner.py \
            tests/test_production_release_builder.py \
```

to `production-release-contract` without creating a second matrix or changing
the pinned dependency set.

- [ ] **Step 6: Run focused documentation and Production suites**

```powershell
python -m pytest -q --noconftest \
  tests/test_release_archive.py \
  tests/test_production_release_contract.py \
  tests/test_production_release_verifier.py \
  tests/test_production_release_cli.py \
  tests/test_production_release_privacy.py \
  tests/test_production_release_assets.py \
  tests/test_production_release_docs.py \
  tests/test_production_runtime_runner.py \
  tests/test_production_release_builder.py
python -m ruff check \
  release/production-runtime-runner.py \
  pipeline/production_release_builder.py \
  tests/test_production_runtime_runner.py \
  tests/test_production_release_builder.py \
  tests/test_production_release_assets.py \
  tests/test_production_release_docs.py
```

Expected: all focused Production tests pass and Ruff reports
`All checks passed!`.

- [ ] **Step 7: Commit and push docs plus CI**

```powershell
git add -- .github/workflows/ci.yml docs/manual/production-runtime-release.md release/production-verify-and-run.md tests/test_production_release_assets.py tests/test_production_release_docs.py
git commit -m "docs: separate production runtime commands" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- .github/workflows/ci.yml docs/manual/production-runtime-release.md release/production-verify-and-run.md tests/test_production_release_assets.py tests/test_production_release_docs.py
git -c http.proxy=http://127.0.0.1:7890 push origin main
```

Expected: docs and three-OS checks encode the same three-command boundary.

### Task 4: Regression proof and exact-HEAD CI

**Files:**

- Verify only: repository and generated test temporaries

- [ ] **Step 1: Prove repository and Preview runners did not change**

```powershell
git diff c887546 -- make.py pipeline/preview_release.py scripts/build_preview_release.py scripts/verify_preview_release.py
python -m pytest -q tests/test_make_runner.py tests/test_preview_release.py tests/test_preview_release_cli.py
```

Expected: the diff is empty and all repository/Preview tests pass.

- [ ] **Step 2: Run the complete Python and browser regression suites**

```powershell
python -m pytest -q tests
python -m ruff check pipeline tests cloud scripts make.py release/production-runtime-runner.py
node --test web/viewer/*.test.mjs
node --test web/studio/*.test.mjs
```

Expected: every command exits zero.

- [ ] **Step 3: Inspect exact-HEAD CI**

```powershell
git rev-parse HEAD
git rev-parse origin/main
gh run list --workflow ci.yml --branch main --limit 5
$head = git rev-parse HEAD
$runs = gh run list --workflow ci.yml --branch main --limit 10 --json databaseId,headSha,status,conclusion,url | ConvertFrom-Json
$run = $runs | Where-Object { $_.headSha -eq $head } | Select-Object -First 1
if ($null -eq $run) { throw "no CI run found for exact HEAD $head" }
gh run view $run.databaseId --json headSha,status,conclusion,jobs,url
```

Expected: local `HEAD`, `origin/main` and the inspected run `headSha` are
identical; all jobs conclude `success`.

- [ ] **Step 4: Record the trust boundary**

Update the working plan status only after local and exact-HEAD CI evidence is
green. Report this feature as closing Production runtime command
self-containment. Keep final `v1.0.0` blocked until the rights-cleared capture,
accepted real-photo SfM, non-mock CUDA 3DGS, measured alignment and real
Viewer/human acceptance gates all exist for one scene identity.
