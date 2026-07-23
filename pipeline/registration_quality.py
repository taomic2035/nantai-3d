"""Registration SfM quality policy — three-state decision + content-addressed report.

Separates "invocation succeeded / coverage insufficient / training-eligible" into
three distinct machine-verifiable states.  Thresholds are operator-supplied (no
defaults); silence is not consent.  The validator re-derives all booleans from
measured fields + policy — it never trusts self-reported pass/fail.

Trust boundary: ``training_allowed=True`` only proves registration satisfies a
coverage policy.  It does NOT prove the photos are real, the camera coverage is
geometrically sufficient for 3DGS, or the scale is metric.  It is a necessary
but not sufficient condition for training.

See: docs/superpowers/specs/2026-07-23-registration-sfm-quality-policy-design.md
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ============================================================
# Policy
# ============================================================

class RegistrationQualityPolicy(FrozenModel):
    """Operator-supplied coverage thresholds.  All fields required — no defaults.

    A default threshold is an implicit recommendation.  The operator must
    explicitly state "I require at least N registered images and M% coverage."
    This also prevents the 2/20 run from implicitly defining a threshold.
    """

    min_registered_count: int = Field(ge=0)
    min_registered_ratio: float = Field(ge=0.0, le=1.0)
    min_session_coverage_ratio: float = Field(ge=0.0, le=1.0)
    max_unregistered_consecutive_run: int = Field(ge=0)
    min_largest_connected_model_share: float = Field(ge=0.0, le=1.0)


def _canonical_json(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, ASCII, no extra whitespace."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)


def policy_canonical_sha256(policy: RegistrationQualityPolicy) -> str:
    """Content-addressed SHA-256 of the policy's canonical JSON bytes."""
    canonical = _canonical_json(policy)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ============================================================
# Sparse model enumeration
# ============================================================

class SparseModelEntry(FrozenModel):
    """One COLMAP sparse model discovered under ``sparse/<index>/``."""

    model_index: int = Field(ge=0)
    image_count: int = Field(ge=0)
    point3d_count: int = Field(ge=0)
    images: tuple[str, ...] = Field(default=())


class SparseModelEnumeration(FrozenModel):
    """Structured output of model discovery from a COLMAP ``sparse/`` directory.

    Replaces the hardcoded ``sparse/"0"`` assumption: if the mapper produces
    multiple connected-component models, the largest by image count is selected
    deterministically.
    """

    models: tuple[SparseModelEntry, ...]
    selected_model_index: int = Field(ge=0)
    selection_rule: Literal["largest_image_count", "largest_point3d_count", "single_model"]
    total_input_images: int = Field(ge=1)

    @property
    def largest_connected_model_share(self) -> float:
        """Fraction of total input images in the selected model."""
        selected = next(
            (m for m in self.models if m.model_index == self.selected_model_index),
            None,
        )
        if selected is None or self.total_input_images == 0:
            return 0.0
        return selected.image_count / self.total_input_images


def _parse_colmap_images_txt(path: Path) -> tuple[str, ...]:
    """Extract image names from a COLMAP images.txt file.

    COLMAP images.txt has two lines per image: an image-id line followed by a
    POINTS2D line.  Only the image-id lines carry the image name.
    """
    images: list[str] = []
    if not path.exists():
        return tuple()
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        # Image-id lines have >= 10 fields (IMAGE_ID, QW..QZ, TX..TZ, CAMERA_ID, NAME)
        # POINTS2D lines have groups of 3 (X, Y, POINT3D_ID) — skip them.
        if len(parts) >= 10:
            images.append(parts[9])
    return tuple(images)


def _parse_colmap_points3d_count(path: Path) -> int:
    """Count points in a COLMAP points3D.txt file."""
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            count += 1
    return count


