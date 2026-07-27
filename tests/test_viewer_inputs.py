from __future__ import annotations

import hashlib
import http.client
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import pipeline.real_scene_import as import_module
import pipeline.viewer_session as session_module
from pipeline.real_dataset import (
    LocalCaptureSource,
    canonical_model_bytes,
)
from pipeline.real_scene_import import import_real_scene
from pipeline.real_scene_runner import (
    StageArtifactBinding,
    StageReceipt,
    canonical_stage_receipt_bytes,
    resolve_latest_production_import,
)
from pipeline.viewer_acceptance import (
    ViewerCameraSetV2,
    ViewerPerformancePolicy,
    canonical_viewer_performance_policy_bytes,
    load_viewer_camera_set_bytes,
)
from pipeline.viewer_inputs import (
    ViewerInputMaterializationError,
    materialize_production_viewer_inputs,
)
from pipeline.viewer_session import (
    ViewerSessionOptions,
    run_production_viewer_session,
)
from tests.test_real_scene_import import (
    _patch_production_bundle,
    _write_control_points,
    _write_production_training_stage,
)


def test_materializer_derives_three_content_bound_registered_camera_poses(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
    )
    _patch_production_bundle(monkeypatch, fixture)
    source = LocalCaptureSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="village-a",
        role="production-acceptance",
        source_kind="local-capture",
        rights_receipt_sha256="b" * 64,
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    source_path = tmp_path / "production-source.json"
    source_bytes = canonical_model_bytes(source)
    source_path.write_bytes(source_bytes)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    workspace_base = tmp_path / "real-scene"
    run_id = "production-a"
    workspace = (
        workspace_base
        / run_id
        / source.dataset_id
        / source_sha256[:16]
    )
    attempt_id = "attempt-import-one"
    import_root = workspace / "stages/import" / attempt_id
    import_receipt = import_real_scene(
        training_root,
        import_root,
        source_role="production-acceptance",
        control_points_path=_write_control_points(
            tmp_path / "control-points.json"
        ),
        geo_origin=(26.0, 119.0, 10.0),
        chunk_size=50.0,
    )
    output_bindings = tuple(
        StageArtifactBinding(
            path=path.relative_to(workspace).as_posix(),
            byte_length=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(
            candidate
            for candidate in import_root.rglob("*")
            if candidate.is_file()
        )
    )
    stage_receipt = StageReceipt(
        dataset_id=source.dataset_id,
        source_sha256=source_sha256,
        stage="import",
        attempt_id=attempt_id,
        created_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        status="completed",
        prerequisites=(),
        outputs=output_bindings,
        alignment_rms_m=import_receipt.alignment_rms_m,
    )
    stage_payload = canonical_stage_receipt_bytes(stage_receipt)
    stage_receipt_dir = workspace / "receipts/import"
    stage_receipt_dir.mkdir(parents=True)
    (
        stage_receipt_dir
        / f"{hashlib.sha256(stage_payload).hexdigest()}.json"
    ).write_bytes(stage_payload)

    resolved = resolve_latest_production_import(
        source_path,
        workspace_base=workspace_base,
        run_id=run_id,
    )
    assert resolved.workspace_root == workspace
    assert resolved.import_root == import_root
    output_dir = workspace / "viewer-inputs"

    result = materialize_production_viewer_inputs(
        import_root=resolved.import_root,
        output_dir=output_dir,
    )

    camera_bytes = result.camera_set_path.read_bytes()
    policy_bytes = result.policy_path.read_bytes()
    camera_set = load_viewer_camera_set_bytes(camera_bytes)
    policy = ViewerPerformancePolicy.model_validate_json(policy_bytes)
    manifest_bytes = (
        import_root / import_receipt.manifest_path
    ).read_bytes()
    registration_path = (
        import_root
        / import_receipt.alignment_observed_registration_path
    )
    registration_bytes = registration_path.read_bytes()
    assert isinstance(camera_set, ViewerCameraSetV2)
    assert camera_set.source_role == "production-acceptance"
    assert camera_set.selection_strategy == "registered-camera-maximin-v1"
    assert camera_set.scene_manifest_sha256 == hashlib.sha256(
        manifest_bytes
    ).hexdigest()
    assert camera_set.import_receipt_sha256 == hashlib.sha256(
        (import_root / "import-receipt.json").read_bytes()
    ).hexdigest()
    assert camera_set.aligned_registration_sha256 == hashlib.sha256(
        registration_bytes
    ).hexdigest()
    assert len(camera_set.poses) == 3
    assert len({pose.pose_id for pose in camera_set.poses}) == 3
    assert policy.required_pose_ids == tuple(
        pose.pose_id for pose in camera_set.poses
    )
    assert policy_bytes == canonical_viewer_performance_policy_bytes(policy)
    assert json.loads(camera_bytes)["schema"] == "nantai.viewer-camera-set.v2"

    with pytest.raises(
        ViewerInputMaterializationError,
        match="output.*absent",
    ):
        materialize_production_viewer_inputs(
            import_root=import_root,
            output_dir=output_dir,
        )

    def _capture(argv, **kwargs):
        studio_url = urlsplit(argv[argv.index("--studio-url") + 1])
        connection = http.client.HTTPConnection(
            studio_url.hostname,
            studio_url.port,
            timeout=30,
        )
        connection.request(
            "GET",
            "/web/data/recon/recon_manifest.json",
        )
        response = connection.getresponse()
        served_manifest = response.read()
        connection.close()
        assert response.status == 200
        assert served_manifest == manifest_bytes
        assert kwargs == {
            "cwd": Path(__file__).resolve().parents[1],
            "check": False,
        }
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(session_module.subprocess, "run", _capture)
    evidence_root = workspace
    assert (
        run_production_viewer_session(
            ViewerSessionOptions(
                project_root=Path(__file__).resolve().parents[1],
                import_root=import_root,
                policy_path=result.policy_path,
                camera_set_path=result.camera_set_path,
                output_path=workspace / "viewer/report.json",
                decision_path=workspace / "viewer/decision.json",
                evidence_root=evidence_root,
                node_executable=Path(sys.executable).resolve(),
                python_executable=Path(sys.executable).resolve(),
                headless=True,
            )
        )
        == 0
    )


def test_materializer_rejects_preview_or_unverified_import_root(
    tmp_path,
    monkeypatch,
):
    unverified = tmp_path / "unverified"
    unverified.mkdir()
    monkeypatch.setattr(
        import_module,
        "validate_real_scene_import_receipt",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        ViewerInputMaterializationError,
        match="production.*import receipt",
    ):
        materialize_production_viewer_inputs(
            import_root=unverified,
            output_dir=tmp_path / "viewer-inputs",
        )
