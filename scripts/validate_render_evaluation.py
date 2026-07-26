"""Validate a held-out render report without requiring CUDA."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path

from pydantic import BaseModel

from pipeline.render_evaluation import (
    RenderEvaluationError,
    RenderEvaluationPolicy,
    RenderEvaluationReport,
    canonical_render_evaluation_bytes,
    validate_render_evaluation,
)


class DocumentLoadError(ValueError):
    """A policy or report document is not canonical and stable."""


def _signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
    )


def _read_document(
    path: Path,
    *,
    label: str,
) -> bytes:
    candidate = Path(path).expanduser().absolute()
    try:
        before = candidate.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > 16 * 1024 * 1024
        ):
            raise DocumentLoadError(
                f"{label} must be a bounded regular non-link file"
            )
        payload = candidate.read_bytes()
        after = candidate.lstat()
    except DocumentLoadError:
        raise
    except OSError as exc:
        raise DocumentLoadError(f"{label} cannot be read") from exc
    if (
        _signature(before) != _signature(after)
        or len(payload) != before.st_size
    ):
        raise DocumentLoadError(
            f"{label} changed while being read"
        )
    return payload


def _load_model(
    payload: bytes,
    model_type,
    *,
    label: str,
) -> BaseModel:
    try:
        model = model_type.model_validate_json(payload)
    except ValueError as exc:
        raise DocumentLoadError(f"{label} is invalid") from exc
    if payload != canonical_render_evaluation_bytes(model):
        raise DocumentLoadError(f"{label} is not canonical JSON")
    return model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate held-out render evaluation evidence",
    )
    parser.add_argument("policy", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)

    policy_bytes: bytes | None = None
    report_bytes: bytes | None = None
    try:
        policy_bytes = _read_document(args.policy, label="policy")
        report_bytes = _read_document(args.report, label="report")
        policy = _load_model(
            policy_bytes,
            RenderEvaluationPolicy,
            label="policy",
        )
        report = _load_model(
            report_bytes,
            RenderEvaluationReport,
            label="report",
        )
        decision = validate_render_evaluation(
            policy,
            report,
            args.root,
        )
    except (
        DocumentLoadError,
        RenderEvaluationError,
        OSError,
        ValueError,
    ) as exc:
        if policy_bytes is not None:
            print(
                "policy_sha256="
                + hashlib.sha256(policy_bytes).hexdigest()
            )
        if report_bytes is not None:
            print(
                "report_sha256="
                + hashlib.sha256(report_bytes).hexdigest()
            )
        print(f"render evaluation invalid: {exc}", file=sys.stderr)
        return 2

    print(f"policy_sha256={decision.policy_sha256}")
    print(f"report_sha256={decision.report_sha256}")
    print(f"accepted={decision.accepted}")
    print(
        "metrics: "
        f"mean_psnr={decision.mean_psnr:.6g} "
        f"mean_ssim={decision.mean_ssim:.6g} "
        f"mean_lpips={decision.mean_lpips:.6g} "
        f"worst_psnr={decision.worst_psnr:.6g}"
    )
    for failure in decision.failed_thresholds:
        print(f"failed: {failure}")
    return 0 if decision.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
