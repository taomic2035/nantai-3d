from __future__ import annotations

import hashlib
from datetime import date

import pytest

from pipeline.production_capture_inputs import (
    ProductionCaptureInputError,
    main,
    materialize_production_capture_inputs,
)
from pipeline.real_dataset import (
    LocalCaptureSource,
    canonical_model_bytes,
    load_capture_rights_receipt,
    load_real_dataset_source,
    validate_capture_rights,
)


def _materialize(tmp_path, **updates):
    values = {
        "output_dir": tmp_path / "private/site-a",
        "dataset_id": "site-a",
        "operator": "Nantai capture operator",
        "capture_scope": "Mountain village image and video capture",
        "effective_date": date(2026, 7, 27),
        "processing_purposes": (
            "3d-reconstruction",
            "internal-evaluation",
        ),
        "redistribution_allowed": False,
        "release_inclusion_allowed": False,
    }
    values.update(updates)
    (tmp_path / "private").mkdir(exist_ok=True)
    return materialize_production_capture_inputs(**values)


def test_materializer_publishes_canonical_rights_and_bound_source(
    tmp_path,
):
    result = _materialize(tmp_path)

    rights = load_capture_rights_receipt(result.rights_path)
    source = load_real_dataset_source(result.source_path)
    assert isinstance(source, LocalCaptureSource)
    validate_capture_rights(source, rights)
    rights_payload = result.rights_path.read_bytes()
    source_payload = result.source_path.read_bytes()
    assert rights_payload == canonical_model_bytes(rights)
    assert source_payload == canonical_model_bytes(source)
    assert result.rights_sha256 == hashlib.sha256(
        rights_payload
    ).hexdigest()
    assert result.source_sha256 == hashlib.sha256(
        source_payload
    ).hexdigest()
    assert source.rights_receipt_sha256 == result.rights_sha256
    assert source.dataset_id == rights.dataset_id == "site-a"
    assert source.redistribution_allowed is False
    assert source.release_inclusion_allowed is False

    with pytest.raises(
        ProductionCaptureInputError,
        match="output.*absent",
    ):
        _materialize(tmp_path)


def test_materializer_rejects_missing_reconstruction_authorization(
    tmp_path,
):
    with pytest.raises(
        ProductionCaptureInputError,
        match="3d-reconstruction",
    ):
        _materialize(
            tmp_path,
            processing_purposes=("internal-evaluation",),
        )

    assert not (tmp_path / "private/site-a").exists()


def test_materializer_rejects_duplicate_purposes_without_partial_output(
    tmp_path,
):
    with pytest.raises(
        ProductionCaptureInputError,
        match="purposes|unique",
    ):
        _materialize(
            tmp_path,
            processing_purposes=(
                "3d-reconstruction",
                "3d-reconstruction",
            ),
        )

    assert not (tmp_path / "private/site-a").exists()


def test_cli_materializes_private_pair_without_manual_sha(
    tmp_path,
    capsys,
):
    parent = tmp_path / "private"
    parent.mkdir()

    exit_code = main(
        [
            "--output-dir",
            str(parent / "site-a"),
            "--dataset-id",
            "site-a",
            "--operator",
            "Reviewer One",
            "--capture-scope",
            "Village capture",
            "--effective-date",
            "2026-07-27",
            "--processing-purpose",
            "3d-reconstruction",
        ]
    )

    assert exit_code == 0
    assert (parent / "site-a/production-source.json").is_file()
    assert (
        parent / "site-a/capture-rights-receipt.json"
    ).is_file()
    output = capsys.readouterr().out
    assert "Rights SHA-256:" in output
    assert "Source SHA-256:" in output
