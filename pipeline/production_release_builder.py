"""Derive redacted Production release evidence from private acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from pydantic import ValidationError

from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRIVATE_EVIDENCE_OMITTED,
    PRODUCTION_ENTRYPOINTS,
    PRODUCTION_GATE_IDS,
    PRODUCTION_RELEASE_NAME,
    build_production_receipt,
    validate_public_evidence,
)
from pipeline.production_release_fs import (
    BoundDirectory,
    BoundFile,
    ProductionReleaseFSError,
    ProductionReleaseMutationError,
    open_bound_directory,
    require_linux_mutation_support,
)
from pipeline.production_release_verifier import (
    verify_production_release_archive_stream,
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
    load_latest_real_scene_acceptance,
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
    deterministic_zip_info,
    portable_path_identity,
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

    def __init__(
        self,
        message: str,
        *,
        published: tuple[str, ...] = (),
        retained: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.published = published
        self.retained = retained


def _git_provenance_command(arguments: Iterable[str]) -> list[str]:
    return ["git", "--no-replace-objects", *arguments]


def _git_provenance_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def _require_no_linklike_ancestors(path: Path, *, label: str) -> None:
    source = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(source.anchor), source)
    except (OSError, ValueError) as exc:
        raise ProductionReleaseBuilderError(f"{label} is unsafe") from exc
    if redirected is not None:
        raise ProductionReleaseBuilderError(f"{label} is unsafe")


def _preflight_real_tree(root: Path, *, label: str) -> None:
    _require_no_linklike_ancestors(root, label=label)
    try:
        for current, directories, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in (*tuple(directories), *tuple(names)):
                candidate = current_path / name
                observed = candidate.lstat()
                if _is_linklike(candidate, observed=observed):
                    raise ProductionReleaseBuilderError(f"{label} is unsafe")
    except ProductionReleaseBuilderError:
        raise
    except OSError as exc:
        raise ProductionReleaseBuilderError(f"{label} is unsafe") from exc


@dataclass(frozen=True)
class ProductionReleaseSourceIdentity:
    source_commit: str
    tracked_files: tuple[str, ...]


def _git_source_output(
    repo_root: Path,
    arguments: list[str],
    operation: str,
) -> bytes:
    command = _git_provenance_command(arguments)
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=False,
            check=False,
            env=_git_provenance_environment(),
        )
    except OSError as exc:
        raise ProductionReleaseBuilderError(
            f"Git source identity {operation} failed"
        ) from exc
    if completed.returncode != 0:
        cause = subprocess.CalledProcessError(
            completed.returncode,
            command,
        )
        raise ProductionReleaseBuilderError(
            f"Git source identity {operation} failed"
        ) from cause
    if not isinstance(completed.stdout, bytes):
        cause = TypeError("Git source output is not bytes")
        raise ProductionReleaseBuilderError(
            f"Git source identity {operation} output is not bytes"
        ) from cause
    return completed.stdout


def resolve_production_release_source_identity(
    repo_root: str | Path,
) -> ProductionReleaseSourceIdentity:
    root = Path(repo_root).expanduser().absolute()
    _require_no_linklike_ancestors(root, label="Git source root")
    try:
        source_commit = _git_source_output(
            root,
            ["rev-parse", "--verify", "HEAD"],
            "rev-parse",
        ).decode("ascii").strip()
    except UnicodeError as exc:
        raise ProductionReleaseBuilderError(
            "Git source identity rev-parse output cannot be decoded"
        ) from exc
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ProductionReleaseBuilderError(
            "Git source identity rev-parse output is not canonical"
        )
    try:
        tracked_output = _git_source_output(
            root,
            ["ls-files", "-z", "--"],
            "ls-files",
        ).decode("utf-8", "surrogateescape")
    except UnicodeError as exc:
        raise ProductionReleaseBuilderError(
            "Git source identity ls-files output cannot be decoded"
        ) from exc
    tracked_files = tuple(
        sorted(relative for relative in tracked_output.split("\0") if relative)
    )
    if not tracked_files:
        raise ProductionReleaseBuilderError(
            "Git source identity ls-files output is empty"
        )
    replacement_refs = _git_source_output(
        root,
        ["for-each-ref", "--format=%(refname)", "refs/replace"],
        "replacement refs",
    )
    if replacement_refs:
        raise ProductionReleaseBuilderError(
            "Git replacement refs are not allowed for release provenance"
        )
    return ProductionReleaseSourceIdentity(
        source_commit=source_commit,
        tracked_files=tracked_files,
    )


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
class ProductionReleaseBuild:
    archive_path: Path
    archive_sha256: str
    package_content_id: str
    artifact_count: int
    total_bytes: int
    scene_identity: str
    acceptance_report_sha256: str
    retained_private_paths: tuple[Path, ...] = ()


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
    _require_no_linklike_ancestors(source, label="release evidence path")
    try:
        before = source.lstat()
    except OSError as exc:
        raise ProductionReleaseBuilderError(
            f"release evidence is unavailable: {source}"
        ) from exc
    if _is_linklike(source, observed=before) or not stat.S_ISREG(
        before.st_mode
    ):
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
    try:
        redirected = first_linklike_path(root, path)
    except (OSError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            f"release evidence path is unsafe: {reference.path}"
        ) from exc
    if redirected is not None:
        raise ProductionReleaseBuilderError(
            f"release evidence path is unsafe: {reference.path}"
        )
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
    try:
        import_stat = import_root.lstat()
        redirected = first_linklike_path(root, import_root)
    except (OSError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            "Production import root is unavailable or unsafe"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(import_root, observed=import_stat)
        or not stat.S_ISDIR(import_stat.st_mode)
    ):
        raise ProductionReleaseBuilderError(
            "Production import root is unavailable or unsafe"
        )
    _preflight_real_tree(import_root, label="Production import root tree")
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
        _require_no_linklike_ancestors(
            expected.path,
            label="release evidence path",
        )
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
    try:
        root_stat = root.lstat()
        redirected = first_linklike_path(Path(root.anchor), report_path)
    except (OSError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            "Production acceptance root is unavailable or unsafe"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(root, observed=root_stat)
        or not stat.S_ISDIR(root_stat.st_mode)
    ):
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


def _strict_json(payload: bytes, *, label: str) -> object:
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ProductionReleaseBuilderError(
                    f"{label} contains duplicate JSON keys"
                )
            value[key] = item
        return value

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")
            ),
        )
    except ProductionReleaseBuilderError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            f"{label} is invalid JSON"
        ) from exc


def _record_scene_reference(
    references: dict[str, tuple[int, str]],
    *,
    base: str,
    relative: object,
    byte_length: object,
    sha256: object,
) -> None:
    if (
        not isinstance(relative, str)
        or isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or byte_length < 0
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ProductionReleaseBuilderError(
            "scene manifest artifact reference is malformed"
        )
    try:
        safe_relative = safe_posix_member_path(relative).as_posix()
        source = safe_posix_member_path(
            f"{base}/{safe_relative}"
        ).as_posix()
    except ReleaseArchiveError as exc:
        raise ProductionReleaseBuilderError(
            "scene manifest artifact path is unsafe"
        ) from exc
    identity = (byte_length, sha256)
    previous = references.get(source)
    if previous is not None and previous != identity:
        raise ProductionReleaseBuilderError(
            f"duplicate scene destination has conflicting identity: {source}"
        )
    references[source] = identity


def _manifest_references(
    value: object,
    references: dict[str, tuple[int, str]],
) -> None:
    if isinstance(value, dict):
        for path_key in ("path", "manifest"):
            if (
                path_key in value
                and "sha256" in value
                and ("bytes" in value or "size_bytes" in value)
            ):
                _record_scene_reference(
                    references,
                    base="web",
                    relative=value[path_key],
                    byte_length=value.get(
                        "bytes",
                        value.get("size_bytes"),
                    ),
                    sha256=value["sha256"],
                )
        for child in value.values():
            _manifest_references(child, references)
    elif isinstance(value, list):
        for child in value:
            _manifest_references(child, references)


def _chunk_references(
    value: object,
    references: dict[str, tuple[int, str]],
) -> None:
    if isinstance(value, dict):
        if (
            "file" in value
            and "sha256" in value
            and "size_bytes" in value
        ):
            _record_scene_reference(
                references,
                base="web/chunks",
                relative=value["file"],
                byte_length=value["size_bytes"],
                sha256=value["sha256"],
            )
        if (
            "ply_file" in value
            and "sha256" in value
            and "size_bytes" in value
        ):
            _record_scene_reference(
                references,
                base="web/chunks",
                relative=value["ply_file"],
                byte_length=value["size_bytes"],
                sha256=value["sha256"],
            )
        for child in value.values():
            _chunk_references(child, references)
    elif isinstance(value, list):
        for child in value:
            _chunk_references(child, references)


def resolve_runtime_scene_payloads(
    context: ProductionReleaseContext,
) -> tuple[SourcePayload, ...]:
    """Resolve only receipt-bound, manifest-reachable runtime scene bytes."""

    receipt = context.import_receipt
    import_root = context.import_root
    _require_no_linklike_ancestors(
        import_root,
        label="Production import root",
    )
    bindings = {
        artifact.path: artifact
        for artifact in receipt.artifacts
    }
    required = (
        receipt.manifest_path,
        receipt.chunks_manifest_path,
    )
    if any(path not in bindings for path in required):
        raise ProductionReleaseBuilderError(
            "scene manifest or chunks manifest binding is missing"
        )

    references: dict[str, tuple[int, str]] = {}
    manifest_binding = bindings[receipt.manifest_path]
    chunks_binding = bindings[receipt.chunks_manifest_path]
    references[receipt.manifest_path] = (
        manifest_binding.byte_length,
        manifest_binding.sha256,
    )
    references[receipt.chunks_manifest_path] = (
        chunks_binding.byte_length,
        chunks_binding.sha256,
    )
    manifest_payload, _manifest_observed = _safe_regular_payload(
        import_root.joinpath(
            *safe_posix_member_path(receipt.manifest_path).parts
        ),
        maximum_bytes=manifest_binding.byte_length,
    )
    chunks_payload, _chunks_observed = _safe_regular_payload(
        import_root.joinpath(
            *safe_posix_member_path(receipt.chunks_manifest_path).parts
        ),
        maximum_bytes=chunks_binding.byte_length,
    )
    if (
        len(manifest_payload) != manifest_binding.byte_length
        or hashlib.sha256(manifest_payload).hexdigest()
        != manifest_binding.sha256
        or len(chunks_payload) != chunks_binding.byte_length
        or hashlib.sha256(chunks_payload).hexdigest()
        != chunks_binding.sha256
    ):
        raise ProductionReleaseBuilderError(
            "scene manifest bytes drifted from the import receipt"
        )
    _manifest_references(
        _strict_json(manifest_payload, label="scene manifest"),
        references,
    )
    _chunk_references(
        _strict_json(chunks_payload, label="chunks manifest"),
        references,
    )

    payloads: list[SourcePayload] = []
    destinations: set[str] = set()
    folded_destinations: set[str] = set()
    for relative, (expected_bytes, expected_sha) in sorted(
        references.items()
    ):
        binding = bindings.get(relative)
        if (
            binding is None
            or binding.byte_length != expected_bytes
            or binding.sha256 != expected_sha
        ):
            raise ProductionReleaseBuilderError(
                f"scene payload is absent or disagrees with import receipt: {relative}"
            )
        if not relative.startswith("web/"):
            raise ProductionReleaseBuilderError(
                f"scene payload is outside the import web root: {relative}"
            )
        destination = safe_posix_member_path(
            "web/data/recon/" + relative.removeprefix("web/")
        ).as_posix()
        if (
            destination in destinations
            or portable_path_identity(destination) in folded_destinations
        ):
            raise ProductionReleaseBuilderError(
                f"duplicate mapped scene destination: {destination}"
            )
        source = import_root.joinpath(
            *safe_posix_member_path(relative).parts
        )
        _require_no_linklike_ancestors(
            source,
            label="scene payload path",
        )
        digest = stable_regular_file_digest(
            source,
            maximum_bytes=expected_bytes,
        )
        if (
            digest.byte_length != expected_bytes
            or digest.sha256 != expected_sha
        ):
            raise ProductionReleaseBuilderError(
                f"scene payload bytes changed: {relative}"
            )
        destinations.add(destination)
        folded_destinations.add(portable_path_identity(destination))
        payloads.append(
            SourcePayload(
                source_path=source,
                destination_path=destination,
                role=(
                    "scene-manifest"
                    if relative == receipt.manifest_path
                    else "scene-runtime"
                ),
                byte_length=expected_bytes,
                sha256=expected_sha,
            )
        )
    for payload in payloads:
        _require_no_linklike_ancestors(
            payload.source_path,
            label="scene payload path",
        )
        observed = stable_regular_file_digest(
            payload.source_path,
            maximum_bytes=payload.byte_length,
        )
        if (
            observed.byte_length != payload.byte_length
            or observed.sha256 != payload.sha256
        ):
            raise ProductionReleaseBuilderError(
                f"scene payload changed after resolution: {payload.source_path}"
            )
    return tuple(payloads)


def _runtime_destination(relative: str) -> tuple[str, str] | None:
    if relative == "release/production-verify-and-run.md":
        return "VERIFY-AND-RUN.md", "release-guide"
    if relative == "release/production-runtime-runner.py":
        return "make.py", "runtime-runner"
    if relative in {"LICENSE", "pyproject.toml"}:
        return relative, "runtime-root"
    if relative == "scripts/verify_production_release.py":
        return relative, "offline-verifier"
    if relative.startswith("pipeline/") and relative.endswith(".py"):
        return relative, "runtime-code"
    if relative.startswith(("web/studio/", "web/viewer/")):
        return relative, "web-runtime"
    return None


def _git_tree_blob(
    repo_root: Path,
    *,
    source_commit: str,
    relative: str,
) -> str:
    output = _git_source_output(
        repo_root,
        ["ls-tree", "-z", source_commit, "--", relative],
        "ls-tree",
    )
    try:
        records = output.split(b"\0")
        if len(records) != 2 or records[1] or not records[0]:
            raise ValueError("Git tree entry count is invalid")
        metadata, separator, encoded_path = records[0].partition(b"\t")
        if not separator:
            raise ValueError("Git tree entry metadata is invalid")
        fields = metadata.split(b" ")
        if len(fields) != 3:
            raise ValueError("Git tree entry metadata is invalid")
        mode = fields[0].decode("ascii")
        object_type = fields[1].decode("ascii")
        object_id = fields[2].decode("ascii")
        observed_path = encoded_path.decode("utf-8", "surrogateescape")
    except (UnicodeError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            "Git source snapshot ls-tree output cannot be decoded"
        ) from exc
    if observed_path != relative:
        raise ProductionReleaseBuilderError(
            "Git source snapshot tree path is not exact"
        )
    if (
        mode not in {"100644", "100755"}
        or object_type != "blob"
        or re.fullmatch(r"[0-9a-f]{40}", object_id) is None
    ):
        raise ProductionReleaseBuilderError(
            "Git source snapshot entry must be a regular blob mode"
        )
    return object_id


def _runtime_source_payloads(
    repo_root: Path,
    tracked_files: Iterable[str],
) -> tuple[SourcePayload, ...]:
    rows: list[SourcePayload] = []
    destinations: set[str] = set()
    folded: set[str] = set()
    for raw in sorted(set(tracked_files)):
        relative = safe_posix_member_path(raw).as_posix()
        mapped = _runtime_destination(relative)
        if mapped is None:
            continue
        destination, role = mapped
        if (
            destination in destinations
            or portable_path_identity(destination) in folded
        ):
            raise ProductionReleaseBuilderError(
                f"duplicate runtime destination: {destination}"
            )
        source = repo_root.joinpath(
            *safe_posix_member_path(relative).parts
        )
        try:
            digest = stable_regular_file_digest(source)
        except ReleaseArchiveError as exc:
            raise ProductionReleaseBuilderError(
                f"tracked runtime source is unavailable: {relative}"
            ) from exc
        destinations.add(destination)
        folded.add(portable_path_identity(destination))
        rows.append(
            SourcePayload(
                source_path=source,
                destination_path=destination,
                role=role,
                byte_length=digest.byte_length,
                sha256=digest.sha256,
            )
        )
    required = {
        "LICENSE",
        "make.py",
        "pyproject.toml",
        "VERIFY-AND-RUN.md",
        "scripts/verify_production_release.py",
        "pipeline/production_release_fs.py",
    }
    if not required <= {row.destination_path for row in rows}:
        raise ProductionReleaseBuilderError(
            "tracked Production runtime allowlist is incomplete"
        )
    for prefix in ("pipeline/", "web/studio/", "web/viewer/"):
        if not any(row.destination_path.startswith(prefix) for row in rows):
            raise ProductionReleaseBuilderError(
                f"tracked Production runtime is missing {prefix}"
            )
    return tuple(sorted(rows, key=lambda row: row.destination_path))


def _ensure_release_sources_clean(
    repo_root: Path,
    _tracked_files: Iterable[str],
) -> None:
    paths = (
        "LICENSE",
        "pyproject.toml",
        "pipeline",
        "scripts/verify_production_release.py",
        "web/studio",
        "web/viewer",
        "release/production-verify-and-run.md",
        "release/production-runtime-runner.py",
    )
    output = _git_source_output(
        repo_root,
        [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--",
            *paths,
        ],
        "status",
    )
    if output:
        raise ProductionReleaseBuilderError(
            "Production runtime source is dirty"
        )


def _checksum_payload(
    artifacts: list[dict[str, object]],
    receipt_payload: bytes,
) -> bytes:
    rows = [
        f"{artifact['sha256']}  {artifact['path']}\n"
        for artifact in artifacts
    ]
    rows.append(
        f"{hashlib.sha256(receipt_payload).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    return "".join(sorted(rows)).encode("ascii")


def _runtime_rows(
    tracked_files: Iterable[str],
) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    destinations: set[str] = set()
    for raw in sorted(set(tracked_files)):
        relative = safe_posix_member_path(raw).as_posix()
        mapped = _runtime_destination(relative)
        if mapped is None:
            continue
        destination, role = mapped
        if destination in destinations:
            raise ProductionReleaseBuilderError(
                f"duplicate runtime destination: {destination}"
            )
        destinations.add(destination)
        rows.append((relative, destination, role))
    required = {
        "LICENSE",
        "make.py",
        "pyproject.toml",
        "VERIFY-AND-RUN.md",
        "scripts/verify_production_release.py",
        "pipeline/production_release_fs.py",
    }
    if not required <= destinations:
        raise ProductionReleaseBuilderError(
            "tracked Production runtime allowlist is incomplete"
        )
    for prefix in ("pipeline/", "web/studio/", "web/viewer/"):
        if not any(destination.startswith(prefix) for destination in destinations):
            raise ProductionReleaseBuilderError(
                f"tracked Production runtime is missing {prefix}"
            )
    return tuple(rows)


def _write_archive_member(
    archive: zipfile.ZipFile,
    *,
    wrapper: str,
    relative: str,
    source: BinaryIO,
    expected_bytes: int,
    expected_sha256: str | None = None,
) -> tuple[str, int]:
    info = deterministic_zip_info(f"{wrapper}/{relative}")
    info.file_size = expected_bytes
    digest = hashlib.sha256()
    observed_bytes = 0
    with archive.open(
        info,
        "w",
        force_zip64=expected_bytes >= zipfile.ZIP64_LIMIT,
    ) as destination:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            observed_bytes += len(chunk)
            if observed_bytes > expected_bytes:
                raise ProductionReleaseBuilderError(
                    f"release source expanded while archiving: {relative}"
                )
            digest.update(chunk)
            destination.write(chunk)
    observed_sha256 = digest.hexdigest()
    if (
        observed_bytes != expected_bytes
        or (
            expected_sha256 is not None
            and observed_sha256 != expected_sha256
        )
    ):
        raise ProductionReleaseBuilderError(
            f"release source changed while archiving: {relative}"
        )
    return observed_sha256, observed_bytes


def _git_blob_size(repo_root: Path, object_id: str) -> int:
    payload = _git_source_output(
        repo_root,
        ["cat-file", "-s", object_id],
        "cat-file size",
    )
    try:
        value = int(payload.strip().decode("ascii"))
    except (UnicodeError, ValueError) as exc:
        raise ProductionReleaseBuilderError(
            "Git source blob size is invalid"
        ) from exc
    if value < 0 or value > _MAXIMUM_PUBLIC_SOURCE_BYTES:
        raise ProductionReleaseBuilderError(
            "Git source blob size exceeds release bound"
        )
    return value


def _write_git_archive_member(
    archive: zipfile.ZipFile,
    *,
    wrapper: str,
    repo_root: Path,
    object_id: str,
    destination: str,
    expected_bytes: int,
) -> tuple[str, int]:
    command = _git_provenance_command(["cat-file", "blob", object_id])
    process = None
    try:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_provenance_environment(),
        )
        if process.stdout is None:
            raise TypeError("Git blob stdout pipe is unavailable")
        with process.stdout as source_stream:
            digest, byte_length = _write_archive_member(
                archive,
                wrapper=wrapper,
                relative=destination,
                source=source_stream,
                expected_bytes=expected_bytes,
            )
        returncode = process.wait()
        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, command)
    except (
        OSError,
        TypeError,
        ValueError,
        ProductionReleaseFSError,
        subprocess.CalledProcessError,
    ) as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        raise ProductionReleaseBuilderError(
            "Git source blob streaming failed"
        ) from exc
    return digest, byte_length


def build_production_release_archive(
    *,
    repo_root: Path,
    acceptance_root: Path,
    output_path: Path,
    version: str,
    source_commit: str,
    tracked_files: Iterable[str],
    output_parent: BoundDirectory | None = None,
) -> ProductionReleaseBuild:
    """Append-only build on a private Linux builder.

    The public archive is created directly with ``O_EXCL`` and remains open
    through verification.  The sidecar is the final commit marker.  Private
    staging and every partial public object are retained on failure.
    """

    try:
        require_linux_mutation_support()
    except ProductionReleaseFSError as exc:
        raise ProductionReleaseBuilderError(str(exc)) from exc

    root = Path(repo_root).expanduser().absolute()
    acceptance = Path(acceptance_root).expanduser().absolute()
    output = Path(output_path).expanduser().absolute()
    sidecar = output.with_suffix(f"{output.suffix}.sha256")
    for label, boundary in (("source", root), ("acceptance", acceptance)):
        try:
            boundary_stat = boundary.lstat()
            redirected = first_linklike_path(Path(boundary.anchor), boundary)
        except (OSError, ValueError) as exc:
            raise ProductionReleaseBuilderError(
                f"Production {label} root is missing or unsafe"
            ) from exc
        if (
            redirected is not None
            or _is_linklike(boundary, observed=boundary_stat)
            or not stat.S_ISDIR(boundary_stat.st_mode)
        ):
            raise ProductionReleaseBuilderError(
                f"Production {label} root is missing or unsafe"
            )
    try:
        tracked = tuple(tracked_files)
    except TypeError as exc:
        raise ProductionReleaseBuilderError(
            "Production source identity tracked files are invalid"
        ) from exc
    live_identity = resolve_production_release_source_identity(root)
    if ProductionReleaseSourceIdentity(source_commit, tracked) != live_identity:
        raise ProductionReleaseBuilderError(
            "Supplied Production source identity is not the exact live identity"
        )
    _ensure_release_sources_clean(root, tracked)
    runtime_rows = _runtime_rows(tracked)
    report_path = load_latest_real_scene_acceptance(acceptance)
    context = derive_production_release_context(report_path)
    scene_sources = resolve_runtime_scene_payloads(context)
    source_rows = (*context.public_files, *scene_sources)

    destinations: set[str] = set()
    folded: set[str] = set()

    def register_destination(relative: str) -> str:
        canonical = safe_posix_member_path(relative).as_posix()
        identity = portable_path_identity(canonical)
        if canonical in destinations or identity in folded:
            raise ProductionReleaseBuilderError(
                f"duplicate release destination: {canonical}"
            )
        destinations.add(canonical)
        folded.add(identity)
        return canonical

    for _relative, destination, _role in runtime_rows:
        register_destination(destination)
    for source in source_rows:
        register_destination(source.destination_path)
    generated = {
        "evidence/public-evidence.json": (
            "public-evidence",
            canonical_json_bytes(context.public_evidence),
        ),
        "evidence/acceptance-decision.json": (
            "acceptance-decision",
            canonical_model_bytes(context.decision),
        ),
        "evidence/human-review/receipt.json": (
            "human-review-receipt",
            canonical_json_bytes(context.redacted_human_review),
        ),
    }
    for relative in generated:
        register_destination(relative)

    runtime_objects = {
        destination: (
            relative,
            role,
            object_id,
            _git_blob_size(root, object_id),
        )
        for relative, destination, role in runtime_rows
        for object_id in (
            _git_tree_blob(
                root,
                source_commit=source_commit,
                relative=relative,
            ),
        )
    }
    parent = None
    archive_bound: BoundFile | None = None
    sidecar_bound: BoundFile | None = None
    public_names: list[str] = []
    try:
        parent = (
            output_parent.duplicate()
            if output_parent is not None
            else open_bound_directory(output.parent)
        )
        final_identity = resolve_production_release_source_identity(root)
        if final_identity != live_identity:
            raise ProductionReleaseBuilderError(
                "Production source identity changed during release build"
            )
        _ensure_release_sources_clean(root, tracked)
        if resolve_production_release_source_identity(root) != live_identity:
            raise ProductionReleaseBuilderError(
                "Production source identity changed during release build"
            )

        try:
            archive_bound = parent.create_file(output.name, mode=0o644)
        except FileExistsError as exc:
            raise ProductionReleaseBuilderError(
                f"Production publication destination exists: {output}"
            ) from exc
        public_names.append(output.name)
        wrapper = f"nantai-3d-{version}"
        artifacts: list[dict[str, object]] = []
        with zipfile.ZipFile(
            archive_bound.stream,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            jobs: list[tuple[str, str, object]] = []
            jobs.extend(
                (destination, "git", runtime_objects[destination])
                for destination in runtime_objects
            )
            jobs.extend(
                (source.destination_path, "source", source)
                for source in source_rows
            )
            jobs.extend(
                (relative, "generated", (role, payload))
                for relative, (role, payload) in generated.items()
            )
            for relative, kind, value in sorted(jobs):
                if kind == "git":
                    (
                        _tracked_relative,
                        role,
                        object_id,
                        expected_bytes,
                    ) = value
                    digest, byte_length = _write_git_archive_member(
                        archive,
                        wrapper=wrapper,
                        repo_root=root,
                        object_id=object_id,
                        destination=relative,
                        expected_bytes=expected_bytes,
                    )
                elif kind == "source":
                    source = value
                    role = source.role
                    _require_no_linklike_ancestors(
                        source.source_path,
                        label="release source path",
                    )
                    before = stable_regular_file_digest(
                        source.source_path,
                        maximum_bytes=source.byte_length,
                    )
                    if (
                        before.byte_length != source.byte_length
                        or before.sha256 != source.sha256
                    ):
                        raise ProductionReleaseBuilderError(
                            "release source changed before archiving"
                        )
                    with source.source_path.open("rb") as source_stream:
                        digest, byte_length = _write_archive_member(
                            archive,
                            wrapper=wrapper,
                            relative=relative,
                            source=source_stream,
                            expected_bytes=source.byte_length,
                            expected_sha256=source.sha256,
                        )
                    after = stable_regular_file_digest(
                        source.source_path,
                        maximum_bytes=source.byte_length,
                    )
                    if after != before:
                        raise ProductionReleaseBuilderError(
                            "release source changed during archiving"
                        )
                else:
                    role, payload = value

                    digest, byte_length = _write_archive_member(
                        archive,
                        wrapper=wrapper,
                        relative=relative,
                        source=BytesIO(payload),
                        expected_bytes=len(payload),
                        expected_sha256=hashlib.sha256(payload).hexdigest(),
                    )
                artifacts.append(
                    {
                        "path": relative,
                        "role": role,
                        "bytes": byte_length,
                        "sha256": digest,
                    }
                )
            artifacts.sort(key=lambda row: str(row["path"]))
            receipt = build_production_receipt(
                version=version,
                source_commit=source_commit,
                artifacts=artifacts,
                protected_roots=("evidence", "pipeline", "scripts", "web"),
                entrypoints=PRODUCTION_ENTRYPOINTS,
                public_evidence=context.public_evidence,
            )
            receipt_payload = canonical_json_bytes(receipt)
            checksum_payload = _checksum_payload(
                artifacts,
                receipt_payload,
            )

            for relative, payload in (
                (PRODUCTION_RELEASE_NAME, receipt_payload),
                (CHECKSUMS_NAME, checksum_payload),
            ):
                _write_archive_member(
                    archive,
                    wrapper=wrapper,
                    relative=relative,
                    source=BytesIO(payload),
                    expected_bytes=len(payload),
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                )
        archive_bound.finish()
        archive_sha256, archive_bytes = archive_bound.digest()
        verification = verify_production_release_archive_stream(
            archive_bound.stream
        )
        if (
            not verification.valid
            or verification.version != version
            or verification.package_content_id
            != receipt["package"]["content_id"]
            or archive_bytes <= 0
        ):
            raise ProductionReleaseBuilderError(
                "Production archive held-handle verification failed"
            )
        if resolve_production_release_source_identity(root) != live_identity:
            raise ProductionReleaseBuilderError(
                "Production source identity changed during release build"
            )
        _ensure_release_sources_clean(root, tracked)
        if resolve_production_release_source_identity(root) != live_identity:
            raise ProductionReleaseBuilderError(
                "Production source identity changed during release build"
            )

        sidecar_payload = (
            f"{archive_sha256}  {output.name}\n"
        ).encode("ascii")
        try:
            sidecar_bound = parent.create_file(sidecar.name, mode=0o644)
        except FileExistsError as exc:
            raise ProductionReleaseBuilderError(
                f"Production publication destination exists: {sidecar}",
                published=tuple(public_names),
                retained=tuple(public_names),
            ) from exc
        public_names.append(sidecar.name)
        sidecar_bound.write_all(sidecar_payload)
        sidecar_bound.finish()
        sidecar_sha, sidecar_bytes = sidecar_bound.digest()
        if (
            sidecar_bytes != len(sidecar_payload)
            or sidecar_sha != hashlib.sha256(sidecar_payload).hexdigest()
        ):
            raise ProductionReleaseBuilderError(
                "Production sidecar changed while held"
            )
        parent.fsync()
        return ProductionReleaseBuild(
            archive_path=output,
            archive_sha256=archive_sha256,
            package_content_id=str(receipt["package"]["content_id"]),
            artifact_count=len(artifacts),
            total_bytes=sum(int(row["bytes"]) for row in artifacts),
            scene_identity=str(
                context.public_evidence["scene"]["scene_identity"]
            ),
            acceptance_report_sha256=context.report_sha256,
        )
    except ProductionReleaseBuilderError as exc:
        published = exc.published or tuple(public_names)
        retained = (
            exc.retained
            or tuple(public_names)
        )
        raise ProductionReleaseBuilderError(
            f"{exc}; published={published}; retained={retained}",
            published=published,
            retained=tuple(retained),
        ) from exc
    except (
        FileExistsError,
        OSError,
        ProductionReleaseFSError,
        ProductionReleaseMutationError,
        ReleaseArchiveError,
        zipfile.BadZipFile,
    ) as exc:
        retained = tuple(public_names)
        raise ProductionReleaseBuilderError(
            "Production release build failed; "
            f"published={tuple(public_names)}; retained={retained}",
            published=tuple(public_names),
            retained=retained,
        ) from exc
    except Exception as exc:
        retained = tuple(public_names)
        raise ProductionReleaseBuilderError(
            "Production release build failed; "
            f"published={tuple(public_names)}; retained={retained}",
            published=tuple(public_names),
            retained=retained,
        ) from exc
    finally:
        if sidecar_bound is not None:
            sidecar_bound.close()
        if archive_bound is not None:
            archive_bound.close()
        if parent is not None:
            parent.close()
