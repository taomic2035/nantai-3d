from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
