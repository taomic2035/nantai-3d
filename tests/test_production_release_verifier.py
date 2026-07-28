from __future__ import annotations

import copy
import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
)
from pipeline.production_release_verifier import (
    ProductionReleaseVerificationError,
    extract_production_release_archive,
    verify_production_release_archive,
    verify_production_release_tree,
)
from pipeline.release_archive import (
    ArchiveLimits,
    canonical_json_bytes,
    deterministic_zip_info,
)
from tests.production_release_fixtures import (
    write_modeled_production_archive,
    write_modeled_production_tree,
)


def _tree(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "runtime"
    return root, write_modeled_production_tree(root)


def _refresh_receipt_and_checksums(
    root: Path,
    receipt: dict[str, object],
) -> None:
    unsigned = copy.deepcopy(receipt)
    unsigned["package"]["content_id"] = None
    receipt["package"]["content_id"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    receipt_bytes = canonical_json_bytes(receipt)
    (root / PRODUCTION_RELEASE_NAME).write_bytes(receipt_bytes)
    rows = [
        f"{artifact['sha256']}  {artifact['path']}\n"
        for artifact in receipt["artifacts"]
    ]
    rows.append(
        f"{hashlib.sha256(receipt_bytes).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    (root / CHECKSUMS_NAME).write_bytes(
        "".join(sorted(rows)).encode("ascii")
    )


def test_tree_verifier_reports_modeled_contract_without_promoting_trust(
    tmp_path: Path,
) -> None:
    root, _receipt = _tree(tmp_path)

    report = verify_production_release_tree(root)

    assert report.valid is True
    assert report.package_integrity == "verified"
    assert report.release_contract == "modeled-contract-only"
    assert report.scene_trust_effect == "none"
    assert report.fixture_kind == "modeled-contract-not-real-release"
    assert report.version == "v1.0.0"


@pytest.mark.parametrize("failure", ("changed", "missing", "extra"))
def test_tree_verifier_rejects_changed_missing_and_extra_protected_files(
    tmp_path: Path,
    failure: str,
) -> None:
    root, _receipt = _tree(tmp_path)
    protected = root / "web/viewer/index.html"
    if failure == "changed":
        protected.write_bytes(b"changed\n")
    elif failure == "missing":
        protected.unlink()
    else:
        (root / "web/viewer/extra.js").write_bytes(b"extra\n")

    with pytest.raises(ProductionReleaseVerificationError):
        verify_production_release_tree(root)


@pytest.mark.parametrize("target", ("receipt", "evidence"))
def test_tree_verifier_rejects_noncanonical_contract_bytes(
    tmp_path: Path,
    target: str,
) -> None:
    root, _receipt = _tree(tmp_path)
    path = (
        root / PRODUCTION_RELEASE_NAME
        if target == "receipt"
        else root / "evidence/public-evidence.json"
    )
    value = json.loads(path.read_bytes())
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    with pytest.raises(ProductionReleaseVerificationError):
        verify_production_release_tree(root)


def test_tree_verifier_rejects_checksum_disagreement(tmp_path: Path) -> None:
    root, _receipt = _tree(tmp_path)
    (root / CHECKSUMS_NAME).write_text("0" * 64 + "  bogus\n", encoding="ascii")

    with pytest.raises(ProductionReleaseVerificationError, match="checksum"):
        verify_production_release_tree(root)


def test_tree_verifier_rejects_scene_evidence_cross_swap(tmp_path: Path) -> None:
    root, receipt = _tree(tmp_path)
    evidence_path = root / "evidence/public-evidence.json"
    evidence = json.loads(evidence_path.read_bytes())
    evidence["scene"]["manifest_sha256"] = "1" * 64
    evidence_bytes = canonical_json_bytes(evidence)
    evidence_path.write_bytes(evidence_bytes)
    for artifact in receipt["artifacts"]:
        if artifact["path"] == "evidence/public-evidence.json":
            artifact["bytes"] = len(evidence_bytes)
            artifact["sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    receipt["acceptance"]["public_evidence_sha256"] = hashlib.sha256(
        evidence_bytes
    ).hexdigest()
    _refresh_receipt_and_checksums(root, receipt)

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="scene manifest",
    ):
        verify_production_release_tree(root)


def test_tree_verifier_rejects_symlink(tmp_path: Path) -> None:
    root, _receipt = _tree(tmp_path)
    link = root / "web/viewer/link.html"
    try:
        link.symlink_to(root / "web/viewer/index.html")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ProductionReleaseVerificationError, match="symlink"):
        verify_production_release_tree(root)


def test_archive_verifier_accepts_one_bounded_wrapper_root(tmp_path: Path) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    report = verify_production_release_archive(archive)

    assert report.valid is True
    assert report.release_contract == "modeled-contract-only"


