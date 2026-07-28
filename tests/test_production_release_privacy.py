from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
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
    ProductionReleasePrivacyError,
    audit_production_release_privacy,
    privacy_report_bytes,
)
from pipeline.release_archive import canonical_json_bytes
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
        if not mutated and Path(stream.name) == target:
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
