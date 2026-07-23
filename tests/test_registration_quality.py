"""Registration SfM quality policy — TDD tests.

Three-state decision: invocation_succeeded / quality_accepted / training_allowed.
Content-addressed policy + report. Re-derive, never trust self-reported booleans.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    RegistrationQualityReport,
    SessionQualityOutcome,
    SparseModelEntry,
    SparseModelEnumeration,
    enumerate_sparse_models,
    policy_canonical_sha256,
    validate_registration_quality,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


def _make_policy(
    *,
    min_registered_count: int = 10,
    min_registered_ratio: float = 0.8,
    min_session_coverage_ratio: float = 0.7,
    max_unregistered_consecutive_run: int = 3,
    min_largest_connected_model_share: float = 0.8,
) -> RegistrationQualityPolicy:
    return RegistrationQualityPolicy(
        min_registered_count=min_registered_count,
        min_registered_ratio=min_registered_ratio,
        min_session_coverage_ratio=min_session_coverage_ratio,
        max_unregistered_consecutive_run=max_unregistered_consecutive_run,
        min_largest_connected_model_share=min_largest_connected_model_share,
    )


def _make_report(
    *,
    registered_count: int = 18,
    total_input_images: int = 20,
    engine: str = "colmap",
    capture_manifest_sha256: str | None = _SHA_C,
    registration_json_sha256: str = _SHA_A,
    model_enumeration: SparseModelEnumeration | None = None,
    session_outcomes: tuple[SessionQualityOutcome, ...] | None = None,
    invocation_succeeded: bool = True,
    quality_accepted: bool = True,
    training_allowed: bool = True,
    rejection_reasons: tuple[str, ...] = (),
    policy: RegistrationQualityPolicy | None = None,
) -> RegistrationQualityReport:
    if model_enumeration is None:
        model_enumeration = SparseModelEnumeration(
            models=(SparseModelEntry(
                model_index=0,
                image_count=registered_count,
                point3d_count=5000,
            ),),
            selected_model_index=0,
            selection_rule="single_model",
            total_input_images=total_input_images,
        )
    if session_outcomes is None:
        session_outcomes = (
            SessionQualityOutcome(
                session_id="s0",
                registered=registered_count,
                total=total_input_images,
            ),
        )
    registered_ratio = registered_count / total_input_images if total_input_images else 0.0
    return RegistrationQualityReport(
        registration_json_sha256=registration_json_sha256,
        capture_manifest_sha256=capture_manifest_sha256,
        policy_canonical_sha256=policy_canonical_sha256(policy or _make_policy()),
        engine=engine,
        engine_version="COLMAP 4.1.0",
        registered_count=registered_count,
        total_input_images=total_input_images,
        registered_ratio=registered_ratio,
        session_outcomes=session_outcomes,
        model_enumeration=model_enumeration,
        invocation_succeeded=invocation_succeeded,
        quality_accepted=quality_accepted,
        training_allowed=training_allowed,
        rejection_reasons=rejection_reasons,
    )


# ============================================================
# Phase 1: schema existence and field validation
# ============================================================

class TestPolicySchema:
    def test_policy_requires_all_thresholds(self):
        with pytest.raises(ValidationError):
            RegistrationQualityPolicy()

    def test_policy_threshold_bounds(self):
        with pytest.raises(ValidationError):
            RegistrationQualityPolicy(
                min_registered_count=0,
                min_registered_ratio=1.5,
                min_session_coverage_ratio=-0.1,
                max_unregistered_consecutive_run=0,
                min_largest_connected_model_share=2.0,
            )

    def test_policy_is_frozen_and_forbids_extra(self):
        policy = _make_policy()
        with pytest.raises((ValidationError, TypeError)):
            policy.min_registered_count = 5  # type: ignore[misc]
        with pytest.raises(ValidationError):
            RegistrationQualityPolicy(
                min_registered_count=10,
                min_registered_ratio=0.8,
                min_session_coverage_ratio=0.7,
                max_unregistered_consecutive_run=3,
                min_largest_connected_model_share=0.8,
                unknown_field=42,  # type: ignore[call-arg]
            )


class TestReportSchema:
    def test_report_requires_all_binding_shas(self):
        with pytest.raises(ValidationError):
            RegistrationQualityReport()

    def test_report_sha_fields_must_be_64_hex(self):
        with pytest.raises(ValidationError):
            RegistrationQualityReport(
                registration_json_sha256="not-a-sha",
                policy_canonical_sha256=_SHA_B,
                engine="colmap",
                registered_count=10,
                total_input_images=20,
                registered_ratio=0.5,
                invocation_succeeded=True,
                quality_accepted=True,
                training_allowed=True,
            )


# ============================================================
# Phase 2: policy canonical SHA
# ============================================================

class TestPolicySha:
    def test_policy_canonical_sha256_is_deterministic(self):
        p1 = _make_policy()
        p2 = _make_policy()
        assert policy_canonical_sha256(p1) == policy_canonical_sha256(p2)

    def test_policy_sha_changes_when_thresholds_change(self):
        p1 = _make_policy(min_registered_count=10)
        p2 = _make_policy(min_registered_count=15)
        assert policy_canonical_sha256(p1) != policy_canonical_sha256(p2)

    def test_policy_sha_uses_lf_and_sort_keys(self):
        policy = _make_policy()
        canonical = json.dumps(policy.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert policy_canonical_sha256(policy) == expected


# ============================================================
# Phase 3: sparse model enumeration
# ============================================================

def _write_colmap_model(model_dir: Path, images: list[str], n_points: int) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for idx, img in enumerate(images):
        # IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
        lines.append(f"{idx + 1} 1 0 0 0 0 0 0 1 {img}\n")
        lines.append("0 0\n")  # POINTS2D line (empty)
    (model_dir / "images.txt").write_text("".join(lines), encoding="utf-8")
    pts = "".join(f"{i + 1} 0 0 0 0 0 0 1 0 0 0\n" for i in range(n_points))
    (model_dir / "points3D.txt").write_text(pts, encoding="utf-8")


class TestSparseModelEnumeration:
    def test_enumeration_selects_largest_model_by_image_count(self, tmp_path):
        sparse = tmp_path / "sparse"
        _write_colmap_model(sparse / "0", ["a", "b", "c"], n_points=100)
        _write_colmap_model(sparse / "1", ["d", "e", "f", "g", "h", "i", "j"], n_points=200)
        enum = enumerate_sparse_models(sparse, total_input_images=10)
        assert enum.selected_model_index == 1
        assert enum.largest_connected_model_share == pytest.approx(0.7)

    def test_enumeration_single_model_has_share_1(self, tmp_path):
        sparse = tmp_path / "sparse"
        _write_colmap_model(sparse / "0", ["a", "b"], n_points=50)
        enum = enumerate_sparse_models(sparse, total_input_images=2)
        assert enum.selection_rule == "single_model"
        assert enum.largest_connected_model_share == 1.0

    def test_enumeration_ties_broken_by_point3d_then_index(self, tmp_path):
        sparse = tmp_path / "sparse"
        _write_colmap_model(sparse / "0", ["a", "b"], n_points=100)
        _write_colmap_model(sparse / "1", ["c", "d"], n_points=200)
        enum = enumerate_sparse_models(sparse, total_input_images=4)
        assert enum.selected_model_index == 1

        sparse2 = tmp_path / "sparse2"
        _write_colmap_model(sparse2 / "0", ["a", "b"], n_points=100)
        _write_colmap_model(sparse2 / "1", ["c", "d"], n_points=100)
        enum2 = enumerate_sparse_models(sparse2, total_input_images=4)
        assert enum2.selected_model_index == 0

    def test_enumeration_empty_dir_fails_closed(self, tmp_path):
        sparse = tmp_path / "sparse"
        sparse.mkdir()
        with pytest.raises(ValueError, match="no.*model|no.*sparse"):
            enumerate_sparse_models(sparse, total_input_images=10)

    def test_enumeration_is_frozen_and_forbids_extra(self):
        with pytest.raises(ValidationError):
            SparseModelEntry(
                model_index=0, image_count=1, point3d_count=1, extra=42,  # type: ignore[call-arg]
            )


# ============================================================
# Phase 4: three-state decision logic
# ============================================================

class TestThreeStateDecision:
    def test_invocation_succeeded_true_on_valid_engine(self):
        report = _make_report(engine="colmap")
        assert report.invocation_succeeded is True

    def test_invocation_succeeded_false_on_crash(self):
        report = _make_report(
            registered_count=0,
            engine="colmap",
            invocation_succeeded=False,
            quality_accepted=False,
            training_allowed=False,
            rejection_reasons=("colmap crashed",),
        )
        assert report.invocation_succeeded is False
        assert report.training_allowed is False

    def test_quality_accepted_true_when_all_thresholds_met(self):
        report = _make_report(registered_count=18, total_input_images=20)
        assert report.quality_accepted is True

    def test_quality_accepted_false_below_registered_count(self, tmp_path):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_registered_count=15)
        report = _make_report(registered_count=10)
        accepted, reasons = derive_quality_accepted(report, policy)
        assert accepted is False
        assert any("registered_count" in r or "min_registered_count" in r for r in reasons)

    def test_quality_accepted_false_below_ratio(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_registered_ratio=0.8)
        report = _make_report(registered_count=8, total_input_images=20)
        accepted, _ = derive_quality_accepted(report, policy)
        assert accepted is False

    def test_quality_accepted_false_low_session_coverage(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_session_coverage_ratio=0.8)
        session_outcomes = (
            SessionQualityOutcome(session_id="s0", registered=2, total=10),
        )
        report = _make_report(
            registered_count=2, total_input_images=10,
            session_outcomes=session_outcomes,
        )
        accepted, reasons = derive_quality_accepted(report, policy)
        assert accepted is False
        assert any("s0" in r for r in reasons)

    def test_quality_accepted_false_long_unregistered_run(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(max_unregistered_consecutive_run=3)
        session_outcomes = (
            SessionQualityOutcome(
                session_id="s0", registered=15, total=20,
                longest_unregistered_run=5,
            ),
        )
        report = _make_report(
            registered_count=15, total_input_images=20,
            session_outcomes=session_outcomes,
        )
        accepted, reasons = derive_quality_accepted(report, policy)
        assert accepted is False
        assert any("consecutive" in r.lower() for r in reasons)

    def test_quality_accepted_false_low_model_share(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_largest_connected_model_share=0.8)
        model_enum = SparseModelEnumeration(
            models=(
                SparseModelEntry(model_index=0, image_count=5, point3d_count=100),
                SparseModelEntry(model_index=1, image_count=5, point3d_count=100),
            ),
            selected_model_index=0,
            selection_rule="largest_image_count",
            total_input_images=20,
        )
        report = _make_report(
            registered_count=10, total_input_images=20,
            model_enumeration=model_enum,
        )
        accepted, reasons = derive_quality_accepted(report, policy)
        assert accepted is False
        assert any("model_share" in r or "connected" in r for r in reasons)


# ============================================================
# Phase 5: training_allowed fail-closed
# ============================================================

class TestTrainingAllowed:
    def test_training_allowed_false_for_mock_engine(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report = _make_report(engine="mock")
        assert derive_training_allowed(report, policy) is False

    def test_training_allowed_false_without_capture_manifest_sha(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report = _make_report(capture_manifest_sha256=None)
        assert derive_training_allowed(report, policy) is False

    def test_training_allowed_true_only_when_all_conditions_met(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report = _make_report(engine="colmap", capture_manifest_sha256=_SHA_C)
        assert derive_training_allowed(report, policy) is True

    def test_training_allowed_false_overrides_rejection_reasons(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report = _make_report(
            rejection_reasons=("manual block",),
            quality_accepted=False,
            training_allowed=False,
        )
        assert derive_training_allowed(report, policy) is False


# ============================================================
# Phase 6: validation (re-derive, don't trust)
# ============================================================

class TestValidation:
    def _registration_bytes(self) -> bytes:
        return b'{"engine":"colmap"}'

    def _reg_sha(self) -> str:
        return hashlib.sha256(self._registration_bytes()).hexdigest()

    def test_validate_recomputes_policy_sha(self):
        policy = _make_policy()
        report = _make_report(policy=policy, registration_json_sha256=self._reg_sha())
        tampered = report.model_copy(update={"policy_canonical_sha256": _SHA_B})
        with pytest.raises(ValueError, match="policy.*sha|sha.*policy"):
            validate_registration_quality(tampered, policy, self._registration_bytes())

    def test_validate_recomputes_registration_sha(self):
        policy = _make_policy()
        report = _make_report(policy=policy, registration_json_sha256=self._reg_sha())
        with pytest.raises(ValueError, match="registration.*sha|sha.*registration"):
            validate_registration_quality(report, policy, b'tampered bytes')

    def test_validate_rederives_quality_accepted(self):
        policy = _make_policy(min_registered_count=15)
        report = _make_report(
            policy=policy,
            registration_json_sha256=self._reg_sha(),
            registered_count=10,
            quality_accepted=True,  # lie
        )
        with pytest.raises(ValueError, match="quality_accepted"):
            validate_registration_quality(report, policy, self._registration_bytes())

    def test_validate_rederives_training_allowed(self):
        policy = _make_policy()
        report = _make_report(
            policy=policy,
            registration_json_sha256=self._reg_sha(),
            engine="mock",
            training_allowed=True,  # lie
        )
        with pytest.raises(ValueError, match="training_allowed"):
            validate_registration_quality(report, policy, self._registration_bytes())

    def test_validate_rejection_reasons_consistency(self):
        policy = _make_policy(min_registered_count=25)
        # quality_accepted=False (genuinely below threshold) but no reasons
        report = _make_report(
            policy=policy,
            registration_json_sha256=self._reg_sha(),
            registered_count=10,
            quality_accepted=False,
            training_allowed=False,
            rejection_reasons=(),
        )
        with pytest.raises(ValueError, match="rejection_reason|reason"):
            validate_registration_quality(report, policy, self._registration_bytes())

        # quality_accepted=True but has reasons
        report2 = _make_report(
            policy=policy,
            registration_json_sha256=self._reg_sha(),
            rejection_reasons=("x",),
        )
        with pytest.raises(ValueError, match="rejection_reason|reason"):
            validate_registration_quality(report2, policy, self._registration_bytes())

    def test_validate_accepts_honest_report(self):
        policy = _make_policy()
        reg_bytes = self._registration_bytes()
        reg_sha = hashlib.sha256(reg_bytes).hexdigest()
        report = _make_report(policy=policy, registration_json_sha256=reg_sha)
        # No exception means pass
        validate_registration_quality(report, policy, reg_bytes)


# ============================================================
# Phase 7: report round-trip and tamper detection
# ============================================================

class TestRoundTrip:
    def test_report_survives_json_roundtrip(self):
        report = _make_report()
        data = report.model_dump_json()
        loaded = RegistrationQualityReport.model_validate_json(data)
        assert loaded == report

    def test_report_written_with_lf_newlines(self, tmp_path):
        report = _make_report()
        path = tmp_path / "quality_report.json"
        path.write_text(report.model_dump_json(indent=2) + "\n", newline="\n")
        raw = path.read_bytes()
        assert b"\r\n" not in raw

    def test_tampered_report_file_fails_validation(self, tmp_path):
        policy = _make_policy(min_registered_count=25)
        reg_bytes = b'{"engine":"colmap"}'
        reg_sha = hashlib.sha256(reg_bytes).hexdigest()
        # Honest report: registered_count=10, below threshold → quality_accepted=False
        report = _make_report(
            policy=policy,
            registration_json_sha256=reg_sha,
            registered_count=10,
            quality_accepted=False,
            training_allowed=False,
            rejection_reasons=("below threshold",),
        )

        path = tmp_path / "quality_report.json"
        path.write_text(report.model_dump_json(indent=2), newline="\n")

        # Tamper: flip quality_accepted to True (lie — count is still below threshold)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["quality_accepted"] = True
        data["training_allowed"] = True
        data["rejection_reasons"] = []
        path.write_text(json.dumps(data, indent=2), newline="\n")

        loaded = RegistrationQualityReport.model_validate_json(
            path.read_text(encoding="utf-8"))
        with pytest.raises(ValueError, match="quality_accepted"):
            validate_registration_quality(loaded, policy, reg_bytes)