def enumerate_sparse_models(
    sparse_dir: Path,
    total_input_images: int,
) -> SparseModelEnumeration:
    """Deterministically enumerate COLMAP sparse models and select the largest.

    Selection rule: most registered images → tie-break by point3d count →
    tie-break by lowest model index.  This replaces the ``sparse/"0"`` hardcode.
    """
    if not sparse_dir.exists() or not sparse_dir.is_dir():
        raise ValueError(f"sparse directory does not exist: {sparse_dir}")

    model_dirs = sorted(
        (d for d in sparse_dir.iterdir() if d.is_dir()),
        key=lambda d: d.name,
    )
    if not model_dirs:
        raise ValueError(f"no sparse models found in {sparse_dir}")

    entries: list[SparseModelEntry] = []
    for model_dir in model_dirs:
        try:
            model_index = int(model_dir.name)
        except ValueError:
            continue
        images = _parse_colmap_images_txt(model_dir / "images.txt")
        point3d_count = _parse_colmap_points3d_count(model_dir / "points3D.txt")
        entries.append(SparseModelEntry(
            model_index=model_index,
            image_count=len(images),
            point3d_count=point3d_count,
            images=images,
        ))

    if not entries:
        raise ValueError(f"no valid sparse models found in {sparse_dir}")

    if len(entries) == 1:
        selected = entries[0]
        rule: str = "single_model"
    else:
        # Sort by (-image_count, -point3d_count, model_index) → first wins
        ranked = sorted(
            entries,
            key=lambda e: (-e.image_count, -e.point3d_count, e.model_index),
        )
        selected = ranked[0]
        if selected.point3d_count > 0 and any(
            e.image_count == selected.image_count
            and e.point3d_count != selected.point3d_count
            for e in entries
            if e.model_index != selected.model_index
        ):
            rule = "largest_point3d_count"
        else:
            rule = "largest_image_count"

    enum = SparseModelEnumeration(
        models=tuple(entries),
        selected_model_index=selected.model_index,
        selection_rule=rule,  # type: ignore[arg-type]
        total_input_images=total_input_images,
    )
    return enum


# ============================================================
# Quality report
# ============================================================

class SessionQualityOutcome(FrozenModel):
    """Per-session registration outcome."""

    session_id: str = Field(min_length=1)
    registered: int = Field(ge=0)
    total: int = Field(ge=0)
    unregistered_images: tuple[str, ...] = Field(default=())
    longest_unregistered_run: int = Field(default=0, ge=0)


class RegistrationQualityReport(FrozenModel):
    """Final auditable artefact binding registration, capture, policy, and outcome."""

    # Content-addressed bindings (all 64-hex SHA-256)
    registration_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_manifest_sha256: str | None = None
    policy_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    # Engine identity
    engine: Literal["colmap", "mock", "external"]
    engine_version: str | None = None

    # Measured outcome
    registered_count: int = Field(ge=0)
    total_input_images: int = Field(ge=0)
    registered_ratio: float = Field(ge=0.0, le=1.0)
    session_outcomes: tuple[SessionQualityOutcome, ...] = Field(default=())
    model_enumeration: SparseModelEnumeration | None = None

    # Three-state decision (the core contract)
    invocation_succeeded: bool
    quality_accepted: bool
    training_allowed: bool

    # Rejection reasons (empty = accepted; non-empty = why rejected)
    rejection_reasons: tuple[str, ...] = Field(default=())


# ============================================================
# Decision derivation
# ============================================================

