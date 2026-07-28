from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.production_release_builder as builder_module
from pipeline.production_release_builder import (
    ProductionReleaseBuilderError,
    build_production_release_archive,
    derive_production_release_context,
    resolve_runtime_scene_payloads,
)
from pipeline.production_release_contract import CHECKSUMS_NAME
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


def _linux_mutation_only(test):
    marked = pytest.mark.production_mutation(test)
    return pytest.mark.skipif(
        sys.platform != "linux",
        reason="Production release mutation is Linux-only",
    )(marked)


LINUX_MUTATION_ONLY = _linux_mutation_only


def test_source_identity_resolves_exact_head_and_tracked_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], Path, dict[str, object]]] = []

    def run(command, *, cwd, **kwargs):
        calls.append((command, cwd, kwargs))
        if command[2:] == ["rev-parse", "--verify", "HEAD"]:
            return SimpleNamespace(
                returncode=0,
                stdout=b"a" * 40 + b"\n",
                stderr=b"",
            )
        if command[2:] == ["ls-files", "-z", "--"]:
            return SimpleNamespace(
                returncode=0,
                stdout=b"web/z.js\0LICENSE\0web/a.js\0",
                stderr=b"",
            )
        if command[2:] == [
            "for-each-ref",
            "--format=%(refname)",
            "refs/replace",
        ]:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise AssertionError(command)

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    identity = builder_module.resolve_production_release_source_identity(
        tmp_path
    )

    assert identity.source_commit == "a" * 40
    assert identity.tracked_files == (
        "LICENSE",
        "web/a.js",
        "web/z.js",
    )
    assert [command for command, _cwd, _kwargs in calls] == [
        ["git", "--no-replace-objects", "rev-parse", "--verify", "HEAD"],
        ["git", "--no-replace-objects", "ls-files", "-z", "--"],
        [
            "git",
            "--no-replace-objects",
            "for-each-ref",
            "--format=%(refname)",
            "refs/replace",
        ],
    ]
    assert all(cwd == tmp_path.absolute() for _command, cwd, _kwargs in calls)
    assert all(
        kwargs["capture_output"] is True
        and kwargs["text"] is False
        and kwargs["check"] is False
        and kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
        for _command, _cwd, kwargs in calls
    )


def test_source_identity_hides_nonzero_git_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=b"private output",
            stderr=b"private stderr",
        )

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="rev-parse",
    ) as captured:
        builder_module.resolve_production_release_source_identity(tmp_path)

    assert "private" not in str(captured.value)


def test_source_identity_wraps_git_launch_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def run(*_args, **_kwargs):
        raise FileNotFoundError("git executable")

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="rev-parse",
    ) as captured:
        builder_module.resolve_production_release_source_identity(tmp_path)

    assert isinstance(captured.value.__cause__, FileNotFoundError)


def test_source_identity_wraps_nonbytes_git_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="private path from invalid runner",
            stderr="private stderr",
        )

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="rev-parse",
    ) as captured:
        builder_module.resolve_production_release_source_identity(tmp_path)

    assert "private" not in str(captured.value)
    assert isinstance(captured.value.__cause__, TypeError)


@pytest.mark.parametrize(
    "commit",
    (
        b"A" * 40 + b"\n",
        b"a" * 39 + b"\n",
        b"\xff" * 40 + b"\n",
    ),
)
def test_source_identity_rejects_noncanonical_commit_bytes(
    tmp_path: Path,
    monkeypatch,
    commit: bytes,
) -> None:
    def run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=commit, stderr=b"")

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    with pytest.raises(ProductionReleaseBuilderError, match="rev-parse"):
        builder_module.resolve_production_release_source_identity(tmp_path)


def test_source_identity_rejects_empty_tracked_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def run(command, **_kwargs):
        if command[2] == "rev-parse":
            return SimpleNamespace(
                returncode=0,
                stdout=b"a" * 40 + b"\n",
                stderr=b"",
            )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    with pytest.raises(ProductionReleaseBuilderError, match="ls-files"):
        builder_module.resolve_production_release_source_identity(tmp_path)


