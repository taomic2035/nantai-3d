"""Regression contracts for the cross-platform Studio CI matrix."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WINDOWS_ONLY_TESTS = {
    "tests/test_studio_job_http.py": {
        "test_enabled_server_advertises_only_ingest_with_startup_scoped_token",
        "test_post_ingest_and_poll_ledger_backed_run_to_success",
        "test_write_request_rejects_rebinding_cross_origin_and_bad_auth",
        "test_early_rejection_does_not_wait_forever_for_a_declared_body",
        "test_oversized_rejection_does_not_drain_an_unbounded_declaration",
        "test_write_request_rejects_unknown_fields_and_oversized_body",
        "test_write_mode_project_and_runs_ignore_legacy_json_ledger",
        "test_requested_jobs_degrade_to_read_only_while_writer_is_live",
    },
    "tests/test_studio_publication.py": {
        "test_successive_commits_recover_only_the_latest_target_owner",
    },
}


def _decorator_text(node: ast.FunctionDef) -> str:
    return " ".join(ast.unparse(decorator) for decorator in node.decorator_list)


def test_windows_ci_installs_the_declared_studio_jobs_extra():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "if: runner.os == 'Windows'" in workflow
    assert 'python -m pip install -e ".[dev,windows-jobs]"' in workflow


def test_windows_ntfs_studio_tests_are_explicitly_platform_guarded():
    missing = []
    for relative_path, test_names in WINDOWS_ONLY_TESTS.items():
        tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        for test_name in sorted(test_names):
            decorators = _decorator_text(functions[test_name])
            if not all(token in decorators for token in ("skipif", "os.name", "nt")):
                missing.append(f"{relative_path}::{test_name}")

    assert missing == []


def _workflow_job(workflow: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\s*$", workflow, flags=re.MULTILINE)
    if match is None:
        raise AssertionError(f"workflow job {name!r} is missing")
    next_job = re.search(
        r"^  [A-Za-z0-9_-]+:\s*$",
        workflow[match.end() :],
        flags=re.MULTILINE,
    )
    end = len(workflow) if next_job is None else match.end() + next_job.start()
    return workflow[match.start() : end]


def test_node_and_playwright_runtime_are_exactly_locked():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    lock_root = lock["packages"][""]

    assert package["engines"]["node"] == "22.14.0"
    assert package["devDependencies"]["playwright"] == "1.62.0"
    assert package["scripts"]["install:viewer-runtime"] == (
        "playwright install chromium"
    )
    assert package["scripts"]["preflight:viewer-runtime"] == (
        "node scripts/viewer_runtime_preflight.mjs"
    )
    assert lock_root["name"] == package["name"]
    assert lock_root["version"] == package["version"]
    assert lock_root["engines"] == package["engines"]
    assert lock_root["devDependencies"] == package["devDependencies"]


def test_test_matrix_uses_exact_node_and_installs_lock_before_standard_test():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    test_job = _workflow_job(workflow, "test")

    assert 'node-version: "22.14.0"' in test_job
    assert "npm ci" in test_job
    assert "python make.py test" in test_job
    assert test_job.index("npm ci") < test_job.index("python make.py test")


def test_viewer_runtime_job_proves_both_os_and_uploads_each_machine_report():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    runtime_job = _workflow_job(workflow, "viewer-runtime")

    assert "os: [ubuntu-latest, windows-latest]" in runtime_job
    assert 'node-version: "22.14.0"' in runtime_job
    assert "npm ci" in runtime_job
    assert (
        "npx --no-install playwright install --with-deps chromium"
        in runtime_job
    )
    assert "npx --no-install playwright install chromium" in runtime_job
    assert (
        'node scripts/viewer_runtime_preflight.mjs --output '
        '"$RUNNER_TEMP/viewer-runtime-preflight.json"'
        in runtime_job
    )
    preflight_step = runtime_job.split(
        "- name: Prove Viewer runtime capabilities",
        1,
    )[1].split("- name:", 1)[0]
    upload_step = runtime_job.split(
        "- name: Upload Viewer runtime preflight report",
        1,
    )[1]
    assert "if: always()" in preflight_step
    assert runtime_job.index("Install locked Chromium runtime (Windows)") < (
        runtime_job.index("Prove Viewer runtime capabilities")
    )
    assert "if: always()" in upload_step
    assert "uses: actions/upload-artifact@v4" in runtime_job
    assert "viewer-runtime-preflight-${{ matrix.os }}" in runtime_job
    assert "${{ runner.temp }}/viewer-runtime-preflight.json" in runtime_job
    assert "capture_viewer_acceptance.mjs" not in runtime_job


def test_ci_uses_only_repository_locked_browser_installers():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    package = (ROOT / "package.json").read_text(encoding="utf-8")
    combined = workflow + package

    assert "@latest" not in combined
    assert "npm install -g" not in combined
    assert "npx playwright" not in workflow
