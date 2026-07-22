"""Durable six-role batch ledger for the reciprocal production caller.

The ledger records caller outcomes only.  It never upgrades the synthetic L0
scene beyond ``modeled-unverified`` and never converts a failed camera into a
quality decision without a canonical quality report.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.studio_jobs import ProjectFileLock

from . import canary
from .production_preflight import (
    ProductionClearancePolicy,
    production_clearance_policy_sha256,
)
from .production_profile import (
    ProductionCameraPlan,
    canonical_production_plan_bytes,
)
from .production_quality_gates import (
    ProductionFrameQualityPolicyV2,
    production_frame_quality_policy_v2_sha256,
)
from .production_render import (
    LocalProductionQualityPolicy,
    local_production_quality_policy_sha256,
)
from .reciprocal_route_module import ModuleId
from .reciprocal_route_production import (
    ReciprocalProductionCameraResult,
    ReciprocalProductionError,
    VerifiedReciprocalProductionBuild,
    reciprocal_object_registry_sha256,
    reciprocal_role_camera_candidate_sha256,
    run_reciprocal_production_camera,
)

RECIPROCAL_PRODUCTION_BATCH_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-batch-journal.v1"
)
RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS: tuple[ModuleId, ...] = (
    "central-courtyard-downhill",
    "bridge-deck-crossing",
    "watermill-tailrace",
    "covered-gallery-underpass",
    "forest-orchard-boundary",
    "lower-valley-uphill",
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReciprocalProductionBatchError(RuntimeError):
    """Raised when a reciprocal batch ledger cannot be trusted."""


class ReciprocalProductionBatchTarget(FrozenModel):
    """One exact role-to-obstructed-camera assignment."""

    role_module_id: ModuleId
    target_camera_id: str = Field(
        pattern=r"^camera-ground-route-(?:010|039)$",
    )


class ReciprocalProductionBatchEntry(FrozenModel):
    """One durable role outcome in the six-role batch."""

    role_module_id: ModuleId
    target_camera_id: str = Field(
        pattern=r"^camera-ground-route-(?:010|039)$",
    )
    role_camera_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["planned", "accepted", "failed"]
    render_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    frame_path: str | None = Field(
        default=None,
        pattern=(
            r"^frames/[0-9a-f]{64}/camera-ground-route-(?:010|039)$"
        ),
    )
    preflight_request_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    preflight_report_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    render_request_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    render_report_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    journal_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    quality_request_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    quality_report_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    error_message: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def _state_fields_are_exact(self) -> ReciprocalProductionBatchEntry:
        evidence = (
            self.render_id,
            self.frame_path,
            self.preflight_request_sha256,
            self.preflight_report_sha256,
            self.render_request_sha256,
            self.render_report_sha256,
            self.journal_sha256,
            self.quality_request_sha256,
            self.quality_report_sha256,
        )
        if self.state == "accepted":
            if any(value is None for value in evidence):
                raise ValueError("accepted batch entry lacks bound evidence")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("accepted batch entry carries a failure")
        elif self.state == "failed":
            if any(value is not None for value in evidence):
                raise ValueError("failed batch entry cannot claim accepted evidence")
            if self.error_code is None or self.error_message is None:
                raise ValueError("failed batch entry lacks a bounded failure")
        elif (
            any(value is not None for value in evidence)
            or self.error_code is not None
            or self.error_message is not None
        ):
            raise ValueError("planned batch entry already carries an outcome")
        return self


class ReciprocalProductionBatchJournal(FrozenModel):
    """Canonical resumable state for the exact six reciprocal roles."""

    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-production-batch-journal.v1"
    ] = RECIPROCAL_PRODUCTION_BATCH_SCHEMA
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blend_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_module_build_report_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    reciprocal_route_module_plan_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    object_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_production_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blender_executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    clearance_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    local_quality_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_render_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[ReciprocalProductionBatchEntry, ...] = Field(
        min_length=6,
        max_length=6,
    )
    synthetic: Literal[True] = True
    verification_level: Literal["L0"] = "L0"
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )

    @model_validator(mode="after")
    def _journal_is_exact(self) -> ReciprocalProductionBatchJournal:
        if tuple(row.role_module_id for row in self.entries) != (
            RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS
        ):
            raise ValueError("batch journal role order is not exact")
        payload = self.model_dump(mode="json", exclude={"journal_sha256"})
        if self.journal_sha256 != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("batch journal SHA-256 is invalid")
        return self


@dataclass(frozen=True)
class ReciprocalProductionBatchResult:
    batch_id: str
    batch_root: Path
    journal_path: Path
    accepted_count: int
    failed_count: int
    reused_count: int


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def canonical_reciprocal_production_batch_journal_bytes(
    journal: ReciprocalProductionBatchJournal,
) -> bytes:
    return _canonical(journal.model_dump(mode="json"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _journal_from_payload(payload: dict[str, object]) -> ReciprocalProductionBatchJournal:
    unsigned = ReciprocalProductionBatchJournal.model_construct(
        journal_sha256="0" * 64,
        **payload,
    )
    journal_sha256 = hashlib.sha256(
        _canonical(unsigned.model_dump(mode="json", exclude={"journal_sha256"})),
    ).hexdigest()
    return ReciprocalProductionBatchJournal(
        journal_sha256=journal_sha256,
        **payload,
    )


def load_reciprocal_production_batch_journal(
    path: Path,
) -> ReciprocalProductionBatchJournal:
    """Load one bounded canonical batch journal and verify its self digest."""

    path = Path(path)
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > canary.MAX_BUILD_REPORT_BYTES:
            raise ReciprocalProductionBatchError(
                "reciprocal batch journal bytes are absent or unbounded",
            )
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=canary._reject_duplicate_keys,
        )
        journal = ReciprocalProductionBatchJournal.model_validate_json(raw)
        if raw != canonical_reciprocal_production_batch_journal_bytes(journal):
            raise ReciprocalProductionBatchError(
                "reciprocal batch journal is not canonical JSON",
            )
        return journal
    except ReciprocalProductionBatchError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReciprocalProductionBatchError(
            "reciprocal batch journal validation failed",
        ) from exc


def _persist_journal(path: Path, journal: ReciprocalProductionBatchJournal) -> None:
    payload = canonical_reciprocal_production_batch_journal_bytes(journal)
    if not path.exists():
        canary._write_new_file(path, payload)  # noqa: SLF001
        canary._flush_directory(path.parent)  # noqa: SLF001
        return
    if canary._is_linklike(path):  # noqa: SLF001
        raise ReciprocalProductionBatchError("reciprocal batch journal is redirected")
    temporary = path.parent / f".batch-journal-{uuid.uuid4().hex}.tmp"
    try:
        canary._write_new_file(temporary, payload)  # noqa: SLF001
        os.replace(temporary, path)
        canary._flush_file(path)  # noqa: SLF001
        canary._flush_directory(path.parent)  # noqa: SLF001
    finally:
        temporary.unlink(missing_ok=True)


def _failure_code(message: str) -> str:
    if message.startswith("post-render quality rejected camera"):
        return "post-render-quality-rejected"
    if message.startswith("preflight rejected camera"):
        return "preflight-rejected"
    if message.startswith("local quality rejected camera"):
        return "local-quality-rejected"
    return "camera-run-failed"


def _immutable_payload(
    *,
    verified_build: VerifiedReciprocalProductionBuild,
    source_plan: ProductionCameraPlan,
    targets: tuple[ReciprocalProductionBatchTarget, ...],
    blender_executable_sha256: str,
    clearance_policy: ProductionClearancePolicy,
    quality_policy: LocalProductionQualityPolicy,
    post_render_policy: ProductionFrameQualityPolicyV2,
) -> dict[str, object]:
    candidates = verified_build.role_camera_candidates
    if (
        tuple(row.role_module_id for row in candidates)
        != RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS
        or tuple(row.role_module_id for row in targets)
        != RECIPROCAL_PRODUCTION_BATCH_ROLE_IDS
    ):
        raise ReciprocalProductionBatchError(
            "reciprocal batch requires the exact ordered six roles",
        )
    entries = tuple(
        ReciprocalProductionBatchEntry(
            role_module_id=target.role_module_id,
            target_camera_id=target.target_camera_id,
            role_camera_candidate_sha256=(
                reciprocal_role_camera_candidate_sha256(candidate)
            ),
            state="planned",
        )
        for target, candidate in zip(targets, candidates, strict=True)
    )
    return {
        "schema_version": RECIPROCAL_PRODUCTION_BATCH_SCHEMA,
        "build_id": verified_build.build_id,
        "build_report_sha256": verified_build.report_sha256,
        "blend_sha256": verified_build.blend_sha256,
        "environment_module_build_report_sha256": (
            verified_build.environment_module_build_report_sha256
        ),
        "reciprocal_route_module_plan_sha256": (
            verified_build.reciprocal_route_module_plan_sha256
        ),
        "object_registry_sha256": reciprocal_object_registry_sha256(
            verified_build.object_registry,
        ),
        "source_production_plan_sha256": hashlib.sha256(
            canonical_production_plan_bytes(source_plan),
        ).hexdigest(),
        "blender_executable_sha256": blender_executable_sha256,
        "clearance_policy_sha256": production_clearance_policy_sha256(
            clearance_policy,
        ),
        "local_quality_policy_sha256": local_production_quality_policy_sha256(
            quality_policy,
        ),
        "post_render_policy_sha256": production_frame_quality_policy_v2_sha256(
            post_render_policy,
        ),
        "entries": entries,
        "synthetic": True,
        "verification_level": "L0",
        "geometry_trust": "simplified-pbr-not-render-parity",
        "trust_effect": "none-quality-filter-only",
    }


def _batch_id(payload: dict[str, object]) -> str:
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"entries"}
    }
    identity["targets"] = [
        {
            "role_module_id": row.role_module_id,
            "target_camera_id": row.target_camera_id,
            "role_camera_candidate_sha256": row.role_camera_candidate_sha256,
        }
        for row in payload["entries"]
    ]
    return hashlib.sha256(_canonical(identity)).hexdigest()


def _journal_immutable(journal: ReciprocalProductionBatchJournal) -> tuple[object, ...]:
    return (
        journal.batch_id,
        journal.build_id,
        journal.build_report_sha256,
        journal.blend_sha256,
        journal.environment_module_build_report_sha256,
        journal.reciprocal_route_module_plan_sha256,
        journal.object_registry_sha256,
        journal.source_production_plan_sha256,
        journal.blender_executable_sha256,
        journal.clearance_policy_sha256,
        journal.local_quality_policy_sha256,
        journal.post_render_policy_sha256,
        tuple(
            (
                row.role_module_id,
                row.target_camera_id,
                row.role_camera_candidate_sha256,
            )
            for row in journal.entries
        ),
    )


def _with_batch_writer_lock(function):
    """Serialize all reads and replacements of one batch journal."""

    @wraps(function)
    def locked(*args, **kwargs):
        root = Path(kwargs["output_root"]).absolute()
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)
        if canary._is_linklike(root):  # noqa: SLF001
            raise ReciprocalProductionBatchError(
                "reciprocal batch root is redirected",
            )
        with ProjectFileLock(
            root / ".reciprocal-production-batch.lock",
            role="writer",
        ):
            return function(*args, **{**kwargs, "output_root": root})

    return locked


@_with_batch_writer_lock
def run_reciprocal_production_batch(
    *,
    verified_build: VerifiedReciprocalProductionBuild,
    source_plan: ProductionCameraPlan,
    targets: tuple[ReciprocalProductionBatchTarget, ...],
    blender_executable: Path,
    output_root: Path,
    clearance_policy: ProductionClearancePolicy,
    quality_policy: LocalProductionQualityPolicy,
    post_render_policy: ProductionFrameQualityPolicyV2,
    camera_runner: Callable[..., ReciprocalProductionCameraResult] = (
        run_reciprocal_production_camera
    ),
    timeout_seconds: int = 1800,
) -> ReciprocalProductionBatchResult:
    """Run/resume the exact six roles and durably record every outcome.

    ``camera_runner`` is a test seam. Production callers must retain the
    default fail-closed per-camera runner.
    """

    blender_executable = Path(blender_executable).resolve(strict=True)
    output_root = Path(output_root).absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    output_root = output_root.resolve(strict=True)
    if canary._is_linklike(output_root):  # noqa: SLF001
        raise ReciprocalProductionBatchError("reciprocal batch root is redirected")
    frames_root = output_root / "frames"
    frames_root.mkdir(exist_ok=True)
    if canary._is_linklike(frames_root):  # noqa: SLF001
        raise ReciprocalProductionBatchError("reciprocal batch frames are redirected")

    payload = _immutable_payload(
        verified_build=verified_build,
        source_plan=source_plan,
        targets=targets,
        blender_executable_sha256=_sha256_file(blender_executable),
        clearance_policy=clearance_policy,
        quality_policy=quality_policy,
        post_render_policy=post_render_policy,
    )
    batch_id = _batch_id(payload)
    payload["batch_id"] = batch_id
    journal_path = output_root / "batch-journal.json"
    if journal_path.exists() or canary._is_linklike(journal_path):  # noqa: SLF001
        journal = load_reciprocal_production_batch_journal(journal_path)
        expected = _journal_from_payload(payload)
        if _journal_immutable(journal) != _journal_immutable(expected):
            raise ReciprocalProductionBatchError(
                "existing reciprocal batch belongs to different inputs",
            )
    else:
        journal = _journal_from_payload(payload)
        _persist_journal(journal_path, journal)

    entries = list(journal.entries)
    reused_count = sum(row.state == "accepted" for row in entries)
    candidates = {
        row.role_module_id: row
        for row in verified_build.role_camera_candidates
    }
    for index, (entry, target) in enumerate(
        zip(entries, targets, strict=True),
    ):
        if entry.state == "accepted":
            frame_path = output_root / str(entry.frame_path)
            if (
                not frame_path.is_dir()
                or canary._is_linklike(frame_path)  # noqa: SLF001
                or frame_path.resolve(strict=True) != frame_path
            ):
                raise ReciprocalProductionBatchError(
                    "accepted reciprocal batch frame is absent or redirected",
                )
            continue
        try:
            result = camera_runner(
                verified_build=verified_build,
                source_plan=source_plan,
                role_camera_candidate=candidates[target.role_module_id],
                target_camera_id=target.target_camera_id,
                blender_executable=blender_executable,
                output_root=frames_root,
                clearance_policy=clearance_policy,
                quality_policy=quality_policy,
                post_render_policy=post_render_policy,
                timeout_seconds=timeout_seconds,
            )
            expected_frame = (
                frames_root / result.render_id / target.target_camera_id
            ).resolve(strict=True)
            if (
                result.camera_id != target.target_camera_id
                or result.frame_root.resolve(strict=True) != expected_frame
                or canary._is_linklike(expected_frame)  # noqa: SLF001
            ):
                raise ReciprocalProductionBatchError(
                    "camera runner returned an unbound reciprocal frame",
                )
            relative_frame = expected_frame.relative_to(output_root).as_posix()
            entries[index] = ReciprocalProductionBatchEntry(
                role_module_id=entry.role_module_id,
                target_camera_id=entry.target_camera_id,
                role_camera_candidate_sha256=(
                    entry.role_camera_candidate_sha256
                ),
                state="accepted",
                render_id=result.render_id,
                frame_path=relative_frame,
                preflight_request_sha256=result.preflight_request_sha256,
                preflight_report_sha256=result.preflight_report_sha256,
                render_request_sha256=result.render_request_sha256,
                render_report_sha256=result.render_report_sha256,
                journal_sha256=result.journal_sha256,
                quality_request_sha256=result.quality_request_sha256,
                quality_report_sha256=result.quality_report_sha256,
            )
        except ReciprocalProductionError as exc:
            message = str(exc)[:512] or "reciprocal camera failed safely"
            entries[index] = ReciprocalProductionBatchEntry(
                role_module_id=entry.role_module_id,
                target_camera_id=entry.target_camera_id,
                role_camera_candidate_sha256=(
                    entry.role_camera_candidate_sha256
                ),
                state="failed",
                error_code=_failure_code(message),
                error_message=message,
            )
        payload["entries"] = tuple(entries)
        journal = _journal_from_payload(payload)
        _persist_journal(journal_path, journal)

    accepted_count = sum(row.state == "accepted" for row in entries)
    failed_count = sum(row.state == "failed" for row in entries)
    return ReciprocalProductionBatchResult(
        batch_id=batch_id,
        batch_root=output_root,
        journal_path=journal_path,
        accepted_count=accepted_count,
        failed_count=failed_count,
        reused_count=reused_count,
    )