def test_source_identity_decodes_tracked_path_bytes_reversibly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def run(command, **_kwargs):
        if command[2] == "rev-parse":
            return SimpleNamespace(
                returncode=0,
                stdout=b"a" * 40 + b"\n",
                stderr=b"",
            )
        if command[2] == "for-each-ref":
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return SimpleNamespace(
            returncode=0,
            stdout=b"web/\xff.js\0LICENSE\0",
            stderr=b"",
        )

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    identity = builder_module.resolve_production_release_source_identity(tmp_path)

    assert identity.tracked_files == ("LICENSE", "web/\udcff.js")


def test_git_tree_lookup_disables_replacement_objects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}
    object_id = "b" * 40

    def run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"100644 blob {object_id}\tpipeline/runtime.py\0"
            ).encode("ascii"),
            stderr=b"",
        )

    monkeypatch.setattr(builder_module.subprocess, "run", run)

    resolved = builder_module._git_tree_blob(
        tmp_path,
        source_commit="a" * 40,
        relative="pipeline/runtime.py",
    )

    assert resolved == object_id
    assert observed["command"] == [
        "git",
        "--no-replace-objects",
        "ls-tree",
        "-z",
        "a" * 40,
        "--",
        "pipeline/runtime.py",
    ]
    assert observed["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"


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


def test_acceptance_projection_rejects_reparse_root_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    _patch_outer_validators(monkeypatch, fixture)
    root = fixture["root"]
    observed = root.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == root:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=getattr(
                    stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x400,
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="root is unavailable or unsafe",
    ):
        derive_production_release_context(fixture["report_path"])


def test_import_tree_rejects_reparse_subtree_before_legacy_validator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    _patch_outer_validators(monkeypatch, fixture)
    redirected = fixture["root"] / "imported" / "redirected"
    redirected.mkdir()
    observed = redirected.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == redirected:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    def legacy_validator_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy validator traversed an unsafe import tree")

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )
    monkeypatch.setattr(
        builder_module,
        "validate_real_scene_import_receipt",
        legacy_validator_must_not_run,
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="import root.*unsafe",
    ):
        derive_production_release_context(fixture["report_path"])


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_import_tree_rejects_real_windows_junction_before_legacy_validator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _modeled_acceptance_tree(tmp_path)
    _patch_outer_validators(monkeypatch, fixture)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = fixture["root"] / "imported" / "redirected"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(redirected), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")

    def legacy_validator_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy validator traversed an unsafe import tree")

    try:
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda _path: False,
            raising=False,
        )
        monkeypatch.setattr(
            builder_module,
            "validate_real_scene_import_receipt",
            legacy_validator_must_not_run,
        )

        with pytest.raises(
            ProductionReleaseBuilderError,
            match="import root.*unsafe",
        ):
            derive_production_release_context(fixture["report_path"])
    finally:
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(redirected)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert removed.returncode == 0, removed.stderr


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
        "pipeline/production_release_fs.py": (
            Path(builder_module.__file__).with_name(
                "production_release_fs.py"
            ).read_bytes()
        ),
        "pipeline/durable_io.py": (
            Path(builder_module.__file__).with_name(
                "durable_io.py"
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


def _git(repo: Path, *arguments: str, input_bytes: bytes | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        input=input_bytes,
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", "replace"
    )
    return completed.stdout.decode("ascii").strip()


def _committed_runtime_repo(
    root: Path,
    *,
    extra_files: dict[str, bytes] | None = None,
) -> builder_module.ProductionReleaseSourceIdentity:
    root.mkdir()
    _runtime_repo(root)
    for relative, payload in (extra_files or {}).items():
        _write(root, relative, payload)
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Production Release Tests")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.symlinks", "false")
    _git(root, "add", "--all", "--")
    _git(root, "commit", "--quiet", "-m", "fixture")
    identity = builder_module.resolve_production_release_source_identity(root)
    assert not _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    return identity


def _patch_build_context(
    monkeypatch,
    fixture: dict[str, object],
    context: builder_module.ProductionReleaseContext,
) -> None:
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


def _build_committed_runtime(
    *,
    repo: Path,
    fixture: dict[str, object],
    identity: builder_module.ProductionReleaseSourceIdentity,
    output: Path,
    source_commit: str | None = None,
    tracked_files: tuple[str, ...] | None = None,
):
    return build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=output,
        version="v1.0.0",
        source_commit=source_commit or identity.source_commit,
        tracked_files=(
            identity.tracked_files
            if tracked_files is None
            else tracked_files
        ),
    )


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


