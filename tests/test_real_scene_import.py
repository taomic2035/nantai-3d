from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

import pipeline.real_scene_import as import_module
from pipeline.metric_alignment_evidence import (
    MetricAlignmentPolicy,
    canonical_metric_alignment_policy_bytes,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_import import (
    RealSceneImportError,
    RealSceneImportIntegrity,
    RealSceneImportReceipt,
    import_real_scene,
    inspect_real_scene_ply,
    validate_real_scene_import_receipt,
)
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    FrameTransform,
    GeoAlignment,
    Handedness,
    MetricStatus,
    RegistrationResult,
    Sim3,
    SplatInput,
    TransformMethod,
)
from pipeline.training_executor import (
    ExecutorInputIdentity,
    ExecutorObservation,
    advance_attempt,
    new_attempt,
)
from pipeline.training_provenance import (
    GpuEnvironment,
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
    TrainingResult,
    build_training_result,
    request_canonical_sha256,
)
from scripts.prepare_import import prepare_from_registration

_BASE_PROPERTIES = (
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
)


def _write_3dgs_ply(
    path: Path,
    *,
    count: int = 4,
    quaternion: tuple[float, float, float, float] = (2.0, 0.0, 0.0, 0.0),
    omit: frozenset[str] = frozenset(),
    rest_indices: tuple[int, ...] = (),
    nan_property: str | None = None,
) -> Path:
    properties = [
        *(
            (name, "f4")
            for name in _BASE_PROPERTIES
            if name not in omit
        ),
        *((f"f_rest_{index}", "f4") for index in rest_indices),
    ]
    vertices = np.zeros(count, dtype=properties)
    indices = np.arange(count, dtype=np.int64)
    compact_xyz = (
        (indices % 100).astype(np.float32) / 10.0,
        ((indices // 100) % 100).astype(np.float32) / 10.0,
        (indices // 10_000).astype(np.float32) / 10.0,
    )
    for values, axis in zip(
        compact_xyz,
        ("x", "y", "z"),
        strict=True,
    ):
        if axis in vertices.dtype.names:
            vertices[axis] = values
    for name in ("scale_0", "scale_1", "scale_2"):
        if name in vertices.dtype.names:
            vertices[name] = -2.0
    for index, name in enumerate(("rot_0", "rot_1", "rot_2", "rot_3")):
        if name in vertices.dtype.names:
            vertices[name] = quaternion[index]
    if nan_property is not None:
        vertices[nan_property][0] = np.nan
    PlyData(
        [PlyElement.describe(vertices, "vertex")],
        text=False,
        byte_order="<",
    ).write(path)
    return path


def test_non_unit_quaternion_is_measured_before_copy_normalization(tmp_path):
    source = _write_3dgs_ply(tmp_path / "source.ply")

    report = inspect_real_scene_ply(source)

    assert report.gaussian_count == 4
    assert report.sh_degree == 0
    assert report.non_unit_quaternion_count == 4


def test_zero_quaternion_is_rejected_as_irrecoverable(tmp_path):
    source = _write_3dgs_ply(
        tmp_path / "source.ply",
        quaternion=(0.0, 0.0, 0.0, 0.0),
    )

    with pytest.raises(RealSceneImportError, match="quaternion"):
        inspect_real_scene_ply(source)


def test_production_import_requires_at_least_100000_gaussians(tmp_path):
    source = _write_3dgs_ply(tmp_path / "source.ply", count=99_999)

    with pytest.raises(RealSceneImportError, match="100000"):
        inspect_real_scene_ply(
            source,
            minimum_gaussians=100_000,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"omit": frozenset({"opacity"})}, "opacity"),
        ({"nan_property": "x"}, "non-finite"),
        ({"rest_indices": (0, 2)}, "contiguous"),
        ({"rest_indices": (0, 1, 2)}, "complete SH"),
    ],
)
def test_semantically_damaged_ply_is_rejected(tmp_path, kwargs, message):
    source = _write_3dgs_ply(tmp_path / "source.ply", **kwargs)

    with pytest.raises(RealSceneImportError, match=message):
        inspect_real_scene_ply(source)


