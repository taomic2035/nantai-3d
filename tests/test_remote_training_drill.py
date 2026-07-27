from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.remote_training_drill import (
    DRILL_CASES,
    RemoteTrainingDrillError,
    RemoteTrainingDrillReport,
    _build_remote_training_drill_report,
    canonical_remote_training_drill_bytes,
    drill_case_set_sha256,
    load_remote_training_drill_report,
    publish_remote_training_drill_report,
    run_remote_training_drills,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_CONTAINER = "registry.example/nantai@sha256:" + ("d" * 64)
_COMMIT = "e" * 40
_ROOT = Path(__file__).resolve().parent.parent


def _context() -> dict[str, str]:
    return {
        "request_sha256": _SHA_A,
        "training_config_sha256": _SHA_B,
        "dataset_receipt_sha256": _SHA_C,
        "trainer_name": "nerfstudio-splatfacto",
        "trainer_version": "1.1.5",
        "container_identity": _CONTAINER,
    }


def _completed(argv: list[str], returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        argv,
        returncode,
        stdout=b"bounded output\n",
        stderr=b"",
    )


def test_registry_is_fixed_and_content_addressed():
    assert tuple(case.case_id for case in DRILL_CASES) == (
        "P1-3A-submit-running",
        "P1-3A-poll-timeout-unknown",
        "P1-3A-remote-failed",
        "P1-3A-fetch-interrupted",
        "P1-3B-result-content-drift",
        "P1-3B-manifest-sha-drift",
        "P1-3B-container-identity-drift",
        "P1-3C-executor-restore-no-transport",
        "P1-3C-operation-reconnect-no-resubmit",
        "P1-3C-resume-original-attempt",
        "P1-3C-retry-new-attempt",
    )
    assert len({case.case_id for case in DRILL_CASES}) == len(DRILL_CASES)
    assert drill_case_set_sha256() == hashlib.sha256(
        b"".join(case.canonical_bytes() for case in DRILL_CASES)
    ).hexdigest()


def test_submit_case_tracks_authoritative_poll_semantics():
    submit_case = next(
        case
        for case in DRILL_CASES
        if case.case_id == "P1-3A-submit-running"
    )

    assert submit_case.pytest_node_id == (
        "tests/test_remote_shell_executor.py::"
        "test_submit_keeps_receipt_not_started_until_authoritative_poll"
    )
    assert submit_case.expected_semantics == (
        "submit remains not-started until authoritative lifecycle/status poll"
    )


def test_public_runner_has_no_caller_supplied_outcome_or_cases():
    parameters = inspect.signature(run_remote_training_drills).parameters
    assert "case_results" not in parameters
    assert "outcome" not in parameters
    assert "cases" not in parameters


def test_runner_executes_fixed_cases_and_derives_accepted_report(
    tmp_path,
    monkeypatch,
):
    calls: list[tuple[str, ...]] = []

    def fake_git(root: Path) -> tuple[str, bool]:
        assert root == tmp_path
        return _COMMIT, True

    def fake_run(argv, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["shell"] is False
        calls.append(tuple(argv))
        if argv[1:4] == ["-m", "ruff", "--version"]:
            return subprocess.CompletedProcess(argv, 0, b"ruff 0.12.7\n", b"")
        return _completed(argv)

    monkeypatch.setattr(
        "pipeline.remote_training_drill._measure_git_state",
        fake_git,
    )
    monkeypatch.setattr(
        "pipeline.remote_training_drill.subprocess.run",
        fake_run,
    )

    report = run_remote_training_drills(
        tmp_path,
        generated_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        **_context(),
    )

    assert report.status == "accepted"
    assert report.evidence_scope == "transport-fixture"
    assert report.exact_commit == _COMMIT
    assert report.tree_clean is True
    assert report.case_set_sha256 == drill_case_set_sha256()
    assert tuple(item.case_id for item in report.case_results) == tuple(
        case.case_id for case in DRILL_CASES
    )
    assert all(item.status == "passed" for item in report.case_results)
    assert len(calls) == len(DRILL_CASES) + 1
    assert all("--maxfail=1" in call for call in calls[1:])


def test_runner_records_failed_case_without_caller_override(
    tmp_path,
    monkeypatch,
):
    call_index = 0

    monkeypatch.setattr(
        "pipeline.remote_training_drill._measure_git_state",
        lambda root: (_COMMIT, True),
    )

    def fake_run(argv, **kwargs):
        nonlocal call_index
        del kwargs
        if argv[1:4] == ["-m", "ruff", "--version"]:
            return subprocess.CompletedProcess(argv, 0, b"ruff 0.12.7\n", b"")
        call_index += 1
        return _completed(argv, returncode=1 if call_index == 3 else 0)

    monkeypatch.setattr(
        "pipeline.remote_training_drill.subprocess.run",
        fake_run,
    )

    report = run_remote_training_drills(
        tmp_path,
        generated_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        **_context(),
    )

    assert report.status == "failed"
    assert report.case_results[2].status == "failed"
    assert report.case_results[2].failure_code == "pytest-failed"
    assert sum(item.status == "failed" for item in report.case_results) == 1


def test_runner_rejects_dirty_or_drifting_git_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pipeline.remote_training_drill._measure_git_state",
        lambda root: (_COMMIT, False),
    )
    with pytest.raises(RemoteTrainingDrillError, match="clean"):
        run_remote_training_drills(
            tmp_path,
            generated_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
            **_context(),
        )

    states = iter(((_COMMIT, True), ("f" * 40, True)))
    monkeypatch.setattr(
        "pipeline.remote_training_drill._measure_git_state",
        lambda root: next(states),
    )
    monkeypatch.setattr(
        "pipeline.remote_training_drill.subprocess.run",
        lambda argv, **kwargs: (
            subprocess.CompletedProcess(argv, 0, b"ruff 0.12.7\n", b"")
            if argv[1:4] == ["-m", "ruff", "--version"]
            else _completed(argv)
        ),
    )
    with pytest.raises(RemoteTrainingDrillError, match="changed"):
        run_remote_training_drills(
            tmp_path,
            generated_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
            **_context(),
        )


def test_report_tamper_and_incomplete_case_set_fail_closed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "pipeline.remote_training_drill._measure_git_state",
        lambda root: (_COMMIT, True),
    )
    monkeypatch.setattr(
        "pipeline.remote_training_drill.subprocess.run",
        lambda argv, **kwargs: (
            subprocess.CompletedProcess(argv, 0, b"ruff 0.12.7\n", b"")
            if argv[1:4] == ["-m", "ruff", "--version"]
            else _completed(argv)
        ),
    )
    report = run_remote_training_drills(
        tmp_path,
        generated_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        **_context(),
    )
    payload = report.model_dump(mode="python", by_alias=True)
    payload["content_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="content_sha256"):
        RemoteTrainingDrillReport.model_validate(payload)

    fields = report.model_dump(
        mode="python",
        by_alias=False,
        exclude={
            "report_id",
            "content_sha256",
            "status",
            "case_results",
        },
    )
    fields["case_results"] = report.case_results[:-1]
    with pytest.raises(ValidationError, match="fixed registry"):
        _build_remote_training_drill_report(**fields)


def test_report_publication_is_canonical_and_no_replace(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "pipeline.remote_training_drill._measure_git_state",
        lambda root: (_COMMIT, True),
    )
    monkeypatch.setattr(
        "pipeline.remote_training_drill.subprocess.run",
        lambda argv, **kwargs: (
            subprocess.CompletedProcess(argv, 0, b"ruff 0.12.7\n", b"")
            if argv[1:4] == ["-m", "ruff", "--version"]
            else _completed(argv)
        ),
    )
    report = run_remote_training_drills(
        tmp_path,
        generated_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        **_context(),
    )
    destination = tmp_path / "drill-report.json"

    publish_remote_training_drill_report(destination, report)

    assert destination.read_bytes() == canonical_remote_training_drill_bytes(report)
    assert load_remote_training_drill_report(destination) == report
    with pytest.raises(FileExistsError):
        publish_remote_training_drill_report(destination, report)
    assert not tuple(tmp_path.glob("*.partial"))


def test_loader_rejects_duplicate_keys_and_noncanonical_bytes(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b'{"schema":"x","schema":"x"}\n')
    with pytest.raises(RemoteTrainingDrillError, match="duplicate|invalid"):
        load_remote_training_drill_report(path)

    path.write_bytes(json.dumps({"schema": "x"}, indent=2).encode("ascii"))
    with pytest.raises(RemoteTrainingDrillError, match="invalid|canonical"):
        load_remote_training_drill_report(path)


def test_direct_runner_help_works_in_isolated_python():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(_ROOT / "scripts/run_remote_training_drill.py"),
            "--help",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--request-sha256" in result.stdout
    assert "--output" in result.stdout


def test_cli_runs_fixed_registry_and_publishes(tmp_path, monkeypatch, capsys):
    script = _ROOT / "scripts/run_remote_training_drill.py"
    spec = importlib.util.spec_from_file_location(
        "run_remote_training_drill_cli",
        script,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = SimpleNamespace(status="accepted")
    calls = []
    monkeypatch.setattr(
        module,
        "run_remote_training_drills",
        lambda root, **kwargs: calls.append(("run", root, kwargs)) or report,
    )
    monkeypatch.setattr(
        module,
        "publish_remote_training_drill_report",
        lambda output, value: calls.append(("publish", output, value)),
    )
    monkeypatch.setattr(
        module,
        "canonical_remote_training_drill_bytes",
        lambda value: b'{"schema":"fixture"}\n',
    )
    output = tmp_path / "report.json"

    exit_code = module.main(
        [
            "--repo-root",
            str(tmp_path),
            "--output",
            str(output),
            "--request-sha256",
            _SHA_A,
            "--training-config-sha256",
            _SHA_B,
            "--dataset-receipt-sha256",
            _SHA_C,
            "--trainer-name",
            "nerfstudio-splatfacto",
            "--trainer-version",
            "1.1.5",
            "--container-identity",
            _CONTAINER,
        ]
    )

    assert exit_code == 0
    assert calls[0][0:2] == ("run", str(tmp_path))
    assert calls[1] == ("publish", str(output), report)
    assert capsys.readouterr().out == '{"schema":"fixture"}\n'
