from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs/manual/production-runtime-release.md"
STATUS = ROOT / "docs/production-v1-status.md"
RUNTIME_GUIDE = ROOT / "release/production-verify-and-run.md"
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


def test_manual_hash_boundary_requires_an_external_trust_anchor() -> None:
    manual = _read(MANUAL)
    hash_boundary = manual.split("## 哈希与现实边界", 1)[1].split(
        "## 发布清单",
        1,
    )[0]

    assert re.search(
        r"内容哈希只能证明.*当前字节.*给定摘要一致",
        hash_boundary,
        re.DOTALL,
    )
    assert re.search(
        r"只有.*摘要或签名.*来自可信外部来源"
        r".*才能\s*证明下载字节.*锁定的字节一致",
        hash_boundary,
        re.DOTALL,
    )
    for unproven_boundary in (
        "不证明素材权利",
        "不证明发布者来源或真实性",
        "不证明 staging 已执行",
        "不证明外部授权",
        "不证明场景对应物理现实",
    ):
        assert unproven_boundary in hash_boundary
    assert re.search(r"不声称.*签名.*存在", hash_boundary, re.DOTALL)
    assert "内容哈希证明“当前字节与被签署字节一致”" not in hash_boundary
    assert not re.search(
        r"内容哈希证明.*(?:签名|签署).*字节一致",
        hash_boundary,
        re.DOTALL,
    )


def test_manual_separates_repository_and_extracted_runtime_commands() -> None:
    manual = _read(MANUAL)
    assert "仓库维护命令" in manual
    runtime_section = manual.split("## 解压与运行", 1)[1].split(
        "## 私有闭包与公开闭包",
        1,
    )[0]
    assert "解压包命令" in runtime_section
    for target in ("help", "verify", "serve"):
        assert f"python make.py {target}" in runtime_section
    for maintenance_target in (
        "build-production",
        "verify-production",
        "audit-production-privacy",
        "stage-production-assets",
        "verify-production-assets",
    ):
        assert maintenance_target not in runtime_section


def test_manual_build_example_creates_the_archive_parent_before_building() -> None:
    manual = _read(MANUAL)
    build = manual.split("## 构建", 1)[1].split(
        "## 独立验证候选字节",
        1,
    )[0]
    create_parent = "New-Item -ItemType Directory -Force dist | Out-Null"
    candidate_assignment = (
        '$candidate = (Join-Path $PWD "dist\\nantai-3d-v1.0.0-candidate.zip")'
    )
    archive_assignment = "$env:ARCHIVE = $candidate"

    assert create_parent in build
    assert candidate_assignment in build
    assert archive_assignment in build
    assert build.index(create_parent) < build.index(candidate_assignment)
    assert build.index(candidate_assignment) < build.index(archive_assignment)
    assert build.index(archive_assignment) < build.index(
        "python make.py build-production"
    )


def test_manual_reuses_one_private_candidate_through_staging() -> None:
    manual = _read(MANUAL)
    build = manual.split("## 构建", 1)[1].split(
        "## 独立验证候选字节",
        1,
    )[0]
    verification = manual.split("## 独立验证候选字节", 1)[1].split(
        "## 隐私机器审计",
        1,
    )[0]
    privacy = manual.split("## 隐私机器审计", 1)[1].split(
        "## 整理最终公开资产",
        1,
    )[0]
    staging = manual.split("## 整理最终公开资产", 1)[1].split(
        "候选构建和 staging rebuild",
        1,
    )[0]

    assert (
        '$candidate = (Join-Path $PWD "dist\\nantai-3d-v1.0.0-candidate.zip")'
        in build
    )
    for section in (build, verification, privacy, staging):
        assert "$env:ARCHIVE = $candidate" in section
    assert "$candidate =" not in verification
    assert "$candidate =" not in privacy
    assert "$candidate =" not in staging


def test_manual_separates_private_candidate_from_downloaded_canonical_name() -> None:
    manual = _read(MANUAL)
    pre_publication = manual.split("成功目录精确包含：", 1)[0]
    public_assets = manual.split("成功目录精确包含：", 1)[1].split(
        "```",
        2,
    )[1]
    canonical_archive = "nantai-3d-v1.0.0-runtime.zip"

    assert canonical_archive in public_assets
    assert re.search(
        rf"下载.*canonical.*`{re.escape(canonical_archive)}`",
        manual,
        re.IGNORECASE | re.DOTALL,
    )
    assert canonical_archive not in pre_publication
    assert "candidate" in pre_publication
    assert re.search(r"候选.*(?:不得|不会).*上传", manual, re.DOTALL)