def _aligned_registration() -> RegistrationResult:
    pose_frame = CoordinateFrame(
        frame_id="sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
        evidence=("colmap-joint-model",),
    )
    world_frame = CoordinateFrame(
        frame_id="project-enu",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.ENU_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.ALIGNED,
        provenance=FrameProvenance.MEASURED,
        evidence=("survey-control-points",),
    )
    transform = FrameTransform(
        source_frame=pose_frame.frame_id,
        target_frame=world_frame.frame_id,
        sim3=Sim3(scale=2.0, t_xyz=(10.0, 20.0, 30.0)),
        method=TransformMethod.CONTROL_POINTS,
        evidence=("survey-control-points",),
    )
    return RegistrationResult(
        engine="colmap",
        pose_frame=pose_frame,
        world_frame=world_frame,
        alignment_status=AlignmentStatus.ALIGNED,
        pose_to_world=transform,
        sessions=[
            CaptureSession(
                session_id="photo-session",
                kind="photo_batch",
                source="capture",
                images=["frame.png"],
            )
        ],
        poses=[],
    )


def test_prepare_from_registration_binds_one_aggregate_splat_transform(tmp_path):
    source = _write_3dgs_ply(tmp_path / "source.ply")
    registration = _aligned_registration()

    registration_path, splat_path = prepare_from_registration(
        source,
        tmp_path / "contracts",
        registration,
        session_id="real-scene-trained",
        extra_evidence=("training_provenance.v1=" + "a" * 64,),
    )

    prepared = RegistrationResult.model_validate_json(
        registration_path.read_bytes()
    )
    splat = SplatInput.model_validate_json(splat_path.read_bytes())
    assert prepared.sessions[-1].session_id == "real-scene-trained"
    assert prepared.pose_frame.evidence[-1] == (
        "training_provenance.v1=" + "a" * 64
    )
    assert splat.source_frame == prepared.pose_frame
    assert splat.transform == prepared.pose_to_world
    assert Path(splat.path) == source.absolute()


