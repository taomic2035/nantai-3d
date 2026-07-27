from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cloud.production_runtime_entrypoint import (
    ProductionRuntimeEntrypointError,
    fixed_production_probe_set_sha256,
    run_clearance_and_train,
)
from cloud.remote_training_worker import build_container_lifecycle_receipt
from pipeline.production_runtime_evidence import (
    ProductionRuntimePolicy,
    canonical_production_runtime_policy_bytes,
    load_production_runtime_decision_bytes,
    load_production_runtime_measurement_bytes,
    training_cli_schema_sha256,
)
from pipeline.remote_shell_executor import (
    canonical_container_lifecycle_bytes,
)

_CONTAINER = "registry.example/nantai@sha256:" + "c" * 64
_CONTAINER_ID = "a" * 64
_GPU_UUID = "GPU-12345678-1234-1234-1234-123456789abc"
_OPTIONS = (
    "--auto-scale-poses",
    "--center-method",
    "--orientation-method",
    "--scale-factor",
)
_NOW = datetime(2026, 7, 28, 1, 2, 3, tzinfo=UTC)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_executable(target: Path) -> Path:
    source = Path(sys.executable).resolve()
    shutil.copyfile(source, target)
    shutil.copymode(source, target)
    return target


def _fixture(tmp_path: Path):
    job_dir = tmp_path / "job"
    repo_root = tmp_path / "repo"
    bin_root = tmp_path / "bin"
    job_dir.mkdir()
    (repo_root / "cloud").mkdir(parents=True)
    bin_root.mkdir()
    checker = repo_root / "cloud" / "production_runtime_entrypoint.py"
    worker = repo_root / "cloud" / "remote_training_worker.py"
    checker.write_bytes(b"checker-source\n")
    worker.write_bytes(b"worker-source\n")
    runtime = _copy_executable(bin_root / "docker.exe")
    python = _copy_executable(bin_root / "python.exe")
    ns_train = _copy_executable(bin_root / "ns-train.exe")
    nvidia_smi = _copy_executable(bin_root / "nvidia-smi.exe")
    git = _copy_executable(bin_root / "git.exe")
    policy = ProductionRuntimePolicy.create(
        expected_exact_commit="1" * 40,
        expected_remote_target_sha256="2" * 64,
        expected_probe_set_sha256=fixed_production_probe_set_sha256(),
        expected_container_identity=_CONTAINER,
        expected_gpu_uuid=_GPU_UUID,
        min_gpu_memory_mib=16_384,
        expected_cuda_runtime_version="12.4",
        expected_python_version="3.11.9",
        expected_nerfstudio_version="1.1.5",
        expected_training_cli_schema_sha256=training_cli_schema_sha256(
            trainer_name="nerfstudio-splatfacto",
            observed_options=_OPTIONS,
        ),
        required_training_cli_options=_OPTIONS,
        expected_checker_sha256=_sha_file(checker),
        expected_container_runtime_sha256=_sha_file(runtime),
        expected_nvidia_smi_sha256=_sha_file(nvidia_smi),
        expected_python_sha256=_sha_file(python),
        expected_training_cli_sha256=_sha_file(ns_train),
        expected_worker_sha256=_sha_file(worker),
    )
    (job_dir / "production-runtime-policy.json").write_bytes(
        canonical_production_runtime_policy_bytes(policy)
    )
    lifecycle = build_container_lifecycle_receipt(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="b" * 64,
        training_bundle_sha256="d" * 64,
        runtime_policy_sha256=policy.content_sha256,
        workspace_path=str(job_dir),
        container_identity=_CONTAINER,
        container_id=_CONTAINER_ID,
    )
    (job_dir / "container-lifecycle.json").write_bytes(
        canonical_container_lifecycle_bytes(lifecycle)
    )
    (job_dir / "container-id.txt").write_bytes(
        (_CONTAINER_ID + "\n").encode("ascii")
    )
    executables = {
        "git": git,
        "ns-train": ns_train,
        "nvidia-smi": nvidia_smi,
        "python": python,
    }
    return {
        "job_dir": job_dir,
        "repo_root": repo_root,
        "checker": checker,
        "worker": worker,
        "runtime": runtime,
        "python": python,
        "ns_train": ns_train,
        "nvidia_smi": nvidia_smi,
        "executables": executables,
        "policy": policy,
    }


def _run_command(
    paths: dict[str, Path],
    *,
    gpu_uuid: str = _GPU_UUID,
    mutate_ns_train: bool = False,
):
    mutated = False

    def run(argv, **_kwargs):
        nonlocal mutated
        command = str(argv[0])
        args = tuple(str(value) for value in argv[1:])
        stdout = b""
        if command == str(paths["git"]) and args[:2] == (
            "rev-parse",
            "HEAD",
        ):
            stdout = ("1" * 40 + "\n").encode("ascii")
        elif command == str(paths["git"]) and args[:2] == (
            "status",
            "--porcelain",
        ):
            stdout = b""
        elif command == str(paths["python"]) and "platform" in " ".join(args):
            stdout = b"3.11.9\n"
        elif command == str(paths["python"]) and "nerfstudio" in " ".join(
            args
        ):
            stdout = b"1.1.5\n"
        elif command == str(paths["python"]) and "torch.version.cuda" in " ".join(
            args
        ):
            stdout = b"12.4\n"
        elif command == str(paths["nvidia-smi"]):
            stdout = (
                f"{gpu_uuid}, NVIDIA RTX 4090, 24564, "
                "575.64.03, 8.9\n"
            ).encode("ascii")
        elif command == str(paths["ns-train"]):
            stdout = (
                b"usage: ns-train splatfacto "
                b"--auto-scale-poses --center-method "
                b"--orientation-method --scale-factor\n"
            )
            if mutate_ns_train and not mutated:
                paths["ns-train"].write_bytes(
                    paths["ns-train"].read_bytes() + b"drift"
                )
                mutated = True
        else:
            return subprocess.CompletedProcess(
                argv,
                2,
                stdout=b"",
                stderr=b"unexpected command",
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=stdout,
            stderr=b"",
        )

    return run