def test_archive_member_streaming_keeps_open_sources_bounded() -> None:
    active = 0
    maximum = 0

    class CountingBytesIO(io.BytesIO):
        def __enter__(self):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            return self

        def __exit__(self, *_args):
            nonlocal active
            active -= 1
            self.close()

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for index in range(1101):
            with CountingBytesIO(b"x") as source:
                builder_module._write_archive_member(
                    archive,
                    wrapper="runtime",
                    relative=f"files/{index:04d}.bin",
                    source=source,
                    expected_bytes=1,
                    expected_sha256=hashlib.sha256(b"x").hexdigest(),
                )

    assert maximum == 1
    assert active == 0


@pytest.mark.parametrize("dirty_kind", ("tracked", "untracked"))
@LINUX_MUTATION_ONLY
def test_build_rejects_real_dirty_release_owned_source_before_staging(
    tmp_path: Path,
    monkeypatch,
    dirty_kind: str,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    _patch_build_context(monkeypatch, fixture, context)
    if dirty_kind == "tracked":
        (repo / "web/viewer/index.html").write_bytes(b"dirty tracked bytes\n")
    else:
        _write(repo, "pipeline/untracked_release_source.py", b"dirty\n")
    output = tmp_path / "runtime.zip"

    with pytest.raises(ProductionReleaseBuilderError, match="dirty"):
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


def test_output_parent_validation_rejects_lexical_mismatch_before_capability_use(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    output = (tmp_path / "expected" / "runtime.zip").absolute()
    parent = SimpleNamespace(
        path=(tmp_path / "wrong").absolute(),
        verify_lexical_identity=lambda: calls.append("verify"),
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="output parent.*does not match",
    ) as raised:
        builder_module._validate_output_parent(output, parent)

    assert calls == []
    assert raised.value.published == ()
    assert raised.value.retained == ()


def test_output_parent_validation_checks_matching_capability_identity(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    parent_path = (tmp_path / "expected").absolute()
    parent = SimpleNamespace(
        path=parent_path,
        verify_lexical_identity=lambda: calls.append("verify"),
    )

    builder_module._validate_output_parent(
        parent_path / "runtime.zip",
        parent,
    )

    assert calls == ["verify"]


@LINUX_MUTATION_ONLY
def test_build_rejects_mismatched_bound_output_parent_before_mutation(
    tmp_path: Path,
) -> None:
    expected_parent = tmp_path / "expected"
    wrong_parent = tmp_path / "wrong"
    expected_parent.mkdir()
    wrong_parent.mkdir()
    output = expected_parent / "runtime.zip"

    with builder_module.open_bound_directory(wrong_parent) as bound:
        with pytest.raises(
            ProductionReleaseBuilderError,
            match="output parent.*does not match",
        ):
            build_production_release_archive(
                repo_root=tmp_path,
                acceptance_root=tmp_path,
                output_path=output,
                version="v1.0.0",
                source_commit="a" * 40,
                tracked_files=(),
                output_parent=bound,
            )

    assert not output.exists()
    assert not (wrong_parent / output.name).exists()


@pytest.mark.parametrize("identity_kind", ("commit", "tracked-files"))
@LINUX_MUTATION_ONLY
def test_build_rejects_supplied_identity_that_is_not_exact_live_identity(
    tmp_path: Path,
    monkeypatch,
    identity_kind: str,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(
        repo,
        extra_files={"notes.txt": b"tracked but not packaged\n"},
    )
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"
    source_commit = (
        "b" * 40
        if identity_kind == "commit"
        else identity.source_commit
    )
    tracked_files = (
        tuple(path for path in identity.tracked_files if path != "notes.txt")
        if identity_kind == "tracked-files"
        else identity.tracked_files
    )

    with pytest.raises(ProductionReleaseBuilderError, match="identity"):
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
            source_commit=source_commit,
            tracked_files=tracked_files,
        )

    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


@LINUX_MUTATION_ONLY
def test_build_rejects_dirty_edit_injected_after_initial_clean_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    output = tmp_path / "runtime.zip"
    runtime_runner = repo / "release/production-runtime-runner.py"
    monkeypatch.setattr(
        builder_module,
        "load_latest_real_scene_acceptance",
        lambda _root: fixture["report_path"],
    )

    def mutate_after_initial_clean(_path):
        runtime_runner.write_bytes(b"transient attacker-controlled runner\n")
        return context

    monkeypatch.setattr(
        builder_module,
        "derive_production_release_context",
        mutate_after_initial_clean,
    )

    with pytest.raises(ProductionReleaseBuilderError, match="dirty"):
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert runtime_runner.read_bytes() == b"transient attacker-controlled runner\n"
    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


@LINUX_MUTATION_ONLY
def test_build_rechecks_identity_after_final_cleanliness_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(
        repo,
        extra_files={"notes.txt": b"initial notes\n"},
    )
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"
    real_clean = builder_module._ensure_release_sources_clean
    clean_calls = 0

    def advance_head_after_final_clean(repo_root, tracked_files):
        nonlocal clean_calls
        clean_calls += 1
        real_clean(repo_root, tracked_files)
        if clean_calls == 2:
            (repo / "notes.txt").write_bytes(b"new clean commit\n")
            _git(repo, "add", "--", "notes.txt")
            _git(repo, "commit", "--quiet", "-m", "advance head")

    monkeypatch.setattr(
        builder_module,
        "_ensure_release_sources_clean",
        advance_head_after_final_clean,
    )

    with pytest.raises(ProductionReleaseBuilderError, match="identity changed"):
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert clean_calls == 2
    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


@LINUX_MUTATION_ONLY
def test_transient_worktree_edit_cannot_enter_archive_under_original_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"
    runtime_runner = repo / "release/production-runtime-runner.py"
    committed_bytes = runtime_runner.read_bytes()
    transient_bytes = bytes([committed_bytes[0] ^ 0x01]) + committed_bytes[1:]
    real_write = builder_module._write_git_archive_member

    def write_during_transient_edit(*args, **kwargs):
        if kwargs["destination"] == "make.py":
            runtime_runner.write_bytes(transient_bytes)
        try:
            return real_write(*args, **kwargs)
        finally:
            if kwargs["destination"] == "make.py":
                runtime_runner.write_bytes(committed_bytes)

    monkeypatch.setattr(
        builder_module,
        "_write_git_archive_member",
        write_during_transient_edit,
    )
    try:
        result = _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )
    finally:
        if runtime_runner.read_bytes() != committed_bytes:
            runtime_runner.write_bytes(committed_bytes)

    with zipfile.ZipFile(output) as archive:
        packaged_runner = archive.read("nantai-3d-v1.0.0/make.py")
        receipt = json.loads(
            archive.read("nantai-3d-v1.0.0/PRODUCTION-RELEASE.json")
        )
    runner_artifact = next(
        artifact
        for artifact in receipt["artifacts"]
        if artifact["path"] == "make.py"
    )
    assert result.archive_path == output
    assert packaged_runner == committed_bytes
    assert packaged_runner != transient_bytes
    assert receipt["source"]["git_commit"] == identity.source_commit
    assert runner_artifact["sha256"] == hashlib.sha256(
        committed_bytes
    ).hexdigest()


@LINUX_MUTATION_ONLY
def test_build_rejects_git_replacement_commit_before_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    trusted_runner = (
        repo / "release/production-runtime-runner.py"
    ).read_bytes()
    replacement_runner = b"raise SystemExit('replacement object leaked')\n"
    (repo / "release/production-runtime-runner.py").write_bytes(
        replacement_runner
    )
    _git(
        repo,
        "add",
        "--",
        "release/production-runtime-runner.py",
    )
    _git(repo, "commit", "--quiet", "-m", "replacement payload")
    replacement_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--quiet", "--detach", identity.source_commit)
    _git(repo, "replace", identity.source_commit, replacement_commit)
    assert _git(repo, "rev-parse", "HEAD") == identity.source_commit
    assert (
        _git(
            repo,
            "show",
            f"{identity.source_commit}:release/production-runtime-runner.py",
        ).encode("ascii")
        == replacement_runner.rstrip(b"\n")
    )
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="replacement refs",
    ):
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert trusted_runner != replacement_runner
    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


@LINUX_MUTATION_ONLY
def test_build_rejects_git_tree_symlink_even_if_worktree_path_is_regular(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    _committed_runtime_repo(repo)
    link_path = repo / "pipeline/runtime_link.py"
    link_payload = b"runtime-target.py"
    link_path.write_bytes(link_payload)
    object_id = _git(repo, "hash-object", "-w", "--stdin", input_bytes=link_payload)
    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{object_id},pipeline/runtime_link.py",
    )
    _git(repo, "commit", "--quiet", "-m", "track symlink mode")
    identity = builder_module.resolve_production_release_source_identity(repo)
    assert not _git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="regular|mode|blob",
    ):
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


