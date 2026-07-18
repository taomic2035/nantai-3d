"""Operator-facing synthetic-village CLI contracts."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.synthetic_village.material_bundle import MaterialBundleResult
from scripts import synthetic_village as synthetic_village_cli


def test_build_materials_prints_one_stable_json_object(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    final_directory = tmp_path / "published" / ("a" * 64)
    calls = []

    def publish_material_bundle(**kwargs):
        calls.append(kwargs)
        return MaterialBundleResult(
            bundle_id="a" * 64,
            final_directory=final_directory,
            record_count=24,
            reused=False,
        )

    monkeypatch.setattr(
        synthetic_village_cli,
        "_publish_material_bundle",
        lambda: publish_material_bundle,
    )

    assert synthetic_village_cli.main(
        [
            "build-materials",
            "--visual-pack-root",
            str(tmp_path / "visual"),
            "--publication-root",
            str(tmp_path / "published"),
            "--work-root",
            str(tmp_path / "work"),
        ],
    ) == 0

    assert calls == [
        {
            "visual_pack_root": tmp_path / "visual",
            "publication_root": tmp_path / "published",
            "work_root": tmp_path / "work",
        },
    ]
    assert json.loads(capsys.readouterr().out) == {
        "bundle_id": "a" * 64,
        "final_directory": str(final_directory),
        "record_count": 24,
        "reused": False,
    }