def derive_quality_accepted(
    report: RegistrationQualityReport,
    policy: RegistrationQualityPolicy,
) -> tuple[bool, list[str]]:
    """Re-derive quality_accepted from measured fields + policy thresholds.

    Returns (accepted, reasons).  reasons is empty iff accepted is True.
    """
    reasons: list[str] = []

    if not report.invocation_succeeded:
        reasons.append("invocation_succeeded=False")
        return False, reasons

    if report.registered_count < policy.min_registered_count:
        reasons.append(
            f"registered_count={report.registered_count} "
            f"< min_registered_count={policy.min_registered_count}"
        )

    if report.registered_ratio < policy.min_registered_ratio:
        reasons.append(
            f"registered_ratio={report.registered_ratio:.4f} "
            f"< min_registered_ratio={policy.min_registered_ratio:.4f}"
        )

    for session in report.session_outcomes:
        if session.total == 0:
            continue
        session_ratio = session.registered / session.total
        if session_ratio < policy.min_session_coverage_ratio:
            reasons.append(
                f"session {session.session_id} coverage "
                f"{session_ratio:.4f} < {policy.min_session_coverage_ratio:.4f}"
            )

        if session.longest_unregistered_run > policy.max_unregistered_consecutive_run:
            reasons.append(
                f"session {session.session_id} consecutive unregistered run "
                f"{session.longest_unregistered_run} > "
                f"{policy.max_unregistered_consecutive_run}"
            )

    if report.model_enumeration is not None:
        share = report.model_enumeration.largest_connected_model_share
        if share < policy.min_largest_connected_model_share:
            reasons.append(
                f"largest_connected_model_share={share:.4f} "
                f"< {policy.min_largest_connected_model_share:.4f}"
            )

    return (len(reasons) == 0), reasons


def derive_training_allowed(
    report: RegistrationQualityReport,
    policy: RegistrationQualityPolicy,
) -> bool:
    """Re-derive training_allowed with fail-closed rules.

    training_allowed = quality_accepted
                      AND engine != "mock"
                      AND capture_manifest_sha256 is not None
                      AND no rejection_reasons
    """
    if not report.invocation_succeeded:
        return False
    if report.engine == "mock":
        return False
    if report.capture_manifest_sha256 is None:
        return False
    if report.rejection_reasons:
        return False
    accepted, _ = derive_quality_accepted(report, policy)
    return accepted


# ============================================================
# Validation (re-derive, don't trust)
# ============================================================

def validate_registration_quality(
    report: RegistrationQualityReport,
    policy: RegistrationQualityPolicy,
    registration_json_bytes: bytes,
) -> None:
    """Validate content closure: re-derive all booleans, never trust self-reported.

    Raises ValueError on any mismatch.
    """
    # 1. Policy SHA
    expected_policy_sha = policy_canonical_sha256(policy)
    if report.policy_canonical_sha256 != expected_policy_sha:
        raise ValueError(
            f"policy_canonical_sha256 mismatch: report claims "
            f"{report.policy_canonical_sha256} but policy computes {expected_policy_sha}"
        )

    # 2. Registration JSON SHA
    expected_reg_sha = hashlib.sha256(registration_json_bytes).hexdigest()
    if report.registration_json_sha256 != expected_reg_sha:
        raise ValueError(
            f"registration_json_sha256 mismatch: report claims "
            f"{report.registration_json_sha256} but bytes compute {expected_reg_sha}"
        )

    # 3. Re-derive quality_accepted
    derived_accepted, reasons = derive_quality_accepted(report, policy)
    if report.quality_accepted != derived_accepted:
        raise ValueError(
            f"quality_accepted mismatch: report claims {report.quality_accepted} "
            f"but derivation gives {derived_accepted} (reasons: {reasons})"
        )

    # 4. Re-derive training_allowed
    derived_training = derive_training_allowed(report, policy)
    if report.training_allowed != derived_training:
        raise ValueError(
            f"training_allowed mismatch: report claims {report.training_allowed} "
            f"but derivation gives {derived_training}"
        )

    # 5. Rejection reasons consistency
    if not report.quality_accepted and not report.rejection_reasons:
        raise ValueError(
            "quality_accepted=False but rejection_reasons is empty — "
            "rejection must be explained"
        )
    if report.quality_accepted and report.rejection_reasons:
        raise ValueError(
            "quality_accepted=True but rejection_reasons is non-empty — "
            "acceptance must have no reasons"
        )
