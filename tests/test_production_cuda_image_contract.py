from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pipeline.production_cuda_runtime_lock import (
    load_production_cuda_runtime_lock_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
IMAGE_ROOT = ROOT / "containers" / "production-cuda"
DOCKERFILE = IMAGE_ROOT / "Dockerfile"
LOCK = IMAGE_ROOT / "runtime-lock.json"
REQUIREMENTS_IN = IMAGE_ROOT / "requirements.in"
REQUIREMENTS_LOCK = IMAGE_ROOT / "requirements.lock"
DOCKERIGNORE = ROOT / ".dockerignore"
_APT_LINE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?=\S+$")
_PYTHON_REQUIREMENT = re.compile(
    r"^([a-z0-9][a-z0-9._-]*)(?:==\S+| @ https://\S+) \\$"
)


def _runtime_lock():
    return load_production_cuda_runtime_lock_bytes(LOCK.read_bytes())


def _package_lines(path: Path) -> tuple[str, ...]:
    return tuple(
        line
        for raw in path.read_text(encoding="ascii").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    )


def test_dockerfile_uses_only_digest_and_hash_locked_inputs() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    lock = _runtime_lock()

    for image in lock.base_images:
        assert f"FROM {image.identity}" in dockerfile
        assert image.platform_manifest_digest in dockerfile
    for artifact in lock.source_artifacts:
        assert f"sha256:{artifact.sha256}" in dockerfile
        assert artifact.url in dockerfile
    assert "pip install --require-hashes" in dockerfile
    assert "--no-build-isolation" in dockerfile
    assert "TORCH_CUDA_ARCH_LIST=\"7.5;8.0;8.6;8.9;9.0+PTX\"" in dockerfile
    assert "snapshot.ubuntu.com/ubuntu/20260701T000000Z" in dockerfile
    assert "install -m 0755 /opt/python/bin/python3.11" in dockerfile
    assert "COPY --from=builder /opt/python /opt/python" in dockerfile
    assert (
        "LD_LIBRARY_PATH=/opt/python/lib "
        "/opt/python/bin/python3.11 -c"
    ) in dockerfile
    assert "/tmp/artifacts/pyliblzfse-0.4.1.tar.gz" in dockerfile
    assert "gsplat|nerfstudio|pyliblzfse|torch|torchvision" in dockerfile
    assert "curl |" not in dockerfile
    assert ":latest" not in dockerfile
    assert "pip install nerfstudio==1.1.5" not in dockerfile
    assert "--mount=type=secret" not in dockerfile
    assert "ARG TOKEN" not in dockerfile


def test_auxiliary_lock_hashes_match_actual_bytes() -> None:
    lock = _runtime_lock()
    for item in lock.auxiliary_files:
        payload = (ROOT / item.path).read_bytes()
        assert len(payload) == item.byte_length
        assert hashlib.sha256(payload).hexdigest() == item.sha256


def test_runtime_lock_has_exact_production_contract() -> None:
    lock = _runtime_lock()
    assert lock.platform == "linux/amd64"
    assert lock.ubuntu_snapshot == "20260701T000000Z"
    assert lock.cuda_architectures == (
        "7.5",
        "8.0",
        "8.6",
        "8.9",
        "9.0+PTX",
    )
    assert {
        item.role: item.version for item in lock.source_artifacts
    } == {
        "cpython-source": "3.11.9",
        "gsplat-sdist": "1.4.0",
        "nerfstudio-wheel": "1.1.5",
        "pyliblzfse-sdist": "0.4.1",
        "torch-wheel": "2.1.2+cu118",
        "torchvision-wheel": "0.16.2+cu118",
    }
    assert lock.required_imports == (
        "gsplat",
        "nerfstudio",
        "pipeline.production_runtime_evidence",
        "torch",
        "torchmetrics",
        "torchvision",
    )


def test_apt_locks_are_sorted_unique_exact_versions() -> None:
    for name in ("apt-build.lock", "apt-runtime.lock"):
        lines = _package_lines(IMAGE_ROOT / name)
        assert lines
        assert lines == tuple(sorted(lines))
        assert len(lines) == len(set(lines))
        assert all(_APT_LINE.fullmatch(line) is not None for line in lines)


def test_python_requirement_inputs_and_hash_lock_are_closed() -> None:
    direct = REQUIREMENTS_IN.read_text(encoding="ascii")
    locked = REQUIREMENTS_LOCK.read_text(encoding="ascii")

    assert (
        "torch @ https://download-r2.pytorch.org/whl/cu118/"
        "torch-2.1.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
    ) in direct
    assert (
        "torchvision @ https://download-r2.pytorch.org/whl/cu118/"
        "torchvision-0.16.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
    ) in direct
    for requirement in (
        "nerfstudio==1.1.5",
        "gsplat==1.4.0",
        "fpsample==0.3.3",
        "numpy<2.0",
        "pydantic>=2.7",
    ):
        assert requirement in direct
    assert "--hash=sha256:" in locked
    assert "-e " not in locked
    assert "git+" not in locked
    assert (
        "torch @ https://download-r2.pytorch.org/whl/cu118/"
        "torch-2.1.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
    ) in locked
    assert (
        "torchvision @ https://download-r2.pytorch.org/whl/cu118/"
        "torchvision-0.16.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
    ) in locked
    assert (
        "051833f6174e672eb313ee1c70dbcaf97e558dc46237215407933d28f40bca85"
        in locked
    )
    assert (
        "9a784073e801c04066a5e4453306010b67bacfbff12bd57e5d65c1a638584a89"
        in locked
    )
    assert "nerfstudio==1.1.5" in locked
    assert "gsplat==1.4.0" in locked
    assert "fpsample==0.3.3" in locked
    assert "fpsample==1.0.2" not in locked
    assert (
        "be912030603108eb32b92fedb5c6afe541b933ab5ed4d713190970e438b18ff6"
        in locked
    )
    assert "nvidia-cublas-cu12" not in locked


def test_every_python_distribution_is_exact_and_hashed() -> None:
    lines = REQUIREMENTS_LOCK.read_text(encoding="ascii").splitlines()
    requirements: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        match = _PYTHON_REQUIREMENT.fullmatch(line)
        if match is not None:
            current = match.group(1)
            assert current not in requirements
            requirements[current] = []
            continue
        if current is not None and line.startswith("    --hash=sha256:"):
            requirements[current].append(line.removesuffix(" \\").strip())

    assert len(requirements) >= 200
    assert all(hashes for hashes in requirements.values())
    assert all(
        re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", digest) is not None
        for hashes in requirements.values()
        for digest in hashes
    )
    assert requirements["torch"] == [
        "--hash=sha256:"
        "051833f6174e672eb313ee1c70dbcaf97e558dc46237215407933d28f40bca85"
    ]
    assert requirements["torchvision"] == [
        "--hash=sha256:"
        "9a784073e801c04066a5e4453306010b67bacfbff12bd57e5d65c1a638584a89"
    ]


def test_build_context_excludes_private_and_release_material() -> None:
    ignored = DOCKERIGNORE.read_text(encoding="utf-8")
    for required in (
        ".nantai-studio/",
        "input/",
        "output/",
        "releases/",
        "trained/",
        "handoff/",
        ".git/",
    ):
        assert required in ignored
    assert "!containers/production-cuda/" in ignored
    assert "!pipeline/" in ignored
    assert "!cloud/probe_production_cuda_image.py" in ignored