@LINUX_MUTATION_ONLY
def test_streaming_git_failure_retains_partial_archive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"
    real_popen = builder_module.subprocess.Popen

    def fail_git_blob_stream(command, *args, **kwargs):
        if command[:4] == [
            "git",
            "--no-replace-objects",
            "cat-file",
            "blob",
        ]:
            assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
            raise FileNotFoundError("private git executable path")
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(builder_module.subprocess, "Popen", fail_git_blob_stream)

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="Git.*blob|blob.*failed",
    ) as captured:
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert "private git executable path" not in str(captured.value)
    assert isinstance(captured.value.__cause__, ProductionReleaseBuilderError)
    assert isinstance(captured.value.__cause__.__cause__, FileNotFoundError)
    assert output.exists()
    assert not output.with_suffix(".zip.sha256").exists()
    assert captured.value.published == ("runtime.zip",)
    assert "runtime.zip" in captured.value.retained


def test_clean_source_gate_tracks_template_not_development_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(builder_module.subprocess, "run", fake_run)

    builder_module._ensure_release_sources_clean(tmp_path, ())

    command = observed["command"]
    assert command[:3] == [
        "git",
        "--no-replace-objects",
        "status",
    ]
    assert "release/production-runtime-runner.py" in command
    assert "make.py" not in command
    assert observed["cwd"] == tmp_path
    assert observed["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_git_blob_stream_failure_always_kills_and_reaps_child(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"payload")
            self.killed = 0
            self.waited = 0

        def poll(self):
            return None

        def kill(self) -> None:
            self.killed += 1

        def wait(self) -> int:
            self.waited += 1
            return 0

    process = Process()
    monkeypatch.setattr(
        builder_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        builder_module,
        "_write_archive_member",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected stream failure")
        ),
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="blob streaming failed",
    ) as raised:
        builder_module._write_git_archive_member(
            SimpleNamespace(),
            wrapper="root",
            repo_root=tmp_path,
            object_id="a" * 40,
            destination="payload.bin",
            expected_bytes=7,
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert process.killed == 1
    assert process.waited == 1


@LINUX_MUTATION_ONLY
def test_build_is_deterministic_verified_and_no_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
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
    first_path = tmp_path / "one/runtime.zip"
    second_path = tmp_path / "two/runtime.zip"
    first_path.parent.mkdir()
    second_path.parent.mkdir()

    first = build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=first_path,
        version="v1.0.0",
        source_commit=identity.source_commit,
        tracked_files=identity.tracked_files,
    )
    second = build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=second_path,
        version="v1.0.0",
        source_commit=identity.source_commit,
        tracked_files=identity.tracked_files,
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
    names = [info.filename for info in infos]
    assert names[:-2] == sorted(names[:-2])
    assert names[-2:] == [
        "nantai-3d-v1.0.0/PRODUCTION-RELEASE.json",
        f"nantai-3d-v1.0.0/{CHECKSUMS_NAME}",
    ]
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
            source_commit=identity.source_commit,
            tracked_files=identity.tracked_files,
        )


