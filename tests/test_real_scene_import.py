from __future__ import annotations

import hashlib
import json
import os
import stat
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
from pipeline.production_runtime_evidence import (
    canonical_production_runtime_decision_bytes,
    canonical_production_runtime_measurement_bytes,
    canonical_production_runtime_policy_bytes,
    load_production_runtime_measurement_bytes,
)
from pipeline.production_training_closure import (
    ProductionResultBundleManifestV2,
    ProductionResultMember,
    ProductionTrainingClosure,
    canonical_production_result_manifest_bytes,
    canonical_production_training_closure_bytes,
    derive_production_training_closure,
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
from pipeline.real_scene_runner import (
    RealSceneRunner,
    RealSceneSourceIdentity,
)
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingImageIdentity,
    held_out_split_canonical_bytes,
)
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
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
from pipeline.remote_shell_executor import (
    RemoteResultBundleError,
    build_production_remote_result_bundle,
    inspect_remote_result_bundle_schema,
    verify_production_remote_result_bundle,
)
from pipeline.render_evaluation import (
    RenderCameraRecord,
    RenderDecision,
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    RenderFrameMetric,
    render_artifact_stem,
    render_evaluation_sha256,
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
    result_canonical_sha256,
)
from scripts.prepare_import import prepare_from_registration
from tests.test_production_training_closure import (
    _derive as derive_closure_fixture,
)
from tests.test_production_training_closure import (
    _fixture as production_closure_fixture,
)
from tests.test_render_evaluation import _png as render_png

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


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def test_read_regular_bytes_rejects_descriptor_reparse_drift(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "import-evidence.json"
    source.write_bytes(b'{"evidence":true}\n')
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        observed = original_fstat(descriptor)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(import_module.os, "fstat", drifting_fstat)

    with pytest.raises(
        RealSceneImportError,
        match="import evidence changed while being read",
    ):
        import_module._read_regular_bytes(
            source,
            label="import evidence",
        )

    assert calls == 2


def test_read_regular_bytes_rejects_size_cap_before_open(
    tmp_path,
    monkeypatch,
):
    source = (tmp_path / "import-evidence.json").absolute()
    source.write_bytes(b'{"evidence":true}\n')
    original_lstat = Path.lstat

    def oversized_lstat(path):
        observed = original_lstat(path)
        if path.absolute() != source:
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=observed.st_mode,
            st_size=import_module._MAX_IMPORT_FILE_BYTES + 1,
            st_mtime_ns=observed.st_mtime_ns,
            st_file_attributes=getattr(
                observed,
                "st_file_attributes",
                0,
            ),
        )

    monkeypatch.setattr(Path, "lstat", oversized_lstat)

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("oversized import input must not be opened")

    monkeypatch.setattr(import_module.os, "open", forbidden_open)

    with pytest.raises(
        RealSceneImportError,
        match="import evidence size is outside the allowed range",
    ):
        import_module._read_regular_bytes(
            source,
            label="import evidence",
        )


