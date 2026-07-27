from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cloud.remote_training_worker import (
    RemoteWorkerError,
    RemoteWorkerSpec,
    initialize_job,
    read_status,
    start_job,
)
from pipeline.remote_shell_executor import RemoteShellStatus

_ROOT = Path(__file__).resolve().parents[1]
_CONTAINER_IDENTITY = "registry.example/nantai@sha256:" + "c" * 64
_DEFAULT_CONTAINER_ID = "a" * 64


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _init_job(
    job_dir: Path,
    *,
    bundle: bytes = b"verified-training-bundle",
) -> None:
    job_dir.parent.parent.mkdir(parents=True, exist_ok=True)
    spec = RemoteWorkerSpec(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256=_sha(bundle),
    )
    initialize_job(job_dir=job_dir, spec=spec)
    (job_dir / "training-job.zip").write_bytes(bundle)


@dataclass
class FakeDocker:
    """Stateful fake for the container runtime.

    Tracks all calls and simulates create/inspect/start/rm without
    touching a real container engine.  Configurable to reproduce
    wrong-ID, digest-drift, start-failure and partial-publication
    scenarios.
    """

    container_id: str = _DEFAULT_CONTAINER_ID
    image_ref: str = "sha256:" + "c" * 64
    config_image: str | None = None
    start_exit: int = 0
    skip_results: bool = False
    create_fails: bool = False
    inspect_fails: bool = False
    calls: list[list[str]] = field(default_factory=list)
    _job_dir: Path | None = None
    _container_identity: str = _CONTAINER_IDENTITY

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        sub = argv[1] if len(argv) > 1 else ""

        if sub == "create":
            if self.create_fails:
                return subprocess.CompletedProcess(
                    argv, 1, stdout="", stderr=b"create failed\n"
                )
            for arg in argv:
                if "@sha256:" in arg:
                    self._container_identity = arg
                if arg.startswith("type=bind,src=") and arg.endswith(
                    ",dst=/job"
                ):
                    self._job_dir = Path(
                        arg[len("type=bind,src=") : -len(",dst=/job")]
                    )
            return subprocess.CompletedProcess(
                argv, 0, stdout=self.container_id + "\n", stderr=""
            )

        if sub == "inspect":
            if self.inspect_fails:
                return subprocess.CompletedProcess(
                    argv, 1, stdout="", stderr=b"inspect failed\n"
                )
            fmt = (
                argv[argv.index("--format") + 1]
                if "--format" in argv
                else ""
            )
            if "{{.Image}}" in fmt:
                return subprocess.CompletedProcess(
                    argv, 0, stdout=self.image_ref + "\n", stderr=""
                )
            if "{{json .Config.Image}}" in fmt:
                ci = self.config_image or self._container_identity
                return subprocess.CompletedProcess(
                    argv, 0, stdout=f'"{ci}"\n', stderr=""
                )
            return subprocess.CompletedProcess(
                argv, 0, stdout="", stderr=""
            )

        if sub == "start":
            stdout_file = kwargs.get("stdout")
            stderr_file = kwargs.get("stderr")
            if stdout_file is not None and stdout_file is not subprocess.DEVNULL:
                stdout_file.write(b"container completed\n")
            if stderr_file is not None and stderr_file is not subprocess.DEVNULL:
                stderr_file.write(b"")
            if not self.skip_results and self._job_dir is not None:
                result_root = (
                    self._job_dir
                    / "runtime"
                    / "production-run"
                    / "result"
                )
                result_root.mkdir(parents=True, exist_ok=True)
                # write_bytes to avoid Windows newline translation
                (result_root / "container-identity.txt").write_bytes(
                    (self._container_identity + "\n").encode("ascii")
                )
                (result_root / "dataparser_transforms.json").write_bytes(
                    b'{"scale":1.0,"transform":'
                    b"[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}\n"
                )
                (
                    result_root / "operator-intent-config.yml"
                ).write_bytes(b"config\n")
                (result_root / "point_cloud.ply").write_bytes(b"ply\n")
                (result_root / "training-request.json").write_bytes(
                    b"{}\n"
                )
                (result_root / "training-result.json").write_bytes(
                    b"{}\n"
                )
                (result_root / "training.log").write_bytes(
                    b"training complete\n"
                )
            return subprocess.CompletedProcess(
                argv, self.start_exit, stdout=None, stderr=None
            )

        if sub == "rm":
            return subprocess.CompletedProcess(
                argv, 0, stdout=None, stderr=None
            )

        return subprocess.CompletedProcess(
            argv, 1, stdout=None, stderr=b"unknown subcommand\n"
        )


