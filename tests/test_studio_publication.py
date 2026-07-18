"""Publication journal, snapshot gate, and NTFS durability tests."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from pipeline.ingest import ingest_all
from pipeline.studio_jobs import (
    ArtifactPromoter,
    CommandRegistry,
    ConcurrentChangeError,
    JobContractError,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
    build_concurrency_snapshot,
)
from pipeline.studio_ledger import StudioLedger


def _now() -> datetime:
    return datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _setup_publishable_run(tmp_path, *, old_target=True):
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "input/photo.jpg").write_bytes(b"new-photo")
    if old_target:
        (root / "photos").mkdir()
        (root / "photos/old.jpg").write_bytes(b"old-photo")
    expected = build_concurrency_snapshot(root)
    registry = CommandRegistry(root)
    params = registry.parse("ingest", {
        "fps": 2,
        "max_frames": 300,
        "blur_threshold": 0,
        "max_long_edge": 2560,
    })
    invocation = registry.build_invocation("run-001", params)
    invocation.stage_dir.parent.mkdir(parents=True)
    ingest_all(invocation.input_dir, invocation.stage_dir, blur_threshold=0)

    ledger = StudioLedger(root / ".nantai-studio/studio.db")
    ledger.initialize()
    (root / ".nantai-studio/backups").mkdir(exist_ok=True)
    ledger.create_run(
        run_id="run-001", request_id="request-001", command="ingest",
        command_schema_version=1, parameters=params.model_dump(),
        snapshot=expected.as_dict(), owner="owner-a", lease_generation=1,
        lease_expires_utc=_now() + timedelta(seconds=30),
        staging_path=".nantai-studio/work/run-001/photos", created_utc=_now(),
    )
    for status, phase in (
        ("running", "executing"),
        ("running", "validating"),
        ("running", "publishing"),
    ):
        ledger.transition_run(
            "run-001", status=status, phase=phase,
            owner="owner-a", lease_generation=1,
            message=phase, occurred_utc=_now(),
        )
    return root, expected, invocation, ledger


def _promoter(root, ledger, *, fault=None):
    return ArtifactPromoter(
        root,
        ledger=ledger,
        durability=WindowsNtfsDurabilityBackend(root),
        fault_injector=fault,
    )


class RecordingDurability:
    def __init__(self):
        self.flushed_files = []
        self.flushed_directories = []

    def flush_file(self, path):
        self.flushed_files.append(path)

    def flush_directory(self, path):
        self.flushed_directories.append(path)

    @staticmethod
    def move(source, destination):
        source.rename(destination)

    @staticmethod
    def remove_tree(path):
        shutil.rmtree(path)


def test_publication_flushes_every_staged_file_before_success_commit(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    durability = RecordingDurability()
    promoter = ArtifactPromoter(root, ledger=ledger, durability=durability)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer:
        promoter.publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )

    assert {path.name for path in durability.flushed_files} == {
        "photo.jpg", "ingest_manifest.json",
    }
    assert ledger.get_run("run-001").status == "succeeded"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_windows_ntfs_durability_self_test_uses_real_operations(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    result = WindowsNtfsDurabilityBackend(root).self_test()
    assert result.ready is True
    assert result.filesystem == "NTFS"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_verified_stage_replaces_formal_target_and_commits_success(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer:
        result = _promoter(root, ledger).publish(
            publication_id="publication-001",
            run_id="run-001",
            owner="owner-a",
            lease_generation=1,
            expected_snapshot=expected,
            invocation=invocation,
            occurred_utc=_now(),
        )

    assert (root / "photos/photo.jpg").read_bytes() == b"new-photo"
    assert not (root / "photos/old.jpg").exists()
    assert not (root / ".nantai-studio/backups/publication-001").exists()
    assert ledger.get_run("run-001").status == "succeeded"
    assert ledger.get_run("run-001").artifact_ids == (result.artifact_id,)


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_invalid_stage_never_changes_the_old_formal_target(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    (invocation.stage_dir / "undeclared.txt").write_text("extra", encoding="utf-8")
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, pytest.raises(JobContractError, match="staged|undeclared|artifact"):
        _promoter(root, ledger).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )

    assert (root / "photos/old.jpg").read_bytes() == b"old-photo"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_changed_formal_target_is_rejected_before_publication(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    (root / "photos/old.jpg").write_bytes(b"concurrent-change")
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, pytest.raises(ConcurrentChangeError):
        _promoter(root, ledger).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )
    assert (root / "photos/old.jpg").read_bytes() == b"concurrent-change"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_uncommitted_recovery_restores_old_target_and_staging(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)

    def crash(point):
        if point == "after_stage_target_move":
            raise RuntimeError("simulated crash")

    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _promoter(root, ledger, fault=crash).publish(
                publication_id="publication-001", run_id="run-001",
                owner="owner-a", lease_generation=1,
                expected_snapshot=expected, invocation=invocation,
                occurred_utc=_now(),
            )
        _promoter(root, ledger).recover_all(
            owner="owner-a", lease_generation=1, occurred_utc=_now(),
        )

    assert (root / "photos/old.jpg").read_bytes() == b"old-photo"
    assert (invocation.stage_dir / "photo.jpg").read_bytes() == b"new-photo"
    assert ledger.get_run("run-001").status == "failed"
    assert ledger.get_run("run-001").error_code == "publish_failed"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_post_commit_recovery_never_rolls_back_success(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)

    def crash(point):
        if point == "after_commit":
            raise RuntimeError("simulated post-commit crash")

    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer:
        with pytest.raises(RuntimeError, match="post-commit"):
            _promoter(root, ledger, fault=crash).publish(
                publication_id="publication-001", run_id="run-001",
                owner="owner-a", lease_generation=1,
                expected_snapshot=expected, invocation=invocation,
                occurred_utc=_now(),
            )
        _promoter(root, ledger).recover_all(
            owner="owner-a", lease_generation=1, occurred_utc=_now(),
        )

    assert ledger.get_run("run-001").status == "succeeded"
    assert (root / "photos/photo.jpg").read_bytes() == b"new-photo"
    assert not (root / "photos/old.jpg").exists()
    assert not (root / ".nantai-studio/backups/publication-001").exists()


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_committed_recovery_uses_journal_bytes_not_changed_live_input(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer:
        _promoter(root, ledger).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )
    (root / "input/photo.jpg").write_bytes(b"next-ingest-input")

    with writer:
        _promoter(root, ledger).recover_all(
            owner="unused", lease_generation=1, occurred_utc=_now(),
        )

    assert ledger.get_run("run-001").status == "succeeded"
    assert (root / "photos/photo.jpg").read_bytes() == b"new-photo"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_committed_recovery_rejects_target_bytes_changed_after_commit(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer:
        _promoter(root, ledger).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )
    (root / "photos/photo.jpg").write_bytes(b"corrupt")

    with writer, pytest.raises(JobContractError, match="journal"):
        _promoter(root, ledger).recover_all(
            owner="unused", lease_generation=1, occurred_utc=_now(),
        )


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_successive_commits_recover_only_the_latest_target_owner(tmp_path):
    root, first_snapshot, first_invocation, ledger = _setup_publishable_run(tmp_path)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer:
        _promoter(root, ledger).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=first_snapshot, invocation=first_invocation,
            occurred_utc=_now(),
        )

    (root / "input/photo.jpg").write_bytes(b"second-photo")
    second_snapshot = build_concurrency_snapshot(root)
    params = CommandRegistry(root).parse("ingest", {
        "fps": 2, "max_frames": 300, "blur_threshold": 0,
        "max_long_edge": 2560,
    })
    second_invocation = CommandRegistry(root).build_invocation("run-002", params)
    second_invocation.stage_dir.parent.mkdir(parents=True)
    ingest_all(
        second_invocation.input_dir,
        second_invocation.stage_dir,
        blur_threshold=0,
    )
    ledger.create_run(
        run_id="run-002", request_id="request-002", command="ingest",
        command_schema_version=1, parameters=params.model_dump(),
        snapshot=second_snapshot.as_dict(), owner="owner-b", lease_generation=1,
        lease_expires_utc=_now() + timedelta(seconds=30),
        staging_path=".nantai-studio/work/run-002/photos", created_utc=_now(),
    )
    for status, phase in (
        ("running", "executing"),
        ("running", "validating"),
        ("running", "publishing"),
    ):
        ledger.transition_run(
            "run-002", status=status, phase=phase,
            owner="owner-b", lease_generation=1,
            message=phase, occurred_utc=_now(),
        )
    with writer:
        _promoter(root, ledger).publish(
            publication_id="publication-002", run_id="run-002",
            owner="owner-b", lease_generation=1,
            expected_snapshot=second_snapshot, invocation=second_invocation,
            occurred_utc=_now(),
        )
        _promoter(root, ledger).recover_all(
            owner="unused", lease_generation=1, occurred_utc=_now(),
        )

    assert (root / "photos/photo.jpg").read_bytes() == b"second-photo"
    assert [item.status for item in ledger.list_publications()] == [
        "committed", "committed",
    ]


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_path_replacement_race_cannot_move_into_an_external_target(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()

    def replace_target_with_link(point):
        if point != "before_stage_target_move":
            return
        try:
            os.symlink(outside, root / "photos", target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlink unavailable: {exc}")

    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    with writer, pytest.raises(JobContractError, match="link|junction|path"):
        _promoter(root, ledger, fault=replace_target_with_link).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )
    assert list(outside.iterdir()) == []


def _capture_publish_values():
    return {
        "revision_id": "capture-" + "a" * 32,
        "intent_id": "capture-publication-" + "b" * 32,
        "run_id": "run-001",
        "owner": "owner-a",
        "lease_generation": 1,
        "synthetic": False,
        "occurred_utc": _now(),
    }


def _capture_publisher(root, ledger, *, durability=None, fault=None):
    from pipeline.studio_jobs import CaptureBundlePublisher

    (root / ".nantai-studio/artifacts/capture").mkdir(
        parents=True,
        exist_ok=True,
    )
    (root / ".nantai-studio/quarantine").mkdir(exist_ok=True)
    return CaptureBundlePublisher(
        root,
        ledger=ledger,
        durability=durability or RecordingDurability(),
        fault_injector=fault,
    )


def test_capture_publisher_moves_absent_bundle_then_commits_revision(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    durability = RecordingDurability()
    publisher = _capture_publisher(
        root,
        ledger,
        durability=durability,
    )
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer:
        published = publisher.publish(
            expected_snapshot=expected,
            invocation=invocation,
            **_capture_publish_values(),
        )

    destination = (
        root / ".nantai-studio/artifacts/capture" / published.revision.id
    )
    assert published.destination == destination
    assert published.revision == ledger.get_capture_revision(
        published.revision.id,
    )
    assert ledger.get_run("run-001").status == "running"
    assert (destination / "payload/photo.jpg").read_bytes() == b"new-photo"
    assert {path.name for path in durability.flushed_files} == {
        "manifest.json",
        "ingest_manifest.json",
        "photo.jpg",
    }
    assert destination.parent in durability.flushed_directories
    assert invocation.stage_dir.parent in durability.flushed_directories


def test_capture_publisher_fault_before_move_exposes_no_revision(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)

    def crash(point):
        if point == "before_capture_move":
            raise RuntimeError("simulated pre-move crash")

    publisher = _capture_publisher(root, ledger, fault=crash)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, pytest.raises(RuntimeError, match="pre-move"):
        publisher.publish(
            expected_snapshot=expected,
            invocation=invocation,
            **_capture_publish_values(),
        )

    destination = (
        root / ".nantai-studio/artifacts/capture"
        / _capture_publish_values()["revision_id"]
    )
    assert not destination.exists()
    assert not ledger.list_capture_revisions()


def test_capture_publisher_rolls_forward_complete_post_move_orphan(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)

    def crash(point):
        if point == "after_capture_move":
            raise RuntimeError("simulated post-move crash")

    publisher = _capture_publisher(root, ledger, fault=crash)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    values = _capture_publish_values()
    with writer:
        with pytest.raises(RuntimeError, match="post-move"):
            publisher.publish(
                expected_snapshot=expected,
                invocation=invocation,
                **values,
            )
        recovered = _capture_publisher(root, ledger).publish(
            expected_snapshot=expected,
            invocation=invocation,
            **values,
        )

    assert recovered.revision.id == values["revision_id"]
    assert ledger.get_run("run-001").status == "running"
    assert recovered.destination.is_dir()


def test_capture_publisher_quarantines_hash_damaged_post_move_orphan(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)

    def crash(point):
        if point == "after_capture_move":
            raise RuntimeError("simulated post-move crash")

    publisher = _capture_publisher(root, ledger, fault=crash)
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")
    values = _capture_publish_values()
    destination = (
        root / ".nantai-studio/artifacts/capture" / values["revision_id"]
    )
    with writer:
        with pytest.raises(RuntimeError, match="post-move"):
            publisher.publish(
                expected_snapshot=expected,
                invocation=invocation,
                **values,
            )
        (destination / "payload/photo.jpg").write_bytes(b"corrupt")
        with pytest.raises(JobContractError, match="quarantined|invalid"):
            _capture_publisher(root, ledger).publish(
                expected_snapshot=expected,
                invocation=invocation,
                **values,
            )

    assert not destination.exists()
    quarantined = list(
        (root / ".nantai-studio/quarantine").glob(
            f"{values['revision_id']}-*",
        ),
    )
    assert len(quarantined) == 1
    assert (quarantined[0] / "payload/photo.jpg").read_bytes() == b"corrupt"
    assert not ledger.list_capture_revisions()


def test_capture_publisher_never_replaces_unjournaled_destination(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    publisher = _capture_publisher(root, ledger)
    values = _capture_publish_values()
    destination = (
        root / ".nantai-studio/artifacts/capture" / values["revision_id"]
    )
    destination.mkdir()
    (destination / "owner.txt").write_text("existing", encoding="utf-8")
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, pytest.raises(JobContractError, match="already exists|absent"):
        publisher.publish(
            expected_snapshot=expected,
            invocation=invocation,
            **values,
        )

    assert (destination / "owner.txt").read_text(encoding="utf-8") == "existing"
    assert not ledger.list_capture_revisions()


def test_capture_path_replacement_race_cannot_publish_outside_project(
    tmp_path,
):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    outside = tmp_path / "outside-captures"
    outside.mkdir()
    capture_root = root / ".nantai-studio/artifacts/capture"

    def replace_capture_root_with_link(point):
        if point != "before_capture_move":
            return
        capture_root.rmdir()
        try:
            os.symlink(outside, capture_root, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"directory symlink unavailable: {exc}")

    publisher = _capture_publisher(
        root,
        ledger,
        fault=replace_capture_root_with_link,
    )
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, pytest.raises(JobContractError, match="link|junction|path"):
        publisher.publish(
            expected_snapshot=expected,
            invocation=invocation,
            **_capture_publish_values(),
        )

    assert list(outside.iterdir()) == []
    assert not ledger.list_capture_revisions()


def test_replaced_backup_root_cannot_create_a_transaction_outside_project(tmp_path):
    root, expected, invocation, ledger = _setup_publishable_run(tmp_path)
    managed = root / ".nantai-studio/backups"
    outside = tmp_path / "outside-backups"
    outside.mkdir()
    managed.rmdir()
    try:
        os.symlink(outside, managed, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, pytest.raises(JobContractError, match="backup|link|junction"):
        ArtifactPromoter(
            root, ledger=ledger, durability=RecordingDurability(),
        ).publish(
            publication_id="publication-001", run_id="run-001",
            owner="owner-a", lease_generation=1,
            expected_snapshot=expected, invocation=invocation,
            occurred_utc=_now(),
        )

    assert list(outside.iterdir()) == []
