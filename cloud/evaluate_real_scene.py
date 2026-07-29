"""Pinned held-out renderer for a production Nerfstudio scene.

The orchestration layer is dependency-injected and CPU-testable.  The default
backend imports Nerfstudio, PyTorch and LPIPS lazily inside the pinned CUDA
container; this Mac is not represented as having executed those metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pipeline.real_scene_training import (
    HeldOutSplit,
    held_out_split_canonical_bytes,
)
from pipeline.render_evaluation import (
    RenderCameraRecord,
    RenderEvaluationError,
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    RenderFrameMetric,
    canonical_render_evaluation_bytes,
    render_artifact_stem,
    render_evaluation_sha256,
    validate_render_evaluation,
)


class RealSceneEvaluatorError(ValueError):
    """The pinned evaluator cannot produce content-closed evidence."""


@dataclass(frozen=True)
class EvaluatedFrame:
    """One backend result before artifact paths and hashes are assigned."""

    frame_id: str
    render_png_bytes: bytes
    camera: RenderCameraRecord
    psnr: float
    ssim: float
    lpips: float


class EvaluationBackend(Protocol):
    def evaluate(
        self,
        *,
        config_path: Path,
        prepared_root: Path,
        protocol: RenderEvaluationProtocol,
    ) -> tuple[EvaluatedFrame, ...]:
        raise NotImplementedError


_PINNED_NERFSTUDIO_VERSION = "1.1.5"
_MAX_CONFIG_BYTES = 16 * 1024 * 1024
_MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _is_linklike(path: Path) -> bool:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0)) & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _read_regular(
    path: Path,
    *,
    label: str,
    max_bytes: int,
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    candidate = Path(path).expanduser().absolute()
    try:
        before = candidate.lstat()
        if _is_linklike(candidate) or not stat.S_ISREG(
            before.st_mode
        ):
            raise RealSceneEvaluatorError(
                f"{label} must be a regular non-link file"
            )
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise RealSceneEvaluatorError(
                f"{label} byte length is outside allowed range"
            )
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            stream = os.fdopen(descriptor, "rb", buffering=0)
        except OSError:
            os.close(descriptor)
            raise
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(descriptor_before.st_mode)
                or _stat_signature(descriptor_before)
                != _stat_signature(before)
            ):
                raise RealSceneEvaluatorError(
                    f"{label} changed before read"
                )
            payload = stream.read(max_bytes + 1)
            descriptor_after = os.fstat(stream.fileno())
        after = candidate.lstat()
    except RealSceneEvaluatorError:
        raise
    except OSError as exc:
        raise RealSceneEvaluatorError(f"{label} cannot be read") from exc
    signature = _stat_signature(before)
    if (
        signature != _stat_signature(after)
        or _stat_signature(descriptor_before)
        != _stat_signature(descriptor_after)
        or len(payload) > max_bytes
        or len(payload) != before.st_size
    ):
        raise RealSceneEvaluatorError(
            f"{label} changed while being read"
        )
    return payload, signature


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise RealSceneEvaluatorError(
            f"evaluation artifact cannot be written: {path.name}"
        ) from exc


def _load_split(
    root: Path,
) -> tuple[HeldOutSplit, bytes]:
    payload, _signature = _read_regular(
        root / "prepared/evidence/held-out-split.json",
        label="held-out split",
        max_bytes=16 * 1024 * 1024,
    )
    try:
        split = HeldOutSplit.model_validate_json(payload)
    except ValueError as exc:
        raise RealSceneEvaluatorError(
            "held-out split is invalid"
        ) from exc
    if payload != held_out_split_canonical_bytes(split):
        raise RealSceneEvaluatorError(
            "held-out split is not canonical"
        )
    return split, payload


def _load_transforms(
    root: Path,
    split: HeldOutSplit,
) -> bytes:
    payload, _signature = _read_regular(
        root / "prepared/transforms.json",
        label="prepared transforms",
        max_bytes=64 * 1024 * 1024,
    )
    try:
        document = json.loads(payload.decode("ascii"))
    except (UnicodeError, ValueError) as exc:
        raise RealSceneEvaluatorError(
            "prepared transforms are invalid"
        ) from exc
    expected = {
        f"images/{identity.logical_path}"
        for identity in split.held_out
    }
    test_filenames = document.get("test_filenames")
    if (
        not isinstance(test_filenames, list)
        or any(not isinstance(item, str) for item in test_filenames)
        or len(test_filenames) != len(set(test_filenames))
        or set(test_filenames) != expected
    ):
        raise RealSceneEvaluatorError(
            "prepared transforms do not exactly bind held-out test filenames"
        )
    return payload


def build_render_evaluation_policy(
    run_root: Path,
    *,
    evaluator_container_digest: str,
    expected_split_sha256: str,
) -> RenderEvaluationPolicy:
    """Bind the fixed v1 policy to one prepared dataset's exact bytes."""

    root = Path(run_root).expanduser().absolute()
    split, split_bytes = _load_split(root)
    split_sha = hashlib.sha256(split_bytes).hexdigest()
    if split_sha != expected_split_sha256:
        raise RealSceneEvaluatorError(
            "held-out split differs from expected sha256"
        )
    transforms_bytes = _load_transforms(root, split)
    try:
        return RenderEvaluationPolicy(
            held_out_split_sha256=split_sha,
            transforms_sha256=hashlib.sha256(
                transforms_bytes
            ).hexdigest(),
            evaluator_container_digest=evaluator_container_digest,
            protocol=RenderEvaluationProtocol(
                width=800,
                height=600,
                crop_mode="center-crop",
                colour_space="srgb",
                alpha_handling="reject",
                mask_handling="none",
                ssim_window_size=11,
                ssim_sigma=1.5,
                ssim_data_range=1.0,
                lpips_backbone="alex",
            ),
            minimum_mean_psnr=24.0,
            minimum_mean_ssim=0.80,
            maximum_mean_lpips=0.25,
            minimum_worst_psnr=18.0,
        )
    except ValueError as exc:
        raise RealSceneEvaluatorError(
            "render evaluation policy inputs are invalid"
        ) from exc


