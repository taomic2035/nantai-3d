"""Content-addressed closure for a verified production training result.

This module deliberately does not read an archive or parse PLY bytes.  The
remote result-bundle verifier, training provenance validator, dataparser
validator, and render evaluator remain the authorities for those raw bytes.
This contract binds their already-verified identities into one durable
production decision and rejects cross-job, cross-container, or replay drift.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.production_runtime_evidence import (
    ProductionRuntimeDecision,
    ProductionRuntimeMeasurement,
    ProductionRuntimePolicy,
    canonical_production_runtime_decision_bytes,
    canonical_production_runtime_measurement_bytes,
    canonical_production_runtime_policy_bytes,
    verify_production_runtime_decision,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.render_evaluation import (
    RenderDecision,
    RenderEvaluationPolicy,
    RenderEvaluationReport,
    render_evaluation_sha256,
)
from pipeline.training_executor import ExecutorAttemptReceipt
from pipeline.training_provenance import (
    TrainingOutputBinding,
    TrainingRequest,
    TrainingResult,
    request_canonical_sha256,
    result_canonical_sha256,
)


class ProductionTrainingClosureError(ValueError):
    """Production result identities are malformed or do not close."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_CONTAINER_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
_RENDER_STEM_PATTERN = r"^[0-9a-f]{64}$"

_FIXED_MEMBERS = frozenset(
    {
        "container-id.txt",
        "container-identity.txt",
        "dataparser_transforms.json",
        "operator-intent-config.yml",
        "point_cloud.ply",
        "production-runtime/decision.json",
        "production-runtime/measurement.json",
        "production-runtime/policy.json",
        "render-evaluation/policy.json",
        "render-evaluation/report.json",
        "render-evaluation/trainer-config.yml",
        "render-evaluation/transforms.json",
        "training-request.json",
        "training-result.json",
        "training.log",
        "worker.stderr.log",
        "worker.stdout.log",
    }
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _portable_member_path(value: str) -> str:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("result member path must be portable relative POSIX")
    return value


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


class ProductionResultMember(FrozenModel):
    path: str
    byte_length: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _portable_path = field_validator("path")(_portable_member_path)


class ProductionResultBundleManifestV2(FrozenModel):
    """Strict whitelist manifest emitted by a fresh production container."""

    schema_id: Literal["nantai.remote-result-bundle.v2"] = Field(
        default="nantai.remote-result-bundle.v2",
        alias="schema",
        serialization_alias="schema",
    )
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    container_instance_id: str = Field(pattern=_SHA256_PATTERN)
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    runtime_measurement_artifact_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    runtime_policy_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_decision_artifact_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    members: tuple[ProductionResultMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _members_are_an_exact_whitelist(
        self,
    ) -> ProductionResultBundleManifestV2:
        paths = tuple(member.path for member in self.members)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError(
                "result members must have sorted unique paths"
            )
        member_map = {member.path: member for member in self.members}
        missing = sorted(_FIXED_MEMBERS - member_map.keys())
        if missing:
            raise ValueError(
                "result member whitelist is missing: " + ", ".join(missing)
            )

        camera_stems: set[str] = set()
        render_stems: set[str] = set()
        for path in member_map.keys() - _FIXED_MEMBERS:
            parsed = PurePosixPath(path)
            if (
                len(parsed.parts) == 3
                and parsed.parts[:2]
                == ("render-evaluation", "cameras")
                and parsed.suffix == ".json"
            ):
                stem = parsed.stem
                camera_stems.add(stem)
            elif (
                len(parsed.parts) == 3
                and parsed.parts[:2]
                == ("render-evaluation", "renders")
                and parsed.suffix == ".png"
            ):
                stem = parsed.stem
                render_stems.add(stem)
            else:
                raise ValueError(
                    f"result member is outside the whitelist: {path}"
                )
            if (
                len(stem) != 64
                or any(character not in "0123456789abcdef" for character in stem)
            ):
                raise ValueError("render member stem must be lowercase SHA-256")
        if not camera_stems or camera_stems != render_stems:
            raise ValueError(
                "result members require paired camera and render payloads"
            )

        runtime_members = (
            (
                "production-runtime/measurement.json",
                self.runtime_measurement_artifact_sha256,
            ),
            (
                "production-runtime/policy.json",
                self.runtime_policy_artifact_sha256,
            ),
            (
                "production-runtime/decision.json",
                self.runtime_decision_artifact_sha256,
            ),
        )
        if any(
            member_map[path].sha256 != expected
            for path, expected in runtime_members
        ):
            raise ValueError(
                "runtime artifact SHA disagrees with result member"
            )
        return self


class ProductionTrainingClosure(FrozenModel):
    """One verified identity chain from request through held-out render."""

    schema_id: Literal["nantai.production-training-closure.v1"] = Field(
        default="nantai.production-training-closure.v1",
        alias="schema",
        serialization_alias="schema",
    )
    closure_id: str = Field(
        pattern=r"^production-training-closure-[0-9a-f]{64}$"
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["verified-production"]
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_bundle_archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_bundle_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    attempt_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_measurement_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    job_id: str = Field(pattern=_ID_PATTERN)
    attempt_id: str = Field(pattern=_ID_PATTERN)
    container_instance_id: str = Field(pattern=_SHA256_PATTERN)
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    point_cloud_sha256: str = Field(pattern=_SHA256_PATTERN)
    gaussian_count: int = Field(ge=100_000)
    sh_degree: int = Field(ge=0)
    trainer_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_log_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataparser_transform_sha256: str = Field(pattern=_SHA256_PATTERN)
    held_out_frame_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _identity_is_content_addressed(self) -> ProductionTrainingClosure:
        expected = _closure_content_sha256(self)
        if self.content_sha256 != expected:
            raise ValueError("production closure content SHA disagrees")
        if (
            self.closure_id
            != f"production-training-closure-{expected}"
        ):
            raise ValueError("production closure id disagrees")
        return self

    @classmethod
    def create(cls, **fields: Any) -> ProductionTrainingClosure:
        zero = "0" * 64
        provisional = cls.model_construct(
            closure_id=f"production-training-closure-{zero}",
            content_sha256=zero,
            **fields,
        )
        digest = _closure_content_sha256(provisional)
        return cls(
            closure_id=f"production-training-closure-{digest}",
            content_sha256=digest,
            **fields,
        )


def _closure_content_sha256(closure: ProductionTrainingClosure) -> str:
    return _sha256(
        _canonical_json_bytes(
            closure.model_dump(
                mode="json",
                by_alias=True,
                exclude={"closure_id", "content_sha256"},
            )
        )
    )


def canonical_production_result_manifest_bytes(
    manifest: ProductionResultBundleManifestV2,
) -> bytes:
    return _canonical_json_bytes(
        manifest.model_dump(mode="json", by_alias=True)
    )


def canonical_production_training_closure_bytes(
    closure: ProductionTrainingClosure,
) -> bytes:
    return _canonical_json_bytes(
        closure.model_dump(mode="json", by_alias=True)
    )


def load_production_result_manifest_bytes(
    payload: bytes,
) -> ProductionResultBundleManifestV2:
    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        manifest = ProductionResultBundleManifestV2.model_validate_json(
            payload
        )
    except ProductionTrainingClosureError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ProductionTrainingClosureError(
            "production result manifest is invalid"
        ) from exc
    if canonical_production_result_manifest_bytes(manifest) != payload:
        raise ProductionTrainingClosureError(
            "production result manifest bytes are noncanonical"
        )
    return manifest


def _reject_duplicate_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionTrainingClosureError(
                "production closure has duplicate JSON keys"
            )
        result[key] = value
    return result


def load_production_training_closure_bytes(
    payload: bytes,
) -> ProductionTrainingClosure:
    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        closure = ProductionTrainingClosure.model_validate_json(payload)
    except ProductionTrainingClosureError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ProductionTrainingClosureError(
            "production closure is invalid"
        ) from exc
    if canonical_production_training_closure_bytes(closure) != payload:
        raise ProductionTrainingClosureError(
            "production closure bytes are noncanonical"
        )
    return closure


def _member_map(
    manifest: ProductionResultBundleManifestV2,
) -> dict[str, ProductionResultMember]:
    return {member.path: member for member in manifest.members}


def _only_binding(
    result: TrainingResult,
    artifact_kind: str,
) -> TrainingOutputBinding:
    bindings = tuple(
        binding
        for binding in result.output_bindings
        if binding.artifact_kind == artifact_kind
    )
    if len(bindings) != 1:
        raise ProductionTrainingClosureError(
            f"production result requires exactly one {artifact_kind} binding"
        )
    return bindings[0]


def _require_member_binding(
    *,
    members: dict[str, ProductionResultMember],
    path: str,
    binding: TrainingOutputBinding,
    label: str,
) -> None:
    member = members[path]
    if (
        member.sha256 != binding.artifact_sha256
        or member.byte_length != binding.artifact_size_bytes
    ):
        raise ProductionTrainingClosureError(
            f"{label} result member disagrees with training binding"
        )


def _validate_render_closure(
    *,
    members: dict[str, ProductionResultMember],
    runtime_measurement: ProductionRuntimeMeasurement,
    result: TrainingResult,
    render_policy: RenderEvaluationPolicy,
    render_report: RenderEvaluationReport,
    render_decision: RenderDecision,
) -> None:
    if not render_decision.accepted or render_decision.failed_thresholds:
        raise ProductionTrainingClosureError(
            "render decision is not accepted"
        )
    policy_sha = render_evaluation_sha256(render_policy)
    report_sha = render_evaluation_sha256(render_report)
    if (
        render_report.policy_sha256 != policy_sha
        or render_decision.policy_sha256 != policy_sha
        or render_decision.report_sha256 != report_sha
    ):
        raise ProductionTrainingClosureError(
            "render policy/report decision identity disagrees"
        )
    container_identity = (
        runtime_measurement.environment.observed_container_identity
    )
    if (
        render_policy.evaluator_container_digest != container_identity
        or render_report.evaluator_container_digest != container_identity
    ):
        raise ProductionTrainingClosureError(
            "render container identity differs from training runtime"
        )
    if (
        render_policy.held_out_split_sha256
        != render_report.held_out_split_sha256
        or render_policy.protocol != render_report.protocol
    ):
        raise ProductionTrainingClosureError(
            "render report differs from evaluation policy"
        )
    metrics = (
        math.fsum(frame.psnr for frame in render_report.frames)
        / len(render_report.frames),
        math.fsum(frame.ssim for frame in render_report.frames)
        / len(render_report.frames),
        math.fsum(frame.lpips for frame in render_report.frames)
        / len(render_report.frames),
        min(frame.psnr for frame in render_report.frames),
    )
    reported = (
        render_report.mean_psnr,
        render_report.mean_ssim,
        render_report.mean_lpips,
        render_report.worst_psnr,
    )
    decision_metrics = (
        render_decision.mean_psnr,
        render_decision.mean_ssim,
        render_decision.mean_lpips,
        render_decision.worst_psnr,
    )
    if (
        metrics != reported
        or decision_metrics != metrics
        or render_decision.frame_count != len(render_report.frames)
    ):
        raise ProductionTrainingClosureError(
            "render aggregate metrics or frame count disagree"
        )
    if (
        metrics[0] < render_policy.minimum_mean_psnr
        or metrics[1] < render_policy.minimum_mean_ssim
        or metrics[2] > render_policy.maximum_mean_lpips
        or metrics[3] < render_policy.minimum_worst_psnr
    ):
        raise ProductionTrainingClosureError(
            "render metrics do not satisfy policy"
        )

    config = _only_binding(result, "training_config_yml")
    if (
        render_report.trainer_config_sha256
        != config.artifact_sha256
    ):
        raise ProductionTrainingClosureError(
            "render trainer config differs from training result"
        )
    policy_payload = canonical_model_bytes(render_policy)
    report_payload = canonical_model_bytes(render_report)
    expected_artifacts = (
        (
            "render-evaluation/policy.json",
            _sha256(policy_payload),
            len(policy_payload),
        ),
        (
            "render-evaluation/report.json",
            _sha256(report_payload),
            len(report_payload),
        ),
        (
            "render-evaluation/trainer-config.yml",
            config.artifact_sha256,
            config.artifact_size_bytes,
        ),
    )
    if any(
        members[path].sha256 != expected
        or members[path].byte_length != expected_length
        for path, expected, expected_length in expected_artifacts
    ) or (
        members["render-evaluation/transforms.json"].sha256
        != render_policy.transforms_sha256
    ):
        raise ProductionTrainingClosureError(
            "render result member identity disagrees"
        )
    for frame in render_report.frames:
        render_path = frame.render_path.removeprefix("result/")
        camera_path = frame.camera_path.removeprefix("result/")
        if (
            render_path not in members
            or camera_path not in members
            or members[render_path].sha256 != frame.render_sha256
            or members[render_path].byte_length
            != frame.render_byte_length
            or members[camera_path].sha256 != frame.camera_sha256
            or members[camera_path].byte_length
            != frame.camera_byte_length
        ):
            raise ProductionTrainingClosureError(
                "render frame payload binding disagrees"
            )


def derive_production_training_closure(
    *,
    training_bundle_sha256: str,
    result_bundle_archive_sha256: str,
    manifest: ProductionResultBundleManifestV2,
    attempt: ExecutorAttemptReceipt,
    request: TrainingRequest,
    result: TrainingResult,
    runtime_measurement: ProductionRuntimeMeasurement,
    runtime_policy: ProductionRuntimePolicy,
    runtime_decision: ProductionRuntimeDecision,
    render_policy: RenderEvaluationPolicy,
    render_report: RenderEvaluationReport,
    render_decision: RenderDecision,
) -> ProductionTrainingClosure:
    """Re-derive a production closure from independently validated evidence."""

    try:
        verify_production_runtime_decision(
            measurement=runtime_measurement,
            policy=runtime_policy,
            decision=runtime_decision,
        )
    except ValueError as exc:
        raise ProductionTrainingClosureError(
            "runtime decision verification failed"
        ) from exc
    if runtime_decision.status != "accepted":
        raise ProductionTrainingClosureError(
            "runtime decision is not accepted"
        )
    environment = runtime_measurement.environment
    if environment.kind != "fresh-job-container":
        raise ProductionTrainingClosureError(
            "runtime is not a fresh job container"
        )

    request_sha = request_canonical_sha256(request)
    result_sha = result_canonical_sha256(result)
    if manifest.job_id != attempt.job_id:
        raise ProductionTrainingClosureError("manifest job identity drift")
    if manifest.attempt_id != attempt.attempt_id:
        raise ProductionTrainingClosureError(
            "manifest attempt identity drift"
        )
    if manifest.request_sha256 != request_sha:
        raise ProductionTrainingClosureError(
            "manifest request identity drift"
        )
    if manifest.training_bundle_sha256 != training_bundle_sha256:
        raise ProductionTrainingClosureError(
            "manifest training bundle identity drift"
        )
    if manifest.container_instance_id != environment.container_instance_id:
        raise ProductionTrainingClosureError(
            "manifest container instance identity drift"
        )
    if (
        manifest.container_identity
        != environment.observed_container_identity
    ):
        raise ProductionTrainingClosureError(
            "manifest container image identity drift"
        )

    runtime_payloads = (
        (
            "production-runtime/measurement.json",
            canonical_production_runtime_measurement_bytes(
                runtime_measurement
            ),
            manifest.runtime_measurement_artifact_sha256,
        ),
        (
            "production-runtime/policy.json",
            canonical_production_runtime_policy_bytes(runtime_policy),
            manifest.runtime_policy_artifact_sha256,
        ),
        (
            "production-runtime/decision.json",
            canonical_production_runtime_decision_bytes(
                runtime_decision
            ),
            manifest.runtime_decision_artifact_sha256,
        ),
    )
    members = _member_map(manifest)
    if any(
        members[path].sha256 != _sha256(payload)
        or members[path].byte_length != len(payload)
        or artifact_sha != _sha256(payload)
        for path, payload, artifact_sha in runtime_payloads
    ):
        raise ProductionTrainingClosureError(
            "runtime artifact identity disagrees"
        )

    if (
        attempt.executor_kind != "remote-shell-nerfstudio"
        or attempt.quality_role != "production"
        or attempt.state != "succeeded"
        or attempt.exit_code != 0
        or attempt.result_bundle_sha256 != result_bundle_archive_sha256
        or attempt.request_sha256 != request_sha
        or attempt.training_config_sha256
        != request.requested_config_sha256
        or attempt.trainer_name != "nerfstudio-splatfacto"
        or attempt.trainer_version != request.training_config.trainer_version
    ):
        raise ProductionTrainingClosureError(
            "production executor attempt does not close"
        )
    if (
        request.training_config.trainer_name
        != "nerfstudio-splatfacto"
        or result.request_canonical_sha256 != request_sha
        or result.training_status.state != "completed"
        or result.training_status.exit_code != 0
        or result.actual_trainer_name
        != request.training_config.trainer_name
        or result.actual_trainer_version
        != request.training_config.trainer_version
        or result.trainer_drift is not None
        or result.gpu_environment.gpu_name
        != runtime_measurement.gpu.name
        or result.gpu_environment.gpu_memory_mb
        != runtime_measurement.gpu.memory_total_mib
        or result.gpu_environment.cuda_version
        != runtime_measurement.gpu.cuda_runtime_version
        or result.gpu_environment.driver_version
        != runtime_measurement.gpu.driver_version
    ):
        raise ProductionTrainingClosureError(
            "production training request/result does not close"
        )

    ply = _only_binding(result, "trained_ply")
    config = _only_binding(result, "training_config_yml")
    log = _only_binding(result, "training_log")
    try:
        dataparser = _only_binding(
            result,
            "dataparser_transform_json",
        )
    except ProductionTrainingClosureError as exc:
        raise ProductionTrainingClosureError(
            "production result requires one dataparser binding"
        ) from exc
    if ply.gaussian_count is None or ply.gaussian_count < 100_000:
        raise ProductionTrainingClosureError(
            "production result requires at least 100000 Gaussians"
        )
    if ply.sh_degree is None:
        raise ProductionTrainingClosureError(
            "production result requires an SH degree"
        )
    if (
        ply.artifact_sha256 != result.primary_ply_sha256
        or ply.artifact_size_bytes != result.primary_ply_size_bytes
        or config.artifact_sha256 != result.actual_config_sha256
        or log.artifact_sha256 != result.training_log_sha256
    ):
        raise ProductionTrainingClosureError(
            "training result output identity disagrees"
        )
    for path, binding, label in (
        ("point_cloud.ply", ply, "point cloud"),
        ("operator-intent-config.yml", config, "trainer config"),
        ("training.log", log, "training log"),
        (
            "dataparser_transforms.json",
            dataparser,
            "dataparser",
        ),
    ):
        _require_member_binding(
            members=members,
            path=path,
            binding=binding,
            label=label,
        )
    canonical_request = canonical_model_bytes(request)
    canonical_result = canonical_model_bytes(result)
    if (
        members["training-request.json"].sha256
        != _sha256(canonical_request)
        or members["training-request.json"].byte_length
        != len(canonical_request)
        or members["training-result.json"].sha256
        != _sha256(canonical_result)
        or members["training-result.json"].byte_length
        != len(canonical_result)
    ):
        raise ProductionTrainingClosureError(
            "training request/result member identity disagrees"
        )
    container_payloads = (
        (
            "container-id.txt",
            (environment.container_instance_id + "\n").encode("ascii"),
        ),
        (
            "container-identity.txt",
            (
                environment.observed_container_identity + "\n"
            ).encode("ascii"),
        ),
    )
    if any(
        members[path].sha256 != _sha256(payload)
        or members[path].byte_length != len(payload)
        for path, payload in container_payloads
    ):
        raise ProductionTrainingClosureError(
            "container identity member disagrees"
        )
    if (
        members["worker.stdout.log"].sha256 != attempt.stdout_sha256
        or members["worker.stderr.log"].sha256 != attempt.stderr_sha256
    ):
        raise ProductionTrainingClosureError(
            "worker log identity differs from executor attempt"
        )

    _validate_render_closure(
        members=members,
        runtime_measurement=runtime_measurement,
        result=result,
        render_policy=render_policy,
        render_report=render_report,
        render_decision=render_decision,
    )
    return ProductionTrainingClosure.create(
        status="verified-production",
        training_bundle_sha256=training_bundle_sha256,
        result_bundle_archive_sha256=result_bundle_archive_sha256,
        result_bundle_manifest_sha256=_sha256(
            canonical_production_result_manifest_bytes(manifest)
        ),
        attempt_receipt_sha256=_sha256(
            canonical_model_bytes(attempt)
        ),
        request_sha256=request_sha,
        result_sha256=result_sha,
        runtime_measurement_sha256=runtime_measurement.content_sha256,
        runtime_policy_sha256=runtime_policy.content_sha256,
        runtime_decision_sha256=runtime_decision.content_sha256,
        render_policy_sha256=render_evaluation_sha256(render_policy),
        render_report_sha256=render_evaluation_sha256(render_report),
        render_decision_sha256=_sha256(
            canonical_model_bytes(render_decision)
        ),
        job_id=attempt.job_id,
        attempt_id=attempt.attempt_id,
        container_instance_id=environment.container_instance_id,
        container_identity=environment.observed_container_identity,
        point_cloud_sha256=ply.artifact_sha256,
        gaussian_count=ply.gaussian_count,
        sh_degree=ply.sh_degree,
        trainer_config_sha256=config.artifact_sha256,
        training_log_sha256=log.artifact_sha256,
        dataparser_transform_sha256=dataparser.artifact_sha256,
        held_out_frame_count=len(render_report.frames),
    )


def verify_production_training_closure(
    *,
    closure: ProductionTrainingClosure,
    **evidence: Any,
) -> None:
    """Re-derive the closure and reject any policy or evidence drift."""

    expected = derive_production_training_closure(**evidence)
    if expected != closure:
        raise ProductionTrainingClosureError(
            "production training closure disagrees with evidence"
        )
