"""Fail-closed held-out render quality contract.

The remote evaluator is trusted only for producing numeric PSNR/SSIM/LPIPS
measurements inside a pinned container.  This module independently closes the
held-out partition, camera/source/render bytes, evaluation protocol and
aggregate threshold decision.  It does not claim to recompute LPIPS locally.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import struct
import zlib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    HeldOutSplit,
    held_out_split_canonical_bytes,
)


class RenderEvaluationError(ValueError):
    """Render evaluation evidence is incomplete, drifted or dishonest."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_CONTAINER_PATTERN = r"^[A-Za-z0-9._/:+-]+@sha256:[0-9a-f]{64}$"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _portable_path(value: str, *, label: str) -> str:
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or "\\" in value
        or "\x00" in value
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError(f"{label} must be portable and relative")
    return value


class RenderEvaluationProtocol(FrozenModel):
    schema_id: Literal["nantai.render-evaluation-protocol.v1"] = Field(
        default="nantai.render-evaluation-protocol.v1",
        alias="schema",
        serialization_alias="schema",
    )
    width: int = Field(ge=1, le=16_384)
    height: int = Field(ge=1, le=16_384)
    crop_mode: Literal["center-crop", "letterbox", "stretch"]
    colour_space: Literal["srgb", "linear-srgb"]
    alpha_handling: Literal["reject", "composite-black", "composite-white"]
    mask_handling: Literal["none", "alpha", "external"]
    render_encoding: Literal["png-rgb8-lossless"] = "png-rgb8-lossless"
    crop_rounding: Literal["floor-center"] = "floor-center"
    resize_filter: Literal["bilinear-antialias"] = "bilinear-antialias"
    psnr_epsilon: float = Field(
        default=1e-10,
        gt=0.0,
        lt=1.0,
        allow_inf_nan=False,
    )
    ssim_window_size: int = Field(ge=3, le=31)
    ssim_sigma: float = Field(gt=0.0, allow_inf_nan=False)
    ssim_data_range: float = Field(gt=0.0, allow_inf_nan=False)
    lpips_backbone: Literal["alex", "vgg", "squeeze"]

    @model_validator(mode="after")
    def _window_is_odd(self) -> RenderEvaluationProtocol:
        if self.ssim_window_size % 2 == 0:
            raise ValueError("SSIM window size must be odd")
        return self


class RenderEvaluationPolicy(FrozenModel):
    schema_id: Literal["nantai.render-evaluation-policy.v1"] = Field(
        default="nantai.render-evaluation-policy.v1",
        alias="schema",
        serialization_alias="schema",
    )
    held_out_split_path: Literal[
        "prepared/evidence/held-out-split.json"
    ] = "prepared/evidence/held-out-split.json"
    transforms_path: Literal["prepared/transforms.json"] = (
        "prepared/transforms.json"
    )
    source_root: Literal["prepared/images"] = "prepared/images"
    render_root: Literal["result/render-evaluation/renders"] = (
        "result/render-evaluation/renders"
    )
    camera_root: Literal["result/render-evaluation/cameras"] = (
        "result/render-evaluation/cameras"
    )
    held_out_split_sha256: str = Field(pattern=_SHA256_PATTERN)
    transforms_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluator_container_digest: str = Field(
        pattern=_CONTAINER_PATTERN,
    )
    protocol: RenderEvaluationProtocol
    minimum_mean_psnr: float = Field(allow_inf_nan=False)
    minimum_mean_ssim: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    maximum_mean_lpips: float = Field(
        ge=0.0,
        allow_inf_nan=False,
    )
    minimum_worst_psnr: float = Field(allow_inf_nan=False)


class RenderCameraRecord(FrozenModel):
    schema_id: Literal["nantai.render-camera.v1"] = Field(
        default="nantai.render-camera.v1",
        alias="schema",
        serialization_alias="schema",
    )
    frame_id: str
    source_path: str
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    transforms_sha256: str = Field(pattern=_SHA256_PATTERN)
    camera_model: Literal[
        "perspective",
        "fisheye",
        "equirectangular",
    ]
    source_width: int = Field(ge=1, le=100_000)
    source_height: int = Field(ge=1, le=100_000)
    fx: float = Field(gt=0.0, allow_inf_nan=False)
    fy: float = Field(gt=0.0, allow_inf_nan=False)
    cx: float = Field(allow_inf_nan=False)
    cy: float = Field(allow_inf_nan=False)
    camera_to_world: tuple[float, ...] = Field(
        min_length=12,
        max_length=12,
    )

    @field_validator("frame_id")
    @classmethod
    def _frame_id_is_portable(cls, value: str) -> str:
        return _portable_path(value, label="camera frame id")

    @field_validator("source_path")
    @classmethod
    def _source_path_is_portable(cls, value: str) -> str:
        return _portable_path(value, label="camera source path")

    @model_validator(mode="after")
    def _matrix_is_finite(self) -> RenderCameraRecord:
        if any(not math.isfinite(value) for value in self.camera_to_world):
            raise ValueError("camera transform must be finite")
        return self


