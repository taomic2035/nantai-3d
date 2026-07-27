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