def test_read_regular_bytes_open_error_hides_private_details(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "import-evidence.json"
    source.write_bytes(b'{"evidence":true}\n')
    private_detail = r"D:\private-capture\secret-token"

    def fail_open(*_args, **_kwargs):
        raise OSError(private_detail)

    monkeypatch.setattr(import_module.os, "open", fail_open)

    with pytest.raises(RealSceneImportError) as captured:
        import_module._read_regular_bytes(
            source,
            label="import evidence",
        )

    assert str(captured.value) == "import evidence cannot be read"
    assert private_detail not in str(captured.value)


def test_read_regular_bytes_tolerates_cross_surface_mode_drift(
    tmp_path,
    monkeypatch,
):
    """RED->GREEN: lstat/fstat st_mode permission bits may differ on Windows.

    The file type (S_IFMT) must match, but permission bits (e.g. executable
    bit) can legitimately differ between path-surface stat and
    descriptor-surface stat on some platforms.  _stat_signature must compare
    only the file type for cross-surface identity, not the full st_mode.
    """
    source = tmp_path / "import-evidence.json"
    source.write_bytes(b'{"evidence":true}\n')
    original_fstat = os.fstat
    calls = 0

    def mode_drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        observed = original_fstat(descriptor)
        if calls == 1:
            # Flip permission bits but keep file type identical
            new_mode = (observed.st_mode & ~0o777) | 0o600
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=new_mode,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=getattr(
                    observed, "st_file_attributes", 0
                ),
            )
        return observed

    monkeypatch.setattr(import_module.os, "fstat", mode_drifting_fstat)

    payload = import_module._read_regular_bytes(
        source,
        label="import evidence",
    )
    assert payload == b'{"evidence":true}\n'


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
    camera_positions = (
        (0.0, 0.0, 1.5),
        (8.0, 0.0, 1.5),
        (0.0, 7.0, 2.0),
        (8.0, 7.0, 2.5),
    )
    images = [
        f"registered/frame-{index:03d}.png"
        for index in range(len(camera_positions))
    ]
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
                images=images,
            )
        ],
        poses=[
            CameraPose(
                image=image,
                session_id="photo-session",
                quat_wxyz=[1.0, 0.0, 0.0, 0.0],
                t_xyz=list(position),
                intrinsics=CameraIntrinsics.from_fov(
                    width=1600,
                    height=900,
                    fov_deg=65.0,
                ),
            )
            for image, position in zip(
                images,
                camera_positions,
                strict=True,
            )
        ],
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