def test_manual_distinguishes_acceptance_staging_from_download_verification() -> None:
    manual = _read(MANUAL)
    staging = manual.split("## 整理最终公开资产", 1)[1].split(
        "## 解压与运行",
        1,
    )[0]
    stage_command = staging.index("python make.py stage-production-assets")

    assert staging.index("$env:ACCEPTANCE_ROOT") < stage_command
    assert staging.index("$env:VERSION") < stage_command
    assert re.search(
        r"正式发布流程要求运行.*stage-production-assets",
        staging,
        re.DOTALL,
    )
    assert re.search(
        r"stage-production-assets.*重新打开.*(?:real acceptance|真实 acceptance)",
        staging,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(r"当前精确\s+HEAD", staging)
    assert re.search(r"deterministic\s+acceptance rebuild", staging)
    assert re.search(
        r"与候选构建(?:相同|一致).*pinned builder environment",
        staging,
        re.DOTALL,
    )
    assert re.search(
        r"重建产物.*输入候选.*逐字节比对.*完全一致"
        r".*隐私审计.*只发布四个最终公开资产",
        staging,
        re.DOTALL,
    )
    assert re.search(
        r"不同 zlib.*ZIP bytes.*不是可移植身份",
        staging,
        re.DOTALL,
    )
    assert re.search(
        r"跨平台身份.*package content ID.*每个"
        r".*artifact.*SHA-256/字节数集合",
        staging,
        re.DOTALL,
    )

    download_verification = staging.split("发布后把四个 GitHub 资产", 1)[1]
    assert re.search(
        r"verify-production-assets.*只检查.*四个文件"
        r".*内部字节绑定.*内部合同",
        download_verification,
        re.DOTALL,
    )
    for unproven_boundary in (
        r"不能证明发布者来源或真实性",
        r"不能证明 staging 已执行",
        r"不能证明私有 acceptance 实际重新打开",
        r"不能证明外部授权",
        r"不能重新证明真实 CUDA、米制对齐、Viewer QA 或人工复核",
    ):
        assert re.search(unproven_boundary, download_verification)
    assert re.search(
        r"不重新打开或访问.*ACCEPTANCE_ROOT.*私有证据",
        download_verification,
        re.DOTALL,
    )
    assert re.search(
        r"报告.*声明.*source-bound identity",
        download_verification,
        re.DOTALL,
    )
    assert re.search(
        r"真实性.*可信发布渠道.*外部可信.*(?:digest|摘要|签名)"
        r".*如果存在",
        download_verification,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(r"不声称.*签名", download_verification, re.DOTALL)


def test_downloaded_runtime_guide_limits_the_offline_verifier_claim() -> None:
    guide = _read(RUNTIME_GUIDE)

    for required in (
        "byte-integrity-verified",
        "does not reopen",
        "ACCEPTANCE_ROOT",
        "re-prove real CUDA",
    ):
        assert required in guide

    assert re.search(
        r"official release process requires.*pre-release staging"
        r".*reopen ACCEPTANCE_ROOT.*exact source commit",
        guide,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"downloaded verifier checks only.*internal byte bindings"
        r".*internal contracts.*supplied four files",
        guide,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"does\s+not\s+reopen or access ACCEPTANCE_ROOT or private evidence",
        guide,
        re.IGNORECASE,
    )
    for unproven_boundary in (
        r"does\s+not\s+prove\s+publisher origin or authenticity",
        r"does\s+not\s+prove\s+(?:that\s+)?staging was executed",
        r"does\s+not\s+prove\s+(?:that\s+)?private acceptance was actually reopened",
        r"does\s+not\s+prove\s+external\s+authorization",
        r"does\s+not\s+re-prove real CUDA, metric alignment, Viewer QA or human\s+review",
    ):
        assert re.search(unproven_boundary, guide, re.IGNORECASE)
    assert re.search(
        r"authenticity must come from a trusted release channel"
        r".*externally trusted (?:digest|signature)"
        r".*if one exists",
        guide,
        re.IGNORECASE | re.DOTALL,
    )
    assert re.search(
        r"does not claim.*signature exists",
        guide,
        re.IGNORECASE | re.DOTALL,
    )
    assert "was reopened by the pre-release staging step" not in guide


def test_readme_and_status_keep_one_concise_authoritative_release_entry() -> None:
    readme = _read(ROOT / "README.md")
    docs_index = _read(ROOT / "docs/README.md")
    status = _read(STATUS)
    manual_link = "docs/manual/production-runtime-release.md"

    assert manual_link in readme
    assert "manual/production-runtime-release.md" in docs_index
    assert "python make.py build-production" not in readme
    assert "release tooling" in status
    assert "正式素材" in status
    assert "真实 GPU" in status
    assert "实测控制点" in status
    assert "真实浏览器" in status
    for release_boundary in ("acceptance rebuild", "staging", "download verifier"):
        assert release_boundary in status


def test_status_does_not_promote_download_consistency_to_authorization() -> None:
    status = _read(STATUS)
    release_boundary = status.split(
        "这里的 release tooling ready",
        1,
    )[1].split("五个外部门禁仍明确开放", 1)[0]

    assert "已授权" not in release_boundary
    assert re.search(
        r"download verifier.*只检查.*内部字节绑定.*内部合同",
        release_boundary,
        re.DOTALL,
    )
    assert re.search(
        r"不能证明发布者来源或真实性.*不能证明 staging 已执行"
        r".*不能证明私有 acceptance 实际重新打开.*不能证明外部授权",
        release_boundary,
        re.DOTALL,
    )


def test_status_keeps_all_five_real_scene_gates_open_for_one_identity() -> None:
    status = _read(STATUS)
    summary = status.split("## 一句话状态", 1)[1].split(
        "## 已完成的必要基础",
        1,
    )[0]
    gate_match = re.search(
        r"五个外部门禁仍明确开放：(?P<gates>.*?)(?:\n\n|\Z)",
        status,
        re.DOTALL,
    )

    assert gate_match is not None
    gates = gate_match.group("gates")
    assert re.fullmatch(
        r"真实重叠采集、accepted real-photo SfM、non-mock CUDA\s+"
        r"3DGS、实测米制对齐，以及同一 scene identity 的真实浏览器重建 "
        r"Viewer/human QA。\s*它们未全部通过前，状态保持 Preview/unknown，"
        r"不会生成或发布正式 `v1\.0\.0`。",
        gates,
        re.DOTALL,
    )
    assert re.search(
        r"它们未全部通过前.*Preview/unknown"
        r".*不会生成或发布正式 `v1\.0\.0`",
        gates,
        re.DOTALL,
    )
    assert re.search(r"仍是\s+Preview，不是\s+Production V1", summary)


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
        "tests/test_production_runtime_runner.py",
        "tests/test_production_release_builder.py",
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
