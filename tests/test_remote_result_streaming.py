"""RED tests for bounded-memory streaming result-bundle verify/extract (J1).

These tests prove that large result-bundle members (e.g., PLY > 8 MiB)
are verified and extracted in bounded chunks (<= 1 MiB) without loading
the entire member into memory via ``archive.read()`` or storing all
payloads in a ``dict[str, bytes]`` before extraction.
"""

from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

from pipeline.remote_shell_executor import (
    RemoteResultBundleError,
    RemoteResultBundleManifest,
    RemoteResultBundleMember,
    canonical_remote_result_manifest_bytes,
    verify_production_remote_result_bundle,
    verify_remote_result_bundle,
)

_ONE_MIB = 1024 * 1024
_CONTAINER_IDENTITY = "registry.example/nantai@sha256:" + ("c" * 64)
_REQUEST_SHA256 = "a" * 64
_TRAINING_BUNDLE_SHA256 = "d" * 64


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# v1 archive helpers
# ---------------------------------------------------------------------------


def _v1_members_by_path(ply_payload: bytes) -> dict[str, bytes]:
    return {
        "container-identity.txt": (
            _CONTAINER_IDENTITY + "\n"
        ).encode("ascii"),
        "dataparser_transforms.json": (
            b'{"scale":1.0,"transform":'
            b"[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n"
        ),
        "operator-intent-config.yml": b"config\n",
        "point_cloud.ply": ply_payload,
        "training-request.json": b"{}\n",
        "training-result.json": b"{}\n",
        "training.log": b"log\n",
        "worker.stderr.log": b"",
        "worker.stdout.log": b"container completed\n",
    }


def _write_v1_archive(
    path: Path,
    *,
    ply_payload: bytes,
    members_override: dict[str, bytes] | None = None,
    manifest_members_override: dict[str, tuple[int, str]] | None = None,
) -> None:
    """Write a v1 result-bundle archive with ZIP_STORED."""
    members_by_path = members_override or _v1_members_by_path(ply_payload)
    members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for name, payload in sorted(members_by_path.items())
    )
    if manifest_members_override:
        members = tuple(
            RemoteResultBundleMember(
                path=name,
                byte_length=size,
                sha256=sha,
            )
            for name, (size, sha) in sorted(
                manifest_members_override.items()
            )
        )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=_REQUEST_SHA256,
        training_bundle_sha256=_TRAINING_BUNDLE_SHA256,
        container_identity=_CONTAINER_IDENTITY,
        members=members,
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            archive.writestr(name, payload)


def _verify_v1(path: Path, **overrides):
    kwargs = {
        "expected_job_id": "job-expected",
        "expected_attempt_id": "attempt-expected",
        "expected_request_sha256": _REQUEST_SHA256,
        "expected_training_bundle_sha256": _TRAINING_BUNDLE_SHA256,
        "expected_container_identity": _CONTAINER_IDENTITY,
    }
    kwargs.update(overrides)
    return verify_remote_result_bundle(path, **kwargs)


# ---------------------------------------------------------------------------
# v2 archive helpers (import from test_real_scene_import)
# ---------------------------------------------------------------------------


