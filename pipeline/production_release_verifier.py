"""Independent fail-closed verification for Production runtime releases."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
    ProductionReleaseContractError,
    load_production_receipt_bytes,
    load_public_evidence_bytes,
)
from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    inspect_zip_members,
    portable_path_identity,
    safe_posix_member_path,
    stable_regular_file_digest,
)

PRODUCTION_ARCHIVE_LIMITS = ArchiveLimits()
_CONTRACT_MAXIMUM_BYTES = 16 * 1024 * 1024


class ProductionReleaseVerificationError(ValueError):
    """Raised when a Production release cannot be independently verified."""


@dataclass(frozen=True)
class ProductionReleaseVerification:
    valid: bool
    version: str
    source_commit: str
    package_content_id: str
    artifact_count: int
    total_bytes: int
    package_integrity: str
    release_contract: str
    scene_trust_effect: str
    fixture_kind: str | None


def _verification_error(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        raise ProductionReleaseVerificationError(message)
    raise ProductionReleaseVerificationError(message) from exc


def _stable_payload(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        _verification_error(f"release file is unavailable: {path}", exc)
    if _is_linklike(path, observed=before):
        _verification_error(f"symlink release file is forbidden: {path}")
    if not stat.S_ISREG(before.st_mode):
        _verification_error(f"release file must be regular: {path}")
    if before.st_size > maximum_bytes:
        _verification_error(f"release file exceeds its maximum: {path}")
    try:
        with path.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            payload = stream.read(maximum_bytes + 1)
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        _verification_error(f"release file cannot be read: {path}", exc)
    def signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
        )
    if (
        len(payload) > maximum_bytes
        or signature(before) != signature(descriptor_before)
        or signature(before) != signature(descriptor_after)
        or signature(before) != signature(after)
        or len(payload) != before.st_size
    ):
        _verification_error(f"release file changed during read: {path}")
    return payload


def _release_files(root: Path) -> set[str]:
    files: set[str] = set()
    folded: set[str] = set()
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in tuple(directories):
            candidate = current_path / directory
            relative = candidate.relative_to(root).as_posix()
            try:
                observed = candidate.lstat()
            except OSError as exc:
                _verification_error(
                    f"release path is unavailable: {relative}",
                    exc,
                )
            if _is_linklike(candidate, observed=observed):
                _verification_error(
                    f"symlink release path is forbidden: {relative}"
                )
            if not stat.S_ISDIR(observed.st_mode):
                _verification_error(
                    f"release path must be a directory: {relative}"
                )
        for name in names:
            candidate = current_path / name
            relative = safe_posix_member_path(
                candidate.relative_to(root).as_posix()
            ).as_posix()
            try:
                observed = candidate.lstat()
            except OSError as exc:
                _verification_error(
                    f"release file is unavailable: {relative}",
                    exc,
                )
            if _is_linklike(candidate, observed=observed):
                _verification_error(
                    f"symlink release file is forbidden: {relative}"
                )
            if not stat.S_ISREG(observed.st_mode):
                _verification_error(
                    f"release file must be regular: {relative}"
                )
            identity = portable_path_identity(relative)
            if identity in folded:
                _verification_error(
                    "case-fold/normalization collision in release tree: "
                    f"{relative}"
                )
            folded.add(identity)
            files.add(relative)
    return files


def _expected_checksum_bytes(
    receipt: dict[str, object],
    receipt_payload: bytes,
) -> bytes:
    rows = [
        f"{artifact['sha256']}  {artifact['path']}\n"
        for artifact in receipt["artifacts"]
    ]
    rows.append(
        f"{hashlib.sha256(receipt_payload).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    return "".join(sorted(rows)).encode("ascii")


def _artifact_path(root: Path, relative: str) -> Path:
    member = safe_posix_member_path(relative)
    path = root.joinpath(*member.parts)
    try:
        redirected = first_linklike_path(root, path)
    except (OSError, ValueError) as exc:
        _verification_error(f"artifact path is unavailable: {relative}", exc)
    if redirected is not None:
        _verification_error(f"symlink artifact is forbidden: {relative}")
    return path


def verify_production_release_tree(
    root: str | Path,
) -> ProductionReleaseVerification:
    """Verify one extracted Production runtime without promoting scene trust."""

    project_root = Path(root).absolute()
    try:
        root_stat = project_root.lstat()
        unsafe_root = _is_linklike(project_root, observed=root_stat)
    except OSError as exc:
        raise ProductionReleaseVerificationError(
            "Production release tree is missing or unsafe"
        ) from exc
    if unsafe_root or not stat.S_ISDIR(root_stat.st_mode):
        raise ProductionReleaseVerificationError(
            "Production release tree is missing or unsafe"
        )

    receipt_path = project_root / PRODUCTION_RELEASE_NAME
    try:
        receipt_payload = _stable_payload(
            receipt_path,
            maximum_bytes=_CONTRACT_MAXIMUM_BYTES,
        )
        receipt = load_production_receipt_bytes(receipt_payload)
    except ProductionReleaseVerificationError:
        raise
    except (ProductionReleaseContractError, ReleaseArchiveError) as exc:
        raise ProductionReleaseVerificationError(
            f"Production receipt verification failed: {exc}"
        ) from exc

    declared_paths = {
        str(artifact["path"])
        for artifact in receipt["artifacts"]
    }
    allowed_paths = declared_paths | {
        PRODUCTION_RELEASE_NAME,
        CHECKSUMS_NAME,
    }
    try:
        observed_paths = _release_files(project_root)
    except ReleaseArchiveError as exc:
        raise ProductionReleaseVerificationError(str(exc)) from exc
    unexpected = sorted(observed_paths - allowed_paths)
    if unexpected:
        raise ProductionReleaseVerificationError(
            f"unexpected protected file: {unexpected[0]}"
        )
    missing = sorted(allowed_paths - observed_paths)
    if missing:
        raise ProductionReleaseVerificationError(
            f"missing protected artifact: {missing[0]}"
        )

    total_bytes = 0
    artifact_by_path: dict[str, dict[str, object]] = {}
    for raw in receipt["artifacts"]:
        artifact = dict(raw)
        relative = str(artifact["path"])
        path = _artifact_path(project_root, relative)
        try:
            digest = stable_regular_file_digest(
                path,
                maximum_bytes=int(artifact["bytes"]),
            )
        except ReleaseArchiveError as exc:
            raise ProductionReleaseVerificationError(
                f"protected artifact verification failed: {relative}: {exc}"
            ) from exc
        if (
            digest.byte_length != artifact["bytes"]
            or digest.sha256 != artifact["sha256"]
        ):
            raise ProductionReleaseVerificationError(
                f"changed protected artifact: {relative}"
            )
        artifact_by_path[relative] = artifact
        total_bytes += digest.byte_length

    checksum_path = project_root / CHECKSUMS_NAME
    checksum_payload = _stable_payload(
        checksum_path,
        maximum_bytes=_CONTRACT_MAXIMUM_BYTES,
    )
    if checksum_payload != _expected_checksum_bytes(receipt, receipt_payload):
        raise ProductionReleaseVerificationError(
            "Production release checksum file is changed or noncanonical"
        )

    acceptance = receipt["acceptance"]
    evidence_relative = str(acceptance["public_evidence_path"])
    evidence_artifact = artifact_by_path[evidence_relative]
    evidence_path = _artifact_path(project_root, evidence_relative)
    try:
        evidence_payload = _stable_payload(
            evidence_path,
            maximum_bytes=int(evidence_artifact["bytes"]),
        )
        evidence = load_public_evidence_bytes(evidence_payload)
    except ProductionReleaseVerificationError:
        raise
    except (ProductionReleaseContractError, ReleaseArchiveError) as exc:
        raise ProductionReleaseVerificationError(
            f"public evidence verification failed: {exc}"
        ) from exc

    evidence_sha = hashlib.sha256(evidence_payload).hexdigest()
    if (
        evidence_sha != acceptance["public_evidence_sha256"]
        or evidence["acceptance"]["report_sha256"]
        != acceptance["report_sha256"]
        or evidence["acceptance"]["decision_sha256"]
        != acceptance["decision_sha256"]
        or evidence["scene"]["scene_identity"]
        != receipt["scene"]["scene_identity"]
    ):
        raise ProductionReleaseVerificationError(
            "Production receipt and public evidence bindings disagree"
        )

    manifest_relative = "web/data/recon/recon_manifest.json"
    manifest_artifact = artifact_by_path.get(manifest_relative)
    if (
        manifest_artifact is None
        or manifest_artifact["sha256"]
        != evidence["scene"]["manifest_sha256"]
    ):
        raise ProductionReleaseVerificationError(
            "Production scene manifest and evidence bindings disagree"
        )
    manifest_digest = stable_regular_file_digest(
        _artifact_path(project_root, manifest_relative)
    )
    if manifest_digest.sha256 != evidence["scene"]["manifest_sha256"]:
        raise ProductionReleaseVerificationError(
            "Production scene manifest content disagrees with evidence"
        )

    fixture_kind = evidence["fixture_kind"]
    release_contract = (
        "modeled-contract-only"
        if fixture_kind == "modeled-contract-not-real-release"
        else "production-accepted"
    )
    return ProductionReleaseVerification(
        valid=True,
        version=str(receipt["version"]),
        source_commit=str(receipt["source"]["git_commit"]),
        package_content_id=str(receipt["package"]["content_id"]),
        artifact_count=len(receipt["artifacts"]),
        total_bytes=total_bytes,
        package_integrity="verified",
        release_contract=release_contract,
        scene_trust_effect=str(receipt["scene"]["trust_effect"]),
        fixture_kind=fixture_kind,
    )


def extract_production_release_archive(
    archive_path: str | Path,
    destination: str | Path,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> Path:
    """Extract one inspected archive into a new destination and return its root."""

    source = Path(archive_path)
    target = Path(destination)
    try:
        source_stat = source.lstat()
        unsafe_source = _is_linklike(source, observed=source_stat)
    except OSError as exc:
        raise ProductionReleaseVerificationError(
            "Production release archive is missing or unsafe"
        ) from exc
    if unsafe_source or not stat.S_ISREG(source_stat.st_mode):
        raise ProductionReleaseVerificationError(
            "Production release archive is missing or unsafe"
        )
    try:
        unsafe_target = first_linklike_path(Path(target.anchor), target)
    except (OSError, ValueError) as exc:
        raise ProductionReleaseVerificationError(
            "Production extraction destination is unsafe"
        ) from exc
    if target.exists() or unsafe_target == target:
        raise ProductionReleaseVerificationError(
            "Production extraction destination already exists"
        )
    if unsafe_target is not None:
        raise ProductionReleaseVerificationError(
            "Production extraction destination is unsafe"
        )

    created = False
    try:
        target.mkdir(parents=False, exist_ok=False)
        created = True
        with zipfile.ZipFile(source) as archive:
            inspected = inspect_zip_members(archive, limits)
            infos = {info.filename: info for info in archive.infolist()}
            wrappers = {member.path.parts[0] for member in inspected}
            if len(wrappers) != 1:
                _verification_error(
                    "Production release archive must contain exactly one root"
                )
            for member in inspected:
                if stat.S_IFMT(member.unix_mode) != stat.S_IFREG:
                    if stat.S_IFMT(member.unix_mode) == stat.S_IFDIR:
                        _verification_error(
                            "Production release archive directory entries "
                            "are forbidden"
                        )
                    _verification_error(
                        "Production release archive members must be regular"
                    )
                if len(member.path.parts) < 2:
                    _verification_error(
                        "Production release archive member lacks wrapper root"
                    )
                relative = PurePosixPath(*member.path.parts[1:])
                safe_posix_member_path(relative.as_posix())
                destination_path = target.joinpath(*relative.parts)
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                info = infos[member.path.as_posix()]
                observed = hashlib.sha256()
                observed_bytes = 0
                with archive.open(info, "r") as source_stream:
                    with destination_path.open("xb") as destination_stream:
                        while True:
                            chunk = source_stream.read(1024 * 1024)
                            if not chunk:
                                break
                            observed_bytes += len(chunk)
                            if observed_bytes > member.byte_length:
                                _verification_error(
                                    "Production archive member expanded beyond "
                                    "declared length"
                                )
                            observed.update(chunk)
                            destination_stream.write(chunk)
                if observed_bytes != member.byte_length:
                    _verification_error(
                        "Production archive member length disagrees"
                    )
                written = stable_regular_file_digest(destination_path)
                if (
                    written.byte_length != observed_bytes
                    or written.sha256 != observed.hexdigest()
                ):
                    _verification_error(
                        "Production archive extracted member changed"
                    )
        return target
    except ProductionReleaseVerificationError:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise
    except (
        ReleaseArchiveError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        OSError,
        EOFError,
    ) as exc:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise ProductionReleaseVerificationError(
            f"Production archive verification failed: {exc}"
        ) from exc


def verify_production_release_archive(
    path: str | Path,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> ProductionReleaseVerification:
    """Safely extract and independently verify one Production runtime ZIP."""

    with tempfile.TemporaryDirectory(
        prefix="nantai-production-verify-"
    ) as temporary:
        extraction = Path(temporary) / "runtime"
        extracted = extract_production_release_archive(
            path,
            extraction,
            limits=limits,
        )
        return verify_production_release_tree(extracted)