def _patch_worker(monkeypatch, fake: FakeDocker) -> None:
    monkeypatch.setattr(
        "cloud.remote_training_worker.subprocess.run", fake.run
    )


def _subcommands(fake: FakeDocker) -> list[str]:
    return [call[1] for call in fake.calls if len(call) > 1]


# ---------------------------------------------------------------------------
# NOW-4: fresh container lifecycle golden path
# ---------------------------------------------------------------------------


def test_worker_fresh_container_lifecycle_golden_path(tmp_path, monkeypatch):
    """docker create → inspect → start → rm; result bundle published."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 0
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "succeeded"
    assert status.exit_code == 0
    assert (job_dir / "result-bundle.zip").is_file()
    assert (
        (job_dir / "container-id.txt")
        .read_text(encoding="ascii")
        .strip()
        == _DEFAULT_CONTAINER_ID
    )

    subs = _subcommands(fake)
    assert "create" in subs
    assert "inspect" in subs
    assert "start" in subs
    assert "rm" in subs

    create_call = fake.calls[subs.index("create")]
    assert _CONTAINER_IDENTITY in create_call
    assert "--gpus" in create_call
    assert "--network" in create_call
    assert create_call[create_call.index("--network") + 1] == "none"
    assert "--security-opt" in create_call
    assert create_call[create_call.index("--security-opt") + 1] == (
        "no-new-privileges"
    )

    start_call = fake.calls[subs.index("start")]
    assert _DEFAULT_CONTAINER_ID in start_call

    rm_call = fake.calls[subs.index("rm")]
    assert _DEFAULT_CONTAINER_ID in rm_call

    # rm must come after start (durable publication before removal)
    assert subs.index("rm") > subs.index("start")
    assert subs.index("rm") > subs.index("create")


# ---------------------------------------------------------------------------
# NOW-4: wrong / short container ID must fail closed
# ---------------------------------------------------------------------------


def test_worker_rejects_non_hex_container_id(tmp_path, monkeypatch):
    """create returning a non-hex ID must fail closed."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(container_id="not-a-hex-id!")
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 75
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    # Container was never tracked, so no rm call
    subs = _subcommands(fake)
    assert "rm" not in subs


def test_worker_rejects_short_container_id(tmp_path, monkeypatch):
    """create returning a short ID must fail closed."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(container_id="abc123")
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 75
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    subs = _subcommands(fake)
    assert "rm" not in subs


# ---------------------------------------------------------------------------
# NOW-4: inspect digest drift must fail closed
# ---------------------------------------------------------------------------


def test_worker_rejects_inspect_digest_drift(tmp_path, monkeypatch):
    """inspect returning a different image digest must fail closed."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(
        config_image="registry.example/other@sha256:" + "f" * 64,
    )
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 75
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    # Container was created, so it must be removed
    subs = _subcommands(fake)
    assert "rm" in subs
    assert subs.index("rm") > subs.index("create")


# ---------------------------------------------------------------------------
# NOW-4: start failure must fail closed and remove container
# ---------------------------------------------------------------------------


