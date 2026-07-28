"""L1: large render/log fetch boundary tests.

Proves that the real ``fetch()`` path streams large render (>2 MiB) in
bounded chunks (<= 1 MiB) without ``ZipFile.read()``, preserves large log
provenance, rejects render drift before publication, and cleans staging +
destination on stream failure.
"""
from __future__ import annotations

import hashlib
import importlib.util
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

import pipeline.remote_shell_executor as remote_module
from pipeline.production_runtime_evidence import (
    load_production_runtime_measurement_bytes,
)
from pipeline.real_dataset import canonical_model_bytes
from pipeline.remote_shell_executor import (
    RemoteResultBundleError,
    build_production_remote_result_bundle,
)
from pipeline.render_evaluation import RenderEvaluationReport
from pipeline.training_provenance import TrainingResult

_ONE_MIB = 1024 * 1024


# ---------------------------------------------------------------------------
# Helper loading from sibling test modules
# ---------------------------------------------------------------------------


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).resolve().parent / filename,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_v2 = _load_module("_v2_l1_helpers", "test_remote_result_fetch_v2.py")
_v2_scenario = _v2._v2_scenario
_v2_succeeded_status = _v2._v2_succeeded_status
_lifecycle_response = _v2._lifecycle_response
_status_response = _v2._status_response


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _large_rgb_png(min_bytes: int) -> bytes:
    """Generate a valid 4x3 RGB PNG larger than min_bytes.

    The policy resolution is 4x3 (matching the production fixture). A large
    tEXt ancillary chunk inflates the file past the streaming threshold
    without altering the policy-bound pixel dimensions.
    """
    width, height = 4, 3
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(width * 3)
    idat = zlib.compress(row * height)
    text_data = b"comment\x00" + b"x" * (min_bytes + 1024)

    def _chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"tEXt", text_data)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
    assert len(png) > min_bytes, (
        f"PNG too small: {len(png)} < {min_bytes}"
    )
    return png


# ---------------------------------------------------------------------------
# Archive rebuild helpers
# ---------------------------------------------------------------------------


def _rebuild_archive(scenario, *, replace_fn):
    """Replace files in result_root and rebuild archive + status."""
    result_root = scenario.result_root
    replace_fn(result_root)

    (result_root / "result-bundle-manifest.json").unlink(missing_ok=True)
    archive_path = scenario.training_root / "result-bundle.zip"
    archive_path.unlink(missing_ok=True)

    measurement = load_production_runtime_measurement_bytes(
        (result_root / "production-runtime" / "measurement.json").read_bytes()
    )
    built = build_production_remote_result_bundle(
        result_root=result_root,
        output_path=archive_path,
        job_id=scenario.job.job_id,
        attempt_id=scenario.job.attempt_id,
        request_sha256=scenario.job.request_sha256,
        training_bundle_sha256=scenario.job.training_bundle_sha256,
        container_instance_id=measurement.environment.container_instance_id,
        container_identity=(
            measurement.environment.observed_container_identity
        ),
        remote_target_sha256=measurement.remote_target_sha256,
        durable_job_ref_sha256=measurement.durable_job_ref_sha256,
        workspace_identity_sha256=measurement.workspace_identity_sha256,
    )

    scenario.archive = archive_path
    scenario.archive_sha256 = built.bundle_sha256
    scenario.archive_size = built.byte_length
    scenario.measurement = measurement
    scenario.status = _v2_succeeded_status(
        scenario.job,
        archive_sha256=built.bundle_sha256,
        archive_size=built.byte_length,
    )


def _replace_render(result_root: Path, *, render_payload: bytes) -> None:
    """Replace render file and rebuild report to match new render SHA/size."""
    renders_dir = result_root / "render-evaluation" / "renders"
    render_files = list(renders_dir.glob("*.png"))
    assert len(render_files) == 1, "expected exactly one render file"
    render_path = render_files[0]
    render_path.write_bytes(render_payload)

    report_path = result_root / "render-evaluation" / "report.json"
    report = RenderEvaluationReport.model_validate_json(
        report_path.read_bytes()
    )
    old_frame = report.frames[0]
    new_frame = old_frame.model_copy(
        update={
            "render_sha256": _sha(render_payload),
            "render_byte_length": len(render_payload),
        }
    )
    new_report = report.model_copy(update={"frames": (new_frame,)})
    report_path.write_bytes(canonical_model_bytes(new_report))


def _replace_training_log(
    result_root: Path, *, log_payload: bytes
) -> None:
    (result_root / "training.log").write_bytes(log_payload)
    result_path = result_root / "training-result.json"
    result = TrainingResult.model_validate_json(result_path.read_bytes())
    log_sha = _sha(log_payload)
    new_bindings = tuple(
        binding.model_copy(
            update={
                "artifact_sha256": log_sha,
                "artifact_size_bytes": len(log_payload),
            }
        )
        if binding.artifact_kind == "training_log"
        else binding
        for binding in result.output_bindings
    )
    updated = result.model_copy(
        update={
            "training_log_sha256": log_sha,
            "training_log_size_bytes": len(log_payload),
            "output_bindings": new_bindings,
        }
    )
    result_path.write_bytes(canonical_model_bytes(updated))


# ---------------------------------------------------------------------------
# Archive read/open observer
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
            member_name = (
                name if isinstance(name, str) else name.filename
            )
            self.read_calls.append(member_name)
            return original_read(self_, name, *args, **kwargs)

        def tracking_open(self_, name, *args, **kwargs):
            stream = original_open(self_, name, *args, **kwargs)
            member_name = (
                name if isinstance(name, str) else name.filename
            )
            self.opened_members.append(member_name)
            if member_name.startswith("render-evaluation/renders/"):
                original_stream_read = stream.read

                def tracking_stream_read(size=-1):
                    self.stream_read_sizes.append(size)
                    return original_stream_read(size)

                stream.read = tracking_stream_read
            return stream

        monkeypatch.setattr(zipfile.ZipFile, "read", tracking_read)
        monkeypatch.setattr(zipfile.ZipFile, "open", tracking_open)


