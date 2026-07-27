from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cloud import remote_readiness_checker as checker

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "cloud/remote_readiness_checker.py"
WORKER = ROOT / "cloud/remote_training_worker.py"
CONTAINER_IDENTITY = (
    "registry.example/nantai@sha256:" + ("c" * 64)
)


def test_remote_readiness_checker_is_directly_runnable():
    result = subprocess.run(
        [sys.executable, "-I", str(CHECKER), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "immutable remote runtime identity" in result.stdout


def test_remote_worker_exposes_stable_version():
    result = subprocess.run(
        [sys.executable, "-I", str(WORKER), "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1.0.0"


def _write_config(tmp_path: Path) -> tuple[Path, Path, bytes]:
    worker = tmp_path / "remote_training_worker.py"
    worker.write_bytes(b"remote-worker-v1\n")
    payload = (
        json.dumps(
            {
                "schema": "nantai.remote-readiness-config.v1",
                "container_runtime": "docker",
                "container_identity": CONTAINER_IDENTITY,
                "worker_path": str(worker),
                "worker_python": sys.executable,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    config = tmp_path / "remote-readiness.json"
    config.write_bytes(payload)
    return config, worker, payload


def test_checker_measures_runtime_image_and_worker_identity(tmp_path):
    config, worker, config_bytes = _write_config(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run(argv, **kwargs):
        assert kwargs["shell"] is False
        calls.append(tuple(str(item) for item in argv))
        if argv[-1] == "--version" and argv[0] == "docker":
            return subprocess.CompletedProcess(
                argv,
                0,
                b"Docker version 28.0.0\n",
                b"",
            )
        if argv[0] == "docker":
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([CONTAINER_IDENTITY]).encode("ascii") + b"\n",
                b"",
            )
        assert Path(argv[1]) == worker
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    evidence = checker.collect_remote_readiness(
        config,
        run_command=run,
    )

    assert evidence == {
        "schema": "nantai.remote-readiness-evidence.v1",
        "checker_version": "nantai.remote-readiness-checker.v1",
        "checker_config_sha256": hashlib.sha256(
            config_bytes
        ).hexdigest(),
        "container_runtime": "docker",
        "container_runtime_version": "Docker version 28.0.0",
        "container_identity": CONTAINER_IDENTITY,
        "worker_sha256": hashlib.sha256(
            worker.read_bytes()
        ).hexdigest(),
        "worker_version": "1.0.0",
    }
    assert len(calls) == 3
    assert checker.canonical_evidence_bytes(evidence).endswith(b"\n")
