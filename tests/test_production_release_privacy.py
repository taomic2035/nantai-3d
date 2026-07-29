from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.production_release_privacy as privacy_module
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
)
from pipeline.production_release_privacy import (
    PRIVACY_SCAN_CHUNK_BYTES,
    ProductionPrivacyReport,
    ProductionReleasePrivacyError,
    audit_production_release_privacy,
    privacy_report_bytes,
    publish_privacy_report,
)
from pipeline.release_archive import ArchiveLimits, canonical_json_bytes
from tests.production_release_fixtures import (
    write_modeled_production_archive,
    write_modeled_production_tree,
)


def _write_policy(path: Path, *needles: bytes) -> None:
    path.write_bytes(
        canonical_json_bytes(
            {
                "needles": [
                    {
                        "encoding": "base64",
                        "value": base64.b64encode(needle).decode("ascii"),
                    }
                    for needle in needles
                ],
                "schema": "nantai.production-privacy-policy.v1",
            }
        )
    )


def test_privacy_tree_walk_counts_empty_directories_as_members(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    root.mkdir()
    (root / "public.txt").write_text("public", encoding="utf-8")
    for index in range(3):
        (root / f"empty-{index}").mkdir()

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="member count",
    ):
        privacy_module._release_files(
            root,
            limits=ArchiveLimits(maximum_members=1),
        )


def test_privacy_tree_walk_stops_streaming_at_member_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "wide"
    root.mkdir()
    for index in range(20):
        (root / f"{index:02d}.txt").write_text("x", encoding="utf-8")
    original_scandir = privacy_module.os.scandir
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
        privacy_module.os,
        "scandir",
        TrackingScandir,
    )
    with pytest.raises(
        ProductionReleasePrivacyError,
        match="member count",
    ):
        privacy_module._release_files(
            root,
            limits=ArchiveLimits(maximum_members=3),
        )

    assert yielded == 4


def test_privacy_tree_walk_fails_closed_on_scandir_iteration_error(
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
        privacy_module.os,
        "scandir",
        lambda _path: FailingScandir(),
    )
    with pytest.raises(
        ProductionReleasePrivacyError,
        match="unavailable",
    ):
        privacy_module._release_files(root, limits=ArchiveLimits())

    assert closed is True


def test_privacy_tree_walk_fails_closed_on_lstat_error(
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
        ProductionReleasePrivacyError,
        match="unavailable",
    ):
        privacy_module._release_files(root, limits=ArchiveLimits())


def _replace_protected_payload(
    root: Path,
    receipt: dict[str, object],
    relative: str,
    payload: bytes,
) -> None:
    root.joinpath(*relative.split("/")).write_bytes(payload)
    for artifact in receipt["artifacts"]:
        if artifact["path"] == relative:
            artifact["bytes"] = len(payload)
            artifact["sha256"] = hashlib.sha256(payload).hexdigest()
            break
    else:
        raise AssertionError(f"missing fixture artifact: {relative}")

    unsigned = copy.deepcopy(receipt)
    unsigned["package"]["content_id"] = None
    receipt["package"]["content_id"] = hashlib.sha256(
        canonical_json_bytes(unsigned)
    ).hexdigest()
    receipt_payload = canonical_json_bytes(receipt)
    (root / PRODUCTION_RELEASE_NAME).write_bytes(receipt_payload)
    checksum_rows = [
        f"{artifact['sha256']}  {artifact['path']}\n"
        for artifact in receipt["artifacts"]
    ]
    checksum_rows.append(
        f"{hashlib.sha256(receipt_payload).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    (root / CHECKSUMS_NAME).write_bytes(
        "".join(sorted(checksum_rows)).encode("ascii")
    )


def test_clean_verified_tree_produces_non_promoting_report(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    report = audit_production_release_privacy(root, policy)

    assert report.valid is True
    assert report.package_content_id == receipt["package"]["content_id"]
    assert report.finding_count == 0
    assert report.findings == ()
    assert report.scene_trust_effect == "none"


def test_private_binary_needle_is_found_across_chunk_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    needle = b"\x00PRIVATE\xff"
    payload = b"x" * (PRIVACY_SCAN_CHUNK_BYTES - 4) + needle + b"tail"
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        payload,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, needle)

    report = audit_production_release_privacy(root, policy)

    assert report.valid is False
    assert report.finding_count == 1
    assert report.findings[0].category == "private-policy-needle"
    assert report.findings[0].path == "web/viewer/index.html"


def test_unverified_tree_is_rejected_before_any_privacy_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    (root / "web/viewer/index.html").write_bytes(b"changed")
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
    scanned: list[str] = []
    monkeypatch.setattr(
        privacy_module,
        "_scan_file",
        lambda *_args: scanned.append("scanned"),
    )

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="verification failed before",
    ):
        audit_production_release_privacy(root, policy)
    assert scanned == []


