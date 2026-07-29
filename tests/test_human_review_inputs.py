from __future__ import annotations

import inspect
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.human_review_inputs as human_review_inputs_module
from pipeline.human_review_inputs import (
    PRODUCTION_MAXIMUM_SCREENSHOT_BYTES,
    HumanReviewInputError,
    materialize_human_review_policy,
)
from pipeline.real_scene_acceptance import (
    REQUIRED_VISUAL_CATEGORIES,
    HumanReviewPolicy,
    canonical_human_review_policy_bytes,
)
from pipeline.viewer_acceptance import (
    canonical_viewer_performance_policy_bytes,
    canonical_viewer_performance_report_bytes,
)
from tests.test_viewer_acceptance import (
    _policy as _viewer_policy,
)
from tests.test_viewer_acceptance import (
    _report_v2 as _viewer_report_v2,
)


def _viewer_evidence(tmp_path):
    report = _viewer_report_v2(tmp_path)
    report_path = tmp_path / "viewer/report.json"
    report_path.write_bytes(canonical_viewer_performance_report_bytes(report))
    return report, report_path, tmp_path / report.viewer_policy.path


def test_materializer_derives_canonical_policy_from_verified_v2_capture(
    tmp_path,
):
    report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)
    output = tmp_path / "review/human-review-policy.json"

    result = materialize_human_review_policy(
        evidence_root=tmp_path,
        viewer_policy_path=viewer_policy_path,
        viewer_report_path=report_path,
        output_path=output,
    )

    payload = output.read_bytes()
    policy = HumanReviewPolicy.model_validate_json(payload)
    assert result.output_path == output
    assert result.viewer_report_sha256 == report.content_sha256
    assert policy.source_role == "production-acceptance"
    assert policy.required_categories == REQUIRED_VISUAL_CATEGORIES
    assert policy.required_pose_ids == tuple(row.pose_id for row in report.poses)
    assert policy.maximum_screenshot_bytes == PRODUCTION_MAXIMUM_SCREENSHOT_BYTES
    assert payload == canonical_human_review_policy_bytes(policy)

    with pytest.raises(HumanReviewInputError, match="output.*absent"):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=output,
        )


def test_materializer_rejects_capture_artifact_tamper(
    tmp_path,
):
    _report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)
    viewer_policy_path.write_bytes(b"tampered")

    with pytest.raises(
        HumanReviewInputError,
        match="Viewer capture|policy|changed",
    ):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=tmp_path / "review/policy.json",
        )


def test_materializer_rejects_output_outside_evidence_root(
    tmp_path,
):
    _report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)

    with pytest.raises(
        HumanReviewInputError,
        match="evidence root",
    ):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=tmp_path.parent / "escaped-policy.json",
        )


def test_materializer_rejects_viewer_policy_pose_drift(
    tmp_path,
):
    report, report_path, viewer_policy_path = _viewer_evidence(tmp_path)
    drifted = _viewer_policy().model_copy(
        update={"required_pose_ids": tuple(reversed(_viewer_policy().required_pose_ids))}
    )
    viewer_policy_path.write_bytes(canonical_viewer_performance_policy_bytes(drifted))
    assert tuple(row.pose_id for row in report.poses) != (drifted.required_pose_ids)

    with pytest.raises(
        HumanReviewInputError,
        match="policy|pose",
    ):
        materialize_human_review_policy(
            evidence_root=tmp_path,
            viewer_policy_path=viewer_policy_path,
            viewer_report_path=report_path,
            output_path=tmp_path / "review/policy.json",
        )


# ============================================================
# RED → GREEN: secure read and parent directory boundary
# ============================================================


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


