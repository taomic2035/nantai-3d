"""Independent fail-closed verification for Production runtime releases."""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
    ProductionReleaseContractError,
    load_production_receipt_bytes,
    load_public_evidence_bytes,
)
from pipeline.production_release_fs import (
    BoundDirectory,
    ProductionReleaseFSError,
    ProductionReleaseMutationError,
    open_bound_directory,
    require_linux_mutation_support,
)
from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    inspect_zip_members,
    portable_path_identity,
    preflight_zip_central_directory,
    safe_posix_member_path,
    stable_regular_file_digest,
)

PRODUCTION_ARCHIVE_LIMITS = ArchiveLimits()
_CONTRACT_MAXIMUM_BYTES = 16 * 1024 * 1024


class ProductionReleaseVerificationError(ValueError):
    """Raised when a Production release cannot be independently verified."""

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


def _require_path_budget(
    relative: str,
    *,
    limits: ArchiveLimits,
) -> None:
    member = safe_posix_member_path(relative)
    if len(relative.encode("utf-8")) > limits.maximum_path_bytes:
        _verification_error(
            f"release path exceeds its maximum: {relative}"
        )
    if len(member.parts) > limits.maximum_path_components:
        _verification_error(
            f"release path depth exceeds its maximum: {relative}"
        )


def _release_files(root: Path, *, limits: ArchiveLimits) -> set[str]:
    files: set[str] = set()
    folded: set[str] = set()
    total_bytes = 0
    observed_members = 0
    stack = []
    try:
        try:
            stack.append((root, os.scandir(root)))
        except OSError as exc:
            _verification_error(
                "release directory is unavailable",
                exc,
            )
        while stack:
            current_path, iterator = stack[-1]
            try:
                entry = next(iterator)
            except StopIteration:
                iterator.close()
                stack.pop()
                continue
            except OSError as exc:
                _verification_error(
                    "release directory is unavailable",
                    exc,
                )
            observed_members += 1
            if observed_members > limits.maximum_members:
                _verification_error(
                    "release tree member count exceeds its maximum"
                )
            candidate = current_path / entry.name
            relative = candidate.relative_to(root).as_posix()
            _require_path_budget(relative, limits=limits)
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
            if stat.S_ISDIR(observed.st_mode):
                try:
                    stack.append((candidate, os.scandir(candidate)))
                except OSError as exc:
                    _verification_error(
                        f"release path is unavailable: {relative}",
                        exc,
                    )
                continue
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
            if observed.st_size > limits.maximum_member_bytes:
                _verification_error(
                    f"release tree member exceeds its maximum: {relative}"
                )
            total_bytes += observed.st_size
            if total_bytes > limits.maximum_total_bytes:
                _verification_error(
                    "release tree total size exceeds its maximum"
                )
    finally:
        for _path, iterator in reversed(stack):
            iterator.close()
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


class _ReleaseReader(Protocol):
    def files(self) -> set[str]: ...

    def payload(self, relative: str, *, maximum_bytes: int) -> bytes: ...

    def digest(
        self,
        relative: str,
        *,
        maximum_bytes: int | None = None,
    ) -> tuple[str, int]: ...


class _TreeReader:
    def __init__(self, root: Path, *, limits: ArchiveLimits) -> None:
        self.root = root
        self.limits = limits

    def files(self) -> set[str]:
        return _release_files(self.root, limits=self.limits)

    def payload(self, relative: str, *, maximum_bytes: int) -> bytes:
        return _stable_payload(
            _artifact_path(self.root, relative),
            maximum_bytes=maximum_bytes,
        )

    def digest(
        self,
        relative: str,
        *,
        maximum_bytes: int | None = None,
    ) -> tuple[str, int]:
        try:
            observed = stable_regular_file_digest(
                _artifact_path(self.root, relative),
                maximum_bytes=maximum_bytes,
            )
        except ReleaseArchiveError as exc:
            raise ProductionReleaseVerificationError(
                f"protected artifact verification failed: {relative}: {exc}"
            ) from exc
        return observed.sha256, observed.byte_length


