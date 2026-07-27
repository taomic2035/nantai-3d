from __future__ import annotations

import json

import pipeline.real_scene_paths as paths_module
from pipeline.real_scene_paths import main
from pipeline.real_scene_runner import (
    RealSceneBlockedError,
    ResolvedProductionImport,
)


def _resolved(tmp_path):
    workspace = tmp_path / "workspace"
    import_root = workspace / "stages/import/attempt-one"
    return ResolvedProductionImport(
        workspace_root=workspace,
        import_root=import_root,
        import_receipt_path=import_root / "import-receipt.json",
        stage_receipt_path=(
            workspace / "receipts/import" / ("a" * 64 + ".json")
        ),
        stage_receipt_sha256="a" * 64,
        source_sha256="b" * 64,
    )


def test_paths_cli_emits_canonical_machine_readable_workspace(
    tmp_path,
    monkeypatch,
    capsys,
):
    resolved = _resolved(tmp_path)
    calls = []

    def _resolve(source, *, workspace_base, run_id):
        calls.append((source, workspace_base, run_id))
        return resolved

    monkeypatch.setattr(
        paths_module,
        "resolve_latest_production_import",
        _resolve,
    )

    exit_code = main(
        [
            "--source",
            str(tmp_path / "source.json"),
            "--workspace",
            str(tmp_path / "real-scene"),
            "--run-id",
            "production-a",
        ]
    )

    assert exit_code == 0
    payload = capsys.readouterr().out.encode("ascii")
    decoded = json.loads(payload)
    assert payload == (
        json.dumps(
            decoded,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    assert decoded == {
        "import_receipt_path": str(resolved.import_receipt_path),
        "import_root": str(resolved.import_root),
        "schema": "nantai.real-scene-paths.v1",
        "source_sha256": "b" * 64,
        "stage_receipt_path": str(resolved.stage_receipt_path),
        "stage_receipt_sha256": "a" * 64,
        "workspace_root": str(resolved.workspace_root),
    }
    assert calls == [
        (
            (tmp_path / "source.json").absolute(),
            (tmp_path / "real-scene").absolute(),
            "production-a",
        )
    ]


def test_paths_cli_reports_blocked_latest_import(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        paths_module,
        "resolve_latest_production_import",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RealSceneBlockedError("latest production import is blocked")
        ),
    )

    exit_code = main(
        [
            "--source",
            str(tmp_path / "source.json"),
            "--workspace",
            str(tmp_path / "real-scene"),
            "--run-id",
            "production-a",
        ]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "latest production import is blocked" in captured.err
