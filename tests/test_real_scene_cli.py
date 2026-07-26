from __future__ import annotations

import importlib.util
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


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts/real_scene.py"
    spec = importlib.util.spec_from_file_location("real_scene_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        rights_receipt_sha256=__import__("hashlib").sha256(
            canonical_model_bytes(rights)
        ).hexdigest(),
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

    assert cli.main(
        [
            "fetch",
            "--source",
            str(source),
            "--run-id",
            "canary-001",
        ]
    ) == 0

    assert calls[0][1] == "fetch"
    assert calls[0][2].run_id == "canary-001"
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

    assert cli.main(
        [
            "import",
            "--source",
            str(source),
            "--geo-origin",
            value,
        ]
    ) == 2
    assert "geo-origin" in capsys.readouterr().err
