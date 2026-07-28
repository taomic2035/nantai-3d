from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.real_dataset import (
    CaptureRightsReceipt,
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
)

ROOT = Path(__file__).resolve().parent.parent


def _load_cli():
    path = ROOT / "scripts/real_scene.py"
    spec = importlib.util.spec_from_file_location("real_scene_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_cli_help_works_in_isolated_python():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts/real_scene.py"),
            "--help",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "train-production" in result.stdout
    assert "preflight-remote" in result.stdout


@pytest.mark.parametrize(
    ("status", "expected_code"),
    (("ready", 0), ("failed", 2)),
)
def test_remote_preflight_cli_bypasses_dataset_and_publishes_report(
    tmp_path,
    monkeypatch,
    capsys,
    status,
    expected_code,
):
    cli = _load_cli()
    config = tmp_path / "remote-config.json"
    output = tmp_path / "preflight.json"
    report = SimpleNamespace(status=status)
    calls = []
    monkeypatch.setattr(
        cli,
        "load_real_dataset_source",
        lambda _path: pytest.fail("preflight must not load a dataset"),
    )
    monkeypatch.setattr(
        cli,
        "run_remote_shell_preflight_from_path",
        lambda path: calls.append(("run", path)) or report,
    )
    monkeypatch.setattr(
        cli,
        "publish_remote_shell_preflight",
        lambda value, path: calls.append(("publish", value, path)),
    )
    monkeypatch.setattr(
        cli,
        "canonical_remote_shell_preflight_bytes",
        lambda value: b'{"schema":"test"}\n',
    )

    result = cli.main(
        [
            "preflight-remote",
            "--remote-config",
            str(config),
            "--preflight-report",
            str(output),
        ]
    )

    assert result == expected_code
    assert calls == [
        ("run", config),
        ("publish", report, output),
    ]
    assert capsys.readouterr().out == '{"schema":"test"}\n'


def test_remote_preflight_cli_rejects_dataset_arguments(
    tmp_path,
    monkeypatch,
    capsys,
):
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "run_remote_shell_preflight_from_path",
        lambda _path: pytest.fail("invalid invocation must not probe"),
    )

    result = cli.main(
        [
            "preflight-remote",
            "--remote-config",
            str(tmp_path / "remote.json"),
            "--preflight-report",
            str(tmp_path / "preflight.json"),
            "--source",
            str(tmp_path / "source.json"),
        ]
    )

    assert result == 2
    assert "accepts only" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("script_name", "expected_help"),
    (
        ("fetch_real_dataset.py", "verify-only"),
        ("validate_render_evaluation.py", "--root"),
        ("record_real_scene_review.py", "reviewer"),
    ),
)
def test_direct_golden_path_cli_help_works_in_isolated_python(
    script_name,
    expected_help,
):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "scripts" / script_name),
            "--help",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert expected_help in result.stdout


def _hf_source(path: Path) -> None:
    source = HfDatasetSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="poster",
        role="internal-canary",
        source_kind="hf-dataset",
        repository="owner/repo",
        repository_revision="4" * 40,
        subtree="poster",
        capture_subtree="poster/images",
        declared_file_count=1,
        declared_total_bytes=5,
        license_status="not-declared",
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    path.write_bytes(canonical_model_bytes(source))


def _local_source(path: Path, rights_path: Path) -> None:
    rights = CaptureRightsReceipt(
        schema="nantai.capture-rights-receipt.v1",
        dataset_id="courtyard",
        operator="Nantai operator",
        capture_scope="acceptance capture",
        effective_date=date(2026, 7, 26),
        processing_purposes=("3d-reconstruction",),
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    rights_path.write_bytes(canonical_model_bytes(rights))
    source = LocalCaptureSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="courtyard",
        role="production-acceptance",
        source_kind="local-capture",
        rights_receipt_sha256=__import__("hashlib")
        .sha256(canonical_model_bytes(rights))
        .hexdigest(),
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    path.write_bytes(canonical_model_bytes(source))


def test_cli_builds_source_bound_options(tmp_path, monkeypatch, capsys):
    cli = _load_cli()
    source = tmp_path / "source.json"
    _hf_source(source)
    calls = []
    monkeypatch.setattr(
        cli,
        "run_real_scene",
        lambda source_path, target, options, **kwargs: (
            calls.append((source_path, target, options, kwargs))
            or SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "stage": "fetch",
                    "status": "completed",
                }
            )
        ),
    )

    assert (
        cli.main(
            [
                "fetch",
                "--source",
                str(source),
                "--run-id",
                "canary-001",
                "--chunk-size",
                "37.5",
                "--viewer-policy",
                str(tmp_path / "viewer-policy.json"),
                "--viewer-report",
                str(tmp_path / "viewer-report.json"),
                "--human-review-policy",
                str(tmp_path / "human-policy.json"),
                "--human-visual-review",
                str(tmp_path / "human-review.json"),
            ]
        )
        == 0
    )

    assert calls[0][1] == "fetch"
    assert calls[0][2].run_id == "canary-001"
    assert calls[0][2].chunk_size == 37.5
    assert calls[0][2].viewer_policy_path == (tmp_path / "viewer-policy.json")
    assert calls[0][2].viewer_report_path == (tmp_path / "viewer-report.json")
    assert calls[0][2].human_review_policy_path == (tmp_path / "human-policy.json")
    assert calls[0][2].human_visual_review_path == (tmp_path / "human-review.json")
    assert '"status":"completed"' in capsys.readouterr().out