def _load_rsi_helpers():
    spec = importlib.util.spec_from_file_location(
        "_rsi_j1_helpers",
        Path(__file__).resolve().parent / "test_real_scene_import.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rsi = _load_rsi_helpers()
_write_production_training_stage = _rsi._write_production_training_stage
_write_production_closure_evidence = _rsi._write_production_closure_evidence


def _build_valid_v2_archive(
    tmp_path: Path,
    *,
    ply_count: int = 200_000,
) -> tuple[Path, dict[str, str]]:
    """Build a fully valid v2 archive with a large PLY.

    Returns (archive_path, expected_kwargs) for verify.
    """
    from pipeline.production_runtime_evidence import (
        load_production_runtime_measurement_bytes,
    )
    from pipeline.remote_shell_executor import (
        build_production_remote_result_bundle,
    )
    from pipeline.training_provenance import request_canonical_sha256

    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=ply_count,
        include_production_closure=False,
    )
    _write_production_closure_evidence(training_root, fixture)

    result_root = training_root / "remote-result"
    for relative in (
        "result-bundle-manifest.json",
        "result-bundle.zip",
        "render-evaluation/decision.json",
        "production-training-closure.json",
    ):
        (result_root / relative).unlink(missing_ok=True)

    measurement = load_production_runtime_measurement_bytes(
        (result_root / "production-runtime" / "measurement.json").read_bytes()
    )

    archive = training_root / "result-bundle.zip"
    build_production_remote_result_bundle(
        result_root=result_root,
        output_path=archive,
        job_id=fixture.attempt.job_id,
        attempt_id=fixture.attempt.attempt_id,
        request_sha256=request_canonical_sha256(fixture.request),
        training_bundle_sha256=fixture.verified_bundle.bundle_sha256,
        container_instance_id=measurement.environment.container_instance_id,
        container_identity=measurement.environment.observed_container_identity,
        remote_target_sha256=measurement.remote_target_sha256,
        durable_job_ref_sha256=measurement.durable_job_ref_sha256,
        workspace_identity_sha256=measurement.workspace_identity_sha256,
    )

    expected = {
        "expected_job_id": fixture.attempt.job_id,
        "expected_attempt_id": fixture.attempt.attempt_id,
        "expected_request_sha256": request_canonical_sha256(
            fixture.request
        ),
        "expected_training_bundle_sha256": (
            fixture.verified_bundle.bundle_sha256
        ),
        "expected_container_instance_id": (
            measurement.environment.container_instance_id
        ),
        "expected_container_identity": (
            measurement.environment.observed_container_identity
        ),
        "expected_remote_target_sha256": measurement.remote_target_sha256,
        "expected_durable_job_ref_sha256": (
            measurement.durable_job_ref_sha256
        ),
        "expected_workspace_identity_sha256": (
            measurement.workspace_identity_sha256
        ),
    }
    return archive, expected


# ---------------------------------------------------------------------------
# Read-size observer
# ---------------------------------------------------------------------------


class _ArchiveObserver:
    """Monkeypatch zipfile.ZipFile to track read() and open() calls."""

    def __init__(self, monkeypatch):
        self.read_calls: list[str] = []
        self.opened_members: list[str] = []
        self.stream_read_sizes: list[int] = []

        original_read = zipfile.ZipFile.read
        original_open = zipfile.ZipFile.open

        def tracking_read(self_, name, *args, **kwargs):
            self.read_calls.append(
                name if isinstance(name, str) else name.filename
            )
            return original_read(self_, name, *args, **kwargs)

        def tracking_open(self_, name, *args, **kwargs):
            stream = original_open(self_, name, *args, **kwargs)
            member_name = name if isinstance(name, str) else name.filename
            self.opened_members.append(member_name)
            if member_name == "point_cloud.ply":
                original_stream_read = stream.read

                def tracking_stream_read(size=-1):
                    self.stream_read_sizes.append(size)
                    return original_stream_read(size)

                stream.read = tracking_stream_read
            return stream

        monkeypatch.setattr(zipfile.ZipFile, "read", tracking_read)
        monkeypatch.setattr(zipfile.ZipFile, "open", tracking_open)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_legacy_result_verifier_streams_large_ply_in_bounded_chunks(
    tmp_path,
    monkeypatch,
):
    """v1 verify must stream large PLY in <= 1 MiB chunks."""
    ply_payload = b"\x00" * (8 * _ONE_MIB + 1)
    archive = tmp_path / "v1.zip"
    _write_v1_archive(archive, ply_payload=ply_payload)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    observer = _ArchiveObserver(monkeypatch)

    verified = _verify_v1(archive, staging_dir=staging_dir)

    # Large PLY was NOT read via archive.read()
    assert "point_cloud.ply" not in observer.read_calls

    # Large PLY WAS opened for streaming
    assert "point_cloud.ply" in observer.opened_members

    # All stream reads were <= 1 MiB
    assert all(size <= _ONE_MIB for size in observer.stream_read_sizes)
    assert any(size >= _ONE_MIB for size in observer.stream_read_sizes)

    # PLY was extracted to staging
    ply_staging = staging_dir / "point_cloud.ply"
    assert ply_staging.exists()
    assert ply_staging.stat().st_size == len(ply_payload)
    assert _sha(ply_staging.read_bytes()) == _sha(ply_payload)

    # Large PLY is NOT in member_bytes
    assert "point_cloud.ply" not in verified.member_bytes

    # Small members ARE in member_bytes
    assert "training.log" in verified.member_bytes
    assert "container-identity.txt" in verified.member_bytes


