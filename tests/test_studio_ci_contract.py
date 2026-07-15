"""Regression contracts for the cross-platform Studio CI matrix."""

from __future__ import annotations

import ast
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
