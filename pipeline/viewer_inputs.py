"""Materialize production Viewer inputs from one verified metric import."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from pipeline import real_scene_import
from pipeline.durable_io import (
    DurableIOError,
    flush_directory,
    flush_file,
    publish_directory_noreplace,
)
from pipeline.real_scene_import import RealSceneImportReceipt
from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CoordinateUnits,
    GeoAlignment,
    MetricStatus,
    RegistrationResult,
)
from pipeline.viewer_acceptance import (
    ViewerCameraPose,
    ViewerCameraSetV2,
    ViewerPerformancePolicy,
    canonical_viewer_camera_set_bytes,
    canonical_viewer_performance_policy_bytes,
    viewer_camera_pose_id,
)


class ViewerInputMaterializationError(ValueError):
    """Production Viewer inputs cannot be proven from the selected import."""


@dataclass(frozen=True)
class ViewerInputMaterialization:
    camera_set_path: Path
    policy_path: Path
    camera_set_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class _WorldCamera:
    identity: str
    position: np.ndarray
    forward: np.ndarray


def _identity(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _read_regular_bytes(path: Path, *, label: str) -> bytes:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ViewerInputMaterializationError(
                f"{label} must be a real regular file"
            )
        with path.open("rb") as stream:
            payload = stream.read()
            after = os.fstat(stream.fileno())
        final = path.lstat()
    except ViewerInputMaterializationError:
        raise
    except OSError as exc:
        raise ViewerInputMaterializationError(
            f"{label} cannot be read"
        ) from exc
    if (
        not payload
        or _identity(before) != _identity(after)
        or _identity(after) != _identity(final)
    ):
        raise ViewerInputMaterializationError(
            f"{label} is empty or changed while being read"
        )
    return payload


def _manifest_core_diagonal(manifest: dict[str, Any]) -> float:
    bounds = manifest.get("core_bounds")
    if not isinstance(bounds, dict):
        raise ViewerInputMaterializationError(
            "production scene manifest has no measured core bounds"
        )
    minimum = bounds.get("min")
    maximum = bounds.get("max")
    if (
        not isinstance(minimum, list)
        or not isinstance(maximum, list)
        or len(minimum) != 3
        or len(maximum) != 3
    ):
        raise ViewerInputMaterializationError(
            "production scene core bounds are invalid"
        )
    lo = np.asarray(minimum, dtype=np.float64)
    hi = np.asarray(maximum, dtype=np.float64)
    extent = hi - lo
    if (
        not np.all(np.isfinite(lo))
        or not np.all(np.isfinite(hi))
        or np.any(extent <= 0.0)
    ):
        raise ViewerInputMaterializationError(
            "production scene core bounds are non-finite or degenerate"
        )
    return float(np.linalg.norm(extent))


def _world_cameras(registration: RegistrationResult) -> tuple[_WorldCamera, ...]:
    target = registration.target_frame
    transform = registration.pose_to_world
    if (
        registration.engine != "colmap"
        or registration.alignment_status is not AlignmentStatus.ALIGNED
        or transform is None
        or registration.world_frame is None
        or target.axes is not AxisConvention.ENU_Z_UP
        or target.units is not CoordinateUnits.METERS
        or target.metric_status is not MetricStatus.METRIC
        or target.geo_aligned is not GeoAlignment.ALIGNED
    ):
        raise ViewerInputMaterializationError(
            "production camera selection requires aligned metric COLMAP poses"
        )
    alignment_rotation = transform.sim3.rotation_matrix()
    cameras: list[_WorldCamera] = []
    identities: set[str] = set()
    for pose in registration.poses:
        identity = f"{pose.session_id}/{pose.image}"
        if identity in identities:
            raise ViewerInputMaterializationError(
                "aligned registration contains duplicate camera identities"
            )
        identities.add(identity)
        position = transform.sim3.apply(
            np.asarray([pose.t_xyz], dtype=np.float64)
        )[0]
        forward = alignment_rotation @ pose.rotation_matrix()[:, 2]
        norm = float(np.linalg.norm(forward))
        if (
            not np.all(np.isfinite(position))
            or not np.all(np.isfinite(forward))
            or not math.isfinite(norm)
            or norm <= 1e-12
        ):
            raise ViewerInputMaterializationError(
                "aligned registration contains an invalid camera pose"
            )
        cameras.append(
            _WorldCamera(
                identity=identity,
                position=position,
                forward=forward / norm,
            )
        )
    if len(cameras) < 3:
        raise ViewerInputMaterializationError(
            "production camera selection requires at least three registered poses"
        )
    return tuple(sorted(cameras, key=lambda camera: camera.identity))


def _maximin_three(
    cameras: tuple[_WorldCamera, ...],
    *,
    scene_diagonal: float,
) -> tuple[_WorldCamera, _WorldCamera, _WorldCamera]:
    centroid = np.mean(
        np.asarray([camera.position for camera in cameras]),
        axis=0,
    )

    def choose(
        candidates: tuple[_WorldCamera, ...],
        score,
    ) -> _WorldCamera:
        return min(
            candidates,
            key=lambda camera: (
                -float(score(camera)),
                camera.identity,
            ),
        )

    first = choose(
        cameras,
        lambda camera: np.linalg.norm(camera.position - centroid),
    )
    second = choose(
        tuple(camera for camera in cameras if camera is not first),
        lambda camera: np.linalg.norm(camera.position - first.position),
    )
    third = choose(
        tuple(
            camera
            for camera in cameras
            if camera is not first and camera is not second
        ),
        lambda camera: min(
            np.linalg.norm(camera.position - first.position),
            np.linalg.norm(camera.position - second.position),
        ),
    )
    selected = (first, second, third)
    minimum_baseline = min(
        float(np.linalg.norm(left.position - right.position))
        for index, left in enumerate(selected)
        for right in selected[index + 1 :]
    )
    required_baseline = max(0.1, scene_diagonal * 0.01)
    if (
        not math.isfinite(minimum_baseline)
        or minimum_baseline < required_baseline
    ):
        raise ViewerInputMaterializationError(
            "registered camera poses are spatially degenerate for three-view QA"
        )
    return selected


def _camera_set(
    *,
    registration: RegistrationResult,
    manifest: dict[str, Any],
    scene_manifest_sha256: str,
    import_receipt_sha256: str,
    aligned_registration_sha256: str,
) -> ViewerCameraSetV2:
    scene_diagonal = _manifest_core_diagonal(manifest)
    selected = _maximin_three(
        _world_cameras(registration),
        scene_diagonal=scene_diagonal,
    )
    focus_distance = max(1.0, scene_diagonal * 0.25)
    poses: list[ViewerCameraPose] = []
    for camera in selected:
        look_at = camera.position + camera.forward * focus_distance
        payload = {
            "schema": "nantai.viewer-camera-pose.v1",
            "position": {
                "east": float(camera.position[0]),
                "north": float(camera.position[1]),
                "up": float(camera.position[2]),
            },
            "look_at": {
                "east": float(look_at[0]),
                "north": float(look_at[1]),
                "up": float(look_at[2]),
            },
        }
        poses.append(
            ViewerCameraPose(
                pose_id=viewer_camera_pose_id(payload),
                **payload,
            )
        )
    return ViewerCameraSetV2(
        source_role="production-acceptance",
        selection_strategy="registered-camera-maximin-v1",
        scene_manifest_sha256=scene_manifest_sha256,
        import_receipt_sha256=import_receipt_sha256,
        aligned_registration_sha256=aligned_registration_sha256,
        poses=tuple(poses),
    )


def _policy(camera_set: ViewerCameraSetV2) -> ViewerPerformancePolicy:
    return ViewerPerformancePolicy(
        required_pose_ids=tuple(
            pose.pose_id for pose in camera_set.poses
        ),
        viewport_width=1280,
        viewport_height=720,
        warmup_frame_count=120,
        measured_frame_count=600,
        maximum_interactive_ms=10_000.0,
        maximum_p50_frame_ms=33.34,
        maximum_p95_frame_ms=50.0,
        maximum_worst_frame_ms=250.0,
    )


def _verified_production_import(
    import_root: Path,
) -> tuple[
    RealSceneImportReceipt,
    bytes,
    bytes,
    bytes,
    RegistrationResult,
    dict[str, Any],
]:
    try:
        resolved_root = import_root.resolve(strict=True)
    except OSError as exc:
        raise ViewerInputMaterializationError(
            "production import root is unavailable"
        ) from exc
    if (
        not resolved_root.is_dir()
        or os.path.normcase(str(resolved_root))
        != os.path.normcase(str(import_root))
    ):
        raise ViewerInputMaterializationError(
            "production import root must be a real directory"
        )
    receipt_path = import_root / "import-receipt.json"
    try:
        receipt = real_scene_import.validate_real_scene_import_receipt(
            receipt_path,
            import_root,
        )
    except (OSError, ValueError) as exc:
        raise ViewerInputMaterializationError(
            "production import receipt cannot be verified"
        ) from exc
    if (
        not isinstance(receipt, RealSceneImportReceipt)
        or receipt.source_role != "production-acceptance"
        or receipt.geometry_usability != "metric-aligned"
        or receipt.target_units != "meters"
        or receipt.alignment_observed_registration_path is None
    ):
        raise ViewerInputMaterializationError(
            "production import receipt is missing metric acceptance evidence"
        )
    receipt_bytes = _read_regular_bytes(
        receipt_path,
        label="production import receipt",
    )
    manifest_bytes = _read_regular_bytes(
        import_root / receipt.manifest_path,
        label="production scene manifest",
    )
    registration_bytes = _read_regular_bytes(
        import_root / receipt.alignment_observed_registration_path,
        label="aligned production registration",
    )
    try:
        registration = RegistrationResult.model_validate_json(
            registration_bytes
        )
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, ValueError) as exc:
        raise ViewerInputMaterializationError(
            "production Viewer source evidence is malformed"
        ) from exc
    if not isinstance(manifest, dict):
        raise ViewerInputMaterializationError(
            "production scene manifest must be an object"
        )
    return (
        receipt,
        receipt_bytes,
        manifest_bytes,
        registration_bytes,
        registration,
        manifest,
    )


def materialize_production_viewer_inputs(
    *,
    import_root: Path,
    output_dir: Path,
) -> ViewerInputMaterialization:
    import_root = Path(import_root).expanduser().absolute()
    output_dir = Path(output_dir).expanduser().absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise ViewerInputMaterializationError(
            "Viewer input output directory must be absent"
        )
    try:
        resolved_parent = output_dir.parent.resolve(strict=True)
    except OSError as exc:
        raise ViewerInputMaterializationError(
            "Viewer input output parent is unavailable"
        ) from exc
    if (
        output_dir.parent.is_symlink()
        or not resolved_parent.is_dir()
        or os.path.normcase(str(resolved_parent))
        != os.path.normcase(str(output_dir.parent))
    ):
        raise ViewerInputMaterializationError(
            "Viewer input output parent must be a real directory"
        )
    (
        receipt,
        receipt_bytes,
        manifest_bytes,
        registration_bytes,
        registration,
        manifest,
    ) = _verified_production_import(import_root)
    camera_set = _camera_set(
        registration=registration,
        manifest=manifest,
        scene_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        import_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
        aligned_registration_sha256=hashlib.sha256(
            registration_bytes
        ).hexdigest(),
    )
    policy = _policy(camera_set)
    camera_bytes = canonical_viewer_camera_set_bytes(camera_set)
    policy_bytes = canonical_viewer_performance_policy_bytes(policy)
    staging = output_dir.parent / (
        f".{output_dir.name}.{uuid.uuid4().hex}.staging"
    )
    published = False
    try:
        staging.mkdir()
        camera_path = staging / "cameras.json"
        policy_path = staging / "policy.json"
        camera_path.write_bytes(camera_bytes)
        policy_path.write_bytes(policy_bytes)
        flush_file(camera_path)
        flush_file(policy_path)
        flush_directory(staging)
        (
            current_receipt,
            current_receipt_bytes,
            current_manifest_bytes,
            current_registration_bytes,
            _registration,
            _manifest,
        ) = _verified_production_import(import_root)
        if (
            current_receipt != receipt
            or current_receipt_bytes != receipt_bytes
            or current_manifest_bytes != manifest_bytes
            or current_registration_bytes != registration_bytes
        ):
            raise ViewerInputMaterializationError(
                "production import changed during Viewer input materialization"
            )
        publish_directory_noreplace(staging, output_dir)
        published = True
    except ViewerInputMaterializationError:
        raise
    except (DurableIOError, OSError) as exc:
        if isinstance(exc, DurableIOError) and exc.published:
            published = True
        raise ViewerInputMaterializationError(
            "Viewer input output cannot be published durably"
        ) from exc
    finally:
        if not published and staging.is_dir() and not staging.is_symlink():
            for filename in ("cameras.json", "policy.json"):
                candidate = staging / filename
                if candidate.is_file() and not candidate.is_symlink():
                    candidate.unlink()
            try:
                staging.rmdir()
            except OSError:
                pass
    return ViewerInputMaterialization(
        camera_set_path=output_dir / "cameras.json",
        policy_path=output_dir / "policy.json",
        camera_set_sha256=hashlib.sha256(camera_bytes).hexdigest(),
        policy_sha256=hashlib.sha256(policy_bytes).hexdigest(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize a provenance-bound Viewer camera set and policy "
            "from one verified production import."
        )
    )
    parser.add_argument("--import-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = materialize_production_viewer_inputs(
            import_root=args.import_root,
            output_dir=args.output_dir,
        )
    except ViewerInputMaterializationError as exc:
        print(f"Viewer input materialization failed: {exc}")
        return 2
    print(f"Camera set: {result.camera_set_path}")
    print(f"Policy: {result.policy_path}")
    print(f"Camera set SHA-256: {result.camera_set_sha256}")
    print(f"Policy SHA-256: {result.policy_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
