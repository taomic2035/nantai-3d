"""Concrete source and SfM operations for the real-scene stage runner."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pipeline.local_brush_executor import (
    LocalBrushExecutionError,
    LocalBrushExecutor,
    LocalBrushExecutorConfig,
)
from pipeline.real_dataset import (
    HfDatasetSource,
    LocalCaptureSource,
    RealDatasetSource,
    canonical_model_bytes,
    load_capture_rights_receipt,
)
from pipeline.real_dataset_fetch import (
    DatasetDownloadError,
    fetch_hf_dataset,
)
from pipeline.real_scene_acceptance import (
    HumanVisualReview,
    RealSceneAcceptance,
    RealSceneAcceptanceError,
    acceptance_directory_reference,
    acceptance_evidence_reference,
    canonical_real_scene_acceptance_bytes,
    publish_real_scene_acceptance,
    publish_real_scene_acceptance_pointer,
)
from pipeline.real_scene_capture import (
    PreparedRealCapture,
    RealSceneCaptureError,
    RealSfmResult,
    prepare_local_capture,
    prepare_real_capture,
    run_real_sfm,
)
from pipeline.real_scene_import import import_real_scene
from pipeline.real_scene_runner import (
    RealSceneRunOptions,
    StageExecution,
    StageName,
    StageReceipt,
    _read_receipt,
)
from pipeline.real_scene_training import (
    RealSceneTrainingError,
    VerifiedTrainingJobBundle,
    build_training_job_bundle,
    verify_production_training_job_bundle,
    verify_training_job_bundle,
)
from pipeline.recon_schema import RegistrationResult
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    RegistrationQualityReport,
    enumerate_sparse_models,
    validate_registration_quality,
)
from pipeline.remote_shell_executor import (
    RemoteResultBundleError,
    RemoteShellExecutionError,
    RemoteShellExecutor,
    RemoteShellExecutorConfig,
)
from pipeline.studio_revisions import (
    CaptureBundleError,
    canonical_manifest_bytes,
    verify_capture_bundle,
)
from pipeline.training_provenance import TrainingConfig

_ROOT = Path(__file__).resolve().parent.parent
_CANARY_POLICY = _ROOT / "config/real-scene/poster-registration-policy.json"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class PreparedCaptureEvidence(BaseModel):
    """Portable bridge from SfM output bytes back to capture identity."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    schema_id: Literal["nantai.prepared-capture-evidence.v1"] = Field(
        default="nantai.prepared-capture-evidence.v1",
        alias="schema",
        serialization_alias="schema",
    )
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    capture_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)


