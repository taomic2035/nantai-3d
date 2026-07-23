"""Registration SfM quality policy — TDD tests (hardened per REVIEW-CODEX-022 P0.1).

Three-state decision: invocation_succeeded / quality_accepted / training_allowed.
The builder derives every measured field from authoritative artifacts; the
validator re-derives and requires exact equality.  Adversarial tests prove
the validator catches self-reported or misparsed data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraIntrinsics,
    CameraPose,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    GeoAlignment,
    Handedness,
    MetricStatus,
    RegistrationResult,
)
from pipeline.registration_quality import (
    RegistrationQualityPolicy,
    RegistrationQualityReport,
    SessionQualityOutcome,
    SparseModelEntry,
    SparseModelEnumeration,
    build_registration_quality_report,
    enumerate_sparse_models,
    policy_canonical_sha256,
    validate_registration_quality,
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64


# ============================================================
# Authoritative-artifact helpers
# ============================================================

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


def _local_frame() -> CoordinateFrame:
    return CoordinateFrame(
        frame_id="sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SFM,
    )


def _make_intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(width=1920, height=1080, fx=1000.0, fy=1000.0,
                            cx=960.0, cy=540.0)


def _make_registration_result(
    *,
    registered_images: list[str],
    total_images: int,
    session_id: str = "s0",
    engine: str = "colmap",
) -> RegistrationResult:
    """Build a minimal authoritative RegistrationResult.

    ``registered_images`` become poses; ``total_images`` is the full image list
    in the session (so unregistered images are derivable).
    """
    all_images = [f"img{i:03d}.jpg" for i in range(total_images)]
    poses = [
        CameraPose(
            image=img,
            session_id=session_id,
            quat_wxyz=[1.0, 0.0, 0.0, 0.0],
            t_xyz=[0.0, 0.0, 0.0],
            intrinsics=_make_intrinsics(),
        )
        for img in registered_images
    ]
    return RegistrationResult(
        schema_version=2,
        engine=engine,  # type: ignore[arg-type]
        pose_frame=_local_frame(),
        world_frame=None,
        alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[CaptureSession(
            session_id=session_id, kind="photo_batch", source="test",
            images=all_images,
        )],
        poses=poses,
    )


def _make_capture_manifest_bytes(
    *,
    total_images: int,
    source_count: int = 1,
    synthetic: bool = False,
) -> bytes:
    """Build a minimal CaptureRevisionManifest as canonical JSON bytes."""
    from pipeline.ingest_manifest import IngestParams
    from pipeline.studio_revisions import (
        CapturePayload,
        CaptureRevisionManifest,
    )
    payloads = tuple(
        CapturePayload(
            logical_path=f"img{i:03d}.jpg",
            sha256="a" * 64,
            byte_length=1024,
            source_kind="photo",
            source_ordinal=i % source_count,
        )
        for i in range(total_images)
    )
    manifest = CaptureRevisionManifest(
        revision_id=f"capture-{'0' * 32}",
        created_utc=datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC),
        provenance="synthetic" if synthetic else "measured",
        synthetic=synthetic,
        source_count=source_count,
        output_count=total_images,
        ingest_session_id=f"ingest-{'a' * 64}",
        ingest_manifest_sha256="b" * 64,
        ingest_parameters=IngestParams(fps=2.0, max_frames=100,
                                       blur_threshold=60.0, max_long_edge=1920),
        payloads=payloads,
    )
    return (json.dumps(manifest.model_dump(mode="json"), sort_keys=True,
                       ensure_ascii=True) + "\n").encode("ascii")


def _make_sparse_enum(
    *,
    model_image_count: int,
    total_input_images: int,
    model_index: int = 0,
    point3d_count: int = 5000,
) -> SparseModelEnumeration:
    images = tuple(f"img{i:03d}.jpg" for i in range(model_image_count))
    return SparseModelEnumeration(
        models=(SparseModelEntry(
            model_index=model_index,
            image_count=model_image_count,
            point3d_count=point3d_count,
            images=images,
        ),),
        selected_model_index=model_index,
        selection_rule="single_model",
        total_input_images=total_input_images,
    )


def _build_honest_report(
    *,
    registered_count: int = 18,
    total_images: int = 20,
    policy: RegistrationQualityPolicy | None = None,
    engine: str = "colmap",
    with_capture_manifest: bool = True,
    invocation_succeeded: bool = True,
) -> tuple[
    RegistrationQualityReport, RegistrationResult, bytes,
    bytes | None, SparseModelEnumeration | None,
]:
    """Build an honest report via the builder; return all authoritative artifacts."""
    if policy is None:
        policy = _make_policy()
    registered_images = [f"img{i:03d}.jpg" for i in range(registered_count)]
    registration = _make_registration_result(
        registered_images=registered_images, total_images=total_images,
        engine=engine,
    )
    reg_bytes = registration.model_dump_json().encode("utf-8")
    if with_capture_manifest:
        manifest_bytes = _make_capture_manifest_bytes(total_images=total_images)
    else:
        manifest_bytes = None
    if engine == "colmap":
        sparse = _make_sparse_enum(
            model_image_count=registered_count,
            total_input_images=total_images,
        )
    else:
        sparse = None
    from pipeline.studio_revisions import CaptureRevisionManifest
    capture_manifest = (
        None if manifest_bytes is None
        else CaptureRevisionManifest.model_validate_json(manifest_bytes)
    )
    report = build_registration_quality_report(
        registration=registration,
        registration_json_bytes=reg_bytes,
        capture_manifest=capture_manifest,
        capture_manifest_bytes=manifest_bytes,
        policy=policy,
        sparse_enumeration=sparse,
        invocation_succeeded=invocation_succeeded,
    )
    return report, registration, reg_bytes, manifest_bytes, sparse


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

    def test_registration_json_sha_must_be_64_hex(self):
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

    def test_capture_manifest_sha_must_be_64_hex_when_present(self):
        """Adversarial: 'not-a-sha' must be rejected at schema level."""
        with pytest.raises(ValidationError):
            RegistrationQualityReport(
                registration_json_sha256=_SHA_A,
                capture_manifest_sha256="not-a-sha",  # must be 64-hex
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
# Phase 3: sparse model enumeration (hardened COLMAP parser)
# ============================================================

def _write_colmap_model(model_dir: Path, images: list[str], n_points: int) -> None:
    """Write a COLMAP sparse model with paired header+POINTS2D lines."""
    model_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for idx, img in enumerate(images):
        # IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
        lines.append(f"{idx + 1} 1 0 0 0 0 0 0 1 {img}\n")
        lines.append("0 0\n")  # POINTS2D line (empty)
    (model_dir / "images.txt").write_text("".join(lines), encoding="utf-8")
    pts = "".join(f"{i + 1} 0 0 0 0 0 0 1 0 0 0\n" for i in range(n_points))
    (model_dir / "points3D.txt").write_text(pts, encoding="utf-8")


def _write_colmap_model_with_points2d(
    model_dir: Path, image_name: str, points2d_count: int, n_points3d: int = 1,
) -> None:
    """Write a COLMAP sparse model where the POINTS2D row has many triples."""
    model_dir.mkdir(parents=True, exist_ok=True)
    # Image header line (10 tokens).
    header = f"1 1 0 0 0 0 0 0 1 {image_name}\n"
    # POINTS2D line: 4 triples = 12 tokens (was misclassified as image header).
    points = " ".join(
        f"{i * 0.1:.1f} {i * 0.2:.1f} {i}" for i in range(points2d_count)
    )
    lines = header + points + "\n"
    (model_dir / "images.txt").write_text(lines, encoding="utf-8")
    pts = "".join(f"{i + 1} 0 0 0 0 0 0 1 0 0 0\n" for i in range(n_points3d))
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

    def test_colmap_points2d_row_not_counted_as_image(self, tmp_path):
        """Adversarial: 1 image header + 1 POINTS2D row with 4 triples (12 tokens)
        must parse as image_count=1, not 2."""
        sparse = tmp_path / "sparse" / "0"
        _write_colmap_model_with_points2d(
            sparse, image_name="photo.jpg", points2d_count=4)
        enum = enumerate_sparse_models(sparse.parent, total_input_images=1)
        assert len(enum.models[0].images) == 1
        assert enum.models[0].image_count == 1
        assert enum.models[0].images == ("photo.jpg",)

    def test_colmap_odd_data_lines_fails_closed(self, tmp_path):
        """Adversarial: odd number of non-comment data lines must fail-closed
        rather than silently miscounting."""
        sparse = tmp_path / "sparse" / "0"
        sparse.mkdir(parents=True)
        # Only an image header, no POINTS2D line — malformed.
        (sparse / "images.txt").write_text(
            "1 1 0 0 0 0 0 0 1 photo.jpg\n", encoding="utf-8")
        (sparse / "points3D.txt").write_text(
            "1 0 0 0 0 0 0 1 0 0 0\n", encoding="utf-8")
        with pytest.raises(ValueError, match="even|odd|two lines per image"):
            enumerate_sparse_models(sparse.parent, total_input_images=1)


# ============================================================
# Phase 4: three-state decision logic
# ============================================================

class TestThreeStateDecision:
    def test_invocation_succeeded_true_on_valid_engine(self):
        report, _, _, _, _ = _build_honest_report(engine="colmap")
        assert report.invocation_succeeded is True

    def test_invocation_succeeded_false_on_crash(self):
        report, _, _, _, _ = _build_honest_report(
            registered_count=0, invocation_succeeded=False)
        assert report.invocation_succeeded is False
        assert report.training_allowed is False
        assert report.quality_accepted is False
        assert "invocation_succeeded=False" in report.rejection_reasons

    def test_quality_accepted_true_when_all_thresholds_met(self):
        report, _, _, _, _ = _build_honest_report(
            registered_count=18, total_images=20)
        assert report.quality_accepted is True

    def test_quality_accepted_false_below_registered_count(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_registered_count=15)
        report, _, _, _, _ = _build_honest_report(
            registered_count=10, policy=policy)
        accepted, reasons = derive_quality_accepted(report, policy)
        assert accepted is False
        assert any("registered_count" in r or "min_registered_count" in r for r in reasons)

    def test_quality_accepted_false_below_ratio(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_registered_ratio=0.8)
        report, _, _, _, _ = _build_honest_report(
            registered_count=8, total_images=20, policy=policy)
        accepted, _ = derive_quality_accepted(report, policy)
        assert accepted is False

    def test_quality_accepted_false_long_unregistered_run(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(max_unregistered_consecutive_run=3)
        # 15 registered, 5 unregistered at the end → run=5
        report, _, _, _, _ = _build_honest_report(
            registered_count=15, total_images=20, policy=policy)
        accepted, reasons = derive_quality_accepted(report, policy)
        assert accepted is False
        assert any("consecutive" in r.lower() for r in reasons)

    def test_quality_accepted_false_low_model_share(self):
        from pipeline.registration_quality import derive_quality_accepted
        policy = _make_policy(min_largest_connected_model_share=0.8)
        # Use a custom registration + sparse enum where model has 5/20 images
        registration = _make_registration_result(
            registered_images=[f"img{i:03d}.jpg" for i in range(10)],
            total_images=20)
        reg_bytes = registration.model_dump_json().encode("utf-8")
        manifest_bytes = _make_capture_manifest_bytes(total_images=20)
        from pipeline.studio_revisions import CaptureRevisionManifest
        manifest = CaptureRevisionManifest.model_validate_json(manifest_bytes)
        sparse = SparseModelEnumeration(
            models=(
                SparseModelEntry(model_index=0, image_count=5, point3d_count=100,
                                 images=tuple(f"img{i:03d}.jpg" for i in range(5))),
                SparseModelEntry(model_index=1, image_count=5, point3d_count=100,
                                 images=tuple(f"img{i:03d}.jpg" for i in range(5, 10))),
            ),
            selected_model_index=0,
            selection_rule="largest_image_count",
            total_input_images=20,
        )
        report = build_registration_quality_report(
            registration=registration, registration_json_bytes=reg_bytes,
            capture_manifest=manifest, capture_manifest_bytes=manifest_bytes,
            policy=policy, sparse_enumeration=sparse, invocation_succeeded=True)
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
        report, _, _, _, _ = _build_honest_report(engine="mock", policy=policy)
        # mock engine → no sparse enumeration needed; capture manifest still bound
        assert derive_training_allowed(report, policy) is False

    def test_training_allowed_false_without_capture_manifest_sha(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report, _, _, _, _ = _build_honest_report(
            with_capture_manifest=False, policy=policy)
        assert derive_training_allowed(report, policy) is False

    def test_training_allowed_true_only_when_all_conditions_met(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report, _, _, _, _ = _build_honest_report(
            engine="colmap", policy=policy)
        assert derive_training_allowed(report, policy) is True

    def test_training_allowed_false_overrides_rejection_reasons(self):
        from pipeline.registration_quality import derive_training_allowed
        policy = _make_policy()
        report, _, _, _, _ = _build_honest_report(
            registered_count=5, total_images=20, policy=policy)
        assert derive_training_allowed(report, policy) is False


# ============================================================
# Phase 6: builder (derives from authoritative artifacts)
# ============================================================

class TestBuilder:
    def test_builder_derives_registered_count_from_poses(self):
        report, registration, _, _, _ = _build_honest_report(
            registered_count=18, total_images=20)
        assert report.registered_count == 18
        assert report.registered_ratio == pytest.approx(0.9)

    def test_builder_derives_total_from_capture_manifest(self):
        report, _, _, manifest_bytes, _ = _build_honest_report(
            registered_count=18, total_images=20)
        assert report.total_input_images == 20
        expected_manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        assert report.capture_manifest_sha256 == expected_manifest_sha

    def test_builder_derives_total_from_sessions_when_no_manifest(self):
        report, _, _, manifest_bytes, _ = _build_honest_report(
            registered_count=18, total_images=20, with_capture_manifest=False)
        assert manifest_bytes is None
        assert report.capture_manifest_sha256 is None
        assert report.total_input_images == 20

    def test_builder_requires_sparse_for_colmap(self):
        registration = _make_registration_result(
            registered_images=["img000.jpg"], total_images=5, engine="colmap")
        reg_bytes = registration.model_dump_json().encode("utf-8")
        with pytest.raises(ValueError, match="colmap.*sparse|sparse.*colmap"):
            build_registration_quality_report(
                registration=registration, registration_json_bytes=reg_bytes,
                capture_manifest=None, capture_manifest_bytes=None,
                policy=_make_policy(), sparse_enumeration=None,
                invocation_succeeded=True)

    def test_builder_rejects_sparse_for_non_colmap(self):
        registration = _make_registration_result(
            registered_images=["img000.jpg"], total_images=5, engine="external")
        reg_bytes = registration.model_dump_json().encode("utf-8")
        sparse = _make_sparse_enum(model_image_count=1, total_input_images=5)
        with pytest.raises(ValueError, match="not allowed"):
            build_registration_quality_report(
                registration=registration, registration_json_bytes=reg_bytes,
                capture_manifest=None, capture_manifest_bytes=None,
                policy=_make_policy(), sparse_enumeration=sparse,
                invocation_succeeded=True)

    def test_builder_rejects_registration_bytes_mismatch(self):
        registration_a = _make_registration_result(
            registered_images=["img000.jpg"], total_images=5)
        registration_b = _make_registration_result(
            registered_images=["img001.jpg"], total_images=5)
        reg_bytes_b = registration_b.model_dump_json().encode("utf-8")
        with pytest.raises(ValueError, match="does not match"):
            build_registration_quality_report(
                registration=registration_a, registration_json_bytes=reg_bytes_b,
                capture_manifest=None, capture_manifest_bytes=None,
                policy=_make_policy(),
                sparse_enumeration=_make_sparse_enum(
                    model_image_count=1, total_input_images=5),
                invocation_succeeded=True)

    def test_builder_rejects_session_total_mismatch(self):
        """Capture manifest says 20 images but registration.sessions only has 15."""
        registration = _make_registration_result(
            registered_images=[f"img{i:03d}.jpg" for i in range(10)],
            total_images=15)
        reg_bytes = registration.model_dump_json().encode("utf-8")
        manifest_bytes = _make_capture_manifest_bytes(total_images=20)
        from pipeline.studio_revisions import CaptureRevisionManifest
        manifest = CaptureRevisionManifest.model_validate_json(manifest_bytes)
        with pytest.raises(ValueError, match="session.*total|inconsistent"):
            build_registration_quality_report(
                registration=registration, registration_json_bytes=reg_bytes,
                capture_manifest=manifest, capture_manifest_bytes=manifest_bytes,
                policy=_make_policy(),
                sparse_enumeration=_make_sparse_enum(
                    model_image_count=10, total_input_images=20),
                invocation_succeeded=True)


# ============================================================
# Phase 7: validation (re-derive, don't trust — hardened)
# ============================================================

class TestValidation:
    def test_validate_accepts_honest_built_report(self):
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report()
        validate_registration_quality(
            report, _make_policy(), reg_bytes,
            capture_manifest_bytes=manifest_bytes,
            sparse_enumeration=sparse)

    def test_validate_recomputes_policy_sha(self):
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report()
        tampered = report.model_copy(update={"policy_canonical_sha256": _SHA_B})
        with pytest.raises(ValueError, match="policy.*sha|sha.*policy"):
            validate_registration_quality(
                tampered, _make_policy(), reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_validate_recomputes_registration_sha(self):
        report, _, _, manifest_bytes, sparse = _build_honest_report()
        with pytest.raises(ValueError, match="registration.*sha|sha.*registration"):
            validate_registration_quality(
                report, _make_policy(), b"tampered bytes",
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_validate_rederives_quality_accepted(self):
        policy = _make_policy(min_registered_count=25)
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report(
            registered_count=10, policy=policy)
        # Lie: flip quality_accepted to True
        tampered = report.model_copy(update={
            "quality_accepted": True,
            "training_allowed": True,
            "rejection_reasons": (),
        })
        with pytest.raises(ValueError, match="quality_accepted"):
            validate_registration_quality(
                tampered, policy, reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_validate_rederives_training_allowed(self):
        report, _, reg_bytes, manifest_bytes, _ = _build_honest_report(engine="mock")
        # mock engine: training_allowed must be False; lie and set True
        tampered = report.model_copy(update={"training_allowed": True})
        with pytest.raises(ValueError, match="training_allowed"):
            validate_registration_quality(
                tampered, _make_policy(), reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=None)

    def test_validate_rejection_reasons_must_equal_derived(self):
        policy = _make_policy(min_registered_count=25)
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report(
            registered_count=10, policy=policy)
        # Honest report has quality_accepted=False + derived reasons.
        # Tamper: clear rejection_reasons while keeping quality_accepted=False.
        tampered = report.model_copy(update={"rejection_reasons": ()})
        with pytest.raises(ValueError, match="rejection_reason"):
            validate_registration_quality(
                tampered, policy, reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)


# ============================================================
# Phase 8: adversarial fail-closed (REVIEW-CODEX-022 P0.1)
# ============================================================

class TestAdversarialFailClosed:
    """Prove the hardened validator catches self-reported or misparsed data."""

    def test_report_claims_100_but_bytes_say_2_of_20(self):
        """Report binds registration bytes that say 2/20 registered, but
        self-reports registered_count=100, ratio=1.0.  The validator must
        re-derive from RegistrationResult.poses and reject."""
        policy = _make_policy(min_registered_count=10, min_registered_ratio=0.5)
        registration = _make_registration_result(
            registered_images=["img000.jpg", "img001.jpg"], total_images=20)
        reg_bytes = registration.model_dump_json().encode("utf-8")
        reg_sha = hashlib.sha256(reg_bytes).hexdigest()
        manifest_bytes = _make_capture_manifest_bytes(total_images=20)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        sparse = _make_sparse_enum(model_image_count=2, total_input_images=20)
        # Lie: claim 100/100, ratio=1.0, quality_accepted=True
        lying_report = RegistrationQualityReport(
            registration_json_sha256=reg_sha,
            capture_manifest_sha256=manifest_sha,
            policy_canonical_sha256=policy_canonical_sha256(policy),
            engine="colmap",
            registered_count=100,  # lie — bytes say 2
            total_input_images=20,
            registered_ratio=1.0,  # lie
            session_outcomes=(SessionQualityOutcome(
                session_id="s0", registered=100, total=20),),  # lie
            model_enumeration=sparse,
            invocation_succeeded=True,
            quality_accepted=True,  # lie
            training_allowed=True,  # lie
            rejection_reasons=(),
        )
        with pytest.raises(ValueError, match="registered_count"):
            validate_registration_quality(
                lying_report, policy, reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_capture_manifest_sha_bound_but_bytes_missing(self):
        """Report binds capture_manifest_sha256 but the validator receives no
        capture_manifest_bytes — must fail-closed, not silently pass."""
        report, _, reg_bytes, _, sparse = _build_honest_report()
        with pytest.raises(ValueError,
                           match="capture_manifest.*bytes|bytes.*required|cannot verify"):
            validate_registration_quality(
                report, _make_policy(), reg_bytes,
                capture_manifest_bytes=None,
                sparse_enumeration=sparse)

    def test_registration_bytes_not_valid_registration_result(self):
        """registration_json_bytes that don't parse as RegistrationResult
        must fail-closed, even if the SHA matches."""
        bad_bytes = b'{"not": "a registration result"}'
        bad_sha = hashlib.sha256(bad_bytes).hexdigest()
        policy = _make_policy()
        report = RegistrationQualityReport(
            registration_json_sha256=bad_sha,
            capture_manifest_sha256=None,
            policy_canonical_sha256=policy_canonical_sha256(policy),
            engine="external",
            registered_count=0,
            total_input_images=0,
            registered_ratio=0.0,
            invocation_succeeded=False,
            quality_accepted=False,
            training_allowed=False,
            rejection_reasons=("invocation_succeeded=False",),
        )
        with pytest.raises(ValueError, match="RegistrationResult|parse"):
            validate_registration_quality(report, policy, bad_bytes)

    def test_colmap_engine_requires_sparse_enumeration_arg(self):
        """engine='colmap' but no sparse_enumeration passed to validator —
        must fail-closed (previously the enumeration was optional)."""
        report, _, reg_bytes, manifest_bytes, _ = _build_honest_report(engine="colmap")
        with pytest.raises(ValueError, match="sparse_enumeration.*required|colmap.*sparse"):
            validate_registration_quality(
                report, _make_policy(), reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=None)

    def test_colmap_engine_requires_model_enumeration_field(self):
        """engine='colmap' but report.model_enumeration is None — must fail."""
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report(engine="colmap")
        tampered = report.model_copy(update={"model_enumeration": None})
        with pytest.raises(ValueError, match="model_enumeration"):
            validate_registration_quality(
                tampered, _make_policy(), reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_session_outcomes_mismatch_with_derived(self):
        """Report claims session s0 has 18/20 but derived from poses shows 2/20.
        Must fail-closed."""
        policy = _make_policy()
        registration = _make_registration_result(
            registered_images=["img000.jpg", "img001.jpg"], total_images=20)
        reg_bytes = registration.model_dump_json().encode("utf-8")
        reg_sha = hashlib.sha256(reg_bytes).hexdigest()
        manifest_bytes = _make_capture_manifest_bytes(total_images=20)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        sparse = _make_sparse_enum(model_image_count=2, total_input_images=20)
        # Lie in session_outcomes: claim 18 registered
        lying_report = RegistrationQualityReport(
            registration_json_sha256=reg_sha,
            capture_manifest_sha256=manifest_sha,
            policy_canonical_sha256=policy_canonical_sha256(policy),
            engine="colmap",
            registered_count=2,  # honest count (matches poses)
            total_input_images=20,
            registered_ratio=0.1,
            session_outcomes=(SessionQualityOutcome(
                session_id="s0", registered=18, total=20),),  # lie
            model_enumeration=sparse,
            invocation_succeeded=True,
            quality_accepted=False,
            training_allowed=False,
            rejection_reasons=("session s0 coverage 0.1000 < 0.7000",),
        )
        with pytest.raises(ValueError, match="session_outcomes"):
            validate_registration_quality(
                lying_report, policy, reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_engine_mismatch_between_report_and_registration(self):
        """Report claims engine='colmap' but registration.engine='external'."""
        registration = _make_registration_result(
            registered_images=["img000.jpg"], total_images=5, engine="external")
        reg_bytes = registration.model_dump_json().encode("utf-8")
        reg_sha = hashlib.sha256(reg_bytes).hexdigest()
        policy = _make_policy()
        report = RegistrationQualityReport(
            registration_json_sha256=reg_sha,
            capture_manifest_sha256=None,
            policy_canonical_sha256=policy_canonical_sha256(policy),
            engine="colmap",  # lie — registration says external
            registered_count=1,
            total_input_images=5,
            registered_ratio=0.2,
            invocation_succeeded=True,
            quality_accepted=False,
            training_allowed=False,
            rejection_reasons=("registered_count=1 < min_registered_count=10",
                              "registered_ratio=0.2000 < min_registered_ratio=0.8000"),
        )
        with pytest.raises(ValueError, match="engine.*mismatch"):
            validate_registration_quality(report, policy, reg_bytes)

    def test_non_colmap_engine_rejects_sparse_enumeration(self):
        """engine='external' but sparse_enumeration passed to validator —
        must fail (was previously silently accepted)."""
        report, _, reg_bytes, manifest_bytes, _ = _build_honest_report(engine="external")
        sparse = _make_sparse_enum(model_image_count=1, total_input_images=5)
        with pytest.raises(ValueError, match="not allowed"):
            validate_registration_quality(
                report, _make_policy(), reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_rejection_reasons_must_exact_match_not_just_nonempty(self):
        """quality_accepted=False but rejection_reasons contains arbitrary
        text that doesn't match the derived reasons — must fail."""
        policy = _make_policy(min_registered_count=25)
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report(
            registered_count=10, policy=policy)
        # Tamper: replace derived reasons with arbitrary text
        tampered = report.model_copy(update={
            "rejection_reasons": ("arbitrary excuse",),
        })
        with pytest.raises(ValueError, match="rejection_reason"):
            validate_registration_quality(
                tampered, policy, reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)

    def test_capture_manifest_bytes_dont_match_sha(self):
        """Report claims capture_manifest_sha256=X but actual bytes compute Y."""
        report, _, reg_bytes, _, sparse = _build_honest_report()
        wrong_manifest_bytes = _make_capture_manifest_bytes(total_images=99)
        with pytest.raises(ValueError, match="capture_manifest.*sha|sha.*capture"):
            validate_registration_quality(
                report, _make_policy(), reg_bytes,
                capture_manifest_bytes=wrong_manifest_bytes,
                sparse_enumeration=sparse)

    def test_capture_manifest_bytes_not_parseable(self):
        """capture_manifest_bytes that don't parse as CaptureRevisionManifest."""
        report, _, reg_bytes, _, sparse = _build_honest_report()
        bad_manifest_bytes = b'{"not": "a capture manifest"}'
        # Make the SHA match the bad bytes so we get to the parse step
        bad_sha = hashlib.sha256(bad_manifest_bytes).hexdigest()
        tampered = report.model_copy(update={"capture_manifest_sha256": bad_sha})
        with pytest.raises(ValueError, match="CaptureRevisionManifest|parse"):
            validate_registration_quality(
                tampered, _make_policy(), reg_bytes,
                capture_manifest_bytes=bad_manifest_bytes,
                sparse_enumeration=sparse)