def _write_preview_training_stage(root: Path) -> SimpleNamespace:
    result_root = root / "local-brush"
    workspace = result_root / "workspace"
    bundle_path = root / "training-bundle/training-job.zip"
    workspace.mkdir(parents=True)
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_bytes(b"verified-preview-bundle")

    registration = _aligned_registration().model_copy(
        update={
            "world_frame": None,
            "pose_to_world": None,
            "alignment_status": AlignmentStatus.UNALIGNED,
        }
    )
    registration_bytes = (
        registration.model_dump_json(indent=2) + "\n"
    ).encode()
    input_bytes = {
        "capture/manifest.json": b"capture-manifest\n",
        "sfm/registration.json": registration_bytes,
    }
    bindings = tuple(
        TrainingInputBinding(
            artifact_kind=kind,
            artifact_sha256=hashlib.sha256(input_bytes[path]).hexdigest(),
            artifact_path=path,
            artifact_size_bytes=len(input_bytes[path]),
        )
        for kind, path in (
            ("capture_manifest", "capture/manifest.json"),
            ("registration_json", "sfm/registration.json"),
        )
    )
    bundle_request = TrainingRequest(
        request_id="bundle-request",
        created_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        input_bindings=bindings,
        training_config=TrainingConfig(
            trainer_name="nerfstudio-splatfacto",
            trainer_version="1.1.5",
            max_resolution=1600,
            total_steps=30_000,
            random_seed=42,
        ),
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256="b" * 64,
    )
    config_bytes = b'{"executor_kind":"local-brush"}\n'
    request = TrainingRequest(
        request_id="local-preview",
        created_at_utc=bundle_request.created_at_utc,
        input_bindings=bindings,
        training_config=TrainingConfig(
            trainer_name="brush",
            trainer_version="0.3.0",
            max_resolution=1024,
            total_steps=1000,
            random_seed=42,
        ),
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
    )
    ply_path = _write_3dgs_ply(
        workspace / "trained.brush-export.ply"
    )
    log_bytes = b"brush completed\n"
    (result_root / "operator-intent-config.yml").write_bytes(config_bytes)
    (workspace / "brush.log").write_bytes(log_bytes)
    result = build_training_result(
        request=request,
        result_id="local-result",
        started_at_utc=bundle_request.created_at_utc,
        finished_at_utc=bundle_request.created_at_utc,
        actual_trainer_name="brush",
        actual_trainer_version="0.3.0",
        actual_config_bytes=config_bytes,
        actual_ply_bytes=ply_path.read_bytes(),
        actual_log_bytes=log_bytes,
        input_bytes_by_path=input_bytes,
        gpu_environment=GpuEnvironment(
            gpu_name="Apple Metal preview",
            gpu_memory_mb=0,
            cuda_version="not-applicable",
            driver_version="test",
        ),
        exit_code=0,
        actual_ply_path="workspace/trained.brush-export.ply",
        actual_config_path="operator-intent-config.yml",
        actual_log_path="workspace/brush.log",
        gaussian_count=4,
        sh_degree=0,
    )
    request_bytes = canonical_model_bytes(request)
    result_bytes = canonical_model_bytes(result)
    (result_root / "training-request.json").write_bytes(request_bytes)
    (result_root / "training-result.json").write_bytes(result_bytes)

    identity = ExecutorInputIdentity(
        executor_kind="local-brush",
        request_sha256=request_canonical_sha256(request),
        dataset_receipt_sha256="d" * 64,
        training_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        trainer_name="brush",
        trainer_version="0.3.0",
        job_id="local-preview-job",
    )
    attempt = new_attempt(
        identity,
        attempt_id="attempt-local-preview",
        created_at_utc=bundle_request.created_at_utc,
        quality_role="preview-only",
    )
    attempt = advance_attempt(
        attempt,
        ExecutorObservation(
            state="running",
            observed_at_utc=bundle_request.created_at_utc,
        ),
    )
    attempt = advance_attempt(
        attempt,
        ExecutorObservation(
            state="succeeded",
            observed_at_utc=bundle_request.created_at_utc,
            exit_code=0,
            stdout_sha256=hashlib.sha256(b"").hexdigest(),
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
            result_bundle_sha256=hashlib.sha256(result_bytes).hexdigest(),
        ),
    )
    (result_root / "executor-attempt.json").write_bytes(
        canonical_model_bytes(attempt)
    )
    return SimpleNamespace(
        verified_bundle=SimpleNamespace(
            path=bundle_path,
            bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            manifest=SimpleNamespace(dataset_receipt_sha256="d" * 64),
            request=bundle_request,
        ),
        input_bytes=input_bytes,
    )


