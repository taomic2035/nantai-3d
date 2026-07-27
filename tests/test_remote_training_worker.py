from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud import remote_training_worker as worker
from cloud.remote_training_worker import (
    RemoteWorkerError,
    RemoteWorkerSpec,
    initialize_job,
    read_status,
    start_job,
)
from pipeline.durable_io import DurableIOError
from pipeline.production_runtime_evidence import (
    ProductionRuntimePolicy,
    canonical_production_runtime_policy_bytes,
    training_cli_schema_sha256,
)
from pipeline.remote_shell_executor import RemoteShellStatus

_ROOT = Path(__file__).resolve().parents[1]
_CONTAINER_IDENTITY = "registry.example/nantai@sha256:" + "c" * 64
_DEFAULT_CONTAINER_ID = "a" * 64
_TEST_EXECUTABLE = Path(sys.executable).resolve()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _runtime_policy() -> ProductionRuntimePolicy:
    options = (
        "--auto-scale-poses",
        "--center-method",
        "--orientation-method",
        "--scale-factor",
    )
    return ProductionRuntimePolicy.create(
        expected_exact_commit="1" * 40,
        expected_remote_target_sha256="2" * 64,
        expected_probe_set_sha256="3" * 64,
        expected_container_identity=_CONTAINER_IDENTITY,
        expected_gpu_uuid="GPU-12345678-1234-1234-1234-123456789abc",
        min_gpu_memory_mib=16_384,
        expected_cuda_runtime_version="12.4",
        expected_python_version="3.11.9",
        expected_nerfstudio_version="1.1.5",
        expected_training_cli_schema_sha256=(
            training_cli_schema_sha256(
                trainer_name="nerfstudio-splatfacto",
                observed_options=options,
            )
        ),
        required_training_cli_options=options,
        expected_checker_sha256=_sha(
            (
                _ROOT
                / "cloud"
                / "production_runtime_entrypoint.py"
            ).read_bytes()
        ),
        expected_container_runtime_sha256=_sha(
            _TEST_EXECUTABLE.read_bytes()
        ),
        expected_nvidia_smi_sha256="6" * 64,
        expected_python_sha256="7" * 64,
        expected_training_cli_sha256="8" * 64,
        expected_worker_sha256="9" * 64,
    )


def _init_job(
    job_dir: Path,
    *,
    bundle: bytes = b"verified-training-bundle",
) -> None:
    job_dir.parent.parent.mkdir(parents=True, exist_ok=True)
    policy = _runtime_policy()
    spec = RemoteWorkerSpec(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256=_sha(bundle),
        runtime_policy_sha256=policy.content_sha256,
        remote_target_sha256=policy.expected_remote_target_sha256,
        durable_job_ref_sha256="3" * 64,
    )
    initialize_job(job_dir=job_dir, spec=spec)
    (job_dir / "training-job.zip").write_bytes(bundle)
    (job_dir / "production-runtime-policy.json").write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )


@dataclass
class FakeDocker:
    """Stateful fake for the container runtime.

    Tracks all calls and simulates create/inspect/start/rm without
    touching a real container engine.  Configurable to reproduce
    wrong-ID, digest-drift, start-failure, partial-publication and
    cleanup-failure scenarios.

    For image verification, three identities are modelled:

    - ``resolved_image_id``: the content ID returned by
      ``image inspect <identity> --format {{.Id}}`` (sha256:<manifest>).
    - ``image_ref``: the value returned by container
      ``inspect --format {{.Image}}`` — must equal ``resolved_image_id``
      in the golden path.
    - ``config_image``: the value returned by
      ``inspect --format {{json .Config.Image}}`` — must equal the
      immutable ``repo@sha256:...`` identity.
    """

    container_id: str = _DEFAULT_CONTAINER_ID
    resolved_image_id: str = "sha256:" + "c" * 64
    image_ref: str = "sha256:" + "c" * 64
    config_image: str | None = None
    start_exit: int = 0
    skip_results: bool = False
    create_fails: bool = False
    inspect_fails: bool = False
    image_inspect_fails: bool = False
    rm_fails: bool = False
    production_runtime_marker: bool = False
    result_container_id_marker: bytes | None = None
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

        if sub == "image":
            if self.image_inspect_fails:
                return subprocess.CompletedProcess(
                    argv, 1, stdout="", stderr=b"image inspect failed\n"
                )
            fmt = (
                argv[argv.index("--format") + 1]
                if "--format" in argv
                else ""
            )
            if "{{.Id}}" in fmt:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=self.resolved_image_id + "\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                argv, 0, stdout="", stderr=""
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
                if self.production_runtime_marker:
                    runtime_root = (
                        self._job_dir / "production-runtime"
                    )
                    runtime_root.mkdir()
                    for name in (
                        "measurement.json",
                        "policy.json",
                        "decision.json",
                    ):
                        (runtime_root / name).write_bytes(
                            f"{name}\n".encode("ascii")
                        )
                if self.result_container_id_marker is not None:
                    (result_root / "container-id.txt").write_bytes(
                        self.result_container_id_marker
                    )
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
            if self.rm_fails:
                return subprocess.CompletedProcess(
                    argv, 1, stdout=None, stderr=b"rm failed\n"
                )
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
    monkeypatch.setattr(
        "cloud.remote_training_worker.shutil.which",
        lambda name: (
            str(_TEST_EXECUTABLE)
            if name in {"docker", "podman"}
            else None
        ),
    )


