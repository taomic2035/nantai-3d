from __future__ import annotations

import json

import pytest

from cloud.validate_dataparser_transform import (
    DataparserTransformError,
    validate_dataparser_transform,
)


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


def test_duplicate_keys_and_symlinks_are_rejected(tmp_path):
    path = tmp_path / "dataparser_transforms.json"
    path.write_text(
        '{"scale":1.0,"scale":1.0,"transform":'
        '[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}',
        encoding="ascii",
    )
    with pytest.raises(DataparserTransformError, match="invalid"):
        validate_dataparser_transform(path)

    path.unlink()
    target = tmp_path / "target.json"
    target.write_text(
        '{"scale":1.0,"transform":'
        '[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}',
        encoding="ascii",
    )
    path.symlink_to(target)
    with pytest.raises(DataparserTransformError, match="link-like"):
        validate_dataparser_transform(path)
