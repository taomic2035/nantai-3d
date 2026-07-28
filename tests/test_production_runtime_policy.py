from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import pipeline.production_runtime_policy as runtime_policy_producer
from cloud.production_runtime_entrypoint import (
    fixed_production_probe_set_sha256,
)
from pipeline.production_runtime_evidence import (
    canonical_production_runtime_policy_bytes,
    load_production_runtime_policy_bytes,
)
from pipeline.production_runtime_policy import (
    ProductionRuntimePolicyInput,
    ProductionRuntimePolicyProducerError,
    canonical_production_runtime_policy_input_bytes,
    create_production_runtime_policy,
    load_production_runtime_policy_input_bytes,
    materialize_production_runtime_policy,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _run(*argv: str, cwd: Path) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, bytes, bytes]:
    root = tmp_path / "repo"
    cloud = root / "cloud"
    cloud.mkdir(parents=True)
    checker = b"#!/usr/bin/env python3\nprint('checker')\n"
    worker = b"#!/usr/bin/env python3\nprint('worker')\n"
    (cloud / "production_runtime_entrypoint.py").write_bytes(checker)
    (cloud / "remote_training_worker.py").write_bytes(worker)
    _run("git", "init", "--initial-branch=main", cwd=root)
    _run("git", "config", "user.email", "tests@example.invalid", cwd=root)
    _run("git", "config", "user.name", "Nantai Tests", cwd=root)
    _run("git", "add", "--", "cloud", cwd=root)
    _run("git", "commit", "-m", "fixture", cwd=root)
    commit = _run("git", "rev-parse", "HEAD", cwd=root)
    return root, commit, checker, worker


def _operator_input(**updates) -> ProductionRuntimePolicyInput:
    fields = {
        "expected_remote_target_sha256": _sha("target"),
        "expected_container_identity": (
            f"registry.example/nantai@sha256:{_sha('image')}"
        ),
        "expected_gpu_uuid": (
            "GPU-12345678-1234-1234-1234-123456789abc"
        ),
        "min_gpu_memory_mib": 16_384,
        "expected_cuda_runtime_version": "12.8",
        "expected_python_version": "3.11.9",
        "expected_nerfstudio_version": "1.1.5",
        "expected_training_cli_schema_sha256": _sha("ns-train-help"),
        "required_training_cli_options": (
            "--data",
            "--output-dir",
        ),
        "expected_container_runtime_sha256": _sha("docker"),
        "expected_nvidia_smi_sha256": _sha("nvidia-smi"),
        "expected_python_sha256": _sha("python"),
        "expected_training_cli_sha256": _sha("ns-train"),
    }
    fields.update(updates)
    return ProductionRuntimePolicyInput(**fields)


def _write_input(path: Path, value: ProductionRuntimePolicyInput) -> None:
    path.write_bytes(canonical_production_runtime_policy_input_bytes(value))


def test_policy_is_deterministic_and_binds_exact_committed_artifacts(
    tmp_path: Path,
) -> None:
    root, commit, checker, worker = _repository(tmp_path)
    operator_input = _operator_input()

    first = create_production_runtime_policy(
        repo_root=root,
        operator_input=operator_input,
    )
    second = create_production_runtime_policy(
        repo_root=root,
        operator_input=operator_input,
    )

    assert first == second
    assert first.expected_exact_commit == commit
    assert (
        first.expected_probe_set_sha256
        == fixed_production_probe_set_sha256()
    )
    assert first.expected_checker_sha256 == hashlib.sha256(
        checker
    ).hexdigest()
    assert first.expected_worker_sha256 == hashlib.sha256(
        worker
    ).hexdigest()
    assert first.expected_remote_target_sha256 == _sha("target")


def test_input_loader_requires_exact_canonical_bytes() -> None:
    operator_input = _operator_input()
    canonical = canonical_production_runtime_policy_input_bytes(
        operator_input
    )

    assert (
        load_production_runtime_policy_input_bytes(canonical)
        == operator_input
    )
    decoded = json.loads(canonical)
    noncanonical = json.dumps(decoded, indent=2).encode("ascii")
    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="not canonical",
    ):
        load_production_runtime_policy_input_bytes(noncanonical)

    duplicate = canonical.replace(
        b'{"expected_container_identity":',
        b'{"schema":"nantai.production-runtime-policy-input.v1",'
        b'"expected_container_identity":',
        1,
    )
    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="duplicate",
    ):
        load_production_runtime_policy_input_bytes(duplicate)


@pytest.mark.parametrize(
    "field",
    [
        "expected_remote_target_sha256",
        "expected_training_cli_schema_sha256",
        "expected_container_runtime_sha256",
        "expected_nvidia_smi_sha256",
        "expected_python_sha256",
        "expected_training_cli_sha256",
    ],
)
def test_operator_input_rejects_repeated_character_placeholder_sha(
    field: str,
) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        _operator_input(**{field: "a" * 64})


def test_operator_input_rejects_placeholder_container_digest() -> None:
    with pytest.raises(ValueError, match="placeholder"):
        _operator_input(
            expected_container_identity=(
                f"registry.example/nantai@sha256:{'b' * 64}"
            )
        )


