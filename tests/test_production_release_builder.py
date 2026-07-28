from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

import pipeline.production_release_builder as builder_module
from pipeline.production_release_builder import (
    ProductionReleaseBuilderError,
    build_production_release_archive,
    derive_production_release_context,
    resolve_runtime_scene_payloads,
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
    root.mkdir(parents=True)
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


def _resign_import_fixture(
    fixture: dict[str, object],
    payloads: dict[str, bytes],
) -> None:
    import_root = fixture["root"] / "imported"
    existing = {
        artifact.path: artifact
        for artifact in fixture["import_receipt"].artifacts
    }
    for relative, payload in payloads.items():
        _write(import_root, relative, payload)
        existing[relative] = ImportArtifactBinding(
            path=relative,
            byte_length=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
    receipt = fixture["import_receipt"].model_copy(
        update={
            "artifacts": tuple(
                existing[path] for path in sorted(existing)
            )
        }
    )
    receipt_payload = canonical_model_bytes(receipt)
    _write(import_root, "import-receipt.json", receipt_payload)
    report = fixture["report"].model_copy(
        update={
            "import_receipt": _reference(
                fixture["root"],
                "imported/import-receipt.json",
            )
        }
    )
    fixture["report_path"].write_bytes(
        canonical_real_scene_acceptance_bytes(report)
    )
    fixture["report"] = report
    fixture["import_receipt"] = receipt
    fixture["decision"] = fixture["decision"].model_copy(
        update={
            "report_sha256": hashlib.sha256(
                fixture["report_path"].read_bytes()
            ).hexdigest()
        }
    )


def _scene_context(
    tmp_path: Path,
    monkeypatch,
):
    fixture = _modeled_acceptance_tree(tmp_path)
    full = b"full-3dgs\n"
    chunk = b"chunk\n"
    chunks = canonical_json_bytes(
        {
            "chunks": [
                {
                    "payloads": {
                        "2": {
                            "file": "chunk-0.ply",
                            "sha256": hashlib.sha256(chunk).hexdigest(),
                            "size_bytes": len(chunk),
                        }
                    }
                }
            ]
        }
    )
    manifest = canonical_json_bytes(
        {
            "artifacts": {
                "full_3dgs": {
                    "path": "scene.ply",
                    "sha256": hashlib.sha256(full).hexdigest(),
                    "bytes": len(full),
                },
                "chunks": {
                    "manifest": "chunks/chunks.json",
                    "sha256": hashlib.sha256(chunks).hexdigest(),
                    "bytes": len(chunks),
                },
            }
        }
    )
    _resign_import_fixture(
        fixture,
        {
            "web/recon_manifest.json": manifest,
            "web/chunks/chunks.json": chunks,
            "web/chunks/chunk-0.ply": chunk,
            "web/scene.ply": full,
            "web/unreferenced.bin": b"private-unreferenced\n",
        },
    )
    _patch_outer_validators(monkeypatch, fixture)
    context = derive_production_release_context(
        fixture["report_path"]
    )
    return fixture, context


def test_scene_resolver_maps_only_manifest_bound_runtime_payloads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _fixture, context = _scene_context(tmp_path, monkeypatch)

    payloads = resolve_runtime_scene_payloads(context)

    assert tuple(row.destination_path for row in payloads) == (
        "web/data/recon/chunks/chunk-0.ply",
        "web/data/recon/chunks/chunks.json",
        "web/data/recon/recon_manifest.json",
        "web/data/recon/scene.ply",
    )
    serialized = "\n".join(row.destination_path for row in payloads)
    for forbidden in (
        "source.ply",
        "normalized.ply",
        "control-points",
        "registration",
        "training",
        "unreferenced",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("failure", ("escape", "missing", "drift", "duplicate"))
def test_scene_resolver_rejects_unclosed_manifest_payloads(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    fixture, context = _scene_context(tmp_path, monkeypatch)
    manifest_path = fixture["root"] / "imported/web/recon_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    full = manifest["artifacts"]["full_3dgs"]
    if failure == "escape":
        full["path"] = "../source.ply"
    elif failure == "missing":
        full["path"] = "missing.ply"
    elif failure == "drift":
        (fixture["root"] / "imported/web/scene.ply").write_bytes(b"changed\n")
    else:
        manifest["artifacts"]["duplicate"] = dict(full)
        manifest["artifacts"]["duplicate"]["sha256"] = "f" * 64
    if failure != "drift":
        changed = canonical_json_bytes(manifest)
        manifest_path.write_bytes(changed)
        artifacts = []
        for artifact in context.import_receipt.artifacts:
            if artifact.path == "web/recon_manifest.json":
                artifact = artifact.model_copy(
                    update={
                        "byte_length": len(changed),
                        "sha256": hashlib.sha256(changed).hexdigest(),
                    }
                )
            artifacts.append(artifact)
        context = context.__class__(
            **{
                **context.__dict__,
                "import_receipt": context.import_receipt.model_copy(
                    update={"artifacts": tuple(artifacts)}
                ),
            }
        )

    with pytest.raises(ProductionReleaseBuilderError):
        resolve_runtime_scene_payloads(context)


def _runtime_repo(root: Path) -> tuple[str, ...]:
    payloads = {
        "LICENSE": b"license\n",
        "make.py": b"raise SystemExit('development runner leaked')\n",
        "pyproject.toml": b"[project]\nname='nantai'\n",
        "pipeline/release_archive.py": (
            Path(builder_module.__file__).with_name(
                "release_archive.py"
            ).read_bytes()
        ),
        "pipeline/production_release_contract.py": (
            Path(builder_module.__file__).with_name(
                "production_release_contract.py"
            ).read_bytes()
        ),
        "pipeline/production_release_verifier.py": (
            Path(builder_module.__file__).with_name(
                "production_release_verifier.py"
            ).read_bytes()
        ),
        "scripts/verify_production_release.py": (
            Path(__file__).parents[1]
            .joinpath("scripts/verify_production_release.py")
            .read_bytes()
        ),
        "web/studio/index.html": b"<h1>Studio</h1>\n",
        "web/viewer/index.html": b"<h1>Viewer</h1>\n",
        "release/production-verify-and-run.md": b"# Verify\n",
        "release/production-runtime-runner.py": (
            Path(__file__).parents[1]
            .joinpath("release/production-runtime-runner.py")
            .read_bytes()
        ),
    }
    for relative, payload in payloads.items():
        _write(root, relative, payload)
    return tuple(sorted(payloads))


def test_runtime_sources_replace_development_runner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)

    payloads = builder_module._runtime_source_payloads(repo, tracked)
    runner = next(row for row in payloads if row.destination_path == "make.py")

    assert runner.role == "runtime-runner"
    assert runner.source_path == (
        repo / "release/production-runtime-runner.py"
    )
    assert not any(row.source_path == repo / "make.py" for row in payloads)


def test_clean_source_gate_tracks_template_not_development_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(builder_module.subprocess, "run", fake_run)

    builder_module._ensure_release_sources_clean(tmp_path, ())

    command = observed["command"]
    assert "release/production-runtime-runner.py" in command
    assert "make.py" not in command
    assert observed["cwd"] == tmp_path


def test_build_is_deterministic_verified_and_no_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)
    monkeypatch.setattr(
        builder_module,
        "load_latest_real_scene_acceptance",
        lambda _root: fixture["report_path"],
    )
    monkeypatch.setattr(
        builder_module,
        "derive_production_release_context",
        lambda _path: context,
    )
    monkeypatch.setattr(
        builder_module,
        "_ensure_release_sources_clean",
        lambda *_args: None,
    )
    first_path = tmp_path / "one/runtime.zip"
    second_path = tmp_path / "two/runtime.zip"
    first_path.parent.mkdir()
    second_path.parent.mkdir()

    first = build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=first_path,
        version="v1.0.0",
        source_commit="a" * 40,
        tracked_files=tracked,
    )
    second = build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=second_path,
        version="v1.0.0",
        source_commit="a" * 40,
        tracked_files=tracked,
    )

    assert first.package_content_id == second.package_content_id
    assert first.archive_sha256 == second.archive_sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    assert (
        first_path.with_suffix(".zip.sha256").read_text(encoding="ascii")
        == second_path.with_suffix(".zip.sha256").read_text(encoding="ascii")
    )
    with zipfile.ZipFile(first_path) as archive:
        infos = archive.infolist()
        packaged_runner = archive.read("nantai-3d-v1.0.0/make.py")
        receipt = json.loads(
            archive.read("nantai-3d-v1.0.0/PRODUCTION-RELEASE.json")
        )
    assert packaged_runner == (
        repo / "release/production-runtime-runner.py"
    ).read_bytes()
    assert b"development runner leaked" not in packaged_runner
    runner_artifact = next(
        row for row in receipt["artifacts"] if row["path"] == "make.py"
    )
    assert runner_artifact["role"] == "runtime-runner"
    assert runner_artifact["sha256"] == hashlib.sha256(
        packaged_runner
    ).hexdigest()
    assert [info.filename for info in infos] == sorted(
        info.filename for info in infos
    )
    assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
    assert all(
        stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG
        for info in infos
    )

    with pytest.raises(ProductionReleaseBuilderError, match="exists"):
        build_production_release_archive(
            repo_root=repo,
            acceptance_root=fixture["root"],
            output_path=first_path,
            version="v1.0.0",
            source_commit="a" * 40,
            tracked_files=tracked,
        )


