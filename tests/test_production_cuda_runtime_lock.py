from __future__ import annotations

import hashlib
import json

import pytest

from pipeline.production_cuda_runtime_lock import (
    LockedAuxiliaryFile,
    LockedBaseImage,
    LockedSourceArtifact,
    ProductionCudaRuntimeLock,
    ProductionCudaRuntimeLockError,
    canonical_production_cuda_runtime_lock_bytes,
    canonical_production_cuda_runtime_lock_signing_bytes,
    load_production_cuda_runtime_lock_bytes,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _base_images() -> tuple[LockedBaseImage, ...]:
    return (
        LockedBaseImage(
            role="builder",
            identity=(
                "nvidia/cuda:11.8.0-devel-ubuntu22.04@sha256:"
                "94fd755736cb58979173d491504f0b573247b1745250249415b07fefc738e41f"
            ),
            platform="linux/amd64",
            platform_manifest_digest=(
                "sha256:"
                "60eda04ab6790aa76d73bf0df245b361eabc6d8f7b6f6cf9846c70f399b9a1eb"
            ),
        ),
        LockedBaseImage(
            role="runtime",
            identity=(
                "nvidia/cuda:11.8.0-runtime-ubuntu22.04@sha256:"
                "eaaccb3528ceca110601131434ab467e41d694a41e8c9bf280fb27ac18fcb29b"
            ),
            platform="linux/amd64",
            platform_manifest_digest=f"sha256:{_digest('runtime-child')}",
        ),
    )


def _source_artifacts() -> tuple[LockedSourceArtifact, ...]:
    return (
        LockedSourceArtifact(
            role="cpython-source",
            version="3.11.9",
            filename="Python-3.11.9.tar.xz",
            url=(
                "https://www.python.org/ftp/python/3.11.9/"
                "Python-3.11.9.tar.xz"
            ),
            byte_length=20_174_824,
            sha256=(
                "9b1e896523fc510691126c864406d9360a3d1e986acbda59cda57b5abda45b87"
            ),
        ),
        LockedSourceArtifact(
            role="gsplat-sdist",
            version="1.4.0",
            filename="gsplat-1.4.0.tar.gz",
            url=(
                "https://files.pythonhosted.org/packages/source/g/gsplat/"
                "gsplat-1.4.0.tar.gz"
            ),
            byte_length=105_355,
            sha256=(
                "8aa81a785e0daf3ed60d0b9930a56c0f337280e6989351d1f1b74e21cf190160"
            ),
            upstream_commit=(
                "4d3a3b69db4de0326f983ccf7b7b255271a17b01"
            ),
        ),
        LockedSourceArtifact(
            role="nerfstudio-wheel",
            version="1.1.5",
            filename="nerfstudio-1.1.5-py3-none-any.whl",
            url=(
                "https://files.pythonhosted.org/packages/py3/n/nerfstudio/"
                "nerfstudio-1.1.5-py3-none-any.whl"
            ),
            byte_length=580_837,
            sha256=(
                "ee6d3d360a1e363ad2f1703b602da5a8987485bff812d0ae8aa4a6e672b994c4"
            ),
            upstream_commit=(
                "6b60855003011b2ca23c2fe3f8e2ca6314c69924"
            ),
        ),
        LockedSourceArtifact(
            role="torch-wheel",
            version="2.1.2+cu118",
            filename=(
                "torch-2.1.2+cu118-cp311-cp311-linux_x86_64.whl"
            ),
            url=(
                "https://download-r2.pytorch.org/whl/cu118/"
                "torch-2.1.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
            ),
            byte_length=2_250_000_000,
            sha256=(
                "051833f6174e672eb313ee1c70dbcaf97e558dc46237215407933d28f40bca85"
            ),
        ),
        LockedSourceArtifact(
            role="torchvision-wheel",
            version="0.16.2+cu118",
            filename=(
                "torchvision-0.16.2+cu118-cp311-cp311-linux_x86_64.whl"
            ),
            url=(
                "https://download-r2.pytorch.org/whl/cu118/"
                "torchvision-0.16.2%2Bcu118-cp311-cp311-linux_x86_64.whl"
            ),
            byte_length=6_900_000,
            sha256=(
                "9a784073e801c04066a5e4453306010b67bacfbff12bd57e5d65c1a638584a89"
            ),
        ),
    )


def _auxiliary_files() -> tuple[LockedAuxiliaryFile, ...]:
    return (
        LockedAuxiliaryFile(
            role="apt-build-lock",
            path="containers/production-cuda/apt-build.lock",
            byte_length=321,
            sha256=_digest("apt-build-lock"),
        ),
        LockedAuxiliaryFile(
            role="apt-runtime-lock",
            path="containers/production-cuda/apt-runtime.lock",
            byte_length=234,
            sha256=_digest("apt-runtime-lock"),
        ),
        LockedAuxiliaryFile(
            role="python-requirements-lock",
            path="containers/production-cuda/requirements.lock",
            byte_length=12_345,
            sha256=_digest("requirements-lock"),
        ),
    )


def _valid_lock() -> ProductionCudaRuntimeLock:
    return ProductionCudaRuntimeLock.create(
        platform="linux/amd64",
        ubuntu_snapshot="20260701T000000Z",
        cuda_architectures=("7.5", "8.0", "8.6", "8.9", "9.0+PTX"),
        base_images=_base_images(),
        source_artifacts=_source_artifacts(),
        auxiliary_files=_auxiliary_files(),
        required_imports=(
            "gsplat",
            "nerfstudio",
            "pipeline.production_runtime_evidence",
            "torch",
            "torchmetrics",
            "torchvision",
        ),
    )


def _canonical_dict() -> dict:
    return json.loads(
        canonical_production_cuda_runtime_lock_bytes(_valid_lock())
    )


def _canonical_payload(document: dict) -> bytes:
    return (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def test_runtime_lock_is_canonical_content_addressed() -> None:
    lock = _valid_lock()
    payload = canonical_production_cuda_runtime_lock_bytes(lock)
    loaded = load_production_cuda_runtime_lock_bytes(payload)

    assert loaded == lock
    assert loaded.platform == "linux/amd64"
    assert {image.role for image in loaded.base_images} == {
        "builder",
        "runtime",
    }
    assert loaded.content_sha256 == hashlib.sha256(
        canonical_production_cuda_runtime_lock_signing_bytes(loaded)
    ).hexdigest()
    assert payload.endswith(b"\n")
    payload.decode("ascii")


def test_runtime_lock_rejects_duplicate_json_keys() -> None:
    payload = canonical_production_cuda_runtime_lock_bytes(_valid_lock())
    text = payload.decode("ascii")
    marker = f'"content_sha256":"{_valid_lock().content_sha256}",'
    duplicated = text.replace(marker, marker + marker, 1).encode("ascii")

    with pytest.raises(
        ProductionCudaRuntimeLockError,
        match="duplicate keys",
    ):
        load_production_cuda_runtime_lock_bytes(duplicated)


def test_runtime_lock_rejects_noncanonical_json() -> None:
    payload = json.dumps(_canonical_dict(), indent=2).encode("ascii")

    with pytest.raises(
        ProductionCudaRuntimeLockError,
        match="not canonical",
    ):
        load_production_cuda_runtime_lock_bytes(payload)


@pytest.mark.parametrize(
    ("field", "mutation"),
    (
        (
            "base_images",
            lambda rows: [
                {
                    **rows[0],
                    "identity": "nvidia/cuda:11.8.0-devel-ubuntu22.04",
                },
                rows[1],
            ],
        ),
        (
            "base_images",
            lambda rows: [
                rows[0],
                {**rows[1], "role": "builder"},
            ],
        ),
        (
            "source_artifacts",
            lambda rows: [
                {
                    **rows[0],
                    "url": (
                        "http://www.python.org/ftp/python/3.11.9/"
                        "Python-3.11.9.tar.xz"
                    ),
                },
                *rows[1:],
            ],
        ),
        (
            "source_artifacts",
            lambda rows: [
                {**rows[0], "sha256": "a" * 64},
                *rows[1:],
            ],
        ),
        (
            "auxiliary_files",
            lambda rows: [
                {
                    **rows[0],
                    "path": "../production-cuda/apt-build.lock",
                },
                *rows[1:],
            ],
        ),
        (
            "auxiliary_files",
            lambda rows: [
                {key: value for key, value in rows[0].items()
                 if key != "sha256"},
                *rows[1:],
            ],
        ),
        (
            "cuda_architectures",
            lambda _rows: ["8.0", "7.5", "8.6", "8.9", "9.0+PTX"],
        ),
        (
            "required_imports",
            lambda _rows: ["torch", "gsplat"],
        ),
    ),
)
def test_runtime_lock_rejects_unbound_or_ambiguous_input(
    field: str,
    mutation,
) -> None:
    document = _canonical_dict()
    document[field] = mutation(document[field])

    with pytest.raises(ProductionCudaRuntimeLockError):
        load_production_cuda_runtime_lock_bytes(_canonical_payload(document))


def test_runtime_lock_rejects_content_sha_drift() -> None:
    document = _canonical_dict()
    document["ubuntu_snapshot"] = "20260601T000000Z"

    with pytest.raises(
        ProductionCudaRuntimeLockError,
        match="content SHA",
    ):
        load_production_cuda_runtime_lock_bytes(_canonical_payload(document))


def test_runtime_lock_rejects_unexpected_upstream_commit_binding() -> None:
    document = _canonical_dict()
    torch = next(
        row for row in document["source_artifacts"]
        if row["role"] == "torch-wheel"
    )
    torch["upstream_commit"] = "0123456789abcdef0123456789abcdef01234567"

    with pytest.raises(ProductionCudaRuntimeLockError):
        load_production_cuda_runtime_lock_bytes(_canonical_payload(document))
