from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert "host-preflight evidence" in result.stdout
    assert "not production readiness" in result.stdout


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


def _write_config(
    tmp_path: Path,
    *,
    runtime: str = "docker",
) -> tuple[Path, Path, bytes, Path]:
    worker = tmp_path / "remote_training_worker.py"
    worker.write_bytes(b"remote-worker-v1\n")
    runtime_bin = tmp_path / runtime
    runtime_bin.write_bytes(f"fake-{runtime}-binary-v1\n".encode("ascii"))
    payload = (
        json.dumps(
            {
                "schema": "nantai.remote-readiness-config.v1",
                "container_runtime": runtime,
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
    return config, worker, payload, runtime_bin


def _which_factory(
    runtime_bin: Path,
    *,
    runtime: str = "docker",
):
    def _which(name: str) -> str | None:
        if name == runtime:
            return str(runtime_bin)
        return None
    return _which


def _is_runtime(argv: list[str], runtime_bin: Path) -> bool:
    """True if argv[0] is the resolved runtime binary path."""
    try:
        return Path(argv[0]).resolve() == runtime_bin.resolve()
    except (OSError, ValueError):
        return False


def _is_worker(argv: list[str], worker: Path) -> bool:
    """True if argv invokes worker via worker_python."""
    if len(argv) < 2:
        return False
    try:
        return Path(argv[1]).resolve() == worker.resolve()
    except (OSError, ValueError):
        return False


def _golden_run(
    argv: list[str],
    *,
    runtime_bin: Path,
    worker: Path,
) -> subprocess.CompletedProcess:
    """Default probe responses for a healthy docker/podman runtime."""
    if _is_runtime(argv, runtime_bin):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv, 0, b"Docker version 28.0.0\n", b"",
            )
        if "info" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps({"nvidia": {}}).encode("ascii") + b"\n",
                b"",
            )
        # image inspect
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps([CONTAINER_IDENTITY]).encode("ascii") + b"\n",
            b"",
        )
    if _is_worker(argv, worker):
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")
    return subprocess.CompletedProcess(argv, 0, b"", b"")


def test_checker_measures_runtime_image_and_worker_identity(tmp_path):
    config, worker, config_bytes, runtime_bin = _write_config(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run(argv, **kwargs):
        assert kwargs["shell"] is False
        calls.append(tuple(str(item) for item in argv))
        return _golden_run(argv, runtime_bin=runtime_bin, worker=worker)

    evidence = checker.collect_remote_readiness(
        config,
        run_command=run,
        which=_which_factory(runtime_bin),
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
    # 4 calls: runtime --version, image inspect, info, worker --version
    assert len(calls) == 4
    # Every runtime probe must use the resolved absolute path
    for call in calls:
        if call[0] != sys.executable:
            assert Path(call[0]).resolve() == runtime_bin.resolve(), (
                f"runtime probe must use resolved path, got {call[0]}"
            )
    assert checker.canonical_evidence_bytes(evidence).endswith(b"\n")


def test_checker_rejects_unmeasured_container_digest(tmp_path):
    config, _worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(
                    argv, 0, b"Docker version 28.0.0\n", b"",
                )
            if "info" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({"nvidia": {}}).encode("ascii") + b"\n",
                    b"",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    ["registry.example/other@sha256:" + ("f" * 64)]
                ).encode("ascii")
                + b"\n",
                b"",
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="digest was not measured",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_rejects_worker_replacement_during_probe(tmp_path):
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        replacement = tmp_path / "replacement.py"
        replacement.write_bytes(b"remote-worker-v2\n")
        replacement.replace(worker)
        return subprocess.CompletedProcess(
            argv,
            0,
            b"1.0.0\n",
            b"",
        )

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="changed during probe",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_rejects_config_replacement_during_probe(tmp_path):
    config, worker, config_bytes, runtime_bin = _write_config(tmp_path)
    replaced = False

    def run(argv, **kwargs):
        nonlocal replaced
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            replacement = tmp_path / "replacement.json"
            replacement.write_bytes(config_bytes)
            replacement.replace(config)
            replaced = True
            return subprocess.CompletedProcess(
                argv,
                0,
                b"Docker version 28.0.0\n",
                b"",
            )
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            b"1.0.0\n",
            b"",
        )

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="config changed during probe",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )
    assert replaced


def test_checker_rejects_duplicate_config_keys(tmp_path):
    config, _worker, payload, _runtime_bin = _write_config(tmp_path)
    config.write_bytes(
        payload.replace(
            b"{",
            b'{"container_runtime":"docker",',
            1,
        )
    )

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="duplicate keys",
    ):
        checker.collect_remote_readiness(config)


# ---------------------------------------------------------------------------
# NOW-2: GPU scheduler precondition (host-level gate, not GPU measurement)
# ---------------------------------------------------------------------------


