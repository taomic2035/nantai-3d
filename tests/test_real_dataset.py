from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import pipeline.real_dataset as real_dataset_module
from pipeline.real_dataset import (
    CaptureRightsReceipt,
    DatasetEvidenceError,
    DatasetLock,
    DatasetLockEntry,
    DatasetReceipt,
    DatasetReceiptEntry,
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
    load_capture_rights_receipt,
    load_real_dataset_source,
    validate_capture_rights,
    validate_dataset_receipt,
)
from pipeline.registration_quality import RegistrationQualityPolicy

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_REVISION = "4" * 40


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _source(**updates: object) -> HfDatasetSource:
    payload: dict[str, object] = {
        "schema": "nantai.real-dataset-source.v1",
        "dataset_id": "poster",
        "role": "internal-canary",
        "source_kind": "hf-dataset",
        "repository": "nerfstudioteam/datasets",
        "repository_revision": _REVISION,
        "subtree": "poster",
        "capture_subtree": "poster/images",
        "declared_file_count": 1,
        "declared_total_bytes": 5,
        "license_status": "not-declared",
        "redistribution_allowed": False,
        "release_inclusion_allowed": False,
    }
    payload.update(updates)
    return HfDatasetSource.model_validate(payload)


def _honest_dataset(
    tmp_path: Path,
) -> tuple[HfDatasetSource, DatasetLock, DatasetReceipt, Path]:
    source = _source()
    dataset_root = tmp_path / "dataset"
    payload_path = dataset_root / "poster" / "images" / "frame.png"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_bytes(b"image")

    source_sha = _sha256(canonical_model_bytes(source))
    lock = DatasetLock(
        schema="nantai.dataset-lock.v1",
        source_sha256=source_sha,
        repository="nerfstudioteam/datasets",
        repository_revision=_REVISION,
        entries=(
            DatasetLockEntry(
                relative_path="poster/images/frame.png",
                expected_bytes=5,
                server_identity='"etag-image"',
            ),
        ),
    )
    lock_sha = _sha256(canonical_model_bytes(lock))
    receipt = DatasetReceipt(
        schema="nantai.dataset-receipt.v1",
        source_sha256=source_sha,
        lock_sha256=lock_sha,
        entries=(
            DatasetReceiptEntry(
                relative_path="poster/images/frame.png",
                expected_bytes=5,
                server_identity='"etag-image"',
                actual_bytes=5,
                actual_sha256=_sha256(b"image"),
            ),
        ),
    )
    return source, lock, receipt, dataset_root


def test_internal_canary_cannot_enable_release() -> None:
    with pytest.raises(ValidationError, match="release"):
        _source(release_inclusion_allowed=True)


def test_internal_canary_cannot_enable_redistribution() -> None:
    with pytest.raises(ValidationError, match="redistribution"):
        _source(redistribution_allowed=True)


@pytest.mark.parametrize("dataset_id", ["../escape", "nested/id", ".", "bad id"])
def test_dataset_id_is_a_safe_portable_component(dataset_id: str) -> None:
    with pytest.raises(ValidationError, match="dataset_id"):
        _source(dataset_id=dataset_id)

    rights = _rights(dataset_id="safe")
    with pytest.raises(ValidationError, match="dataset_id"):
        _local_source(rights, dataset_id=dataset_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subtree", "../poster"),
        ("subtree", "/poster"),
        ("subtree", r"poster\images"),
        ("capture_subtree", "poster/images/../private"),
        ("capture_subtree", "other/images"),
    ],
)
def test_source_rejects_unsafe_or_unrelated_paths(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError, match="path|subtree"):
        _source(**{field: value})


def test_canonical_bytes_are_sorted_ascii_compact_and_lf_terminated() -> None:
    model = _rights(operator="Pöstér")
    payload = canonical_model_bytes(model)
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert b'": ' not in payload
    assert b", " not in payload
    assert b"\\u00f6" in payload
    assert json.loads(payload) == model.model_dump(
        mode="json",
        by_alias=True,
    )


