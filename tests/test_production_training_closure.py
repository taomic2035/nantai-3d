from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pipeline.production_runtime_evidence import (
    canonical_production_runtime_decision_bytes,
    canonical_production_runtime_measurement_bytes,
    canonical_production_runtime_policy_bytes,
    decide_production_runtime,
)
from pipeline.production_training_closure import (
    ProductionResultBundleManifestV2,
    ProductionResultMember,
    ProductionTrainingClosureError,
    canonical_production_result_manifest_bytes,
    canonical_production_training_closure_bytes,
    derive_production_training_closure,
    load_production_result_manifest_bytes,
    load_production_training_closure_bytes,
    verify_production_training_closure,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.render_evaluation import (
    RenderDecision,
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    RenderFrameMetric,
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
    build_training_result,
    request_canonical_sha256,
)
from tests.test_production_runtime_evidence import (
    _measurement as runtime_measurement,
)
from tests.test_production_runtime_evidence import _policy as runtime_policy


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _member(path: str, payload: bytes) -> ProductionResultMember:
    return ProductionResultMember(
        path=path,
        byte_length=len(payload),
        sha256=_sha(payload),
    )


def _fixture():
    config_bytes = b"trainer: nerfstudio-splatfacto\n"
    log_bytes = b"training completed\n"
    ply_bytes = b"ply-production-fixture\n"
    dataparser_bytes = b'{"scale":1.0,"transform":[[1,0,0,0]]}\n'
    input_bytes = b"registration\n"
    request = TrainingRequest(
        request_id="request-production",
        created_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        input_bindings=(
            TrainingInputBinding(
                artifact_kind="registration_json",
                artifact_sha256=_sha(input_bytes),
                artifact_path="training/registration.json",
                artifact_size_bytes=len(input_bytes),
            ),
        ),
        training_config=TrainingConfig(
            trainer_name="nerfstudio-splatfacto",
            trainer_version="1.1.5",
            max_resolution=2048,
            total_steps=30_000,
            random_seed=7,
        ),
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256=_sha(config_bytes),
    )
    result = build_training_result(
        request=request,
        result_id="result-production",
        started_at_utc=datetime(2026, 7, 27, 1, tzinfo=UTC),
        finished_at_utc=datetime(2026, 7, 27, 2, tzinfo=UTC),
        actual_trainer_name="nerfstudio-splatfacto",
        actual_trainer_version="1.1.5",
        actual_config_bytes=config_bytes,
        actual_ply_bytes=ply_bytes,
        actual_log_bytes=log_bytes,
        input_bytes_by_path={
            "training/registration.json": input_bytes,
        },
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
        gaussian_count=120_000,
        sh_degree=3,
        dataparser_transform_bytes=dataparser_bytes,
    )
    runtime = runtime_measurement()
    policy = runtime_policy(runtime)
    runtime_decision = decide_production_runtime(runtime, policy)
    transforms_bytes = b"transforms\n"
    camera_bytes = b"camera\n"
    render_bytes = b"render\n"
    protocol = RenderEvaluationProtocol(
        width=1280,
        height=720,
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
        held_out_split_sha256="1" * 64,
        transforms_sha256=_sha(transforms_bytes),
        evaluator_container_digest=(
            runtime.environment.observed_container_identity
        ),
        protocol=protocol,
        minimum_mean_psnr=24.0,
        minimum_mean_ssim=0.8,
        maximum_mean_lpips=0.25,
        minimum_worst_psnr=18.0,
    )
    frame = RenderFrameMetric(
        frame_id="held-out/frame.png",
        source_path="prepared/images/held-out/frame.png",
        source_byte_length=100,
        source_sha256="3" * 64,
        render_path=(
            "result/render-evaluation/renders/" + "4" * 64 + ".png"
        ),
        render_byte_length=len(render_bytes),
        render_sha256=_sha(render_bytes),
        camera_path=(
            "result/render-evaluation/cameras/" + "4" * 64 + ".json"
        ),
        camera_byte_length=len(camera_bytes),
        camera_sha256=_sha(camera_bytes),
        psnr=28.0,
        ssim=0.9,
        lpips=0.1,
    )
    render_report = RenderEvaluationReport(
        evaluation_id="evaluation-production",
        policy_sha256=render_evaluation_sha256(render_policy),
        held_out_split_sha256=render_policy.held_out_split_sha256,
        evaluator_container_digest=render_policy.evaluator_container_digest,
        protocol=protocol,
        frames=(frame,),
        trainer_config_sha256=_sha(config_bytes),
        mean_psnr=28.0,
        mean_ssim=0.9,
        mean_lpips=0.1,
        worst_psnr=28.0,
    )
    render_decision = RenderDecision(
        accepted=True,
        failed_thresholds=(),
        policy_sha256=render_evaluation_sha256(render_policy),
        report_sha256=render_evaluation_sha256(render_report),
        frame_count=1,
        mean_psnr=28.0,
        mean_ssim=0.9,
        mean_lpips=0.1,
        worst_psnr=28.0,
    )
    archive_sha = "7" * 64
    training_bundle_sha = "8" * 64
    attempt = new_attempt(
        ExecutorInputIdentity(
            executor_kind="remote-shell-nerfstudio",
            request_sha256=request_canonical_sha256(request),
            dataset_receipt_sha256="9" * 64,
            training_config_sha256=_sha(config_bytes),
            trainer_name="nerfstudio-splatfacto",
            trainer_version="1.1.5",
            job_id="job-production",
        ),
        attempt_id="attempt-production",
        created_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        quality_role="production",
    )
    attempt = advance_attempt(
        attempt,
        ExecutorObservation(
            state="running",
            observed_at_utc=(
                datetime(2026, 7, 27, tzinfo=UTC)
                + timedelta(minutes=1)
            ),
        ),
    )
    attempt = advance_attempt(
        attempt,
        ExecutorObservation(
            state="succeeded",
            observed_at_utc=(
                datetime(2026, 7, 27, tzinfo=UTC)
                + timedelta(hours=2)
            ),
            exit_code=0,
            stdout_sha256=_sha(b""),
            stderr_sha256=_sha(b""),
            result_bundle_sha256=archive_sha,
        ),
    )
    payloads = {
        "container-id.txt": (
            runtime.environment.container_instance_id + "\n"
        ).encode("ascii"),
        "container-identity.txt": (
            runtime.environment.observed_container_identity + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": dataparser_bytes,
        "operator-intent-config.yml": config_bytes,
        "point_cloud.ply": ply_bytes,
        "production-runtime/decision.json": (
            canonical_production_runtime_decision_bytes(runtime_decision)
        ),
        "production-runtime/measurement.json": (
            canonical_production_runtime_measurement_bytes(runtime)
        ),
        "production-runtime/policy.json": (
            canonical_production_runtime_policy_bytes(policy)
        ),
        "render-evaluation/cameras/" + "4" * 64 + ".json": camera_bytes,
        "render-evaluation/policy.json": canonical_model_bytes(
            render_policy
        ),
        "render-evaluation/renders/" + "4" * 64 + ".png": render_bytes,
        "render-evaluation/report.json": canonical_model_bytes(
            render_report
        ),
        "render-evaluation/trainer-config.yml": config_bytes,
        "render-evaluation/transforms.json": transforms_bytes,
        "training-request.json": canonical_model_bytes(request),
        "training-result.json": canonical_model_bytes(result),
        "training.log": log_bytes,
        "worker.stderr.log": b"",
        "worker.stdout.log": b"",
    }
    manifest = ProductionResultBundleManifestV2(
        job_id=attempt.job_id,
        attempt_id=attempt.attempt_id,
        request_sha256=request_canonical_sha256(request),
        training_bundle_sha256=training_bundle_sha,
        container_instance_id=runtime.environment.container_instance_id,
        container_identity=(
            runtime.environment.observed_container_identity
        ),
        runtime_measurement_artifact_sha256=_sha(
            payloads["production-runtime/measurement.json"]
        ),
        runtime_policy_artifact_sha256=_sha(
            payloads["production-runtime/policy.json"]
        ),
        runtime_decision_artifact_sha256=_sha(
            payloads["production-runtime/decision.json"]
        ),
        members=tuple(
            _member(path, payload)
            for path, payload in sorted(payloads.items())
        ),
    )
    return {
        "attempt": attempt,
        "request": request,
        "result": result,
        "runtime": runtime,
        "runtime_policy": policy,
        "runtime_decision": runtime_decision,
        "render_policy": render_policy,
        "render_report": render_report,
        "render_decision": render_decision,
        "manifest": manifest,
        "archive_sha": archive_sha,
        "training_bundle_sha": training_bundle_sha,
    }


def _derive(fixture, **updates):
    fields = {
        "training_bundle_sha256": fixture["training_bundle_sha"],
        "result_bundle_archive_sha256": fixture["archive_sha"],
        "manifest": fixture["manifest"],
        "attempt": fixture["attempt"],
        "request": fixture["request"],
        "result": fixture["result"],
        "runtime_measurement": fixture["runtime"],
        "runtime_policy": fixture["runtime_policy"],
        "runtime_decision": fixture["runtime_decision"],
        "render_policy": fixture["render_policy"],
        "render_report": fixture["render_report"],
        "render_decision": fixture["render_decision"],
    }
    fields.update(updates)
    return derive_production_training_closure(**fields)


def test_production_closure_binds_runtime_training_and_render_decisions():
    fixture = _fixture()
    closure = _derive(fixture)

    assert closure.status == "verified-production"
    assert closure.container_instance_id == (
        fixture["runtime"].environment.container_instance_id
    )
    assert closure.runtime_decision_sha256 == (
        fixture["runtime_decision"].content_sha256
    )
    assert closure.gaussian_count == 120_000
    payload = canonical_production_training_closure_bytes(closure)
    assert load_production_training_closure_bytes(payload) == closure
    verify_production_training_closure(
        closure=closure,
        training_bundle_sha256=fixture["training_bundle_sha"],
        result_bundle_archive_sha256=fixture["archive_sha"],
        manifest=fixture["manifest"],
        attempt=fixture["attempt"],
        request=fixture["request"],
        result=fixture["result"],
        runtime_measurement=fixture["runtime"],
        runtime_policy=fixture["runtime_policy"],
        runtime_decision=fixture["runtime_decision"],
        render_policy=fixture["render_policy"],
        render_report=fixture["render_report"],
        render_decision=fixture["render_decision"],
    )


def test_result_manifest_loader_requires_canonical_duplicate_free_json():
    manifest = _fixture()["manifest"]
    payload = canonical_production_result_manifest_bytes(manifest)
    duplicate = payload.replace(
        b'"schema":"nantai.remote-result-bundle.v2",',
        (
            b'"schema":"nantai.remote-result-bundle.v2",'
            b'"schema":"nantai.remote-result-bundle.v2",'
        ),
    )

    assert load_production_result_manifest_bytes(payload) == manifest
    with pytest.raises(
        ProductionTrainingClosureError,
        match="duplicate",
    ):
        load_production_result_manifest_bytes(duplicate)
    with pytest.raises(
        ProductionTrainingClosureError,
        match="noncanonical",
    ):
        load_production_result_manifest_bytes(payload.rstrip(b"\n"))


def test_manifest_requires_runtime_evidence_and_paired_render_payloads():
    fixture = _fixture()
    manifest = fixture["manifest"]
    members = tuple(
        member
        for member in manifest.members
        if member.path != "production-runtime/decision.json"
    )

    with pytest.raises(ValidationError, match="runtime|member"):
        ProductionResultBundleManifestV2.model_validate(
            manifest.model_copy(
                update={"members": members}
            ).model_dump(by_alias=True)
        )


def test_manifest_rejects_unpaired_render_and_unknown_extra_members():
    fixture = _fixture()
    manifest = fixture["manifest"]
    without_camera = tuple(
        member
        for member in manifest.members
        if not member.path.startswith("render-evaluation/cameras/")
    )
    with_extra = tuple(
        sorted(
            (
                *manifest.members,
                _member("debug/intermediate.bin", b"not publishable"),
            ),
            key=lambda member: member.path,
        )
    )

    for members in (without_camera, with_extra):
        with pytest.raises(ValidationError, match="paired|whitelist"):
            ProductionResultBundleManifestV2.model_validate(
                manifest.model_copy(
                    update={"members": members}
                ).model_dump(by_alias=True)
            )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("job_id", "job-other", "job"),
        ("attempt_id", "attempt-other", "attempt"),
        ("container_instance_id", "f" * 64, "container"),
        (
            "container_identity",
            "registry.example/other@sha256:" + "f" * 64,
            "container",
        ),
    ],
)
def test_closure_rejects_manifest_identity_drift(
    field,
    value,
    message,
):
    fixture = _fixture()
    manifest = fixture["manifest"].model_copy(update={field: value})

    with pytest.raises(ProductionTrainingClosureError, match=message):
        _derive(fixture, manifest=manifest)


