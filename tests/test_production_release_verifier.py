from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.production_release_fs as release_fs
import pipeline.production_release_verifier as verifier_module
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
)
from pipeline.production_release_verifier import (
    ProductionReleaseVerificationError,
    extract_production_release_archive,
    verify_production_release_archive,
    verify_production_release_archive_stream,
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


def test_tree_verifier_rejects_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Windows junction (reparse point) must be rejected like a symlink."""
    root, _receipt = _tree(tmp_path)
    junction = root / "web/viewer/junction-link.html"
    junction.mkdir()
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == junction or original(self),
        raising=False,
    )

    with pytest.raises(ProductionReleaseVerificationError, match="symlink"):
        verify_production_release_tree(root)


def test_tree_verifier_rejects_reparse_root_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)
    observed = root.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == root:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=getattr(
                    stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x400,
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="missing or unsafe",
    ):
        verify_production_release_tree(root)


def test_tree_verifier_rejects_reparse_ancestor_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ancestor = tmp_path / "alias"
    root = ancestor / "runtime"
    write_modeled_production_tree(root)
    observed = ancestor.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="missing or unsafe",
    ):
        verify_production_release_tree(root)


def test_archive_entrypoints_reject_reparse_source_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ancestor = tmp_path / "alias"
    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    archive = ancestor / "runtime.zip"
    ancestor.mkdir()
    write_modeled_production_archive(tree, archive)
    observed = ancestor.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    extraction_message = (
        "missing or unsafe"
        if sys.platform == "linux"
        else "private Linux builder"
    )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match=extraction_message,
    ):
        extract_production_release_archive(
            archive,
            tmp_path / "extracted",
        )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="missing or unsafe",
    ):
        verify_production_release_archive(archive)


def test_tree_verifier_rejects_scandir_toctou_root_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Post-scan lstat must detect root identity swap between lstat and scandir.

    The root is checked by verify_production_release_tree, then _release_files
    opens it via os.scandir by name.  Without a post-scan identity recheck, a
    TOCTOU swap to a reparse point would cause scandir to follow the redirect
    and walk an untrusted tree.
    """

    root, _receipt = _tree(tmp_path)

    scandir_called = False
    original_lstat = Path.lstat
    original_scandir = os.scandir

    def swapping_lstat(path):
        result = original_lstat(path)
        if path == root and scandir_called:
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino + 1,
                st_mode=result.st_mode,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
            )
        return result

    def tracking_scandir(path, *args, **kwargs):
        nonlocal scandir_called
        if path == root:
            scandir_called = True
        return original_scandir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    monkeypatch.setattr(os, "scandir", tracking_scandir)

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="release directory changed during scan",
    ):
        verify_production_release_tree(root)


