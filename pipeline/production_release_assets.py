"""Stage the four public assets for one accepted Production release."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.production_release_builder import (
    ProductionReleaseBuilderError,
    build_production_release_archive,
    resolve_production_release_source_identity,
)
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
    ProductionReleaseContractError,
    load_production_receipt_bytes,
)
from pipeline.production_release_fs import (
    BoundDirectory,
    ProductionReleaseFSError,
    ProductionReleaseMutationError,
    close_bound_capabilities_best_effort,
    open_bound_directory,
    require_linux_mutation_support,
)
from pipeline.production_release_privacy import (
    ProductionReleasePrivacyError,
    audit_production_release_privacy_stream,
)
from pipeline.production_release_verifier import (
    ProductionReleaseVerificationError,
    verify_production_release_archive,
    verify_production_release_archive_stream,
)
from pipeline.release_archive import (
    ArchiveLimits,
    ReleaseArchiveError,
    preflight_zip_central_directory,
    stable_regular_file_digest,
)

_MAXIMUM_PUBLIC_CONTRACT_BYTES = 16 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


class ProductionReleaseAssetsError(ValueError):
    """A final Production public-asset bundle cannot be trusted."""

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
class ProductionReleaseAssets:
    output_dir: Path
    archive_path: Path
    archive_sha256: str
    receipt_path: Path
    checksums_path: Path
    package_content_id: str
    privacy_valid: bool
    scene_trust_effect: str
    retained_private_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ProductionReleaseAssetsVerification:
    valid: bool
    bundle_dir: Path
    archive_path: Path
    archive_sha256: str
    version: str
    package_content_id: str
    scene_trust_effect: str


def _signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _stable_regular_files_equal(left: Path, right: Path) -> bool:
    try:
        left_path_before = left.lstat()
        right_path_before = right.lstat()
        if (
            _is_linklike(left, observed=left_path_before)
            or not stat.S_ISREG(left_path_before.st_mode)
            or _is_linklike(right, observed=right_path_before)
            or not stat.S_ISREG(right_path_before.st_mode)
        ):
            raise ProductionReleaseAssetsError(
                "Production acceptance byte comparison requires regular "
                "non-link files"
            )
        if left_path_before.st_size != right_path_before.st_size:
            return False

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        try:
            left_descriptor = os.open(left, flags)
        except OSError as exc:
            raise ProductionReleaseAssetsError(
                "Production acceptance byte comparison cannot be read"
            ) from exc
        try:
            right_descriptor = os.open(right, flags)
        except OSError as exc:
            try:
                os.close(left_descriptor)
            except OSError:
                pass
            raise ProductionReleaseAssetsError(
                "Production acceptance byte comparison cannot be read"
            ) from exc
        try:
            left_stream = os.fdopen(left_descriptor, "rb", buffering=0)
        except OSError as exc:
            try:
                os.close(left_descriptor)
            except OSError:
                pass
            try:
                os.close(right_descriptor)
            except OSError:
                pass
            raise ProductionReleaseAssetsError(
                "Production acceptance byte comparison cannot be read"
            ) from exc
        try:
            right_stream = os.fdopen(right_descriptor, "rb", buffering=0)
        except OSError as exc:
            try:
                os.close(right_descriptor)
            except OSError:
                pass
            try:
                left_stream.close()
            except OSError:
                pass
            raise ProductionReleaseAssetsError(
                "Production acceptance byte comparison cannot be read"
            ) from exc

        equal = True
        try:
            with left_stream, right_stream:
                left_descriptor_before = os.fstat(left_stream.fileno())
                right_descriptor_before = os.fstat(right_stream.fileno())
                while True:
                    left_chunk = left_stream.read(_COPY_CHUNK_BYTES)
                    right_chunk = right_stream.read(_COPY_CHUNK_BYTES)
                    if left_chunk != right_chunk:
                        equal = False
                    if not left_chunk and not right_chunk:
                        break
                left_descriptor_after = os.fstat(left_stream.fileno())
                right_descriptor_after = os.fstat(right_stream.fileno())
        except OSError as exc:
            raise ProductionReleaseAssetsError(
                "Production acceptance byte comparison cannot be read"
            ) from exc
        left_path_after = left.lstat()
        right_path_after = right.lstat()
    except ProductionReleaseAssetsError:
        raise
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production acceptance byte comparison failed"
        ) from exc

    left_expected = _signature(left_path_before)
    right_expected = _signature(right_path_before)
    if (
        left_expected != _signature(left_descriptor_before)
        or left_expected != _signature(left_descriptor_after)
        or left_expected != _signature(left_path_after)
        or right_expected != _signature(right_descriptor_before)
        or right_expected != _signature(right_descriptor_after)
        or right_expected != _signature(right_path_after)
    ):
        raise ProductionReleaseAssetsError(
            "Production acceptance byte comparison trust changed during read"
        )
    return equal


def _stable_contract_bytes(path: Path) -> bytes:
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production public contract is unavailable"
        ) from exc
    if (
        _is_linklike(path, observed=path_before)
        or not stat.S_ISREG(path_before.st_mode)
        or path_before.st_size > _MAXIMUM_PUBLIC_CONTRACT_BYTES
    ):
        raise ProductionReleaseAssetsError(
            "Production public contract is unsafe"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production public contract cannot be read"
        ) from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ProductionReleaseAssetsError(
            "Production public contract cannot be read"
        ) from exc
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            payload = stream.read(_MAXIMUM_PUBLIC_CONTRACT_BYTES + 1)
            descriptor_after = os.fstat(stream.fileno())
        path_after = path.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production public contract cannot be read"
        ) from exc
    expected = _signature(path_before)
    if (
        len(payload) > _MAXIMUM_PUBLIC_CONTRACT_BYTES
        or len(payload) != path_before.st_size
        or expected != _signature(descriptor_before)
        or expected != _signature(descriptor_after)
        or expected != _signature(path_after)
    ):
        raise ProductionReleaseAssetsError(
            "Production public contract changed during read"
        )
    return payload


def _archive_contract_payloads_stream(
    source: BinaryIO,
) -> tuple[bytes, bytes]:
    previous = source.tell()
    try:
        source.seek(0)
        preflight_zip_central_directory(
            source,
            limits=ArchiveLimits(),
        )
        with zipfile.ZipFile(source) as archive:
            names = tuple(info.filename for info in archive.infolist())

            def read_contract(name: str) -> bytes:
                matches = [
                    candidate
                    for candidate in names
                    if candidate.endswith(f"/{name}")
                ]
                if len(matches) != 1:
                    raise ProductionReleaseAssetsError(
                        "Production archive contract is missing or ambiguous"
                    )
                info = archive.getinfo(matches[0])
                if info.file_size > _MAXIMUM_PUBLIC_CONTRACT_BYTES:
                    raise ProductionReleaseAssetsError(
                        "Production archive contract exceeds its maximum"
                    )
                with archive.open(info, "r") as stream:
                    payload = stream.read(
                        _MAXIMUM_PUBLIC_CONTRACT_BYTES + 1
                    )
                    extra = stream.read(1)
                if len(payload) != info.file_size or extra:
                    raise ProductionReleaseAssetsError(
                        "Production archive contract changed during read"
                    )
                return payload

            receipt = read_contract(PRODUCTION_RELEASE_NAME)
            checksums = read_contract(CHECKSUMS_NAME)
    except ProductionReleaseAssetsError:
        raise
    except (
        OSError,
        EOFError,
        RuntimeError,
        zipfile.BadZipFile,
        ReleaseArchiveError,
    ) as exc:
        raise ProductionReleaseAssetsError(
            "Production archive contracts cannot be read"
        ) from exc
    finally:
        source.seek(previous)
    return receipt, checksums


def _archive_contract_payloads(path: Path) -> tuple[bytes, bytes]:
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production archive contracts cannot be read"
        ) from exc
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production archive contracts cannot be read"
        ) from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ProductionReleaseAssetsError(
            "Production archive contracts cannot be read"
        ) from exc
    try:
        with stream as source:
            descriptor_before = os.fstat(source.fileno())
            if _signature(path_before) != _signature(descriptor_before):
                raise ProductionReleaseAssetsError(
                    "Production archive changed before contract read"
                )
            result = _archive_contract_payloads_stream(source)
            descriptor_after = os.fstat(source.fileno())
        path_after = path.lstat()
    except ProductionReleaseAssetsError:
        raise
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production archive contracts cannot be read"
        ) from exc
    expected = _signature(path_before)
    if (
        expected != _signature(descriptor_after)
        or expected != _signature(path_after)
    ):
        raise ProductionReleaseAssetsError(
            "Production archive changed during contract read"
        )
    return result


def _public_bundle_files(root: Path) -> dict[str, Path]:
    try:
        redirected = first_linklike_path(Path(root.absolute().anchor), root)
        root_stat = root.lstat()
    except (OSError, ValueError) as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset directory is missing or unsafe"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(root, observed=root_stat)
        or not stat.S_ISDIR(root_stat.st_mode)
    ):
        raise ProductionReleaseAssetsError(
            "Production release asset directory is missing or unsafe"
        )
    observed: dict[str, Path] = {}
    iterator = None
    try:
        iterator = os.scandir(root)
        try:
            root_after_scan = root.lstat()
        except OSError as exc:
            raise ProductionReleaseAssetsError(
                "Production release asset directory cannot be read"
            ) from exc
        if (
            _is_linklike(root, observed=root_after_scan)
            or not stat.S_ISDIR(root_after_scan.st_mode)
            or root_stat.st_dev != root_after_scan.st_dev
            or root_stat.st_ino != root_after_scan.st_ino
        ):
            raise ProductionReleaseAssetsError(
                "Production release asset directory changed during scan"
            )
        for entry in iterator:
            candidate = root / entry.name
            try:
                current = candidate.lstat()
            except OSError as exc:
                raise ProductionReleaseAssetsError(
                    "Production release asset is unavailable"
                ) from exc
            if _is_linklike(candidate, observed=current) or not stat.S_ISREG(
                current.st_mode
            ):
                raise ProductionReleaseAssetsError(
                    "Production release assets must be regular non-link files"
                )
            observed[candidate.name] = candidate
    except ProductionReleaseAssetsError:
        raise
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset directory cannot be read"
        ) from exc
    finally:
        if iterator is not None:
            iterator.close()
    return observed


def verify_production_release_assets(
    bundle_dir: str | Path,
) -> ProductionReleaseAssetsVerification:
    """Verify one downloaded four-file Production Release bundle."""

    root = Path(bundle_dir).expanduser().absolute()
    try:
        observed = _public_bundle_files(root)
        if len(observed) != 4:
            raise ProductionReleaseAssetsError(
                "Production release asset directory must contain four files"
            )
        archive_names = sorted(
            name
            for name in observed
            if name.startswith("nantai-3d-v")
            and name.endswith("-runtime.zip")
        )
        if len(archive_names) != 1:
            raise ProductionReleaseAssetsError(
                "Production release asset archive name is missing or ambiguous"
            )
        archive_name = archive_names[0]
        expected_names = {
            archive_name,
            f"{archive_name}.sha256",
            PRODUCTION_RELEASE_NAME,
            CHECKSUMS_NAME,
        }
        if set(observed) != expected_names:
            raise ProductionReleaseAssetsError(
                "Production release asset filenames are not canonical"
            )
        archive = observed[archive_name]
        archive_digest = stable_regular_file_digest(archive)
        sidecar_payload = _stable_contract_bytes(
            observed[f"{archive_name}.sha256"]
        )
        expected_sidecar = (
            f"{archive_digest.sha256}  {archive_name}\n"
        ).encode("ascii")
        if sidecar_payload != expected_sidecar:
            raise ProductionReleaseAssetsError(
                "Production release archive sidecar disagrees"
            )
        receipt_payload = _stable_contract_bytes(
            observed[PRODUCTION_RELEASE_NAME]
        )
        checksums_payload = _stable_contract_bytes(
            observed[CHECKSUMS_NAME]
        )
        receipt = load_production_receipt_bytes(receipt_payload)
        verification = verify_production_release_archive(archive)
        archive_receipt, archive_checksums = _archive_contract_payloads(archive)
        if (
            archive_receipt != receipt_payload
            or archive_checksums != checksums_payload
        ):
            raise ProductionReleaseAssetsError(
                "standalone Production contracts disagree with the archive"
            )
        if (
            verification.fixture_kind is not None
            or verification.release_contract != "production-accepted"
        ):
            raise ProductionReleaseAssetsError(
                "modeled contract cannot be verified as a Production release"
            )
        expected_archive_name = (
            f"nantai-3d-{verification.version}-runtime.zip"
        )
        if (
            archive_name != expected_archive_name
            or receipt["version"] != verification.version
            or receipt["package"]["content_id"]
            != verification.package_content_id
        ):
            raise ProductionReleaseAssetsError(
                "Production release asset identities disagree"
            )
        if (
            _public_bundle_files(root).keys() != observed.keys()
            or stable_regular_file_digest(archive) != archive_digest
            or _stable_contract_bytes(
                observed[f"{archive_name}.sha256"]
            )
            != sidecar_payload
            or _stable_contract_bytes(
                observed[PRODUCTION_RELEASE_NAME]
            )
            != receipt_payload
            or _stable_contract_bytes(observed[CHECKSUMS_NAME])
            != checksums_payload
        ):
            raise ProductionReleaseAssetsError(
                "Production release assets changed during verification"
            )
        return ProductionReleaseAssetsVerification(
            valid=True,
            bundle_dir=root,
            archive_path=archive,
            archive_sha256=archive_digest.sha256,
            version=verification.version,
            package_content_id=verification.package_content_id,
            scene_trust_effect=verification.scene_trust_effect,
        )
    except ProductionReleaseAssetsError:
        raise
    except (
        OSError,
        ProductionReleaseContractError,
        ProductionReleaseVerificationError,
        ReleaseArchiveError,
    ) as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset verification failed"
        ) from exc


def stage_production_release_assets(
    *,
    repo_root: str | Path,
    acceptance_root: str | Path,
    version: str,
    archive_path: str | Path,
    privacy_policy_path: str | Path,
    output_dir: str | Path,
) -> ProductionReleaseAssets:
    """Append-only stage exactly four public assets on private Linux."""

    try:
        require_linux_mutation_support()
    except ProductionReleaseFSError as exc:
        raise ProductionReleaseAssetsError(str(exc)) from exc
    source = Path(archive_path).expanduser().absolute()
    policy = Path(privacy_policy_path).expanduser().absolute()
    acceptance = Path(acceptance_root).expanduser().absolute()
    repo = Path(repo_root).expanduser().absolute()
    output = Path(output_dir).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(source.anchor), source)
        source_before = source.lstat()
    except (OSError, ValueError) as exc:
        raise ProductionReleaseAssetsError(
            "Production candidate archive is unavailable"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(source, observed=source_before)
        or not stat.S_ISREG(source_before.st_mode)
    ):
        raise ProductionReleaseAssetsError(
            "Production candidate archive must be a regular non-link file"
        )
    parent: BoundDirectory | None = None
    rebuild_dir: BoundDirectory | None = None
    public_dir: BoundDirectory | None = None
    held_files = []
    public_names: list[str] = []
    private_names: list[str] = []
    body_failed = True
    try:
        parent = open_bound_directory(output.parent)
        if parent.entry_exists(output.name):
            raise ProductionReleaseAssetsError(
                "Production release asset output directory must be absent"
            )
        rebuild_name = f".{output.name}.{uuid.uuid4().hex}.rebuild"
        rebuild_dir = parent.create_directory(rebuild_name, mode=0o700)
        private_names.append(rebuild_name)
        snapshot_file = rebuild_dir.create_file(
            "candidate-snapshot.zip",
            mode=0o600,
        )
        held_files.append(snapshot_file)
        private_names.append(
            f"{rebuild_name}/candidate-snapshot.zip"
        )
        with source.open("rb") as source_stream:
            descriptor_before = os.fstat(source_stream.fileno())
            if _signature(source_before) != _signature(descriptor_before):
                raise ProductionReleaseAssetsError(
                    "Production candidate archive changed before staging"
                )
            copied_sha, archive_bytes = snapshot_file.copy_from(
                source_stream,
                expected_bytes=source_before.st_size,
            )
            descriptor_after = os.fstat(source_stream.fileno())
            if _signature(source_before) != _signature(descriptor_after):
                raise ProductionReleaseAssetsError(
                    "Production candidate archive changed during snapshot"
                )
        snapshot_file.finish()
        archive_sha256, snapshot_bytes = snapshot_file.digest()
        if (
            copied_sha != archive_sha256
            or archive_bytes != snapshot_bytes
            or archive_bytes != source_before.st_size
        ):
            raise ProductionReleaseAssetsError(
                "Production candidate snapshot identity disagrees"
            )

        verification = verify_production_release_archive_stream(
            snapshot_file.stream
        )
        privacy = audit_production_release_privacy_stream(
            snapshot_file.stream,
            policy,
        )
        receipt_payload, checksums_payload = _archive_contract_payloads_stream(
            snapshot_file.stream
        )
        if verification.version != version:
            raise ProductionReleaseAssetsError(
                "Production candidate version does not match requested version"
            )
        if (
            verification.fixture_kind is not None
            or verification.release_contract != "production-accepted"
        ):
            raise ProductionReleaseAssetsError(
                "modeled contract cannot be staged as a Production release"
            )
        if (
            not privacy.valid
            or privacy.finding_count != 0
            or privacy.package_content_id != verification.package_content_id
        ):
            raise ProductionReleaseAssetsError(
                "Production privacy audit failed"
            )
        receipt = load_production_receipt_bytes(receipt_payload)
        if (
            receipt["version"] != verification.version
            or receipt["package"]["content_id"]
            != verification.package_content_id
        ):
            raise ProductionReleaseAssetsError(
                "Production candidate identities disagree"
            )
        source_identity_before = resolve_production_release_source_identity(
            repo
        )
        rebuilt_path = rebuild_dir.path / "acceptance-rebuild.zip"
        rebuilt_result = build_production_release_archive(
            repo_root=repo,
            acceptance_root=acceptance,
            output_path=rebuilt_path,
            version=version,
            source_commit=source_identity_before.source_commit,
            tracked_files=source_identity_before.tracked_files,
            output_parent=rebuild_dir,
        )
        private_names.extend(
            (
                f"{rebuild_name}/{rebuilt_result.archive_path.name}",
                f"{rebuild_name}/"
                f"{rebuilt_result.archive_path.name}.sha256",
            )
        )
        if (
            resolve_production_release_source_identity(repo)
            != source_identity_before
        ):
            raise ProductionReleaseAssetsError(
                "Production source identity changed during acceptance rebuild"
            )
        if (
            rebuilt_result.archive_path != rebuilt_path
            or rebuilt_result.archive_sha256 != archive_sha256
        ):
            raise ProductionReleaseAssetsError(
                "Production candidate does not match acceptance rebuild"
            )

        try:
            public_dir = parent.create_directory(output.name, mode=0o755)
        except FileExistsError as exc:
            raise ProductionReleaseAssetsError(
                "Production release asset output directory must be absent"
            ) from exc
        public_names.append(output.name)
        archive_name = f"nantai-3d-{verification.version}-runtime.zip"
        archive_file = public_dir.create_file(archive_name, mode=0o644)
        held_files.append(archive_file)
        public_names.append(f"{output.name}/{archive_name}")
        snapshot_file.stream.seek(0)
        copied_sha, copied_bytes = archive_file.copy_from(
            snapshot_file.stream,
            expected_bytes=archive_bytes,
        )
        archive_file.finish()
        held_sha, held_bytes = archive_file.digest()
        if (
            copied_sha != archive_sha256
            or held_sha != archive_sha256
            or copied_bytes != archive_bytes
            or held_bytes != archive_bytes
        ):
            raise ProductionReleaseAssetsError(
                "Production archive changed during final staging"
            )
        sidecar_payload = (
            f"{archive_sha256}  {archive_name}\n"
        ).encode("ascii")
        public_payloads: dict[str, bytes] = {}
        public_files = {archive_name: archive_file}
        for name, payload in (
            (f"{archive_name}.sha256", sidecar_payload),
            (CHECKSUMS_NAME, checksums_payload),
            (PRODUCTION_RELEASE_NAME, receipt_payload),
        ):
            bound = public_dir.create_file(name, mode=0o644)
            held_files.append(bound)
            public_names.append(f"{output.name}/{name}")
            bound.write_all(payload)
            bound.finish()
            public_payloads[name] = payload
            public_files[name] = bound
            digest, byte_length = bound.digest()
            if (
                byte_length != len(payload)
                or digest != hashlib.sha256(payload).hexdigest()
            ):
                raise ProductionReleaseAssetsError(
                    f"Production public asset changed while held: {name}"
                )
        public_dir.fsync()
        parent.fsync()

        parent.verify_lexical_identity()
        parent.verify_child_identity(rebuild_name, rebuild_dir)
        parent.verify_child_identity(output.name, public_dir)
        rebuild_dir.verify_lexical_identity()
        rebuild_dir.verify_child_identity(
            "candidate-snapshot.zip",
            snapshot_file,
        )
        public_dir.verify_lexical_identity()
        snapshot_sha, final_snapshot_bytes = snapshot_file.digest()
        if (
            snapshot_sha != archive_sha256
            or final_snapshot_bytes != archive_bytes
        ):
            raise ProductionReleaseAssetsError(
                "Production candidate snapshot changed before commit"
            )
        for name, bound in public_files.items():
            public_dir.verify_child_identity(name, bound)
            digest, byte_length = bound.digest()
            if name == archive_name:
                expected_payload_sha = archive_sha256
                expected_payload_bytes = archive_bytes
            else:
                expected_payload = public_payloads[name]
                expected_payload_sha = hashlib.sha256(
                    expected_payload
                ).hexdigest()
                expected_payload_bytes = len(expected_payload)
                if bound.read_bytes(
                    maximum_bytes=_MAXIMUM_PUBLIC_CONTRACT_BYTES
                ) != expected_payload:
                    raise ProductionReleaseAssetsError(
                        f"Production public asset payload changed: {name}"
                    )
            if (
                digest != expected_payload_sha
                or byte_length != expected_payload_bytes
            ):
                raise ProductionReleaseAssetsError(
                    f"Production public asset final seal failed: {name}"
                )
        final_verification = verify_production_release_archive_stream(
            archive_file.stream
        )
        final_receipt, final_checksums = _archive_contract_payloads_stream(
            archive_file.stream
        )
        if (
            final_verification != verification
            or final_receipt != receipt_payload
            or final_checksums != checksums_payload
        ):
            raise ProductionReleaseAssetsError(
                "Production public archive final contracts disagree"
            )
        result = ProductionReleaseAssets(
            output_dir=output,
            archive_path=output / archive_name,
            archive_sha256=archive_sha256,
            receipt_path=output / PRODUCTION_RELEASE_NAME,
            checksums_path=output / CHECKSUMS_NAME,
            package_content_id=verification.package_content_id,
            privacy_valid=True,
            scene_trust_effect="none",
            retained_private_paths=(
                rebuild_dir.path,
                snapshot_file.path,
                rebuilt_result.archive_path,
                rebuilt_result.archive_path.with_suffix(
                    f"{rebuilt_result.archive_path.suffix}.sha256"
                ),
            ),
        )
        body_failed = False
        return result
    except ProductionReleaseAssetsError as exc:
        published = exc.published or tuple(public_names)
        retained = exc.retained or tuple((*private_names, *public_names))
        raise ProductionReleaseAssetsError(
            f"{exc}; published={published}; retained={retained}",
            published=published,
            retained=retained,
        ) from exc
    except ProductionReleaseMutationError as exc:
        published = tuple(
            dict.fromkeys((*public_names, *exc.published))
        )
        retained = tuple(
            dict.fromkeys(
                (*private_names, *public_names, *exc.retained)
            )
        )
        raise ProductionReleaseAssetsError(
            f"{exc}; published={published}; retained={retained}",
            published=published,
            retained=retained,
        ) from exc
    except ProductionReleaseBuilderError as exc:
        child_retained = tuple(
            f"{rebuild_name}/{name}" for name in exc.retained
        )
        retained = tuple(
            dict.fromkeys(
                (*private_names, *public_names, *child_retained)
            )
        )
        raise ProductionReleaseAssetsError(
            f"Production acceptance rebuild failed: {exc}; "
            f"published={tuple(public_names)}; retained={retained}",
            published=tuple(public_names),
            retained=retained,
        ) from exc
    except (
        FileExistsError,
        OSError,
        ProductionReleaseFSError,
        ProductionReleasePrivacyError,
        ProductionReleaseVerificationError,
        ReleaseArchiveError,
    ) as exc:
        retained = tuple((*private_names, *public_names))
        raise ProductionReleaseAssetsError(
            "Production release assets cannot be staged; "
            f"published={tuple(public_names)}; retained={retained}",
            published=tuple(public_names),
            retained=retained,
        ) from exc
    finally:
        close_error = close_bound_capabilities_best_effort(
            (
                *reversed(held_files),
                public_dir,
                rebuild_dir,
                parent,
            )
        )
        if close_error is not None and not body_failed:
            published = tuple(
                dict.fromkeys((*public_names, *close_error.published))
            )
            retained = tuple(
                dict.fromkeys(
                    (
                        *private_names,
                        *public_names,
                        *close_error.retained,
                    )
                )
            )
            raise ProductionReleaseAssetsError(
                "Production release capabilities failed to close; "
                f"published={published}; retained={retained}",
                published=published,
                retained=retained,
            ) from close_error