def _subcommands(fake: FakeDocker) -> list[str]:
    return [call[1] for call in fake.calls if len(call) > 1]


# ---------------------------------------------------------------------------
# F1a: production runtime policy is a bound job input
# ---------------------------------------------------------------------------


def test_worker_binds_runtime_policy_to_spec_and_lifecycle(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    bundle = b"verified-training-bundle"
    policy = _runtime_policy()
    job_dir.parent.parent.mkdir(parents=True, exist_ok=True)
    spec = RemoteWorkerSpec(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256=_sha(bundle),
        runtime_policy_sha256=policy.content_sha256,
        remote_target_sha256=policy.expected_remote_target_sha256,
        durable_job_ref_sha256="3" * 64,
    )
    initialize_job(job_dir=job_dir, spec=spec)
    (job_dir / "training-job.zip").write_bytes(bundle)
    (job_dir / "production-runtime-policy.json").write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 0
    )

    lifecycle = worker.load_container_lifecycle_receipt(
        (job_dir / "container-lifecycle.json").read_bytes()
    )
    assert lifecycle.runtime_policy_sha256 == policy.content_sha256
    assert _subcommands(fake).index("create") < _subcommands(fake).index(
        "start"
    )


def test_worker_rejects_runtime_policy_swap_before_container_create(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    bundle = b"verified-training-bundle"
    policy = _runtime_policy()
    job_dir.parent.parent.mkdir(parents=True, exist_ok=True)
    spec = RemoteWorkerSpec(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256=_sha(bundle),
        runtime_policy_sha256="f" * 64,
        remote_target_sha256=policy.expected_remote_target_sha256,
        durable_job_ref_sha256="3" * 64,
    )
    initialize_job(job_dir=job_dir, spec=spec)
    (job_dir / "training-job.zip").write_bytes(bundle)
    (job_dir / "production-runtime-policy.json").write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )

    assert "create" not in _subcommands(fake)
    assert "start" not in _subcommands(fake)
    assert not (job_dir / "container-lifecycle.json").exists()


def test_worker_container_entrypoint_gates_training_in_same_instance(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 0
    )

    create = next(call for call in fake.calls if call[1] == "create")
    assert any(
        value.endswith(
            ",dst=/nantai-host/container-runtime,readonly"
        )
        for value in create
    )
    checker_index = create.index(
        "cloud/production_runtime_entrypoint.py"
    )
    separator_index = create.index("--")
    training_index = create.index(
        "cloud/train_3dgs_nerfstudio.sh"
    )
    assert checker_index < separator_index < training_index
    assert create[training_index - 1] == "/bin/bash"


def test_worker_rejects_unbound_clearance_entrypoint_before_create(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    bundle = b"verified-training-bundle"
    original = _runtime_policy()
    fields = original.model_dump(
        exclude={"policy_id", "content_sha256"}
    )
    fields["expected_checker_sha256"] = "f" * 64
    policy = ProductionRuntimePolicy.create(**fields)
    job_dir.parent.parent.mkdir(parents=True, exist_ok=True)
    initialize_job(
        job_dir=job_dir,
        spec=RemoteWorkerSpec(
            job_id="job-1",
            attempt_id="attempt-1",
            request_sha256="a" * 64,
            training_bundle_sha256=_sha(bundle),
            runtime_policy_sha256=policy.content_sha256,
            remote_target_sha256=(
                policy.expected_remote_target_sha256
            ),
            durable_job_ref_sha256="3" * 64,
        ),
    )
    (job_dir / "training-job.zip").write_bytes(bundle)
    (job_dir / "production-runtime-policy.json").write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )
    assert "create" not in _subcommands(fake)
    assert "start" not in _subcommands(fake)