def test_closure_rejects_runtime_decision_artifact_swap():
    fixture = _fixture()
    manifest = fixture["manifest"].model_copy(
        update={"runtime_decision_artifact_sha256": "f" * 64}
    )

    with pytest.raises(ProductionTrainingClosureError, match="runtime"):
        _derive(fixture, manifest=manifest)


def test_closure_rejects_runtime_vs_training_gpu_drift():
    fixture = _fixture()
    result = fixture["result"].model_copy(
        update={
            "gpu_environment": fixture[
                "result"
            ].gpu_environment.model_copy(
                update={"cuda_version": "12.7"}
            )
        }
    )

    with pytest.raises(ProductionTrainingClosureError, match="training"):
        _derive(fixture, result=result)


def test_closure_rejects_render_payload_swap():
    fixture = _fixture()
    manifest = fixture["manifest"]
    members = tuple(
        member.model_copy(update={"sha256": "f" * 64})
        if member.path.startswith("render-evaluation/renders/")
        else member
        for member in manifest.members
    )

    with pytest.raises(ProductionTrainingClosureError, match="render"):
        _derive(
            fixture,
            manifest=manifest.model_copy(update={"members": members}),
        )


def test_closure_rejects_low_gaussian_claim():
    fixture = _fixture()
    result = fixture["result"]
    outputs = tuple(
        binding.model_copy(update={"gaussian_count": 99_999})
        if binding.artifact_kind == "trained_ply"
        else binding
        for binding in result.output_bindings
    )
    result = result.model_copy(update={"output_bindings": outputs})

    with pytest.raises(
        ProductionTrainingClosureError,
        match="100000",
    ):
        _derive(fixture, result=result)