def test_tree_verifier_rejects_scandir_toctou_subdirectory_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Post-scan lstat must detect subdirectory identity swap after scandir.

    Each subdirectory is lstat'd, then opened via os.scandir by name.  Without
    a post-scan identity recheck, a TOCTOU swap to a reparse point between
    lstat and scandir would cause the iterator to follow the redirect.
    """

    root, _receipt = _tree(tmp_path)
    sub = root / "web/viewer"

    sub_scandir_called = False
    original_lstat = Path.lstat
    original_scandir = os.scandir

    def swapping_lstat(path):
        result = original_lstat(path)
        if path == sub and sub_scandir_called:
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino + 1,
                st_mode=result.st_mode,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
            )
        return result

    def tracking_scandir(path, *args, **kwargs):
        nonlocal sub_scandir_called
        if path == sub:
            sub_scandir_called = True
        return original_scandir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    monkeypatch.setattr(os, "scandir", tracking_scandir)

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="release directory changed during scan",
    ):
        verify_production_release_tree(root)


def test_tree_verifier_payload_errors_do_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Contract: _stable_payload error messages must not echo absolute paths."""

    root, _receipt = _tree(tmp_path)
    receipt_path = root / PRODUCTION_RELEASE_NAME

    original_os_open = os.open

    def failing_os_open(path, *args, **kwargs):
        if Path(path) == receipt_path:
            raise OSError("injected")
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_os_open)

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="release file cannot be read",
    ) as exc:
        verify_production_release_tree(root)

    message = str(exc.value)
    assert str(receipt_path) not in message
    assert str(receipt_path.parent) not in message


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_tree_verifier_rejects_real_windows_junction_before_walk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"must not be walked")
    junction = root / "web/viewer/junction"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    try:
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda _path: False,
            raising=False,
        )

        with pytest.raises(
            ProductionReleaseVerificationError,
            match="symlink",
        ):
            verify_production_release_tree(root)
    finally:
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(junction)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert removed.returncode == 0, removed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_release_entrypoints_reject_real_windows_junction_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    real = tmp_path / "real"
    tree = real / "runtime"
    write_modeled_production_tree(tree)
    archive = real / "runtime.zip"
    write_modeled_production_archive(tree, archive)
    alias = tmp_path / "alias"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(real)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    try:
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda _path: False,
            raising=False,
        )

        with pytest.raises(
            ProductionReleaseVerificationError,
            match="missing or unsafe",
        ):
            verify_production_release_tree(alias / "runtime")
        with pytest.raises(
            ProductionReleaseVerificationError,
            match="private Linux builder",
        ):
            extract_production_release_archive(
                alias / "runtime.zip",
                tmp_path / "extracted",
            )
        with pytest.raises(
            ProductionReleaseVerificationError,
            match="missing or unsafe",
        ):
            verify_production_release_archive(alias / "runtime.zip")
    finally:
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(alias)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert removed.returncode == 0, removed.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
@pytest.mark.parametrize(
    "failure",
    (
        ProductionReleaseVerificationError("injected verification failure"),
        EOFError("injected archive failure"),
    ),
)
def test_extraction_cleanup_does_not_follow_swapped_parent_junction(
    tmp_path: Path,
    monkeypatch,
    failure: Exception,
) -> None:
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("nantai-runtime/file.txt", b"payload")
    parent = tmp_path / "extract-parent"
    parent.mkdir()
    target = parent / "runtime"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / target.name
    outside_target.mkdir()
    sentinel = outside_target / "sentinel.bin"
    sentinel.write_bytes(b"outside-sentinel")
    original_parent = tmp_path / "extract-parent-original"
    swapped = False

    def fail_after_parent_swap(*_args, **_kwargs):
        nonlocal swapped
        parent.rename(original_parent)
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            original_parent.rename(parent)
            pytest.skip(f"junction creation unavailable: {created.stderr}")
        swapped = True
        raise failure

    monkeypatch.setattr(
        verifier_module,
        "inspect_zip_members",
        fail_after_parent_swap,
    )
    try:
        with pytest.raises(ProductionReleaseVerificationError):
            extract_production_release_archive(archive, target)
        assert sentinel.read_bytes() == b"outside-sentinel"
    finally:
        if swapped:
            removed = subprocess.run(
                ["cmd", "/c", "rmdir", str(parent)],
                check=False,
                capture_output=True,
                text=True,
            )
            assert removed.returncode == 0, removed.stderr


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


def test_archive_verifier_does_not_extract_or_create_temporary_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("archive verification must be read-only")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(
        verifier_module,
        "extract_production_release_archive",
        forbidden,
    )

    result = verify_production_release_archive(archive)

    assert result.valid is True


def test_archive_stream_verifier_does_not_reopen_by_name(
    tmp_path: Path,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    stream = io.BytesIO(archive.read_bytes())
    archive.unlink()
    result = verify_production_release_archive_stream(stream)

    assert result.valid is True


@pytest.mark.parametrize(
    "limits",
    (
        ArchiveLimits(maximum_members=1),
        ArchiveLimits(maximum_central_directory_bytes=1),
    ),
)
def test_archive_stream_preflight_rejects_before_zipfile_construction(
    tmp_path: Path,
    monkeypatch,
    limits: ArchiveLimits,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    stream = io.BytesIO(archive.read_bytes())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("ZipFile construction must not start")

    monkeypatch.setattr(verifier_module.zipfile, "ZipFile", forbidden)
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="maximum",
    ):
        verify_production_release_archive_stream(
            stream,
            limits=limits,
        )


def test_archive_stream_preflight_counts_actual_central_records_before_zipfile(
    monkeypatch,
) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for index in range(3):
            archive.writestr(f"payload-{index}.txt", b"payload")
    payload = bytearray(stream.getvalue())
    eocd_offset = payload.rfind(b"PK\x05\x06")
    payload[eocd_offset + 8 : eocd_offset + 12] = (
        b"\x01\x00\x01\x00"
    )
    forged = io.BytesIO(payload)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("ZipFile construction must not start")

    monkeypatch.setattr(verifier_module.zipfile, "ZipFile", forbidden)
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="member count",
    ):
        verify_production_release_archive_stream(
            forged,
            limits=ArchiveLimits(maximum_members=1),
        )


