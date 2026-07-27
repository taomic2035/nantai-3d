"""Pure, fail-closed acceptance derivation for real Viewer measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import struct
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class ViewerAcceptanceError(ValueError):
    """Viewer performance evidence is invalid or self-authored."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_POSE_PATTERN = r"^pose-[0-9a-f]{64}$"
_REPORT_ID_PATTERN = r"^viewer-capture-[0-9a-f]{64}$"
_MAX_CAPTURE_ARTIFACT_BYTES = 100 * 1024 * 1024


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _portable_relative_path(value: str, *, label: str) -> str:
    if not value or "\\" in value or "\x00" in value or len(value) > 240:
        raise ValueError(f"{label} must be a portable relative path")
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"{label} must be a portable relative path")
    return value


class ViewerPerformancePolicy(FrozenModel):
    schema_id: Literal["nantai.viewer-performance-policy.v1"] = Field(
        default="nantai.viewer-performance-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    required_pose_ids: tuple[str, ...] = Field(min_length=3)
    viewport_width: int = Field(ge=1, le=16_384)
    viewport_height: int = Field(ge=1, le=16_384)
    warmup_frame_count: int = Field(ge=1)
    measured_frame_count: int = Field(ge=1)
    maximum_interactive_ms: float = Field(
        gt=0.0,
        allow_inf_nan=False,
    )
    maximum_p50_frame_ms: float = Field(
        gt=0.0,
        allow_inf_nan=False,
    )
    maximum_p95_frame_ms: float = Field(
        gt=0.0,
        allow_inf_nan=False,
    )
    maximum_worst_frame_ms: float = Field(
        gt=0.0,
        allow_inf_nan=False,
    )
    percentile_method: Literal["nearest-rank"] = "nearest-rank"
    required_representation: Literal["full-3dgs"] = "full-3dgs"
    required_http_cache: Literal["empty"] = "empty"
    allow_software_renderer: Literal[False] = False

    @field_validator("required_pose_ids")
    @classmethod
    def _pose_ids_are_content_addressed(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        import re

        if (
            len(value) != len(set(value))
            or any(re.fullmatch(_POSE_PATTERN, item) is None for item in value)
        ):
            raise ValueError(
                "required pose ids must be unique and content-addressed"
            )
        return value

    @model_validator(mode="after")
    def _threshold_order_is_sane(self) -> ViewerPerformancePolicy:
        if not (
            self.maximum_p50_frame_ms
            <= self.maximum_p95_frame_ms
            <= self.maximum_worst_frame_ms
        ):
            raise ValueError(
                "viewer frame thresholds must be monotonically ordered"
            )
        return self


def _validated_enu_coordinates(
    value: dict[str, int | float],
    *,
    label: str,
) -> dict[str, int | float]:
    if (
        not isinstance(value, dict)
        or set(value) != {"east", "north", "up"}
        or any(
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not math.isfinite(float(coordinate))
            for coordinate in value.values()
        )
    ):
        raise ValueError(f"{label} must be a finite ENU coordinate")
    return value


class ViewerCameraPose(FrozenModel):
    pose_id: str = Field(pattern=_POSE_PATTERN)
    schema_id: Literal["nantai.viewer-camera-pose.v1"] = Field(
        default="nantai.viewer-camera-pose.v1",
        alias="schema",
        serialization_alias="schema",
    )
    position: dict[str, int | float]
    look_at: dict[str, int | float]

    @field_validator("position", "look_at")
    @classmethod
    def _coordinates_are_finite(
        cls,
        value: dict[str, int | float],
        info,
    ) -> dict[str, int | float]:
        return _validated_enu_coordinates(
            value,
            label=f"Viewer camera pose {info.field_name}",
        )

    @model_validator(mode="after")
    def _pose_id_is_content_addressed(self) -> ViewerCameraPose:
        payload = {
            "schema": self.schema_id,
            "position": self.position,
            "look_at": self.look_at,
        }
        if self.pose_id != viewer_camera_pose_id(payload):
            raise ValueError("Viewer camera pose content hash disagrees")
        return self


class ViewerCameraSetV1(FrozenModel):
    schema_id: Literal["nantai.viewer-camera-set.v1"] = Field(
        default="nantai.viewer-camera-set.v1",
        alias="schema",
        serialization_alias="schema",
    )
    poses: tuple[ViewerCameraPose, ...] = Field(
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def _poses_are_unique(self) -> ViewerCameraSetV1:
        pose_ids = tuple(pose.pose_id for pose in self.poses)
        if len(pose_ids) != len(set(pose_ids)):
            raise ValueError("Viewer camera set pose ids must be unique")
        return self


class ViewerCameraSetV2(FrozenModel):
    schema_id: Literal["nantai.viewer-camera-set.v2"] = Field(
        default="nantai.viewer-camera-set.v2",
        alias="schema",
        serialization_alias="schema",
    )
    source_role: Literal["production-acceptance"]
    selection_strategy: Literal["registered-camera-maximin-v1"]
    scene_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    import_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    aligned_registration_sha256: str = Field(pattern=_SHA256_PATTERN)
    poses: tuple[ViewerCameraPose, ...] = Field(
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def _poses_are_unique(self) -> ViewerCameraSetV2:
        pose_ids = tuple(pose.pose_id for pose in self.poses)
        if len(pose_ids) != len(set(pose_ids)):
            raise ValueError("Viewer camera set pose ids must be unique")
        return self


ViewerCameraSet = ViewerCameraSetV1 | ViewerCameraSetV2


class ViewerRuntimeIdentity(FrozenModel):
    browser_name: str = Field(min_length=1)
    browser_version: str = Field(min_length=1)
    playwright_version: str = Field(min_length=1)
    operating_system: str = Field(min_length=1)
    gpu_vendor: str = Field(min_length=1)
    gpu_renderer: str = Field(min_length=1)
    webgl_version: str = Field(min_length=1)


class ViewerPoseMeasurement(FrozenModel):
    pose_id: str = Field(pattern=_POSE_PATTERN)
    representation: Literal[
        "full-3dgs",
        "dc-point-preview",
        "mesh-preview",
        "unavailable",
    ]
    interactive_ms: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    warmup_frame_ms: tuple[float, ...]
    measured_frame_ms: tuple[float, ...]
    timed_out: bool
    sample_overflow: bool

    @field_validator("warmup_frame_ms", "measured_frame_ms")
    @classmethod
    def _frame_samples_are_finite_positive(
        cls,
        value: tuple[float, ...],
    ) -> tuple[float, ...]:
        if any(
            not math.isfinite(sample) or sample <= 0.0
            for sample in value
        ):
            raise ValueError(
                "viewer frame samples must be finite and positive"
            )
        return value

    @model_validator(mode="after")
    def _unknown_interactive_time_requires_timeout(
        self,
    ) -> ViewerPoseMeasurement:
        if self.interactive_ms is None and not self.timed_out:
            raise ValueError(
                "unknown interactive time requires an explicit timeout"
            )
        return self


class ViewerPerformanceReport(FrozenModel):
    schema_id: Literal["nantai.viewer-performance-report.v1"] = Field(
        default="nantai.viewer-performance-report.v1",
        alias="schema",
        serialization_alias="schema",
    )
    probe_version: Literal["nantai.viewer-acceptance-probe.v1"] = (
        "nantai.viewer-acceptance-probe.v1"
    )
    source_role: Literal["internal-canary", "production-acceptance"]
    scene_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    viewport_width: int = Field(ge=1, le=16_384)
    viewport_height: int = Field(ge=1, le=16_384)
    http_cache: Literal["empty", "warm", "unknown"]
    runtime: ViewerRuntimeIdentity
    poses: tuple[ViewerPoseMeasurement, ...] = Field(min_length=1)
    console_errors: tuple[str, ...]
    unhandled_rejections: tuple[str, ...]

    @model_validator(mode="after")
    def _poses_are_unique(self) -> ViewerPerformanceReport:
        pose_ids = tuple(pose.pose_id for pose in self.poses)
        if len(pose_ids) != len(set(pose_ids)):
            raise ValueError(
                "viewer performance report contains duplicate poses"
            )
        return self


class ViewerCaptureArtifactBinding(FrozenModel):
    path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_length: int = Field(ge=1, le=_MAX_CAPTURE_ARTIFACT_BYTES)

    @field_validator("path")
    @classmethod
    def _path_is_portable(cls, value: str) -> str:
        return _portable_relative_path(
            value,
            label="Viewer capture artifact path",
        )


class ViewerScreenshotBinding(ViewerCaptureArtifactBinding):
    pose_id: str = Field(pattern=_POSE_PATTERN)


class ViewerExecutableSnapshot(FrozenModel):
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_length: int = Field(ge=1, le=4 * 1024 * 1024 * 1024)
    device_id: str = Field(pattern=r"^[0-9]+$")
    file_id: str = Field(pattern=r"^[0-9]+$")
    mtime_ns: str = Field(pattern=r"^[0-9]+$")
    mode: int = Field(ge=0)
    executable: Literal[True] = True

    @model_validator(mode="after")
    def _is_regular_executable(self) -> ViewerExecutableSnapshot:
        if not stat.S_ISREG(self.mode):
            raise ValueError(
                "Viewer executable snapshot must describe a regular file"
            )
        return self


class StableViewerExecutableObservation(FrozenModel):
    role: Literal["node", "browser"]
    before: ViewerExecutableSnapshot
    after: ViewerExecutableSnapshot

    @model_validator(mode="after")
    def _identity_is_stable(self) -> StableViewerExecutableObservation:
        if self.before != self.after:
            raise ValueError(
                f"{self.role} executable changed during Viewer capture"
            )
        return self


def viewer_performance_report_content_sha256(
    report: ViewerPerformanceReportV2,
) -> str:
    payload = report.model_dump(mode="json", by_alias=True)
    payload.pop("report_id", None)
    payload.pop("content_sha256", None)
    return hashlib.sha256(
        _canonical_json_bytes(_numeric_hash_projection(payload))
    ).hexdigest()


def _numeric_hash_projection(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return {"$f64": struct.pack(">d", float(value)).hex()}
    if isinstance(value, list):
        return [_numeric_hash_projection(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _numeric_hash_projection(item)
            for key, item in value.items()
        }
    raise TypeError(
        f"unsupported Viewer report hash value: {type(value).__name__}"
    )


def viewer_camera_pose_id(pose: dict[str, Any]) -> str:
    if (
        not isinstance(pose, dict)
        or set(pose) != {"schema", "position", "look_at"}
        or pose["schema"] != "nantai.viewer-camera-pose.v1"
    ):
        raise ValueError("Viewer camera pose has an invalid field set or schema")
    for label in ("position", "look_at"):
        coordinates = pose[label]
        if (
            not isinstance(coordinates, dict)
            or set(coordinates) != {"east", "north", "up"}
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in coordinates.values()
            )
        ):
            raise ValueError(f"Viewer camera pose {label} is invalid")
    return "pose-" + hashlib.sha256(
        _canonical_json_bytes(_numeric_hash_projection(pose))[:-1]
    ).hexdigest()


class ViewerPerformanceReportV2(ViewerPerformanceReport):
    schema_id: Literal["nantai.viewer-performance-report.v2"] = Field(
        default="nantai.viewer-performance-report.v2",
        alias="schema",
        serialization_alias="schema",
    )
    report_id: str = Field(pattern=_REPORT_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    scene_manifest: ViewerCaptureArtifactBinding
    viewer_policy: ViewerCaptureArtifactBinding
    camera_set: ViewerCaptureArtifactBinding
    capture_script: ViewerCaptureArtifactBinding
    probe_module: ViewerCaptureArtifactBinding
    playwright_package: ViewerCaptureArtifactBinding
    node_executable: StableViewerExecutableObservation
    browser_executable: StableViewerExecutableObservation
    screenshots: tuple[ViewerScreenshotBinding, ...] = Field(min_length=3)

    @model_validator(mode="after")
    def _capture_contract_is_closed(self) -> ViewerPerformanceReportV2:
        if self.source_role != "production-acceptance":
            raise ValueError(
                "Viewer v2 capture report is reserved for production acceptance"
            )
        if self.scene_manifest_sha256 != self.scene_manifest.sha256:
            raise ValueError(
                "Viewer v2 scene manifest binding disagrees"
            )
        if self.node_executable.role != "node":
            raise ValueError("Viewer v2 Node executable role disagrees")
        if self.browser_executable.role != "browser":
            raise ValueError("Viewer v2 browser executable role disagrees")
        pose_ids = tuple(row.pose_id for row in self.poses)
        screenshot_pose_ids = tuple(row.pose_id for row in self.screenshots)
        if screenshot_pose_ids != pose_ids:
            raise ValueError(
                "Viewer v2 screenshot pose order differs from measurements"
            )
        paths = tuple(
            binding.path
            for binding in (
                self.scene_manifest,
                self.viewer_policy,
                self.camera_set,
                self.capture_script,
                self.probe_module,
                self.playwright_package,
                *self.screenshots,
            )
        )
        if len(paths) != len(set(paths)):
            raise ValueError(
                "Viewer v2 capture artifact paths must be unique"
            )
        expected = viewer_performance_report_content_sha256(self)
        if self.content_sha256 != expected:
            raise ValueError(
                "Viewer v2 report content SHA-256 disagrees"
            )
        if self.report_id != f"viewer-capture-{expected}":
            raise ValueError("Viewer v2 report id disagrees")
        return self


class ViewerPerformanceDecision(FrozenModel):
    schema_id: Literal["nantai.viewer-performance-decision.v1"] = Field(
        default="nantai.viewer-performance-decision.v1",
        alias="schema",
        serialization_alias="schema",
    )
    accepted: bool
    failed_gates: tuple[str, ...]
    pose_count: int = Field(ge=1)
    maximum_observed_p50_frame_ms: float = Field(
        allow_inf_nan=False,
    )
    maximum_observed_p95_frame_ms: float = Field(
        allow_inf_nan=False,
    )
    maximum_observed_worst_frame_ms: float = Field(
        allow_inf_nan=False,
    )
    maximum_observed_interactive_ms: float = Field(
        allow_inf_nan=False,
    )


def _nearest_rank(
    samples: tuple[float, ...],
    percentile: float,
) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _validated_report(
    report: ViewerPerformanceReport | ViewerPerformanceReportV2,
) -> ViewerPerformanceReport | ViewerPerformanceReportV2:
    if "accepted" in getattr(report, "__dict__", {}):
        raise ViewerAcceptanceError(
            "report-authored acceptance decision is forbidden"
        )
    try:
        model_type = (
            ViewerPerformanceReportV2
            if isinstance(report, ViewerPerformanceReportV2)
            else ViewerPerformanceReport
        )
        return model_type.model_validate(
            report.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise ViewerAcceptanceError(
            f"viewer performance report is invalid: {exc}"
        ) from exc


def derive_viewer_decision(
    policy: ViewerPerformancePolicy,
    report: ViewerPerformanceReport | ViewerPerformanceReportV2,
) -> ViewerPerformanceDecision:
    """Recompute every browser gate from raw bounded samples."""

    try:
        policy = ViewerPerformancePolicy.model_validate(
            policy.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise ViewerAcceptanceError(
            f"viewer performance policy is invalid: {exc}"
        ) from exc
    report = _validated_report(report)
    failures: list[str] = []
    if (
        report.viewport_width != policy.viewport_width
        or report.viewport_height != policy.viewport_height
    ):
        failures.append(
            "viewport differs from required "
            f"{policy.viewport_width}x{policy.viewport_height}"
        )
    if report.http_cache != policy.required_http_cache:
        failures.append("HTTP cache was not empty")
    if report.console_errors:
        failures.append(
            f"browser console errors: {len(report.console_errors)}"
        )
    if report.unhandled_rejections:
        failures.append(
            "browser unhandled rejection count: "
            f"{len(report.unhandled_rejections)}"
        )
    renderer = report.runtime.gpu_renderer.casefold()
    generic_renderers = {
        "masked",
        "unknown",
        "webkit webgl",
        "webgl",
    }
    if (
        renderer.strip() in generic_renderers
        or report.runtime.gpu_vendor.casefold().strip()
        in {"masked", "unknown"}
    ):
        failures.append("GPU renderer identity was not measurable")
    if any(
        marker in renderer
        for marker in ("swiftshader", "llvmpipe", "software")
    ):
        failures.append("software GPU renderer is not accepted")

    expected_pose_ids = set(policy.required_pose_ids)
    observed_pose_ids = {pose.pose_id for pose in report.poses}
    if observed_pose_ids != expected_pose_ids:
        failures.append("measured pose set differs from policy")

    p50_values: list[float] = []
    p95_values: list[float] = []
    worst_values: list[float] = []
    interactive_values: list[float] = []
    for pose in report.poses:
        if pose.representation != policy.required_representation:
            failures.append(
                f"{pose.pose_id}: full-3dgs representation was not rendered"
            )
        if len(pose.warmup_frame_ms) != policy.warmup_frame_count:
            failures.append(
                f"{pose.pose_id}: expected "
                f"{policy.warmup_frame_count} warmup frames"
            )
        if len(pose.measured_frame_ms) != policy.measured_frame_count:
            failures.append(
                f"{pose.pose_id}: expected "
                f"{policy.measured_frame_count} measured frames"
            )
        if pose.timed_out:
            failures.append(f"{pose.pose_id}: loading timeout")
        if pose.sample_overflow:
            failures.append(f"{pose.pose_id}: sample buffer overflow")
        if pose.interactive_ms is None:
            failures.append(
                f"{pose.pose_id}: interactive time unavailable"
            )
        else:
            interactive_values.append(pose.interactive_ms)
        if (
            pose.interactive_ms is not None
            and pose.interactive_ms > policy.maximum_interactive_ms
        ):
            failures.append(
                f"{pose.pose_id}: interactive_ms "
                f"{pose.interactive_ms:.6g} > "
                f"{policy.maximum_interactive_ms:.6g}"
            )
        if not pose.measured_frame_ms:
            p50_values.append(0.0)
            p95_values.append(0.0)
            worst_values.append(0.0)
            continue
        p50 = _nearest_rank(pose.measured_frame_ms, 0.50)
        p95 = _nearest_rank(pose.measured_frame_ms, 0.95)
        worst = max(pose.measured_frame_ms)
        p50_values.append(p50)
        p95_values.append(p95)
        worst_values.append(worst)
        if p50 > policy.maximum_p50_frame_ms:
            failures.append(
                f"{pose.pose_id}: p50 {p50:.6g} > "
                f"{policy.maximum_p50_frame_ms:.6g}"
            )
        if p95 > policy.maximum_p95_frame_ms:
            failures.append(
                f"{pose.pose_id}: p95 {p95:.6g} > "
                f"{policy.maximum_p95_frame_ms:.6g}"
            )
        if worst > policy.maximum_worst_frame_ms:
            failures.append(
                f"{pose.pose_id}: worst {worst:.6g} > "
                f"{policy.maximum_worst_frame_ms:.6g}"
            )
    return ViewerPerformanceDecision(
        accepted=not failures,
        failed_gates=tuple(failures),
        pose_count=len(report.poses),
        maximum_observed_p50_frame_ms=max(p50_values, default=0.0),
        maximum_observed_p95_frame_ms=max(p95_values, default=0.0),
        maximum_observed_worst_frame_ms=max(
            worst_values,
            default=0.0,
        ),
        maximum_observed_interactive_ms=max(
            interactive_values,
            default=0.0,
        ),
    )


def canonical_viewer_performance_policy_bytes(
    policy: ViewerPerformancePolicy,
) -> bytes:
    validated = ViewerPerformancePolicy.model_validate(
        policy.model_dump(by_alias=True)
    )
    return _canonical_json_bytes(
        validated.model_dump(mode="json", by_alias=True)
    )


def canonical_viewer_performance_report_bytes(
    report: ViewerPerformanceReport | ViewerPerformanceReportV2,
) -> bytes:
    validated = _validated_report(report)
    return _canonical_json_bytes(
        validated.model_dump(mode="json", by_alias=True)
    )


def build_viewer_performance_report_v2(
    **fields: Any,
) -> ViewerPerformanceReportV2:
    zero = "0" * 64
    draft = ViewerPerformanceReportV2.model_construct(
        report_id=f"viewer-capture-{zero}",
        content_sha256=zero,
        **fields,
    )
    digest = viewer_performance_report_content_sha256(draft)
    return ViewerPerformanceReportV2(
        report_id=f"viewer-capture-{digest}",
        content_sha256=digest,
        **fields,
    )


def _reject_duplicate_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ViewerAcceptanceError(
                f"duplicate JSON key in Viewer evidence: {key}"
            )
        result[key] = value
    return result


def load_viewer_performance_report_bytes(
    payload: bytes,
) -> ViewerPerformanceReport | ViewerPerformanceReportV2:
    try:
        decoded = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        if not isinstance(decoded, dict):
            raise ValueError("Viewer report root must be an object")
        if payload != _canonical_json_bytes(decoded):
            raise ViewerAcceptanceError(
                "Viewer performance report is not canonical JSON"
            )
        schema = decoded.get("schema")
        if schema == "nantai.viewer-performance-report.v2":
            model_type = ViewerPerformanceReportV2
        elif schema == "nantai.viewer-performance-report.v1":
            model_type = ViewerPerformanceReport
        else:
            raise ValueError("Viewer report schema is unsupported")
        report = model_type.model_validate_json(
            _canonical_json_bytes(decoded)
        )
    except ViewerAcceptanceError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise ViewerAcceptanceError(
            f"Viewer performance report is invalid: {exc}"
        ) from exc
    return report


def canonical_viewer_camera_set_bytes(
    camera_set: ViewerCameraSet,
) -> bytes:
    return _canonical_json_bytes(
        camera_set.model_dump(mode="json", by_alias=True)
    )


def load_viewer_camera_set_bytes(payload: bytes) -> ViewerCameraSet:
    try:
        decoded = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        if not isinstance(decoded, dict):
            raise ValueError("camera set root must be an object")
        if payload != _canonical_json_bytes(decoded):
            raise ValueError("camera set must be canonical JSON")
        schema = decoded.get("schema")
        if schema == "nantai.viewer-camera-set.v1":
            model_type = ViewerCameraSetV1
        elif schema == "nantai.viewer-camera-set.v2":
            model_type = ViewerCameraSetV2
        else:
            raise ValueError("camera set schema is unsupported")
        return model_type.model_validate_json(payload)
    except ViewerAcceptanceError:
        raise
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ViewerAcceptanceError(
            f"Viewer camera set is invalid: {exc}"
        ) from exc


def _stable_bound_file(
    root: Path,
    binding: ViewerCaptureArtifactBinding | ViewerScreenshotBinding,
    *,
    label: str,
) -> bytes:
    try:
        root = Path(root).resolve(strict=True)
    except OSError as exc:
        raise ViewerAcceptanceError(
            "Viewer capture evidence root is unavailable"
        ) from exc
    candidate = root.joinpath(*PurePosixPath(binding.path).parts)
    current = root
    try:
        if current.is_symlink() or not current.is_dir():
            raise ViewerAcceptanceError(
                "Viewer capture evidence root must be a real directory"
            )
        for part in PurePosixPath(binding.path).parts:
            current = current / part
            inspected = current.lstat()
            if stat.S_ISLNK(inspected.st_mode):
                raise ViewerAcceptanceError(
                    f"{label} must not traverse a symlink"
                )
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except ViewerAcceptanceError:
        raise
    except (OSError, ValueError) as exc:
        raise ViewerAcceptanceError(
            f"{label} is unavailable or escapes the evidence root"
        ) from exc
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ViewerAcceptanceError(
            f"{label} cannot be opened"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != binding.byte_length
        ):
            raise ViewerAcceptanceError(
                f"{label} changed; byte length or file type disagrees"
            )
        chunks: list[bytes] = []
        remaining = binding.byte_length + 1
        while remaining:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, remaining),
            )
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ViewerAcceptanceError(
            f"{label} cannot be read"
        ) from exc
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    try:
        final = resolved.lstat()
    except OSError as exc:
        raise ViewerAcceptanceError(
            f"{label} changed while being verified"
        ) from exc

    def identity(item):
        return (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_mode,
        )

    if (
        identity(before) != identity(after)
        or identity(after) != identity(final)
        or len(payload) != binding.byte_length
        or hashlib.sha256(payload).hexdigest() != binding.sha256
    ):
        raise ViewerAcceptanceError(
            f"{label} changed or its SHA-256 disagrees"
        )
    return payload


def verify_viewer_capture_report(
    policy: ViewerPerformancePolicy,
    report: ViewerPerformanceReport | ViewerPerformanceReportV2,
    root: Path,
) -> ViewerCameraSetV2:
    if not isinstance(report, ViewerPerformanceReportV2):
        raise ViewerAcceptanceError(
            "production Viewer capture requires a v2 report"
        )
    validated = _validated_report(report)
    assert isinstance(validated, ViewerPerformanceReportV2)
    policy_payload = canonical_viewer_performance_policy_bytes(policy)
    if (
        validated.viewer_policy.sha256
        != hashlib.sha256(policy_payload).hexdigest()
        or validated.viewer_policy.byte_length != len(policy_payload)
    ):
        raise ViewerAcceptanceError(
            "Viewer capture policy binding disagrees"
        )
    camera_set: ViewerCameraSet | None = None
    for name in (
        "scene_manifest",
        "viewer_policy",
        "camera_set",
        "capture_script",
        "probe_module",
        "playwright_package",
    ):
        binding = getattr(validated, name)
        payload = _stable_bound_file(
            root,
            binding,
            label=f"Viewer capture {name.replace('_', ' ')}",
        )
        if name == "viewer_policy" and payload != policy_payload:
            raise ViewerAcceptanceError(
                "Viewer capture policy is not canonical or differs"
            )
        if name == "camera_set":
            camera_set = load_viewer_camera_set_bytes(payload)
            if not isinstance(camera_set, ViewerCameraSetV2):
                raise ViewerAcceptanceError(
                    "production Viewer camera set requires v2 provenance"
                )
            if (
                camera_set.scene_manifest_sha256
                != validated.scene_manifest.sha256
            ):
                raise ViewerAcceptanceError(
                    "Viewer camera set scene manifest binding disagrees"
                )
            camera_pose_ids = tuple(
                pose.pose_id for pose in camera_set.poses
            )
            report_pose_ids = tuple(
                pose.pose_id for pose in validated.poses
            )
            if (
                camera_pose_ids != report_pose_ids
                or camera_pose_ids != policy.required_pose_ids
            ):
                raise ViewerAcceptanceError(
                    "Viewer camera set pose order differs from report or policy"
                )
    for screenshot in validated.screenshots:
        _stable_bound_file(
            root,
            screenshot,
            label=f"Viewer screenshot {screenshot.pose_id}",
        )
    derive_viewer_decision(policy, validated)
    assert isinstance(camera_set, ViewerCameraSetV2)
    return camera_set


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Re-derive Viewer acceptance from raw browser evidence."
        )
    )
    parser.add_argument("--policy", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--evidence-root")
    parser.add_argument("--decision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = ViewerPerformancePolicy.model_validate_json(
            Path(args.policy).read_bytes()
        )
        report = load_viewer_performance_report_bytes(
            Path(args.report).read_bytes()
        )
        if isinstance(report, ViewerPerformanceReportV2):
            if not args.evidence_root:
                raise ViewerAcceptanceError(
                    "Viewer v2 report requires an evidence root"
                )
            verify_viewer_capture_report(
                policy,
                report,
                Path(args.evidence_root),
            )
        decision = derive_viewer_decision(policy, report)
    except (OSError, ValueError, ViewerAcceptanceError) as exc:
        print(f"INVALID: {exc}")
        return 2

    decision_json = _canonical_json(
        decision.model_dump(mode="json", by_alias=True)
    )
    if args.decision:
        Path(args.decision).write_text(
            decision_json,
            encoding="ascii",
            newline="",
        )
    verdict = "ACCEPTED" if decision.accepted else "REJECTED"
    print(
        f"{verdict}: {len(decision.failed_gates)} failed gate(s)"
    )
    for failure in decision.failed_gates:
        print(f"- {failure}")
    return 0 if decision.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
