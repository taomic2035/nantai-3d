from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.viewer_session as session_module
from pipeline.viewer_session import (
    ViewerSessionError,
    ViewerSessionOptions,
    run_production_viewer_session,
)


class _FakeServer:
    def __init__(self) -> None:
        self.server_address = ("127.0.0.1", 45678)
        self.started = threading.Event()
        self.released = threading.Event()
        self.shutdown_calls = 0
        self.close_calls = 0

    def serve_forever(self) -> None:
        self.started.set()
        self.released.wait(timeout=5)

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.released.set()

    def server_close(self) -> None:
        self.close_calls += 1


def _options(tmp_path: Path) -> ViewerSessionOptions:
    project_root = tmp_path / "project"
    capture_script = project_root / "scripts/capture_viewer_acceptance.mjs"
    capture_script.parent.mkdir(parents=True)
    capture_script.write_text("// capture fixture\n", encoding="utf-8")
    import_root = tmp_path / "run/imported"
    (import_root / "web").mkdir(parents=True)
    (import_root / "web/recon_manifest.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    viewer_inputs = tmp_path / "run/viewer-inputs"
    viewer_inputs.mkdir()
    (viewer_inputs / "policy.json").write_text("{}\n", encoding="utf-8")
    (viewer_inputs / "cameras.json").write_text("{}\n", encoding="utf-8")
    evidence_root = tmp_path / "run"
    return ViewerSessionOptions(
        project_root=project_root,
        import_root=import_root,
        policy_path=viewer_inputs / "policy.json",
        camera_set_path=viewer_inputs / "cameras.json",
        output_path=evidence_root / "viewer/performance-report.v2.json",
        decision_path=evidence_root / "viewer/performance-decision.json",
        evidence_root=evidence_root,
        node_executable=tmp_path / "node.exe",
        python_executable=tmp_path / "python.exe",
        headless=True,
        measurement_timeout_ms=345_000,
    )


def test_session_starts_bound_server_runs_fixed_capture_and_always_closes(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    server = _FakeServer()
    server_calls = []
    process_calls = []

    def _make_server(root, **kwargs):
        server_calls.append((root, kwargs))
        return server

    def _run(argv, **kwargs):
        assert server.started.wait(timeout=1)
        process_calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(session_module, "make_server", _make_server)
    monkeypatch.setattr(session_module.subprocess, "run", _run)

    result = run_production_viewer_session(options)

    assert result == 7
    assert server_calls == [
        (
            options.project_root,
            {
                "host": "127.0.0.1",
                "port": 0,
                "real_scene_import_root": options.import_root,
            },
        ),
    ]
    assert process_calls == [
        (
            [
                str(options.node_executable),
                str(
                    options.project_root
                    / "scripts/capture_viewer_acceptance.mjs"
                ),
                "--policy",
                str(options.policy_path),
                "--camera-set",
                str(options.camera_set_path),
                "--studio-url",
                "http://127.0.0.1:45678/web/studio/",
                "--scene-manifest",
                str(options.import_root / "web/recon_manifest.json"),
                "--output",
                str(options.output_path),
                "--decision",
                str(options.decision_path),
                "--source-role",
                "production-acceptance",
                "--evidence-root",
                str(options.evidence_root),
                "--python",
                str(options.python_executable),
                "--measurement-timeout-ms",
                "345000",
                "--headless",
            ],
            {
                "cwd": options.project_root,
                "check": False,
            },
        ),
    ]
    assert server.shutdown_calls == 1
    assert server.close_calls == 1


def test_session_closes_server_when_capture_process_cannot_start(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    server = _FakeServer()
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: server,
    )

    def _fail(*_args, **_kwargs):
        raise OSError("process unavailable")

    monkeypatch.setattr(session_module.subprocess, "run", _fail)

    with pytest.raises(
        ViewerSessionError,
        match="capture process could not be started",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1


def test_successful_session_materializes_bound_human_review_policy(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    human_policy_output = (
        options.evidence_root / "review/human-review-policy.json"
    )
    options = replace(
        options,
        human_review_policy_output_path=human_policy_output,
    )
    server = _FakeServer()
    calls = []
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: server,
    )
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    def _materialize(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        session_module,
        "materialize_human_review_policy",
        _materialize,
    )

    result = run_production_viewer_session(options)

    assert result == 0
    assert calls == [
        {
            "evidence_root": options.evidence_root,
            "viewer_policy_path": options.policy_path,
            "viewer_report_path": options.output_path,
            "output_path": human_policy_output,
        }
    ]
    assert server.shutdown_calls == 1
    assert server.close_calls == 1


def test_session_rejects_human_review_policy_collision_before_port(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    output = options.evidence_root / "review/human-review-policy.json"
    output.parent.mkdir()
    output.write_text("existing\n", encoding="utf-8")
    options = replace(
        options,
        human_review_policy_output_path=output,
    )
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: pytest.fail(
            "server must remain unreachable"
        ),
    )

    with pytest.raises(ViewerSessionError, match="already exists"):
        run_production_viewer_session(options)


def test_session_reports_post_capture_human_policy_failure_without_rerun(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    options = replace(
        options,
        human_review_policy_output_path=(
            options.evidence_root / "review/human-review-policy.json"
        ),
    )
    server = _FakeServer()
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: server,
    )
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    def _fail(**_kwargs):
        raise session_module.HumanReviewInputError(
            "simulated report drift"
        )

    monkeypatch.setattr(
        session_module,
        "materialize_human_review_policy",
        _fail,
    )

    with pytest.raises(
        ViewerSessionError,
        match="capture completed.*could not be materialized",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1


@pytest.mark.parametrize(
    "missing",
    [
        "capture_script",
        "scene_manifest",
        "policy",
        "camera_set",
        "node",
        "python",
    ],
)
def test_session_rejects_missing_inputs_before_binding_a_port(
    tmp_path,
    monkeypatch,
    missing,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    paths = {
        "capture_script": (
            options.project_root / "scripts/capture_viewer_acceptance.mjs"
        ),
        "scene_manifest": options.import_root / "web/recon_manifest.json",
        "policy": options.policy_path,
        "camera_set": options.camera_set_path,
        "node": options.node_executable,
        "python": options.python_executable,
    }
    paths[missing].unlink()
    called = False

    def _make_server(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("server must remain unreachable")

    monkeypatch.setattr(session_module, "make_server", _make_server)

    with pytest.raises(ViewerSessionError, match="regular file"):
        run_production_viewer_session(options)

    assert called is False


def test_session_rejects_output_collision_before_binding_a_port(
    tmp_path,
    monkeypatch,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    options.output_path.parent.mkdir(parents=True)
    options.output_path.write_text("existing\n", encoding="utf-8")
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: pytest.fail(
            "server must remain unreachable"
        ),
    )

    with pytest.raises(ViewerSessionError, match="already exists"):
        run_production_viewer_session(options)


@pytest.mark.parametrize(
    "field",
    [
        "import_root",
        "policy_path",
        "camera_set_path",
        "output_path",
        "decision_path",
        "human_review_policy_output_path",
    ],
)
def test_session_rejects_evidence_paths_outside_evidence_root(
    tmp_path,
    monkeypatch,
    field,
):
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    outside = tmp_path / "outside"
    outside.mkdir()
    if field == "import_root":
        replacement = outside / "imported"
        (replacement / "web").mkdir(parents=True)
        (replacement / "web/recon_manifest.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
    elif field in {"policy_path", "camera_set_path"}:
        replacement = outside / f"{field}.json"
        replacement.write_text("{}\n", encoding="utf-8")
    else:
        replacement = outside / f"{field}.json"
    options = replace(options, **{field: replacement})
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: pytest.fail(
            "server must remain unreachable"
        ),
    )

    with pytest.raises(ViewerSessionError, match="evidence root"):
        run_production_viewer_session(options)
