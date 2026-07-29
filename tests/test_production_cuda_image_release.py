from __future__ import annotations

import hashlib
import json
import stat

import pytest

from pipeline.production_cuda_image_release import (
    ImageExecutableObservation,
    OciAttestationBinding,
    ProductionCudaImageProbe,
    ProductionCudaImageRelease,
    ProductionCudaImageReleaseError,
    canonical_production_cuda_image_probe_bytes,
    canonical_production_cuda_image_probe_signing_bytes,
    canonical_production_cuda_image_release_bytes,
    canonical_production_cuda_image_release_signing_bytes,
    load_production_cuda_image_probe_bytes,
    load_production_cuda_image_release_bytes,
)
from pipeline.production_runtime_evidence import (
    training_cli_schema_sha256,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _executable(
    role: str,
    path: str,
) -> ImageExecutableObservation:
    return ImageExecutableObservation(
        role=role,
        resolved_path=path,
        byte_length=4096,
        sha256=_digest(f"{role}-bytes"),
        mode=stat.S_IFREG | 0o755,
    )


def _options() -> tuple[str, ...]:
    return (
        "--data",
        "--help",
        "--machine.seed",
        "--max-num-iterations",
        "--output-dir",
        "--viewer.quit-on-train-completion",
    )


def _valid_probe() -> ProductionCudaImageProbe:
    options = _options()
    return ProductionCudaImageProbe.create(
        platform="linux/amd64",
        runtime_lock_sha256=_digest("runtime-lock"),
        python_version="3.11.9",
        torch_version="2.1.2+cu118",
        torch_cuda_version="11.8",
        torchvision_version="0.16.2+cu118",
        nerfstudio_version="1.1.5",
        gsplat_version="1.4.0",
        executables=(
            _executable("ns-export", "/opt/python/bin/ns-export"),
            _executable("ns-train", "/opt/python/bin/ns-train"),
            _executable("python", "/usr/local/bin/python"),
        ),
        training_cli_options=options,
        training_cli_schema_sha256=training_cli_schema_sha256(
            trainer_name="nerfstudio-splatfacto",
            observed_options=options,
        ),
        imported_modules=(
            "gsplat",
            "nerfstudio",
            "pipeline.production_runtime_evidence",
            "torch",
            "torchmetrics",
            "torchvision",
        ),
    )


def _attestations() -> tuple[OciAttestationBinding, ...]:
    return (
        OciAttestationBinding(
            role="buildkit-provenance",
            predicate_type="https://slsa.dev/provenance/v1",
            manifest_digest=f"sha256:{_digest('buildkit-provenance')}",
        ),
        OciAttestationBinding(
            role="buildkit-sbom",
            predicate_type="https://spdx.dev/Document",
            manifest_digest=f"sha256:{_digest('buildkit-sbom')}",
        ),
        OciAttestationBinding(
            role="github-build-provenance",
            predicate_type="https://slsa.dev/provenance/v1",
            manifest_digest=f"sha256:{_digest('github-provenance')}",
        ),
    )


def _valid_release() -> ProductionCudaImageRelease:
    return ProductionCudaImageRelease.create(
        source_commit="0123456789abcdef0123456789abcdef01234567",
        image_name=(
            "ghcr.io/taomic2035/nantai-3d-production-cuda"
        ),
        image_digest=f"sha256:{_digest('published-image')}",
        platform_manifest_digest=(
            f"sha256:{_digest('amd64-manifest')}"
        ),
        dockerfile_sha256=_digest("dockerfile"),
        requirements_lock_sha256=_digest("requirements-lock"),
        image_probe=_valid_probe(),
        workflow_repository="taomic2035/nantai-3d",
        workflow_run_id=304_131_516_67,
        workflow_run_attempt=1,
        attestations=_attestations(),
    )


def _canonical_dict(payload: bytes) -> dict:
    return json.loads(payload)


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


def test_probe_is_canonical_content_addressed() -> None:
    probe = _valid_probe()
    payload = canonical_production_cuda_image_probe_bytes(probe)

    assert load_production_cuda_image_probe_bytes(payload) == probe
    assert probe.probe_id == (
        f"production-cuda-image-probe-{probe.content_sha256}"
    )
    assert probe.content_sha256 == hashlib.sha256(
        canonical_production_cuda_image_probe_signing_bytes(probe)
    ).hexdigest()
    assert payload.endswith(b"\n")


def test_release_is_canonical_content_addressed() -> None:
    release = _valid_release()
    payload = canonical_production_cuda_image_release_bytes(release)

    assert load_production_cuda_image_release_bytes(payload) == release
    assert release.release_id == (
        f"production-cuda-image-release-{release.content_sha256}"
    )
    assert release.content_sha256 == hashlib.sha256(
        canonical_production_cuda_image_release_signing_bytes(release)
    ).hexdigest()
    assert release.runtime_lock_sha256 == (
        release.image_probe.runtime_lock_sha256
    )
    assert release.image_probe_sha256 == (
        release.image_probe.content_sha256
    )


def test_release_projects_exact_runtime_policy_image_facts() -> None:
    release = _valid_release()
    probe = release.image_probe

    facts = release.runtime_policy_image_facts()

    assert facts.expected_container_identity == (
        f"{release.image_name}@{release.image_digest}"
    )
    assert facts.expected_cuda_runtime_version == "11.8"
    assert facts.expected_python_version == "3.11.9"
    assert facts.expected_nerfstudio_version == "1.1.5"
    assert facts.expected_python_sha256 == _digest("python-bytes")
    assert facts.expected_training_cli_sha256 == _digest(
        "ns-train-bytes"
    )
    assert facts.expected_training_cli_schema_sha256 == (
        probe.training_cli_schema_sha256
    )
    assert facts.required_training_cli_options == (
        "--data",
        "--machine.seed",
        "--max-num-iterations",
        "--output-dir",
        "--viewer.quit-on-train-completion",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("python_version", "3.11.10"),
        ("torch_version", "2.4.1+cu118"),
        ("torch_cuda_version", "12.8"),
        ("nerfstudio_version", "1.1.4"),
        ("gsplat_version", "1.5.0"),
        (
            "training_cli_options",
            ["--data", "--output-dir"],
        ),
    ),
)
def test_probe_rejects_runtime_contract_drift(
    field: str,
    value,
) -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_probe_bytes(_valid_probe())
    )
    document[field] = value

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_probe_bytes(
            _canonical_payload(document)
        )