def test_prepare_from_registration_does_not_follow_symlink_redirect(
    tmp_path: Path,
) -> None:
    """RED->GREEN: prepare_from_registration must not write the registration
    trust root through a pre-placed dangling symlink redirect.

    ``_write_lf`` used ``Path.write_text``, which follows symlinks.  A
    pre-placed dangling symlink at ``out_dir/registration.json`` would
    redirect the coordinate trust-root bytes to an attacker target and leave
    the operator's ``registration.json`` as the dangling link.  The same
    vulnerability class was already closed for ``pipeline/registration.py``,
    ``pipeline/alignment.py`` and ``pipeline/reconstruct.py``; this is the
    parallel caller-supplied contract write path in the CLI helper.
    """
    source = _write_3dgs_ply(tmp_path / "source.ply")
    registration = _aligned_registration()
    out_dir = tmp_path / "contracts"
    out_dir.mkdir()
    reg_path = out_dir / "registration.json"
    attacker_target = tmp_path / "stolen-registration.json"
    try:
        reg_path.symlink_to(attacker_target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    prepare_from_registration(
        source,
        out_dir,
        registration,
        session_id="real-scene-trained",
    )

    assert not attacker_target.exists(), (
        "prepare_from_registration followed the dangling symlink and "
        "wrote the registration trust root to the attacker target"
    )
    assert reg_path.is_symlink() is False
    assert reg_path.is_file()


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
    include_production_closure: bool = True,
    invalid_render_camera: bool = False,
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
    evaluation_payloads = {
        "held-out/frame.png": b"held-out source\n",
        "training/frame.png": b"training source\n",
    }
    ordered_images = tuple(
        sorted(
            (
                TrainingImageIdentity(
                    logical_path=path,
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
                for path, payload in evaluation_payloads.items()
            ),
            key=lambda identity: (
                identity.sha256,
                identity.logical_path,
            ),
        )
    )
    split = HeldOutSplit(
        ratio=0.5,
        total_count=2,
        held_out=ordered_images[:1],
        train=ordered_images[1:],
    )
    split_bytes = held_out_split_canonical_bytes(split)
    input_bytes = {
        "capture/manifest.json": b"capture-manifest\n",
        "sfm/registration.json": registration_bytes,
        "training/held-out-split.json": split_bytes,
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
            ("held_out_split", "training/held-out-split.json"),
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
            gpu_name="NVIDIA RTX 4090",
            gpu_memory_mb=24_564,
            cuda_version="12.8",
            driver_version="575.64.03",
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
    fixture = SimpleNamespace(
        verified_bundle=SimpleNamespace(
            path=bundle_path,
            bundle_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            manifest=SimpleNamespace(dataset_receipt_sha256="d" * 64),
            request=request,
        ),
        attempt=attempt,
        input_bytes=input_bytes,
        evaluation_bytes={
            identity.logical_path: evaluation_payloads[
                identity.logical_path
            ]
            for identity in split.held_out
        },
        include_production_closure=include_production_closure,
        invalid_render_camera=invalid_render_camera,
        request=request,
        result=result,
        split=split,
    )
    if include_production_closure and count >= 100_000:
        _write_production_closure_evidence(root, fixture)
    return fixture


def _write_production_closure_evidence(
    root: Path,
    fixture: SimpleNamespace,
) -> None:
    result_root = root / "remote-result"
    base = production_closure_fixture()
    runtime = base["runtime"]
    runtime_policy = base["runtime_policy"]
    runtime_decision = base["runtime_decision"]
    transforms_bytes = (
        b'{"test_filenames":["bound-by-camera-records"]}\n'
    )
    held_out = fixture.split.held_out[0]
    source_bytes = fixture.evaluation_bytes[held_out.logical_path]
    protocol = RenderEvaluationProtocol(
        width=4,
        height=3,
        crop_mode="center-crop",
        colour_space="srgb",
        alpha_handling="reject",
        mask_handling="none",
        ssim_window_size=11,
        ssim_sigma=1.5,
        ssim_data_range=1.0,
        lpips_backbone="alex",
    )
    render_policy = RenderEvaluationPolicy(
        held_out_split_sha256=hashlib.sha256(
            fixture.input_bytes["training/held-out-split.json"]
        ).hexdigest(),
        transforms_sha256=hashlib.sha256(
            transforms_bytes
        ).hexdigest(),
        evaluator_container_digest=(
            runtime.environment.observed_container_identity
        ),
        protocol=protocol,
        minimum_mean_psnr=24.0,
        minimum_mean_ssim=0.8,
        maximum_mean_lpips=0.25,
        minimum_worst_psnr=18.0,
    )
    camera = RenderCameraRecord(
        frame_id=held_out.logical_path,
        source_path=(
            f"prepared/images/{held_out.logical_path}"
        ),
        source_sha256=held_out.sha256,
        transforms_sha256=render_policy.transforms_sha256,
        camera_model="perspective",
        source_width=4,
        source_height=3,
        fx=4.0,
        fy=4.0,
        cx=2.0,
        cy=1.5,
        camera_to_world=(
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
        ),
    )
    camera_bytes = (
        b"not a camera record\n"
        if fixture.invalid_render_camera
        else canonical_model_bytes(camera)
    )
    render_bytes = render_png(4, 3)
    stem = render_artifact_stem(held_out.logical_path)
    frame = RenderFrameMetric(
        frame_id=held_out.logical_path,
        source_path=(
            f"prepared/images/{held_out.logical_path}"
        ),
        source_byte_length=len(source_bytes),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        render_path=(
            f"result/render-evaluation/renders/{stem}.png"
        ),
        render_byte_length=len(render_bytes),
        render_sha256=hashlib.sha256(render_bytes).hexdigest(),
        camera_path=(
            f"result/render-evaluation/cameras/{stem}.json"
        ),
        camera_byte_length=len(camera_bytes),
        camera_sha256=hashlib.sha256(camera_bytes).hexdigest(),
        psnr=28.0,
        ssim=0.9,
        lpips=0.1,
    )
    config_bytes = (
        result_root / "operator-intent-config.yml"
    ).read_bytes()
    report = RenderEvaluationReport(
        evaluation_id="evaluation-production-import",
        policy_sha256=render_evaluation_sha256(render_policy),
        held_out_split_sha256=render_policy.held_out_split_sha256,
        evaluator_container_digest=(
            render_policy.evaluator_container_digest
        ),
        protocol=render_policy.protocol,
        frames=(frame,),
        trainer_config_sha256=hashlib.sha256(
            config_bytes
        ).hexdigest(),
        mean_psnr=28.0,
        mean_ssim=0.9,
        mean_lpips=0.1,
        worst_psnr=28.0,
    )
    decision = RenderDecision(
        accepted=True,
        failed_thresholds=(),
        policy_sha256=render_evaluation_sha256(render_policy),
        report_sha256=render_evaluation_sha256(report),
        frame_count=1,
        mean_psnr=28.0,
        mean_ssim=0.9,
        mean_lpips=0.1,
        worst_psnr=28.0,
    )
    payloads = {
        "container-id.txt": (
            runtime.environment.container_instance_id + "\n"
        ).encode("ascii"),
        "container-identity.txt": (
            runtime.environment.observed_container_identity + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            result_root / "dataparser_transforms.json"
        ).read_bytes(),
        "operator-intent-config.yml": config_bytes,
        "point_cloud.ply": (
            result_root / "point_cloud.ply"
        ).read_bytes(),
        "production-runtime/decision.json": (
            canonical_production_runtime_decision_bytes(
                runtime_decision
            )
        ),
        "production-runtime/measurement.json": (
            canonical_production_runtime_measurement_bytes(runtime)
        ),
        "production-runtime/policy.json": (
            canonical_production_runtime_policy_bytes(runtime_policy)
        ),
        f"render-evaluation/cameras/{stem}.json": camera_bytes,
        "render-evaluation/policy.json": canonical_model_bytes(
            render_policy
        ),
        f"render-evaluation/renders/{stem}.png": render_bytes,
        "render-evaluation/report.json": canonical_model_bytes(report),
        "render-evaluation/trainer-config.yml": config_bytes,
        "render-evaluation/transforms.json": transforms_bytes,
        "training-request.json": canonical_model_bytes(fixture.request),
        "training-result.json": canonical_model_bytes(fixture.result),
        "training.log": (
            result_root / "training.log"
        ).read_bytes(),
        "worker.stderr.log": b"stderr",
        "worker.stdout.log": b"stdout",
    }
    for relative, payload in payloads.items():
        path = result_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    manifest = ProductionResultBundleManifestV2(
        job_id=fixture.attempt.job_id,
        attempt_id=fixture.attempt.attempt_id,
        request_sha256=request_canonical_sha256(fixture.request),
        training_bundle_sha256=(
            fixture.verified_bundle.bundle_sha256
        ),
        container_instance_id=(
            runtime.environment.container_instance_id
        ),
        container_identity=(
            runtime.environment.observed_container_identity
        ),
        runtime_measurement_artifact_sha256=hashlib.sha256(
            payloads["production-runtime/measurement.json"]
        ).hexdigest(),
        runtime_policy_artifact_sha256=hashlib.sha256(
            payloads["production-runtime/policy.json"]
        ).hexdigest(),
        runtime_decision_artifact_sha256=hashlib.sha256(
            payloads["production-runtime/decision.json"]
        ).hexdigest(),
        members=tuple(
            ProductionResultMember(
                path=path,
                byte_length=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            for path, payload in sorted(payloads.items())
        ),
    )
    (result_root / "result-bundle-manifest.json").write_bytes(
        canonical_production_result_manifest_bytes(manifest)
    )
    (result_root / "render-evaluation/decision.json").write_bytes(
        canonical_model_bytes(decision)
    )
    closure = derive_production_training_closure(
        training_bundle_sha256=(
            fixture.verified_bundle.bundle_sha256
        ),
        result_bundle_archive_sha256=(
            fixture.attempt.result_bundle_sha256
        ),
        manifest=manifest,
        attempt=fixture.attempt,
        request=fixture.request,
        result=fixture.result,
        runtime_measurement=runtime,
        runtime_policy=runtime_policy,
        runtime_decision=runtime_decision,
        render_policy=render_policy,
        render_report=report,
        render_decision=decision,
    )
    (
        result_root / "production-training-closure.json"
    ).write_bytes(
        canonical_production_training_closure_bytes(closure)
    )


def test_production_result_bundle_v2_builder_closes_runtime_and_render(
    tmp_path,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        include_production_closure=False,
    )
    _write_production_closure_evidence(training_root, fixture)
    result_root = training_root / "remote-result"
    measurement = load_production_runtime_measurement_bytes(
        (
            result_root
            / "production-runtime"
            / "measurement.json"
        ).read_bytes()
    )
    for relative in (
        "result-bundle-manifest.json",
        "result-bundle.zip",
        "render-evaluation/decision.json",
        "production-training-closure.json",
    ):
        (result_root / relative).unlink()
    archive = training_root / "result-bundle.zip"

    built = build_production_remote_result_bundle(
        result_root=result_root,
        output_path=archive,
        job_id=fixture.attempt.job_id,
        attempt_id=fixture.attempt.attempt_id,
        request_sha256=request_canonical_sha256(fixture.request),
        training_bundle_sha256=fixture.verified_bundle.bundle_sha256,
        container_instance_id=(
            measurement.environment.container_instance_id
        ),
        container_identity=(
            measurement.environment.observed_container_identity
        ),
        remote_target_sha256=measurement.remote_target_sha256,
        durable_job_ref_sha256=measurement.durable_job_ref_sha256,
        workspace_identity_sha256=(
            measurement.workspace_identity_sha256
        ),
    )
    verified = verify_production_remote_result_bundle(
        archive,
        expected_job_id=fixture.attempt.job_id,
        expected_attempt_id=fixture.attempt.attempt_id,
        expected_request_sha256=request_canonical_sha256(
            fixture.request
        ),
        expected_training_bundle_sha256=(
            fixture.verified_bundle.bundle_sha256
        ),
        expected_container_instance_id=(
            measurement.environment.container_instance_id
        ),
        expected_container_identity=(
            measurement.environment.observed_container_identity
        ),
        expected_remote_target_sha256=measurement.remote_target_sha256,
        expected_durable_job_ref_sha256=(
            measurement.durable_job_ref_sha256
        ),
        expected_workspace_identity_sha256=(
            measurement.workspace_identity_sha256
        ),
    )

    assert built.bundle_sha256 == verified.bundle_sha256
    assert inspect_remote_result_bundle_schema(archive) == (
        "nantai.remote-result-bundle.v2"
    )
    assert verified.manifest.schema_id == (
        "nantai.remote-result-bundle.v2"
    )
    assert verified.member_bytes[
        "production-runtime/decision.json"
    ] == (
        result_root / "production-runtime/decision.json"
    ).read_bytes()
    with pytest.raises(
        RemoteResultBundleError,
        match="runtime evidence",
    ):
        verify_production_remote_result_bundle(
            archive,
            expected_job_id=fixture.attempt.job_id,
            expected_attempt_id=fixture.attempt.attempt_id,
            expected_request_sha256=request_canonical_sha256(
                fixture.request
            ),
            expected_training_bundle_sha256=(
                fixture.verified_bundle.bundle_sha256
            ),
            expected_container_instance_id=(
                measurement.environment.container_instance_id
            ),
            expected_container_identity=(
                measurement.environment.observed_container_identity
            ),
            expected_remote_target_sha256=(
                measurement.remote_target_sha256
            ),
            expected_durable_job_ref_sha256="9" * 64,
            expected_workspace_identity_sha256=(
                measurement.workspace_identity_sha256
            ),
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
    monkeypatch.setattr(
        import_module,
        "load_training_job_evaluation_bytes",
        lambda bundle: fixture.evaluation_bytes,
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


def test_production_import_requires_g5_training_closure(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        include_production_closure=False,
    )
    _patch_production_bundle(monkeypatch, fixture)

    with pytest.raises(RealSceneImportError, match="production.*closure"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=_write_control_points(
                tmp_path / "control-points.json"
            ),
            geo_origin=(26.0, 119.0, 10.0),
        )


def test_production_import_rejects_invalid_g5_training_closure(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        include_production_closure=False,
    )
    _patch_production_bundle(monkeypatch, fixture)
    (
        training_root
        / "remote-result/production-training-closure.json"
    ).write_bytes(b"{}\n")

    with pytest.raises(RealSceneImportError, match="production.*closure"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=_write_control_points(
                tmp_path / "control-points.json"
            ),
            geo_origin=(26.0, 119.0, 10.0),
        )


def test_production_import_rejects_closure_for_another_job(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        include_production_closure=False,
    )
    _patch_production_bundle(monkeypatch, fixture)
    unrelated = production_closure_fixture()
    (
        training_root
        / "remote-result/production-training-closure.json"
    ).write_bytes(
        canonical_production_training_closure_bytes(
            derive_closure_fixture(unrelated)
        )
    )

    with pytest.raises(
        RealSceneImportError,
        match="production.*closure|job|attempt",
    ):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=_write_control_points(
                tmp_path / "control-points.json"
            ),
            geo_origin=(26.0, 119.0, 10.0),
        )


def test_production_import_rejects_identity_only_closure_without_raw_evidence(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        include_production_closure=False,
    )
    _patch_production_bundle(monkeypatch, fixture)
    base = derive_closure_fixture(production_closure_fixture())
    fields = base.model_dump(
        exclude={"closure_id", "content_sha256"},
    )
    fields.update(
        {
            "training_bundle_sha256": (
                fixture.verified_bundle.bundle_sha256
            ),
            "result_bundle_archive_sha256": (
                fixture.attempt.result_bundle_sha256
            ),
            "attempt_receipt_sha256": hashlib.sha256(
                canonical_model_bytes(fixture.attempt)
            ).hexdigest(),
            "request_sha256": request_canonical_sha256(
                fixture.request
            ),
            "result_sha256": result_canonical_sha256(fixture.result),
            "job_id": fixture.attempt.job_id,
            "attempt_id": fixture.attempt.attempt_id,
            "point_cloud_sha256": fixture.result.primary_ply_sha256,
            "gaussian_count": 100_000,
            "sh_degree": 0,
            "trainer_config_sha256": (
                fixture.result.actual_config_sha256
            ),
            "training_log_sha256": (
                fixture.result.training_log_sha256
            ),
            "dataparser_transform_sha256": next(
                binding.artifact_sha256
                for binding in fixture.result.output_bindings
                if binding.artifact_kind
                == "dataparser_transform_json"
            ),
        }
    )
    forged = ProductionTrainingClosure.create(**fields)
    (
        training_root
        / "remote-result/production-training-closure.json"
    ).write_bytes(
        canonical_production_training_closure_bytes(forged)
    )

    with pytest.raises(
        RealSceneImportError,
        match="runtime|manifest|raw|evidence",
    ):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=_write_control_points(
                tmp_path / "control-points.json"
            ),
            geo_origin=(26.0, 119.0, 10.0),
        )


def test_production_import_revalidates_runtime_evidence_bytes(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
    )
    _patch_production_bundle(monkeypatch, fixture)
    decision_path = (
        training_root
        / "remote-result/production-runtime/decision.json"
    )
    decision_path.write_bytes(decision_path.read_bytes() + b" ")

    with pytest.raises(
        RealSceneImportError,
        match="runtime|closure|canonical",
    ):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=_write_control_points(
                tmp_path / "control-points.json"
            ),
            geo_origin=(26.0, 119.0, 10.0),
        )


def test_production_import_rejects_content_closed_invalid_camera_record(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
        invalid_render_camera=True,
    )
    _patch_production_bundle(monkeypatch, fixture)

    with pytest.raises(RealSceneImportError, match="render|camera"):
        import_real_scene(
            training_root,
            tmp_path / "import",
            source_role="production-acceptance",
            control_points_path=_write_control_points(
                tmp_path / "control-points.json"
            ),
            geo_origin=(26.0, 119.0, 10.0),
        )


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
    assert receipt.schema_id == "nantai.real-scene-import-receipt.v3"
    assert receipt.production_training_closure_path == (
        "evidence/production-training-closure.json"
    )
    assert receipt.production_training_closure_sha256 is not None
    assert receipt.production_runtime_decision_sha256 is not None
    assert (
        output_root / receipt.production_training_closure_path
    ).is_file()
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
    runner = RealSceneRunner(
        source=RealSceneSourceIdentity(
            dataset_id="production-fixture",
            role="production-acceptance",
            source_sha256="a" * 64,
        ),
        workspace_base=tmp_path / "runner",
        operations=SimpleNamespace(),
    )
    assert runner._verify_production_import_output(
        stage_root=output_root,
        artifacts=tuple(
            path
            for path in output_root.rglob("*")
            if path.is_file()
        ),
        claimed_alignment_rms_m=receipt.alignment_rms_m,
    ) == pytest.approx(0.0, abs=1e-12)

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


# ============================================================
# RED → GREEN: manifest and contract stable read boundary
# ============================================================


def test_import_module_has_no_raw_read_text_or_read_bytes_in_trust_paths():
    """Trust-critical manifest and contract reads must use _read_regular_bytes."""
    import inspect

    source = inspect.getsource(import_module)
    # _read_regular_bytes itself is allowed to use os.open
    # but no other function should call Path.read_text or Path.read_bytes
    lines = source.splitlines()
    violations = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if (
            ".read_text(" in stripped or ".read_bytes(" in stripped
        ) and "def _read_regular_bytes" not in stripped:
            violations.append((lineno, stripped))
    assert not violations, (
        f"raw read_text/read_bytes found: {violations}"
    )


def test_read_regular_bytes_rejects_path_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The path lstat after reading must match the pre-open lstat."""
    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')
    original_lstat = Path.lstat
    evidence_calls = [0]

    def swapping_lstat(self):
        observed = original_lstat(self)
        if self == evidence:
            evidence_calls[0] += 1
            # first_linklike_path calls lstat once, then before.lstat,
            # then after.lstat — swap on the 3rd call (after read)
            if evidence_calls[0] >= 3:
                return SimpleNamespace(
                    st_dev=observed.st_dev,
                    st_ino=observed.st_ino + 1,
                    st_mode=observed.st_mode,
                    st_size=observed.st_size,
                    st_mtime_ns=observed.st_mtime_ns,
                    st_ctime_ns=observed.st_ctime_ns,
                    st_file_attributes=getattr(
                        observed, "st_file_attributes", 0
                    ),
                )
        return observed

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    with pytest.raises(
        RealSceneImportError, match="changed while being read"
    ):
        import_module._read_regular_bytes(
            evidence,
            label="test manifest",
        )


def test_read_regular_bytes_rejects_short_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A stream returning fewer bytes than st_size must be rejected."""
    evidence = tmp_path / "manifest.json"
    payload = b'{"valid":true}'
    evidence.write_bytes(payload)

    original_open = import_module.os.open

    def short_read_open(path, flags):
        fd = original_open(path, flags)
        original_fdopen = import_module.os.fdopen
        real_stream = original_fdopen(fd, "rb", buffering=0)

        class ShortStream:
            def __init__(self, stream):
                self._stream = stream
                self._done = False

            def read(self, size=-1):
                if self._done:
                    return b""
                self._done = True
                # Return one byte fewer than the actual file content
                data = self._stream.read(size)
                return data[:-1] if data else data

            def fileno(self):
                return self._stream.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._stream.close()

        def patched_fdopen(fd_arg, *a, **kw):
            if fd_arg == fd:
                return ShortStream(real_stream)
            return original_fdopen(fd_arg, *a, **kw)

        monkeypatch.setattr(import_module.os, "fdopen", patched_fdopen)
        return fd

    monkeypatch.setattr(import_module.os, "open", short_read_open)

    with pytest.raises(
        RealSceneImportError, match="changed while being read"
    ):
        import_module._read_regular_bytes(
            evidence,
            label="test manifest",
        )


def test_validate_receipt_rejects_manifest_drift_after_digest(
    tmp_path,
    monkeypatch,
):
    """RED->GREEN: manifest re-read after digest must match binding SHA.

    The digest loop proves the manifest SHA at read time using
    ``_stream_regular_digest``.  Subsequent re-reads via
    ``_read_regular_bytes`` must prove the SAME bytes are used, not just a
    file with the same stat signature.  This test tampers the manifest
    payload returned by ``_read_regular_bytes`` while leaving the on-disk
    file (and thus ``_stream_regular_digest``) untouched, proving that
    re-reads are now bound to the receipt artifact SHA.
    """
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

    original_read = import_module._read_regular_bytes

    def drifting_read(path, *, label, allow_empty=False):
        payload = original_read(
            path, label=label, allow_empty=allow_empty,
        )
        if Path(path).name == "recon_manifest.json":
            manifest = json.loads(payload)
            manifest["_drift_marker"] = True
            return json.dumps(manifest).encode("utf-8")
        return payload

    monkeypatch.setattr(
        import_module, "_read_regular_bytes", drifting_read,
    )

    with pytest.raises(
        RealSceneImportError,
        match="differs from receipt-bound bytes",
    ):
        import_module.validate_real_scene_import_receipt(
            output_root / "import-receipt.json",
            output_root,
        )
