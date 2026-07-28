from __future__ import annotations

import importlib.util
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