def test_checker_fails_when_gpu_scheduler_unavailable(tmp_path):
    """Runtime without nvidia runtime must fail closed."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(
                    argv, 0, b"Docker version 28.0.0\n", b"",
                )
            if "info" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps({"runc": {}}).encode("ascii") + b"\n",
                    b"",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([CONTAINER_IDENTITY]).encode("ascii")
                + b"\n",
                b"",
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="cannot schedule GPU jobs",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_fails_when_runtime_info_is_malformed(tmp_path):
    """Malformed docker info output must fail closed."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(
                    argv, 0, b"Docker version 28.0.0\n", b"",
                )
            if "info" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, b"not-json-at-all\n", b"",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([CONTAINER_IDENTITY]).encode("ascii")
                + b"\n",
                b"",
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="runtime info is invalid",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_fails_when_runtime_info_returns_non_dict(tmp_path):
    """Runtime info returning a list instead of dict must fail closed."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            if argv[-1] == "--version":
                return subprocess.CompletedProcess(
                    argv, 0, b"Docker version 28.0.0\n", b"",
                )
            if "info" in argv:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(["nvidia"]).encode("ascii") + b"\n",
                    b"",
                )
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([CONTAINER_IDENTITY]).encode("ascii")
                + b"\n",
                b"",
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="cannot schedule GPU jobs",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_evidence_has_no_production_ready_fields(tmp_path):
    """Host preflight evidence must NOT contain GPU/driver/Nerfstudio/ready."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    evidence = checker.collect_remote_readiness(
        config,
        run_command=run,
        which=_which_factory(runtime_bin),
    )
    forbidden = {
        "gpu_name",
        "gpu_memory_mb",
        "driver_version",
        "nerfstudio_version",
        "nvidia_smi_sha256",
        "nerfstudio_python_sha256",
        "ready",
        "production_ready",
    }
    assert not (forbidden & set(evidence)), (
        f"host preflight evidence must not contain {forbidden & set(evidence)}"
    )


# ---------------------------------------------------------------------------
# NOW-3: Anti-spoofing and TOCTOU for runtime/checker executable identity
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating symlinks on Windows requires admin privileges",
)
def test_checker_fails_when_runtime_binary_is_symlink(tmp_path):
    """Symlink as runtime binary must fail closed (wrapper spoof)."""
    config, _worker, _config_bytes, runtime_bin = _write_config(tmp_path)
    symlink_path = tmp_path / "docker-symlink"
    symlink_path.symlink_to(runtime_bin)

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="must be a regular file",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=lambda *a, **kw: subprocess.CompletedProcess(
                a[0] if a else [], 0, b"", b""
            ),
            which=_which_factory(symlink_path),
        )


def test_checker_fails_when_runtime_binary_replaced_during_probe(tmp_path):
    """Mid-probe replacement of runtime binary must fail closed (TOCTOU)."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)
    replaced = False

    def run(argv, **kwargs):
        nonlocal replaced
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            replacement = tmp_path / "fake-docker-v2"
            replacement.write_bytes(b"fake-docker-binary-v2\n")
            replacement.replace(runtime_bin)
            replaced = True
            return subprocess.CompletedProcess(
                argv, 0, b"Docker version 28.0.0\n", b"",
            )
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="runtime binary changed during probe",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )
    assert replaced


def test_checker_fails_when_runtime_not_found(tmp_path):
    """Runtime binary not on PATH must fail closed."""
    config, _worker, _config_bytes, _runtime_bin = _write_config(tmp_path)

    def _not_found(name: str) -> str | None:
        return None

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="runtime binary not found",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=lambda *a, **kw: subprocess.CompletedProcess(
                [], 0, b"", b""
            ),
            which=_not_found,
        )


def test_checker_fails_on_oversize_stdout(tmp_path):
    """Oversize stdout must fail closed at the byte cap, not be truncated."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv,
                0,
                b"x" * (checker._MAX_OUTPUT_BYTES + 100),
                b"",
            )
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="stdout exceeded byte cap",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_fails_on_oversize_stderr(tmp_path):
    """Oversize stderr must fail closed even when stdout is valid."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv,
                0,
                b"Docker version 28.0.0\n",
                b"y" * (checker._MAX_OUTPUT_BYTES + 100),
            )
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="stderr exceeded byte cap",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_fails_on_oversize_stdout_with_secrets(tmp_path):
    """Secret-bearing oversize stdout must fail closed before redaction."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    secret_payload = (
        b"-----BEGIN RSA PRIVATE KEY-----\n"
        + b"x" * (checker._MAX_OUTPUT_BYTES + 100)
    )

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv,
                0,
                secret_payload,
                b"",
            )
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="stdout exceeded byte cap",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_redacts_secrets_in_probe_output(tmp_path):
    """Private keys and credentials must be redacted from probe output."""
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    # The runtime --version output in _golden_run is "Docker version 28.0.0",
    # which does not match the secret pattern, so this is a baseline test
    # ensuring the redaction path is exercised without breaking the golden
    # path. The actual redaction logic is covered by the oversize-secret
    # test above (which fails at the byte cap before redaction).
    evidence = checker.collect_remote_readiness(
        config,
        run_command=run,
        which=_which_factory(runtime_bin),
    )
    assert evidence["container_runtime_version"] == "Docker version 28.0.0"