def test_tree_receipt_limits_fail_before_directory_walk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("tree walk must not start")

    monkeypatch.setattr(verifier_module, "_release_files", forbidden)
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="member count",
    ):
        verify_production_release_tree(
            root,
            limits=ArchiveLimits(maximum_members=1),
        )


def test_tree_verification_counts_empty_directories_as_members(
    tmp_path: Path,
) -> None:
    root, _receipt = _tree(tmp_path)
    for index in range(4):
        (root / f"empty-{index}").mkdir()
    file_count = sum(
        len(names)
        for _current, _directories, names in os.walk(root)
    )

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="member count",
    ):
        verify_production_release_tree(
            root,
            limits=ArchiveLimits(maximum_members=file_count),
        )


def test_tree_walk_stops_streaming_at_member_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "wide"
    root.mkdir()
    for index in range(20):
        (root / f"{index:02d}.txt").write_text("x", encoding="utf-8")
    original_scandir = verifier_module.os.scandir
    yielded = 0

    class TrackingScandir:
        def __init__(self, path):
            self._iterator = original_scandir(path)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal yielded
            entry = next(self._iterator)
            yielded += 1
            return entry

        def close(self):
            self._iterator.close()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        verifier_module.os,
        "scandir",
        TrackingScandir,
    )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="member count",
    ):
        verifier_module._release_files(
            root,
            limits=ArchiveLimits(maximum_members=3),
            root_stat=root.lstat(),
        )

    assert yielded == 4


def test_tree_walk_fails_closed_on_scandir_iteration_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "unreadable"
    root.mkdir()
    closed = False

    class FailingScandir:
        def __iter__(self):
            return self

        def __next__(self):
            raise PermissionError("denied")

        def close(self):
            nonlocal closed
            closed = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        verifier_module.os,
        "scandir",
        lambda _path: FailingScandir(),
    )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="unavailable",
    ):
        verifier_module._release_files(
            root,
            limits=ArchiveLimits(),
            root_stat=root.lstat(),
        )

    assert closed is True