def test_cli_requires_private_runtime_inputs_for_local_capture(
    tmp_path,
    capsys,
):
    cli = _load_cli()
    source = tmp_path / "source.json"
    rights = tmp_path / "rights.json"
    _local_source(source, rights)

    assert cli.main(["fetch", "--source", str(source)]) == 2
    assert "media-root" in capsys.readouterr().err


def test_cli_serve_revalidates_acceptance_and_import_before_loopback_studio(
    tmp_path,
    monkeypatch,
):
    cli = _load_cli()
    source = tmp_path / "source.json"
    workspace = tmp_path / "workspace"
    import_root = tmp_path / "accepted-import"
    source.write_text("{}\n", encoding="ascii")
    calls = []
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda source_path, *, workspace_base, run_id: (
            calls.append(
                ("snapshot", source_path, workspace_base, run_id)
            )
            or SimpleNamespace(
                state="accepted-from-authoritative-decision",
                source=SimpleNamespace(
                    role="production-acceptance",
                    source_sha256="a" * 64,
                ),
                stages=(
                    SimpleNamespace(
                        stage="import",
                        receipt_sha256="c" * 64,
                    ),
                ),
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_latest_production_import",
        lambda source_path, *, workspace_base, run_id: (
            calls.append(
                ("resolve", source_path, workspace_base, run_id)
            )
            or SimpleNamespace(
                import_root=import_root,
                source_sha256="a" * 64,
                stage_receipt_sha256="c" * 64,
            )
        ),
    )
    monkeypatch.setattr(
        "pipeline.studio_server.main",
        lambda argv: calls.append(("studio", argv)) or 0,
    )

    result = cli.main(
        [
            "serve",
            "--source",
            str(source),
            "--workspace",
            str(workspace),
            "--run-id",
            "production-001",
        ]
    )

    assert result == 0
    assert calls == [
        ("snapshot", source, workspace, "production-001"),
        ("resolve", source, workspace, "production-001"),
        (
            "studio",
            [
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--real-scene-import-root",
                str(import_root),
            ],
        ),
    ]


def test_cli_serve_blocks_before_resolve_when_acceptance_is_not_authoritative(
    tmp_path,
    monkeypatch,
    capsys,
):
    cli = _load_cli()
    source = tmp_path / "source.json"
    workspace = tmp_path / "workspace"
    source.write_text("{}\n", encoding="ascii")
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda *_args, **_kwargs: SimpleNamespace(state="blocked"),
    )
    monkeypatch.setattr(
        cli,
        "resolve_latest_production_import",
        lambda *_args, **_kwargs: pytest.fail(
            "blocked acceptance must not resolve an import"
        ),
    )
    monkeypatch.setattr(
        "pipeline.studio_server.main",
        lambda _argv: pytest.fail(
            "blocked acceptance must not start Studio"
        ),
    )

    result = cli.main(
        [
            "serve",
            "--source",
            str(source),
            "--workspace",
            str(workspace),
            "--run-id",
            "production-001",
        ]
    )

    assert result == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "real-scene serve not accepted\n"


