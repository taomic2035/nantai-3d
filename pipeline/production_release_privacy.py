"""Fail-closed privacy audit for verified Production runtime releases."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from pipeline.durable_io import (
    DurableIOError,
    _is_linklike,
    bind_directory,
    bound_temporary_directory,
    first_linklike_path,
)
from pipeline.production_release_verifier import (
    ProductionReleaseVerification,
    ProductionReleaseVerificationError,
    extract_production_release_archive,
    verify_production_release_tree,
)
from pipeline.release_archive import (
    ReleaseArchiveError,
    canonical_json_bytes,
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
    """Durably publish one canonical report without replacing prior evidence."""

    destination = Path(output).absolute()
    try:
        redirected = first_linklike_path(Path(destination.anchor), destination)
    except (OSError, ValueError) as exc:
        raise ProductionReleasePrivacyError(
            "privacy report destination is unsafe"
        ) from exc
    if destination.exists() or redirected == destination:
        raise ProductionReleasePrivacyError(
            "privacy report destination already exists"
        )
    if redirected is not None or not destination.parent.is_dir():
        raise ProductionReleasePrivacyError(
            "privacy report parent directory is missing"
        )
    try:
        parent = bind_directory(destination.parent)
    except (DurableIOError, OSError) as exc:
        raise ProductionReleasePrivacyError(
            "privacy report parent directory is unsafe"
        ) from exc
    temporary_name = f".{destination.name}.{uuid.uuid4().hex}.tmp"
    candidate = None
    published = False
    try:
        candidate = parent.create_file(temporary_name)
        candidate.stream.write(privacy_report_bytes(report))
        candidate.flush()
        candidate.publish_noreplace(parent, destination.name)
        published = True
    except (DurableIOError, FileExistsError, OSError) as exc:
        raise ProductionReleasePrivacyError(
            "privacy report durable publication failed"
        ) from exc
    finally:
        if candidate is not None:
            candidate.close()
        if not published:
            try:
                parent.unlink_file(temporary_name, missing_ok=True)
            except OSError:
                pass
        parent.close()


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


def _release_files(root: Path) -> tuple[tuple[str, Path], ...]:
    observed: list[tuple[str, Path]] = []
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in tuple(directories):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
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

    overlap_bytes = max(
        _BUILTIN_OVERLAP_BYTES,
        max(len(needle) for needle in policy.needles) - 1,
    )
    carry = b""
    findings: set[PrivacyFinding] = set()
    observed_bytes = 0
    try:
        with path.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            if _signature(path_before) != _signature(descriptor_before):
                raise ProductionReleasePrivacyError(
                    f"release file changed before privacy scan: {relative}"
                )
            while True:
                chunk = _read_scan_chunk(stream)
                if not chunk:
                    break
                if len(chunk) > PRIVACY_SCAN_CHUNK_BYTES:
                    raise ProductionReleasePrivacyError(
                        f"privacy scan chunk exceeded its bound: {relative}"
                    )
                observed_bytes += len(chunk)
                window = carry + chunk
                findings.update(
                    PrivacyFinding(category=category, path=relative)
                    for category in _window_categories(window, policy)
                )
                carry = window[-overlap_bytes:]
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
        or observed_bytes != path_before.st_size
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
    with bound_temporary_directory(
        prefix="nantai-production-privacy-"
    ) as temporary:
        root = temporary.path / "runtime"
        try:
            extract_production_release_archive(
                source,
                root,
                _destination_parent=temporary,
            )
        except ProductionReleaseVerificationError as exc:
            raise ProductionReleasePrivacyError(
                "Production archive verification failed before privacy scan"
            ) from exc
        return _audit_verified_tree(root, policy_path)