def test_tree_walk_fails_closed_on_lstat_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "release"
    root.mkdir()
    blocked = root / "blocked.txt"
    blocked.write_text("x", encoding="utf-8")
    original_lstat = Path.lstat

    def fail_blocked(path):
        if path == blocked:
            raise PermissionError("denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_blocked)
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="unavailable",
    ):
        verifier_module._release_files(
            root,
            limits=ArchiveLimits(),
            root_stat=root.lstat(),
        )


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_refuses_existing_destination(tmp_path: Path) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "existing"
    destination.mkdir()

    with pytest.raises(ProductionReleaseVerificationError, match="exists"):
        extract_production_release_archive(archive, destination)
    assert list(destination.iterdir()) == []


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
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


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_rejects_path_budget_before_mutation(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "deep.zip"
    with zipfile.ZipFile(archive, "w") as zip_handle:
        info = zipfile.ZipInfo("root/deep/file.bin")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        zip_handle.writestr(info, b"x")

    destination = tmp_path / "extracted"
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="depth.*maximum",
    ):
        extract_production_release_archive(
            archive,
            destination,
            limits=ArchiveLimits(maximum_path_components=2),
        )
    assert not destination.exists()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_counts_deep_implicit_parents_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = tmp_path / "deep.zip"
    with zipfile.ZipFile(archive, "w") as zip_handle:
        zip_handle.writestr(
            deterministic_zip_info("wrapper/a/b/c/member.bin"),
            b"x",
        )
    monkeypatch.setattr(
        verifier_module,
        "_verify_reader",
        lambda *_args, **_kwargs: object(),
    )
    destination = tmp_path / "extracted"

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="node count",
    ):
        extract_production_release_archive(
            archive,
            destination,
            limits=ArchiveLimits(maximum_members=3),
        )

    assert not destination.exists()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_deduplicates_shared_but_counts_distinct_parents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = tmp_path / "parents.zip"
    with zipfile.ZipFile(archive, "w") as zip_handle:
        for relative in (
            "wrapper/shared/a.bin",
            "wrapper/shared/b.bin",
            "wrapper/distinct/c.bin",
        ):
            zip_handle.writestr(
                deterministic_zip_info(relative),
                b"x",
            )
    monkeypatch.setattr(
        verifier_module,
        "_verify_reader",
        lambda *_args, **_kwargs: object(),
    )
    destination = tmp_path / "extracted"

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="node count",
    ):
        extract_production_release_archive(
            archive,
            destination,
            limits=ArchiveLimits(maximum_members=4),
        )

    assert not destination.exists()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_counts_one_shared_parent_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = tmp_path / "shared.zip"
    with zipfile.ZipFile(archive, "w") as zip_handle:
        for relative in (
            "wrapper/shared/a.bin",
            "wrapper/shared/b.bin",
        ):
            zip_handle.writestr(
                deterministic_zip_info(relative),
                b"x",
            )
    verification = object()
    monkeypatch.setattr(
        verifier_module,
        "_verify_reader",
        lambda *_args, **_kwargs: verification,
    )
    monkeypatch.setattr(
        verifier_module,
        "verify_production_release_tree",
        lambda *_args, **_kwargs: verification,
    )
    destination = tmp_path / "extracted"

    observed = extract_production_release_archive(
        archive,
        destination,
        limits=ArchiveLimits(maximum_members=3),
    )

    assert observed == destination
    assert (destination / "shared/a.bin").read_bytes() == b"x"
    assert (destination / "shared/b.bin").read_bytes() == b"x"


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_rejects_invalid_contract_before_mutation(
    tmp_path: Path,
) -> None:
    root, _receipt = _tree(tmp_path)
    (root / "web/viewer/index.html").write_bytes(b"tampered")
    archive = tmp_path / "invalid-contract.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "extracted"

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="changed protected artifact",
    ):
        extract_production_release_archive(archive, destination)

    assert not destination.exists()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_detects_target_name_swap_at_final_seal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "extracted"
    moved = tmp_path / "held-extracted"
    original = release_fs.BoundDirectory.verify_child_identity

    def swap_before_identity(self, name, child):
        if name == destination.name and destination.exists():
            destination.rename(moved)
            destination.mkdir()
        return original(self, name, child)

    monkeypatch.setattr(
        release_fs.BoundDirectory,
        "verify_child_identity",
        swap_before_identity,
    )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="identity.*published=.*retained=",
    ) as raised:
        extract_production_release_archive(archive, destination)

    assert raised.value.published
    assert raised.value.retained
    assert moved.is_dir()
    assert destination.is_dir()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_rehashes_tree_after_member_rewrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "extracted"
    original = verifier_module.verify_production_release_tree

    def rewrite_then_verify(target, *, limits):
        rewritten = (
            Path(target) / "web/viewer/index.html"
        )
        original_bytes = rewritten.read_bytes()
        rewritten.write_bytes(b"X" + original_bytes[1:])
        return original(target, limits=limits)

    monkeypatch.setattr(
        verifier_module,
        "verify_production_release_tree",
        rewrite_then_verify,
    )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="changed protected artifact.*published=.*retained=",
    ) as raised:
        extract_production_release_archive(archive, destination)

    assert raised.value.published
    assert raised.value.retained
    assert destination.exists()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