class RenderFrameMetric(FrozenModel):
    frame_id: str
    source_path: str
    source_byte_length: int = Field(ge=1)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    render_path: str
    render_byte_length: int = Field(ge=1)
    render_sha256: str = Field(pattern=_SHA256_PATTERN)
    camera_path: str
    camera_byte_length: int = Field(ge=1)
    camera_sha256: str = Field(pattern=_SHA256_PATTERN)
    psnr: float = Field(allow_inf_nan=False)
    ssim: float = Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    lpips: float = Field(ge=0.0, allow_inf_nan=False)

    @field_validator("frame_id")
    @classmethod
    def _frame_id_is_portable(cls, value: str) -> str:
        return _portable_path(value, label="render frame id")

    @field_validator("source_path", "render_path", "camera_path")
    @classmethod
    def _artifact_path_is_portable(cls, value: str) -> str:
        return _portable_path(value, label="render artifact path")


class RenderEvaluationReport(FrozenModel):
    schema_id: Literal["nantai.render-evaluation-report.v1"] = Field(
        default="nantai.render-evaluation-report.v1",
        alias="schema",
        serialization_alias="schema",
    )
    evaluation_id: str = Field(pattern=_ID_PATTERN)
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    held_out_split_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluator_container_digest: str = Field(
        pattern=_CONTAINER_PATTERN,
    )
    protocol: RenderEvaluationProtocol
    frames: tuple[RenderFrameMetric, ...] = Field(min_length=1)
    trainer_config_path: Literal[
        "result/render-evaluation/trainer-config.yml"
    ] = "result/render-evaluation/trainer-config.yml"
    trainer_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    mean_psnr: float = Field(allow_inf_nan=False)
    mean_ssim: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    mean_lpips: float = Field(ge=0.0, allow_inf_nan=False)
    worst_psnr: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def _frame_ids_are_unique(self) -> RenderEvaluationReport:
        frame_ids = tuple(frame.frame_id for frame in self.frames)
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("render evaluation contains duplicate frames")
        return self


class RenderDecision(FrozenModel):
    schema_id: Literal["nantai.render-decision.v1"] = Field(
        default="nantai.render-decision.v1",
        alias="schema",
        serialization_alias="schema",
    )
    accepted: bool
    failed_thresholds: tuple[str, ...]
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    frame_count: int = Field(ge=1)
    mean_psnr: float = Field(allow_inf_nan=False)
    mean_ssim: float = Field(allow_inf_nan=False)
    mean_lpips: float = Field(allow_inf_nan=False)
    worst_psnr: float = Field(allow_inf_nan=False)


def canonical_render_evaluation_bytes(model: BaseModel) -> bytes:
    """Return canonical ASCII JSON with one LF terminator."""

    return canonical_model_bytes(model)


def render_evaluation_sha256(model: BaseModel) -> str:
    return hashlib.sha256(
        canonical_render_evaluation_bytes(model)
    ).hexdigest()


def render_artifact_stem(frame_id: str) -> str:
    portable = _portable_path(frame_id, label="render frame id")
    return hashlib.sha256(portable.encode("utf-8")).hexdigest()


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
    )


def _member_path(root: Path, relative: str, *, label: str) -> Path:
    parsed = PurePosixPath(_portable_path(relative, label=label))
    current = root
    for part in parsed.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise RenderEvaluationError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(mode):
            raise RenderEvaluationError(f"{label} contains a symlink")
    return current


def _read_stable(
    root: Path,
    relative: str,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    path = _member_path(root, relative, label=label)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RenderEvaluationError(f"{label} is not a regular file")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise RenderEvaluationError(
                f"{label} byte length is outside allowed range"
            )
        payload = path.read_bytes()
        after = path.lstat()
    except RenderEvaluationError:
        raise
    except OSError as exc:
        raise RenderEvaluationError(f"{label} cannot be read") from exc
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(payload) != before.st_size
    ):
        raise RenderEvaluationError(f"{label} changed while being read")
    return payload


