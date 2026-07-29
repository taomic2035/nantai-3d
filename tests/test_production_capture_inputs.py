from __future__ import annotations

import hashlib
import stat
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.production_capture_inputs as inputs_module
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
from pipeline.registration_quality import RegistrationQualityPolicy


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
        "min_registered_count": 90,
        "min_registered_ratio": 0.9,
        "min_session_coverage_ratio": 0.9,
        "max_unregistered_consecutive_run": 5,
        "min_largest_connected_model_share": 0.95,
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
    policy_payload = result.registration_policy_path.read_bytes()
    policy = RegistrationQualityPolicy.model_validate_json(
        policy_payload
    )
    assert rights_payload == canonical_model_bytes(rights)
    assert source_payload == canonical_model_bytes(source)
    assert policy_payload == canonical_model_bytes(policy)
    assert result.rights_sha256 == hashlib.sha256(
        rights_payload
    ).hexdigest()
    assert result.source_sha256 == hashlib.sha256(
        source_payload
    ).hexdigest()
    assert result.registration_policy_sha256 == hashlib.sha256(
        policy_payload
    ).hexdigest()
    assert source.rights_receipt_sha256 == result.rights_sha256
    assert source.dataset_id == rights.dataset_id == "site-a"
    assert source.redistribution_allowed is False
    assert source.release_inclusion_allowed is False
    assert policy == RegistrationQualityPolicy(
        min_registered_count=90,
        min_registered_ratio=0.9,
        min_session_coverage_ratio=0.9,
        max_unregistered_consecutive_run=5,
        min_largest_connected_model_share=0.95,
    )

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


def test_materializer_rejects_invalid_registration_thresholds(
    tmp_path,
):
    with pytest.raises(
        ProductionCaptureInputError,
        match="registration|less than or equal",
    ):
        _materialize(
            tmp_path,
            min_registered_ratio=1.1,
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
            "--min-registered-count",
            "90",
            "--min-registered-ratio",
            "0.9",
            "--min-session-coverage-ratio",
            "0.9",
            "--max-unregistered-consecutive-run",
            "5",
            "--min-largest-connected-model-share",
            "0.95",
        ]
    )

    assert exit_code == 0
    assert (parent / "site-a/production-source.json").is_file()
    assert (
        parent / "site-a/capture-rights-receipt.json"
    ).is_file()
    assert (parent / "site-a/registration-policy.json").is_file()
    output = capsys.readouterr().out
    assert "Rights SHA-256:" in output
    assert "Source SHA-256:" in output


def test_production_capture_inputs_source_has_no_bare_read_bytes() -> None:
    """Static contract: trust-critical reads must not use Path.read_bytes."""
    source_path = Path(inputs_module.__file__)
    source_text = source_path.read_text(encoding="utf-8")
    forbidden = ".read_bytes()"
    assert forbidden not in source_text, (
        "production_capture_inputs.py must not contain bare .read_bytes() calls"
    )


def test_capture_input_link_check_rejects_windows_reparse_attribute() -> None:
    candidate = SimpleNamespace(
        is_symlink=lambda: False,
        is_junction=lambda: False,
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_file_attributes=0x400,
        ),
    )

    assert inputs_module._is_linklike(candidate)


def test_capture_input_file_identity_binds_windows_reparse_attribute() -> None:
    common = {
        "st_dev": 1,
        "st_ino": 2,
        "st_mode": stat.S_IFREG | 0o600,
        "st_size": 3,
        "st_mtime_ns": 4,
    }

    assert inputs_module._file_identity(
        SimpleNamespace(**common, st_file_attributes=0)
    ) != inputs_module._file_identity(
        SimpleNamespace(**common, st_file_attributes=0x400)
    )


def test_capture_input_stable_read_rejects_descriptor_after_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "registration-policy.json"
    target.write_bytes(b"payload\n")
    original_fstat = inputs_module.os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        observed = original_fstat(descriptor)
        calls += 1
        if calls != 2:
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=observed.st_mode,
            st_size=observed.st_size + 1,
            st_mtime_ns=observed.st_mtime_ns,
        )

    monkeypatch.setattr(inputs_module.os, "fstat", drifting_fstat)

    with pytest.raises(
        ProductionCaptureInputError,
        match="changed during read",
    ):
        inputs_module._stable_read_bytes(
            target,
            label="registration policy",
        )