def test_archive_extraction_rejects_source_tree_verification_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "extracted"
    original = verifier_module.verify_production_release_tree

    def mismatched_verify(target, *, limits):
        verified = original(target, limits=limits)
        return replace(verified, package_content_id="0" * 64)

    monkeypatch.setattr(
        verifier_module,
        "verify_production_release_tree",
        mismatched_verify,
    )
    with pytest.raises(
        ProductionReleaseVerificationError,
        match="source.*tree.*published=.*retained=",
    ) as raised:
        extract_production_release_archive(archive, destination)

    assert raised.value.published
    assert raised.value.retained
    assert destination.exists()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="append-only extraction is Linux-only",
)
@pytest.mark.parametrize("failure_type", (EOFError, NotImplementedError))
def test_archive_extraction_wraps_stream_failures_after_destination_creation(
    tmp_path: Path,
    monkeypatch,
    failure_type: type[Exception],
) -> None:
    """Stream failures after mkdir retain their cause and owned residue."""
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    parent = tmp_path / "extract-parent"
    parent.mkdir()
    sentinel = parent / "keep.txt"
    sentinel.write_text("parent identity sentinel", encoding="utf-8")
    destination = parent / "extracted"

    def fail_after_destination_creation(*_args, **_kwargs):
        assert destination.is_dir()
        raise failure_type("injected archive stream failure")

    monkeypatch.setattr(
        release_fs.BoundFile,
        "copy_from",
        fail_after_destination_creation,
    )

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="archive verification failed",
    ) as raised:
        extract_production_release_archive(archive, destination)

    assert isinstance(raised.value.__cause__, failure_type)
    assert destination.exists()
    assert raised.value.retained
    assert sentinel.read_text(encoding="utf-8") == "parent identity sentinel"
    assert parent.is_dir()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform == "linux",
    reason="non-Linux platform contract",
)
def test_archive_extraction_rejects_non_linux_before_mutation(
    tmp_path: Path,
) -> None:
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    destination = tmp_path / "extracted"

    with pytest.raises(
        ProductionReleaseVerificationError,
        match="private Linux builder",
    ):
        extract_production_release_archive(archive, destination)

    assert not destination.exists()
    assert verify_production_release_archive(archive).valid is True


# ---------------------------------------------------------------------------
# GLM-046: check-then-reopen and absolute-path-leak boundary matrix.
# These tests start RED and go GREEN after the minimal fix in
# production_release_verifier.py.  They cover:
#   - _stable_payload must open via os.open + os.fstat (single handle),
#     not Path.lstat + Path.open (check-then-reopen).
#   - _stable_payload errors must not echo absolute paths.
#   - verify_production_release_archive must open via os.open, not Path.open.
#   - No name-based lstat after the controlled read.
# stable_regular_file_digest (release_archive.py) has the same gap but is
# out of scope for GLM-046.
# ---------------------------------------------------------------------------


def test_stable_payload_does_not_reopen_receipt_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED→GREEN: _stable_payload must use os.open, not Path.open.

    Only receipt and checksums are checked because they are opened exclusively
    by _stable_payload.  Artifact files (including evidence) are also opened
    by stable_regular_file_digest (release_archive.py) which is out of scope.
    """

    root, _receipt = _tree(tmp_path)
    opened_names: list[str] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self.is_relative_to(root) and args and "rb" in args:
            opened_names.append(self.relative_to(root).as_posix())
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    verify_production_release_tree(root)

    payload_names = {
        name
        for name in opened_names
        if name in {PRODUCTION_RELEASE_NAME, CHECKSUMS_NAME}
    }
    assert not payload_names, (
        f"Path.open called for payload files (should use os.open): "
        f"{sorted(payload_names)}"
    )


def test_stable_payload_errors_must_not_leak_absolute_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED→GREEN: _stable_payload errors must use relative, not absolute."""

    root, _receipt = _tree(tmp_path)
    original_fstat = os.fstat
    call_count = {"n": 0}

    def drift_fstat(fd):
        result = original_fstat(fd)
        call_count["n"] += 1
        if call_count["n"] == 2:
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_mode=result.st_mode,
                st_size=result.st_size + 1,
                st_mtime_ns=result.st_mtime_ns,
                st_file_attributes=getattr(
                    result, "st_file_attributes", 0
                ),
            )
        return result

    monkeypatch.setattr(os, "fstat", drift_fstat)
    with pytest.raises(ProductionReleaseVerificationError) as exc:
        verify_production_release_tree(root)
    message = str(exc.value)
    assert str(root) not in message
    assert str(tmp_path) not in message


def test_archive_verifier_does_not_reopen_archive_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED→GREEN: verify_production_release_archive must use os.open."""

    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    opened: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == archive:
            opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)
    result = verify_production_release_archive(archive)

    assert result.valid is True
    assert not opened, "Path.open was called for the archive"


def test_archive_verifier_does_not_lstat_archive_after_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED→GREEN: no name-based lstat after the controlled read."""

    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    lstat_after_open: list[Path] = []
    original_lstat = Path.lstat
    opened = {"done": False}

    def tracking_lstat(self):
        if self == archive and opened["done"]:
            lstat_after_open.append(self)
        return original_lstat(self)

    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        result = original_open(self, *args, **kwargs)
        if self == archive:
            opened["done"] = True
        return result

    monkeypatch.setattr(Path, "lstat", tracking_lstat)
    monkeypatch.setattr(Path, "open", tracking_open)
    result = verify_production_release_archive(archive)

    assert result.valid is True
    assert not lstat_after_open, "source.lstat called after read"


