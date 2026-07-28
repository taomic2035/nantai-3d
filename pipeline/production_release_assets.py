"""Stage the four public assets for one accepted Production release."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from pipeline.durable_io import (
    DurableIOError,
    flush_directory,
    flush_file,
    publish_directory_noreplace,
)
from pipeline.production_release_builder import (
    ProductionReleaseBuilderError,
    build_production_release_archive,
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


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


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
    if output.exists() or _is_linklike(output):
        raise ProductionReleaseAssetsError(
            "Production release asset output directory must be absent"
        )
    try:
        parent_stat = output.parent.lstat()
        parent_real = output.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProductionReleaseAssetsError(
            "Production release asset output parent is unavailable"
        ) from exc
    if (
        _is_linklike(output.parent)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or parent_real != output.parent
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
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(
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


def _stable_contract_bytes(path: Path) -> bytes:
    try:
        path_before = path.lstat()
    except OSError as exc:
        raise ProductionReleaseAssetsError(
            "Production public contract is unavailable"
        ) from exc
    if (
        stat.S_ISLNK(path_before.st_mode)
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
        if _is_linklike(candidate) or not stat.S_ISREG(
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


def _git_output(arguments: list[str], repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ProductionReleaseAssetsError(
            "Git source identity cannot be resolved for staging rebuild"
        )
    return completed.stdout


def _rebuild_candidate_from_acceptance(
    *,
    acceptance_root: Path,
    version: str,
    repo_root: Path,
    output_path: Path,
) -> str:
    """Rebuild a candidate archive from acceptance root on current HEAD.

    Returns the SHA-256 of the rebuilt archive.  The caller must
    byte-compare this with the input candidate archive SHA-256.
    """
    source_commit = _git_output(
        ["rev-parse", "--verify", "HEAD"], repo_root
    ).strip()
    tracked_files = tuple(
        relative
        for relative in _git_output(
            ["ls-files", "-z"], repo_root
        ).split("\0")
        if relative
    )
    result = build_production_release_archive(
        repo_root=repo_root,
        acceptance_root=acceptance_root,
        output_path=output_path,
        version=version,
        source_commit=source_commit,
        tracked_files=tracked_files,
    )
    return result.archive_sha256


def stage_production_release_assets(
    *,
    archive_path: str | Path,
    privacy_policy_path: str | Path,
    output_dir: str | Path,
    acceptance_root: str | Path,
    version: str,
    repo_root: str | Path | None = None,
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
    repo = (
        Path(repo_root).expanduser().absolute()
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    output = _real_absent_output(Path(output_dir))
    staging = output.parent / (
        f".{output.name}.{uuid.uuid4().hex}.staging"
    )
    candidate = staging / ".candidate.zip"
    rebuilt = staging / ".rebuilt.zip"
    published = False
    try:
        staging.mkdir(mode=0o700)
        archive_sha256 = _copy_stable_regular_file(source, candidate)
        rebuilt_sha256 = _rebuild_candidate_from_acceptance(
            acceptance_root=acceptance,
            version=version,
            repo_root=repo,
            output_path=rebuilt,
        )
        candidate_digest = stable_regular_file_digest(candidate)
        if candidate_digest.sha256 != rebuilt_sha256:
            raise ProductionReleaseAssetsError(
                "rebuilt candidate disagrees with input archive"
            )
        if (
            stable_regular_file_digest(candidate).sha256
            != archive_sha256
        ):
            raise ProductionReleaseAssetsError(
                "candidate archive changed during rebuild"
            )
        with tempfile.TemporaryDirectory(
            prefix="nantai-production-assets-"
        ) as temporary:
            extracted = extract_production_release_archive(
                candidate,
                Path(temporary) / "runtime",
            )
            verification_before = verify_production_release_tree(extracted)
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
        ProductionReleaseBuilderError,
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
