#!/usr/bin/env python3
"""Fail closed unless Nerfstudio preserved the prepared coordinate frame."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DataparserTransformError(ValueError):
    """The dataparser transform is unsafe, ambiguous, or non-identity."""


@dataclass(frozen=True)
class DataparserTransformEvidence:
    path: Path
    sha256: str
    byte_length: int
    scale: float
    is_identity: bool


_MAX_BYTES = 1024 * 1024
_IDENTITY_3X4 = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
)
_IDENTITY_4X4 = (
    *_IDENTITY_3X4,
    (0.0, 0.0, 0.0, 1.0),
)


def _stat_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataparserTransformError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise DataparserTransformError(f"{label} must be finite")
    return result


def _parse_transform(value: Any) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, list) or len(value) not in {3, 4}:
        raise DataparserTransformError(
            "dataparser transform must be a 3x4 or 4x4 matrix"
        )
    rows: list[tuple[float, ...]] = []
    for row_index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != 4:
            raise DataparserTransformError(
                "dataparser transform must be a 3x4 or 4x4 matrix"
            )
        rows.append(
            tuple(
                _finite_number(
                    item,
                    label=(
                        "dataparser transform "
                        f"[{row_index}][{column_index}]"
                    ),
                )
                for column_index, item in enumerate(row)
            )
        )
    return tuple(rows)


def validate_dataparser_transform(
    path: Path,
) -> DataparserTransformEvidence:
    transform_path = Path(path).expanduser().absolute()
    try:
        before = transform_path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise DataparserTransformError(
                "dataparser transform is missing or link-like"
            )
        if before.st_size <= 0 or before.st_size > _MAX_BYTES:
            raise DataparserTransformError(
                "dataparser transform size is outside the allowed range"
            )
        raw = transform_path.read_bytes()
        after = transform_path.lstat()
    except DataparserTransformError:
        raise
    except OSError as exc:
        raise DataparserTransformError(
            "dataparser transform cannot be read"
        ) from exc
    if _stat_signature(before) != _stat_signature(after):
        raise DataparserTransformError(
            "dataparser transform changed while being read"
        )
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeError, ValueError) as exc:
        raise DataparserTransformError(
            "dataparser transform JSON is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise DataparserTransformError(
            "dataparser transform JSON root must be an object"
        )
    if "scale" not in payload or "transform" not in payload:
        raise DataparserTransformError(
            "dataparser transform is missing scale or transform"
        )
    scale = _finite_number(payload["scale"], label="dataparser scale")
    matrix = _parse_transform(payload["transform"])
    expected = _IDENTITY_3X4 if len(matrix) == 3 else _IDENTITY_4X4
    if scale != 1.0:
        raise DataparserTransformError(
            "dataparser scale is not exactly 1.0"
        )
    if matrix != expected:
        raise DataparserTransformError(
            "dataparser transform is not exactly identity"
        )
    return DataparserTransformEvidence(
        path=transform_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        scale=scale,
        is_identity=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require an identity Nerfstudio dataparser transform and scale 1"
        ),
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        evidence = validate_dataparser_transform(args.path)
    except DataparserTransformError as exc:
        parser.error(str(exc))
    print(
        f"{evidence.sha256} {evidence.byte_length} "
        f"{evidence.path.as_posix()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