class _ArchiveReader:
    def __init__(
        self,
        archive: zipfile.ZipFile,
        *,
        limits: ArchiveLimits,
    ) -> None:
        inspected = inspect_zip_members(archive, limits)
        wrappers = {member.path.parts[0] for member in inspected}
        if len(wrappers) != 1:
            _verification_error(
                "Production release archive must contain exactly one root"
            )
        self._archive = archive
        self._infos: dict[str, zipfile.ZipInfo] = {}
        self._sizes: dict[str, int] = {}
        for member in inspected:
            mode = stat.S_IFMT(member.unix_mode)
            if mode == stat.S_IFDIR:
                _verification_error(
                    "Production release archive directory entries are forbidden"
                )
            if mode != stat.S_IFREG:
                _verification_error(
                    "Production release archive members must be regular"
                )
            if len(member.path.parts) < 2:
                _verification_error(
                    "Production release archive member lacks wrapper root"
                )
            relative = safe_posix_member_path(
                PurePosixPath(*member.path.parts[1:]).as_posix()
            ).as_posix()
            self._infos[relative] = archive.getinfo(member.path.as_posix())
            self._sizes[relative] = member.byte_length

    def files(self) -> set[str]:
        return set(self._infos)

    def _stream(self, relative: str) -> BinaryIO:
        try:
            info = self._infos[relative]
        except KeyError as exc:
            raise ProductionReleaseVerificationError(
                f"missing protected artifact: {relative}"
            ) from exc
        return self._archive.open(info, "r")

    def payload(self, relative: str, *, maximum_bytes: int) -> bytes:
        declared = self._sizes.get(relative)
        if declared is None:
            raise ProductionReleaseVerificationError(
                f"missing protected artifact: {relative}"
            )
        if declared > maximum_bytes:
            raise ProductionReleaseVerificationError(
                f"release file exceeds its maximum: {relative}"
            )
        try:
            with self._stream(relative) as stream:
                payload = stream.read(maximum_bytes + 1)
                extra = stream.read(1)
        except (
            OSError,
            EOFError,
            RuntimeError,
            NotImplementedError,
            zipfile.BadZipFile,
        ) as exc:
            raise ProductionReleaseVerificationError(
                f"release file cannot be read: {relative}"
            ) from exc
        if len(payload) != declared or len(payload) > maximum_bytes or extra:
            raise ProductionReleaseVerificationError(
                f"release file changed during read: {relative}"
            )
        return payload

    def digest(
        self,
        relative: str,
        *,
        maximum_bytes: int | None = None,
    ) -> tuple[str, int]:
        declared = self._sizes.get(relative)
        if declared is None:
            raise ProductionReleaseVerificationError(
                f"missing protected artifact: {relative}"
            )
        if maximum_bytes is not None and declared > maximum_bytes:
            raise ProductionReleaseVerificationError(
                f"protected artifact verification failed: {relative}: "
                "file exceeds its maximum"
            )
        digest = hashlib.sha256()
        observed_bytes = 0
        try:
            with self._stream(relative) as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    observed_bytes += len(chunk)
                    if observed_bytes > declared:
                        raise ProductionReleaseVerificationError(
                            "Production archive member expanded beyond "
                            f"declared length: {relative}"
                        )
                    digest.update(chunk)
        except ProductionReleaseVerificationError:
            raise
        except (
            OSError,
            EOFError,
            RuntimeError,
            NotImplementedError,
            zipfile.BadZipFile,
        ) as exc:
            raise ProductionReleaseVerificationError(
                f"protected artifact verification failed: {relative}"
            ) from exc
        if observed_bytes != declared:
            raise ProductionReleaseVerificationError(
                f"Production archive member length disagrees: {relative}"
            )
        return digest.hexdigest(), observed_bytes


