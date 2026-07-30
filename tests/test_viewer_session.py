from __future__ import annotations

import os
import subprocess
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


def _make_directory_redirect(alias: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip(f"junction creation unavailable: {created.stderr}")
        return
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is not permitted")


def _remove_directory_redirect(alias: Path) -> None:
    if os.name == "nt":
        os.rmdir(alias)
    else:
        alias.unlink()


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


# ============================================================
# RED → GREEN: launch preflight link/reparse and error privacy
# ============================================================


def test_require_regular_file_rejects_symlink(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    target = tmp_path / "real.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(ViewerSessionError, match="regular file"):
        session_module._require_regular_file(link, label="test")


def test_require_absent_rejects_symlink(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    target = tmp_path / "real.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(ViewerSessionError, match="already exists"):
        session_module._require_absent(link, label="test")


def test_require_regular_file_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    (real_parent / "policy.json").write_text("{}\n", encoding="utf-8")
    link_parent = tmp_path / "link-parent"
    _make_directory_redirect(link_parent, real_parent)
    try:
        with pytest.raises(ViewerSessionError, match="regular file"):
            session_module._require_regular_file(
                link_parent / "policy.json",
                label="test",
            )
    finally:
        _remove_directory_redirect(link_parent)


def test_require_absent_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    link_parent = tmp_path / "link-parent"
    _make_directory_redirect(link_parent, real_parent)
    try:
        with pytest.raises(ViewerSessionError, match="redirected"):
            session_module._require_absent(
                link_parent / "report.json",
                label="test",
            )
    finally:
        _remove_directory_redirect(link_parent)


def test_validated_options_rejects_symlinked_project_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    real_project = tmp_path / "real-project"
    real_project.mkdir()
    (real_project / "scripts").mkdir(parents=True)
    (real_project / "scripts/capture_viewer_acceptance.mjs").write_text(
        "// capture\n", encoding="utf-8"
    )
    link_project = tmp_path / "link-project"
    try:
        link_project.symlink_to(real_project, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    options = _options(tmp_path)
    options = replace(options, project_root=link_project)
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: pytest.fail(
            "server must remain unreachable"
        ),
    )

    with pytest.raises(ViewerSessionError, match="regular director"):
        run_production_viewer_session(options)


def test_validated_options_rejects_symlinked_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    real_policy = options.policy_path.parent / "real-policy.json"
    real_policy.write_text("{}\n", encoding="utf-8")
    link_policy = options.policy_path.parent / "link-policy.json"
    try:
        link_policy.symlink_to(real_policy)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    options = replace(options, policy_path=link_policy)
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: pytest.fail(
            "server must remain unreachable"
        ),
    )

    with pytest.raises(ViewerSessionError, match="regular file"):
        run_production_viewer_session(options)


def test_validated_options_rejects_symlinked_node_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    options = _options(tmp_path)
    real_node = tmp_path / "real-node.exe"
    real_node.write_bytes(b"node")
    link_node = tmp_path / "link-node.exe"
    try:
        link_node.symlink_to(real_node)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    options = replace(options, node_executable=link_node)
    monkeypatch.setattr(
        session_module,
        "make_server",
        lambda *_args, **_kwargs: pytest.fail(
            "server must remain unreachable"
        ),
    )

    with pytest.raises(ViewerSessionError, match="regular file"):
        run_production_viewer_session(options)


def test_studio_server_error_does_not_leak_exception_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")

    secret_message = "SECRET_PATH_C:\\users\\admin\\private"

    def _fail(*_args, **_kwargs):
        raise OSError(secret_message)

    monkeypatch.setattr(session_module, "make_server", _fail)

    with pytest.raises(ViewerSessionError, match="could not start") as exc_info:
        run_production_viewer_session(options)

    assert secret_message not in str(exc_info.value)


def test_human_review_error_does_not_leak_exception_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    secret_message = "SECRET_PATH_C:\\users\\admin\\private"

    def _fail(**_kwargs):
        raise session_module.HumanReviewInputError(secret_message)

    monkeypatch.setattr(
        session_module,
        "materialize_human_review_policy",
        _fail,
    )

    with pytest.raises(
        ViewerSessionError, match="could not be materialized"
    ) as exc_info:
        run_production_viewer_session(options)

    assert secret_message not in str(exc_info.value)


# ============================================================
# RED -> GREEN: launch preflight identity re-verification
# ============================================================


def _make_swapping_make_server(
    target_path: Path,
    new_payload: bytes,
    server: _FakeServer,
):
    """Return a make_server replacement that swaps target_path's content
    before returning the server, simulating a TOCTOU swap between
    _validated_options and subprocess.run."""

    def _swap(*_args, **_kwargs):
        target_path.write_bytes(new_payload)
        return server

    return _swap


def test_launch_rejects_policy_swap_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: A Viewer policy swapped between validation and launch must
    fail closed, not be silently re-bound by the downstream mjs probe."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    original_bytes = options.policy_path.read_bytes()
    server = _FakeServer()

    monkeypatch.setattr(
        session_module,
        "make_server",
        _make_swapping_make_server(
            options.policy_path,
            b"swapped\n",
            server,
        ),
    )
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="changed before launch|no longer a regular file",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    options.policy_path.write_bytes(original_bytes)


def test_launch_rejects_camera_set_swap_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: A Viewer camera set swapped between validation and launch
    must fail closed."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    original_bytes = options.camera_set_path.read_bytes()
    server = _FakeServer()

    monkeypatch.setattr(
        session_module,
        "make_server",
        _make_swapping_make_server(
            options.camera_set_path,
            b"swapped\n",
            server,
        ),
    )
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="changed before launch|no longer a regular file",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    options.camera_set_path.write_bytes(original_bytes)


def test_launch_rejects_capture_script_swap_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: The capture script swapped between validation and launch
    must fail closed."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    capture_script = (
        options.project_root / "scripts/capture_viewer_acceptance.mjs"
    )
    original_bytes = capture_script.read_bytes()
    server = _FakeServer()

    monkeypatch.setattr(
        session_module,
        "make_server",
        _make_swapping_make_server(
            capture_script,
            b"// replaced\n",
            server,
        ),
    )
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="changed before launch|no longer a regular file",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    capture_script.write_bytes(original_bytes)


def test_launch_rejects_node_executable_swap_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: The Node executable swapped between validation and launch
    must fail closed."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    original_bytes = options.node_executable.read_bytes()
    server = _FakeServer()

    monkeypatch.setattr(
        session_module,
        "make_server",
        _make_swapping_make_server(
            options.node_executable,
            b"replaced\n",
            server,
        ),
    )
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="changed before launch|no longer a regular file",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    options.node_executable.write_bytes(original_bytes)


def test_launch_rejects_output_pre_occupied_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: A Viewer report path pre-occupied between validation and
    launch must fail closed, not be silently overwritten."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    server = _FakeServer()

    def _occupy(*_args, **_kwargs):
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        options.output_path.write_text("occupied\n", encoding="utf-8")
        return server

    monkeypatch.setattr(session_module, "make_server", _occupy)
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="pre-occupied|already exists",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1


def test_launch_rejects_decision_pre_occupied_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: A Viewer decision path pre-occupied between validation and
    launch must fail closed."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    server = _FakeServer()

    def _occupy(*_args, **_kwargs):
        options.decision_path.parent.mkdir(parents=True, exist_ok=True)
        options.decision_path.write_text("occupied\n", encoding="utf-8")
        return server

    monkeypatch.setattr(session_module, "make_server", _occupy)
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="pre-occupied|already exists",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1


def test_launch_rejects_policy_replaced_with_symlink_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: A Viewer policy replaced with a symlink between validation
    and launch must fail closed."""
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    original_bytes = options.policy_path.read_bytes()
    server = _FakeServer()
    replacement = options.policy_path.parent / "replacement.json"
    replacement.write_text("{}\n", encoding="utf-8")

    def _symlink_swap(*_args, **_kwargs):
        options.policy_path.unlink()
        try:
            options.policy_path.symlink_to(replacement)
        except OSError:
            pytest.skip("symlink creation is not permitted")
        return server

    monkeypatch.setattr(session_module, "make_server", _symlink_swap)
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(
        ViewerSessionError,
        match="no longer a regular file|changed before launch",
    ):
        run_production_viewer_session(options)

    assert server.shutdown_calls == 1
    assert server.close_calls == 1
    try:
        options.policy_path.unlink()
    except OSError:
        pass
    options.policy_path.write_bytes(original_bytes)


def test_launch_error_does_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED: Launch identity re-verification errors must not contain absolute
    paths from OSError text."""
    options = _options(tmp_path)
    options.node_executable.write_bytes(b"node")
    options.python_executable.write_bytes(b"python")
    server = _FakeServer()
    private_path = str(options.policy_path.resolve())

    def _deleting_make_server(*_args, **_kwargs):
        options.policy_path.unlink()
        return server

    monkeypatch.setattr(session_module, "make_server", _deleting_make_server)
    monkeypatch.setattr(
        session_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(ViewerSessionError) as exc_info:
        run_production_viewer_session(options)

    assert private_path not in str(exc_info.value)
    assert server.shutdown_calls == 1
    assert server.close_calls == 1
