from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs/manual/production-runtime-release.md"
WORKFLOW = ROOT / ".github/workflows/ci.yml"
PROBE = ROOT / "tests/probe_production_release_content_id.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _job_block(workflow: str, job_id: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_id)}:\n(?P<body>.*?)(?=^  [a-z0-9-]+:\n|\Z)",
        workflow,
    )
    assert match is not None, f"missing CI job: {job_id}"
    return match.group(0)


def test_manual_owns_the_exact_production_build_verify_and_run_path() -> None:
    manual = _read(MANUAL)

    for required in (
        "v1.0.0-preview.2",
        "Production V1",
        "$env:ACCEPTANCE_ROOT",
        "$env:VERSION",
        "$env:ARCHIVE",
        "$env:PRIVACY_POLICY",
        "$env:PRIVACY_REPORT",
        "$env:RELEASE_DIR",
        "python make.py build-production",
        "python make.py verify-production",
        "python make.py audit-production-privacy",
        "python make.py stage-production-assets",
        "python make.py verify-production-assets",
        "python make.py serve",
        "PRODUCTION-RELEASE.json",
        "evidence/public-evidence.json",
        "SHA256SUMS.txt",
        ".nantai-studio/",
    ):
        assert required in manual

    assert re.search(r"VERSION.*没有默认值", manual, re.DOTALL)
    assert re.search(r"production_release_allowed\s*=\s*true", manual)
    assert re.search(r"下载.*字节.*verify-production", manual, re.DOTALL)
    assert re.search(r"真实浏览器.*QA", manual, re.IGNORECASE)
    assert re.search(
        r"stage-production-assets.*四个.*公开",
        manual,
        re.DOTALL,
    )
    assert "nantai-3d-v1.0.0-runtime.zip.sha256" in manual


def test_manual_states_privacy_and_hash_limits_without_promoting_reality() -> None:
    manual = _read(MANUAL)

    for excluded in (
        "原始照片",
        "原始视频",
        "控制点坐标",
        "凭据",
        "工作区",
    ):
        assert excluded in manual
    assert re.search(r"哈希.*不证明.*权利", manual, re.DOTALL)
    assert re.search(r"哈希.*不证明.*物理现实", manual, re.DOTALL)
    assert re.search(r"不得.*v1\.0\.0", manual)


def test_readme_and_status_keep_one_concise_authoritative_release_entry() -> None:
    readme = _read(ROOT / "README.md")
    docs_index = _read(ROOT / "docs/README.md")
    status = _read(ROOT / "docs/production-v1-status.md")
    manual_link = "docs/manual/production-runtime-release.md"

    assert manual_link in readme
    assert "manual/production-runtime-release.md" in docs_index
    assert "python make.py build-production" not in readme
    assert "release tooling" in status
    assert "正式素材" in status
    assert "真实 GPU" in status
    assert "实测控制点" in status
    assert "真实浏览器" in status


def test_ci_has_three_os_production_contract_and_content_id_compare_jobs() -> None:
    workflow = _read(WORKFLOW)
    matrix_job = _job_block(workflow, "production-release-contract")
    compare_job = _job_block(workflow, "production-release-content-id-compare")

    assert re.search(
        r"os:\s*\[ubuntu-latest,\s*windows-latest,\s*macos-latest\]",
        matrix_job,
    )
    assert "modeled-contract-not-real-release" in matrix_job
    assert "tests/probe_production_release_content_id.py" in matrix_job
    assert "--noconftest" in matrix_job
    for dependency in (
        "numpy==",
        "pydantic==",
        "plyfile==",
        "loguru==",
        "pywin32==",
    ):
        assert dependency in matrix_job
    for test_path in (
        "tests/test_release_archive.py",
        "tests/test_production_release_contract.py",
        "tests/test_production_release_verifier.py",
        "tests/test_production_release_cli.py",
        "tests/test_production_release_privacy.py",
        "tests/test_production_release_assets.py",
    ):
        assert test_path in matrix_job
    assert "actions/upload-artifact@v4" in matrix_job
    assert "production-release-content-id-${{ matrix.os }}" in matrix_job

    assert "runs-on: ubuntu-latest" in compare_job
    assert "needs: production-release-contract" in compare_job
    assert "actions/download-artifact@v4" in compare_job
    for os_name in ("ubuntu-latest", "windows-latest", "macos-latest"):
        assert os_name in compare_job
    assert re.search(r"(diff|compare).*content", compare_job, re.IGNORECASE | re.DOTALL)


def test_content_id_probe_writes_one_ascii_digest_line(tmp_path: Path) -> None:
    output = tmp_path / "content-id.txt"
    completed = subprocess.run(
        [sys.executable, str(PROBE), str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    payload = output.read_bytes()
    assert re.fullmatch(rb"[0-9a-f]{64}\n", payload)