def test_load_source_rejects_duplicate_keys_and_noncanonical_bytes(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema":"nantai.real-dataset-source.v1","schema":"other"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetEvidenceError, match="duplicate"):
        load_real_dataset_source(duplicate)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(
        json.dumps(_source().model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetEvidenceError, match="canonical"):
        load_real_dataset_source(noncanonical)


def test_load_source_accepts_canonical_hf_and_local_variants(
    tmp_path: Path,
) -> None:
    hf_path = tmp_path / "hf.json"
    hf_path.write_bytes(canonical_model_bytes(_source()))
    assert isinstance(load_real_dataset_source(hf_path), HfDatasetSource)

    local = LocalCaptureSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="production-courtyard",
        role="production-acceptance",
        source_kind="local-capture",
        rights_receipt_sha256=_SHA_A,
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    local_path = tmp_path / "local.json"
    local_path.write_bytes(canonical_model_bytes(local))
    assert load_real_dataset_source(local_path) == local


def test_load_capture_rights_requires_canonical_duplicate_free_bytes(
    tmp_path: Path,
) -> None:
    rights = _rights()
    path = tmp_path / "rights.json"
    path.write_bytes(canonical_model_bytes(rights))
    assert load_capture_rights_receipt(path) == rights

    path.write_text(
        json.dumps(rights.model_dump(mode="json", by_alias=True), indent=2),
        encoding="utf-8",
    )
    with pytest.raises(DatasetEvidenceError, match="canonical"):
        load_capture_rights_receipt(path)

    path.write_text(
        '{"schema":"nantai.capture-rights-receipt.v1",'
        '"schema":"nantai.capture-rights-receipt.v1"}\n',
        encoding="utf-8",
    )
    with pytest.raises(DatasetEvidenceError, match="duplicate"):
        load_capture_rights_receipt(path)


@pytest.mark.parametrize(
    "path",
    [
        "../frame.png",
        "/frame.png",
        r"poster\frame.png",
        "poster/./frame.png",
        "poster//frame.png",
    ],
)
def test_lock_rejects_nonportable_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        DatasetLockEntry(
            relative_path=path,
            expected_bytes=5,
            server_identity='"etag-image"',
        )


def test_lock_and_receipt_reject_casefold_path_collisions() -> None:
    with pytest.raises(ValidationError, match="casefold"):
        DatasetLock(
            schema="nantai.dataset-lock.v1",
            source_sha256=_SHA_A,
            repository="nerfstudioteam/datasets",
            repository_revision=_REVISION,
            entries=(
                DatasetLockEntry(
                    relative_path="poster/A.png",
                    expected_bytes=1,
                    server_identity="a",
                ),
                DatasetLockEntry(
                    relative_path="poster/a.png",
                    expected_bytes=1,
                    server_identity="b",
                ),
            ),
        )

    with pytest.raises(ValidationError, match="casefold"):
        DatasetReceipt(
            schema="nantai.dataset-receipt.v1",
            source_sha256=_SHA_A,
            lock_sha256=_SHA_B,
            entries=(
                DatasetReceiptEntry(
                    relative_path="poster/A.png",
                    expected_bytes=1,
                    server_identity="a",
                    actual_bytes=1,
                    actual_sha256=_SHA_A,
                ),
                DatasetReceiptEntry(
                    relative_path="poster/a.png",
                    expected_bytes=1,
                    server_identity="b",
                    actual_bytes=1,
                    actual_sha256=_SHA_B,
                ),
            ),
        )


def test_receipt_rejects_live_byte_tamper(tmp_path: Path) -> None:
    source, lock, receipt, root = _honest_dataset(tmp_path)
    (root / "poster/images/frame.png").write_bytes(b"other")
    with pytest.raises(DatasetEvidenceError, match="sha256"):
        validate_dataset_receipt(source, lock, receipt, root)


def test_receipt_rejects_source_lock_and_receipt_identity_drift(
    tmp_path: Path,
) -> None:
    source, lock, receipt, root = _honest_dataset(tmp_path)
    changed_lock = lock.model_copy(update={"source_sha256": _SHA_A})
    with pytest.raises(DatasetEvidenceError, match="source_sha256"):
        validate_dataset_receipt(source, changed_lock, receipt, root)

    changed_receipt = receipt.model_copy(update={"lock_sha256": _SHA_B})
    with pytest.raises(DatasetEvidenceError, match="lock_sha256"):
        validate_dataset_receipt(source, lock, changed_receipt, root)


def test_receipt_rejects_missing_and_extra_files(tmp_path: Path) -> None:
    source, lock, receipt, root = _honest_dataset(tmp_path)
    (root / "poster/images/frame.png").unlink()
    with pytest.raises(DatasetEvidenceError, match="file set"):
        validate_dataset_receipt(source, lock, receipt, root)

    (root / "poster/images/frame.png").write_bytes(b"image")
    (root / "poster/images/extra.png").write_bytes(b"extra")
    with pytest.raises(DatasetEvidenceError, match="file set"):
        validate_dataset_receipt(source, lock, receipt, root)


def test_receipt_rejects_symlinked_payload(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    source, lock, receipt, root = _honest_dataset(tmp_path)
    payload = root / "poster/images/frame.png"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"image")
    payload.unlink()
    try:
        payload.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    with pytest.raises(DatasetEvidenceError, match="symlink"):
        validate_dataset_receipt(source, lock, receipt, root)


def test_receipt_rejects_declared_count_and_total_drift(tmp_path: Path) -> None:
    source, lock, receipt, root = _honest_dataset(tmp_path)
    with pytest.raises(DatasetEvidenceError, match="declared_file_count"):
        validate_dataset_receipt(
            source.model_copy(update={"declared_file_count": 2}),
            lock,
            receipt,
            root,
        )
    with pytest.raises(DatasetEvidenceError, match="declared_total_bytes"):
        validate_dataset_receipt(
            source.model_copy(update={"declared_total_bytes": 6}),
            lock,
            receipt,
            root,
        )


def test_hf_receipt_rejects_lock_members_outside_declared_subtree(
    tmp_path: Path,
) -> None:
    source, lock, receipt, root = _honest_dataset(tmp_path)
    escaped_lock = lock.model_copy(
        update={
            "entries": (
                lock.entries[0].model_copy(
                    update={"relative_path": "other/frame.png"}
                ),
            )
        }
    )
    escaped_receipt = receipt.model_copy(
        update={
            "lock_sha256": _sha256(canonical_model_bytes(escaped_lock)),
            "entries": (
                receipt.entries[0].model_copy(
                    update={"relative_path": "other/frame.png"}
                ),
            ),
        }
    )
    (root / "other").mkdir()
    (root / "other/frame.png").write_bytes(b"image")
    (root / "poster/images/frame.png").unlink()
    with pytest.raises(DatasetEvidenceError, match="subtree"):
        validate_dataset_receipt(
            source,
            escaped_lock,
            escaped_receipt,
            root,
        )


def test_honest_receipt_revalidates_live_bytes(tmp_path: Path) -> None:
    source, lock, receipt, root = _honest_dataset(tmp_path)
    validate_dataset_receipt(source, lock, receipt, root)


def _rights(**updates: object) -> CaptureRightsReceipt:
    payload: dict[str, object] = {
        "schema": "nantai.capture-rights-receipt.v1",
        "dataset_id": "production-courtyard",
        "operator": "Nantai capture operator",
        "capture_scope": "Courtyard image and video capture",
        "effective_date": date(2026, 7, 26),
        "processing_purposes": ("3d-reconstruction", "internal-evaluation"),
        "redistribution_allowed": False,
        "release_inclusion_allowed": False,
    }
    payload.update(updates)
    return CaptureRightsReceipt.model_validate(payload)


def _local_source(
    rights: CaptureRightsReceipt,
    **updates: object,
) -> LocalCaptureSource:
    payload: dict[str, object] = {
        "schema": "nantai.real-dataset-source.v1",
        "dataset_id": rights.dataset_id,
        "role": "production-acceptance",
        "source_kind": "local-capture",
        "rights_receipt_sha256": _sha256(canonical_model_bytes(rights)),
        "redistribution_allowed": False,
        "release_inclusion_allowed": False,
    }
    payload.update(updates)
    return LocalCaptureSource.model_validate(payload)


def test_capture_rights_are_content_addressed_and_scope_checked() -> None:
    rights = _rights()
    validate_capture_rights(_local_source(rights), rights)

    with pytest.raises(DatasetEvidenceError, match="rights_receipt_sha256"):
        validate_capture_rights(
            _local_source(rights).model_copy(
                update={"rights_receipt_sha256": _SHA_A}
            ),
            rights,
        )

    mismatched_rights = rights.model_copy(
        update={"dataset_id": "another-capture"}
    )
    source_bound_to_mismatched_rights = _local_source(
        mismatched_rights
    ).model_copy(update={"dataset_id": rights.dataset_id})
    with pytest.raises(DatasetEvidenceError, match="dataset_id"):
        validate_capture_rights(
            source_bound_to_mismatched_rights,
            mismatched_rights,
        )


def test_capture_source_cannot_claim_permissions_missing_from_rights() -> None:
    rights = _rights()
    with pytest.raises(DatasetEvidenceError, match="redistribution"):
        validate_capture_rights(
            _local_source(rights, redistribution_allowed=True),
            rights,
        )
    with pytest.raises(DatasetEvidenceError, match="release"):
        validate_capture_rights(
            _local_source(rights, release_inclusion_allowed=True),
            rights,
        )


def test_capture_rights_must_authorize_3d_reconstruction() -> None:
    rights = _rights(processing_purposes=("internal-evaluation",))
    with pytest.raises(DatasetEvidenceError, match="3d-reconstruction"):
        validate_capture_rights(_local_source(rights), rights)


def test_committed_poster_source_and_policy_are_frozen() -> None:
    source_path = Path("config/real-scene/nerfstudio-poster.json")
    source = load_real_dataset_source(source_path)
    assert source == HfDatasetSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="nerfstudio-poster-internal-canary",
        role="internal-canary",
        source_kind="hf-dataset",
        repository="nerfstudioteam/datasets",
        repository_revision="461701c17e83c3f4d2481db32315aa7df703d2f8",
        subtree="poster",
        capture_subtree="poster/images",
        declared_file_count=408,
        declared_total_bytes=379_280_986,
        license_status="not-declared",
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )

    policy_payload = json.loads(
        Path(
            "config/real-scene/poster-registration-policy.json"
        ).read_text(encoding="utf-8")
    )
    assert RegistrationQualityPolicy.model_validate(policy_payload) == (
        RegistrationQualityPolicy(
            min_registered_count=90,
            min_registered_ratio=0.90,
            min_session_coverage_ratio=0.90,
            max_unregistered_consecutive_run=5,
            min_largest_connected_model_share=0.95,
        )
    )


# ---------------------------------------------------------------------------
# RED → GREEN: stable-read security contracts for real_dataset.py
# ---------------------------------------------------------------------------


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


def test_stable_read_bytes_rejects_descriptor_reparse_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Descriptor-after reparse drift must be rejected."""
    evidence = tmp_path / "source.json"
    evidence.write_bytes(b'{"valid":true}')
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(fd):
        nonlocal calls
        calls += 1
        observed = original_fstat(fd)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(real_dataset_module.os, "fstat", drifting_fstat)

    with pytest.raises(
        DatasetEvidenceError,
        match="changed while being read",
    ):
        real_dataset_module._stable_read_bytes(
            evidence, label="test-evidence"
        )

    assert calls == 2


def test_stable_read_bytes_rejects_short_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Early EOF must be rejected."""
    evidence = tmp_path / "source.json"
    evidence.write_bytes(b'{"valid":true}')

    class ShortStream:
        def __init__(self, fd):
            self._fd = fd
            self._done = False

        def fileno(self):
            return self._fd

        def read(self, size=-1):
            del size
            if self._done:
                return b""
            self._done = True
            return b"short"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

    monkeypatch.setattr(
        real_dataset_module.os,
        "fdopen",
        lambda fd, *a, **kw: ShortStream(fd),
    )

    with pytest.raises(
        DatasetEvidenceError,
        match="changed while being read",
    ):
        real_dataset_module._stable_read_bytes(
            evidence, label="test-evidence"
        )


def test_stable_read_bytes_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Files exceeding the 1 MiB cap must be rejected before read."""
    evidence = tmp_path / "large.json"
    evidence.write_bytes(b"x" * 100)
    original_lstat = Path.lstat

    def oversized_lstat(path):
        observed = original_lstat(path)
        if path == evidence:
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode,
                st_size=(1024 * 1024 + 1),
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=getattr(
                    observed, "st_file_attributes", 0
                ),
            )
        return observed

    monkeypatch.setattr(Path, "lstat", oversized_lstat)

    with pytest.raises(
        DatasetEvidenceError,
        match="not a bounded regular file",
    ):
        real_dataset_module._stable_read_bytes(
            evidence, label="test-evidence"
        )


def test_stable_read_bytes_oserror_does_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """OSError text must not appear in the user-facing error."""
    evidence = tmp_path / "source.json"
    evidence.write_bytes(b'{"valid":true}')
    private_detail = str(evidence.resolve())

    def fail_open(*args, **kwargs):
        del args, kwargs
        raise OSError(private_detail)

    monkeypatch.setattr(real_dataset_module.os, "open", fail_open)

    with pytest.raises(DatasetEvidenceError) as captured:
        real_dataset_module._stable_read_bytes(
            evidence, label="test-evidence"
        )

    assert private_detail not in str(captured.value)


def test_real_dataset_source_has_no_bare_read_bytes() -> None:
    """Static contract: no Path.read_bytes in trust-critical loaders."""
    import re

    source = (
        Path(__file__).resolve().parents[1]
        / "pipeline"
        / "real_dataset.py"
    ).read_text(encoding="utf-8")
    assert not re.search(r"(?<!os)\.open\s*\(", source), (
        "Path.open detected in real_dataset.py"
    )
    read_bytes_calls = re.findall(r"\.read_bytes\s*\(", source)
    assert not read_bytes_calls, (
        "Path.read_bytes detected in real_dataset.py"
    )


# ============================================================
# RED → GREEN: ancestor reparse / junction bypass
# ============================================================


def test_stable_read_bytes_rejects_ancestor_reparse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _stable_read_bytes must reject a reparse-point ancestor."""
    evidence = tmp_path / "source.json"
    evidence.write_bytes(b'{"valid":true}')

    sentinel = tmp_path / "ancestor-reparse"

    def fake_first_linklike_path(root, leaf):
        return sentinel

    monkeypatch.setattr(
        real_dataset_module,
        "first_linklike_path",
        fake_first_linklike_path,
        raising=False,
    )

    with pytest.raises(
        DatasetEvidenceError,
        match="bounded regular file|redirected|unsafe",
    ):
        real_dataset_module._stable_read_bytes(
            evidence, label="test-evidence"
        )
