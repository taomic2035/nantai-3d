"""Pure, fail-closed acceptance derivation for real Viewer measurements."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Literal

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
    report: ViewerPerformanceReport,
) -> ViewerPerformanceReport:
    if "accepted" in getattr(report, "__dict__", {}):
        raise ViewerAcceptanceError(
            "report-authored acceptance decision is forbidden"
        )
    try:
        return ViewerPerformanceReport.model_validate(
            report.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise ViewerAcceptanceError(
            f"viewer performance report is invalid: {exc}"
        ) from exc


def derive_viewer_decision(
    policy: ViewerPerformancePolicy,
    report: ViewerPerformanceReport,
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
    parser.add_argument("--decision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = ViewerPerformancePolicy.model_validate_json(
            Path(args.policy).read_bytes()
        )
        report = ViewerPerformanceReport.model_validate_json(
            Path(args.report).read_bytes()
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