def test_worker_rejects_start_failure(tmp_path, monkeypatch):
    """start returning non-zero must fail closed and remove container."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(start_exit=1)
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 1
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    assert status.exit_code == 1
    subs = _subcommands(fake)
    assert "rm" in subs
    assert subs.index("rm") > subs.index("start")


# ---------------------------------------------------------------------------
# NOW-4: partial publication must fail closed
# ---------------------------------------------------------------------------


def test_worker_rejects_partial_publication(tmp_path, monkeypatch):
    """start succeeding but result files missing must fail closed."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(skip_results=True)
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 75
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    subs = _subcommands(fake)
    assert "rm" in subs
    assert subs.index("rm") > subs.index("start")
    assert not (job_dir / "result-bundle.zip").exists()


# ---------------------------------------------------------------------------
# NOW-4: reconnect replay must fail (lock prevents second start)
# ---------------------------------------------------------------------------


def test_worker_rejects_reconnect_replay(tmp_path, monkeypatch):
    """Second start_job must fail due to lock; no new container created."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )
    assert returncode == 0

    create_count = sum(1 for c in fake.calls if len(c) > 1 and c[1] == "create")
    assert create_count == 1

    with pytest.raises(RemoteWorkerError, match="already started"):
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )

    create_count_after = sum(
        1 for c in fake.calls if len(c) > 1 and c[1] == "create"
    )
    assert create_count_after == 1


# ---------------------------------------------------------------------------
# NOW-4: structured argv (no shell injection)
# ---------------------------------------------------------------------------


def test_worker_uses_structured_argv_no_shell(tmp_path, monkeypatch):
    """All subprocess calls must use shell=False and structured argv."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    shells_seen: list[bool] = []
    original_run = fake.run

    def checking_run(argv, **kwargs):
        shells_seen.append(bool(kwargs.get("shell", False)))
        return original_run(argv, **kwargs)

    monkeypatch.setattr(
        "cloud.remote_training_worker.subprocess.run", checking_run
    )

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 0
    assert shells_seen
    assert not any(shells_seen)


# ---------------------------------------------------------------------------
# NOW-4: create failure must fail closed
# ---------------------------------------------------------------------------


def test_worker_rejects_create_failure(tmp_path, monkeypatch):
    """docker create returning non-zero must fail closed."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(create_fails=True)
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 75
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    subs = _subcommands(fake)
    assert "create" in subs
    assert "start" not in subs
    assert "rm" not in subs


# ---------------------------------------------------------------------------
# NOW-4: inspect failure must fail closed
# ---------------------------------------------------------------------------


def test_worker_rejects_inspect_failure(tmp_path, monkeypatch):
    """docker inspect returning non-zero must fail closed."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(inspect_fails=True)
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 75
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"
    subs = _subcommands(fake)
    assert "create" in subs
    assert "inspect" in subs
    assert "start" not in subs
    assert "rm" in subs


# ---------------------------------------------------------------------------
# NOW-4: container-id.txt is persisted for audit
# ---------------------------------------------------------------------------


def test_worker_persists_container_id_for_audit(tmp_path, monkeypatch):
    """container-id.txt must be written before start for audit trail."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    assert returncode == 0
    container_id_file = job_dir / "container-id.txt"
    assert container_id_file.is_file()
    persisted_id = container_id_file.read_text(encoding="ascii").strip()
    assert persisted_id == _DEFAULT_CONTAINER_ID
    assert persisted_id == fake.container_id


# ---------------------------------------------------------------------------
# Existing: init is exclusive, status is canonical
# ---------------------------------------------------------------------------


def test_worker_init_is_exclusive_and_status_is_canonical(tmp_path):
    remote_root = tmp_path / "jobs"
    remote_root.mkdir()
    job_dir = remote_root / "job-1" / "attempt-1"
    spec = RemoteWorkerSpec(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256="b" * 64,
    )
    initialize_job(job_dir=job_dir, spec=spec)

    with pytest.raises(RemoteWorkerError, match="absent"):
        initialize_job(job_dir=job_dir, spec=spec)
    assert json.loads(
        (job_dir / "job-spec.json").read_text(encoding="ascii")
    )["job_id"] == "job-1"
    with pytest.raises(RemoteWorkerError, match="status"):
        read_status(job_dir, max_bytes=64 * 1024)