def test_extra_file_is_rejected_before_any_privacy_scan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    (root / "web/viewer/extra.txt").write_bytes(b"not declared")
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="verification failed before",
    ):
        audit_production_release_privacy(root, policy)


def test_nonregular_file_is_rejected_before_privacy_scan(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    fifo = root / "web/viewer/private-fifo"
    try:
        os.mkfifo(fifo)
    except OSError:
        pytest.skip("FIFO creation is unavailable")
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="verification failed before",
    ):
        audit_production_release_privacy(root, policy)


@pytest.mark.parametrize(
    ("payload", "category"),
    (
        (b"C:\\Users\\alice\\private.txt", "absolute-filesystem-path"),
        (b"/home/alice/private.txt", "absolute-filesystem-path"),
        (b"-----BEGIN PRIVATE KEY-----", "pem-private-key"),
        (b"Authorization: Bearer secret-value", "credential-marker"),
    ),
)
def test_builtin_sensitive_markers_fail_closed(
    tmp_path: Path,
    payload: bytes,
    category: str,
) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        payload,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    report = audit_production_release_privacy(root, policy)

    assert report.valid is False
    assert category in {finding.category for finding in report.findings}


def test_noncanonical_policy_is_rejected_without_echoing_private_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    policy = tmp_path / "operator-private-policy.json"
    policy.write_text(
        '{"schema": "nantai.production-privacy-policy.v1", "needles": []}',
        encoding="utf-8",
    )

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="canonical JSON",
    ) as error:
        audit_production_release_privacy(root, policy)
    assert str(policy) not in str(error.value)


def test_policy_reader_rejects_reparse_ancestor_before_leaf_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ancestor = tmp_path / "operator-private-alias"
    ancestor.mkdir()
    policy = ancestor / "policy.json"
    secret_needle = b"operator-private-needle"
    _write_policy(policy, secret_needle)
    observed = ancestor.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        if path == policy:
            raise AssertionError(
                "policy leaf was accessed through reparse ancestor"
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
        ProductionReleasePrivacyError,
        match="location is unsafe",
    ) as captured:
        privacy_module._stable_policy_bytes(policy)

    message = str(captured.value)
    assert str(policy) not in message
    assert secret_needle.decode("ascii") not in message


