"""Derive redacted Production release evidence from private acceptance."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from pipeline.production_release_contract import (
    PRIVATE_EVIDENCE_OMITTED,
    PRODUCTION_GATE_IDS,
    validate_public_evidence,
)
from pipeline.production_training_closure import (
    ProductionTrainingClosureError,
    load_production_training_closure_bytes,
)
from pipeline.real_dataset import (
    DatasetEvidenceError,
    canonical_model_bytes,
    load_capture_rights_receipt,
)
from pipeline.real_scene_acceptance import (
    AcceptanceDecision,
    HumanReviewPolicy,
    HumanVisualReview,
    RealSceneAcceptance,
    RealSceneAcceptanceError,
    canonical_human_review_bytes,
    canonical_human_review_policy_bytes,
    canonical_real_scene_acceptance_bytes,
    validate_human_visual_review,
    validate_real_scene_acceptance,
)
from pipeline.real_scene_import import (
    RealSceneImportError,
    RealSceneImportReceipt,
    validate_real_scene_import_receipt,
)
from pipeline.release_archive import (
    ReleaseArchiveError,
    canonical_json_bytes,
    safe_posix_member_path,
    stable_regular_file_digest,
)
from pipeline.viewer_acceptance import (
    ViewerAcceptanceError,
    ViewerPerformancePolicy,
    ViewerPerformanceReportV2,
    canonical_viewer_performance_policy_bytes,
    derive_viewer_decision,
    load_viewer_performance_report_bytes,
)

_MAXIMUM_PUBLIC_SOURCE_BYTES = 4 * 1024 * 1024 * 1024


class ProductionReleaseBuilderError(ValueError):
    """Raised when private acceptance cannot produce a safe public projection."""


@dataclass(frozen=True)
class SourcePayload:
    source_path: Path
    destination_path: str
    role: str
    byte_length: int
    sha256: str


@dataclass(frozen=True)
class ProductionReleaseContext:
    acceptance_root: Path
    report_sha256: str
    decision: AcceptanceDecision
    import_root: Path
    import_receipt: RealSceneImportReceipt
    public_evidence: dict[str, object]
    public_files: tuple[SourcePayload, ...]
    redacted_human_review: dict[str, object]


@dataclass(frozen=True)
class _ObservedFile:
    path: Path
    byte_length: int
    sha256: str


def _safe_regular_payload(
    path: Path,
    *,
    maximum_bytes: int = _MAXIMUM_PUBLIC_SOURCE_BYTES,
) -> tuple[bytes, _ObservedFile]:
    source = Path(path)
    try:
        before = source.lstat()
    except OSError as exc:
        raise ProductionReleaseBuilderError(
            f"release evidence is unavailable: {source}"
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ProductionReleaseBuilderError(
            f"release evidence must be a regular non-link file: {source}"
        )
    if before.st_size < 0 or before.st_size > maximum_bytes:
        raise ProductionReleaseBuilderError(
            f"release evidence length is invalid: {source}"
        )
    try:
        payload = source.read_bytes()
        after = source.lstat()
    except OSError as exc:
        raise ProductionReleaseBuilderError(
            f"release evidence cannot be read: {source}"
        ) from exc
    def signature(value) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
        )
    if (
        signature(before) != signature(after)
        or len(payload) != before.st_size
    ):
        raise ProductionReleaseBuilderError(
            f"release evidence changed during read: {source}"
        )
    return payload, _ObservedFile(
        path=source,
        byte_length=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _reference_payload(
    root: Path,
    reference,
    *,
    observations: list[_ObservedFile],
) -> bytes:
    relative = safe_posix_member_path(reference.path)
    path = root.joinpath(*relative.parts)
    payload, observed = _safe_regular_payload(
        path,
        maximum_bytes=reference.byte_length,
    )
    if (
        observed.byte_length != reference.byte_length
        or observed.sha256 != reference.sha256
    ):
        raise ProductionReleaseBuilderError(
            f"release evidence changed or SHA disagrees: {reference.path}"
        )
    observations.append(observed)
    return payload


def _validate_decision(
    decision: AcceptanceDecision,
    *,
    report_sha256: str,
) -> None:
    if decision.source_role != "production-acceptance":
        raise ProductionReleaseBuilderError(
            "Production release decision source role is not production-acceptance"
        )
    if decision.report_sha256 != report_sha256:
        raise ProductionReleaseBuilderError(
            "Production acceptance report SHA disagrees with decision"
        )
    if (
        decision.technical_accepted is not True
        or decision.canary_accepted is not False
        or decision.production_release_allowed is not True
        or decision.failed_gates
        or decision.reasons
    ):
        raise ProductionReleaseBuilderError(
            "Production release decision is not fully release-accepted"
        )
    if tuple(gate.gate for gate in decision.gates) != PRODUCTION_GATE_IDS or any(
        gate.state != "accepted" or gate.reasons
        for gate in decision.gates
    ):
        raise ProductionReleaseBuilderError(
            "Production acceptance gates are incomplete, unordered, or rejected"
        )


def _load_acceptance_report(
    report_path: Path,
    *,
    observations: list[_ObservedFile],
) -> tuple[RealSceneAcceptance, bytes, str]:
    payload, observed = _safe_regular_payload(
        report_path,
        maximum_bytes=4 * 1024 * 1024,
    )
    observations.append(observed)
    try:
        report = RealSceneAcceptance.model_validate_json(payload)
    except ValidationError as exc:
        raise ProductionReleaseBuilderError(
            "Production acceptance report is invalid"
        ) from exc
    if payload != canonical_real_scene_acceptance_bytes(report):
        raise ProductionReleaseBuilderError(
            "Production acceptance report is noncanonical"
        )
    return report, payload, observed.sha256


def _load_import_receipt(
    report: RealSceneAcceptance,
    root: Path,
    *,
    observations: list[_ObservedFile],
) -> tuple[RealSceneImportReceipt, Path]:
    import_relative = safe_posix_member_path(report.import_root.path)
    import_root = root.joinpath(*import_relative.parts)
    if import_root.is_symlink() or not import_root.is_dir():
        raise ProductionReleaseBuilderError(
            "Production import root is unavailable or unsafe"
        )
    receipt_path = root.joinpath(
        *safe_posix_member_path(report.import_receipt.path).parts
    )
    try:
        validated = validate_real_scene_import_receipt(
            receipt_path,
            import_root,
        )
    except (RealSceneImportError, OSError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            f"Production import receipt cannot be validated: {exc}"
        ) from exc
    payload = _reference_payload(
        root,
        report.import_receipt,
        observations=observations,
    )
    try:
        reopened = RealSceneImportReceipt.model_validate_json(payload)
    except ValidationError as exc:
        raise ProductionReleaseBuilderError(
            "Production import receipt is invalid"
        ) from exc
    if payload != canonical_model_bytes(reopened) or reopened != validated:
        raise ProductionReleaseBuilderError(
            "Production import receipt differs after validation"
        )
    if reopened.source_role != "production-acceptance":
        raise ProductionReleaseBuilderError(
            "Production import source role disagrees"
        )
    if reopened.training_quality_role != "production":
        raise ProductionReleaseBuilderError(
            "Production import quality role disagrees"
        )
    if reopened.target_units != "meters":
        raise ProductionReleaseBuilderError(
            "Production import units are not meters"
        )
    if reopened.geometry_usability != "metric-aligned":
        raise ProductionReleaseBuilderError(
            "Production import geometry is not metric-aligned"
        )
    if (
        reopened.alignment_rms_m is None
        or reopened.alignment_rms_m > 0.25
    ):
        raise ProductionReleaseBuilderError(
            "Production import alignment evidence is not accepted"
        )
    if reopened.gaussian_count < 100_000:
        raise ProductionReleaseBuilderError(
            "Production import Gaussian count is below minimum"
        )
    return reopened, import_root


def _load_closure(
    receipt: RealSceneImportReceipt,
    import_root: Path,
    *,
    observations: list[_ObservedFile],
):
    relative = receipt.production_training_closure_path
    if relative is None:
        raise ProductionReleaseBuilderError(
            "Production training closure binding is missing"
        )
    binding = next(
        (
            artifact
            for artifact in receipt.artifacts
            if artifact.path == relative
        ),
        None,
    )
    if binding is None:
        raise ProductionReleaseBuilderError(
            "Production training closure artifact is unbound"
        )
    payload, observed = _safe_regular_payload(
        import_root.joinpath(*safe_posix_member_path(relative).parts),
        maximum_bytes=binding.byte_length,
    )
    observations.append(observed)
    if (
        observed.byte_length != binding.byte_length
        or observed.sha256 != binding.sha256
    ):
        raise ProductionReleaseBuilderError(
            "Production training closure bytes changed"
        )
    try:
        closure = load_production_training_closure_bytes(payload)
    except ProductionTrainingClosureError as exc:
        raise ProductionReleaseBuilderError(
            "Production training closure is invalid"
        ) from exc
    if (
        closure.content_sha256
        != receipt.production_training_closure_sha256
        or closure.runtime_decision_sha256
        != receipt.production_runtime_decision_sha256
        or closure.gaussian_count != receipt.gaussian_count
        or closure.sh_degree != receipt.sh_degree
    ):
        raise ProductionReleaseBuilderError(
            "Production training closure differs from import receipt"
        )
    return closure


def _source_payload(
    path: Path,
    destination: str,
    role: str,
    *,
    observations: list[_ObservedFile],
    expected_sha256: str | None = None,
    expected_bytes: int | None = None,
) -> tuple[SourcePayload, bytes]:
    safe_posix_member_path(destination)
    payload, observed = _safe_regular_payload(
        path,
        maximum_bytes=(
            expected_bytes
            if expected_bytes is not None
            else _MAXIMUM_PUBLIC_SOURCE_BYTES
        ),
    )
    if (
        expected_sha256 is not None
        and observed.sha256 != expected_sha256
    ) or (
        expected_bytes is not None
        and observed.byte_length != expected_bytes
    ):
        raise ProductionReleaseBuilderError(
            f"public release source changed: {path}"
        )
    observations.append(observed)
    return (
        SourcePayload(
            source_path=path,
            destination_path=destination,
            role=role,
            byte_length=observed.byte_length,
            sha256=observed.sha256,
        ),
        payload,
    )


def _privacy_check(
    payloads: tuple[bytes, ...],
    *,
    private_values: tuple[str, ...],
) -> None:
    dynamic = tuple(
        value.encode("utf-8")
        for value in private_values
        if value
    )
    forbidden = (
        *dynamic,
        b"C:\\",
        b"c:\\\\",
        b"/home/",
        b"ssh",
        b"control-points",
        b'":"/',
    )
    for payload in payloads:
        lowered = payload.lower()
        if any(token.lower() in lowered for token in forbidden):
            raise ProductionReleaseBuilderError(
                "private operator, path, control or host data entered public evidence"
            )


def _second_pass(observations: list[_ObservedFile]) -> None:
    for expected in observations:
        try:
            observed = stable_regular_file_digest(
                expected.path,
                maximum_bytes=expected.byte_length,
            )
        except ReleaseArchiveError as exc:
            raise ProductionReleaseBuilderError(
                f"release evidence changed after validation: {expected.path}"
            ) from exc
        if (
            observed.byte_length != expected.byte_length
            or observed.sha256 != expected.sha256
        ):
            raise ProductionReleaseBuilderError(
                f"release evidence changed after validation: {expected.path}"
            )


def derive_production_release_context(
    acceptance_report_path: Path,
) -> ProductionReleaseContext:
    """Revalidate private acceptance and derive one redacted public context."""

    report_path = Path(acceptance_report_path).expanduser().absolute()
    root = report_path.parent
    if root.is_symlink() or not root.is_dir():
        raise ProductionReleaseBuilderError(
            "Production acceptance root is unavailable or unsafe"
        )
    try:
        decision = validate_real_scene_acceptance(report_path)
    except (RealSceneAcceptanceError, OSError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            f"Production acceptance cannot be revalidated: {exc}"
        ) from exc

    observations: list[_ObservedFile] = []
    report, _report_payload, report_sha256 = _load_acceptance_report(
        report_path,
        observations=observations,
    )
    _validate_decision(decision, report_sha256=report_sha256)
    if report.source_role != "production-acceptance":
        raise ProductionReleaseBuilderError(
            "Production acceptance report source role disagrees"
        )

    if report.rights_receipt is None:
        raise ProductionReleaseBuilderError(
            "Production release rights receipt is missing"
        )
    _reference_payload(
        root,
        report.rights_receipt,
        observations=observations,
    )
    rights_path = root.joinpath(
        *safe_posix_member_path(report.rights_receipt.path).parts
    )
    try:
        rights = load_capture_rights_receipt(rights_path)
    except DatasetEvidenceError as exc:
        raise ProductionReleaseBuilderError(
            "Production release rights are invalid"
        ) from exc
    if (
        rights.redistribution_allowed is not True
        or rights.release_inclusion_allowed is not True
        or "3d-reconstruction" not in rights.processing_purposes
    ):
        raise ProductionReleaseBuilderError(
            "Production release rights do not allow redistribution and inclusion"
        )

    import_receipt, import_root = _load_import_receipt(
        report,
        root,
        observations=observations,
    )
    closure = _load_closure(
        import_receipt,
        import_root,
        observations=observations,
    )

    manifest_binding = next(
        (
            artifact
            for artifact in import_receipt.artifacts
            if artifact.path == import_receipt.manifest_path
        ),
        None,
    )
    if manifest_binding is None:
        raise ProductionReleaseBuilderError(
            "Production scene manifest binding is missing"
        )
    scene_identity = f"scene-{manifest_binding.sha256}"

    render_policy_payload = _reference_payload(
        root,
        report.render_policy,
        observations=observations,
    )
    render_report_payload = _reference_payload(
        root,
        report.render_report,
        observations=observations,
    )
    viewer_policy_payload = _reference_payload(
        root,
        report.viewer_policy,
        observations=observations,
    )
    viewer_report_payload = _reference_payload(
        root,
        report.viewer_report,
        observations=observations,
    )
    try:
        viewer_policy = ViewerPerformancePolicy.model_validate_json(
            viewer_policy_payload
        )
    except ValidationError as exc:
        raise ProductionReleaseBuilderError(
            "Production Viewer policy is invalid"
        ) from exc
    if (
        viewer_policy_payload
        != canonical_viewer_performance_policy_bytes(viewer_policy)
    ):
        raise ProductionReleaseBuilderError(
            "Production Viewer policy is noncanonical"
        )
    try:
        viewer_report = load_viewer_performance_report_bytes(
            viewer_report_payload
        )
    except ViewerAcceptanceError as exc:
        raise ProductionReleaseBuilderError(
            "Production Viewer report is invalid"
        ) from exc
    if not isinstance(viewer_report, ViewerPerformanceReportV2):
        raise ProductionReleaseBuilderError(
            "Production release requires a Viewer v2 report"
        )
    viewer_decision = derive_viewer_decision(
        viewer_policy,
        viewer_report,
    )
    if not viewer_decision.accepted:
        raise ProductionReleaseBuilderError(
            "Production Viewer evidence is not accepted"
        )

    human_policy_payload = _reference_payload(
        root,
        report.human_review_policy,
        observations=observations,
    )
    human_review_payload = _reference_payload(
        root,
        report.human_visual_review,
        observations=observations,
    )
    try:
        human_policy = HumanReviewPolicy.model_validate_json(
            human_policy_payload
        )
        human_review = HumanVisualReview.model_validate_json(
            human_review_payload
        )
    except ValidationError as exc:
        raise ProductionReleaseBuilderError(
            "Production human-review evidence is invalid"
        ) from exc
    if (
        human_policy_payload
        != canonical_human_review_policy_bytes(human_policy)
        or human_review_payload
        != canonical_human_review_bytes(human_review)
    ):
        raise ProductionReleaseBuilderError(
            "Production human-review evidence is noncanonical"
        )
    try:
        human_decision = validate_human_visual_review(
            human_policy,
            human_review,
            root,
        )
    except RealSceneAcceptanceError as exc:
        raise ProductionReleaseBuilderError(
            "Production human-review evidence cannot be verified"
        ) from exc
    if not human_decision.accepted:
        raise ProductionReleaseBuilderError(
            "Production human review is not accepted"
        )

    public_files: list[SourcePayload] = []
    public_json_payloads: list[bytes] = []
    for source, destination, role, sha, length in (
        (
            root.joinpath(*safe_posix_member_path(report.viewer_policy.path).parts),
            "evidence/viewer/policy.json",
            "viewer-policy",
            report.viewer_policy.sha256,
            report.viewer_policy.byte_length,
        ),
        (
            root.joinpath(*safe_posix_member_path(report.viewer_report.path).parts),
            "evidence/viewer/report.json",
            "viewer-report",
            report.viewer_report.sha256,
            report.viewer_report.byte_length,
        ),
        (
            root.joinpath(
                *safe_posix_member_path(report.human_review_policy.path).parts
            ),
            "evidence/human-review/policy.json",
            "human-review-policy",
            report.human_review_policy.sha256,
            report.human_review_policy.byte_length,
        ),
    ):
        source_payload, payload = _source_payload(
            source,
            destination,
            role,
            observations=observations,
            expected_sha256=sha,
            expected_bytes=length,
        )
        public_files.append(source_payload)
        public_json_payloads.append(payload)

    screenshots = tuple(viewer_report.screenshots[:3])
    human_screenshots = {
        screenshot.pose_id: screenshot
        for screenshot in human_review.screenshots
    }
    if len(screenshots) != 3 or any(
        screenshot.pose_id not in human_screenshots
        for screenshot in screenshots
    ):
        raise ProductionReleaseBuilderError(
            "Production release requires three reviewed Viewer screenshots"
        )
    public_screenshot_rows: list[dict[str, object]] = []
    for index, screenshot in enumerate(screenshots, start=1):
        reviewed = human_screenshots[screenshot.pose_id]
        if (
            reviewed.path != screenshot.path
            or reviewed.sha256 != screenshot.sha256
            or reviewed.byte_count != screenshot.byte_length
        ):
            raise ProductionReleaseBuilderError(
                "Production Viewer and human screenshot bindings disagree"
            )
        destination = (
            f"evidence/viewer/screenshots/{index:02d}-"
            f"{screenshot.pose_id}.png"
        )
        source_payload, _payload = _source_payload(
            root.joinpath(
                *safe_posix_member_path(screenshot.path).parts
            ),
            destination,
            "viewer-screenshot",
            observations=observations,
            expected_sha256=screenshot.sha256,
            expected_bytes=screenshot.byte_length,
        )
        public_files.append(source_payload)
        public_screenshot_rows.append(
            {
                "pose_id": screenshot.pose_id,
                "path": destination,
                "sha256": screenshot.sha256,
                "bytes": screenshot.byte_length,
                "width": reviewed.width,
                "height": reviewed.height,
            }
        )

    decision_payload = canonical_model_bytes(decision)
    redacted_human_review = {
        "schema": "nantai.public-human-review.v1",
        "review_id": human_review.review_id,
        "review_sha256": report.human_visual_review.sha256,
        "reviewer_identity_sha256": hashlib.sha256(
            human_review.reviewer.encode("utf-8")
        ).hexdigest(),
        "reviewed_at": human_review.reviewed_at.isoformat().replace(
            "+00:00",
            "Z",
        ),
        "policy_sha256": report.human_review_policy.sha256,
        "accepted": True,
        "dispositions": [
            {
                "category": row.category,
                "disposition": row.disposition,
            }
            for row in human_review.dispositions
        ],
        "screenshots": public_screenshot_rows,
    }
    redacted_payload = canonical_json_bytes(redacted_human_review)

    public_evidence = validate_public_evidence(
        {
            "schema": "nantai.production-public-evidence.v1",
            "fixture_kind": None,
            "acceptance": {
                "report_sha256": report_sha256,
                "decision_sha256": hashlib.sha256(
                    decision_payload
                ).hexdigest(),
                "source_role": "production-acceptance",
                "production_release_allowed": True,
                "gates": [
                    {"id": gate.gate, "state": gate.state}
                    for gate in decision.gates
                ],
            },
            "source": {
                "dataset_id_sha256": hashlib.sha256(
                    rights.dataset_id.encode("utf-8")
                ).hexdigest(),
                "capture_manifest_sha256": (
                    report.capture_manifest.sha256
                ),
                "rights": {
                    "redistribution_allowed": True,
                    "release_inclusion_allowed": True,
                    "processing_purposes": sorted(
                        rights.processing_purposes
                    ),
                },
            },
            "scene": {
                "scene_identity": scene_identity,
                "import_receipt_sha256": (
                    report.import_receipt.sha256
                ),
                "manifest_sha256": manifest_binding.sha256,
                "quality_role": "production",
                "geometry_usability": "metric-aligned",
                "units": "meters",
                "alignment_rms_m": import_receipt.alignment_rms_m,
                "gaussian_count": import_receipt.gaussian_count,
            },
            "training": {
                "closure_sha256": closure.content_sha256,
                "runtime_decision_sha256": (
                    closure.runtime_decision_sha256
                ),
                "container_identity_sha256": hashlib.sha256(
                    closure.container_identity.encode("ascii")
                ).hexdigest(),
            },
            "render": {
                "policy_sha256": hashlib.sha256(
                    render_policy_payload
                ).hexdigest(),
                "report_sha256": hashlib.sha256(
                    render_report_payload
                ).hexdigest(),
                "accepted": True,
            },
            "viewer": {
                "schema": "nantai.viewer-performance-report.v2",
                "policy_sha256": report.viewer_policy.sha256,
                "report_sha256": report.viewer_report.sha256,
                "accepted": True,
                "screenshot_count": len(screenshots),
            },
            "human_review": {
                "policy_sha256": report.human_review_policy.sha256,
                "review_sha256": report.human_visual_review.sha256,
                "accepted": True,
                "categories": [
                    row.category
                    for row in human_review.dispositions
                ],
            },
            "private_evidence_omitted": list(
                PRIVATE_EVIDENCE_OMITTED
            ),
        }
    )
    public_evidence_payload = canonical_json_bytes(public_evidence)
    _privacy_check(
        (
            public_evidence_payload,
            decision_payload,
            redacted_payload,
            *public_json_payloads,
        ),
        private_values=(
            rights.operator,
            rights.capture_scope,
            human_review.reviewer,
        ),
    )
    _second_pass(observations)
    return ProductionReleaseContext(
        acceptance_root=root,
        report_sha256=report_sha256,
        decision=decision,
        import_root=import_root,
        import_receipt=import_receipt,
        public_evidence=public_evidence,
        public_files=tuple(
            sorted(
                public_files,
                key=lambda row: row.destination_path,
            )
        ),
        redacted_human_review=redacted_human_review,
    )
