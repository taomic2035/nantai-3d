"""Concrete source and SfM operations for the real-scene stage runner."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from pydantic import ValidationError

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
from pipeline.real_scene_capture import (
    PreparedRealCapture,
    RealSceneCaptureError,
    prepare_local_capture,
    prepare_real_capture,
    run_real_sfm,
)
from pipeline.real_scene_runner import (
    RealSceneRunOptions,
    StageExecution,
    StageName,
    StageReceipt,
)
from pipeline.registration_quality import RegistrationQualityPolicy
from pipeline.studio_revisions import (
    CaptureBundleError,
    verify_capture_bundle,
)

_ROOT = Path(__file__).resolve().parent.parent
_CANARY_POLICY = (
    _ROOT / "config/real-scene/poster-registration-policy.json"
)


def _regular_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise RealSceneCaptureError(
            "stage artifact boundary cannot be inspected"
        ) from exc
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise RealSceneCaptureError(
            "stage artifact boundary must be a real directory"
        )
    files: list[Path] = []

    def scan_error(error: OSError) -> None:
        raise RealSceneCaptureError(
            "stage artifact boundary cannot be enumerated"
        ) from error

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
                raise RealSceneCaptureError(
                    "stage artifact member cannot be inspected"
                ) from exc
            if stat.S_ISLNK(mode):
                raise RealSceneCaptureError(
                    "stage artifact boundary contains a link"
                )
            if stat.S_ISREG(mode):
                files.append(candidate)
            elif not stat.S_ISDIR(mode):
                raise RealSceneCaptureError(
                    "stage artifact boundary contains a non-regular member"
                )
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def _stage_root_for(
    workspace: Path,
    receipt: StageReceipt,
) -> Path:
    return workspace / "stages" / receipt.stage / receipt.attempt_id


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
                reason=(
                    "local-capture fetch requires MEDIA_ROOT and RIGHTS"
                ),
            )
        try:
            rights = load_capture_rights_receipt(
                self.options.rights_path
            )
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
                raise RealSceneCaptureError(
                    "production SfM requires an explicit POLICY"
                )
            policy_path = _CANARY_POLICY
        try:
            raw = Path(policy_path).read_bytes()
            policy = RegistrationQualityPolicy.model_validate_json(raw)
        except (OSError, ValidationError) as exc:
            raise RealSceneCaptureError(
                f"registration quality policy is invalid: {exc}"
            ) from exc
        stage_root.mkdir(parents=True, exist_ok=True)
        (stage_root / "registration-quality-policy.json").write_bytes(
            canonical_model_bytes(policy)
        )
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
            capture = verify_capture_bundle(
                fetch_root / "capture/bundle"
            )
        except CaptureBundleError as exc:
            raise RealSceneCaptureError(
                f"private capture bundle is invalid: {exc}"
            ) from exc
        return PreparedRealCapture(
            source_sha256=hashlib.sha256(
                canonical_model_bytes(self.source)
            ).hexdigest(),
            dataset_receipt_sha256=(
                capture.manifest.ingest_manifest_sha256
            ),
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
        return StageExecution(
            state="blocked",
            artifacts=(),
            reason=f"{stage} integration is not available before its task",
        )