def test_worker_uses_v2_builder_for_accepted_runtime_evidence(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(production_runtime_marker=True)
    _patch_worker(monkeypatch, fake)
    calls = []

    def build_v2(**kwargs):
        calls.append(kwargs)
        assert (
            kwargs["result_root"] / "container-id.txt"
        ).read_bytes() == (_DEFAULT_CONTAINER_ID + "\n").encode("ascii")
        assert {
            path.name
            for path in (
                kwargs["result_root"] / "production-runtime"
            ).iterdir()
        } == {"measurement.json", "policy.json", "decision.json"}
        return SimpleNamespace(
            bundle_sha256="f" * 64,
            byte_length=123,
        )

    monkeypatch.setattr(
        worker,
        "build_production_remote_result_bundle",
        build_v2,
    )
    monkeypatch.setattr(
        worker,
        "build_remote_result_bundle",
        lambda **_kwargs: pytest.fail("v1 builder must be unreachable"),
    )

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 0
    )
    assert len(calls) == 1
    assert calls[0]["container_instance_id"] == _DEFAULT_CONTAINER_ID
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024)
    )
    assert status.result_bundle_sha256 == "f" * 64


def test_worker_never_overwrites_result_container_id_collision(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    original = b"attacker-controlled-container-id\n"
    fake = FakeDocker(
        production_runtime_marker=True,
        result_container_id_marker=original,
    )
    _patch_worker(monkeypatch, fake)
    build_calls = []

    def build_v2(**kwargs):
        build_calls.append(kwargs)
        return SimpleNamespace(
            bundle_sha256="f" * 64,
            byte_length=123,
        )

    monkeypatch.setattr(
        worker,
        "build_production_remote_result_bundle",
        build_v2,
    )

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        != 0
    )
    assert build_calls == []
    assert (
        job_dir
        / "runtime"
        / "production-run"
        / "result"
        / "container-id.txt"
    ).read_bytes() == original


def test_runtime_evidence_materialization_rejects_extra_source_member(
    tmp_path,
):
    job_dir = tmp_path / "job"
    result_root = tmp_path / "result"
    source_root = job_dir / "production-runtime"
    source_root.mkdir(parents=True)
    result_root.mkdir()
    for name in (
        "measurement.json",
        "policy.json",
        "decision.json",
        "unbound.json",
    ):
        (source_root / name).write_bytes(f"{name}\n".encode("ascii"))

    with pytest.raises(
        RemoteWorkerError,
        match="file set is incomplete",
    ):
        worker._materialize_runtime_evidence(
            job_dir=job_dir,
            result_root=result_root,
            container_id=_DEFAULT_CONTAINER_ID,
        )

    assert not (result_root / "production-runtime").exists()
    assert not (result_root / "container-id.txt").exists()


# ---------------------------------------------------------------------------
# E1: durable container lifecycle receipt
# ---------------------------------------------------------------------------