def test_probe_rejects_cli_schema_drift() -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_probe_bytes(_valid_probe())
    )
    document["training_cli_schema_sha256"] = _digest("wrong-schema")

    with pytest.raises(
        ProductionCudaImageReleaseError,
        match="CLI schema",
    ):
        load_production_cuda_image_probe_bytes(
            _canonical_payload(document)
        )


def test_probe_rejects_nonregular_or_duplicate_executable_roles() -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_probe_bytes(_valid_probe())
    )
    document["executables"][0]["mode"] = stat.S_IFLNK | 0o777

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_probe_bytes(
            _canonical_payload(document)
        )

    document = _canonical_dict(
        canonical_production_cuda_image_probe_bytes(_valid_probe())
    )
    document["executables"][1]["role"] = "ns-export"

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_probe_bytes(
            _canonical_payload(document)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        (
            "image_name",
            "ghcr.io/taomic2035/nantai-3d-production-cuda:latest",
        ),
        ("image_digest", "sha256:" + "a" * 64),
        ("source_commit", "f" * 40),
        ("workflow_repository", "taomic2035/nantai-3d/extra"),
    ),
)
def test_release_rejects_untrusted_identity(
    field: str,
    value: str,
) -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_release_bytes(_valid_release())
    )
    document[field] = value

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_release_bytes(
            _canonical_payload(document)
        )


def test_release_rejects_missing_or_misbound_attestation() -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_release_bytes(_valid_release())
    )
    document["attestations"] = document["attestations"][:-1]

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_release_bytes(
            _canonical_payload(document)
        )

    document = _canonical_dict(
        canonical_production_cuda_image_release_bytes(_valid_release())
    )
    sbom = next(
        item for item in document["attestations"]
        if item["role"] == "buildkit-sbom"
    )
    sbom["predicate_type"] = "https://slsa.dev/provenance/v1"

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_release_bytes(
            _canonical_payload(document)
        )


def test_release_rejects_probe_or_lock_sha_mismatch() -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_release_bytes(_valid_release())
    )
    document["runtime_lock_sha256"] = _digest("other-lock")

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_release_bytes(
            _canonical_payload(document)
        )

    document = _canonical_dict(
        canonical_production_cuda_image_release_bytes(_valid_release())
    )
    document["image_probe_sha256"] = _digest("other-probe")

    with pytest.raises(ProductionCudaImageReleaseError):
        load_production_cuda_image_release_bytes(
            _canonical_payload(document)
        )


def test_probe_and_release_reject_duplicate_keys() -> None:
    for payload, loader in (
        (
            canonical_production_cuda_image_probe_bytes(_valid_probe()),
            load_production_cuda_image_probe_bytes,
        ),
        (
            canonical_production_cuda_image_release_bytes(
                _valid_release()
            ),
            load_production_cuda_image_release_bytes,
        ),
    ):
        text = payload.decode("ascii")
        marker = '"schema":'
        duplicated = text.replace(
            marker,
            '"schema":"duplicate","schema":',
            1,
        ).encode("ascii")

        with pytest.raises(
            ProductionCudaImageReleaseError,
            match="duplicate keys",
        ):
            loader(duplicated)


def test_probe_and_release_reject_noncanonical_json() -> None:
    for payload, loader in (
        (
            canonical_production_cuda_image_probe_bytes(_valid_probe()),
            load_production_cuda_image_probe_bytes,
        ),
        (
            canonical_production_cuda_image_release_bytes(
                _valid_release()
            ),
            load_production_cuda_image_release_bytes,
        ),
    ):
        pretty = json.dumps(json.loads(payload), indent=2).encode("ascii")

        with pytest.raises(
            ProductionCudaImageReleaseError,
            match="not canonical",
        ):
            loader(pretty)