def test_closure_rejects_missing_dataparser_binding():
    fixture = _fixture()
    result = fixture["result"]
    result = result.model_copy(
        update={
            "output_bindings": tuple(
                binding
                for binding in result.output_bindings
                if binding.artifact_kind
                != "dataparser_transform_json"
            )
        }
    )

    with pytest.raises(
        ProductionTrainingClosureError,
        match="dataparser",
    ):
        _derive(fixture, result=result)


def test_closure_rejects_nonaccepted_render_decision():
    fixture = _fixture()
    rejected = fixture["render_decision"].model_copy(
        update={
            "accepted": False,
            "failed_thresholds": ("mean_psnr",),
        }
    )

    with pytest.raises(
        ProductionTrainingClosureError,
        match="render",
    ):
        _derive(fixture, render_decision=rejected)


def test_closure_verifier_rejects_runtime_policy_drift():
    fixture = _fixture()
    closure = _derive(fixture)
    changed_policy = runtime_policy(
        fixture["runtime"],
        min_gpu_memory_mib=32_768,
    )

    with pytest.raises(
        ProductionTrainingClosureError,
        match="runtime",
    ):
        verify_production_training_closure(
            closure=closure,
            training_bundle_sha256=fixture["training_bundle_sha"],
            result_bundle_archive_sha256=fixture["archive_sha"],
            manifest=fixture["manifest"],
            attempt=fixture["attempt"],
            request=fixture["request"],
            result=fixture["result"],
            runtime_measurement=fixture["runtime"],
            runtime_policy=changed_policy,
            runtime_decision=fixture["runtime_decision"],
            render_policy=fixture["render_policy"],
            render_report=fixture["render_report"],
            render_decision=fixture["render_decision"],
        )


def test_closure_loader_rejects_duplicate_and_noncanonical_json():
    fixture = _fixture()
    payload = canonical_production_training_closure_bytes(
        _derive(fixture)
    )
    duplicate = payload.replace(
        b'"status":"verified-production",',
        (
            b'"status":"verified-production",'
            b'"status":"verified-production",'
        ),
    )

    with pytest.raises(
        ProductionTrainingClosureError,
        match="duplicate",
    ):
        load_production_training_closure_bytes(duplicate)
    with pytest.raises(
        ProductionTrainingClosureError,
        match="noncanonical",
    ):
        load_production_training_closure_bytes(payload.rstrip(b"\n"))
