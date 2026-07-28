from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pipeline.production_release_builder as builder_module
import scripts.build_production_release as build_cli
from pipeline.production_release_builder import ProductionReleaseBuild
from tests.production_release_fixtures import (
    write_modeled_production_archive,
    write_modeled_production_tree,
)


def _run_isolated(script: Path, target: Path) -> subprocess.CompletedProcess[str]:
    source = (
        "import runpy,sys;"
        "sys.stdout.reconfigure(encoding='ascii',errors='strict');"
        f"sys.argv=['verify_production_release.py',r'{target}','--json'];"
        f"runpy.run_path(r'{script}',run_name='__main__')"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )


def test_isolated_cli_emits_ascii_json_for_valid_archive(tmp_path: Path) -> None:
    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(tree, archive)
    script = Path(__file__).parents[1] / "scripts/verify_production_release.py"

    completed = _run_isolated(script, archive)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["valid"] is True
    assert report["package_integrity"] == "verified"
    assert report["release_contract"] == "modeled-contract-only"
    completed.stdout.encode("ascii")


def test_isolated_cli_emits_ascii_json_for_valid_tree(tmp_path: Path) -> None:
    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    script = Path(__file__).parents[1] / "scripts/verify_production_release.py"

    completed = _run_isolated(script, tree)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["valid"] is True
    assert report["package_integrity"] == "verified"
    assert report["release_contract"] == "modeled-contract-only"
    completed.stdout.encode("ascii")


def test_isolated_cli_fails_cleanly_without_partial_extraction(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"not a zip")
    script = Path(__file__).parents[1] / "scripts/verify_production_release.py"

    completed = _run_isolated(script, archive)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert not (tmp_path / "corrupt").exists()


def test_build_cli_uses_exact_git_head_and_tracked_allowlist(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    acceptance = tmp_path / "private"
    acceptance.mkdir()
    output = tmp_path / "dist/runtime.zip"
    output.parent.mkdir()
    observed = {}

    def build(**kwargs):
        observed.update(kwargs)
        return ProductionReleaseBuild(
            archive_path=output,
            archive_sha256="b" * 64,
            package_content_id="c" * 64,
            artifact_count=9,
            total_bytes=123,
            scene_identity="scene-" + "d" * 64,
            acceptance_report_sha256="e" * 64,
        )

    identity = builder_module.ProductionReleaseSourceIdentity(
        source_commit="a" * 40,
        tracked_files=("LICENSE", "pipeline/runtime.py"),
    )
    monkeypatch.setattr(
        build_cli,
        "resolve_production_release_source_identity",
        lambda root: identity,
    )
    monkeypatch.setattr(
        build_cli,
        "build_production_release_archive",
        build,
    )

    exit_code = build_cli.main(
        [
            "--acceptance-root",
            str(acceptance),
            "--version",
            "v1.0.0",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert observed["source_commit"] == "a" * 40
    assert observed["tracked_files"] == (
        "LICENSE",
        "pipeline/runtime.py",
    )
    assert json.loads(capsys.readouterr().out)["archive_sha256"] == "b" * 64
