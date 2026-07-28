"""Stage the four public assets for one accepted Production release."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from pipeline.durable_io import (
    DurableIOError,
    _is_linklike,
    first_linklike_path,
    flush_directory,
    flush_file,
    publish_directory_noreplace,
)
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
from pipeline.production_release_privacy import (
    ProductionReleasePrivacyError,
    audit_production_release_privacy,
)
from pipeline.production_release_verifier import (
    ProductionReleaseVerificationError,
    extract_production_release_archive,
    verify_production_release_tree,
)
from pipeline.release_archive import (
    ReleaseArchiveError,
    stable_regular_file_digest,
)

_MAXIMUM_PUBLIC_CONTRACT_BYTES = 16 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


class ProductionReleaseAssetsError(ValueError):
    """A final Production public-asset bundle cannot be trusted."""


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


def _real_absent_output(path: Path) -> Path:
    output = Path(path).expanduser().absolute()
    try:
        redirected = first_linklike_path(Path(output.anchor), output)
    except (OSError, ValueError) as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset output parent is unavailable"
        ) from exc
    if output.exists() or redirected == output:
        raise ProductionReleaseAssetsError(
            "Production release asset output directory must be absent"
        )
    try:
        parent_stat = output.parent.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset output parent is unavailable"
        ) from exc
    if (
        redirected is not None
        or _is_linklike(output.parent, observed=parent_stat)
        or not stat.S_ISDIR(parent_stat.st_mode)
    ):
        raise ProductionReleaseAssetsError(
            "Production release asset output parent must be a real directory"
        )
    return output


def _copy_stable_regular_file(source: Path, destination: Path) -> str:
    try:
        path_before = source.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production candidate archive is unavailable"
        ) from exc
    if _is_linklike(source, observed=path_before) or not stat.S_ISREG(
        path_before.st_mode
    ):
        raise ProductionReleaseAssetsError(
            "Production candidate archive must be a regular non-link file"
        )

    digest = hashlib.sha256()
    observed_bytes = 0
    descriptor = None
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        with source.open("rb") as source_stream:
            descriptor_before = os.fstat(source_stream.fileno())
            with os.fdopen(descriptor, "wb") as destination_stream:
                descriptor = None
                while True:
                    chunk = source_stream.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    observed_bytes += len(chunk)
                    digest.update(chunk)
                    destination_stream.write(chunk)
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            descriptor_after = os.fstat(source_stream.fileno())
        path_after = source.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production candidate archive cannot be copied"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    expected = _signature(path_before)
    if (
        expected != _signature(descriptor_before)
        or expected != _signature(descriptor_after)
        or expected != _signature(path_after)
        or observed_bytes != path_before.st_size
    ):
        raise ProductionReleaseAssetsError(
            "Production candidate archive changed during copy"
        )
    copied = stable_regular_file_digest(destination)
    if (
        copied.byte_length != observed_bytes
        or copied.sha256 != digest.hexdigest()
    ):
        raise ProductionReleaseAssetsError(
            "Production candidate archive copy is inconsistent"
        )
    return copied.sha256


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

        equal = True
        with left.open("rb") as left_stream, right.open("rb") as right_stream:
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
    try:
        with path.open("rb") as stream:
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


def _write_public_payload(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o644,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _public_bundle_files(root: Path) -> dict[str, Path]:
    if _is_linklike(root) or not root.is_dir():
        raise ProductionReleaseAssetsError(
            "Production release asset directory is missing or unsafe"
        )
    observed: dict[str, Path] = {}
    try:
        entries = tuple(root.iterdir())
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset directory cannot be read"
        ) from exc
    for candidate in entries:
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
        with tempfile.TemporaryDirectory(
            prefix="nantai-production-assets-verify-"
        ) as temporary:
            extracted = extract_production_release_archive(
                archive,
                Path(temporary) / "runtime",
            )
            verification = verify_production_release_tree(extracted)
            if (
                _stable_contract_bytes(
                    extracted / PRODUCTION_RELEASE_NAME
                )
                != receipt_payload
                or _stable_contract_bytes(extracted / CHECKSUMS_NAME)
                != checksums_payload
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
    """Verify, privacy-audit and stage exactly four public Release assets.

    The input candidate archive is byte-compared against a deterministic
    rebuild from *acceptance_root* + *version* on the current HEAD before
    any public asset is written.  This closes the self-declared
    ``fixture_kind`` trust gap identified in GLM-026R P1.
    """
    source = Path(archive_path).expanduser().absolute()
    policy = Path(privacy_policy_path).expanduser().absolute()
    acceptance = Path(acceptance_root).expanduser().absolute()
    repo = Path(repo_root).expanduser().absolute()
    output = _real_absent_output(Path(output_dir))
    staging = output.parent / (
        f".{output.name}.{uuid.uuid4().hex}.staging"
    )
    candidate = staging / ".candidate.zip"
    rebuilt = staging / ".acceptance-rebuild.zip"
    rebuilt_sidecar = rebuilt.with_suffix(f"{rebuilt.suffix}.sha256")
    published = False
    try:
        staging.mkdir(mode=0o700)
        archive_sha256 = _copy_stable_regular_file(source, candidate)
        try:
            source_identity_before = (
                resolve_production_release_source_identity(repo)
            )
            rebuilt_result = build_production_release_archive(
                repo_root=repo,
                acceptance_root=acceptance,
                output_path=rebuilt,
                version=version,
                source_commit=source_identity_before.source_commit,
                tracked_files=source_identity_before.tracked_files,
            )
            source_identity_after = (
                resolve_production_release_source_identity(repo)
            )
        except ProductionReleaseBuilderError as exc:
            raise ProductionReleaseAssetsError(
                "Production acceptance rebuild failed"
            ) from exc
        if source_identity_after != source_identity_before:
            raise ProductionReleaseAssetsError(
                "Production source identity changed during acceptance rebuild"
            )
        if rebuilt_result.archive_path != rebuilt:
            raise ProductionReleaseAssetsError(
                "Production candidate does not match acceptance rebuild"
            )
        try:
            rebuilt_digest = stable_regular_file_digest(rebuilt)
        except ReleaseArchiveError as exc:
            raise ProductionReleaseAssetsError(
                "Production candidate does not match acceptance rebuild"
            ) from exc
        if (
            rebuilt_result.archive_sha256 != rebuilt_digest.sha256
            or archive_sha256 != rebuilt_result.archive_sha256
            or not _stable_regular_files_equal(candidate, rebuilt)
        ):
            raise ProductionReleaseAssetsError(
                "Production candidate does not match acceptance rebuild"
            )
        rebuilt.unlink()
        rebuilt_sidecar.unlink()
        with tempfile.TemporaryDirectory(
            prefix="nantai-production-assets-"
        ) as temporary:
            extracted = extract_production_release_archive(
                candidate,
                Path(temporary) / "runtime",
            )
            verification_before = verify_production_release_tree(extracted)
            if verification_before.version != version:
                raise ProductionReleaseAssetsError(
                    "Production candidate version does not match requested "
                    "version"
                )
            if (
                verification_before.fixture_kind is not None
                or verification_before.release_contract
                != "production-accepted"
            ):
                raise ProductionReleaseAssetsError(
                    "modeled contract cannot be staged as a Production release"
                )
            privacy = audit_production_release_privacy(
                extracted,
                policy,
            )
            if not privacy.valid or privacy.finding_count != 0:
                raise ProductionReleaseAssetsError(
                    "Production privacy audit failed"
                )
            if (
                privacy.package_content_id
                != verification_before.package_content_id
            ):
                raise ProductionReleaseAssetsError(
                    "Production privacy identity disagrees with the package"
                )
            receipt_payload = _stable_contract_bytes(
                extracted / PRODUCTION_RELEASE_NAME
            )
            checksums_payload = _stable_contract_bytes(
                extracted / CHECKSUMS_NAME
            )
            verification_after = verify_production_release_tree(extracted)
            if verification_after != verification_before:
                raise ProductionReleaseAssetsError(
                    "Production package identity changed during asset staging"
                )

        archive_name = (
            f"nantai-3d-{verification_before.version}-runtime.zip"
        )
        final_archive = staging / archive_name
        os.replace(candidate, final_archive)
        if (
            stable_regular_file_digest(final_archive).sha256
            != archive_sha256
        ):
            raise ProductionReleaseAssetsError(
                "Production archive changed during asset staging"
            )
        _write_public_payload(
            staging / PRODUCTION_RELEASE_NAME,
            receipt_payload,
        )
        _write_public_payload(
            staging / CHECKSUMS_NAME,
            checksums_payload,
        )
        _write_public_payload(
            staging / f"{archive_name}.sha256",
            f"{archive_sha256}  {archive_name}\n".encode("ascii"),
        )
        flush_file(final_archive)
        flush_directory(staging)
        try:
            staged_verification = verify_production_release_assets(staging)
        except ProductionReleaseAssetsError as exc:
            raise ProductionReleaseAssetsError(
                "Production staged release validation failed"
            ) from exc
        if (
            not staged_verification.valid
            or staged_verification.archive_path != final_archive
            or staged_verification.archive_sha256 != archive_sha256
            or staged_verification.version != verification_before.version
            or staged_verification.package_content_id
            != verification_before.package_content_id
            or staged_verification.scene_trust_effect
            != verification_before.scene_trust_effect
        ):
            raise ProductionReleaseAssetsError(
                "Production staged release validation failed"
            )
        publish_directory_noreplace(staging, output)
        published = True
        return ProductionReleaseAssets(
            output_dir=output,
            archive_path=output / archive_name,
            archive_sha256=archive_sha256,
            receipt_path=output / PRODUCTION_RELEASE_NAME,
            checksums_path=output / CHECKSUMS_NAME,
            package_content_id=verification_before.package_content_id,
            privacy_valid=True,
            scene_trust_effect="none",
        )
    except ProductionReleaseAssetsError:
        raise
    except (
        DurableIOError,
        FileExistsError,
        OSError,
        ProductionReleasePrivacyError,
        ProductionReleaseVerificationError,
        ReleaseArchiveError,
    ) as exc:
        if isinstance(exc, DurableIOError) and exc.published:
            published = True
        state = (
            "published but durability is unconfirmed"
            if isinstance(exc, DurableIOError) and exc.published
            else "not published"
        )
        raise ProductionReleaseAssetsError(
            f"Production release assets cannot be staged ({state})"
        ) from exc
    finally:
        if not published and not _is_linklike(staging):
            shutil.rmtree(staging, ignore_errors=True)