def _verify_reader(
    reader: _ReleaseReader,
    *,
    limits: ArchiveLimits,
) -> ProductionReleaseVerification:
    """Verify one immutable release reader without promoting scene trust."""

    try:
        receipt_payload = reader.payload(
            PRODUCTION_RELEASE_NAME,
            maximum_bytes=_CONTRACT_MAXIMUM_BYTES,
        )
        receipt = load_production_receipt_bytes(receipt_payload)
    except ProductionReleaseVerificationError:
        raise
    except (ProductionReleaseContractError, ReleaseArchiveError) as exc:
        raise ProductionReleaseVerificationError(
            f"Production receipt verification failed: {exc}"
        ) from exc

    artifacts = tuple(receipt["artifacts"])
    if len(artifacts) + 2 > limits.maximum_members:
        raise ProductionReleaseVerificationError(
            "Production receipt member count exceeds its maximum"
        )
    if len(receipt_payload) > limits.maximum_member_bytes:
        raise ProductionReleaseVerificationError(
            "Production receipt exceeds its member maximum"
        )
    declared_total = len(receipt_payload)
    for artifact in artifacts:
        relative = str(artifact["path"])
        _require_path_budget(relative, limits=limits)
        byte_length = int(artifact["bytes"])
        if byte_length > limits.maximum_member_bytes:
            raise ProductionReleaseVerificationError(
                f"Production receipt member exceeds its maximum: {relative}"
            )
        declared_total += byte_length
    checksum_bytes = _expected_checksum_bytes(receipt, receipt_payload)
    if len(checksum_bytes) > limits.maximum_member_bytes:
        raise ProductionReleaseVerificationError(
            "Production checksum file exceeds its member maximum"
        )
    declared_total += len(checksum_bytes)
    if declared_total > limits.maximum_total_bytes:
        raise ProductionReleaseVerificationError(
            "Production receipt total size exceeds its maximum"
        )

    declared_paths = {
        str(artifact["path"]) for artifact in receipt["artifacts"]
    }
    allowed_paths = declared_paths | {
        PRODUCTION_RELEASE_NAME,
        CHECKSUMS_NAME,
    }
    try:
        observed_paths = reader.files()
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
        digest, byte_length = reader.digest(
            relative,
            maximum_bytes=int(artifact["bytes"]),
        )
        if (
            byte_length != artifact["bytes"]
            or digest != artifact["sha256"]
        ):
            raise ProductionReleaseVerificationError(
                f"changed protected artifact: {relative}"
            )
        artifact_by_path[relative] = artifact
        total_bytes += byte_length

    checksum_payload = reader.payload(
        CHECKSUMS_NAME,
        maximum_bytes=_CONTRACT_MAXIMUM_BYTES,
    )
    if checksum_payload != checksum_bytes:
        raise ProductionReleaseVerificationError(
            "Production release checksum file is changed or noncanonical"
        )

    acceptance = receipt["acceptance"]
    evidence_relative = str(acceptance["public_evidence_path"])
    evidence_artifact = artifact_by_path[evidence_relative]
    try:
        evidence_payload = reader.payload(
            evidence_relative,
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
    manifest_sha, _manifest_bytes = reader.digest(manifest_relative)
    if manifest_sha != evidence["scene"]["manifest_sha256"]:
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


def verify_production_release_tree(
    root: str | Path,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> ProductionReleaseVerification:
    """Verify one extracted Production runtime without promoting scene trust."""

    project_root = Path(root).absolute()
    try:
        redirected = first_linklike_path(
            Path(project_root.anchor),
            project_root,
        )
        root_stat = project_root.lstat()
        unsafe_root = _is_linklike(project_root, observed=root_stat)
    except OSError as exc:
        raise ProductionReleaseVerificationError(
            "Production release tree is missing or unsafe"
        ) from exc
    if (
        redirected is not None
        or unsafe_root
        or not stat.S_ISDIR(root_stat.st_mode)
    ):
        raise ProductionReleaseVerificationError(
            "Production release tree is missing or unsafe"
        )
    return _verify_reader(
        _TreeReader(project_root, limits=limits),
        limits=limits,
    )


def extract_production_release_archive(
    archive_path: str | Path,
    destination: str | Path,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> Path:
    """Append-only extract on a private Linux builder.

    Once the destination namespace is created, every failure retains it for
    audit.  This function never removes or replaces a path.
    """

    try:
        require_linux_mutation_support()
    except ProductionReleaseFSError as exc:
        raise ProductionReleaseVerificationError(str(exc)) from exc

    source = Path(archive_path).absolute()
    target = Path(destination).absolute()
    if target.parent == target:
        raise ProductionReleaseVerificationError(
            "Production extraction destination is unsafe"
        )
    try:
        redirected_source = first_linklike_path(Path(source.anchor), source)
        source_stat = source.lstat()
    except (OSError, ValueError) as exc:
        raise ProductionReleaseVerificationError(
            "Production release archive is missing or unsafe"
        ) from exc
    if (
        redirected_source is not None
        or _is_linklike(source, observed=source_stat)
        or not stat.S_ISREG(source_stat.st_mode)
    ):
        raise ProductionReleaseVerificationError(
            "Production release archive is missing or unsafe"
        )

    retained: list[str] = []

    def signature(
        value: os.stat_result,
    ) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
        )

    try:
        with source.open("rb") as source_stream:
            descriptor_before = os.fstat(source_stream.fileno())
            preflight_zip_central_directory(
                source_stream,
                limits=limits,
            )
            with zipfile.ZipFile(source_stream) as archive:
                reader = _ArchiveReader(archive, limits=limits)
                source_verification = _verify_reader(
                    reader,
                    limits=limits,
                )
                descriptor_after_preflight = os.fstat(
                    source_stream.fileno()
                )
                source_after_preflight = source.lstat()
                if (
                    signature(source_stat) != signature(descriptor_before)
                    or signature(source_stat)
                    != signature(descriptor_after_preflight)
                    or signature(source_stat)
                    != signature(source_after_preflight)
                ):
                    raise ProductionReleaseVerificationError(
                        "Production archive changed during preflight"
                    )
                members = tuple(
                    (
                        safe_posix_member_path(relative),
                        reader._infos[relative],
                        reader._sizes[relative],
                    )
                    for relative in sorted(reader.files())
                )
                extraction_nodes: set[str] = set()
                for relative, _info, _byte_length in members:
                    for depth in range(1, len(relative.parts) + 1):
                        node = "/".join(relative.parts[:depth])
                        if node in extraction_nodes:
                            continue
                        if len(extraction_nodes) >= limits.maximum_members:
                            raise ProductionReleaseVerificationError(
                                "Production extraction node count exceeds "
                                "its maximum"
                            )
                        extraction_nodes.add(node)
                with open_bound_directory(target.parent) as parent:
                    try:
                        root = parent.create_directory(
                            target.name,
                            mode=0o700,
                        )
                    except FileExistsError as exc:
                        raise ProductionReleaseVerificationError(
                            "Production extraction destination already exists"
                        ) from exc
                    retained.append(target.name)
                    with root:
                        directory_parts: list[str] = []
                        directory_stack: list[BoundDirectory] = []
                        try:
                            for relative, info, byte_length in members:
                                wanted = list(relative.parts[:-1])
                                common = 0
                                while (
                                    common < len(directory_parts)
                                    and common < len(wanted)
                                    and directory_parts[common] == wanted[common]
                                ):
                                    common += 1
                                while len(directory_stack) > common:
                                    directory_stack.pop().close()
                                    directory_parts.pop()
                                current = (
                                    directory_stack[-1]
                                    if directory_stack
                                    else root
                                )
                                for component in wanted[common:]:
                                    child = current.create_directory(
                                        component,
                                        mode=0o700,
                                    )
                                    directory_stack.append(child)
                                    directory_parts.append(component)
                                    retained.append(
                                        f"{target.name}/"
                                        + "/".join(directory_parts)
                                    )
                                    current = child
                                with archive.open(info, "r") as member_stream:
                                    with current.create_file(
                                        relative.name,
                                        mode=0o600,
                                    ) as output:
                                        observed_sha, observed_bytes = (
                                            output.copy_from(
                                                member_stream,
                                                expected_bytes=byte_length,
                                            )
                                        )
                                        output.finish()
                                        written_sha, written_bytes = (
                                            output.digest()
                                        )
                                        if (
                                            observed_sha != written_sha
                                            or observed_bytes != written_bytes
                                        ):
                                            raise ProductionReleaseMutationError(
                                                "Production extracted member "
                                                "changed; retained",
                                                published=(
                                                    relative.as_posix(),
                                                ),
                                                retained=(
                                                    relative.as_posix(),
                                                ),
                                            )
                                current.fsync()
                                retained.append(
                                    f"{target.name}/{relative.as_posix()}"
                                )
                        finally:
                            for opened_directory in reversed(directory_stack):
                                opened_directory.close()
                        root.fsync()
                        parent.fsync()
                        parent.verify_lexical_identity()
                        parent.verify_child_identity(target.name, root)
                        root.verify_lexical_identity()
                        tree_verification = verify_production_release_tree(
                            target,
                            limits=limits,
                        )
                        parent.verify_lexical_identity()
                        parent.verify_child_identity(target.name, root)
                        root.verify_lexical_identity()
                        if tree_verification != source_verification:
                            state = tuple(retained)
                            raise ProductionReleaseVerificationError(
                                "Production source and extracted tree "
                                "verification disagree; "
                                f"published={state}; retained={state}",
                                published=state,
                                retained=state,
                            )
            descriptor_after = os.fstat(source_stream.fileno())
        source_after = source.lstat()
        if (
            signature(source_stat) != signature(descriptor_before)
            or signature(source_stat) != signature(descriptor_after)
            or signature(source_stat) != signature(source_after)
        ):
            raise ProductionReleaseVerificationError(
                "Production archive changed during extraction; "
                f"published={tuple(retained)}; retained={tuple(retained)}",
                published=tuple(retained),
                retained=tuple(retained),
            )
        return target
    except ProductionReleaseVerificationError as exc:
        if not retained or (exc.published and exc.retained):
            raise
        state = tuple(retained)
        raise ProductionReleaseVerificationError(
            f"{exc}; published={state}; retained={state}",
            published=state,
            retained=state,
        ) from exc
    except ProductionReleaseMutationError as exc:
        published = tuple(retained) + exc.published
        residue = tuple(retained) + exc.retained
        raise ProductionReleaseVerificationError(
            f"Production archive extraction failed: {exc}; "
            f"published={published}; retained={residue}",
            published=published,
            retained=residue,
        ) from exc
    except (
        ReleaseArchiveError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        ProductionReleaseFSError,
        OSError,
        EOFError,
    ) as exc:
        state = tuple(retained)
        raise ProductionReleaseVerificationError(
            f"Production archive verification failed: {exc}; "
            f"published={state}; retained={state}",
            published=state,
            retained=state,
        ) from exc


def verify_production_release_archive(
    path: str | Path,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> ProductionReleaseVerification:
    """Stream and independently verify one Production runtime ZIP."""

    source = Path(path).absolute()
    try:
        redirected = first_linklike_path(Path(source.anchor), source)
        before = source.lstat()
    except (OSError, ValueError) as exc:
        raise ProductionReleaseVerificationError(
            "Production release archive is missing or unsafe"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(source, observed=before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise ProductionReleaseVerificationError(
            "Production release archive is missing or unsafe"
        )

    def signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
        )

    try:
        with source.open("rb") as stream:
            descriptor_before = os.fstat(stream.fileno())
            if signature(before) != signature(descriptor_before):
                raise ProductionReleaseVerificationError(
                    "Production release archive changed before verification"
                )
            preflight_zip_central_directory(stream, limits=limits)
            with zipfile.ZipFile(stream) as archive:
                result = _verify_reader(
                    _ArchiveReader(archive, limits=limits),
                    limits=limits,
                )
            descriptor_after = os.fstat(stream.fileno())
        after = source.lstat()
    except ProductionReleaseVerificationError:
        raise
    except (
        ReleaseArchiveError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        OSError,
        EOFError,
    ) as exc:
        raise ProductionReleaseVerificationError(
            f"Production archive verification failed: {exc}"
        ) from exc
    if (
        signature(before) != signature(descriptor_after)
        or signature(before) != signature(after)
    ):
        raise ProductionReleaseVerificationError(
            "Production release archive changed during verification"
        )
    return result


def verify_production_release_archive_stream(
    stream: BinaryIO,
    *,
    limits: ArchiveLimits = PRODUCTION_ARCHIVE_LIMITS,
) -> ProductionReleaseVerification:
    """Verify one already-held archive inode without reopening its name."""

    try:
        previous = stream.tell()
        stream.seek(0)
        preflight_zip_central_directory(stream, limits=limits)
        with zipfile.ZipFile(stream) as archive:
            result = _verify_reader(
                _ArchiveReader(archive, limits=limits),
                limits=limits,
            )
        stream.seek(previous)
        return result
    except ProductionReleaseVerificationError:
        raise
    except (
        ReleaseArchiveError,
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        OSError,
        EOFError,
    ) as exc:
        raise ProductionReleaseVerificationError(
            f"Production archive verification failed: {exc}"
        ) from exc