# ---------------------------------------------------------------------------
# Poll + fetch helper
# ---------------------------------------------------------------------------


def _poll_and_fetch(scenario, destination: Path):
    """Poll to bind lifecycle+status, then fetch."""
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    return scenario.executor.fetch(scenario.job, destination)


def _staging_glob(destination: Path) -> list[Path]:
    return list(
        destination.parent.glob(f".{destination.name}.*.staging")
    )


# ---------------------------------------------------------------------------
# Test 1: large render streamed without archive.read()
# ---------------------------------------------------------------------------


def test_fetch_v2_streams_large_render_without_archive_read(
    tmp_path, monkeypatch
):
    """Large render (>2 MiB) must stream in <=1 MiB chunks."""
    scenario = _v2_scenario(tmp_path, monkeypatch)
    render_payload = _large_rgb_png(3 * _ONE_MIB)
    _rebuild_archive(
        scenario,
        replace_fn=lambda root: _replace_render(
            root, render_payload=render_payload
        ),
    )

    observer = _ArchiveObserver(monkeypatch)
    destination = tmp_path / "result"
    receipt = _poll_and_fetch(scenario, destination)

    assert receipt.state == "succeeded"

    render_name = next(
        name for name in observer.opened_members
        if name.startswith("render-evaluation/renders/")
    )
    # Large render was NOT read via archive.read()
    assert render_name not in observer.read_calls
    # Large render WAS opened for streaming
    assert render_name in observer.opened_members
    # All stream reads were <= 1 MiB
    assert observer.stream_read_sizes
    assert all(size <= _ONE_MIB for size in observer.stream_read_sizes)
    assert any(size >= _ONE_MIB for size in observer.stream_read_sizes)

    # Render was materialized in destination
    render_path = destination / render_name
    assert render_path.exists()
    assert render_path.stat().st_size == len(render_payload)
    assert _sha(render_path.read_bytes()) == _sha(render_payload)


# ---------------------------------------------------------------------------
# Test 2: large log preserves provenance
# ---------------------------------------------------------------------------


def test_fetch_v2_large_log_preserves_semantic_validation(
    tmp_path, monkeypatch
):
    """Large training.log (>2 MiB) must not break log provenance."""
    scenario = _v2_scenario(tmp_path, monkeypatch)
    log_payload = b"log line\n" * 300_000  # > 2 MiB
    _rebuild_archive(
        scenario,
        replace_fn=lambda root: _replace_training_log(
            root, log_payload=log_payload
        ),
    )

    destination = tmp_path / "result"
    receipt = _poll_and_fetch(scenario, destination)

    assert receipt.state == "succeeded"
    log_path = destination / "training.log"
    assert log_path.exists()
    assert log_path.stat().st_size == len(log_payload)
    assert _sha(log_path.read_bytes()) == _sha(log_payload)


# ---------------------------------------------------------------------------
# Test 3: streamed render drift before publication rejected
# ---------------------------------------------------------------------------


def test_fetch_v2_rejects_streamed_render_drift_before_publication(
    tmp_path, monkeypatch
):
    """Tampered render after streaming but before publication must fail."""
    scenario = _v2_scenario(tmp_path, monkeypatch)
    render_payload = _large_rgb_png(3 * _ONE_MIB)
    _rebuild_archive(
        scenario,
        replace_fn=lambda root: _replace_render(
            root, render_payload=render_payload
        ),
    )

    # Poll first
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    # Inject drift: tamper render staging file after streaming,
    # before publication.
    original_revalidate = remote_module._revalidate_staged_members

    def tampering_revalidate(verified, *, max_member_bytes):
        for name, path in verified.large_member_paths.items():
            if name.startswith("render-evaluation/renders/"):
                path.write_bytes(b"tampered render content")
                break
        return original_revalidate(
            verified, max_member_bytes=max_member_bytes
        )

    monkeypatch.setattr(
        remote_module,
        "_revalidate_staged_members",
        tampering_revalidate,
    )

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "result"
    with pytest.raises(
        RemoteResultBundleError, match="changed before publication"
    ):
        scenario.executor.fetch(scenario.job, destination)

    assert not destination.exists()
    assert not _staging_glob(destination)


# ---------------------------------------------------------------------------
# Test 4: stream failure removes staging and destination
# ---------------------------------------------------------------------------


def test_fetch_v2_stream_failure_removes_staging_and_destination(
    tmp_path, monkeypatch
):
    """Stream failure must clean staging and leave destination absent."""
    scenario = _v2_scenario(tmp_path, monkeypatch)
    render_payload = b"\x00" * (3 * _ONE_MIB)
    _rebuild_archive(
        scenario,
        replace_fn=lambda root: _replace_render(
            root, render_payload=render_payload
        ),
    )

    # Poll first
    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    scenario.runner.responses.append(_status_response(scenario.status))
    scenario.runner.download_source = scenario.archive
    scenario.executor.poll(scenario.job)

    # Make streaming fail
    def failing_stream(*args, **kwargs):
        raise RemoteResultBundleError("injected stream failure")

    monkeypatch.setattr(
        remote_module, "_stream_zip_member_to_staging", failing_stream
    )

    scenario.runner.responses.append(_lifecycle_response(scenario.lifecycle))
    destination = tmp_path / "result"
    with pytest.raises(RemoteResultBundleError, match="injected stream"):
        scenario.executor.fetch(scenario.job, destination)

    assert not destination.exists()
    assert not _staging_glob(destination)