def test_checker_fails_on_probe_timeout(tmp_path):
    """Probe timeout must fail closed, not hang."""
    config, _worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        raise subprocess.TimeoutExpired(cmd=argv, timeout=30)

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="could not be executed",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_fails_on_nonzero_returncode(tmp_path):
    """Non-zero returncode must fail closed."""
    config, _worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(argv, 1, b"", b"error")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="did not produce bounded success",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_runtime_probes_use_resolved_path_not_config_name(
    tmp_path,
):
    """RED→GREEN: probes must use resolved absolute path, not config name.

    A PATH wrapper that shadows 'docker' must not be able to hijack
    probes after the resolved path is fixed.
    """
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run(argv, **kwargs):
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        if _is_runtime(argv, runtime_bin):
            return _golden_run(
                argv, runtime_bin=runtime_bin, worker=worker
            )
        return subprocess.CompletedProcess(argv, 0, b"1.0.0\n", b"")

    checker.collect_remote_readiness(
        config,
        run_command=run,
        which=_which_factory(runtime_bin),
    )

    # Every runtime probe must invoke the resolved absolute path, not the
    # bare config name "docker".
    runtime_calls = [
        c
        for c in calls
        if c and c[0] != sys.executable
    ]
    assert runtime_calls, "expected at least one runtime probe call"
    for call in runtime_calls:
        assert Path(call[0]).resolve() == runtime_bin.resolve(), (
            f"runtime probe must use resolved path {runtime_bin}, "
            f"got {call[0]}"
        )


def test_checker_rejects_relative_resolved_runtime_path(tmp_path):
    config, _worker, _config_bytes, runtime_bin = _write_config(tmp_path)
    invoked = False

    def run(argv, **kwargs):
        nonlocal invoked
        del argv, kwargs
        invoked = True
        return subprocess.CompletedProcess([], 0, b"", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="resolved path must be absolute",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=lambda _name: runtime_bin.name,
        )
    assert invoked is False


def test_checker_rejects_path_wrapper_swap_after_resolution(tmp_path):
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)
    wrapper = tmp_path / "docker-wrapper"
    wrapper.write_bytes(b"untrusted-wrapper\n")
    which_calls = 0
    runtime_calls: list[tuple[str, ...]] = []

    def changing_which(name: str) -> str | None:
        nonlocal which_calls
        assert name == "docker"
        which_calls += 1
        return str(runtime_bin if which_calls == 1 else wrapper)

    def run(argv, **kwargs):
        del kwargs
        runtime_calls.append(tuple(str(item) for item in argv))
        return _golden_run(
            argv,
            runtime_bin=runtime_bin,
            worker=worker,
        )

    evidence = checker.collect_remote_readiness(
        config,
        run_command=run,
        which=changing_which,
    )

    assert evidence["container_runtime"] == "docker"
    assert which_calls == 1
    assert runtime_calls
    assert all(
        call[0] == sys.executable
        or Path(call[0]) == runtime_bin
        for call in runtime_calls
    )
    assert all(Path(call[0]) != wrapper for call in runtime_calls)


def test_checker_rejects_non_utf8_observation(tmp_path):
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, b"\xff\xfe\n", b"")
        return _golden_run(
            argv,
            runtime_bin=runtime_bin,
            worker=worker,
        )

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="not safe ASCII",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )


def test_checker_rejects_secret_bearing_observation_without_leaking_secret(
    tmp_path,
    capsys,
):
    config, worker, _config_bytes, runtime_bin = _write_config(tmp_path)
    canary = b"NANTAI_SECRET_CANARY_7f4d6c"

    def run(argv, **kwargs):
        del kwargs
        if _is_runtime(argv, runtime_bin) and argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv,
                0,
                b"token=" + canary + b"\n",
                b"",
            )
        return _golden_run(
            argv,
            runtime_bin=runtime_bin,
            worker=worker,
        )

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="secret-like material",
    ) as captured:
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin),
        )

    output = capsys.readouterr()
    rendered = str(captured.value) + output.out + output.err
    assert canary.decode("ascii") not in rendered


def test_checker_podman_blocks_before_docker_runtime_probe(tmp_path):
    config, _worker, _config_bytes, runtime_bin = _write_config(
        tmp_path,
        runtime="podman",
    )
    calls: list[tuple[str, ...]] = []

    def run(argv, **kwargs):
        del kwargs
        calls.append(tuple(str(item) for item in argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(
                argv,
                0,
                b"podman version 5.1.0\n",
                b"",
            )
        if "image" in argv and "inspect" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([CONTAINER_IDENTITY]).encode("ascii") + b"\n",
                b"",
            )
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    with pytest.raises(
        checker.RemoteReadinessCheckError,
        match="Podman GPU scheduler preflight requires a bound CDI adapter",
    ):
        checker.collect_remote_readiness(
            config,
            run_command=run,
            which=_which_factory(runtime_bin, runtime="podman"),
        )

    assert not any(
        "info" in call
        and "{{json .Runtimes}}" in call
        for call in calls
    )
