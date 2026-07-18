"""Durable L0 batch runner for the immutable 180-camera production plan."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from pipeline.studio_jobs import JobContractError, ProjectFileLock

from . import canary
from .local_textured_preview import (
    DEFAULT_LOCAL_BLENDER,
    LOCAL_TRAINING_BUILD_ENTRIES,
    LocalTexturedPreviewError,
    build_local_textured_preview_request,
    probe_local_blender_identity,
    verify_local_textured_training_build_directory,
)
from .production_profile import (
    ProductionCameraPlan,
    build_production_camera_plan,
)
from .production_render import (
    LocalProductionCameraMetadata,
    LocalProductionQualityPolicy,
    LocalProductionRenderFrameReport,
    LocalProductionRenderFrameRequest,
    LocalProductionRenderJournal,
    build_local_production_frame_request,
    canonical_local_production_camera_metadata_bytes,
    canonical_local_production_render_journal_bytes,
    canonical_local_production_render_report_bytes,
    canonical_local_production_render_request_bytes,
    compute_local_production_journal_sha256,
    evaluate_local_production_frame_quality,
    new_local_production_render_journal,
    transition_local_production_frame,
)
from .scene_plan import build_scene_plan

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_PRODUCTION_RENDER_ROOT = (
    ROOT / ".nantai-studio/synthetic-village/hybrid-v3/local-production-renders"
)


@dataclass(frozen=True)
class LocalProductionRenderResult:
    render_root: Path
    journal_path: Path
    render_id: str
    rendered_count: int
    rejected_count: int
    reused_count: int
    stdout: str
    stderr: str


def _load_journal(path: Path) -> LocalProductionRenderJournal:
    try:
        raw = canary._read_stable_metadata(path, label="local production journal")
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if canary._contains_private_path(parsed):
            raise LocalTexturedPreviewError(
                "local production journal contains a private path",
            )
        journal = LocalProductionRenderJournal.model_validate_json(raw)
        if raw != canonical_local_production_render_journal_bytes(journal):
            raise LocalTexturedPreviewError(
                "local production journal is not canonical JSON",
            )
        if journal.journal_sha256 != compute_local_production_journal_sha256(
            journal,
        ):
            raise LocalTexturedPreviewError(
                "local production journal SHA-256 is invalid",
            )
        return journal
    except LocalTexturedPreviewError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        raise LocalTexturedPreviewError(
            f"local production journal validation failed: {exc}",
        ) from exc


def _load_frame_report(path: Path) -> LocalProductionRenderFrameReport:
    try:
        raw = canary._read_stable_metadata(
            path,
            label="local production frame report",
        )
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        if canary._contains_private_path(parsed):
            raise LocalTexturedPreviewError(
                "local production frame report contains a private path",
            )
        report = LocalProductionRenderFrameReport.model_validate_json(raw)
        if raw != canonical_local_production_render_report_bytes(report):
            raise LocalTexturedPreviewError(
                "local production frame report is not canonical JSON",
            )
        expected = hashlib.sha256(
            canonical_local_production_render_report_bytes(
                report,
                exclude_sha256=True,
            ),
        ).hexdigest()
        if report.content_sha256 != expected:
            raise LocalTexturedPreviewError(
                "local production frame report SHA-256 is invalid",
            )
        return report
    except LocalTexturedPreviewError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        raise LocalTexturedPreviewError(
            f"local production frame report validation failed: {exc}",
        ) from exc


def _load_camera_metadata(path: Path) -> LocalProductionCameraMetadata:
    try:
        raw = canary._read_stable_metadata(
            path,
            label="local production camera metadata",
        )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        metadata = LocalProductionCameraMetadata.model_validate_json(raw)
        if raw != canonical_local_production_camera_metadata_bytes(metadata):
            raise LocalTexturedPreviewError(
                "local production camera metadata is not canonical JSON",
            )
        return metadata
    except LocalTexturedPreviewError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        raise LocalTexturedPreviewError(
            f"local production camera metadata validation failed: {exc}",
        ) from exc


def _validate_camera_metadata(
    metadata: LocalProductionCameraMetadata,
    request: LocalProductionRenderFrameRequest,
) -> None:
    camera = request.camera
    settings_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(request.settings.model_dump(mode="json")),
    ).hexdigest()
    immutable = (
        metadata.build_id,
        metadata.render_id,
        metadata.blender_executable_sha256,
        metadata.camera_id,
        metadata.settings_sha256,
        metadata.intrinsics,
        metadata.requested_c2w_opencv,
        metadata.requested_c2w_blender,
        metadata.object_registry_sha256,
        metadata.semantic_registry,
        metadata.profile_id,
        metadata.production_plan_sha256,
        metadata.camera_registry_sha256,
        metadata.elevated_topology_sha256,
        metadata.group_id,
        metadata.topology_ref,
        metadata.arc_length_m,
        metadata.audit_only,
        metadata.disclosure,
    )
    expected = (
        request.build_id,
        request.render_id,
        request.blender_executable_sha256,
        camera.camera_id,
        settings_sha256,
        camera.intrinsics,
        camera.c2w_opencv,
        request.requested_c2w_blender,
        request.object_registry_sha256,
        request.semantic_registry,
        request.profile_id,
        request.production_plan_sha256,
        request.camera_registry_sha256,
        request.elevated_topology_sha256,
        camera.group_id,
        camera.topology_ref,
        camera.arc_length_m,
        camera.audit_only,
        camera.disclosure,
    )
    if immutable != expected:
        raise LocalTexturedPreviewError(
            "local production camera metadata disagrees with its immutable request",
        )
    if not np.allclose(
        np.asarray(metadata.measured_c2w_blender),
        np.asarray(request.requested_c2w_blender),
        atol=4e-5,
        rtol=1.2e-7,
    ):
        raise LocalTexturedPreviewError(
            "local production measured camera pose diverged from its request",
        )
    expected_opencv = canary._blender_c2w_to_opencv(
        metadata.measured_c2w_blender,
    )
    if metadata.measured_c2w_opencv != expected_opencv:
        raise LocalTexturedPreviewError(
            "local production measured coordinate conversion is invalid",
        )


def _validate_frame_staging(
    staging: Path,
    request: LocalProductionRenderFrameRequest,
) -> tuple[LocalProductionRenderFrameReport, str]:
    try:
        staging = canary._require_real_directory(
            staging,
            label="local production frame staging",
        )
        report_path = staging / "frame-report.json"
        if not report_path.is_file() or canary._is_linklike(report_path):
            raise LocalTexturedPreviewError(
                "local production render has no trusted frame report",
            )
        report = _load_frame_report(report_path)
        expected_settings_sha256 = hashlib.sha256(
            canary._canonical_json_bytes(request.settings.model_dump(mode="json")),
        ).hexdigest()
        immutable = (
            report.build_id,
            report.render_id,
            report.blender_executable_sha256,
            report.camera_id,
            report.settings_sha256,
            report.profile_id,
            report.production_plan_sha256,
            report.camera_registry_sha256,
            report.elevated_topology_sha256,
            report.group_id,
            report.topology_ref,
        )
        expected = (
            request.build_id,
            request.render_id,
            request.blender_executable_sha256,
            request.camera.camera_id,
            expected_settings_sha256,
            request.profile_id,
            request.production_plan_sha256,
            request.camera_registry_sha256,
            request.elevated_topology_sha256,
            request.camera.group_id,
            request.camera.topology_ref,
        )
        if immutable != expected:
            raise LocalTexturedPreviewError(
                "local production frame report disagrees with its immutable request",
            )
        expected_entries = {
            "frame-report.json",
            *(row.path.split("/", 1)[0] for row in report.artifacts),
        }
        if {row.name for row in staging.iterdir()} != expected_entries:
            raise LocalTexturedPreviewError(
                "local production frame staging is incomplete or unregistered",
            )
        for artifact in report.artifacts:
            artifact_path = staging / Path(artifact.path)
            if artifact_path.parent.parent != staging:
                raise LocalTexturedPreviewError(
                    "local production artifact path escapes staging",
                )
            digest, size = canary._validate_frame_artifact_file(
                artifact_path,
                kind=artifact.kind,
            )
            if digest != artifact.sha256 or size != artifact.size_bytes:
                raise LocalTexturedPreviewError(
                    "local production artifact digest or size mismatch",
                )
        metadata = _load_camera_metadata(
            staging / f"cameras/{request.camera.camera_id}.json",
        )
        _validate_camera_metadata(metadata, request)
        return report, canary._sha256_file(report_path)
    except LocalTexturedPreviewError:
        raise
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc


def _verify_published_frame(
    render_root: Path,
    *,
    camera_id: str,
    artifacts: tuple,
) -> None:
    paths = canary._preflight_render_artifact_paths(render_root, camera_id)
    for artifact, path in zip(artifacts, paths, strict=True):
        digest, size = canary._validate_frame_artifact_file(
            path,
            kind=artifact.kind,
        )
        if digest != artifact.sha256 or size != artifact.size_bytes:
            raise LocalTexturedPreviewError(
                "published local production frame hash mismatch",
            )


def _normalize_camera_ids(
    plan: ProductionCameraPlan,
    camera_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    all_ids = tuple(row.camera_id for row in plan.cameras)
    if camera_ids is None:
        return all_ids
    if (
        type(camera_ids) is not tuple
        or not camera_ids
        or any(type(row) is not str for row in camera_ids)
        or len(set(camera_ids)) != len(camera_ids)
        or any(row not in all_ids for row in camera_ids)
    ):
        raise LocalTexturedPreviewError(
            "selected production camera IDs must be a unique plan subset",
        )
    selected = set(camera_ids)
    return tuple(row for row in all_ids if row in selected)


def run_local_production_render(
    *,
    training_build_directory: Path,
    material_bundle_root: Path,
    minimum_valid_pixel_ratio: float,
    repo_root: Path = ROOT,
    visual_pack_root: Path | None = None,
    executable: Path = DEFAULT_LOCAL_BLENDER,
    render_root: Path | None = None,
    camera_ids: tuple[str, ...] | None = None,
    timeout_seconds: int = canary.DEFAULT_RENDER_TIMEOUT_SECONDS,
) -> LocalProductionRenderResult:
    """Render a resumable L0 subset, retaining but rejecting low-valid-pixel frames."""

    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 24 * 60 * 60
    ):
        raise LocalTexturedPreviewError(
            "local production timeout must be an integer from 1 to 86400 seconds",
        )
    quality_policy = LocalProductionQualityPolicy(
        minimum_valid_pixel_ratio=minimum_valid_pixel_ratio,
    )
    try:
        repo_root = canary._require_real_directory(
            Path(repo_root).absolute(),
            label="repository root",
        )
        training_build_directory = canary._require_real_directory(
            Path(training_build_directory).absolute(),
            label="local production build directory",
        )
        private_root = repo_root / ".nantai-studio"
        training_build_directory.relative_to(private_root)
        bundle_root = canary._require_real_directory(
            Path(material_bundle_root).absolute(),
            label="material bundle root",
        )
        pack_root = canary._require_real_directory(
            Path(
                visual_pack_root
                or repo_root
                / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources",
            ).absolute(),
            label="visual source pack",
        )
    except (ValueError, canary.CanaryBuildError) as exc:
        raise LocalTexturedPreviewError(
            "local production inputs must be real private project paths",
        ) from exc

    executable = Path(executable).absolute()
    identity = probe_local_blender_identity(executable)
    scene = build_scene_plan()
    build_request = build_local_textured_preview_request(
        repo_root=repo_root,
        scene_plan=scene,
        visual_pack_root=pack_root,
        material_bundle_root=bundle_root,
        tool_identity=identity,
    )
    report, _audit, _manifest = verify_local_textured_training_build_directory(
        training_build_directory,
        request=build_request,
    )
    plan = build_production_camera_plan(scene, build_request.elevated_topology)
    selected_ids = _normalize_camera_ids(plan, camera_ids)
    try:
        executable_snapshot = canary._snapshot_regular_file(executable)
        renderer_snapshot = canary._snapshot_regular_file(
            repo_root / "scripts/blender/render_synthetic_village.py",
        )
        build_snapshots = tuple(
            canary._snapshot_regular_file(training_build_directory / name)
            for name in LOCAL_TRAINING_BUILD_ENTRIES
        )
    except canary.CanaryBuildError as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    report_path = training_build_directory / "build-report.json"
    report_snapshot = next(
        row for row in build_snapshots if row.path == report_path
    )
    if (
        executable_snapshot.sha256 != report.tool_identity.executable_sha256
        or report_snapshot.sha256 != training_build_directory.name
    ):
        raise LocalTexturedPreviewError(
            "local production runtime or build identity disagrees",
        )
    blend_record = next(
        row for row in report.artifacts if row.name == "village-canary.blend"
    )
    blend_path = training_build_directory / blend_record.name
    blend_snapshot = next(row for row in build_snapshots if row.path == blend_path)
    if (
        blend_snapshot.sha256 != blend_record.sha256
        or blend_snapshot.signature[2] != blend_record.size_bytes
    ):
        raise LocalTexturedPreviewError(
            "local production Blender scene disagrees with build evidence",
        )
    immutable_snapshots = (
        executable_snapshot,
        renderer_snapshot,
        *build_snapshots,
    )
    seed_request = build_local_production_frame_request(
        plan=plan,
        camera_id=plan.cameras[0].camera_id,
        build_id=report.preview_id,
        blender_executable_sha256=executable_snapshot.sha256,
        renderer_script_sha256=renderer_snapshot.sha256,
        blend_sha256=blend_snapshot.sha256,
        build_report_sha256=report_snapshot.sha256,
        object_registry=report.object_registry,
        auxiliary_registry=report.auxiliary_registry,
        semantic_registry=report.semantic_registry,
    )
    try:
        selected_render_root = (
            Path(render_root).absolute()
            if render_root is not None
            else DEFAULT_LOCAL_PRODUCTION_RENDER_ROOT
            / report_snapshot.sha256
            / seed_request.render_id
        )
        selected_render_root.relative_to(private_root)
        canary._ensure_real_directory_tree(private_root, repo_root=repo_root)
        selected_render_root = canary._ensure_real_directory_tree(
            selected_render_root,
            repo_root=repo_root,
        )
    except (ValueError, canary.CanaryBuildError) as exc:
        raise LocalTexturedPreviewError(
            "local production render root must remain below .nantai-studio",
        ) from exc

    journal_path = selected_render_root / "render-journal.json"
    rendered_count = 0
    rejected_count = 0
    reused_count = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    try:
        with ProjectFileLock(
            selected_render_root / ".local-production-render.lock",
            role="writer",
        ):
            if journal_path.exists() or canary._is_linklike(journal_path):
                journal = _load_journal(journal_path)
                immutable = (
                    journal.render_id,
                    journal.build_id,
                    journal.production_plan_sha256,
                    journal.camera_registry_sha256,
                    journal.elevated_topology_sha256,
                    journal.blender_executable_sha256,
                    journal.renderer_script_sha256,
                    journal.blend_sha256,
                    journal.build_report_sha256,
                    journal.object_registry_sha256,
                    journal.quality_policy,
                )
                expected = (
                    seed_request.render_id,
                    seed_request.build_id,
                    seed_request.production_plan_sha256,
                    seed_request.camera_registry_sha256,
                    seed_request.elevated_topology_sha256,
                    seed_request.blender_executable_sha256,
                    seed_request.renderer_script_sha256,
                    seed_request.blend_sha256,
                    seed_request.build_report_sha256,
                    seed_request.object_registry_sha256,
                    quality_policy,
                )
                if immutable != expected:
                    raise LocalTexturedPreviewError(
                        "existing local production journal belongs to different inputs",
                    )
            else:
                journal = new_local_production_render_journal(
                    seed_request,
                    quality_policy=quality_policy,
                    timeout_limit_seconds=timeout_seconds,
                )
                canary._write_render_journal(journal_path, journal)

            for camera_id in selected_ids:
                started = time.monotonic()
                nonce = uuid.uuid4().hex[:12]
                temporary_root = selected_render_root.parent
                invocation_root = temporary_root / f".lpri-{nonce}"
                staging = temporary_root / f".lprs-{nonce}"
                runtime_work = staging.with_name(
                    f".{staging.name}.tmp-{seed_request.render_id[:12]}",
                )
                try:
                    frame = next(
                        row for row in journal.frames if row.camera_id == camera_id
                    )
                    if frame.state in {"verified", "rejected"}:
                        try:
                            _verify_published_frame(
                                selected_render_root,
                                camera_id=camera_id,
                                artifacts=frame.artifacts,
                            )
                            reused_count += 1
                            if frame.state == "rejected":
                                rejected_count += 1
                            continue
                        except LocalTexturedPreviewError:
                            canary._quarantine_frame_outputs(
                                selected_render_root,
                                camera_id,
                            )
                    elif canary._frame_has_any_output(
                        selected_render_root,
                        camera_id,
                    ):
                        canary._quarantine_frame_outputs(
                            selected_render_root,
                            camera_id,
                        )
                    journal = transition_local_production_frame(
                        journal,
                        camera_id,
                        state="rendering",
                    )
                    canary._write_render_journal(journal_path, journal)

                    invocation_root.mkdir(exist_ok=False)
                    canary._flush_directory(temporary_root)
                    frame_request = build_local_production_frame_request(
                        plan=plan,
                        camera_id=camera_id,
                        build_id=report.preview_id,
                        blender_executable_sha256=executable_snapshot.sha256,
                        renderer_script_sha256=renderer_snapshot.sha256,
                        blend_sha256=blend_snapshot.sha256,
                        build_report_sha256=report_snapshot.sha256,
                        object_registry=report.object_registry,
                        auxiliary_registry=report.auxiliary_registry,
                        semantic_registry=report.semantic_registry,
                    )
                    request_path = invocation_root / "render-request.json"
                    canary._write_new_file(
                        request_path,
                        canonical_local_production_render_request_bytes(
                            frame_request,
                        ),
                    )
                    request_snapshot = canary._snapshot_regular_file(request_path)
                    try:
                        returncode, stdout, stderr = (
                            canary._run_blender_render_process(
                                repo_root=repo_root,
                                executable=executable_snapshot.path,
                                blend_path=blend_path,
                                request_path=request_path,
                                staging=staging,
                                invocation_root=invocation_root,
                                timeout_seconds=timeout_seconds,
                            )
                        )
                    finally:
                        canary._verify_snapshots_unchanged(
                            (*immutable_snapshots, request_snapshot),
                        )
                    duration = time.monotonic() - started
                    stdout_parts.append(stdout)
                    stderr_parts.append(stderr)
                    if returncode != 0:
                        raise LocalTexturedPreviewError(
                            f"local production Blender render failed with exit code {returncode}",
                        )
                    frame_report, frame_report_sha256 = _validate_frame_staging(
                        staging,
                        frame_request,
                    )
                    quality = evaluate_local_production_frame_quality(
                        frame_report.statistics,
                        policy=quality_policy,
                    )
                    canary._durably_flush_frame_staging(staging)
                    canary._publish_frame(
                        staging,
                        selected_render_root,
                        frame_report,
                    )
                    _verify_published_frame(
                        selected_render_root,
                        camera_id=camera_id,
                        artifacts=frame_report.artifacts,
                    )
                    canary._verify_snapshots_unchanged(immutable_snapshots)
                    state = "verified" if quality.passes else "rejected"
                    journal = transition_local_production_frame(
                        journal,
                        camera_id,
                        state=state,
                        artifacts=frame_report.artifacts,
                        runtime_report_sha256=frame_report_sha256,
                        statistics=frame_report.statistics,
                        quality=quality,
                        wall_clock_seconds=duration,
                    )
                    canary._write_render_journal(journal_path, journal)
                    rendered_count += 1
                    if state == "rejected":
                        rejected_count += 1
                except Exception as exc:
                    duration = time.monotonic() - started
                    error = (
                        exc
                        if isinstance(
                            exc,
                            (LocalTexturedPreviewError, canary.CanaryBuildError),
                        )
                        else LocalTexturedPreviewError(str(exc))
                    )
                    timed_out = "exceeded the" in str(error) and "timeout" in str(error)
                    if timed_out and duration < timeout_seconds:
                        duration = float(timeout_seconds)
                    journal = transition_local_production_frame(
                        journal,
                        camera_id,
                        state="timed-out" if timed_out else "failed",
                        wall_clock_seconds=duration,
                        error=canary._sanitize_render_error(error),
                    )
                    canary._write_render_journal(journal_path, journal)
                    if error is exc:
                        raise
                    raise error from exc
                finally:
                    for owned in (runtime_work, staging, invocation_root):
                        canary._cleanup_owned_directory(
                            owned,
                            work_root=temporary_root,
                            expected_name=owned.name,
                        )
    except LocalTexturedPreviewError:
        raise
    except (canary.CanaryBuildError, JobContractError) as exc:
        raise LocalTexturedPreviewError(str(exc)) from exc
    return LocalProductionRenderResult(
        render_root=selected_render_root,
        journal_path=journal_path,
        render_id=seed_request.render_id,
        rendered_count=rendered_count,
        rejected_count=rejected_count,
        reused_count=reused_count,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )
