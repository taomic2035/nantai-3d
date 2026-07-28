from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/verification/2026-07-26-real-golden-path-canary.md"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_real_canary_report_records_exact_local_evidence() -> None:
    report = REPORT.read_text(encoding="utf-8")

    for identity in (
        "2c1602472eadafc6728a139515f81f62ab914c66",
        "a9c51dea74fbe5bfdfd637d85454380cd65abe8c",
        "fc208c22958476b95394029f2704ba8cdec04fcd3a4a6d61339cbfe350dbb9a6",
        "611a153d8c5a7e3003e8a00409dd8f3c931f3a3bccd7a798581396c6d0d10f1a",
        "225f001523ae56d1a03096b8406d6265e6f222be54bfe375834a1ea7d285ca62",
        "467c2246541baaceb90fdda81e4d517baf21db86f7da1c07f45dba4fe82f59b0",
        "c58e8a32e61a7eecc98147c7e150d529ffc55704125fdaff61c33610df64aa25",
        "85d7d85c7f046f2c7ce402206d5547c716bf73466247e5791e235a57baa405cb",
        "af4cc9f7d075759d0e48e179ab4e1f2fab69019e5c444006df2f60d7abe637a4",
    ):
        assert identity in report
    for measured in (
        "408/408",
        "379,280,986",
        "COLMAP 4.1.0",
        "96/100",
        "0.96",
        "Brush 0.3.0",
        "preview-only",
    ):
        assert measured in report


def test_real_canary_report_preserves_negative_drill_receipts() -> None:
    report = REPORT.read_text(encoding="utf-8")

    for identity in (
        "5c5cf5219fb5cb4b53a16a5c3019d4e9f3c44e1ae21457526568f3b0b0a53834",
        "66f544f5b4d0470711bcbbef908cccdfd3b96aa0a2162eee06ce6cc9f532830d",
        "3e9536cc0481c8b8c86a85f73309f5117fa542da1353f001e7210b06331955fc",
        "b803da1cab3e151390b6690e7d598742fa05f0596ae2b5e1a06e38847087ab0d",
    ):
        assert identity in report
    assert "blocked" in report
    assert "unknown" in report
    assert "submit_calls=1" in report
    assert "explicit retry" in report


def test_real_canary_docs_do_not_promote_internal_evidence() -> None:
    report = REPORT.read_text(encoding="utf-8")
    readme = _read("README.md")
    manual = _read("docs/manual/reconstruction-setup.md")
    workflow = _read("docs/real-data-workflow.md")

    for boundary in (
        "internal-only",
        "arbitrary",
        "unaligned",
        "production_release_allowed=false",
        "subprojects 2–5",
    ):
        assert boundary in report
    for blocker in (
        "SSH alias",
        "known-hosts",
        "remote root",
        "CUDA container",
        "four non-coplanar",
    ):
        assert blocker in report
    assert "2026-07-26-real-golden-path-canary.md" in readme
    assert "real-canary" in manual
    assert "train-production" in manual
    assert "production-acceptance" in workflow
    assert "rights receipt" in workflow
    assert "python -m pipeline.production_capture_inputs" in workflow
    assert "python -m pipeline.production_capture_inputs" in manual
    assert "capture-rights-receipt.json" in workflow
    assert "production-source.json" in workflow
    assert "registration-policy.json" in workflow
    assert "--min-registered-count" in workflow
    assert "--min-registered-ratio" in manual
    assert "五个 registration 阈值也没有默认值" in workflow
    assert "registration 阈值没有默认值" in manual
    assert "不要手写 `rights_receipt_sha256`" in workflow
    assert "失败不会留下可误用的半套输入" in workflow


def test_production_viewer_docs_materialize_provenance_bound_inputs() -> None:
    manual = _read("docs/manual/reconstruction-setup.md")
    status = _read("docs/production-v1-status.md")

    assert "python -m pipeline.viewer_inputs" in manual
    assert "python -m pipeline.real_scene_paths" in manual
    assert "$paths.import_root" in manual
    assert "不会回退到旧 import" in manual
    assert "--import-root" in manual
    assert "--output-dir" in manual
    assert "nantai.viewer-camera-set.v2" in manual
    assert "registered-camera-maximin-v1" in manual
    assert "不能手写或任意挑选三机位" in manual
    assert "python make.py real-scene" in manual
    assert '"WORKSPACE=$workspace"' in manual
    assert '"RUN_ID=$runId" status' in manual
    assert '"RUN_ID=$runId" serve' in manual
    assert "authoritative acceptance" in manual
    assert "不写 stage" in manual
    assert "receipt 白名单" in manual
    assert "python -m pipeline.viewer_session" in manual
    assert "--human-review-policy-output" in manual
    assert "临时回环端口" in manual
    assert "默认打开可见 Chromium" in manual
    assert "python -m pipeline.human_review_inputs" in manual
    assert "--viewer-report" in manual
    assert "全部七类结论" in manual
    assert "不会替 reviewer 自动接受" in manual
    assert "python -m scripts.real_scene accept" in manual
    assert "--human-visual-review" in manual
    assert "status=completed" in manual
    assert "--retry" in manual
    assert "camera-set v2" in status
    assert "receipt-bound" in status
    assert "viewer_session" in status
    assert "human_review_inputs" in status