def test_policy_reader_rechecks_ancestors_after_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    policy = tmp_path / "policy.json"
    _write_policy(policy, b"operator-private-needle")
    checks = 0

    def redirect_after_read(_root: Path, _leaf: Path):
        nonlocal checks
        checks += 1
        return None if checks == 1 else policy.parent

    monkeypatch.setattr(
        privacy_module,
        "first_linklike_path",
        redirect_after_read,
    )

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="location is unsafe",
    ):
        privacy_module._stable_policy_bytes(policy)

    assert checks == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_policy_junction_ancestor_is_rejected_without_report_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    private_source = tmp_path / "operator-private-source"
    private_source.mkdir()
    secret_needle = b"operator-private-needle"
    _write_policy(private_source / "policy.json", secret_needle)
    alias = tmp_path / "operator-private-alias"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(private_source)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    policy = alias / "policy.json"
    destination = tmp_path / "privacy-report.json"
    try:
        with pytest.raises(
            ProductionReleasePrivacyError,
            match="location is unsafe",
        ) as captured:
            report = audit_production_release_privacy(root, policy)
            publish_privacy_report(report, destination)

        message = str(captured.value)
        assert str(policy) not in message
        assert secret_needle.decode("ascii") not in message
        assert not destination.exists()
    finally:
        removed = subprocess.run(
            ["cmd", "/c", "rmdir", str(alias)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert removed.returncode == 0, removed.stderr


def test_noncanonical_base64_needle_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    policy = tmp_path / "private-policy.json"
    policy.write_bytes(
        canonical_json_bytes(
            {
                "needles": [
                    {
                        "encoding": "base64",
                        # Decodes to 12345678 but has noncanonical pad bits.
                        "value": "MTIzNDU2Nzh=",
                    }
                ],
                "schema": "nantai.production-privacy-policy.v1",
            }
        )
    )

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="needle is invalid",
    ):
        audit_production_release_privacy(root, policy)


def test_public_schema_field_names_are_not_credentials(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    payload = canonical_json_bytes(
        {
            "properties": {
                "api_key": {"type": "string"},
                "password": {"type": "string"},
            },
            "type": "object",
        }
    )
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        payload,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    report = audit_production_release_privacy(root, policy)

    assert report.valid is True
    assert report.findings == ()


def test_symlink_is_rejected_before_privacy_scan(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    link = root / "web/viewer/private-link"
    try:
        link.symlink_to(root / "web/viewer/index.html")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="verification failed before",
    ):
        audit_production_release_privacy(root, policy)


def test_junction_is_rejected_before_privacy_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Windows junction (reparse point) must be rejected like a symlink."""
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    junction = root / "web/viewer/private-junction"
    junction.mkdir()
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == junction or original(self),
        raising=False,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="verification failed before",
    ):
        audit_production_release_privacy(root, policy)


def test_reparse_root_is_rejected_before_privacy_scan_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
    observed = root.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == root:
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
        ProductionReleasePrivacyError,
        match="unavailable or unsafe",
    ):
        audit_production_release_privacy(root, policy)


@pytest.mark.parametrize("target_kind", ("tree", "archive"))
def test_reparse_target_ancestor_is_rejected_before_privacy_scan(
    tmp_path: Path,
    monkeypatch,
    target_kind: str,
) -> None:
    ancestor = tmp_path / "alias"
    ancestor.mkdir()
    tree = ancestor / "runtime"
    write_modeled_production_tree(tree)
    target = tree
    if target_kind == "archive":
        target = ancestor / "runtime.zip"
        write_modeled_production_archive(tree, target)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
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
        ProductionReleasePrivacyError,
        match="unsafe|verification failed before",
    ):
        audit_production_release_privacy(target, policy)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_privacy_cleanup_does_not_follow_swapped_parent_junction(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    policy = tmp_path / "policy.json"
    _write_policy(policy, b"private-canonical-needle")
    report = audit_production_release_privacy(tree, policy)
    parent = tmp_path / "publish-parent"
    parent.mkdir()
    destination = parent / "privacy.json"
    with pytest.raises(
        ProductionReleasePrivacyError,
        match="private Linux builder",
    ):
        publish_privacy_report(report, destination)
    assert not destination.exists()


def test_mid_read_drift_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    payload = b"x" * (PRIVACY_SCAN_CHUNK_BYTES + 17)
    target = root / "web/viewer/index.html"
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        payload,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
    original_read = privacy_module._read_scan_chunk
    mutated = False

    def mutate_after_first_target_chunk(stream):
        nonlocal mutated
        chunk = original_read(stream)
        if not mutated:
            stream_stat = os.fstat(stream.fileno())
            target_stat = target.lstat()
            if (
                stream_stat.st_dev == target_stat.st_dev
                and stream_stat.st_ino == target_stat.st_ino
            ):
                mutated = True
                target.write_bytes(payload + b"changed")
        return chunk

    monkeypatch.setattr(
        privacy_module,
        "_read_scan_chunk",
        mutate_after_first_target_chunk,
    )

    with pytest.raises(ProductionReleasePrivacyError, match="changed during"):
        audit_production_release_privacy(root, policy)
    assert mutated is True


def test_oversized_scan_chunk_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    payload = b"x" * (PRIVACY_SCAN_CHUNK_BYTES + 1)
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        payload,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
    monkeypatch.setattr(
        privacy_module,
        "_read_scan_chunk",
        lambda stream: stream.read(PRIVACY_SCAN_CHUNK_BYTES + 1),
    )

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="chunk exceeded",
    ):
        audit_production_release_privacy(root, policy)


def test_verified_archive_is_extracted_then_privacy_scanned(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    report = audit_production_release_privacy(archive, policy)

    assert report.valid is True
    assert report.package_content_id == receipt["package"]["content_id"]


def test_verified_archive_privacy_scan_is_read_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    archive = tmp_path / "runtime.zip"
    write_modeled_production_archive(root, archive)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("privacy scan must not extract")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(
        privacy_module,
        "extract_production_release_archive",
        forbidden,
        raising=False,
    )

    assert audit_production_release_privacy(archive, policy).valid is True


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform == "linux",
    reason="non-Linux platform contract",
)
def test_privacy_report_publication_rejects_non_linux_before_creation(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "privacy-report.json"
    report = ProductionPrivacyReport(
        schema="nantai.production-privacy-audit.v1",
        valid=True,
        package_content_id="package-" + "a" * 64,
        finding_count=0,
        findings=(),
        scene_trust_effect="none",
    )

    with pytest.raises(
        ProductionReleasePrivacyError,
        match="private Linux builder",
    ):
        publish_privacy_report(report, destination)

    assert not destination.exists()


def test_report_bytes_never_echo_private_needle_or_absolute_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-workspace" / "runtime"
    receipt = write_modeled_production_tree(root)
    needle = b"private-operator-secret"
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        needle,
    )
    policy = tmp_path / "private-workspace" / "policy.json"
    _write_policy(policy, needle)

    payload = privacy_report_bytes(
        audit_production_release_privacy(root, policy)
    )
    parsed = json.loads(payload)

    assert needle not in payload
    assert str(root).encode() not in payload
    assert str(policy).encode() not in payload
    assert parsed == {
        "finding_count": 1,
        "findings": [
            {
                "category": "private-policy-needle",
                "path": "web/viewer/index.html",
            }
        ],
        "package_content_id": receipt["package"]["content_id"],
        "scene_trust_effect": "none",
        "schema": "nantai.production-privacy-audit.v1",
        "valid": False,
    }


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="privacy report publication is Linux-only",
)
def test_cli_writes_machine_report_and_returns_nonzero_for_finding(
    tmp_path: Path,
    capsys,
) -> None:
    from scripts.audit_production_release_privacy import main

    root = tmp_path / "runtime"
    receipt = write_modeled_production_tree(root)
    needle = b"private-operator-secret"
    _replace_protected_payload(
        root,
        receipt,
        "web/viewer/index.html",
        needle,
    )
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, needle)
    output = tmp_path / "privacy-report.json"

    assert (
        main(
            [
                str(root),
                "--policy",
                str(policy),
                "--report",
                str(output),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "1 finding" in captured.err
    assert needle.decode() not in captured.err
    assert str(policy) not in captured.err
    assert privacy_report_bytes(
        audit_production_release_privacy(root, policy)
    ) == output.read_bytes()


@pytest.mark.production_mutation
@pytest.mark.skipif(
    sys.platform != "linux",
    reason="privacy report publication is Linux-only",
)
def test_cli_clean_report_returns_zero(tmp_path: Path, capsys) -> None:
    from scripts.audit_production_release_privacy import main

    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
    output = tmp_path / "privacy-report.json"

    assert (
        main(
            [
                str(root),
                "--policy",
                str(policy),
                "--report",
                str(output),
            ]
        )
        == 0
    )
    assert "privacy audit passed" in capsys.readouterr().out
    assert output.is_file()


def test_cli_refuses_report_inside_verified_release_tree(
    tmp_path: Path,
    capsys,
) -> None:
    from scripts.audit_production_release_privacy import main

    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")
    output = root / "privacy-report.json"

    assert (
        main(
            [
                str(root),
                "--policy",
                str(policy),
                "--report",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
    assert str(root) not in capsys.readouterr().err


def test_privacy_release_files_rejects_scandir_toctou_root_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _release_files must re-stat root after os.scandir.

    Without a post-scan identity recheck, a TOCTOU swap of the root directory to
    a reparse point between the pre-scan lstat and os.scandir would cause the
    iterator to follow the redirect and walk an untrusted tree.
    """

    root = tmp_path / "runtime"
    write_modeled_production_tree(root)

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
        ProductionReleasePrivacyError,
        match="changed during scan",
    ):
        privacy_module._release_files(root)


def test_privacy_release_files_rejects_scandir_toctou_subdirectory_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _release_files must re-stat each subdirectory after scandir.

    Each subdirectory is lstat'd, then opened via os.scandir by name.  Without
    a post-scan identity recheck, a TOCTOU swap to a reparse point between
    lstat and scandir would cause the iterator to follow the redirect.
    """

    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
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
        ProductionReleasePrivacyError,
        match="changed during scan",
    ):
        privacy_module._release_files(root)


def test_stable_policy_bytes_does_not_reopen_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _stable_policy_bytes must use os.open, not Path.open.

    Path.open reopens by name after the pre-open lstat, which is a
    check-then-reopen TOCTOU that follows symlinks.  Verified bytes must
    come from a single fd opened with O_NOFOLLOW.
    """

    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    called: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == policy:
            called.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    privacy_module._stable_policy_bytes(policy)

    assert not called, "Path.open was called (should use os.open)"


def test_scan_file_does_not_reopen_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _scan_file must use os.open, not Path.open.

    Path.open reopens by name after the pre-open lstat, which is a
    check-then-reopen TOCTOU that follows symlinks.  Privacy scan input
    must come from a single fd opened with O_NOFOLLOW.
    """

    root = tmp_path / "runtime"
    write_modeled_production_tree(root)
    target = root / "web/viewer/index.html"
    _write_policy(tmp_path / "private-policy.json", b"private-canonical-needle")
    policy = privacy_module._load_policy(tmp_path / "private-policy.json")

    called: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == target:
            called.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    privacy_module._scan_file("web/viewer/index.html", target, policy)

    assert not called, "Path.open was called (should use os.open)"


def test_audit_verified_archive_does_not_reopen_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _audit_verified_archive must use os.open, not Path.open.

    Path.open reopens by name after the pre-open lstat, which is a
    check-then-reopen TOCTOU that follows symlinks.  Archive privacy scan
    input must come from a single fd opened with O_NOFOLLOW.
    """

    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    archive = tmp_path / "release.zip"
    write_modeled_production_archive(tree, archive)
    policy = tmp_path / "private-policy.json"
    _write_policy(policy, b"private-canonical-needle")

    called: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == archive:
            called.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    privacy_module._audit_verified_archive(archive, policy)

    assert not called, "Path.open was called (should use os.open)"