def _expected_workspace_identity_sha256(
    *,
    job_id: str,
    attempt_id: str,
    workspace: Path,
) -> str:
    payload = (
        json.dumps(
            {
                "attempt_id": attempt_id,
                "job_id": job_id,
                "workspace": str(workspace.absolute()),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def test_worker_publishes_closed_lifecycle_after_digest_before_start(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    original_run = fake.run
    lifecycle_seen_at_start: list[bytes] = []

    def observe_start(argv, **kwargs):
        if len(argv) > 1 and argv[1] == "start":
            lifecycle_seen_at_start.append(
                (job_dir / "container-lifecycle.json").read_bytes()
            )
        return original_run(argv, **kwargs)

    monkeypatch.setattr(
        "cloud.remote_training_worker.subprocess.run",
        observe_start,
    )
    monkeypatch.setattr(
        "cloud.remote_training_worker.shutil.which",
        lambda name: (
            str(_TEST_EXECUTABLE)
            if name in {"docker", "podman"}
            else None
        ),
    )

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 0
    )

    assert len(lifecycle_seen_at_start) == 1
    raw = lifecycle_seen_at_start[0]
    receipt = json.loads(raw, object_pairs_hook=dict)
    assert set(receipt) == {
        "schema",
        "job_id",
        "attempt_id",
        "request_sha256",
        "training_bundle_sha256",
        "runtime_policy_sha256",
        "workspace_identity_sha256",
        "container_identity",
        "container_id",
        "transition",
        "receipt_sha256",
    }
    assert receipt["schema"] == "nantai.remote-container-lifecycle.v2"
    assert receipt["job_id"] == "job-1"
    assert receipt["attempt_id"] == "attempt-1"
    assert receipt["request_sha256"] == "a" * 64
    assert receipt["training_bundle_sha256"] == _sha(
        b"verified-training-bundle"
    )
    assert receipt["runtime_policy_sha256"] == (
        _runtime_policy().content_sha256
    )
    assert receipt["workspace_identity_sha256"] == (
        _expected_workspace_identity_sha256(
            job_id="job-1",
            attempt_id="attempt-1",
            workspace=job_dir,
        )
    )
    assert receipt["container_identity"] == _CONTAINER_IDENTITY
    assert receipt["container_id"] == _DEFAULT_CONTAINER_ID
    assert receipt["transition"] == (
        "container-created-identity-verified"
    )
    unhashed = dict(receipt)
    receipt_sha256 = unhashed.pop("receipt_sha256")
    canonical_unhashed = (
        json.dumps(
            unhashed,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    assert receipt_sha256 == hashlib.sha256(
        canonical_unhashed
    ).hexdigest()
    assert raw == (
        json.dumps(
            receipt,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    for forbidden in (
        "timestamp",
        "history",
        "evidence",
        "config_identity",
        "gpu",
        "cuda",
        "python",
        "nerfstudio",
        "readiness",
        "accepted",
    ):
        assert forbidden not in receipt


def test_lifecycle_reader_is_bounded_regular_canonical_and_duplicate_safe(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    lifecycle = job_dir / "container-lifecycle.json"
    lifecycle.write_bytes(b'{"schema":"duplicate","schema":"value"}\n')

    with pytest.raises(RemoteWorkerError, match="validation|duplicate"):
        worker.read_lifecycle(job_dir, max_bytes=1024)

    lifecycle.write_bytes(b"x" * 1025)
    with pytest.raises(RemoteWorkerError, match="size"):
        worker.read_lifecycle(job_dir, max_bytes=1024)

    lifecycle.unlink()
    target = job_dir / "real-lifecycle.json"
    target.write_bytes(b"{}\n")
    try:
        lifecycle.symlink_to(target)
    except (OSError, NotImplementedError):
        original_lstat = Path.lstat

        def link_like_lstat(path):
            if path == lifecycle:
                return SimpleNamespace(st_mode=0o120777)
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", link_like_lstat)
    with pytest.raises(RemoteWorkerError, match="link-like"):
        worker.read_lifecycle(job_dir, max_bytes=1024)


def test_stable_reader_rejects_growth_beyond_bound_before_allocation(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "bounded.json"
    path.write_bytes(b"x")
    original_open = Path.open

    def growing_open(target, mode="r", *args, **kwargs):
        if target == path and mode == "rb":
            return io.BytesIO(b"x" * 1025)
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)

    with pytest.raises(RemoteWorkerError, match="size"):
        worker._read_stable(path, max_bytes=1024, label="bounded test")


def test_stable_reader_rejects_file_mutation_during_read(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "mutating.json"
    path.write_bytes(b"{}\n")
    original_lstat = Path.lstat
    calls = 0

    def mutating_lstat(target):
        nonlocal calls
        measured = original_lstat(target)
        if target != path:
            return measured
        calls += 1
        if calls == 1:
            return measured
        values = list(measured)
        values[8] += 1
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", mutating_lstat)

    with pytest.raises(RemoteWorkerError, match="changed"):
        worker._read_stable(path, max_bytes=1024, label="mutation test")


def _lifecycle_fixture(job_dir: Path):
    policy = _runtime_policy()
    return worker.build_container_lifecycle_receipt(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256=_sha(b"verified-training-bundle"),
        runtime_policy_sha256=policy.content_sha256,
        workspace_path=str(job_dir.absolute()),
        container_identity=_CONTAINER_IDENTITY,
        container_id="b" * 64,
    )


def test_lifecycle_reader_returns_only_valid_canonical_bytes(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    receipt = _lifecycle_fixture(job_dir)
    canonical = worker.canonical_container_lifecycle_bytes(receipt)
    lifecycle = job_dir / "container-lifecycle.json"
    lifecycle.write_bytes(canonical)

    assert worker.read_lifecycle(job_dir, max_bytes=1024) == canonical

    lifecycle.write_bytes(canonical.replace(b",", b", ", 1))
    with pytest.raises(RemoteWorkerError, match="canonical"):
        worker.read_lifecycle(job_dir, max_bytes=1024)


def test_lifecycle_cli_outputs_only_canonical_bytes(
    tmp_path,
    capsysbinary,
):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    receipt = _lifecycle_fixture(job_dir)
    canonical = worker.canonical_container_lifecycle_bytes(receipt)
    (job_dir / "container-lifecycle.json").write_bytes(canonical)

    assert worker.main(
        [
            "lifecycle",
            "--job-dir",
            str(job_dir),
            "--max-bytes",
            "1024",
        ]
    ) == 0
    captured = capsysbinary.readouterr()
    assert captured.out == canonical
    assert captured.err == b""


def test_worker_lifecycle_no_replace_collision_preserves_receipt(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    original = _lifecycle_fixture(job_dir)
    lifecycle_path = job_dir / "container-lifecycle.json"
    original_bytes = worker.canonical_container_lifecycle_bytes(original)
    lifecycle_path.write_bytes(original_bytes)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )

    assert lifecycle_path.read_bytes() == original_bytes
    assert "start" not in _subcommands(fake)
    assert "create" not in _subcommands(fake)
    assert "rm" not in _subcommands(fake)
    assert not (job_dir / "container-id.txt").exists()
    assert not (job_dir / "status.json").exists()


def test_worker_rejects_lifecycle_symlink_before_container_creation(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    target = tmp_path / "existing-lifecycle.json"
    target.write_bytes(
        worker.canonical_container_lifecycle_bytes(
            _lifecycle_fixture(job_dir)
        )
    )
    lifecycle = job_dir / "container-lifecycle.json"
    try:
        lifecycle.symlink_to(target)
    except (OSError, NotImplementedError):
        original_lstat = Path.lstat

        def link_like_lstat(path):
            if path == lifecycle:
                return SimpleNamespace(st_mode=0o120777)
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", link_like_lstat)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )

    assert fake.calls == []
    assert not (job_dir / "container-id.txt").exists()
    assert not (job_dir / "status.json").exists()


def test_worker_lifecycle_sync_unknown_preserves_published_container(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)
    real_publish = worker.publish_file_noreplace

    def publish_then_fail(source, destination):
        if Path(destination).name != "container-lifecycle.json":
            return real_publish(source, destination)
        os.link(source, destination)
        raise DurableIOError(
            "lifecycle namespace published but sync failed",
            published=True,
        )

    monkeypatch.setattr(
        worker,
        "publish_file_noreplace",
        publish_then_fail,
    )

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )

    lifecycle_path = job_dir / "container-lifecycle.json"
    assert lifecycle_path.is_file()
    assert worker.read_lifecycle(
        job_dir,
        max_bytes=64 * 1024,
    ) == lifecycle_path.read_bytes()
    assert (
        job_dir / "container-id.txt"
    ).read_text(encoding="ascii").strip() == _DEFAULT_CONTAINER_ID
    assert "start" not in _subcommands(fake)
    assert "rm" not in _subcommands(fake)


def test_worker_lifecycle_publish_race_preserves_both_identities(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    conflicting = _lifecycle_fixture(job_dir)
    conflicting_bytes = worker.canonical_container_lifecycle_bytes(
        conflicting
    )
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)
    real_publish = worker.publish_file_noreplace
    lifecycle_publish_attempts = 0

    def publish_racing_destination(source, destination):
        nonlocal lifecycle_publish_attempts
        destination = Path(destination)
        if destination.name != "container-lifecycle.json":
            return real_publish(source, destination)
        lifecycle_publish_attempts += 1
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(conflicting_bytes)
        raise FileExistsError(
            "competing writer published lifecycle first"
        )

    monkeypatch.setattr(
        worker,
        "publish_file_noreplace",
        publish_racing_destination,
    )

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )

    assert lifecycle_publish_attempts == 1
    assert (
        job_dir / "container-lifecycle.json"
    ).read_bytes() == conflicting_bytes
    assert (
        job_dir / "container-id.txt"
    ).read_text(encoding="ascii").strip() == _DEFAULT_CONTAINER_ID
    assert "start" not in _subcommands(fake)
    assert "rm" not in _subcommands(fake)
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "running"
    assert not (job_dir / "cleanup-observation.json").exists()


def test_worker_lifecycle_staging_collision_is_unpublished_failure(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    staging = job_dir / ".container-lifecycle.collision.tmp"
    staging.write_bytes(b"other-writer-staging\n")
    monkeypatch.setattr(
        worker.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="collision"),
    )
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 75
    )

    assert staging.read_bytes() == b"other-writer-staging\n"
    assert not (job_dir / "container-lifecycle.json").exists()
    assert "start" not in _subcommands(fake)
    assert "rm" in _subcommands(fake)
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "failed"


def test_lifecycle_staging_cleanup_preserves_published_durable_error(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    receipt = worker.build_container_lifecycle_receipt(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256="b" * 64,
        runtime_policy_sha256=_runtime_policy().content_sha256,
        workspace_path=str(job_dir.absolute()),
        container_identity=_CONTAINER_IDENTITY,
        container_id=_DEFAULT_CONTAINER_ID,
    )
    original_unlink = Path.unlink

    def publish_then_fail(source, destination):
        os.link(source, destination)
        raise DurableIOError(
            "lifecycle namespace published but sync failed",
            published=True,
        )

    def reject_staging_cleanup(path, *, missing_ok=False):
        if path.name.startswith(".container-lifecycle."):
            raise OSError("staging cleanup failed")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(
        worker,
        "publish_file_noreplace",
        publish_then_fail,
    )
    monkeypatch.setattr(Path, "unlink", reject_staging_cleanup)

    with pytest.raises(DurableIOError) as exc_info:
        worker._publish_container_lifecycle(job_dir, receipt)

    assert exc_info.value.published is True
    assert (job_dir / "container-lifecycle.json").read_bytes() == (
        worker.canonical_container_lifecycle_bytes(receipt)
    )


def test_lifecycle_staging_cleanup_preserves_unpublished_durable_error(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    receipt = _lifecycle_fixture(job_dir)
    original_unlink = Path.unlink

    def reject_publish(_source, _destination):
        raise DurableIOError(
            "lifecycle namespace was not published",
            published=False,
        )

    def reject_staging_cleanup(path, *, missing_ok=False):
        if path.name.startswith(".container-lifecycle."):
            raise OSError("staging cleanup failed")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(
        worker,
        "publish_file_noreplace",
        reject_publish,
    )
    monkeypatch.setattr(Path, "unlink", reject_staging_cleanup)

    with pytest.raises(DurableIOError) as exc_info:
        worker._publish_container_lifecycle(job_dir, receipt)

    assert exc_info.value.published is False
    assert not (job_dir / "container-lifecycle.json").exists()


def test_worker_lifecycle_order_brackets_digest_and_start(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    original_run = fake.run
    original_publish = worker._publish_container_lifecycle
    observed: list[str] = []

    def observe_runtime(argv, **kwargs):
        subcommand = argv[1] if len(argv) > 1 else ""
        if subcommand in {"image", "inspect"}:
            assert not (job_dir / "container-lifecycle.json").exists()
            observed.append("digest-inspect")
        if subcommand == "start":
            assert (job_dir / "container-lifecycle.json").is_file()
            observed.append("start")
        return original_run(argv, **kwargs)

    def observe_lifecycle_publish(job_path, receipt):
        assert (job_path / "container-id.txt").read_text(
            encoding="ascii"
        ).strip() == _DEFAULT_CONTAINER_ID
        assert observed.count("digest-inspect") == 3
        observed.append("lifecycle")
        return original_publish(job_path, receipt)

    monkeypatch.setattr(
        "cloud.remote_training_worker.subprocess.run",
        observe_runtime,
    )
    monkeypatch.setattr(
        "cloud.remote_training_worker.shutil.which",
        lambda name: (
            str(_TEST_EXECUTABLE)
            if name in {"docker", "podman"}
            else None
        ),
    )
    monkeypatch.setattr(
        worker,
        "_publish_container_lifecycle",
        observe_lifecycle_publish,
    )

    assert (
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
        == 0
    )
    assert observed == [
        "digest-inspect",
        "digest-inspect",
        "digest-inspect",
        "lifecycle",
        "start",
    ]


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
    assert "rm" not in subs
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
    monkeypatch.setattr(
        "cloud.remote_training_worker.shutil.which",
        lambda name: (
            str(_TEST_EXECUTABLE)
            if name in {"docker", "podman"}
            else None
        ),
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
    assert not (job_dir / "container-lifecycle.json").exists()


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
        runtime_policy_sha256=_runtime_policy().content_sha256,
        remote_target_sha256=(
            _runtime_policy().expected_remote_target_sha256
        ),
        durable_job_ref_sha256="3" * 64,
    )
    initialize_job(job_dir=job_dir, spec=spec)

    with pytest.raises(RemoteWorkerError, match="absent"):
        initialize_job(job_dir=job_dir, spec=spec)
    assert json.loads(
        (job_dir / "job-spec.json").read_text(encoding="ascii")
    )["job_id"] == "job-1"
    with pytest.raises(RemoteWorkerError, match="status"):
        read_status(job_dir, max_bytes=64 * 1024)


# ---------------------------------------------------------------------------
# NOW-4 Codex review rework: durable publication + bound image identity
# ---------------------------------------------------------------------------


def test_worker_rejects_wrong_resolved_image_id_when_config_ref_matches(
    tmp_path, monkeypatch,
):
    """container .Image must equal resolved image ID, not any sha256:*.

    A wrong image ID must fail closed even when ``.Config.Image`` still
    matches the immutable repo@sha256 identity.  This is the P0 fix
    for "image content unbound": ``inspect {{.Image}}`` returning any
    ``sha256:*`` was previously accepted.
    """
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(
        resolved_image_id="sha256:" + "c" * 64,
        image_ref="sha256:" + "d" * 64,  # wrong image content
        config_image=_CONTAINER_IDENTITY,  # config ref matches
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
    subs = _subcommands(fake)
    assert "rm" in subs  # container was created, must be cleaned up


def test_worker_rejects_preexisting_container_id_without_overwrite(
    tmp_path, monkeypatch,
):
    """container-id.txt pre-existing must block; no silent overwrite."""
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    # Pre-existing container-id.txt (replay or attempt swap)
    (job_dir / "container-id.txt").write_bytes(
        (b"x" * 64) + b"\n"
    )
    fake = FakeDocker()
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
    # Container was created, must be cleaned up
    subs = _subcommands(fake)
    assert "create" in subs
    assert "rm" in subs
    # Pre-existing container-id.txt must NOT be overwritten
    persisted = (
        (job_dir / "container-id.txt").read_bytes().strip()
    )
    assert persisted == b"x" * 64


def test_worker_records_cleanup_failure_without_rewriting_terminal_result(
    tmp_path, monkeypatch,
):
    """rm failure must be recorded but NOT rewrite the terminal status.

    A failed cleanup is captured in cleanup-observation.json; the
    terminal status.json must remain unchanged.  This addresses the
    P1 "cleanup failure silently swallowed" rejection.
    """
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker(rm_fails=True)
    _patch_worker(monkeypatch, fake)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )

    # Succeeded training, but cleanup failed — exit code reflects training
    assert returncode == 0
    status = RemoteShellStatus.model_validate_json(
        read_status(job_dir, max_bytes=64 * 1024),
    )
    assert status.state == "succeeded"
    # Cleanup observation must be recorded
    cleanup_obs = job_dir / "cleanup-observation.json"
    assert cleanup_obs.is_file()
    obs = json.loads(cleanup_obs.read_text(encoding="ascii"))
    assert obs["schema"] == "nantai.remote-cleanup-observation.v1"
    assert obs["rm_exit_code"] == 1
    assert obs["container_id_prefix"] == _DEFAULT_CONTAINER_ID[:12]
    # No secret / no full container_id / no identity
    assert _DEFAULT_CONTAINER_ID not in cleanup_obs.read_text(
        encoding="ascii"
    )
    assert _CONTAINER_IDENTITY not in cleanup_obs.read_text(
        encoding="ascii"
    )


def test_worker_duplicate_start_is_not_reported_as_reconnect_recovery(
    tmp_path, monkeypatch,
):
    """Second start_job must fail as ambiguous, not reconnect recovery.

    The previous "reconnect replay" test only verified the second call
    could not obtain the lock.  This RED asserts the failure message
    explicitly states ambiguity (not recovery), so a caller cannot
    mistake blocked second-start for a recovery path.
    """
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    first = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=_CONTAINER_IDENTITY,
        container_runtime="docker",
        detach=False,
    )
    assert first == 0

    with pytest.raises(RemoteWorkerError) as exc_info:
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=_CONTAINER_IDENTITY,
            container_runtime="docker",
            detach=False,
        )
    # Must explicitly state ambiguity, NOT "recovery" or "reconnect"
    msg = str(exc_info.value).lower()
    assert "ambiguous" in msg or "already started" in msg
    assert "recover" not in msg
    assert "reconnect" not in msg


def test_worker_does_not_remove_when_terminal_status_durability_is_unknown(
    tmp_path, monkeypatch,
):
    """If terminal status fsync is unknown, cleanup must NOT proceed.

    Fault injection: status.json write fails after start succeeded.
    The container must be preserved so audit can recover the terminal
    state.  This addresses the P0 "cleanup before durable publication".
    """
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    def publish_then_fail(source, destination):
        source = Path(source)
        destination = Path(destination)
        payload = source.read_bytes()
        source.replace(destination)
        if (
            destination.name == "status.json"
            and b'"state":"succeeded"' in payload
        ):
            raise DurableIOError(
                "status namespace published but sync failed",
                published=True,
            )

    monkeypatch.setattr(
        "cloud.remote_training_worker.atomic_replace",
        publish_then_fail,
    )

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
    assert status.state == "succeeded"
    subs = _subcommands(fake)
    assert "rm" not in subs
    assert not (job_dir / "cleanup-observation.json").exists()


def test_worker_does_not_remove_when_result_publication_durability_is_unknown(
    tmp_path, monkeypatch,
):
    """If result bundle publication fails, cleanup must NOT proceed.

    Fault injection occurs at the real durable publication primitive after
    the result namespace is visible but before durability is confirmed.
    """
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    def publish_then_fail(source, destination):
        source = Path(source)
        destination = Path(destination)
        source.replace(destination)
        raise DurableIOError(
            "result namespace published but sync failed",
            published=True,
        )

    monkeypatch.setattr(
        "pipeline.durable_io.publish_file_noreplace",
        publish_then_fail,
    )

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
    assert status.state == "running"
    assert (job_dir / "result-bundle.zip").exists()
    subs = _subcommands(fake)
    assert "rm" not in subs
    assert not (job_dir / "cleanup-observation.json").exists()


def test_worker_does_not_remove_when_container_id_durability_is_unknown(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)

    def publish_then_fail(source, destination):
        source = Path(source)
        destination = Path(destination)
        source.replace(destination)
        raise DurableIOError(
            "container id published but sync failed",
            published=True,
        )

    monkeypatch.setattr(
        "cloud.remote_training_worker.publish_file_noreplace",
        publish_then_fail,
    )

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
    assert status.state == "running"
    assert (
        job_dir / "container-id.txt"
    ).read_text(encoding="ascii").strip() == _DEFAULT_CONTAINER_ID
    assert "rm" not in _subcommands(fake)


def test_status_staging_cleanup_never_masks_unpublished_durability_error(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "status.json"
    original_unlink = Path.unlink

    def reject_replace(source, destination):
        raise DurableIOError(
            "status namespace was not published",
            published=False,
        )

    def reject_staging_cleanup(path, *, missing_ok=False):
        if path.name.startswith(".status.json.") and path.suffix == ".tmp":
            raise OSError("staging cleanup failed")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(worker, "atomic_replace", reject_replace)
    monkeypatch.setattr(Path, "unlink", reject_staging_cleanup)

    with pytest.raises(DurableIOError) as exc_info:
        worker._atomic_write(target, b"{}\n")

    assert exc_info.value.published is False
    assert "not published" in str(exc_info.value)


def test_container_id_staging_cleanup_never_masks_published_durability_error(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "container-id.txt"
    original_unlink = Path.unlink

    def publish_then_fail(source, destination):
        source = Path(source)
        destination = Path(destination)
        os.link(source, destination)
        raise DurableIOError(
            "container id namespace published but sync failed",
            published=True,
        )

    def reject_staging_cleanup(path, *, missing_ok=False):
        if (
            path.name.startswith(".container-id.txt.")
            and path.suffix == ".tmp"
        ):
            raise OSError("staging cleanup failed")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(
        worker,
        "publish_file_noreplace",
        publish_then_fail,
    )
    monkeypatch.setattr(Path, "unlink", reject_staging_cleanup)

    with pytest.raises(DurableIOError) as exc_info:
        worker._publish_container_id(target, _DEFAULT_CONTAINER_ID)

    assert exc_info.value.published is True
    assert "published but sync failed" in str(exc_info.value)
    assert target.read_text(encoding="ascii").strip() == _DEFAULT_CONTAINER_ID


def test_worker_cleanup_observation_failure_never_rewrites_terminal_status(
    tmp_path,
    monkeypatch,
):
    job_dir = tmp_path / "jobs" / "job-1" / "attempt-1"
    _init_job(job_dir)
    fake = FakeDocker()
    _patch_worker(monkeypatch, fake)
    rm_calls = 0
    original_run = fake.run

    def counting_run(argv, **kwargs):
        nonlocal rm_calls
        if len(argv) > 1 and argv[1] == "rm":
            rm_calls += 1
        return original_run(argv, **kwargs)

    monkeypatch.setattr(
        "cloud.remote_training_worker.subprocess.run",
        counting_run,
    )

    def replace_unless_cleanup(source, destination):
        source = Path(source)
        destination = Path(destination)
        if destination.name == "cleanup-observation.json":
            raise DurableIOError(
                "cleanup observation was not published",
                published=False,
            )
        os.replace(source, destination)

    monkeypatch.setattr(
        "cloud.remote_training_worker.atomic_replace",
        replace_unless_cleanup,
    )

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
    assert status.state == "succeeded"
    assert rm_calls == 1
    assert not (job_dir / "cleanup-observation.json").exists()
