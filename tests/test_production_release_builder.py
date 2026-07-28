from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

import pipeline.production_release_builder as builder_module
from pipeline.production_release_builder import (
    ProductionReleaseBuilderError,
    derive_production_release_context,
)
from pipeline.production_training_closure import (
    ProductionTrainingClosure,
    canonical_production_training_closure_bytes,
)
from pipeline.real_dataset import (
    CaptureRightsReceipt,
    canonical_model_bytes,
)
from pipeline.real_scene_acceptance import (
    AcceptanceDecision,
    AcceptanceDirectoryReference,
    AcceptanceEvidenceReference,
    AcceptanceGate,
    HumanReviewPolicy,
    RealSceneAcceptance,
    canonical_human_review_bytes,
    canonical_human_review_policy_bytes,
    canonical_real_scene_acceptance_bytes,
)
from pipeline.real_scene_import import (
    ImportArtifactBinding,
    RealSceneImportReceipt,
)
from pipeline.release_archive import canonical_json_bytes
from pipeline.viewer_acceptance import (
    canonical_viewer_performance_policy_bytes,
    canonical_viewer_performance_report_bytes,
)
from tests.test_real_scene_acceptance import (
    _review_for_viewer_capture,
)
from tests.test_viewer_acceptance import (
    SCENE_MANIFEST_BYTES,
)
from tests.test_viewer_acceptance import (
    _policy as viewer_policy,
)
from tests.test_viewer_acceptance import (
    _report as viewer_report_v1,
)
from tests.test_viewer_acceptance import (
    _report_v2 as viewer_report_v2,
)

GATES = (
    "dataset",
    "capture",
    "sfm",
    "production-training",
    "import-integrity",
    "render-quality",
    "viewer-performance",
    "human-review",
    "release-rights",
    "metric-alignment",
)


def _reference(root: Path, relative: str) -> AcceptanceEvidenceReference:
    payload = (root / relative).read_bytes()
    return AcceptanceEvidenceReference(
        path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
    )


