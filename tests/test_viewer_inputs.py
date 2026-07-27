from __future__ import annotations

import hashlib
import json

import pytest

import pipeline.real_scene_import as import_module
from pipeline.real_scene_import import import_real_scene
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
    import_root = tmp_path / "imported"
    receipt = import_real_scene(
        training_root,
        import_root,
        source_role="production-acceptance",
        control_points_path=_write_control_points(
            tmp_path / "control-points.json"
        ),
        geo_origin=(26.0, 119.0, 10.0),
        chunk_size=50.0,
    )
    output_dir = tmp_path / "viewer-inputs"

    result = materialize_production_viewer_inputs(
        import_root=import_root,
        output_dir=output_dir,
    )

    camera_bytes = result.camera_set_path.read_bytes()
    policy_bytes = result.policy_path.read_bytes()
    camera_set = load_viewer_camera_set_bytes(camera_bytes)
    policy = ViewerPerformancePolicy.model_validate_json(policy_bytes)
    manifest_bytes = (import_root / receipt.manifest_path).read_bytes()
    registration_path = import_root / receipt.alignment_observed_registration_path
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