def test_production_result_verifier_streams_large_ply_in_bounded_chunks(
    tmp_path,
    monkeypatch,
):
    """v2 verify must stream large PLY in <= 1 MiB chunks."""
    archive, expected = _build_valid_v2_archive(tmp_path)
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    observer = _ArchiveObserver(monkeypatch)

    verified = verify_production_remote_result_bundle(
        archive,
        staging_dir=staging_dir,
        **expected,
    )

    # Large PLY was NOT read via archive.read()
    assert "point_cloud.ply" not in observer.read_calls

    # Large PLY WAS opened for streaming
    assert "point_cloud.ply" in observer.opened_members

    # All stream reads were <= 1 MiB
    assert all(size <= _ONE_MIB for size in observer.stream_read_sizes)
    assert any(size >= _ONE_MIB for size in observer.stream_read_sizes)

    # PLY was extracted to staging
    ply_staging = staging_dir / "point_cloud.ply"
    assert ply_staging.exists()
    assert ply_staging.stat().st_size > 8 * _ONE_MIB

    # Large PLY is NOT in member_bytes
    assert "point_cloud.ply" not in verified.member_bytes

    # Small members ARE in member_bytes
    assert "training.log" in verified.member_bytes
    assert (
        "production-runtime/decision.json" in verified.member_bytes
    )


def test_streaming_result_member_rejects_truncation(tmp_path, monkeypatch):
    """Truncated PLY (fewer bytes than manifest claims) must fail closed."""
    full_payload = b"\x00" * (8 * _ONE_MIB + 1)
    # Manifest claims full size, but archive member is truncated
    truncated = full_payload[: len(full_payload) // 2]

    members_by_path = _v1_members_by_path(full_payload)
    members_by_path["point_cloud.ply"] = truncated

    archive = tmp_path / "truncated.zip"
    # Build manifest with full_payload SHA/size, but write truncated bytes
    _write_v1_archive(
        archive,
        ply_payload=full_payload,
        members_override=members_by_path,
    )
    # Rebuild archive: manifest references full_payload, member has truncated
    members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(full_payload) if name == "point_cloud.ply"
            else len(payload),
            sha256=(
                _sha(full_payload) if name == "point_cloud.ply"
                else _sha(payload)
            ),
        )
        for name, payload in sorted(members_by_path.items())
    )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=_REQUEST_SHA256,
        training_bundle_sha256=_TRAINING_BUNDLE_SHA256,
        container_identity=_CONTAINER_IDENTITY,
        members=members,
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            zf.writestr(name, payload)

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    with pytest.raises(RemoteResultBundleError, match="sha256/size mismatch"):
        _verify_v1(archive, staging_dir=staging_dir)

    # Staging must not contain a partial PLY
    assert not (staging_dir / "point_cloud.ply").exists()


def test_streaming_result_member_rejects_sha_or_size_mismatch(
    tmp_path, monkeypatch
):
    """PLY whose SHA or size differs from manifest must fail closed."""
    real_payload = b"\x00" * (8 * _ONE_MIB + 1)
    tampered_payload = b"\x01" * (8 * _ONE_MIB + 1)

    members_by_path = _v1_members_by_path(real_payload)
    members_by_path["point_cloud.ply"] = tampered_payload

    archive = tmp_path / "mismatch.zip"
    # Manifest claims real_payload SHA/size, but archive has tampered
    members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=(
                len(real_payload) if name == "point_cloud.ply"
                else len(payload)
            ),
            sha256=(
                _sha(real_payload) if name == "point_cloud.ply"
                else _sha(payload)
            ),
        )
        for name, payload in sorted(members_by_path.items())
    )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=_REQUEST_SHA256,
        training_bundle_sha256=_TRAINING_BUNDLE_SHA256,
        container_identity=_CONTAINER_IDENTITY,
        members=members,
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            zf.writestr(name, payload)

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    with pytest.raises(RemoteResultBundleError, match="sha256/size mismatch"):
        _verify_v1(archive, staging_dir=staging_dir)

    assert not (staging_dir / "point_cloud.ply").exists()


