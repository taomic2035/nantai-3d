from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

import cloud.validate_dataparser_transform as transform_module
from cloud.validate_dataparser_transform import (
    DataparserTransformError,
    validate_dataparser_transform,
)


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=observed.st_ctime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def test_dataparser_transform_rejects_descriptor_reparse_drift(
    tmp_path,
    monkeypatch,
):
    path = _write(
        tmp_path,
        {
            "scale": 1.0,
            "transform": [
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
            ],
        },
    )
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        observed = original_fstat(descriptor)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(transform_module.os, "fstat", drifting_fstat)

    with pytest.raises(
        DataparserTransformError,
        match="changed while being read",
    ):
        validate_dataparser_transform(path)

    assert calls == 2


def _write(tmp_path, payload):
    path = tmp_path / "dataparser_transforms.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "transform",
    [
        [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
        [
            [1, 0, 0, 0],
            [0, 1, 0, 0],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ],
    ],
)
def test_identity_transform_and_unit_scale_are_accepted(
    tmp_path,
    transform,
):
    path = _write(tmp_path, {"transform": transform, "scale": 1.0})

    evidence = validate_dataparser_transform(path)

    assert evidence.scale == 1.0
    assert evidence.is_identity is True


@pytest.mark.parametrize(
    "payload",
    [
        {"transform": [[1, 0, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0]],
         "scale": 1.0},
        {"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
         "scale": 0.25},
        {"transform": [[1, 0], [0, 1]], "scale": 1.0},
        {"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]},
    ],
)
def test_nonidentity_or_incomplete_transform_is_rejected(tmp_path, payload):
    path = _write(tmp_path, payload)

    with pytest.raises(DataparserTransformError):
        validate_dataparser_transform(path)


def test_duplicate_keys_are_rejected(tmp_path):
    path = tmp_path / "dataparser_transforms.json"
    path.write_text(
        '{"scale":1.0,"scale":1.0,"transform":'
        '[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}',
        encoding="ascii",
    )
    with pytest.raises(DataparserTransformError, match="invalid"):
        validate_dataparser_transform(path)


def test_symlinks_are_rejected(tmp_path):
    path = tmp_path / "dataparser_transforms.json"
    target = tmp_path / "target.json"
    target.write_text(
        '{"scale":1.0,"transform":'
        '[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}',
        encoding="ascii",
    )
    try:
        path.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(
                "Windows SeCreateSymbolicLinkPrivilege not held"
            )
        raise
    with pytest.raises(DataparserTransformError, match="link-like"):
        validate_dataparser_transform(path)
