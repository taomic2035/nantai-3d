"""Registration SfM quality policy — measured, not report-authored.

Hardened per REVIEW-CODEX-022 P0.1: the builder and validator derive every
measured field from authoritative artifacts (RegistrationResult bytes, capture
manifest bytes, sparse model enumeration).  Self-reported counts/ratios are
never trusted — the validator re-derives and requires exact equality.

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

from pipeline.recon_schema import RegistrationResult


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


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

    COLMAP images.txt format: every image occupies exactly two lines —
    an image-id header (``IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME``)
    followed by a POINTS2D line (``X Y POINT3D_ID`` triples).  Pairing the
    lines deterministically is the only safe way to avoid counting POINTS2D
    rows as image headers — a POINTS2D line with 4+ points easily exceeds 10
    tokens and was previously misclassified as an image header.
    """
    if not path.exists():
        return tuple()
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    data_lines: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        data_lines.append(stripped)
    if len(data_lines) % 2 != 0:
        raise ValueError(
            f"COLMAP images.txt {path} has {len(data_lines)} non-comment data lines; "
            "expected an even count (two lines per image: header + POINTS2D)"
        )
    images: list[str] = []
    for i in range(0, len(data_lines), 2):
        header = data_lines[i]
        # data_lines[i + 1] is the POINTS2D row — skipped by pairing.
        parts = header.split()
        if len(parts) < 10:
            raise ValueError(
                f"COLMAP image header at line {i} has {len(parts)} tokens; expected "
                "at least 10 (IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME)"
            )
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


def _select_model(
    models: tuple[SparseModelEntry, ...],
    total_input_images: int,
) -> tuple[int, str]:
    """Re-derive (selected_model_index, selection_rule) from the models tuple.

    Selection: most registered images → tie-break by point3d count →
    tie-break by lowest model index.  Extracted so the validator can re-derive
    the selection without re-reading disk.
    """
    if len(models) == 1:
        return models[0].model_index, "single_model"
    ranked = sorted(
        models,
        key=lambda e: (-e.image_count, -e.point3d_count, e.model_index),
    )
    selected = ranked[0]
    if selected.point3d_count > 0 and any(
        e.image_count == selected.image_count
        and e.point3d_count != selected.point3d_count
        for e in models
        if e.model_index != selected.model_index
    ):
        rule = "largest_point3d_count"
    else:
        rule = "largest_image_count"
    return selected.model_index, rule


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

    selected_index, rule = _select_model(tuple(entries), total_input_images)
    return SparseModelEnumeration(
        models=tuple(entries),
        selected_model_index=selected_index,
        selection_rule=rule,  # type: ignore[arg-type]
        total_input_images=total_input_images,
    )


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
    """Final auditable artefact binding registration, capture, policy, and outcome.

    All measured fields (registered_count, total_input_images, registered_ratio,
    session_outcomes, model_enumeration) are **derived** from authoritative
    artifacts by the builder and re-derived by the validator.  Self-reported
    values are never trusted.
    """

    # Content-addressed bindings (all 64-hex SHA-256)
    registration_json_sha256: str = Field(pattern=_SHA256_PATTERN)
    capture_manifest_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    policy_canonical_sha256: str = Field(pattern=_SHA256_PATTERN)

    # Engine identity
    engine: Literal["colmap", "mock", "external"]
    engine_version: str | None = None

    # Measured outcome (derived, not self-reported)
    registered_count: int = Field(ge=0)
    total_input_images: int = Field(ge=0)
    registered_ratio: float = Field(ge=0.0, le=1.0)
    session_outcomes: tuple[SessionQualityOutcome, ...] = Field(default=())
    model_enumeration: SparseModelEnumeration | None = None

    # Three-state decision (the core contract)
    invocation_succeeded: bool
    quality_accepted: bool
    training_allowed: bool

    # Rejection reasons (empty = accepted; non-empty = why rejected).
    # Must exactly equal the derived reasons — not just non-empty.
    rejection_reasons: tuple[str, ...] = Field(default=())


# ============================================================
# Derivation helpers (used by both builder and validator)
# ============================================================

def _derive_registered_names(registration: RegistrationResult) -> tuple[str, ...]:
    """Unique image names from ``registration.poses``, in first-appearance order.

    Raises on duplicate pose identities — a registration result must not
    register the same image twice.
    """
    seen: dict[str, None] = {}
    for pose in registration.poses:
        if pose.image in seen:
            raise ValueError(
                f"duplicate registered image in RegistrationResult.poses: {pose.image}"
            )
        seen[pose.image] = None
    return tuple(seen)