def test_streaming_result_extraction_leaves_no_destination_on_failure(
    tmp_path,
    monkeypatch,
):
    """On verification failure, staging must be cleaned and destination absent."""
    real_payload = b"\x00" * (8 * _ONE_MIB + 1)
    # Tamper: manifest SHA is wrong
    members_by_path = _v1_members_by_path(real_payload)

    archive = tmp_path / "bad.zip"
    members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(payload),
            sha256=(
                "0" * 64 if name == "point_cloud.ply"
                else _sha(payload)
            ),
        )
        for name, payload in sorted(members_by_path.items())
    )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=_REQUEST_SHA256,
        training_bundle_sha256=_TRAINING_BUNDLE_SHA256,
        container_identity=_CONTAINER_IDENTITY,
        members=members,
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(members_by_path.items()):
            zf.writestr(name, payload)

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    with pytest.raises(RemoteResultBundleError):
        _verify_v1(archive, staging_dir=staging_dir)

    # Destination (staging dir) must not contain any extracted members
    # from the failed verification.  No partial PLY, no partial small members.
    assert not (staging_dir / "point_cloud.ply").exists()
    assert not (staging_dir / "training.log").exists()
    assert not (staging_dir / "container-identity.txt").exists()


def test_streaming_result_extraction_rejects_member_path_or_type_drift(
    tmp_path,
    monkeypatch,
):
    """ZIP members with path traversal or non-regular type must be rejected
    before any file is written to the staging directory."""
    import stat as stat_module

    ply_payload = b"\x00" * (8 * _ONE_MIB + 1)

    # --- Case 1: path traversal member in archive (not in manifest) ---
    archive_traversal = tmp_path / "traversal.zip"
    base_members = _v1_members_by_path(ply_payload)
    manifest_members = tuple(
        RemoteResultBundleMember(
            path=name,
            byte_length=len(payload),
            sha256=_sha(payload),
        )
        for name, payload in sorted(base_members.items())
    )
    manifest = RemoteResultBundleManifest(
        job_id="job-expected",
        attempt_id="attempt-expected",
        request_sha256=_REQUEST_SHA256,
        training_bundle_sha256=_TRAINING_BUNDLE_SHA256,
        container_identity=_CONTAINER_IDENTITY,
        members=manifest_members,
    )
    with zipfile.ZipFile(
        archive_traversal, "w", compression=zipfile.ZIP_STORED
    ) as zf:
        zf.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(base_members.items()):
            zf.writestr(name, payload)
        # Extra traversal member not listed in manifest
        zf.writestr("../escape.ply", b"escape\n")

    staging_dir = tmp_path / "staging-traversal"
    staging_dir.mkdir()

    with pytest.raises(RemoteResultBundleError, match="portable|differ|traversal"):
        _verify_v1(archive_traversal, staging_dir=staging_dir)

    # No file escaped the staging directory
    assert not (tmp_path / "escape.ply").exists()
    assert not (staging_dir / "point_cloud.ply").exists()

    # --- Case 2: symlink member type (non-regular) ---
    archive_symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(
        archive_symlink, "w", compression=zipfile.ZIP_STORED
    ) as zf:
        zf.writestr(
            "result-bundle-manifest.json",
            canonical_remote_result_manifest_bytes(manifest),
        )
        for name, payload in sorted(base_members.items()):
            info = zipfile.ZipInfo(filename=name)
            info.compress_type = zipfile.ZIP_STORED
            if name == "point_cloud.ply":
                # Mark as symlink (S_IFLNK in external_attr)
                info.external_attr = (
                    stat_module.S_IFLNK | 0o777
                ) << 16
            else:
                info.external_attr = (
                    stat_module.S_IFREG | 0o600
                ) << 16
            zf.writestr(info, payload)

    staging_dir2 = tmp_path / "staging-symlink"
    staging_dir2.mkdir()

    with pytest.raises(RemoteResultBundleError, match="type|not allowed"):
        _verify_v1(archive_symlink, staging_dir=staging_dir2)

    assert not (staging_dir2 / "point_cloud.ply").exists()