def test_materialization_is_private_canonical_and_no_replace(
    tmp_path: Path,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    input_path = tmp_path / "operator-input.json"
    output_path = tmp_path / "private" / "runtime-policy.json"
    output_path.parent.mkdir()
    _write_input(input_path, _operator_input())

    result = materialize_production_runtime_policy(
        repo_root=root,
        operator_input_path=input_path,
        output_path=output_path,
    )

    payload = output_path.read_bytes()
    assert load_production_runtime_policy_bytes(payload) == result
    assert payload == canonical_production_runtime_policy_bytes(result)
    if os.name != "nt":
        assert output_path.stat().st_mode & 0o777 == 0o600

    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="must be absent",
    ):
        materialize_production_runtime_policy(
            repo_root=root,
            operator_input_path=input_path,
            output_path=output_path,
        )


def test_artifact_drift_from_head_is_rejected_without_output(
    tmp_path: Path,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    (root / "cloud" / "remote_training_worker.py").write_text(
        "print('drift')\n",
        encoding="utf-8",
    )
    input_path = tmp_path / "operator-input.json"
    output_path = tmp_path / "runtime-policy.json"
    _write_input(input_path, _operator_input())

    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="differs from exact commit",
    ):
        materialize_production_runtime_policy(
            repo_root=root,
            operator_input_path=input_path,
            output_path=output_path,
        )

    assert not output_path.exists()


def test_dirty_repository_is_rejected_without_output(
    tmp_path: Path,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    (root / "untracked.txt").write_text("not committed\n", encoding="utf-8")
    input_path = tmp_path / "operator-input.json"
    output_path = tmp_path / "runtime-policy.json"
    _write_input(input_path, _operator_input())

    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="must be clean",
    ):
        materialize_production_runtime_policy(
            repo_root=root,
            operator_input_path=input_path,
            output_path=output_path,
        )

    assert not output_path.exists()


def test_inherited_git_repository_overrides_cannot_redirect_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, commit, checker, worker = _repository(tmp_path)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing-git-dir"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "missing-worktree"))

    policy = create_production_runtime_policy(
        repo_root=root,
        operator_input=_operator_input(),
    )

    assert policy.expected_exact_commit == commit
    assert policy.expected_checker_sha256 == hashlib.sha256(
        checker
    ).hexdigest()
    assert policy.expected_worker_sha256 == hashlib.sha256(
        worker
    ).hexdigest()


def test_publication_race_never_replaces_competing_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    input_path = tmp_path / "operator-input.json"
    output_path = tmp_path / "runtime-policy.json"
    _write_input(input_path, _operator_input())
    actual_publish = runtime_policy_producer.publish_file_noreplace

    def race_publish(source: Path, destination: Path) -> None:
        destination.write_bytes(b"competing-writer\n")
        actual_publish(source, destination)

    monkeypatch.setattr(
        runtime_policy_producer,
        "publish_file_noreplace",
        race_publish,
    )

    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="publication is ambiguous",
    ):
        materialize_production_runtime_policy(
            repo_root=root,
            operator_input_path=input_path,
            output_path=output_path,
        )

    assert output_path.read_bytes() == b"competing-writer\n"
    assert not tuple(tmp_path.glob(".runtime-policy.json.*.staging"))


def test_symlinked_operator_input_is_rejected_without_output(
    tmp_path: Path,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    real_input = tmp_path / "real-input.json"
    linked_input = tmp_path / "linked-input.json"
    output_path = tmp_path / "runtime-policy.json"
    _write_input(real_input, _operator_input())
    try:
        linked_input.symlink_to(real_input)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="real regular file",
    ):
        materialize_production_runtime_policy(
            repo_root=root,
            operator_input_path=linked_input,
            output_path=output_path,
        )

    assert not output_path.exists()


def test_missing_output_parent_is_rejected_without_creating_directories(
    tmp_path: Path,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    input_path = tmp_path / "operator-input.json"
    output_path = tmp_path / "missing" / "runtime-policy.json"
    _write_input(input_path, _operator_input())

    with pytest.raises(
        ProductionRuntimePolicyProducerError,
        match="output parent must be a real directory",
    ):
        materialize_production_runtime_policy(
            repo_root=root,
            operator_input_path=input_path,
            output_path=output_path,
        )

    assert not output_path.parent.exists()


def test_cli_publishes_policy_without_echoing_operator_input(
    tmp_path: Path,
) -> None:
    root, _, _, _ = _repository(tmp_path)
    input_path = tmp_path / "operator-input.json"
    output_path = tmp_path / "runtime-policy.json"
    _write_input(input_path, _operator_input())

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline.production_runtime_policy",
            "--repo-root",
            str(root),
            "--operator-input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    policy = load_production_runtime_policy_bytes(output_path.read_bytes())
    assert completed.stdout.splitlines() == [
        f"content_sha256={policy.content_sha256}",
        f"output={output_path.absolute()}",
    ]
    assert completed.stderr == ""
    assert _sha("target") not in completed.stdout