def _longest_consecutive_unregistered_run(
    images: list[str],
    registered: frozenset[str],
) -> int:
    """Longest run of consecutive images not in the registered set."""
    longest = 0
    current = 0
    for img in images:
        if img not in registered:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def _derive_session_outcomes(
    registration: RegistrationResult,
    registered_names: frozenset[str],
) -> tuple[SessionQualityOutcome, ...]:
    """Derive per-session outcomes from registration.sessions + registered names."""
    outcomes: list[SessionQualityOutcome] = []
    for session in registration.sessions:
        session_registered = {
            pose.image for pose in registration.poses
            if pose.session_id == session.session_id
        }
        unregistered = tuple(
            img for img in session.images if img not in registered_names
        )
        longest_run = _longest_consecutive_unregistered_run(
            session.images, registered_names
        )
        outcomes.append(SessionQualityOutcome(
            session_id=session.session_id,
            registered=len(session_registered),
            total=len(session.images),
            unregistered_images=unregistered,
            longest_unregistered_run=longest_run,
        ))
    return tuple(outcomes)


def _derive_total_input_images(
    registration: RegistrationResult,
    capture_manifest: object | None,
) -> int:
    """Derive total input images from capture manifest (authoritative) or
    registration sessions (fallback when no manifest is bound).
    """
    if capture_manifest is not None:
        output_count = getattr(capture_manifest, "output_count", None)
        if output_count is None:
            raise ValueError("capture_manifest has no output_count attribute")
        return int(output_count)
    return sum(len(s.images) for s in registration.sessions)


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
# Builder (derives all fields from authoritative artifacts)
# ============================================================

def build_registration_quality_report(
    *,
    registration: RegistrationResult,
    registration_json_bytes: bytes,
    capture_manifest: object | None = None,
    capture_manifest_bytes: bytes | None = None,
    policy: RegistrationQualityPolicy,
    sparse_enumeration: SparseModelEnumeration | None = None,
    invocation_succeeded: bool,
    engine_version: str | None = None,
) -> RegistrationQualityReport:
    """Build a report by deriving every measured field from authoritative artifacts.

    The builder never accepts self-reported counts.  All measurements are
    computed from ``registration.poses``, ``registration.sessions``, the
    capture manifest, and the sparse enumeration.
    """
    # 1. Registration bytes must match the passed object.
    reg_sha = hashlib.sha256(registration_json_bytes).hexdigest()
    reparsed_reg = RegistrationResult.model_validate_json(registration_json_bytes)
    if reparsed_reg != registration:
        raise ValueError(
            "registration object does not match registration_json_bytes"
        )

    # 2. Capture manifest bytes must match the passed object.
    if capture_manifest is not None:
        if capture_manifest_bytes is None:
            raise ValueError(
                "capture_manifest provided but capture_manifest_bytes is None"
            )
        manifest_sha = hashlib.sha256(capture_manifest_bytes).hexdigest()
        # Lazy import to avoid circular dependency at module load.
        from pipeline.studio_revisions import CaptureRevisionManifest
        reparsed_manifest = CaptureRevisionManifest.model_validate_json(
            capture_manifest_bytes
        )
        if reparsed_manifest != capture_manifest:
            raise ValueError(
                "capture_manifest object does not match capture_manifest_bytes"
            )
    else:
        manifest_sha = None

    # 3. Engine consistency.
    engine = registration.engine
    if engine == "colmap" and sparse_enumeration is None:
        raise ValueError("engine='colmap' requires sparse_enumeration")
    if engine != "colmap" and sparse_enumeration is not None:
        raise ValueError(
            f"sparse_enumeration is not allowed for engine={engine!r}"
        )

    # 4. Derive measured fields.
    registered_names = _derive_registered_names(registration)
    registered_count = len(registered_names)
    total_input_images = _derive_total_input_images(registration, capture_manifest)
    registered_ratio = (
        registered_count / total_input_images if total_input_images > 0 else 0.0
    )
    session_outcomes = _derive_session_outcomes(
        registration, frozenset(registered_names)
    )

    # 5. Consistency: session totals must sum to the global total.
    session_total_sum = sum(s.total for s in session_outcomes)
    if session_total_sum != total_input_images:
        raise ValueError(
            f"session totals sum ({session_total_sum}) != total_input_images "
            f"({total_input_images}); capture manifest and registration sessions "
            "are inconsistent"
        )

    # 6. Sparse enumeration consistency.
    if sparse_enumeration is not None:
        if sparse_enumeration.total_input_images != total_input_images:
            raise ValueError(
                f"sparse_enumeration.total_input_images "
                f"({sparse_enumeration.total_input_images}) != "
                f"total_input_images ({total_input_images})"
            )
        # Re-derive selection to ensure internal consistency.
        re_selected, re_rule = _select_model(
            sparse_enumeration.models, sparse_enumeration.total_input_images
        )
        if re_selected != sparse_enumeration.selected_model_index:
            raise ValueError(
                f"sparse_enumeration.selected_model_index "
                f"({sparse_enumeration.selected_model_index}) != "
                f"re-derived ({re_selected})"
            )
        if re_rule != sparse_enumeration.selection_rule:
            raise ValueError(
                f"sparse_enumeration.selection_rule "
                f"({sparse_enumeration.selection_rule}) != "
                f"re-derived ({re_rule})"
            )
        # image_count must equal len(images) for each entry.
        for entry in sparse_enumeration.models:
            if entry.image_count != len(entry.images):
                raise ValueError(
                    f"sparse model {entry.model_index}: image_count "
                    f"({entry.image_count}) != len(images) ({len(entry.images)})"
                )

    # 7. Build the report with placeholder booleans, then derive.
    policy_sha = policy_canonical_sha256(policy)
    report = RegistrationQualityReport(
        registration_json_sha256=reg_sha,
        capture_manifest_sha256=manifest_sha,
        policy_canonical_sha256=policy_sha,
        engine=engine,
        engine_version=engine_version,
        registered_count=registered_count,
        total_input_images=total_input_images,
        registered_ratio=registered_ratio,
        session_outcomes=session_outcomes,
        model_enumeration=sparse_enumeration,
        invocation_succeeded=invocation_succeeded,
        quality_accepted=False,  # placeholder, derived below
        training_allowed=False,
        rejection_reasons=(),
    )

    quality_accepted, reasons = derive_quality_accepted(report, policy)
    training_allowed = derive_training_allowed(report, policy)

    return report.model_copy(update={
        "quality_accepted": quality_accepted,
        "training_allowed": training_allowed,
        "rejection_reasons": tuple(reasons),
    })