def test_build_failure_removes_partial_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)
    output = tmp_path / "runtime.zip"
    monkeypatch.setattr(
        builder_module,
        "load_latest_real_scene_acceptance",
        lambda _root: fixture["report_path"],
    )
    monkeypatch.setattr(
        builder_module,
        "derive_production_release_context",
        lambda _path: context,
    )
    monkeypatch.setattr(
        builder_module,
        "_ensure_release_sources_clean",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        builder_module,
        "verify_production_release_archive",
        lambda _path: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(ProductionReleaseBuilderError, match="injected"):
        build_production_release_archive(
            repo_root=repo,
            acceptance_root=fixture["root"],
            output_path=output,
            version="v1.0.0",
            source_commit="a" * 40,
            tracked_files=tracked,
        )
    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()
    assert not tuple(tmp_path.glob(".*.staging"))
    assert not tuple(tmp_path.glob(".*.partial"))


def test_fresh_runtime_runner_verification_is_repeatable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = _runtime_repo(repo)
    monkeypatch.setattr(
        builder_module,
        "load_latest_real_scene_acceptance",
        lambda _root: fixture["report_path"],
    )
    monkeypatch.setattr(
        builder_module,
        "derive_production_release_context",
        lambda _path: context,
    )
    monkeypatch.setattr(
        builder_module,
        "_ensure_release_sources_clean",
        lambda *_args: None,
    )
    archive_path = tmp_path / "runtime.zip"
    build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=archive_path,
        version="v1.0.0",
        source_commit="a" * 40,
        tracked_files=tracked,
    )
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)
    package_root = extracted / "nantai-3d-v1.0.0"

    for _attempt in range(2):
        completed = subprocess.run(
            [
                sys.executable,
                "make.py",
                "verify",
            ],
            cwd=package_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["valid"] is True
    assert not tuple(package_root.rglob("__pycache__"))