def _regular_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise RealSceneCaptureError("stage artifact boundary cannot be inspected") from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise RealSceneCaptureError("stage artifact boundary must be a real directory")
    files: list[Path] = []

    def scan_error(error: OSError) -> None:
        raise RealSceneCaptureError("stage artifact boundary cannot be enumerated") from error

    for directory, directory_names, file_names in os.walk(
        root,
        followlinks=False,
        onerror=scan_error,
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = parent / name
            try:
                mode = candidate.lstat().st_mode
            except OSError as exc:
                raise RealSceneCaptureError("stage artifact member cannot be inspected") from exc
            if stat.S_ISLNK(mode):
                raise RealSceneCaptureError("stage artifact boundary contains a link")
            if stat.S_ISREG(mode):
                files.append(candidate)
            elif not stat.S_ISDIR(mode):
                raise RealSceneCaptureError("stage artifact boundary contains a non-regular member")
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def _stage_root_for(
    workspace: Path,
    receipt: StageReceipt,
) -> Path:
    return workspace / "stages" / receipt.stage / receipt.attempt_id


def _training_config() -> TrainingConfig:
    return TrainingConfig(
        trainer_name="nerfstudio-splatfacto",
        trainer_version="1.1.5",
        max_resolution=1600,
        total_steps=30_000,
        export_every=5_000,
        random_seed=42,
        extra_config=(
            ("auto_scale_poses", "false"),
            ("center_method", "none"),
            ("orientation_method", "none"),
            ("scale_factor", "1.0"),
        ),
    )


def _find_runtime_binary(
    candidates: tuple[Path, ...],
    path_name: str,
) -> Path:
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which(path_name)
    if found is None:
        raise LocalBrushExecutionError(f"required runtime binary is missing: {path_name}")
    return Path(found).resolve()


def _brush_version(binary: Path) -> str:
    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            shell=False,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalBrushExecutionError("Brush version probe could not run") from exc
    output = completed.stdout or b""
    error = completed.stderr or b""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if isinstance(error, bytes):
        error = error.decode("utf-8", errors="replace")
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", output + error)
    if completed.returncode != 0 or match is None:
        raise LocalBrushExecutionError("Brush version probe did not prove an exact version")
    return match.group(1)


def _local_brush_config(stage_root: Path) -> LocalBrushExecutorConfig:
    brush = _find_runtime_binary(
        (
            _ROOT / "third/brush/brush_app",
            _ROOT / "third/brush/brush_app.exe",
        ),
        "brush_app",
    )
    colmap = _find_runtime_binary(
        (
            _ROOT / "third/colmap/bin/colmap",
            _ROOT / "third/colmap/bin/colmap.exe",
            _ROOT / "third/colmap/colmap",
            _ROOT / "third/colmap/colmap.exe",
        ),
        "colmap",
    )
    return LocalBrushExecutorConfig(
        execution_root=(stage_root / "local-brush").absolute(),
        python_executable=Path(sys.executable).absolute(),
        reconstruct_script=(_ROOT / "scripts/reconstruct_local.py").absolute(),
        colmap_binary=colmap,
        brush_binary=brush,
        trainer_version=_brush_version(brush),
        total_steps=1_000,
        max_resolution=1_024,
        random_seed=42,
        gpu_name=(f"{platform.system()} {platform.machine()} wgpu device (preview-only)"),
        gpu_memory_mb=0,
        driver_version=platform.platform(),
    )


class RealScenePipelineOperations:
    """Connect verified source preparation and COLMAP to stage receipts."""

    def __init__(
        self,
        *,
        source: RealDatasetSource,
        options: RealSceneRunOptions,
    ):
        self.source = source
        self.options = options

    def _fetch(
        self,
        stage_root: Path,
    ) -> StageExecution:
        if isinstance(self.source, HfDatasetSource):
            try:
                fetch_hf_dataset(self.source, stage_root)
                artifacts = _regular_files(stage_root)
            except (DatasetDownloadError, RealSceneCaptureError) as exc:
                return StageExecution(
                    state="unknown",
                    artifacts=(),
                    reason=f"dataset fetch is incomplete: {exc}",
                    evidence_artifacts=_regular_files(stage_root),
                )
            return StageExecution(
                state="completed",
                artifacts=artifacts,
            )

        if self.options.media_root is None or self.options.rights_path is None:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=("local-capture fetch requires MEDIA_ROOT and RIGHTS"),
            )
        try:
            rights = load_capture_rights_receipt(self.options.rights_path)
            prepare_local_capture(
                self.source,
                self.options.media_root,
                rights,
                stage_root,
            )
            rights_copy = stage_root / "capture-rights-receipt.json"
            rights_copy.write_bytes(canonical_model_bytes(rights))
            artifacts = _regular_files(stage_root)
        except (OSError, ValueError, RealSceneCaptureError) as exc:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"private capture preparation failed: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )
        return StageExecution(
            state="completed",
            artifacts=artifacts,
        )

    def _policy(self, stage_root: Path) -> RegistrationQualityPolicy:
        policy_path = self.options.policy_path
        if policy_path is None:
            if isinstance(self.source, LocalCaptureSource):
                raise RealSceneCaptureError("production SfM requires an explicit POLICY")
            policy_path = _CANARY_POLICY
        try:
            raw = Path(policy_path).read_bytes()
            policy = RegistrationQualityPolicy.model_validate_json(raw)
        except (OSError, ValidationError) as exc:
            raise RealSceneCaptureError(f"registration quality policy is invalid: {exc}") from exc
        stage_root.mkdir(parents=True, exist_ok=True)
        (stage_root / "registration-quality-policy.json").write_bytes(canonical_model_bytes(policy))
        return policy

    def _prepared_capture(
        self,
        fetch_root: Path,
        stage_root: Path,
    ) -> PreparedRealCapture:
        if isinstance(self.source, HfDatasetSource):
            return prepare_real_capture(
                self.source,
                fetch_root,
                stage_root,
            )
        try:
            capture = verify_capture_bundle(fetch_root / "capture/bundle")
        except CaptureBundleError as exc:
            raise RealSceneCaptureError(f"private capture bundle is invalid: {exc}") from exc
        return PreparedRealCapture(
            source_sha256=hashlib.sha256(canonical_model_bytes(self.source)).hexdigest(),
            dataset_receipt_sha256=(capture.manifest.ingest_manifest_sha256),
            selected_paths=(),
            capture=capture,
        )

    def _sfm(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
    ) -> StageExecution:
        if len(prerequisites) != 1 or prerequisites[0].stage != "fetch":
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason="SfM requires one verified fetch receipt",
            )
        workspace = stage_root.parents[2]
        fetch_root = _stage_root_for(workspace, prerequisites[0])
        try:
            policy = self._policy(stage_root)
            capture = self._prepared_capture(fetch_root, stage_root)
            result = run_real_sfm(capture, stage_root, policy)
            prepared_evidence = PreparedCaptureEvidence(
                source_sha256=capture.source_sha256,
                dataset_receipt_sha256=(capture.dataset_receipt_sha256),
                capture_manifest_sha256=(capture.capture.manifest_digest),
            )
            (stage_root / "prepared-capture-evidence.json").write_bytes(
                canonical_model_bytes(prepared_evidence)
            )
            artifacts = _regular_files(stage_root)
        except (OSError, ValueError, RealSceneCaptureError) as exc:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"SfM execution failed: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )
        if not result.quality.training_allowed:
            reasons = "; ".join(result.quality.rejection_reasons) or (
                "registration quality did not authorize training"
            )
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"SfM registration gate rejected: {reasons}",
                evidence_artifacts=artifacts,
            )
        return StageExecution(
            state="completed",
            artifacts=artifacts,
        )

    def _training_inputs(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
    ) -> tuple[
        PreparedRealCapture,
        RealSfmResult,
        RegistrationQualityPolicy,
    ]:
        if len(prerequisites) != 1 or prerequisites[0].stage != "sfm":
            raise RealSceneTrainingError("training requires one verified SfM receipt")
        workspace = stage_root.parents[2]
        sfm_root = _stage_root_for(workspace, prerequisites[0])
        try:
            capture = verify_capture_bundle(sfm_root / "capture/bundle")
            evidence_path = sfm_root / "prepared-capture-evidence.json"
            evidence_bytes = evidence_path.read_bytes()
            evidence = PreparedCaptureEvidence.model_validate_json(evidence_bytes)
            if evidence_bytes != canonical_model_bytes(evidence):
                raise RealSceneTrainingError("prepared capture evidence is not canonical")
            expected_source_sha = hashlib.sha256(canonical_model_bytes(self.source)).hexdigest()
            if (
                evidence.source_sha256 != expected_source_sha
                or evidence.capture_manifest_sha256 != capture.manifest_digest
            ):
                raise RealSceneTrainingError("prepared capture evidence identity mismatch")
            prepared = PreparedRealCapture(
                source_sha256=evidence.source_sha256,
                dataset_receipt_sha256=(evidence.dataset_receipt_sha256),
                selected_paths=(),
                capture=capture,
            )

            policy_bytes = (sfm_root / "registration-quality-policy.json").read_bytes()
            policy = RegistrationQualityPolicy.model_validate_json(policy_bytes)
            if policy_bytes != canonical_model_bytes(policy):
                raise RealSceneTrainingError("registration policy evidence is not canonical")

            registration_path = sfm_root / "sfm/registration.json"
            registration_bytes = registration_path.read_bytes()
            registration = RegistrationResult.model_validate_json(registration_bytes)
            quality_path = sfm_root / "sfm/registration-quality-report.json"
            quality_bytes = quality_path.read_bytes()
            quality = RegistrationQualityReport.model_validate_json(quality_bytes)
            enumeration = enumerate_sparse_models(
                sfm_root / "sfm/colmap/sparse",
                capture.manifest.output_count,
            )
            validate_registration_quality(
                quality,
                policy,
                registration_bytes,
                capture_manifest_bytes=canonical_manifest_bytes(capture.manifest),
                sparse_enumeration=enumeration,
            )
        except (
            CaptureBundleError,
            OSError,
            ValidationError,
            ValueError,
        ) as exc:
            if isinstance(exc, RealSceneTrainingError):
                raise
            raise RealSceneTrainingError(f"verified SfM inputs cannot be reopened: {exc}") from exc
        sfm = RealSfmResult(
            registration=registration,
            registration_path=registration_path,
            registration_sha256=hashlib.sha256(registration_bytes).hexdigest(),
            sparse_enumeration=enumeration,
            quality=quality,
            quality_path=quality_path,
            quality_sha256=hashlib.sha256(quality_bytes).hexdigest(),
        )
        return prepared, sfm, policy

    def _build_training_bundle(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
        *,
        production: bool,
    ) -> VerifiedTrainingJobBundle:
        capture, sfm, policy = self._training_inputs(
            stage_root,
            prerequisites,
        )
        stage_root.mkdir(parents=True, exist_ok=True)
        built = build_training_job_bundle(
            capture,
            sfm,
            _training_config(),
            stage_root / "training-bundle",
            policy=policy,
        )
        if production:
            return verify_production_training_job_bundle(built.path)
        return verify_training_job_bundle(built.path)

    def _train_preview(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
    ) -> StageExecution:
        try:
            bundle = self._build_training_bundle(
                stage_root,
                prerequisites,
                production=False,
            )
            result = LocalBrushExecutor(_local_brush_config(stage_root)).run(bundle)
            artifacts = _regular_files(stage_root)
            if result.receipt.quality_role != "preview-only":
                raise LocalBrushExecutionError("local Brush result was not preview-only")
        except (
            LocalBrushExecutionError,
            RealSceneCaptureError,
            RealSceneTrainingError,
            OSError,
            ValueError,
        ) as exc:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"local Brush preview failed: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )
        return StageExecution(
            state="completed",
            artifacts=artifacts,
        )

    def _remote_config(self) -> RemoteShellExecutorConfig:
        if self.options.remote_config_path is None:
            raise RemoteShellExecutionError("production training requires REMOTE_CONFIG")
        try:
            raw = self.options.remote_config_path.read_bytes()
            return RemoteShellExecutorConfig.model_validate_json(raw)
        except (OSError, ValidationError) as exc:
            raise RemoteShellExecutionError(f"remote executor config is invalid: {exc}") from exc

    @staticmethod
    def _write_private_model(path: Path, model: BaseModel) -> None:
        path.write_bytes(canonical_model_bytes(model))

    def _train_production(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
    ) -> StageExecution:
        try:
            bundle = self._build_training_bundle(
                stage_root,
                prerequisites,
                production=True,
            )
        except (OSError, ValueError, RealSceneTrainingError) as exc:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"production bundle preparation failed: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )
        try:
            config = self._remote_config()
            stage_root.mkdir(parents=True, exist_ok=True)
            public_config = {
                "container_identity": config.container_identity,
                "container_runtime": config.container_runtime,
                "expected_host_key_fingerprint": (config.expected_host_key_fingerprint),
            }
            (stage_root / "remote-executor-public-config.json").write_text(
                json.dumps(
                    public_config,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="ascii",
            )
            executor = RemoteShellExecutor(config)
            prepared = executor.prepare(bundle)
        except (OSError, ValidationError, RemoteShellExecutionError) as exc:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"remote executor preflight failed: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )
        try:
            job = executor.submit(prepared)
            self._write_private_model(
                stage_root / "remote-job.private.json",
                job,
            )
        except (OSError, ValidationError, RemoteShellExecutionError) as exc:
            return StageExecution(
                state="unknown",
                artifacts=(),
                reason=f"remote submission state is unknown: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )

        deadline = time.monotonic() + self.options.remote_timeout_seconds
        while True:
            try:
                observation = executor.poll(job)
            except (OSError, RemoteShellExecutionError) as exc:
                return StageExecution(
                    state="unknown",
                    artifacts=(),
                    reason=f"remote poll state is unknown: {exc}",
                    evidence_artifacts=_regular_files(stage_root),
                )
            self._write_private_model(
                stage_root / "remote-observation.private.json",
                observation,
            )
            if observation.state == "failed":
                return StageExecution(
                    state="blocked",
                    artifacts=(),
                    reason=(f"remote Splatfacto failed with exit code {observation.exit_code}"),
                    evidence_artifacts=_regular_files(stage_root),
                )
            if observation.state == "unknown":
                if observation.exit_code != 0:
                    return StageExecution(
                        state="unknown",
                        artifacts=(),
                        reason=("remote Splatfacto state is unknown; no success was inferred"),
                        evidence_artifacts=_regular_files(stage_root),
                    )
                try:
                    receipt = executor.fetch(
                        job,
                        stage_root / "remote-result",
                    )
                except RemoteResultBundleError as exc:
                    return StageExecution(
                        state="blocked",
                        artifacts=(),
                        reason=f"remote result failed validation: {exc}",
                        evidence_artifacts=_regular_files(stage_root),
                    )
                except (OSError, RemoteShellExecutionError) as exc:
                    return StageExecution(
                        state="unknown",
                        artifacts=(),
                        reason=(f"remote result closure is unknown: {exc}"),
                        evidence_artifacts=_regular_files(stage_root),
                    )
                if receipt.state != "succeeded" or receipt.quality_role != "production":
                    return StageExecution(
                        state="blocked",
                        artifacts=(),
                        reason=("remote result receipt is not succeeded production evidence"),
                        evidence_artifacts=_regular_files(stage_root),
                    )
                self._write_private_model(
                    stage_root / "executor-attempt.json",
                    receipt,
                )
                return StageExecution(
                    state="completed",
                    artifacts=_regular_files(stage_root),
                )
            if time.monotonic() >= deadline:
                return StageExecution(
                    state="unknown",
                    artifacts=(),
                    reason="remote Splatfacto polling timed out",
                    evidence_artifacts=_regular_files(stage_root),
                )
            time.sleep(self.options.remote_poll_interval_seconds)

    def _import(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
    ) -> StageExecution:
        allowed_training_stages = (
            {"train-production"}
            if isinstance(self.source, LocalCaptureSource)
            else {"train-preview", "train-production"}
        )
        if len(prerequisites) != 1 or prerequisites[0].stage not in allowed_training_stages:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason="import requires one verified training receipt",
            )
        workspace = stage_root.parents[2]
        training_root = _stage_root_for(workspace, prerequisites[0])
        try:
            receipt = import_real_scene(
                training_root,
                stage_root,
                source_role=self.source.role,
                control_points_path=self.options.control_points_path,
                geo_origin=self.options.geo_origin,
                chunk_size=self.options.chunk_size,
            )
            artifacts = _regular_files(stage_root)
        except (OSError, ValueError) as exc:
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"real-scene import failed: {exc}",
                evidence_artifacts=_regular_files(stage_root),
            )
        return StageExecution(
            state="completed",
            artifacts=artifacts,
            alignment_rms_m=receipt.alignment_rms_m,
        )

    @staticmethod
    def _prerequisite_receipt(
        workspace: Path,
        receipt: StageReceipt,
        expected_stage: StageName,
    ) -> StageReceipt:
        matches = tuple(
            binding for binding in receipt.prerequisites if binding.stage == expected_stage
        )
        if len(matches) != 1 or len(receipt.prerequisites) != 1:
            raise RealSceneAcceptanceError(
                f"{receipt.stage} does not bind exactly one {expected_stage} receipt"
            )
        binding = matches[0]
        path = workspace / "receipts" / expected_stage / f"{binding.receipt_sha256}.json"
        loaded, digest = _read_receipt(path)
        if (
            digest != binding.receipt_sha256
            or loaded.stage != expected_stage
            or loaded.dataset_id != receipt.dataset_id
            or loaded.source_sha256 != receipt.source_sha256
        ):
            raise RealSceneAcceptanceError(
                f"{expected_stage} prerequisite receipt identity differs"
            )
        return loaded

    def _acceptance_option_path(
        self,
        workspace: Path,
        configured: Path | None,
        default_name: str,
    ) -> Path:
        return (
            Path(configured).expanduser().absolute()
            if configured is not None
            else workspace / "evidence" / default_name
        )

    def _acceptance_external_files(
        self,
        workspace: Path,
        report: RealSceneAcceptance,
    ) -> tuple[Path, ...]:
        files = [
            workspace / report.viewer_policy.path,
            workspace / report.viewer_report.path,
            workspace / report.human_review_policy.path,
            workspace / report.human_visual_review.path,
        ]
        try:
            review = HumanVisualReview.model_validate_json(files[-1].read_bytes())
        except (OSError, ValidationError) as exc:
            raise RealSceneAcceptanceError(
                f"human review evidence cannot be reopened: {exc}"
            ) from exc
        for screenshot in review.screenshots:
            path = workspace.joinpath(*PurePosixPath(screenshot.path).parts)
            acceptance_evidence_reference(workspace, path)
            files.append(path)
        return tuple(files)

    def _accept(
        self,
        stage_root: Path,
        prerequisites: tuple[StageReceipt, ...],
    ) -> StageExecution:
        if len(prerequisites) != 1 or prerequisites[0].stage != "import":
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason="accept requires one verified import receipt",
            )
        workspace = stage_root.parents[2]
        import_receipt = prerequisites[0]
        failure_candidates: list[Path] = []
        try:
            training_receipt = self._prerequisite_receipt(
                workspace,
                import_receipt,
                "train-production",
            )
            sfm_receipt = self._prerequisite_receipt(
                workspace,
                training_receipt,
                "sfm",
            )
            fetch_receipt = self._prerequisite_receipt(
                workspace,
                sfm_receipt,
                "fetch",
            )
            fetch_root = _stage_root_for(workspace, fetch_receipt)
            sfm_root = _stage_root_for(workspace, sfm_receipt)
            training_root = _stage_root_for(
                workspace,
                training_receipt,
            )
            import_root = _stage_root_for(
                workspace,
                import_receipt,
            )
            capture_bundle = (
                sfm_root / "capture/bundle"
                if isinstance(self.source, HfDatasetSource)
                else fetch_root / "capture/bundle"
            )
            stage_root.mkdir(parents=True)
            source_path = stage_root / "source.json"
            source_path.write_bytes(canonical_model_bytes(self.source))
            failure_candidates.append(source_path)

            viewer_policy = self._acceptance_option_path(
                workspace,
                self.options.viewer_policy_path,
                "viewer-performance-policy.json",
            )
            viewer_report = self._acceptance_option_path(
                workspace,
                self.options.viewer_report_path,
                "viewer-performance-report.json",
            )
            human_policy = self._acceptance_option_path(
                workspace,
                self.options.human_review_policy_path,
                "human-review-policy.json",
            )
            human_review = self._acceptance_option_path(
                workspace,
                self.options.human_visual_review_path,
                "human-visual-review.json",
            )
            failure_candidates.extend(
                (
                    viewer_policy,
                    viewer_report,
                    human_policy,
                    human_review,
                )
            )
            report = RealSceneAcceptance(
                source_role=self.source.role,
                source=acceptance_evidence_reference(
                    workspace,
                    source_path,
                ),
                rights_receipt=(
                    acceptance_evidence_reference(
                        workspace,
                        fetch_root / "capture-rights-receipt.json",
                    )
                    if isinstance(self.source, LocalCaptureSource)
                    else None
                ),
                fetch_root=acceptance_directory_reference(
                    workspace,
                    fetch_root,
                ),
                dataset_lock=(
                    acceptance_evidence_reference(
                        workspace,
                        fetch_root / "dataset-lock.json",
                    )
                    if isinstance(self.source, HfDatasetSource)
                    else None
                ),
                dataset_receipt=(
                    acceptance_evidence_reference(
                        workspace,
                        fetch_root / "dataset-receipt.json",
                    )
                    if isinstance(self.source, HfDatasetSource)
                    else None
                ),
                capture_bundle=acceptance_directory_reference(
                    workspace,
                    capture_bundle,
                ),
                capture_manifest=acceptance_evidence_reference(
                    workspace,
                    capture_bundle / "manifest.json",
                ),
                prepared_capture_evidence=(
                    acceptance_evidence_reference(
                        workspace,
                        sfm_root / "prepared-capture-evidence.json",
                    )
                ),
                sfm_root=acceptance_directory_reference(
                    workspace,
                    sfm_root,
                ),
                registration=acceptance_evidence_reference(
                    workspace,
                    sfm_root / "sfm/registration.json",
                ),
                registration_policy=acceptance_evidence_reference(
                    workspace,
                    sfm_root / "registration-quality-policy.json",
                ),
                registration_report=acceptance_evidence_reference(
                    workspace,
                    sfm_root / "sfm/registration-quality-report.json",
                ),
                training_root=acceptance_directory_reference(
                    workspace,
                    training_root,
                ),
                training_bundle=acceptance_evidence_reference(
                    workspace,
                    training_root / "training-bundle/training-job.zip",
                ),
                import_root=acceptance_directory_reference(
                    workspace,
                    import_root,
                ),
                import_receipt=acceptance_evidence_reference(
                    workspace,
                    import_root / "import-receipt.json",
                ),
                render_root=acceptance_directory_reference(
                    workspace,
                    training_root / "remote-result",
                ),
                render_policy=acceptance_evidence_reference(
                    workspace,
                    training_root / "remote-result/render-evaluation/policy.json",
                ),
                render_report=acceptance_evidence_reference(
                    workspace,
                    training_root / "remote-result/render-evaluation/report.json",
                ),
                viewer_policy=acceptance_evidence_reference(
                    workspace,
                    viewer_policy,
                ),
                viewer_report=acceptance_evidence_reference(
                    workspace,
                    viewer_report,
                ),
                human_review_policy=acceptance_evidence_reference(
                    workspace,
                    human_policy,
                ),
                human_visual_review=acceptance_evidence_reference(
                    workspace,
                    human_review,
                ),
            )
            report_payload = canonical_real_scene_acceptance_bytes(report)
            failure_candidates.append(
                workspace
                / (f"real-scene-acceptance-{hashlib.sha256(report_payload).hexdigest()}.json")
            )
            published, decision = publish_real_scene_acceptance(
                report,
                workspace,
            )
            external = self._acceptance_external_files(
                workspace,
                report,
            )
            publish_real_scene_acceptance_pointer(
                published,
                Path(self.options.workspace_base).expanduser().absolute(),
            )
            evidence = (*external,)
            artifacts = (source_path, published)
        except (OSError, ValueError) as exc:
            evidence_paths = list(_regular_files(stage_root))
            for candidate in failure_candidates:
                try:
                    candidate.absolute().relative_to(workspace)
                except ValueError:
                    continue
                try:
                    mode = candidate.lstat().st_mode
                except OSError:
                    continue
                if (
                    not stat.S_ISLNK(mode)
                    and stat.S_ISREG(mode)
                    and candidate not in evidence_paths
                ):
                    evidence_paths.append(candidate)
            evidence = tuple(
                sorted(
                    evidence_paths,
                    key=lambda path: path.as_posix(),
                )
            )
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=f"real-scene acceptance failed: {exc}",
                evidence_artifacts=evidence,
            )
        accepted = (
            decision.canary_accepted
            if self.source.role == "internal-canary"
            else decision.production_release_allowed
        )
        if not accepted:
            reason = "; ".join(decision.reasons) or ("real-scene acceptance gates rejected")
            return StageExecution(
                state="blocked",
                artifacts=(),
                reason=reason,
                evidence_artifacts=(
                    *evidence,
                    *artifacts,
                ),
            )
        return StageExecution(
            state="completed",
            artifacts=artifacts,
            evidence_artifacts=evidence,
        )

    def execute(
        self,
        stage: StageName,
        stage_root: Path,
        prerequisite_receipts: tuple[StageReceipt, ...],
    ) -> StageExecution:
        if stage == "fetch":
            return self._fetch(stage_root)
        if stage == "sfm":
            return self._sfm(stage_root, prerequisite_receipts)
        if stage == "train-preview":
            return self._train_preview(
                stage_root,
                prerequisite_receipts,
            )
        if stage == "train-production":
            return self._train_production(
                stage_root,
                prerequisite_receipts,
            )
        if stage == "import":
            return self._import(stage_root, prerequisite_receipts)
        if stage == "accept":
            return self._accept(stage_root, prerequisite_receipts)
        return StageExecution(
            state="blocked",
            artifacts=(),
            reason=f"{stage} integration is not available before its task",
        )