# ============================================================
# Phase 9: round-trip and tamper detection
# ============================================================

class TestRoundTrip:
    def test_report_survives_json_roundtrip(self):
        report, _, _, _, _ = _build_honest_report()
        data = report.model_dump_json()
        loaded = RegistrationQualityReport.model_validate_json(data)
        assert loaded == report

    def test_report_written_with_lf_newlines(self, tmp_path):
        report, _, _, _, _ = _build_honest_report()
        path = tmp_path / "quality_report.json"
        path.write_text(report.model_dump_json(indent=2) + "\n", newline="\n")
        raw = path.read_bytes()
        assert b"\r\n" not in raw

    def test_tampered_report_file_fails_validation(self, tmp_path):
        """File-tampering attack: flip quality_accepted to True in the JSON."""
        policy = _make_policy(min_registered_count=25)
        report, _, reg_bytes, manifest_bytes, sparse = _build_honest_report(
            registered_count=10, policy=policy)
        path = tmp_path / "quality_report.json"
        path.write_text(report.model_dump_json(indent=2), newline="\n")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["quality_accepted"] = True
        data["training_allowed"] = True
        data["rejection_reasons"] = []
        path.write_text(json.dumps(data, indent=2), newline="\n")
        loaded = RegistrationQualityReport.model_validate_json(
            path.read_text(encoding="utf-8"))
        with pytest.raises(ValueError, match="quality_accepted|rejection_reason"):
            validate_registration_quality(
                loaded, policy, reg_bytes,
                capture_manifest_bytes=manifest_bytes,
                sparse_enumeration=sparse)