@pytest.mark.parametrize(
    ("entries", "message"),
    (
        (
            (
                ("nantai-runtime/../escape.txt", b"x"),
                ("nantai-runtime/file.txt", b"x"),
            ),
            "unsafe",
        ),
        (
            (("first/a.txt", b"a"), ("second/b.txt", b"b")),
            "root",
        ),
        (
            (
                ("nantai-runtime/web/App.js", b"a"),
                ("nantai-runtime/web/app.js", b"b"),
            ),
            "case-fold",
        ),
    ),
)
def test_archive_verifier_rejects_path_and_root_ambiguity(
    tmp_path: Path,
    entries: tuple[tuple[str, bytes], ...],
    message: str,
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, payload in entries:
            if ".." in name.split("/"):
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
            else:
                info = deterministic_zip_info(name)
            archive.writestr(info, payload)

    with pytest.raises(ProductionReleaseVerificationError, match=message):
        verify_production_release_archive(archive_path)


@pytest.mark.parametrize(
    ("file_type", "name"),
    (
        (stat.S_IFLNK, "link"),
        (stat.S_IFIFO, "fifo"),
    ),
)
def test_archive_verifier_rejects_unsupported_member_types(
    tmp_path: Path,
    file_type: int,
    name: str,
) -> None:
    archive_path = tmp_path / f"{name}.zip"
    info = zipfile.ZipInfo(f"nantai-runtime/{name}")
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (file_type | 0o644) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, b"x")

    with pytest.raises(ProductionReleaseVerificationError, match="regular"):
        verify_production_release_archive(archive_path)


def test_archive_verifier_rejects_directory_entry(tmp_path: Path) -> None:
    archive_path = tmp_path / "directory.zip"
    info = zipfile.ZipInfo("nantai-runtime/web/")
    info.create_system = 3
    info.external_attr = (stat.S_IFDIR | 0o755) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(info, b"")
        archive.writestr(
            deterministic_zip_info("nantai-runtime/file.txt"),
            b"x",
        )

    with pytest.raises(ProductionReleaseVerificationError, match="directory"):
        verify_production_release_archive(archive_path)


def test_archive_verifier_rejects_encrypted_member_metadata(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "encrypted.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            deterministic_zip_info("nantai-runtime/file.txt"),
            b"payload",
        )
    payload = bytearray(archive_path.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = payload.index(signature)
        flags = int.from_bytes(payload[index + offset : index + offset + 2], "little")
        payload[index + offset : index + offset + 2] = (flags | 1).to_bytes(
            2,
            "little",
        )
    archive_path.write_bytes(payload)

    with pytest.raises(ProductionReleaseVerificationError, match="encrypted"):
        verify_production_release_archive(archive_path)


@pytest.mark.parametrize(
    ("limits", "message"),
    (
        (ArchiveLimits(maximum_member_bytes=999), "member"),
        (ArchiveLimits(maximum_total_bytes=999), "total"),
        (ArchiveLimits(maximum_compression_ratio=2), "ratio"),
    ),
)
def test_archive_verifier_enforces_expansion_limits(
    tmp_path: Path,
    limits: ArchiveLimits,
    message: str,
) -> None:
    archive_path = tmp_path / "expanded.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            deterministic_zip_info("nantai-runtime/zeros.bin"),
            b"\0" * 1000,
        )

    with pytest.raises(ProductionReleaseVerificationError, match=message):
        verify_production_release_archive(archive_path, limits=limits)


def test_archive_extraction_refuses_existing_destination(tmp_path: Path) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "existing"
    destination.mkdir()

    with pytest.raises(ProductionReleaseVerificationError, match="exists"):
        extract_production_release_archive(archive, destination)
    assert list(destination.iterdir()) == []


def test_archive_extraction_rejects_illegal_path_and_leaves_no_destination(
    tmp_path: Path,
) -> None:
    """Illegal child path must be rejected before files are created."""
    archive = tmp_path / "bad-child.zip"
    with zipfile.ZipFile(archive, "w") as zip_handle:
        info = zipfile.ZipInfo("nantai-runtime/CON")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        zip_handle.writestr(info, b"x")

    destination = tmp_path / "extracted"
    with pytest.raises(ProductionReleaseVerificationError, match="reserved"):
        extract_production_release_archive(archive, destination)
    assert not destination.exists()


def test_archive_extraction_rejects_truncated_zip_and_cleans_destination(
    tmp_path: Path,
) -> None:
    """Truncated ZIP (EOFError) must fail closed and remove destination."""
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    payload = bytearray(archive.read_bytes())
    truncated = payload[: len(payload) // 2]
    archive.write_bytes(truncated)

    destination = tmp_path / "extracted"
    with pytest.raises(ProductionReleaseVerificationError):
        extract_production_release_archive(archive, destination)
    assert not destination.exists()


def test_archive_extraction_rejects_unsupported_compression_and_cleans_destination(
    tmp_path: Path,
) -> None:
    """Unsupported compression method must fail closed and remove destination."""
    archive_path = tmp_path / "unsupported.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            deterministic_zip_info("nantai-runtime/placeholder.txt"),
            b"placeholder",
        )
    payload = bytearray(archive_path.read_bytes())
    local_sig = b"PK\x03\x04"
    central_sig = b"PK\x01\x02"
    local_index = payload.index(local_sig)
    payload[local_index + 8 : local_index + 10] = (99).to_bytes(2, "little")
    central_index = payload.index(central_sig)
    payload[central_index + 10 : central_index + 12] = (99).to_bytes(
        2, "little"
    )
    archive_path.write_bytes(payload)

    destination = tmp_path / "extracted"
    with pytest.raises(ProductionReleaseVerificationError):
        extract_production_release_archive(archive_path, destination)
    assert not destination.exists()
