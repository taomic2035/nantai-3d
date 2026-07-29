from __future__ import annotations

import hashlib
import http.client
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import pipeline.real_scene_import as import_module
import pipeline.viewer_inputs as viewer_inputs_module
import pipeline.viewer_session as session_module
from pipeline.real_dataset import (
    LocalCaptureSource,
    canonical_model_bytes,
)
from pipeline.real_scene_import import import_real_scene
from pipeline.real_scene_runner import (
    StageArtifactBinding,
    StageReceipt,
    canonical_stage_receipt_bytes,
    resolve_latest_production_import,
)
from pipeline.viewer_acceptance import (
    ViewerCameraSetV2,
    ViewerPerformancePolicy,
    canonical_viewer_performance_policy_bytes,
    load_viewer_camera_set_bytes,
)
from pipeline.viewer_inputs import (
    ViewerInputMaterializationError,
    materialize_production_viewer_inputs,
)
from pipeline.viewer_session import (
    ViewerSessionOptions,
    run_production_viewer_session,
)
from tests.test_real_scene_import import (
    _patch_production_bundle,
    _write_control_points,
    _write_production_training_stage,
)


def test_materializer_derives_three_content_bound_registered_camera_poses(
    tmp_path,
    monkeypatch,
):
    training_root = tmp_path / "training"
    fixture = _write_production_training_stage(
        training_root,
        count=100_000,
    )
    _patch_production_bundle(monkeypatch, fixture)
    source = LocalCaptureSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="village-a",
        role="production-acceptance",
        source_kind="local-capture",
        rights_receipt_sha256="b" * 64,
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    source_path = tmp_path / "production-source.json"
    source_bytes = canonical_model_bytes(source)
    source_path.write_bytes(source_bytes)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    workspace_base = tmp_path / "real-scene"
    run_id = "production-a"
    workspace = (
        workspace_base
        / run_id
        / source.dataset_id
        / source_sha256[:16]
    )
    attempt_id = "attempt-import-one"
    import_root = workspace / "stages/import" / attempt_id
    import_receipt = import_real_scene(
        training_root,
        import_root,
        source_role="production-acceptance",
        control_points_path=_write_control_points(
            tmp_path / "control-points.json"
        ),
        geo_origin=(26.0, 119.0, 10.0),
        chunk_size=50.0,
    )
    output_bindings = tuple(
        StageArtifactBinding(
            path=path.relative_to(workspace).as_posix(),
            byte_length=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(
            candidate
            for candidate in import_root.rglob("*")
            if candidate.is_file()
        )
    )
    stage_receipt = StageReceipt(
        dataset_id=source.dataset_id,
        source_sha256=source_sha256,
        stage="import",
        attempt_id=attempt_id,
        created_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        status="completed",
        prerequisites=(),
        outputs=output_bindings,
        alignment_rms_m=import_receipt.alignment_rms_m,
    )
    stage_payload = canonical_stage_receipt_bytes(stage_receipt)
    stage_receipt_dir = workspace / "receipts/import"
    stage_receipt_dir.mkdir(parents=True)
    (
        stage_receipt_dir
        / f"{hashlib.sha256(stage_payload).hexdigest()}.json"
    ).write_bytes(stage_payload)

    resolved = resolve_latest_production_import(
        source_path,
        workspace_base=workspace_base,
        run_id=run_id,
    )
    assert resolved.workspace_root == workspace
    assert resolved.import_root == import_root
    output_dir = workspace / "viewer-inputs"

    result = materialize_production_viewer_inputs(
        import_root=resolved.import_root,
        output_dir=output_dir,
    )

    camera_bytes = result.camera_set_path.read_bytes()
    policy_bytes = result.policy_path.read_bytes()
    camera_set = load_viewer_camera_set_bytes(camera_bytes)
    policy = ViewerPerformancePolicy.model_validate_json(policy_bytes)
    manifest_bytes = (
        import_root / import_receipt.manifest_path
    ).read_bytes()
    registration_path = (
        import_root
        / import_receipt.alignment_observed_registration_path
    )
    registration_bytes = registration_path.read_bytes()
    assert isinstance(camera_set, ViewerCameraSetV2)
    assert camera_set.source_role == "production-acceptance"
    assert camera_set.selection_strategy == "registered-camera-maximin-v1"
    assert camera_set.scene_manifest_sha256 == hashlib.sha256(
        manifest_bytes
    ).hexdigest()
    assert camera_set.import_receipt_sha256 == hashlib.sha256(
        (import_root / "import-receipt.json").read_bytes()
    ).hexdigest()
    assert camera_set.aligned_registration_sha256 == hashlib.sha256(
        registration_bytes
    ).hexdigest()
    assert len(camera_set.poses) == 3
    assert len({pose.pose_id for pose in camera_set.poses}) == 3
    assert policy.required_pose_ids == tuple(
        pose.pose_id for pose in camera_set.poses
    )
    assert policy_bytes == canonical_viewer_performance_policy_bytes(policy)
    assert json.loads(camera_bytes)["schema"] == "nantai.viewer-camera-set.v2"

    with pytest.raises(
        ViewerInputMaterializationError,
        match="output.*absent",
    ):
        materialize_production_viewer_inputs(
            import_root=import_root,
            output_dir=output_dir,
        )

    def _capture(argv, **kwargs):
        studio_url = urlsplit(argv[argv.index("--studio-url") + 1])
        connection = http.client.HTTPConnection(
            studio_url.hostname,
            studio_url.port,
            timeout=30,
        )
        connection.request(
            "GET",
            "/web/data/recon/recon_manifest.json",
        )
        response = connection.getresponse()
        served_manifest = response.read()
        connection.close()
        assert response.status == 200
        assert served_manifest == manifest_bytes
        assert kwargs == {
            "cwd": Path(__file__).resolve().parents[1],
            "check": False,
        }
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(session_module.subprocess, "run", _capture)
    evidence_root = workspace
    assert (
        run_production_viewer_session(
            ViewerSessionOptions(
                project_root=Path(__file__).resolve().parents[1],
                import_root=import_root,
                policy_path=result.policy_path,
                camera_set_path=result.camera_set_path,
                output_path=workspace / "viewer/report.json",
                decision_path=workspace / "viewer/decision.json",
                evidence_root=evidence_root,
                node_executable=Path(sys.executable).resolve(),
                python_executable=Path(sys.executable).resolve(),
                headless=True,
            )
        )
        == 0
    )


def test_materializer_rejects_preview_or_unverified_import_root(
    tmp_path,
    monkeypatch,
):
    unverified = tmp_path / "unverified"
    unverified.mkdir()
    monkeypatch.setattr(
        import_module,
        "validate_real_scene_import_receipt",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        ViewerInputMaterializationError,
        match="production.*import receipt",
    ):
        materialize_production_viewer_inputs(
            import_root=unverified,
            output_dir=tmp_path / "viewer-inputs",
        )


def test_read_regular_bytes_does_not_reopen_by_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _read_regular_bytes must use os.open, not Path.open.

    Path.open reopens by name after the pre-open lstat, which is a
    check-then-reopen TOCTOU that follows symlinks.  Verified bytes must
    come from a single fd opened with O_NOFOLLOW.
    """

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid": true}')

    called: list[Path] = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == evidence:
            called.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracking_open)

    viewer_inputs_module._read_regular_bytes(evidence, label="test manifest")

    assert not called, "Path.open was called (should use os.open)"


def test_read_regular_bytes_rejects_oversized_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: _read_regular_bytes must enforce a bounded file size.

    Without a pre-size check, an attacker could supply an enormous file
    that exhausts memory during stream.read().
    """

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid": true}')

    monkeypatch.setattr(
        viewer_inputs_module,
        "_MAXIMUM_VIEWER_INPUT_BYTES",
        4,
    )

    with pytest.raises(
        ViewerInputMaterializationError,
        match="outside the allowed range|exceeds",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_rejects_short_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """RED->GREEN: a stream returning fewer bytes than st_size must be rejected."""

    evidence = tmp_path / "manifest.json"
    payload = b'{"valid":true}'
    evidence.write_bytes(payload)

    original_open = viewer_inputs_module.os.open

    def short_read_open(path, flags):
        fd = original_open(path, flags)
        original_fdopen = viewer_inputs_module.os.fdopen
        real_stream = original_fdopen(fd, "rb", buffering=0)

        class ShortStream:
            def __init__(self, stream):
                self._stream = stream
                self._done = False

            def read(self, size=-1):
                if self._done:
                    return b""
                self._done = True
                data = self._stream.read(size)
                return data[:-1] if data else data

            def fileno(self):
                return self._stream.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._stream.close()

        def patched_fdopen(fd_arg, *a, **kw):
            if fd_arg == fd:
                return ShortStream(real_stream)
            return original_fdopen(fd_arg, *a, **kw)

        monkeypatch.setattr(viewer_inputs_module.os, "fdopen", patched_fdopen)
        return fd

    monkeypatch.setattr(viewer_inputs_module.os, "open", short_read_open)

    with pytest.raises(
        ViewerInputMaterializationError,
        match="changed while being read",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_rejects_descriptor_before_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A file swap between os.open and the first fstat must be rejected."""

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')
    swapped_payload = b'{"swapped":true}'

    original_open = viewer_inputs_module.os.open

    def swapping_open(path, flags):
        fd = original_open(path, flags)
        # Swap the path content immediately after open, before _read_regular_bytes
        # can call fstat on the descriptor.  The descriptor still points at the
        # original inode, so the post-open fstat identity must disagree with the
        # pre-open lstat identity (different mtime/size).
        evidence.write_bytes(swapped_payload)
        return fd

    monkeypatch.setattr(viewer_inputs_module.os, "open", swapping_open)

    with pytest.raises(
        ViewerInputMaterializationError,
        match="changed before read",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_rejects_descriptor_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A file swap during read must be detected via post-read fstat."""

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')

    original_open = viewer_inputs_module.os.open

    def mutating_open(path, flags):
        fd = original_open(path, flags)
        original_fdopen = viewer_inputs_module.os.fdopen
        real_stream = original_fdopen(fd, "rb", buffering=0)

        class MutatingStream:
            def __init__(self, stream):
                self._stream = stream
                self._read_done = False

            def read(self, size=-1):
                data = self._stream.read(size)
                if not self._read_done:
                    self._read_done = True
                    # Mutate the underlying file so the post-read fstat
                    # signature differs from the pre-read descriptor identity.
                    evidence.write_bytes(b'{"valid":true,"extra":1}')
                return data

            def fileno(self):
                return self._stream.fileno()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._stream.close()

        def patched_fdopen(fd_arg, *a, **kw):
            if fd_arg == fd:
                return MutatingStream(real_stream)
            return original_fdopen(fd_arg, *a, **kw)

        monkeypatch.setattr(viewer_inputs_module.os, "fdopen", patched_fdopen)
        return fd

    monkeypatch.setattr(viewer_inputs_module.os, "open", mutating_open)

    with pytest.raises(
        ViewerInputMaterializationError,
        match="changed while being read",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_rejects_path_after_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A file swap at the path after read must be caught by post-read lstat."""

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')

    original_lstat = Path.lstat
    read_completed = []

    def swapping_lstat(path):
        result = original_lstat(path)
        if path == evidence and read_completed:
            # Post-read lstat: report a different inode so the post-read
            # identity disagrees with the pre-open identity.
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino + 1,
                st_mode=result.st_mode,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
            )
        return result

    original_fdopen = viewer_inputs_module.os.fdopen

    def tracking_fdopen(fd, *args, **kwargs):
        stream = original_fdopen(fd, *args, **kwargs)
        original_read = stream.read

        def tracking_read(size=-1):
            data = original_read(size)
            read_completed.append(True)
            return data

        stream.read = tracking_read
        return stream

    monkeypatch.setattr(Path, "lstat", swapping_lstat)
    monkeypatch.setattr(viewer_inputs_module.os, "fdopen", tracking_fdopen)

    with pytest.raises(
        ViewerInputMaterializationError,
        match="changed while being read",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_rejects_reparse_parent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A reparse-point leaf must be rejected before any read."""

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')

    original_lstat = Path.lstat
    probe = tmp_path / "manifest.json"

    def reparse_lstat(path: Path):
        result = original_lstat(path)
        if path == probe:
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_mode=result.st_mode,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
                st_file_attributes=0x400,
            )
        return result

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(
        ViewerInputMaterializationError,
        match="missing or link-like",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_rejects_empty_file(
    tmp_path: Path,
) -> None:
    """An empty file must be rejected after the identity checks pass."""

    evidence = tmp_path / "empty.json"
    evidence.write_bytes(b"")

    with pytest.raises(
        ViewerInputMaterializationError,
        match="empty",
    ):
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )


def test_read_regular_bytes_errors_do_not_leak_absolute_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Error messages must not echo the absolute path or parent directory."""

    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(b'{"valid":true}')
    absolute = str(evidence)

    original_os_open = viewer_inputs_module.os.open

    def failing_os_open(p, *args, **kwargs):
        if Path(p) == evidence:
            raise OSError("injected")
        return original_os_open(p, *args, **kwargs)

    monkeypatch.setattr(viewer_inputs_module.os, "open", failing_os_open)

    with pytest.raises(
        ViewerInputMaterializationError,
        match="cannot be read",
    ) as exc:
        viewer_inputs_module._read_regular_bytes(
            evidence, label="test manifest"
        )

    message = str(exc.value)
    assert absolute not in message
    assert str(evidence.parent) not in message


def test_read_regular_bytes_preserves_canonical_json_payload(
    tmp_path: Path,
) -> None:
    """A valid canonical JSON payload must round-trip without regression."""

    payload = b'{"valid":true}'
    evidence = tmp_path / "manifest.json"
    evidence.write_bytes(payload)

    result = viewer_inputs_module._read_regular_bytes(
        evidence, label="test manifest"
    )

    assert result == payload