def _invoke(fixture, *, run_command, exec_calls):
    return run_clearance_and_train(
        job_dir=fixture["job_dir"],
        repo_root=fixture["repo_root"],
        checker_path=fixture["checker"],
        worker_path=fixture["worker"],
        mounted_container_runtime_path=fixture["runtime"],
        python_executable=fixture["python"],
        container_runtime="docker",
        remote_target_sha256="2" * 64,
        durable_job_ref_sha256="3" * 64,
        expected_container_identity=_CONTAINER,
        training_argv=(
            "bash",
            "cloud/train_3dgs_nerfstudio.sh",
            "--prepared-bundle",
            "/job/training-job.zip",
        ),
        run_command=run_command,
        which=lambda name: str(fixture["executables"].get(name, "")) or None,
        exec_command=lambda executable, argv: exec_calls.append(
            (executable, tuple(argv))
        ),
        now=lambda: _NOW,
        logical_path=lambda path: f"/fixture/{path.name}",
    )


def test_accepted_clearance_publishes_closed_evidence_before_training(
    tmp_path,
):
    fixture = _fixture(tmp_path)
    exec_calls = []

    with pytest.raises(
        ProductionRuntimeEntrypointError,
        match="training exec unexpectedly returned",
    ):
        _invoke(
            fixture,
            run_command=_run_command(fixture["executables"]),
            exec_calls=exec_calls,
        )

    assert exec_calls == [
        (
            "bash",
            (
                "bash",
                "cloud/train_3dgs_nerfstudio.sh",
                "--prepared-bundle",
                "/job/training-job.zip",
            ),
        )
    ]
    evidence_root = (
        fixture["job_dir"] / "production-runtime"
    )
    assert not (
        fixture["job_dir"] / "runtime" / "production-run"
    ).exists()
    measurement = load_production_runtime_measurement_bytes(
        (evidence_root / "measurement.json").read_bytes()
    )
    decision = load_production_runtime_decision_bytes(
        (evidence_root / "decision.json").read_bytes()
    )
    assert measurement.environment.container_instance_id == _CONTAINER_ID
    assert measurement.durable_job_ref_sha256 == "3" * 64
    assert decision.status == "accepted"
    assert (evidence_root / "policy.json").read_bytes() == (
        canonical_production_runtime_policy_bytes(fixture["policy"])
    )


def test_rejected_gpu_identity_never_reaches_training(tmp_path):
    fixture = _fixture(tmp_path)
    exec_calls = []

    result = _invoke(
        fixture,
        run_command=_run_command(
            fixture["executables"],
            gpu_uuid="GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ),
        exec_calls=exec_calls,
    )

    assert result == 78
    assert exec_calls == []
    decision_path = (
        fixture["job_dir"] / "production-runtime" / "decision.json"
    )
    decision = load_production_runtime_decision_bytes(
        decision_path.read_bytes()
    )
    assert decision.status == "rejected"
    assert decision.failure_codes == ("gpu-identity-mismatch",)


def test_executable_drift_during_probe_never_reaches_training(tmp_path):
    fixture = _fixture(tmp_path)
    exec_calls = []

    with pytest.raises(
        ProductionRuntimeEntrypointError,
        match="changed during clearance",
    ):
        _invoke(
            fixture,
            run_command=_run_command(
                fixture["executables"],
                mutate_ns_train=True,
            ),
            exec_calls=exec_calls,
        )

    assert exec_calls == []


def test_container_identity_swap_never_reaches_training(tmp_path):
    fixture = _fixture(tmp_path)
    exec_calls = []
    lifecycle_path = fixture["job_dir"] / "container-id.txt"
    lifecycle_path.write_bytes(("f" * 64 + "\n").encode("ascii"))

    with pytest.raises(
        ProductionRuntimeEntrypointError,
        match="container instance identity differs",
    ):
        _invoke(
            fixture,
            run_command=_run_command(fixture["executables"]),
            exec_calls=exec_calls,
        )

    assert exec_calls == []


def test_existing_runtime_evidence_namespace_never_reaches_training(
    tmp_path,
):
    fixture = _fixture(tmp_path)
    exec_calls = []
    evidence_root = (
        fixture["job_dir"] / "production-runtime"
    )
    evidence_root.mkdir(parents=True)
    (evidence_root / "policy.json").write_bytes(b"other-writer\n")

    with pytest.raises(
        ProductionRuntimeEntrypointError,
        match="publication is ambiguous",
    ):
        _invoke(
            fixture,
            run_command=_run_command(fixture["executables"]),
            exec_calls=exec_calls,
        )

    assert exec_calls == []
    assert (evidence_root / "policy.json").read_bytes() == b"other-writer\n"