@LINUX_MUTATION_ONLY
def test_build_failure_retains_partial_archive_without_commit_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
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
        "verify_production_release_archive_stream",
        lambda _stream: (_ for _ in ()).throw(RuntimeError("injected")),
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="injected.*published=.*retained=",
    ) as raised:
        build_production_release_archive(
            repo_root=repo,
            acceptance_root=fixture["root"],
            output_path=output,
            version="v1.0.0",
            source_commit=identity.source_commit,
            tracked_files=identity.tracked_files,
        )
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert output.exists()
    assert not output.with_suffix(".zip.sha256").exists()


@LINUX_MUTATION_ONLY
def test_build_success_close_failure_is_domain_error_and_closes_all_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"
    close_calls: list[str] = []
    original_close = builder_module.BoundFile.close

    def close_with_injected_sidecar_failure(bound) -> None:
        close_calls.append(bound.name)
        original_close(bound)
        if bound.name.endswith(".sha256"):
            raise builder_module.ProductionReleaseMutationError(
                "injected sidecar close failure",
                published=(bound.name,),
                retained=(bound.name,),
            )

    monkeypatch.setattr(
        builder_module.BoundFile,
        "close",
        close_with_injected_sidecar_failure,
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="capabilities failed to close",
    ) as raised:
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert close_calls[-2:] == ["runtime.zip.sha256", "runtime.zip"]
    assert raised.value.published == ("runtime.zip", "runtime.zip.sha256")
    assert raised.value.retained == ("runtime.zip", "runtime.zip.sha256")
    assert isinstance(
        raised.value.__cause__,
        builder_module.ProductionReleaseMutationError,
    )