# ============================================================
# Validation (re-derive everything from authoritative artifacts)
# ============================================================

def validate_registration_quality(
    report: RegistrationQualityReport,
    policy: RegistrationQualityPolicy,
    registration_json_bytes: bytes,
    *,
    capture_manifest_bytes: bytes | None = None,
    sparse_enumeration: SparseModelEnumeration | None = None,
) -> None:
    """Validate by re-deriving every measured field from authoritative artifacts.

    The validator never trusts self-reported counts.  It re-parses
    ``RegistrationResult`` from bytes, re-derives registered_count / ratio /
    session_outcomes / model_enumeration, and requires every stored boolean /
    reason / count to equal the derived value.

    Raises ValueError on any mismatch.
    """
    # 1. Policy SHA.
    expected_policy_sha = policy_canonical_sha256(policy)
    if report.policy_canonical_sha256 != expected_policy_sha:
        raise ValueError(
            f"policy_canonical_sha256 mismatch: report claims "
            f"{report.policy_canonical_sha256} but policy computes {expected_policy_sha}"
        )

    # 2. Registration JSON SHA.
    expected_reg_sha = hashlib.sha256(registration_json_bytes).hexdigest()
    if report.registration_json_sha256 != expected_reg_sha:
        raise ValueError(
            f"registration_json_sha256 mismatch: report claims "
            f"{report.registration_json_sha256} but bytes compute {expected_reg_sha}"
        )

    # 3. Parse RegistrationResult from bytes (fail-closed on unparseable).
    try:
        registration = RegistrationResult.model_validate_json(registration_json_bytes)
    except Exception as exc:
        raise ValueError(
            f"registration_json_bytes do not parse as RegistrationResult: {exc}"
        ) from exc

    # 4. Engine must match registration.
    if report.engine != registration.engine:
        raise ValueError(
            f"engine mismatch: report claims {report.engine!r} but "
            f"registration.engine is {registration.engine!r}"
        )

    # 5. Capture manifest: if SHA is bound, bytes must be supplied and match.
    if report.capture_manifest_sha256 is not None:
        if capture_manifest_bytes is None:
            raise ValueError(
                "report binds capture_manifest_sha256 but no capture_manifest_bytes "
                "were provided to the validator — cannot verify"
            )
        expected_manifest_sha = hashlib.sha256(capture_manifest_bytes).hexdigest()
        if report.capture_manifest_sha256 != expected_manifest_sha:
            raise ValueError(
                f"capture_manifest_sha256 mismatch: report claims "
                f"{report.capture_manifest_sha256} but bytes compute "
                f"{expected_manifest_sha}"
            )
        try:
            from pipeline.studio_revisions import CaptureRevisionManifest
            capture_manifest = CaptureRevisionManifest.model_validate_json(
                capture_manifest_bytes
            )
        except Exception as exc:
            raise ValueError(
                f"capture_manifest_bytes do not parse as CaptureRevisionManifest: {exc}"
            ) from exc
    else:
        capture_manifest = None

    # 6. Sparse enumeration: required for colmap, forbidden otherwise.
    if report.engine == "colmap":
        if sparse_enumeration is None:
            raise ValueError(
                "engine='colmap' requires sparse_enumeration argument to validator"
            )
        if report.model_enumeration is None:
            raise ValueError(
                "engine='colmap' report must have model_enumeration field"
            )
        if report.model_enumeration != sparse_enumeration:
            raise ValueError(
                "report.model_enumeration does not match authoritative "
                "sparse_enumeration argument"
            )
        # Re-derive selection from models tuple.
        re_selected, re_rule = _select_model(
            sparse_enumeration.models, sparse_enumeration.total_input_images
        )
        if sparse_enumeration.selected_model_index != re_selected:
            raise ValueError(
                f"sparse_enumeration.selected_model_index "
                f"({sparse_enumeration.selected_model_index}) != "
                f"re-derived ({re_selected})"
            )
        if sparse_enumeration.selection_rule != re_rule:
            raise ValueError(
                f"sparse_enumeration.selection_rule "
                f"({sparse_enumeration.selection_rule!r}) != "
                f"re-derived ({re_rule!r})"
            )
        for entry in sparse_enumeration.models:
            if entry.image_count != len(entry.images):
                raise ValueError(
                    f"sparse model {entry.model_index}: image_count "
                    f"({entry.image_count}) != len(images) ({len(entry.images)})"
                )
    else:
        if report.model_enumeration is not None:
            raise ValueError(
                f"engine={report.engine!r} report must not have model_enumeration"
            )
        if sparse_enumeration is not None:
            raise ValueError(
                f"sparse_enumeration argument not allowed for engine={report.engine!r}"
            )

    # 7. Re-derive all measured fields from authoritative artifacts.
    registered_names = _derive_registered_names(registration)
    registered_count = len(registered_names)
    total_input_images = _derive_total_input_images(registration, capture_manifest)
    registered_ratio = (
        registered_count / total_input_images if total_input_images > 0 else 0.0
    )
    session_outcomes = _derive_session_outcomes(
        registration, frozenset(registered_names)
    )

    # 8. Require exact equality with stored values.
    if report.registered_count != registered_count:
        raise ValueError(
            f"registered_count mismatch: report claims {report.registered_count} "
            f"but derivation from RegistrationResult.poses gives {registered_count}"
        )
    if report.total_input_images != total_input_images:
        raise ValueError(
            f"total_input_images mismatch: report claims {report.total_input_images} "
            f"but derivation gives {total_input_images}"
        )
    if abs(report.registered_ratio - registered_ratio) > 1e-9:
        raise ValueError(
            f"registered_ratio mismatch: report claims {report.registered_ratio} "
            f"but derivation gives {registered_ratio}"
        )
    if report.session_outcomes != session_outcomes:
        raise ValueError(
            "session_outcomes mismatch: report does not match derivation from "
            "RegistrationResult.sessions + poses"
        )

    # 9. Re-derive booleans and require equality.
    derived_accepted, reasons = derive_quality_accepted(report, policy)
    if report.quality_accepted != derived_accepted:
        raise ValueError(
            f"quality_accepted mismatch: report claims {report.quality_accepted} "
            f"but derivation gives {derived_accepted} (reasons: {reasons})"
        )
    derived_training = derive_training_allowed(report, policy)
    if report.training_allowed != derived_training:
        raise ValueError(
            f"training_allowed mismatch: report claims {report.training_allowed} "
            f"but derivation gives {derived_training}"
        )

    # 10. Rejection reasons must exactly equal derived reasons (not just non-empty).
    derived_reasons_tuple = tuple(reasons)
    if report.rejection_reasons != derived_reasons_tuple:
        raise ValueError(
            f"rejection_reasons mismatch: report claims {report.rejection_reasons} "
            f"but derivation gives {derived_reasons_tuple}"
        )