def _write_production_training_stage(
    root: Path,
    *,
    count: int,
    dataparser_scale: float = 1.0,
) -> SimpleNamespace:
    result_root = root / "remote-result"
    result_root.mkdir(parents=True)
    bundle_path = root / "training-bundle/training-job.zip"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_bytes(b"verified-production-bundle")

    registration = _aligned_registration().model_copy(
        update={
            "world_frame": None,
            "pose_to_world": None,
            "alignment_status": AlignmentStatus.UNALIGNED,
        }
    )
    registration_bytes = (
        registration.model_dump_json(indent=2) + "\n"
    ).encode()
    input_bytes = {
        "capture/manifest.json": b"capture-manifest\n",
        "sfm/registration.json": registration_bytes,
    }
    bindings = tuple(
        TrainingInputBinding(
            artifact_kind=kind,
            artifact_sha256=hashlib.sha256(input_bytes[path]).hexdigest(),
            artifact_path=path,
            artifact_size_bytes=len(input_bytes[path]),
        )
        for kind, path in (
            ("capture_manifest", "capture/manifest.json"),
            ("registration_json", "sfm/registration.json"),
        )
    )
    config_bytes = b"trainer: nerfstudio-splatfacto\nversion: 1.1.5\n"
    request = TrainingRequest(
        request_id="remote-production",
        created_at_utc=datetime(2026, 7, 26, tzinfo=UTC),
        input_bindings=bindings,
        training_config=TrainingConfig(
            trainer_name="nerfstudio-splatfacto",
            trainer_version="1.1.5",
            max_resolution=1600,
            total_steps=30_000,
            random_seed=42,
            extra_config=(
                ("auto_scale_poses", "false"),
                ("center_method", "none"),
                ("orientation_method", "none"),
                ("scale_factor", "1.0"),
            ),
        ),
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
    )
    ply_path = _write_3dgs_ply(
        result_root / "point_cloud.ply",
        count=count,
    )
    log_bytes = b"remote training completed\n"
    dataparser_bytes = (
        json.dumps(
            {
                "scale": dataparser_scale,
                "transform": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    (result_root / "operator-intent-config.yml").write_bytes(config_bytes)
    (result_root / "training.log").write_bytes(log_bytes)
    (result_root / "dataparser_transforms.json").write_bytes(
        dataparser_bytes
    )
    result = build_training_result(
        request=request,
        result_id="remote-result",
        started_at_utc=request.created_at_utc,
        finished_at_utc=request.created_at_utc,
        actual_trainer_name="nerfstudio-splatfacto",
        actual_trainer_version="1.1.5",
        actual_config_bytes=config_bytes,
        actual_ply_bytes=ply_path.read_bytes(),
        actual_log_bytes=log_bytes,
        input_bytes_by_path=input_bytes,
        gpu_environment=GpuEnvironment(
            gpu_name="NVIDIA production GPU",
            gpu_memory_mb=24_576,
            cuda_version="12.4",
            driver_version="550.54",
        ),
        exit_code=0,
        actual_ply_path="point_cloud.ply",
        actual_config_path="operator-intent-config.yml",
        actual_log_path="training.log",
        gaussian_count=count,
        sh_degree=0,
        dataparser_transform_bytes=dataparser_bytes,
        dataparser_transform_path="dataparser_transforms.json",
    )
    request_bytes = canonical_model_bytes(request)
    result_bytes = canonical_model_bytes(result)
    (result_root / "training-request.json").write_bytes(request_bytes)
    (result_root / "training-result.json").write_bytes(result_bytes)
    archive_bytes = b"verified remote result archive"
    (result_root / "result-bundle.zip").write_bytes(archive_bytes)

    identity = ExecutorInputIdentity(
        executor_kind="remote-shell-nerfstudio",
        request_sha256=request_canonical_sha256(request),
        dataset_receipt_sha256="d" * 64,
        training_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        trainer_name="nerfstudio-splatfacto",
        trainer_version="1.1.5",
        job_id="remote-production-job",
    )
    attempt = new_attempt(
        identity,
        attempt_id="attempt-remote-production",
        created_at_utc=request.created_at_utc,
        quality_role="production",
    )
    attempt = advance_attempt(
        attempt,
        ExecutorObservation(
            state="running",
            observed_at_utc=request.created_at_utc,
        ),
    )
    attempt = advance_attempt(
        attempt,
        ExecutorObservation(
            state="succeeded",
            observed_at_utc=request.created_at_utc,
            exit_code=0,
            stdout_sha256=hashlib.sha256(b"stdout").hexdigest(),
            stderr_sha256=hashlib.sha256(b"stderr").hexdigest(),
            result_bundle_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        ),
    )
    (root / "executor-attempt.json").write_bytes(
        canonical_model_bytes(attempt)
    )
    return SimpleNamespace(
        verified_bundle=SimpleNamespace(
            path=bundle_path,
            bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            manifest=SimpleNamespace(dataset_receipt_sha256="d" * 64),
            request=request,
        ),
        input_bytes=input_bytes,
    )


def test_canary_preview_import_stays_arbitrary_and_closes_all_outputs(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_preview_training_stage(training_root)
    monkeypatch.setattr(
        import_module,
        "verify_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )
    output_root = tmp_path / "import"
    trained_ply = (
        training_root
        / "local-brush/workspace/trained.brush-export.ply"
    )
    trained_sha_before = hashlib.sha256(trained_ply.read_bytes()).hexdigest()

    receipt = import_real_scene(
        training_root,
        output_root,
        source_role="internal-canary",
        chunk_size=2.0,
    )

    manifest = json.loads(
        (output_root / receipt.manifest_path).read_bytes()
    )
    assert (
        manifest["coordinate_contract"]["target_frame"]["units"]
        == "arbitrary"
    )
    assert manifest["provenance"]["geometry_usability"] == "preview-only"
    assert receipt.training_quality_role == "preview-only"
    assert receipt.chunk_units == "source-units"
    assert receipt.gaussian_count == 4
    assert receipt.normalized_quaternion_count == 4
    assert hashlib.sha256(trained_ply.read_bytes()).hexdigest() == (
        trained_sha_before
    )
    assert inspect_real_scene_ply(
        output_root / receipt.normalized_ply_path
    ).non_unit_quaternion_count == 0
    assert validate_real_scene_import_receipt(
        output_root / "import-receipt.json",
        output_root,
    ) == receipt


def test_brush_preview_cannot_satisfy_production_import(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_preview_training_stage(training_root)
    monkeypatch.setattr(
        import_module,
        "verify_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )

    with pytest.raises(RealSceneImportError, match="preview-only"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=tmp_path / "control-points.json",
            geo_origin=(26.0, 119.0, 10.0),
        )


def _patch_production_bundle(monkeypatch, fixture) -> None:
    monkeypatch.setattr(
        import_module,
        "verify_production_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )


def test_non_identity_nerfstudio_transform_blocks_import(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=4,
        dataparser_scale=0.25,
    )
    _patch_production_bundle(monkeypatch, fixture)

    with pytest.raises(RealSceneImportError, match="dataparser"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=tmp_path / "control-points.json",
            geo_origin=(26.0, 119.0, 10.0),
        )


def _write_control_points(path: Path) -> Path:
    source = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    payload = [
        {
            "label": f"survey-{index}",
            "source_xyz": list(point),
            "enu_xyz": [
                2.0 * point[0] + 10.0,
                2.0 * point[1] + 20.0,
                2.0 * point[2] + 30.0,
            ],
        }
        for index, point in enumerate(source)
    ]
    path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_production_import_is_metric_chunked_and_content_closed(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
    )
    _patch_production_bundle(monkeypatch, fixture)
    output_root = tmp_path / "import"

    receipt = import_real_scene(
        training_root,
        output_root,
        source_role="production-acceptance",
        control_points_path=_write_control_points(
            tmp_path / "control-points.json"
        ),
        geo_origin=(26.0, 119.0, 10.0),
        chunk_size=50.0,
    )

    manifest = json.loads(
        (output_root / receipt.manifest_path).read_bytes()
    )
    transform = manifest["coordinate_contract"]["transform_chain"][0]
    assert receipt.training_quality_role == "production"
    assert receipt.schema_id == "nantai.real-scene-import-receipt.v2"
    assert receipt.gaussian_count == 100_000
    assert receipt.target_units == "meters"
    assert receipt.chunk_units == "metres"
    assert receipt.geometry_usability == "metric-aligned"
    assert receipt.alignment_rms_m == pytest.approx(0.0, abs=1e-12)
    assert receipt.alignment_measurement_path is not None
    assert receipt.alignment_policy_path is not None
    assert receipt.alignment_decision_path is not None
    decision = json.loads(
        (output_root / receipt.alignment_decision_path).read_bytes()
    )
    assert decision["status"] == "accepted"
    assert (
        decision["measurement_sha256"]
        == receipt.alignment_measurement_sha256
    )
    assert decision["policy_sha256"] == receipt.alignment_policy_sha256
    assert decision["content_sha256"] == receipt.alignment_decision_sha256
    with pytest.raises(ValueError, match="100000"):
        RealSceneImportReceipt.model_validate(
            receipt.model_copy(
                update={"gaussian_count": 99_999}
            ).model_dump(by_alias=True)
        )
    assert transform["transform_id"].startswith("xf-")
    assert manifest["artifacts"]["chunks"]["total_points"] == 100_000
    assert validate_real_scene_import_receipt(
        output_root / "import-receipt.json",
        output_root,
    ) == receipt

    replacement_policy = MetricAlignmentPolicy.create(
        max_rms_m=0.2,
        max_residual_m=0.2,
        min_span_ratio=1e-3,
    )
    policy_bytes = canonical_metric_alignment_policy_bytes(
        replacement_policy
    )
    policy_path = output_root / receipt.alignment_policy_path
    policy_path.write_bytes(policy_bytes)
    replacement_binding = next(
        binding
        for binding in receipt.artifacts
        if binding.path == receipt.alignment_policy_path
    ).model_copy(
        update={
            "byte_length": len(policy_bytes),
            "sha256": hashlib.sha256(policy_bytes).hexdigest(),
        }
    )
    drifted_receipt = RealSceneImportReceipt.model_validate(
        receipt.model_copy(
            update={
                "alignment_policy_sha256": replacement_policy.content_sha256,
                "artifacts": tuple(
                    replacement_binding
                    if binding.path == receipt.alignment_policy_path
                    else binding
                    for binding in receipt.artifacts
                ),
            }
        )
    )
    (output_root / "import-receipt.json").write_bytes(
        canonical_model_bytes(drifted_receipt)
    )
    with pytest.raises(
        RealSceneImportError,
        match="decision disagrees",
    ):
        validate_real_scene_import_receipt(
            output_root / "import-receipt.json",
            output_root,
        )


def test_import_receipt_rejects_chunk_payload_tamper(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_preview_training_stage(training_root)
    monkeypatch.setattr(
        import_module,
        "verify_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )
    output_root = tmp_path / "import"
    import_real_scene(
        training_root,
        output_root,
        source_role="internal-canary",
        chunk_size=2.0,
    )
    chunk = next((output_root / "web/chunks").glob("chunk_*.ply"))
    chunk.write_bytes(chunk.read_bytes() + b"tamper")

    with pytest.raises(RealSceneImportError, match="sha256"):
        validate_real_scene_import_receipt(
            output_root / "import-receipt.json",
            output_root,
        )


def test_import_receipt_rejects_lie_in_bound_integrity_report(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_preview_training_stage(training_root)
    monkeypatch.setattr(
        import_module,
        "verify_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )
    output_root = tmp_path / "import"
    receipt = import_real_scene(
        training_root,
        output_root,
        source_role="internal-canary",
        chunk_size=2.0,
    )
    integrity_path = output_root / receipt.integrity_report_path
    integrity = RealSceneImportIntegrity.model_validate_json(
        integrity_path.read_bytes()
    )
    lying_integrity = integrity.model_copy(
        update={
            "scene_gaussian_count": integrity.scene_gaussian_count + 1,
            "chunk_gaussian_count": integrity.chunk_gaussian_count + 1,
        }
    )
    integrity_bytes = canonical_model_bytes(lying_integrity)
    integrity_path.write_bytes(integrity_bytes)
    replacement = next(
        binding
        for binding in receipt.artifacts
        if binding.path == receipt.integrity_report_path
    ).model_copy(
        update={
            "byte_length": len(integrity_bytes),
            "sha256": hashlib.sha256(integrity_bytes).hexdigest(),
        }
    )
    lying_receipt = RealSceneImportReceipt.model_validate(
        receipt.model_copy(
            update={
                "artifacts": tuple(
                    replacement
                    if binding.path == receipt.integrity_report_path
                    else binding
                    for binding in receipt.artifacts
                )
            }
        )
    )
    (output_root / "import-receipt.json").write_bytes(
        canonical_model_bytes(lying_receipt)
    )

    with pytest.raises(RealSceneImportError, match="integrity report"):
        validate_real_scene_import_receipt(
            output_root / "import-receipt.json",
            output_root,
        )


def test_result_request_drift_is_rejected(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=4,
    )
    _patch_production_bundle(monkeypatch, fixture)
    result_path = training_root / "remote-result/training-result.json"
    result = TrainingResult.model_validate_json(result_path.read_bytes())
    result_path.write_bytes(
        canonical_model_bytes(
            result.model_copy(
                update={"request_canonical_sha256": "f" * 64}
            )
        )
    )

    with pytest.raises(RealSceneImportError, match="request"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=tmp_path / "control-points.json",
            geo_origin=(26.0, 119.0, 10.0),
        )


def test_training_ply_sha_mismatch_is_rejected(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_preview_training_stage(training_root)
    monkeypatch.setattr(
        import_module,
        "verify_training_job_bundle",
        lambda path: fixture.verified_bundle,
    )
    monkeypatch.setattr(
        import_module,
        "load_training_job_input_bytes",
        lambda bundle: fixture.input_bytes,
    )
    ply = (
        training_root
        / "local-brush/workspace/trained.brush-export.ply"
    )
    ply.write_bytes(ply.read_bytes() + b"tamper")

    with pytest.raises(RealSceneImportError, match="content-closed"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="internal-canary",
        )