@LINUX_MUTATION_ONLY
def test_build_body_error_wins_over_close_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
    _patch_build_context(monkeypatch, fixture, context)
    output = tmp_path / "runtime.zip"
    body_error = RuntimeError("injected body failure")
    monkeypatch.setattr(
        builder_module,
        "verify_production_release_archive_stream",
        lambda _stream: (_ for _ in ()).throw(body_error),
    )
    close_calls: list[str] = []
    original_close = builder_module.BoundFile.close

    def close_with_injected_archive_failure(bound) -> None:
        close_calls.append(bound.name)
        original_close(bound)
        raise builder_module.ProductionReleaseMutationError(
            "injected archive close failure",
            published=(bound.name,),
            retained=(bound.name,),
        )

    monkeypatch.setattr(
        builder_module.BoundFile,
        "close",
        close_with_injected_archive_failure,
    )

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="injected body failure",
    ) as raised:
        _build_committed_runtime(
            repo=repo,
            fixture=fixture,
            identity=identity,
            output=output,
        )

    assert close_calls[-1:] == ["runtime.zip"]
    assert raised.value.__cause__ is body_error
    assert raised.value.published == ("runtime.zip",)
    assert raised.value.retained == ("runtime.zip",)


@LINUX_MUTATION_ONLY
def test_fresh_runtime_runner_verification_is_repeatable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture, context = _scene_context(tmp_path / "private", monkeypatch)
    repo = tmp_path / "repo"
    identity = _committed_runtime_repo(repo)
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
    archive_path = tmp_path / "runtime.zip"
    build_production_release_archive(
        repo_root=repo,
        acceptance_root=fixture["root"],
        output_path=archive_path,
        version="v1.0.0",
        source_commit=identity.source_commit,
        tracked_files=identity.tracked_files,
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
@pytest.mark.skipif(
    sys.platform == "linux",
    reason="non-Linux platform contract",
)
def test_build_rejects_non_linux_before_any_output_creation(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runtime.zip"

    with pytest.raises(
        ProductionReleaseBuilderError,
        match="private Linux builder",
    ):
        build_production_release_archive(
            repo_root=tmp_path,
            acceptance_root=tmp_path,
            output_path=output,
            version="v1.0.0",
            source_commit="a" * 40,
            tracked_files=(),
        )

    assert not output.exists()
    assert not output.with_suffix(".zip.sha256").exists()