def _write(root: Path, relative: str, payload: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _closure() -> ProductionTrainingClosure:
    return ProductionTrainingClosure.create(
        status="verified-production",
        training_bundle_sha256="1" * 64,
        result_bundle_archive_sha256="2" * 64,
        result_bundle_manifest_sha256="3" * 64,
        attempt_receipt_sha256="4" * 64,
        request_sha256="5" * 64,
        result_sha256="6" * 64,
        runtime_measurement_sha256="7" * 64,
        runtime_policy_sha256="8" * 64,
        runtime_decision_sha256="9" * 64,
        render_policy_sha256="a" * 64,
        render_report_sha256="b" * 64,
        render_decision_sha256="c" * 64,
        job_id="job-1",
        attempt_id="attempt-1",
        container_instance_id="d" * 64,
        container_identity="ghcr.io/nantai/trainer@sha256:" + "e" * 64,
        point_cloud_sha256="f" * 64,
        gaussian_count=100_000,
        sh_degree=3,
        trainer_config_sha256="0" * 64,
        training_log_sha256="1" * 64,
        dataparser_transform_sha256="2" * 64,
        held_out_frame_count=3,
    )


def _import_receipt(root: Path) -> RealSceneImportReceipt:
    closure = _closure()
    payloads = {
        "alignment/control-points.json": b'{"private":"controls"}\n',
        "alignment/decision.json": b'{"accepted":true}\n',
        "alignment/measurement.json": b'{"rms":0.1}\n',
        "alignment/observed-registration.json": b'{"aligned":true}\n',
        "alignment/policy.json": b'{"maximum_rms_m":0.25}\n',
        "alignment/source-registration.json": b'{"source":"private"}\n',
        "contracts/registration.json": b'{"registration":"private"}\n',
        "contracts/splat-input.json": b'{"input":"private"}\n',
        "evidence/production-training-closure.json": (
            canonical_production_training_closure_bytes(closure)
        ),
        "import-integrity.json": b'{"verified":true}\n',
        "inputs/normalized.ply": b"ply\n",
        "inputs/source.ply": b"ply\n",
        "web/chunks/chunks.json": b'{"chunks":[]}\n',
        "web/recon_manifest.json": SCENE_MANIFEST_BYTES,
    }
    import_root = root / "imported"
    for relative, payload in payloads.items():
        _write(import_root, relative, payload)
    artifacts = tuple(
        ImportArtifactBinding(
            path=relative,
            byte_length=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for relative, payload in sorted(payloads.items())
    )
    receipt = RealSceneImportReceipt(
        source_role="production-acceptance",
        training_quality_role="production",
        training_bundle_sha256=closure.training_bundle_sha256,
        training_request_sha256=closure.request_sha256,
        training_result_sha256=closure.result_sha256,
        production_training_closure_path=(
            "evidence/production-training-closure.json"
        ),
        production_training_closure_sha256=closure.content_sha256,
        production_runtime_decision_sha256=(
            closure.runtime_decision_sha256
        ),
        gaussian_count=100_000,
        sh_degree=3,
        normalized_quaternion_count=100_000,
        target_frame_id="ENU:modeled",
        target_units="meters",
        geometry_usability="metric-aligned",
        chunk_size=10.0,
        chunk_units="metres",
        alignment_rms_m=0.1,
        alignment_source_registration_path=(
            "alignment/source-registration.json"
        ),
        alignment_control_points_path="alignment/control-points.json",
        alignment_observed_registration_path=(
            "alignment/observed-registration.json"
        ),
        alignment_measurement_path="alignment/measurement.json",
        alignment_policy_path="alignment/policy.json",
        alignment_decision_path="alignment/decision.json",
        alignment_measurement_sha256="3" * 64,
        alignment_policy_sha256="4" * 64,
        alignment_decision_sha256="5" * 64,
        artifacts=artifacts,
    )
    _write(
        import_root,
        "import-receipt.json",
        canonical_model_bytes(receipt),
    )
    return receipt


def _modeled_acceptance_tree(tmp_path: Path) -> dict[str, object]:
    root = tmp_path / "acceptance"
    root.mkdir()
    receipt = _import_receipt(root)
    rights = CaptureRightsReceipt(
        schema="nantai.capture-rights-receipt.v1",
        dataset_id="mountain-village-private",
        operator="Reviewer One",
        capture_scope="/home/private/control-points",
        effective_date=date(2026, 7, 28),
        processing_purposes=("3d-reconstruction",),
        redistribution_allowed=True,
        release_inclusion_allowed=True,
    )
    _write(root, "fetch/rights.json", canonical_model_bytes(rights))
    for relative in (
        "fetch/source.json",
        "capture/manifest.json",
        "sfm/prepared-capture-evidence.json",
        "sfm/registration.json",
        "sfm/registration-policy.json",
        "sfm/registration-report.json",
        "training/training-bundle/training-job.zip",
        "training/remote-result/render-evaluation/policy.json",
        "training/remote-result/render-evaluation/report.json",
    ):
        _write(root, relative, f"modeled:{relative}\n".encode("ascii"))

    viewer = viewer_report_v2(root)
    viewer_policy_value = viewer_policy()
    _write(
        root,
        "viewer/policy.json",
        canonical_viewer_performance_policy_bytes(viewer_policy_value),
    )
    _write(
        root,
        "viewer/report.json",
        canonical_viewer_performance_report_bytes(viewer),
    )
    human_review = _review_for_viewer_capture(root, viewer)
    human_policy = HumanReviewPolicy(
        source_role="production-acceptance",
        required_categories=tuple(
            row.category for row in human_review.dispositions
        ),
        required_pose_ids=tuple(
            row.pose_id for row in human_review.screenshots
        ),
        maximum_screenshot_bytes=10_000,
    )
    _write(
        root,
        "human/policy.json",
        canonical_human_review_policy_bytes(human_policy),
    )
    _write(
        root,
        "human/review.json",
        canonical_human_review_bytes(human_review),
    )

    report = RealSceneAcceptance(
        source_role="production-acceptance",
        source=_reference(root, "fetch/source.json"),
        rights_receipt=_reference(root, "fetch/rights.json"),
        fetch_root=AcceptanceDirectoryReference(path="fetch"),
        capture_bundle=AcceptanceDirectoryReference(path="capture"),
        capture_manifest=_reference(root, "capture/manifest.json"),
        prepared_capture_evidence=_reference(
            root,
            "sfm/prepared-capture-evidence.json",
        ),
        sfm_root=AcceptanceDirectoryReference(path="sfm"),
        registration=_reference(root, "sfm/registration.json"),
        registration_policy=_reference(
            root,
            "sfm/registration-policy.json",
        ),
        registration_report=_reference(
            root,
            "sfm/registration-report.json",
        ),
        training_root=AcceptanceDirectoryReference(path="training"),
        training_bundle=_reference(
            root,
            "training/training-bundle/training-job.zip",
        ),
        import_root=AcceptanceDirectoryReference(path="imported"),
        import_receipt=_reference(
            root,
            "imported/import-receipt.json",
        ),
        render_root=AcceptanceDirectoryReference(
            path="training/remote-result"
        ),
        render_policy=_reference(
            root,
            "training/remote-result/render-evaluation/policy.json",
        ),
        render_report=_reference(
            root,
            "training/remote-result/render-evaluation/report.json",
        ),
        viewer_policy=_reference(root, "viewer/policy.json"),
        viewer_report=_reference(root, "viewer/report.json"),
        human_review_policy=_reference(root, "human/policy.json"),
        human_visual_review=_reference(root, "human/review.json"),
    )
    report_path = root / "real-scene-acceptance.json"
    report_path.write_bytes(canonical_real_scene_acceptance_bytes(report))
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    decision = AcceptanceDecision(
        source_role="production-acceptance",
        technical_accepted=True,
        canary_accepted=False,
        production_release_allowed=True,
        gates=tuple(
            AcceptanceGate(gate=gate, state="accepted")
            for gate in GATES
        ),
        failed_gates=(),
        reasons=(),
        report_sha256=report_sha,
    )
    return {
        "root": root,
        "report_path": report_path,
        "report": report,
        "decision": decision,
        "import_receipt": receipt,
    }


def _patch_outer_validators(monkeypatch, fixture: dict[str, object]) -> list[str]:
    calls: list[str] = []

    def accept(_path: Path) -> AcceptanceDecision:
        calls.append("acceptance")
        return fixture["decision"]

    def imported(
        _receipt_path: Path,
        _root: Path,
    ) -> RealSceneImportReceipt:
        calls.append("import")
        return fixture["import_receipt"]

    monkeypatch.setattr(
        builder_module,
        "validate_real_scene_acceptance",
        accept,
    )
    monkeypatch.setattr(
        builder_module,
        "validate_real_scene_import_receipt",
        imported,
    )
    return calls


def test_acceptance_projection_is_fresh_redacted_and_canonical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    calls = _patch_outer_validators(monkeypatch, fixture)

    context = derive_production_release_context(fixture["report_path"])

    assert calls == ["acceptance", "import"]
    assert context.decision.production_release_allowed is True
    assert context.import_receipt.source_role == "production-acceptance"
    assert context.public_evidence["scene"]["units"] == "meters"
    assert context.public_evidence["fixture_kind"] is None
    assert len(context.public_files) == 6
    serialized = canonical_json_bytes(context.public_evidence)
    redacted = canonical_json_bytes(context.redacted_human_review)
    for secret in (
        b"Reviewer One",
        b"C:\\",
        b"/home/",
        b"ssh",
        b"control-points",
    ):
        assert secret not in serialized
        assert secret not in redacted


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("internal", "source role"),
        ("gate", "gates"),
        ("release", "release"),
        ("report", "report SHA"),
    ),
)
def test_acceptance_projection_rejects_untrusted_decision(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
    message: str,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    decision = fixture["decision"]
    if mutation == "internal":
        decision = decision.model_copy(
            update={"source_role": "internal-canary"}
        )
    elif mutation == "gate":
        rejected = decision.gates[0].model_copy(
            update={"state": "rejected", "reasons": ("failed",)}
        )
        decision = decision.model_copy(
            update={"gates": (rejected, *decision.gates[1:])}
        )
    elif mutation == "release":
        decision = decision.model_copy(
            update={"production_release_allowed": False}
        )
    else:
        decision = decision.model_copy(
            update={"report_sha256": "f" * 64}
        )
    fixture["decision"] = decision
    _patch_outer_validators(monkeypatch, fixture)

    with pytest.raises(ProductionReleaseBuilderError, match=message):
        derive_production_release_context(fixture["report_path"])


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_role", "internal-canary"),
        ("training_quality_role", "preview-only"),
        ("target_units", "arbitrary"),
        ("geometry_usability", "preview-only"),
        ("alignment_rms_m", None),
    ),
)
def test_acceptance_projection_rejects_import_trust_drift(
    tmp_path: Path,
    monkeypatch,
    field: str,
    value: object,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    fixture["import_receipt"] = fixture["import_receipt"].model_copy(
        update={field: value}
    )
    _patch_outer_validators(monkeypatch, fixture)

    with pytest.raises(ProductionReleaseBuilderError, match="differs"):
        derive_production_release_context(fixture["report_path"])


def test_acceptance_projection_rejects_viewer_v1(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    report = fixture["report"]
    payload = canonical_viewer_performance_report_bytes(viewer_report_v1())
    _write(fixture["root"], report.viewer_report.path, payload)
    changed_reference = _reference(
        fixture["root"],
        report.viewer_report.path,
    )
    changed_report = report.model_copy(
        update={"viewer_report": changed_reference}
    )
    fixture["report_path"].write_bytes(
        canonical_real_scene_acceptance_bytes(changed_report)
    )
    fixture["decision"] = fixture["decision"].model_copy(
        update={
            "report_sha256": hashlib.sha256(
                fixture["report_path"].read_bytes()
            ).hexdigest()
        }
    )
    _patch_outer_validators(monkeypatch, fixture)

    with pytest.raises(ProductionReleaseBuilderError, match="Viewer v2"):
        derive_production_release_context(fixture["report_path"])


def test_acceptance_projection_rejects_rights_without_release_inclusion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    report = fixture["report"]
    rights = CaptureRightsReceipt(
        schema="nantai.capture-rights-receipt.v1",
        dataset_id="mountain-village-private",
        operator="Reviewer One",
        capture_scope="private",
        effective_date=date(2026, 7, 28),
        processing_purposes=("3d-reconstruction",),
        redistribution_allowed=True,
        release_inclusion_allowed=False,
    )
    _write(
        fixture["root"],
        report.rights_receipt.path,
        canonical_model_bytes(rights),
    )
    changed_report = report.model_copy(
        update={
            "rights_receipt": _reference(
                fixture["root"],
                report.rights_receipt.path,
            )
        }
    )
    fixture["report_path"].write_bytes(
        canonical_real_scene_acceptance_bytes(changed_report)
    )
    fixture["decision"] = fixture["decision"].model_copy(
        update={
            "report_sha256": hashlib.sha256(
                fixture["report_path"].read_bytes()
            ).hexdigest()
        }
    )
    _patch_outer_validators(monkeypatch, fixture)

    with pytest.raises(ProductionReleaseBuilderError, match="rights"):
        derive_production_release_context(fixture["report_path"])


def test_acceptance_projection_rejects_evidence_changed_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)

    def accept(_path: Path) -> AcceptanceDecision:
        policy = (
            fixture["root"]
            / fixture["report"].viewer_policy.path
        )
        policy.write_bytes(policy.read_bytes() + b" ")
        return fixture["decision"]

    monkeypatch.setattr(
        builder_module,
        "validate_real_scene_acceptance",
        accept,
    )
    monkeypatch.setattr(
        builder_module,
        "validate_real_scene_import_receipt",
        lambda *_args: fixture["import_receipt"],
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="changed|SHA|length",
    ):
        derive_production_release_context(fixture["report_path"])
