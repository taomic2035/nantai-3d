"""Fail-closed privacy audit for verified Production runtime releases."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.production_release_fs import (
    ProductionReleaseFSError,
    ProductionReleaseMutationError,
    open_bound_directory,
    require_linux_mutation_support,
)
from pipeline.production_release_verifier import (
    PRODUCTION_ARCHIVE_LIMITS,
    ProductionReleaseVerification,
    ProductionReleaseVerificationError,
    verify_production_release_archive_stream,
    verify_production_release_tree,
)
from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    canonical_json_bytes,
    inspect_zip_members,
    safe_posix_member_path,
)

PRIVACY_POLICY_SCHEMA = "nantai.production-privacy-policy.v1"
PRIVACY_REPORT_SCHEMA = "nantai.production-privacy-audit.v1"
PRIVACY_SCAN_CHUNK_BYTES = 1024 * 1024
_MAXIMUM_POLICY_BYTES = 1024 * 1024
_MAXIMUM_NEEDLES = 1024
_MINIMUM_NEEDLE_BYTES = 8
_MAXIMUM_NEEDLE_BYTES = PRIVACY_SCAN_CHUNK_BYTES
_BUILTIN_OVERLAP_BYTES = 512

_PEM_MARKERS = (
    b"-----begin private key-----",
    b"-----begin rsa private key-----",
    b"-----begin ec private key-----",
    b"-----begin openssh private key-----",
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?:"
    rb"authorization[ \t]*:[ \t]*bearer[ \t]+"
    rb"[A-Za-z0-9._~+/\-=]{8,}"
    rb"|"
    rb"[\"']?(?:aws_secret_access_key|openai_api_key|api[_-]?key|"
    rb"password|client_secret)[\"']?[ \t]*(?:=|:)[ \t]*[\"']?"
    rb"[A-Za-z0-9._~+/\-=]{8,}"
    rb")",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    rb"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._-]{1,255}[\\/])"
)
_POSIX_ABSOLUTE_PATH = re.compile(
    rb"(?<![A-Za-z0-9:/])/"
    rb"(?:home|Users|root|private|tmp|var|opt|srv|mnt|Volumes|etc)/"
)


class ProductionReleasePrivacyError(ValueError):
    """Raised when a Production privacy audit contract cannot be trusted."""

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


@dataclass(frozen=True, order=True)
class PrivacyFinding:
    category: str
    path: str


@dataclass(frozen=True)
class ProductionPrivacyReport:
    schema: str
    valid: bool
    package_content_id: str
    finding_count: int
    findings: tuple[PrivacyFinding, ...]
    scene_trust_effect: str


@dataclass(frozen=True)
class _PrivacyPolicy:
    needles: tuple[bytes, ...]


def privacy_report_bytes(report: ProductionPrivacyReport) -> bytes:
    """Serialize one public-safe privacy report as canonical JSON."""

    return canonical_json_bytes(asdict(report))


def publish_privacy_report(
    report: ProductionPrivacyReport,
    output: str | Path,
) -> None:
    """Append-only publish one canonical report on a private Linux builder."""

    destination = Path(output).absolute()
    try:
        require_linux_mutation_support()
    except ProductionReleaseFSError as exc:
        raise ProductionReleasePrivacyError(str(exc)) from exc
    payload = privacy_report_bytes(report)
    created = False
    try:
        with open_bound_directory(destination.parent) as parent:
            try:
                bound = parent.create_file(destination.name, mode=0o600)
            except FileExistsError as exc:
                raise ProductionReleasePrivacyError(
                    "privacy report destination already exists"
                ) from exc
            created = True
            with bound:
                bound.write_all(payload)
                bound.finish()
                observed_sha, observed_bytes = bound.digest()
                if (
                    observed_bytes != len(payload)
                    or observed_sha != hashlib.sha256(payload).hexdigest()
                ):
                    raise ProductionReleasePrivacyError(
                        "privacy report changed while held; retained",
                        published=(destination.name,),
                        retained=(destination.name,),
                    )
                parent.fsync()
                parent.verify_lexical_identity()
                parent.verify_child_identity(destination.name, bound)
                final_sha, final_bytes = bound.digest()
                if (
                    final_sha != observed_sha
                    or final_bytes != observed_bytes
                    or bound.read_bytes(
                        maximum_bytes=len(payload)
                    )
                    != payload
                ):
                    raise ProductionReleasePrivacyError(
                        "privacy report final held-handle seal failed",
                        published=(destination.name,),
                        retained=(destination.name,),
                    )
    except ProductionReleasePrivacyError:
        raise
    except ProductionReleaseMutationError as exc:
        state = tuple(
            dict.fromkeys(
                ((destination.name,) if created else ()) + exc.retained
            )
        )
        raise ProductionReleasePrivacyError(
            f"{exc}; published={state}; retained={state}",
            published=state,
            retained=state,
        ) from exc
    except (ProductionReleaseFSError, OSError) as exc:
        state = (destination.name,) if created else ()
        raise ProductionReleasePrivacyError(
            "privacy report publication failed; "
            f"published={state}; retained={state}",
            published=state,
            retained=state,
        ) from exc


def _signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _require_safe_policy_location(path: Path) -> Path:
    policy_path = path.expanduser().absolute()
    try:
        redirected = first_linklike_path(
            Path(policy_path.anchor),
            policy_path,
        )
    except (OSError, ValueError) as exc:
        raise ProductionReleasePrivacyError(
            "privacy policy location is unsafe"
        ) from exc
    if redirected is not None:
        raise ProductionReleasePrivacyError(
            "privacy policy location is unsafe"
        )
    return policy_path


def _stable_policy_bytes(path: Path) -> bytes:
    path = _require_safe_policy_location(path)
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ProductionReleasePrivacyError(
            "privacy policy is unavailable"
        ) from exc
    if _is_linklike(path, observed=path_before) or not stat.S_ISREG(
        path_before.st_mode
    ):
        raise ProductionReleasePrivacyError(
            "privacy policy must be a regular non-link file"
        )
    if path_before.st_size > _MAXIMUM_POLICY_BYTES:
        raise ProductionReleasePrivacyError("privacy policy exceeds its limit")
    try:
        with path.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            payload = stream.read(_MAXIMUM_POLICY_BYTES + 1)
            descriptor_after = os.fstat(stream.fileno())
        _require_safe_policy_location(path)
        path_after = path.lstat()
    except OSError as exc:
        raise ProductionReleasePrivacyError(
            "privacy policy cannot be read"
        ) from exc
    expected = _signature(path_before)
    if (
        len(payload) > _MAXIMUM_POLICY_BYTES
        or expected != _signature(descriptor_before)
        or expected != _signature(descriptor_after)
        or expected != _signature(path_after)
        or len(payload) != path_before.st_size
    ):
        raise ProductionReleasePrivacyError(
            "privacy policy changed during read"
        )
    return payload


def _load_policy(path: Path) -> _PrivacyPolicy:
    payload = _stable_policy_bytes(path)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionReleasePrivacyError(
            "privacy policy is not canonical JSON"
        ) from exc
    if canonical_json_bytes(value) != payload:
        raise ProductionReleasePrivacyError(
            "privacy policy is not canonical JSON"
        )
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "needles"}
        or value["schema"] != PRIVACY_POLICY_SCHEMA
        or not isinstance(value["needles"], list)
        or not value["needles"]
        or len(value["needles"]) > _MAXIMUM_NEEDLES
    ):
        raise ProductionReleasePrivacyError(
            "privacy policy fields are invalid"
        )

    needles: list[bytes] = []
    observed: set[bytes] = set()
    for row in value["needles"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"encoding", "value"}
            or row["encoding"] != "base64"
            or not isinstance(row["value"], str)
        ):
            raise ProductionReleasePrivacyError(
                "privacy policy needle is invalid"
            )
        try:
            needle = base64.b64decode(row["value"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ProductionReleasePrivacyError(
                "privacy policy needle is invalid"
            ) from exc
        if base64.b64encode(needle).decode("ascii") != row["value"]:
            raise ProductionReleasePrivacyError(
                "privacy policy needle is invalid"
            )
        if not (
            _MINIMUM_NEEDLE_BYTES
            <= len(needle)
            <= _MAXIMUM_NEEDLE_BYTES
        ):
            raise ProductionReleasePrivacyError(
                "privacy policy needle length is invalid"
            )
        if needle in observed:
            raise ProductionReleasePrivacyError(
                "privacy policy contains duplicate needles"
            )
        observed.add(needle)
        needles.append(needle)
    return _PrivacyPolicy(needles=tuple(needles))


def _release_files(
    root: Path,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> tuple[tuple[str, Path], ...]:
    observed: list[tuple[str, Path]] = []
    total_bytes = 0
    observed_members = 0
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in tuple(directories):
            observed_members += 1
            if observed_members > limits.maximum_members:
                raise ProductionReleasePrivacyError(
                    "release member count exceeds its maximum"
                )
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            try:
                member = safe_posix_member_path(relative)
            except ReleaseArchiveError as exc:
                raise ProductionReleasePrivacyError(
                    "release directory is unavailable or unsafe"
                ) from exc
            if (
                len(relative.encode("utf-8")) > limits.maximum_path_bytes
                or len(member.parts) > limits.maximum_path_components
            ):
                raise ProductionReleasePrivacyError(
                    f"release directory path exceeds its maximum: {relative}"
                )
            try:
                candidate_stat = candidate.lstat()
            except OSError as exc:
                raise ProductionReleasePrivacyError(
                    "release directory is unavailable"
                ) from exc
            if _is_linklike(candidate, observed=candidate_stat):
                raise ProductionReleasePrivacyError(
                    f"unsafe release directory: {relative}"
                )
            if not stat.S_ISDIR(candidate_stat.st_mode):
                raise ProductionReleasePrivacyError(
                    f"unsafe release directory: {relative}"
                )
        for name in names:
            observed_members += 1
            if observed_members > limits.maximum_members:
                raise ProductionReleasePrivacyError(
                    "release member count exceeds its maximum"
                )
            candidate = current_path / name
            try:
                relative = safe_posix_member_path(
                    candidate.relative_to(root).as_posix()
                ).as_posix()
                candidate_stat = candidate.lstat()
            except (OSError, ReleaseArchiveError) as exc:
                raise ProductionReleasePrivacyError(
                    "release file is unavailable or unsafe"
                ) from exc
            if _is_linklike(candidate, observed=candidate_stat):
                raise ProductionReleasePrivacyError(
                    f"unsafe release file: {relative}"
                )
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise ProductionReleasePrivacyError(
                    f"unsafe release file: {relative}"
                )
            observed.append((relative, candidate))
            if (
                len(relative.encode("utf-8")) > limits.maximum_path_bytes
                or len(safe_posix_member_path(relative).parts)
                > limits.maximum_path_components
            ):
                raise ProductionReleasePrivacyError(
                    f"release file path exceeds its maximum: {relative}"
                )
            if candidate_stat.st_size > limits.maximum_member_bytes:
                raise ProductionReleasePrivacyError(
                    f"release file exceeds its maximum: {relative}"
                )
            total_bytes += candidate_stat.st_size
            if total_bytes > limits.maximum_total_bytes:
                raise ProductionReleasePrivacyError(
                    "release total size exceeds its maximum"
                )
    return tuple(sorted(observed))


def _window_categories(window: bytes, policy: _PrivacyPolicy) -> set[str]:
    categories: set[str] = set()
    if any(needle in window for needle in policy.needles):
        categories.add("private-policy-needle")
    lowered = window.lower()
    if any(marker in lowered for marker in _PEM_MARKERS):
        categories.add("pem-private-key")
    if _CREDENTIAL_ASSIGNMENT.search(window) is not None:
        categories.add("credential-marker")
    if (
        _WINDOWS_ABSOLUTE_PATH.search(window) is not None
        or _POSIX_ABSOLUTE_PATH.search(window) is not None
    ):
        categories.add("absolute-filesystem-path")
    return categories


def _read_scan_chunk(stream) -> bytes:
    return stream.read(PRIVACY_SCAN_CHUNK_BYTES)


def _scan_stream(
    relative: str,
    stream,
    policy: _PrivacyPolicy,
    *,
    expected_bytes: int,
) -> set[PrivacyFinding]:
    overlap_bytes = max(
        _BUILTIN_OVERLAP_BYTES,
        max(len(needle) for needle in policy.needles) - 1,
    )
    carry = b""
    findings: set[PrivacyFinding] = set()
    observed_bytes = 0
    while True:
        chunk = _read_scan_chunk(stream)
        if not chunk:
            break
        if len(chunk) > PRIVACY_SCAN_CHUNK_BYTES:
            raise ProductionReleasePrivacyError(
                f"privacy scan chunk exceeded its bound: {relative}"
            )
        observed_bytes += len(chunk)
        if observed_bytes > expected_bytes:
            raise ProductionReleasePrivacyError(
                f"release file changed during privacy scan: {relative}"
            )
        window = carry + chunk
        findings.update(
            PrivacyFinding(category=category, path=relative)
            for category in _window_categories(window, policy)
        )
        carry = window[-overlap_bytes:]
    if observed_bytes != expected_bytes:
        raise ProductionReleasePrivacyError(
            f"release file changed during privacy scan: {relative}"
        )
    return findings


def _scan_file(
    relative: str,
    path: Path,
    policy: _PrivacyPolicy,
) -> set[PrivacyFinding]:
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ProductionReleasePrivacyError(
            f"release file became unavailable: {relative}"
        ) from exc
    if _is_linklike(path, observed=path_before) or not stat.S_ISREG(
        path_before.st_mode
    ):
        raise ProductionReleasePrivacyError(
            f"release file became unsafe: {relative}"
        )

    try:
        with path.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            if _signature(path_before) != _signature(descriptor_before):
                raise ProductionReleasePrivacyError(
                    f"release file changed before privacy scan: {relative}"
                )
            findings = _scan_stream(
                relative,
                stream,
                policy,
                expected_bytes=path_before.st_size,
            )
            descriptor_after = os.fstat(stream.fileno())
        path_after = path.lstat()
    except ProductionReleasePrivacyError:
        raise
    except OSError as exc:
        raise ProductionReleasePrivacyError(
            f"release file cannot be privacy scanned: {relative}"
        ) from exc

    expected = _signature(path_before)
    if (
        expected != _signature(descriptor_after)
        or expected != _signature(path_after)
    ):
        raise ProductionReleasePrivacyError(
            f"release file changed during privacy scan: {relative}"
        )
    return findings


def _audit_verified_tree(
    root: Path,
    policy_path: Path,
) -> ProductionPrivacyReport:
    try:
        verification_before = verify_production_release_tree(root)
    except ProductionReleaseVerificationError as exc:
        raise ProductionReleasePrivacyError(
            "Production release verification failed before privacy scan"
        ) from exc
    policy_path = _require_safe_policy_location(policy_path)
    root = root.expanduser().absolute()
    if policy_path.is_relative_to(root):
        raise ProductionReleasePrivacyError(
            "privacy policy must remain outside the public release"
        )
    policy = _load_policy(policy_path)

    findings: set[PrivacyFinding] = set()
    for relative, path in _release_files(root):
        findings.update(_scan_file(relative, path, policy))

    try:
        verification_after = verify_production_release_tree(root)
    except ProductionReleaseVerificationError as exc:
        raise ProductionReleasePrivacyError(
            "Production release verification failed after privacy scan"
        ) from exc
    _require_same_verification(verification_before, verification_after)
    ordered = tuple(sorted(findings))
    return ProductionPrivacyReport(
        schema=PRIVACY_REPORT_SCHEMA,
        valid=not ordered,
        package_content_id=verification_after.package_content_id,
        finding_count=len(ordered),
        findings=ordered,
        scene_trust_effect="none",
    )


def _require_same_verification(
    before: ProductionReleaseVerification,
    after: ProductionReleaseVerification,
) -> None:
    if before != after:
        raise ProductionReleasePrivacyError(
            "Production release identity changed during privacy scan"
        )


def audit_production_release_privacy_stream(
    source_stream: BinaryIO,
    policy_path: Path,
) -> ProductionPrivacyReport:
    """Audit one already-held archive inode without reopening its name."""

    previous = source_stream.tell()
    try:
        verification_before = verify_production_release_archive_stream(
            source_stream
        )
    except ProductionReleaseVerificationError as exc:
        raise ProductionReleasePrivacyError(
            "Production archive verification failed before privacy scan"
        ) from exc
    policy_path = _require_safe_policy_location(policy_path)
    policy = _load_policy(policy_path)
    try:
        findings: set[PrivacyFinding] = set()
        source_stream.seek(0)
        with zipfile.ZipFile(source_stream) as archive:
            inspected = inspect_zip_members(archive, ArchiveLimits())
            wrappers = {member.path.parts[0] for member in inspected}
            if len(wrappers) != 1:
                raise ProductionReleasePrivacyError(
                    "Production archive wrapper is invalid"
                )
            infos = {
                info.filename: info for info in archive.infolist()
            }
            for member in inspected:
                if (
                    stat.S_IFMT(member.unix_mode) != stat.S_IFREG
                    or len(member.path.parts) < 2
                ):
                    raise ProductionReleasePrivacyError(
                        "Production archive member is unsafe"
                    )
                relative = safe_posix_member_path(
                    "/".join(member.path.parts[1:])
                ).as_posix()
                with archive.open(
                    infos[member.path.as_posix()],
                    "r",
                ) as member_stream:
                    findings.update(
                        _scan_stream(
                            relative,
                            member_stream,
                            policy,
                            expected_bytes=member.byte_length,
                        )
                    )
    except ProductionReleasePrivacyError:
        raise
    except (
        OSError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        zipfile.BadZipFile,
        ReleaseArchiveError,
    ) as exc:
        raise ProductionReleasePrivacyError(
            "Production archive cannot be privacy scanned"
        ) from exc
    try:
        verification_after = verify_production_release_archive_stream(
            source_stream
        )
    except ProductionReleaseVerificationError as exc:
        raise ProductionReleasePrivacyError(
            "Production archive verification failed after privacy scan"
        ) from exc
    finally:
        source_stream.seek(previous)
    _require_same_verification(verification_before, verification_after)
    ordered = tuple(sorted(findings))
    return ProductionPrivacyReport(
        schema=PRIVACY_REPORT_SCHEMA,
        valid=not ordered,
        package_content_id=verification_after.package_content_id,
        finding_count=len(ordered),
        findings=ordered,
        scene_trust_effect="none",
    )


def _audit_verified_archive(
    source: Path,
    policy_path: Path,
) -> ProductionPrivacyReport:
    try:
        path_before = source.lstat()
        with source.open("rb") as source_stream:
            descriptor_before = os.fstat(source_stream.fileno())
            if _signature(path_before) != _signature(descriptor_before):
                raise ProductionReleasePrivacyError(
                    "Production archive changed before privacy scan"
                )
            result = audit_production_release_privacy_stream(
                source_stream,
                policy_path,
            )
            descriptor_after = os.fstat(source_stream.fileno())
        path_after = source.lstat()
    except ProductionReleasePrivacyError:
        raise
    except OSError as exc:
        raise ProductionReleasePrivacyError(
            "Production archive cannot be privacy scanned"
        ) from exc
    expected = _signature(path_before)
    if (
        expected != _signature(descriptor_after)
        or expected != _signature(path_after)
    ):
        raise ProductionReleasePrivacyError(
            "Production archive changed during privacy scan"
        )
    return result


def audit_production_release_privacy(
    target: str | Path,
    policy: str | Path,
) -> ProductionPrivacyReport:
    """Audit a verified Production tree or ZIP without promoting scene trust."""

    source = Path(target).expanduser().absolute()
    policy_path = Path(policy)
    try:
        redirected = first_linklike_path(Path(source.anchor), source)
        source_stat = source.lstat()
    except (OSError, ValueError) as exc:
        raise ProductionReleasePrivacyError(
            "Production privacy target is unavailable or unsafe"
        ) from exc
    if redirected is not None or _is_linklike(source, observed=source_stat):
        raise ProductionReleasePrivacyError(
            "Production privacy target is unavailable or unsafe"
        )
    if stat.S_ISDIR(source_stat.st_mode):
        return _audit_verified_tree(source, policy_path)
    if not stat.S_ISREG(source_stat.st_mode):
        raise ProductionReleasePrivacyError(
            "Production privacy target is unavailable or unsafe"
        )
    return _audit_verified_archive(source, policy_path)