def _require_binding(
    payload: bytes,
    *,
    expected_length: int,
    expected_sha256: str,
    label: str,
) -> None:
    if (
        len(payload) != expected_length
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise RenderEvaluationError(f"{label} sha256/size mismatch")


def _validate_rgb_png(
    payload: bytes,
    *,
    width: int,
    height: int,
) -> None:
    if not payload.startswith(_PNG_SIGNATURE):
        raise RenderEvaluationError("render is not a lossless RGB PNG")
    offset = len(_PNG_SIGNATURE)
    chunks: list[bytes] = []
    ihdr: bytes | None = None
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise RenderEvaluationError("render is not a lossless RGB PNG")
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        end = offset + 12 + length
        if end > len(payload):
            raise RenderEvaluationError("render is not a lossless RGB PNG")
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:offset + 8 + length]
        declared_crc = struct.unpack(">I", payload[end - 4:end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != declared_crc:
            raise RenderEvaluationError("render is not a lossless RGB PNG")
        chunks.append(kind)
        if kind == b"IHDR":
            ihdr = data
        offset = end
        if kind == b"IEND":
            break
    if (
        offset != len(payload)
        or not chunks
        or chunks[0] != b"IHDR"
        or b"IDAT" not in chunks
        or chunks[-1] != b"IEND"
        or ihdr is None
        or len(ihdr) != 13
    ):
        raise RenderEvaluationError("render is not a lossless RGB PNG")
    actual_width, actual_height = struct.unpack(">II", ihdr[:8])
    bit_depth, colour_type, compression, filtering, interlace = ihdr[8:]
    if (
        actual_width != width
        or actual_height != height
        or bit_depth != 8
        or colour_type != 2
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        raise RenderEvaluationError(
            "render is not an RGB PNG at the policy resolution"
        )


def _validated_model(model: BaseModel, model_type, *, label: str):
    try:
        return model_type.model_validate(
            model.model_dump(by_alias=True),
        )
    except (AttributeError, ValueError) as exc:
        raise RenderEvaluationError(
            f"{label} is invalid: {exc}"
        ) from exc


def validate_render_evaluation(
    policy: RenderEvaluationPolicy,
    report: RenderEvaluationReport,
    root: Path,
) -> RenderDecision:
    """Reopen evidence and derive the only trusted quality decision."""

    if "accepted" in getattr(report, "__dict__", {}):
        raise RenderEvaluationError(
            "report-authored decision is forbidden"
        )
    policy = _validated_model(
        policy,
        RenderEvaluationPolicy,
        label="render evaluation policy",
    )
    report = _validated_model(
        report,
        RenderEvaluationReport,
        label="render evaluation report",
    )
    boundary = Path(root).expanduser().absolute()
    try:
        root_stat = boundary.lstat()
    except OSError as exc:
        raise RenderEvaluationError(
            "render evaluation root is unavailable"
        ) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(
        root_stat.st_mode
    ):
        raise RenderEvaluationError(
            "render evaluation root must be a real directory"
        )

    policy_sha = render_evaluation_sha256(policy)
    if report.policy_sha256 != policy_sha:
        raise RenderEvaluationError(
            "render report policy sha256 mismatch"
        )
    if report.protocol != policy.protocol:
        raise RenderEvaluationError(
            "render evaluation protocol differs from policy"
        )
    if (
        report.evaluator_container_digest
        != policy.evaluator_container_digest
    ):
        raise RenderEvaluationError(
            "render evaluator container digest differs from policy"
        )

    split_bytes = _read_stable(
        boundary,
        policy.held_out_split_path,
        label="held-out split",
        max_bytes=16 * 1024 * 1024,
    )
    split_sha = hashlib.sha256(split_bytes).hexdigest()
    if (
        split_sha != policy.held_out_split_sha256
        or split_sha != report.held_out_split_sha256
    ):
        raise RenderEvaluationError("held-out split sha256 mismatch")
    try:
        json.loads(split_bytes.decode("ascii"))
        split = HeldOutSplit.model_validate_json(split_bytes)
    except (UnicodeError, ValueError) as exc:
        raise RenderEvaluationError("held-out split is invalid") from exc
    if split_bytes != held_out_split_canonical_bytes(split):
        raise RenderEvaluationError("held-out split is not canonical")

    transforms_bytes = _read_stable(
        boundary,
        policy.transforms_path,
        label="camera transforms",
        max_bytes=64 * 1024 * 1024,
    )
    if (
        hashlib.sha256(transforms_bytes).hexdigest()
        != policy.transforms_sha256
    ):
        raise RenderEvaluationError(
            "camera transforms sha256 mismatch"
        )
    trainer_config_bytes = _read_stable(
        boundary,
        report.trainer_config_path,
        label="trainer config",
        max_bytes=16 * 1024 * 1024,
    )
    if (
        hashlib.sha256(trainer_config_bytes).hexdigest()
        != report.trainer_config_sha256
    ):
        raise RenderEvaluationError("trainer config sha256 mismatch")

    held_out = {
        identity.logical_path: identity
        for identity in split.held_out
    }
    frame_ids = tuple(frame.frame_id for frame in report.frames)
    if set(frame_ids) != set(held_out) or len(frame_ids) != len(held_out):
        raise RenderEvaluationError(
            "render frames do not exactly cover held-out identities"
        )

    for frame in report.frames:
        identity = held_out[frame.frame_id]
        stem = render_artifact_stem(frame.frame_id)
        expected_source = (
            f"{policy.source_root}/{identity.logical_path}"
        )
        expected_render = f"{policy.render_root}/{stem}.png"
        expected_camera = f"{policy.camera_root}/{stem}.json"
        if (
            frame.source_path != expected_source
            or frame.source_sha256 != identity.sha256
        ):
            raise RenderEvaluationError(
                f"source identity mismatch: {frame.frame_id}"
            )
        if frame.render_path != expected_render:
            raise RenderEvaluationError(
                f"render path mismatch: {frame.frame_id}"
            )
        if frame.camera_path != expected_camera:
            raise RenderEvaluationError(
                f"camera path mismatch: {frame.frame_id}"
            )

        source_bytes = _read_stable(
            boundary,
            frame.source_path,
            label=f"source frame {frame.frame_id}",
            max_bytes=2 * 1024 * 1024 * 1024,
        )
        _require_binding(
            source_bytes,
            expected_length=frame.source_byte_length,
            expected_sha256=frame.source_sha256,
            label=f"source frame {frame.frame_id}",
        )
        render_bytes = _read_stable(
            boundary,
            frame.render_path,
            label=f"render frame {frame.frame_id}",
            max_bytes=2 * 1024 * 1024 * 1024,
        )
        _require_binding(
            render_bytes,
            expected_length=frame.render_byte_length,
            expected_sha256=frame.render_sha256,
            label=f"render frame {frame.frame_id}",
        )
        _validate_rgb_png(
            render_bytes,
            width=policy.protocol.width,
            height=policy.protocol.height,
        )
        camera_bytes = _read_stable(
            boundary,
            frame.camera_path,
            label=f"camera frame {frame.frame_id}",
            max_bytes=1024 * 1024,
        )
        _require_binding(
            camera_bytes,
            expected_length=frame.camera_byte_length,
            expected_sha256=frame.camera_sha256,
            label=f"camera frame {frame.frame_id}",
        )
        try:
            camera = RenderCameraRecord.model_validate_json(camera_bytes)
        except ValueError as exc:
            raise RenderEvaluationError(
                f"camera frame is invalid: {frame.frame_id}"
            ) from exc
        if camera_bytes != canonical_render_evaluation_bytes(camera):
            raise RenderEvaluationError(
                f"camera frame is not canonical: {frame.frame_id}"
            )
        if (
            camera.frame_id != frame.frame_id
            or camera.source_path != frame.source_path
            or camera.source_sha256 != frame.source_sha256
            or camera.transforms_sha256 != policy.transforms_sha256
        ):
            raise RenderEvaluationError(
                f"camera record differs from held-out source: "
                f"{frame.frame_id}"
            )

    count = len(report.frames)
    mean_psnr = math.fsum(frame.psnr for frame in report.frames) / count
    mean_ssim = math.fsum(frame.ssim for frame in report.frames) / count
    mean_lpips = math.fsum(frame.lpips for frame in report.frames) / count
    worst_psnr = min(frame.psnr for frame in report.frames)
    reported = (
        report.mean_psnr,
        report.mean_ssim,
        report.mean_lpips,
        report.worst_psnr,
    )
    derived = (mean_psnr, mean_ssim, mean_lpips, worst_psnr)
    if reported != derived:
        raise RenderEvaluationError(
            "render evaluation aggregate metrics differ from frames"
        )

    failures: list[str] = []
    if mean_psnr < policy.minimum_mean_psnr:
        failures.append(
            f"mean_psnr {mean_psnr:.6g} < "
            f"{policy.minimum_mean_psnr:.6g}"
        )
    if mean_ssim < policy.minimum_mean_ssim:
        failures.append(
            f"mean_ssim {mean_ssim:.6g} < "
            f"{policy.minimum_mean_ssim:.6g}"
        )
    if mean_lpips > policy.maximum_mean_lpips:
        failures.append(
            f"mean_lpips {mean_lpips:.6g} > "
            f"{policy.maximum_mean_lpips:.6g}"
        )
    if worst_psnr < policy.minimum_worst_psnr:
        failures.append(
            f"worst_psnr {worst_psnr:.6g} < "
            f"{policy.minimum_worst_psnr:.6g}"
        )
    accepted = not failures
    return RenderDecision(
        accepted=accepted,
        failed_thresholds=tuple(failures),
        policy_sha256=policy_sha,
        report_sha256=render_evaluation_sha256(report),
        frame_count=count,
        mean_psnr=mean_psnr,
        mean_ssim=mean_ssim,
        mean_lpips=mean_lpips,
        worst_psnr=worst_psnr,
    )