def _real_result_parent(root: Path) -> Path:
    result = root / "result"
    try:
        if not result.exists():
            result.mkdir()
        result_stat = result.lstat()
    except OSError as exc:
        raise RealSceneEvaluatorError(
            "evaluation result parent is unavailable"
        ) from exc
    if stat.S_ISLNK(result_stat.st_mode) or not stat.S_ISDIR(
        result_stat.st_mode
    ):
        raise RealSceneEvaluatorError(
            "evaluation result parent must be a real directory"
        )
    return result


def _recheck_config(
    path: Path,
    original: bytes,
    signature: tuple[int, int, int, int, int, int],
) -> None:
    current, current_signature = _read_regular(
        path,
        label="trainer config",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    if current != original or current_signature != signature:
        raise RealSceneEvaluatorError(
            "trainer config changed during evaluation"
        )


def evaluate_real_scene(
    config_path: Path,
    run_root: Path,
    policy: RenderEvaluationPolicy,
    *,
    backend: EvaluationBackend,
    evaluation_id: str,
) -> RenderEvaluationReport:
    """Run one backend, close every byte, then publish one report directory."""

    root = Path(run_root).expanduser().absolute()
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise RealSceneEvaluatorError(
            "evaluation run root is unavailable"
        ) from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(
        root_stat.st_mode
    ):
        raise RealSceneEvaluatorError(
            "evaluation run root must be a real directory"
        )
    try:
        policy = RenderEvaluationPolicy.model_validate(
            policy.model_dump(by_alias=True)
        )
    except (AttributeError, ValueError) as exc:
        raise RealSceneEvaluatorError(
            "render evaluation policy is invalid"
        ) from exc
    split, split_bytes = _load_split(root)
    transforms_bytes = _load_transforms(root, split)
    if (
        hashlib.sha256(split_bytes).hexdigest()
        != policy.held_out_split_sha256
        or hashlib.sha256(transforms_bytes).hexdigest()
        != policy.transforms_sha256
    ):
        raise RealSceneEvaluatorError(
            "evaluation policy differs from prepared dataset"
        )

    config = Path(config_path).expanduser().absolute()
    config_bytes, config_signature = _read_regular(
        config,
        label="trainer config",
        max_bytes=_MAX_CONFIG_BYTES,
    )
    result_parent = _real_result_parent(root)
    output = result_parent / "render-evaluation"
    if output.exists() or output.is_symlink():
        raise RealSceneEvaluatorError(
            "render evaluation output boundary must be absent"
        )
    staging = result_parent / (
        f".render-evaluation.{uuid.uuid4().hex}.staging"
    )
    published = False
    try:
        staging.mkdir(mode=0o700)
        _write_new(
            staging / "policy.json",
            canonical_render_evaluation_bytes(policy),
        )
        _write_new(
            staging / "trainer-config.yml",
            config_bytes,
        )
        _write_new(
            staging / "transforms.json",
            transforms_bytes,
        )
        try:
            frames = backend.evaluate(
                config_path=config,
                prepared_root=root / "prepared",
                protocol=policy.protocol,
            )
        except RealSceneEvaluatorError:
            raise
        except Exception as exc:
            raise RealSceneEvaluatorError(
                f"evaluation backend failed: {type(exc).__name__}"
            ) from exc
        _recheck_config(config, config_bytes, config_signature)
        if not isinstance(frames, tuple):
            raise RealSceneEvaluatorError(
                "evaluation backend must return an immutable frame tuple"
            )
        expected_ids = tuple(
            identity.logical_path for identity in split.held_out
        )
        observed_ids = tuple(frame.frame_id for frame in frames)
        if (
            len(observed_ids) != len(set(observed_ids))
            or set(observed_ids) != set(expected_ids)
            or len(observed_ids) != len(expected_ids)
        ):
            raise RealSceneEvaluatorError(
                "evaluation backend did not return exact held-out frames"
            )
        by_id = {frame.frame_id: frame for frame in frames}
        metric_rows: list[RenderFrameMetric] = []
        identity_by_id = {
            identity.logical_path: identity
            for identity in split.held_out
        }
        for frame_id in expected_ids:
            evaluated = by_id[frame_id]
            identity = identity_by_id[frame_id]
            source_path = f"prepared/images/{frame_id}"
            source_bytes, _source_signature = _read_regular(
                root / source_path,
                label=f"held-out source {frame_id}",
                max_bytes=_MAX_SOURCE_BYTES,
            )
            source_sha = hashlib.sha256(source_bytes).hexdigest()
            if source_sha != identity.sha256:
                raise RealSceneEvaluatorError(
                    f"held-out source sha256 mismatch: {frame_id}"
                )
            if (
                evaluated.camera.frame_id != frame_id
                or evaluated.camera.source_path != source_path
                or evaluated.camera.source_sha256 != source_sha
                or evaluated.camera.transforms_sha256
                != policy.transforms_sha256
            ):
                raise RealSceneEvaluatorError(
                    f"camera record differs from held-out source: {frame_id}"
                )
            stem = render_artifact_stem(frame_id)
            render_relative = (
                f"result/render-evaluation/renders/{stem}.png"
            )
            camera_relative = (
                f"result/render-evaluation/cameras/{stem}.json"
            )
            render_bytes = evaluated.render_png_bytes
            camera_bytes = canonical_render_evaluation_bytes(
                evaluated.camera
            )
            _write_new(
                staging / "renders" / f"{stem}.png",
                render_bytes,
            )
            _write_new(
                staging / "cameras" / f"{stem}.json",
                camera_bytes,
            )
            try:
                metric_rows.append(
                    RenderFrameMetric(
                        frame_id=frame_id,
                        source_path=source_path,
                        source_byte_length=len(source_bytes),
                        source_sha256=source_sha,
                        render_path=render_relative,
                        render_byte_length=len(render_bytes),
                        render_sha256=hashlib.sha256(
                            render_bytes
                        ).hexdigest(),
                        camera_path=camera_relative,
                        camera_byte_length=len(camera_bytes),
                        camera_sha256=hashlib.sha256(
                            camera_bytes
                        ).hexdigest(),
                        psnr=evaluated.psnr,
                        ssim=evaluated.ssim,
                        lpips=evaluated.lpips,
                    )
                )
            except ValueError as exc:
                raise RealSceneEvaluatorError(
                    f"backend metrics are invalid: {frame_id}"
                ) from exc
        frame_count = len(metric_rows)
        report = RenderEvaluationReport(
            evaluation_id=evaluation_id,
            policy_sha256=render_evaluation_sha256(policy),
            held_out_split_sha256=policy.held_out_split_sha256,
            evaluator_container_digest=(
                policy.evaluator_container_digest
            ),
            protocol=policy.protocol,
            frames=tuple(metric_rows),
            trainer_config_sha256=hashlib.sha256(
                config_bytes
            ).hexdigest(),
            mean_psnr=math.fsum(
                frame.psnr for frame in metric_rows
            ) / frame_count,
            mean_ssim=math.fsum(
                frame.ssim for frame in metric_rows
            ) / frame_count,
            mean_lpips=math.fsum(
                frame.lpips for frame in metric_rows
            ) / frame_count,
            worst_psnr=min(frame.psnr for frame in metric_rows),
        )
        _write_new(
            staging / "report.json",
            canonical_render_evaluation_bytes(report),
        )
        os.replace(staging, output)
        published = True
        try:
            validate_render_evaluation(policy, report, root)
        except RenderEvaluationError as exc:
            raise RealSceneEvaluatorError(str(exc)) from exc
        _recheck_config(config, config_bytes, config_signature)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if published:
            shutil.rmtree(output, ignore_errors=True)
        raise


def _center_crop_resize(tensor, protocol, functional):
    """Apply the protocol's floor-centred crop and pinned interpolation."""

    if tensor.ndim != 3 or tensor.shape[-1] != 3:
        raise RealSceneEvaluatorError(
            "evaluation images must contain exactly three RGB channels"
        )
    height, width, _channels = tensor.shape
    target_width = protocol.width
    target_height = protocol.height
    if width * target_height > height * target_width:
        crop_height = height
        crop_width = height * target_width // target_height
    else:
        crop_width = width
        crop_height = width * target_height // target_width
    if crop_width <= 0 or crop_height <= 0:
        raise RealSceneEvaluatorError("evaluation crop is empty")
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    cropped = tensor[
        top:top + crop_height,
        left:left + crop_width,
        :,
    ]
    resized = functional.interpolate(
        cropped.permute(2, 0, 1).unsqueeze(0),
        size=(target_height, target_width),
        mode="bilinear",
        align_corners=False,
        antialias=True,
    )
    return resized.clamp(0.0, 1.0)


def _tensor_scalar(value, *, label: str) -> float:
    try:
        scalar = float(value.detach().cpu().reshape(-1)[0].item())
    except (AttributeError, TypeError, ValueError) as exc:
        raise RealSceneEvaluatorError(
            f"Nerfstudio camera {label} is invalid"
        ) from exc
    if not math.isfinite(scalar):
        raise RealSceneEvaluatorError(
            f"Nerfstudio camera {label} is non-finite"
        )
    return scalar


def _rgb_png_bytes(tensor, image_type) -> bytes:
    array = (
        tensor.squeeze(0)
        .permute(1, 2, 0)
        .mul(255.0)
        .round()
        .to("cpu", non_blocking=False)
        .to(dtype=__import__("torch").uint8)
        .numpy()
    )
    image = image_type.fromarray(array, mode="RGB")
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    payload = stream.getvalue()
    stream.close()
    return payload


class NerfstudioEvaluationBackend:
    """Nerfstudio 1.1.5 + PyTorch evaluator loaded only in the CUDA image."""

    def evaluate(
        self,
        *,
        config_path: Path,
        prepared_root: Path,
        protocol: RenderEvaluationProtocol,
    ) -> tuple[EvaluatedFrame, ...]:
        try:
            import importlib.metadata

            import torch
            import torch.nn.functional as functional
            from nerfstudio.cameras.cameras import CameraType
            from nerfstudio.utils.eval_utils import eval_setup
            from PIL import Image
            from torchmetrics.functional.image import (
                structural_similarity_index_measure,
            )
            from torchmetrics.image.lpip import (
                LearnedPerceptualImagePatchSimilarity,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise RealSceneEvaluatorError(
                "pinned Nerfstudio evaluation dependencies are unavailable"
            ) from exc
        version = importlib.metadata.version("nerfstudio")
        if version != _PINNED_NERFSTUDIO_VERSION:
            raise RealSceneEvaluatorError(
                "evaluator requires Nerfstudio exactly 1.1.5"
            )
        if not torch.cuda.is_available():
            raise RealSceneEvaluatorError(
                "pinned evaluator requires a usable CUDA torch stack"
            )
        try:
            _config, pipeline, _checkpoint, _step = eval_setup(
                config_path,
                test_mode="test",
            )
            datamanager = pipeline.datamanager
            dataset = datamanager.eval_dataset
            dataloader = datamanager.fixed_indices_eval_dataloader
            dataparser_outputs = getattr(
                dataset,
                "_dataparser_outputs",
                None,
            )
            if dataparser_outputs is None:
                dataparser_outputs = (
                    datamanager.eval_dataparser_outputs
                )
            filenames = tuple(dataparser_outputs.image_filenames)
        except (AttributeError, AssertionError, OSError, RuntimeError) as exc:
            raise RealSceneEvaluatorError(
                "Nerfstudio test pipeline cannot be loaded"
            ) from exc
        if len(filenames) != len(dataloader):
            raise RealSceneEvaluatorError(
                "Nerfstudio test cameras differ from test filenames"
            )
        lpips_metric = LearnedPerceptualImagePatchSimilarity(
            net_type=protocol.lpips_backbone,
            normalize=True,
        ).to(pipeline.device)
        frames: list[EvaluatedFrame] = []
        images_root = prepared_root / "images"
        pipeline.eval()
        for index, (camera, batch) in enumerate(dataloader):
            filename = Path(filenames[index]).expanduser().absolute()
            try:
                frame_id = filename.relative_to(
                    images_root.absolute()
                ).as_posix()
            except ValueError as exc:
                raise RealSceneEvaluatorError(
                    "Nerfstudio test filename escapes prepared images"
                ) from exc
            if protocol.mask_handling == "none" and "mask" in batch:
                raise RealSceneEvaluatorError(
                    "evaluation policy forbids masks"
                )
            try:
                source = batch["image"].to(pipeline.device)
                with torch.no_grad():
                    outputs = pipeline.model.get_outputs_for_camera(
                        camera
                    )
                    rendered = outputs["rgb"]
            except (KeyError, RuntimeError, TypeError) as exc:
                raise RealSceneEvaluatorError(
                    f"Nerfstudio render failed: {frame_id}"
                ) from exc
            if (
                source.ndim != 3
                or rendered.ndim != 3
                or source.shape[-1] != 3
                or rendered.shape[-1] != 3
            ):
                raise RealSceneEvaluatorError(
                    "alpha handling policy requires RGB-only tensors"
                )
            source_eval = _center_crop_resize(
                source,
                protocol,
                functional,
            )
            render_eval = _center_crop_resize(
                rendered,
                protocol,
                functional,
            )
            render_png = _rgb_png_bytes(render_eval, Image)
            reopened = Image.open(io.BytesIO(render_png))
            reopened.load()
            if reopened.mode != "RGB" or reopened.size != (
                protocol.width,
                protocol.height,
            ):
                raise RealSceneEvaluatorError(
                    "render PNG roundtrip changed pixel contract"
                )
            render_quantized = (
                torch.from_numpy(
                    __import__("numpy").array(
                        reopened,
                        dtype="float32",
                    )
                )
                .to(pipeline.device)
                .permute(2, 0, 1)
                .unsqueeze(0)
                / 255.0
            )
            reopened.close()
            with torch.no_grad():
                mse = torch.mean(
                    (render_quantized - source_eval) ** 2
                ).clamp_min(protocol.psnr_epsilon)
                psnr = float((-10.0 * torch.log10(mse)).item())
                ssim = float(
                    structural_similarity_index_measure(
                        render_quantized,
                        source_eval,
                        data_range=protocol.ssim_data_range,
                        kernel_size=protocol.ssim_window_size,
                        sigma=protocol.ssim_sigma,
                    ).item()
                )
                lpips = float(
                    lpips_metric(
                        render_quantized,
                        source_eval,
                    ).item()
                )
            metrics = (psnr, ssim, lpips)
            if any(not math.isfinite(value) for value in metrics):
                raise RealSceneEvaluatorError(
                    f"non-finite evaluation metric: {frame_id}"
                )
            source_bytes, _source_signature = _read_regular(
                filename,
                label=f"held-out source {frame_id}",
                max_bytes=_MAX_SOURCE_BYTES,
            )
            camera_type = int(
                _tensor_scalar(
                    camera.camera_type,
                    label="camera_type",
                )
            )
            if camera_type != int(CameraType.PERSPECTIVE.value):
                raise RealSceneEvaluatorError(
                    "production Splatfacto evaluation requires "
                    "perspective cameras"
                )
            matrix = tuple(
                float(value)
                for value in camera.camera_to_worlds.detach()
                .cpu()
                .reshape(-1)
                .tolist()
            )
            frames.append(
                EvaluatedFrame(
                    frame_id=frame_id,
                    render_png_bytes=render_png,
                    camera=RenderCameraRecord(
                        frame_id=frame_id,
                        source_path=f"prepared/images/{frame_id}",
                        source_sha256=hashlib.sha256(
                            source_bytes
                        ).hexdigest(),
                        transforms_sha256=hashlib.sha256(
                            _read_regular(
                                prepared_root / "transforms.json",
                                label="transforms.json",
                                max_bytes=_MAX_CONFIG_BYTES,
                            )[0]
                        ).hexdigest(),
                        camera_model="perspective",
                        source_width=int(source.shape[1]),
                        source_height=int(source.shape[0]),
                        fx=_tensor_scalar(camera.fx, label="fx"),
                        fy=_tensor_scalar(camera.fy, label="fy"),
                        cx=_tensor_scalar(camera.cx, label="cx"),
                        cy=_tensor_scalar(camera.cy, label="cy"),
                        camera_to_world=matrix,
                    ),
                    psnr=psnr,
                    ssim=ssim,
                    lpips=lpips,
                )
            )
        return tuple(frames)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate exact held-out real-scene cameras",
    )
    parser.add_argument("--load-config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--expected-split-sha256", required=True)
    parser.add_argument("--evaluator-container-digest", required=True)
    parser.add_argument("--evaluation-id", required=True)
    args = parser.parse_args(argv)
    try:
        policy = build_render_evaluation_policy(
            args.run_root,
            evaluator_container_digest=(
                args.evaluator_container_digest
            ),
            expected_split_sha256=args.expected_split_sha256,
        )
        report = evaluate_real_scene(
            args.load_config,
            args.run_root,
            policy,
            backend=NerfstudioEvaluationBackend(),
            evaluation_id=args.evaluation_id,
        )
    except (OSError, ValueError) as exc:
        print(f"render evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(
        "render evaluation report: "
        f"{args.run_root / 'result/render-evaluation/report.json'}"
    )
    print(
        f"report_sha256={render_evaluation_sha256(report)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
