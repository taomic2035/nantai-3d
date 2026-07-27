from __future__ import annotations

import hashlib
import json
import os
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


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_runtime(path: Path) -> Path:
    path.write_bytes(
        r"""#!/bin/bash
set -euo pipefail
printf '%s\0' "$@" > "$REMOTE_CONTAINER_ARGV"
JOB_ROOT=""
CONTAINER=""
for argument in "$@"; do
  case "$argument" in
    type=bind,src=*,dst=/job)
      JOB_ROOT="${argument#type=bind,src=}"
      JOB_ROOT="${JOB_ROOT%,dst=/job}"
      ;;
    *@sha256:*) CONTAINER="$argument" ;;
  esac
done
RESULT="$JOB_ROOT/runtime/production-run/result"
mkdir -p "$RESULT"
printf '%s\n' "$CONTAINER" > "$RESULT/container-identity.txt"
printf '%s\n' '{"scale":1.0,"transform":[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}' \
  > "$RESULT/dataparser_transforms.json"
printf 'config\n' > "$RESULT/operator-intent-config.yml"
printf 'ply\n' > "$RESULT/point_cloud.ply"
printf '{}\n' > "$RESULT/training-request.json"
printf '{}\n' > "$RESULT/training-result.json"
printf 'training complete\n' > "$RESULT/training.log"
printf 'container completed\n'
""".encode("ascii"),
    )
    path.chmod(0o755)
    return path


@pytest.mark.skipif(
    os.name == "nt",
    reason="remote worker golden path executes a Linux container runtime",
)
def test_worker_runs_digest_container_and_publishes_success(tmp_path, monkeypatch):
    remote_root = tmp_path / "jobs"
    remote_root.mkdir()
    job_dir = remote_root / "job-1" / "attempt-1"
    bundle = b"verified-training-bundle"
    spec = RemoteWorkerSpec(
        job_id="job-1",
        attempt_id="attempt-1",
        request_sha256="a" * 64,
        training_bundle_sha256=_sha(bundle),
    )
    initialize_job(job_dir=job_dir, spec=spec)
    (job_dir / "training-job.zip").write_bytes(bundle)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_runtime(bin_dir / "docker")
    argv_path = tmp_path / "container.argv"
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
    )
    monkeypatch.setenv("REMOTE_CONTAINER_ARGV", str(argv_path))
    container = "registry.example/nantai@sha256:" + ("c" * 64)

    returncode = start_job(
        job_dir=job_dir,
        repo_root=_ROOT,
        container_identity=container,
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
    tokens = [
        item.decode("utf-8")
        for item in argv_path.read_bytes().split(b"\0")
        if item
    ]
    assert tokens[:4] == ["run", "--rm", "--gpus", "all"]
    assert "--network" in tokens
    assert tokens[tokens.index("--network") + 1] == "none"
    assert container in tokens
    assert "/job/training-job.zip" in tokens

    with pytest.raises(RemoteWorkerError, match="already started"):
        start_job(
            job_dir=job_dir,
            repo_root=_ROOT,
            container_identity=container,
            container_runtime="docker",
            detach=False,
        )


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