# ---------------------------------------------------------------------------
# GLM-046 (continued): extract_production_release_archive check-then-reopen
# and OSError absolute-path-leak gaps.  These tests start RED and go GREEN
# after the minimal fix.  They are cross-platform: require_linux_mutation_support
# and open_bound_directory are stubbed so the open path is exercised without
# performing real mutation.
# ---------------------------------------------------------------------------


def _stub_mutation_gates(monkeypatch) -> None:
    """Stub the Linux-only gates so the open path runs on any platform."""
    monkeypatch.setattr(
        verifier_module,
        "require_linux_mutation_support",
        lambda: None,
    )

    def fail_bound_directory(*_args, **_kwargs):
        raise release_fs.ProductionReleaseFSError(
            "open_bound_directory stubbed for non-mutating test"
        )

    monkeypatch.setattr(
        verifier_module,
        "open_bound_directory",
        fail_bound_directory,
    )


def test_extraction_does_not_reopen_archive_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: extract_production_release_archive must use os.open, not Path.open."""
    _stub_mutation_gates(monkeypatch)

    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    opened: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == archive:
            opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    with pytest.raises(ProductionReleaseVerificationError):
        extract_production_release_archive(archive, tmp_path / "extracted")

    assert not opened, "Path.open was called for the source archive"


def test_extraction_does_not_lstat_archive_after_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: no name-based lstat for the source after the controlled open."""
    _stub_mutation_gates(monkeypatch)

    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    lstat_after_open: list[Path] = []
    open_seen = {"done": False}
    original_lstat = Path.lstat
    original_path_open = Path.open
    original_os_open = os.open

    def tracking_lstat(self):
        if self == archive and open_seen["done"]:
            lstat_after_open.append(self)
        return original_lstat(self)

    def tracking_path_open(self, *args, **kwargs):
        result = original_path_open(self, *args, **kwargs)
        if self == archive:
            open_seen["done"] = True
        return result

    def tracking_os_open(path, *args, **kwargs):
        result = original_os_open(path, *args, **kwargs)
        if isinstance(path, (str, Path)) and Path(path) == archive:
            open_seen["done"] = True
        return result

    monkeypatch.setattr(Path, "lstat", tracking_lstat)
    monkeypatch.setattr(Path, "open", tracking_path_open)
    monkeypatch.setattr(os, "open", tracking_os_open)

    with pytest.raises(ProductionReleaseVerificationError):
        extract_production_release_archive(archive, tmp_path / "extracted")

    assert not lstat_after_open, "source.lstat called after open"


def test_extraction_errors_must_not_leak_absolute_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: OSError messages must not echo absolute paths."""
    _stub_mutation_gates(monkeypatch)

    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    secret = str(tmp_path / "secret" / "leaked.txt")
    injected = OSError(13, "Permission denied", secret)

    def failing_preflight(*_args, **_kwargs):
        raise injected

    monkeypatch.setattr(
        verifier_module,
        "preflight_zip_central_directory",
        failing_preflight,
    )

    with pytest.raises(ProductionReleaseVerificationError) as exc:
        extract_production_release_archive(archive, tmp_path / "extracted")

    message = str(exc.value)
    assert str(injected) not in message, "OSError message leaked into error"
    assert "leaked.txt" not in message, "absolute path component leaked"


def test_archive_verifier_errors_must_not_leak_absolute_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: verify_production_release_archive OSError must not leak paths."""
    root, _receipt = _tree(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)

    secret = str(tmp_path / "secret" / "leaked.txt")
    injected = OSError(13, "Permission denied", secret)

    def failing_preflight(*_args, **_kwargs):
        raise injected

    monkeypatch.setattr(
        verifier_module,
        "preflight_zip_central_directory",
        failing_preflight,
    )

    with pytest.raises(ProductionReleaseVerificationError) as exc:
        verify_production_release_archive(archive)

    message = str(exc.value)
    assert str(injected) not in message, "OSError message leaked into error"
    assert "leaked.txt" not in message, "absolute path component leaked"