def test_read_regular_bytes_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Files exceeding the byte cap must be rejected before read."""
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b"x" * 100)
    original_lstat = Path.lstat

    def oversized_lstat(path):
        observed = original_lstat(path)
        if path == evidence:
            return SimpleNamespace(
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_mode=observed.st_mode,
                st_size=human_review_inputs_module._MAX_VIEWER_EVIDENCE_BYTES + 1,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=getattr(observed, "st_file_attributes", 0),
            )
        return observed

    monkeypatch.setattr(Path, "lstat", oversized_lstat)
    with pytest.raises(HumanReviewInputError, match="bounded regular file"):
        human_review_inputs_module._read_regular_bytes(
            evidence,
            root=tmp_path,
            label="test",
        )


def test_read_regular_bytes_rejects_descriptor_after_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The second fstat (fd_after) must match fd_before."""
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(fd):
        nonlocal calls
        calls += 1
        observed = original_fstat(fd)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(human_review_inputs_module.os, "fstat", drifting_fstat)
    with pytest.raises(HumanReviewInputError, match="changed while being read"):
        human_review_inputs_module._read_regular_bytes(
            evidence,
            root=tmp_path,
            label="test",
        )
    assert calls == 2


def test_read_regular_bytes_rejects_path_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The path lstat after reading must match the pre-open lstat."""
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')
    original_lstat = Path.lstat
    evidence_calls = [0]

    def swapping_lstat(self):
        observed = original_lstat(self)
        if self == evidence:
            evidence_calls[0] += 1
            if evidence_calls[0] >= 3:
                return SimpleNamespace(
                    st_dev=observed.st_dev,
                    st_ino=observed.st_ino + 1,
                    st_mode=observed.st_mode,
                    st_size=observed.st_size,
                    st_mtime_ns=observed.st_mtime_ns,
                    st_ctime_ns=observed.st_ctime_ns,
                    st_file_attributes=getattr(observed, "st_file_attributes", 0),
                )
        return observed

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    with pytest.raises(HumanReviewInputError, match="changed while being read"):
        human_review_inputs_module._read_regular_bytes(
            evidence,
            root=tmp_path,
            label="test",
        )


def test_read_regular_bytes_rejects_symlink(tmp_path: Path) -> None:
    """A symlink evidence file must be rejected."""
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable")
    target = tmp_path / "real.json"
    target.write_bytes(b'{"valid":true}')
    link = tmp_path / "policy.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    with pytest.raises(HumanReviewInputError, match="bounded regular file|traverse"):
        human_review_inputs_module._read_regular_bytes(
            link,
            root=tmp_path,
            label="test",
        )


def test_read_regular_bytes_oserror_does_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """OSError text must not appear in the user-facing error."""
    evidence = tmp_path / "policy.json"
    evidence.write_bytes(b'{"valid":true}')
    private_detail = str(evidence.resolve())

    def fail_open(*args, **kwargs):
        del args, kwargs
        raise OSError(private_detail)

    monkeypatch.setattr(human_review_inputs_module.os, "open", fail_open)
    with pytest.raises(HumanReviewInputError) as captured:
        human_review_inputs_module._read_regular_bytes(
            evidence,
            root=tmp_path,
            label="test",
        )
    assert private_detail not in str(captured.value)


def test_read_regular_bytes_has_no_bare_read_or_path_open() -> None:
    """Static contract: no unbounded read() or path.open in _read_regular_bytes."""
    import re

    source = inspect.getsource(
        human_review_inputs_module._read_regular_bytes
    )
    path_opens = re.findall(r"(?<!os)\.open\s*\(", source)
    assert not path_opens, (
        "_read_regular_bytes must not use Path.open"
    )
    bare_reads = re.findall(r"\.read\(\s*\)", source)
    assert not bare_reads, (
        "_read_regular_bytes must not use unbounded read()"
    )


def test_staging_uses_os_open_with_no_follow() -> None:
    """Static contract: staging file must use os.open, not Path.open."""
    source = inspect.getsource(
        human_review_inputs_module.materialize_human_review_policy
    )
    assert "os.open(" in source, (
        "staging file must be created with os.open"
    )
    assert "O_NOFOLLOW" in source, (
        "staging file must use O_NOFOLLOW"
    )
    assert "O_EXCL" in source, (
        "staging file must use O_EXCL"
    )


def test_staging_verifies_parent_directory_identity() -> None:
    """Static contract: parent directory identity must be re-verified."""
    source = inspect.getsource(
        human_review_inputs_module.materialize_human_review_policy
    )
    assert "capture_real_directory_identity" in source, (
        "parent directory identity must be captured before staging"
    )
    assert "matches_real_directory_identity" in source, (
        "parent directory identity must be re-verified before staging"
    )