@pytest.mark.parametrize(
    "extra",
    (
        ["--control-points", "points.json"],
        ["--media-root", "capture"],
        ["--remote-config", "remote.json"],
        ["--resume"],
        ["--retry"],
    ),
)
def test_cli_serve_rejects_stage_only_arguments(
    tmp_path,
    monkeypatch,
    capsys,
    extra,
):
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid serve arguments must fail before journal inspection"
        ),
    )

    result = cli.main(
        [
            "serve",
            "--source",
            str(tmp_path / "source.json"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--run-id",
            "production-001",
            *extra,
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "real-scene serve invalid\n"


@pytest.mark.parametrize("missing", ("source", "workspace", "run-id"))
def test_cli_serve_requires_complete_identity(
    tmp_path,
    monkeypatch,
    capsys,
    missing,
):
    cli = _load_cli()
    arguments = {
        "source": str(tmp_path / "source.json"),
        "workspace": str(tmp_path / "workspace"),
        "run-id": "production-001",
    }
    argv = ["serve"]
    for name, value in arguments.items():
        if name != missing:
            argv.extend((f"--{name}", value))
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda *_args, **_kwargs: pytest.fail(
            "incomplete identity must fail before journal inspection"
        ),
    )

    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "real-scene serve invalid\n"


def test_cli_serve_rejects_canary_acceptance_without_resolving_import(
    tmp_path,
    monkeypatch,
    capsys,
):
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="accepted-from-authoritative-decision",
            source=SimpleNamespace(
                role="internal-canary",
                source_sha256="a" * 64,
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_latest_production_import",
        lambda *_args, **_kwargs: pytest.fail(
            "canary acceptance must not resolve a production import"
        ),
    )

    result = cli.main(
        [
            "serve",
            "--source",
            str(tmp_path / "source.json"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--run-id",
            "canary-001",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "real-scene serve invalid\n"


def test_cli_serve_rejects_source_identity_change_before_studio(
    tmp_path,
    monkeypatch,
    capsys,
):
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="accepted-from-authoritative-decision",
            source=SimpleNamespace(
                role="production-acceptance",
                source_sha256="a" * 64,
            ),
            stages=(
                SimpleNamespace(
                    stage="import",
                    receipt_sha256="c" * 64,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_latest_production_import",
        lambda *_args, **_kwargs: SimpleNamespace(
            import_root=tmp_path / "import",
            source_sha256="b" * 64,
            stage_receipt_sha256="c" * 64,
        ),
    )
    monkeypatch.setattr(
        "pipeline.studio_server.main",
        lambda _argv: pytest.fail(
            "source identity mismatch must block Studio"
        ),
    )

    result = cli.main(
        [
            "serve",
            "--source",
            str(tmp_path / "source.json"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--run-id",
            "production-001",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "real-scene serve invalid\n"


def test_cli_serve_rejects_import_outside_accepted_stage_chain(
    tmp_path,
    monkeypatch,
    capsys,
):
    cli = _load_cli()
    monkeypatch.setattr(
        cli,
        "snapshot_real_scene_stages",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="accepted-from-authoritative-decision",
            source=SimpleNamespace(
                role="production-acceptance",
                source_sha256="a" * 64,
            ),
            stages=(
                SimpleNamespace(
                    stage="import",
                    receipt_sha256="c" * 64,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_latest_production_import",
        lambda *_args, **_kwargs: SimpleNamespace(
            import_root=tmp_path / "import",
            source_sha256="a" * 64,
            stage_receipt_sha256="d" * 64,
        ),
    )
    monkeypatch.setattr(
        "pipeline.studio_server.main",
        lambda _argv: pytest.fail(
            "unaccepted import must not start Studio"
        ),
    )

    result = cli.main(
        [
            "serve",
            "--source",
            str(tmp_path / "source.json"),
            "--workspace",
            str(tmp_path / "workspace"),
            "--run-id",
            "production-001",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "real-scene serve invalid\n"


@pytest.mark.parametrize(
    "value",
    ["31.2,121.5", "nan,121.5,4", "91,121.5,4"],
)
def test_cli_rejects_invalid_geo_origin(tmp_path, value, capsys):
    cli = _load_cli()
    source = tmp_path / "source.json"
    _hf_source(source)

    assert (
        cli.main(
            [
                "import",
                "--source",
                str(source),
                "--geo-origin",
                value,
            ]
        )
        == 2
    )
    assert "geo-origin" in capsys.readouterr().err


def test_cli_status_rejects_extra_arguments(tmp_path, capsys):
    cli = _load_cli()
    source = tmp_path / "source.json"
    _hf_source(source)

    result = cli.main(
        [
            "status",
            "--source",
            str(source),
            "--workspace",
            str(tmp_path / "ws"),
            "--run-id",
            "canary-001",
            "--media-root",
            str(tmp_path / "media"),
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "real-scene status invalid" in captured.err


def test_cli_status_missing_workspace_returns_invalid(tmp_path, capsys):
    cli = _load_cli()
    source = tmp_path / "source.json"
    _hf_source(source)

    result = cli.main(
        [
            "status",
            "--source",
            str(source),
            "--run-id",
            "canary-001",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "real-scene status invalid" in captured.err


def test_cli_status_missing_journal_returns_canonical_blocked_snapshot(
    tmp_path,
    capsys,
):
    cli = _load_cli()
    source = tmp_path / "source.json"
    workspace = tmp_path / "workspace"
    _hf_source(source)

    result = cli.main(
        [
            "status",
            "--source",
            str(source),
            "--workspace",
            str(workspace),
            "--run-id",
            "canary-001",
        ]
    )

    assert result == 2
    captured = capsys.readouterr()
    payload = __import__("json").loads(captured.out)
    assert captured.err == ""
    assert payload["schema"] == "nantai.real-scene-status.v1"
    assert payload["state"] == "blocked"
    assert payload["earliest_blocker"] == {
        "reason_code": "receipt-missing",
        "stage": "fetch",
    }
    assert not workspace.exists()
