from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

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
from scripts import emit_production_cuda_image_release as release_script

ROOT = Path(__file__).resolve().parents[1]


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


def _valid_probe(
    *,
    runtime_lock_sha256: str | None = None,
) -> ProductionCudaImageProbe:
    options = _options()
    return ProductionCudaImageProbe.create(
        platform="linux/amd64",
        runtime_lock_sha256=(
            runtime_lock_sha256 or _digest("runtime-lock")
        ),
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
    image_digest = f"sha256:{_digest('published-image')}"
    platform_digest = f"sha256:{_digest('amd64-manifest')}"
    buildkit_manifest = f"sha256:{_digest('buildkit-attestations')}"
    return (
        OciAttestationBinding(
            role="buildkit-provenance",
            predicate_type="https://slsa.dev/provenance/v1",
            manifest_digest=buildkit_manifest,
            attestation_blob_digest=(
                f"sha256:{_digest('buildkit-provenance')}"
            ),
            subject_digest=platform_digest,
        ),
        OciAttestationBinding(
            role="buildkit-sbom",
            predicate_type="https://spdx.dev/Document",
            manifest_digest=buildkit_manifest,
            attestation_blob_digest=f"sha256:{_digest('buildkit-sbom')}",
            subject_digest=platform_digest,
        ),
        OciAttestationBinding(
            role="github-build-provenance",
            predicate_type="https://slsa.dev/provenance/v1",
            manifest_digest=f"sha256:{_digest('github-provenance')}",
            attestation_blob_digest=(
                f"sha256:{_digest('github-provenance-predicate')}"
            ),
            subject_digest=image_digest,
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


def _producer_fixture(tmp_path: Path) -> tuple[list[str], Path]:
    runtime_lock_bytes = (
        ROOT / "containers" / "production-cuda" / "runtime-lock.json"
    ).read_bytes()
    runtime_lock = tmp_path / "runtime-lock.json"
    runtime_lock.write_bytes(runtime_lock_bytes)
    probe = _valid_probe(
        runtime_lock_sha256=hashlib.sha256(
            runtime_lock_bytes
        ).hexdigest()
    )
    probe_path = tmp_path / "image-probe.json"
    probe_path.write_bytes(
        canonical_production_cuda_image_probe_bytes(probe)
    )
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_bytes(b"FROM example.invalid@sha256:bound\n")
    requirements = tmp_path / "requirements.lock"
    requirements.write_bytes(
        b"example==1.0 \\\n"
        b"    --hash=sha256:"
        + _digest("example-wheel").encode("ascii")
        + b"\n"
    )
    output = tmp_path / "image-release.json"
    image_digest = f"sha256:{_digest('published-image')}"
    argv = [
        "--runtime-lock",
        str(runtime_lock),
        "--probe",
        str(probe_path),
        "--source-commit",
        "0123456789abcdef0123456789abcdef01234567",
        "--image-name",
        "ghcr.io/taomic2035/nantai-3d-production-cuda",
        "--image-digest",
        image_digest,
        "--platform-manifest-digest",
        f"sha256:{_digest('amd64-manifest')}",
        "--dockerfile",
        str(dockerfile),
        "--requirements-lock",
        str(requirements),
        "--workflow-repository",
        "taomic2035/nantai-3d",
        "--workflow-run-id",
        "30413151667",
        "--workflow-run-attempt",
        "1",
    ]
    platform_digest = f"sha256:{_digest('amd64-manifest')}"
    buildkit_manifest = f"sha256:{_digest('buildkit-attestations')}"
    for (
        role,
        predicate,
        manifest_digest,
        attestation_blob_digest,
        subject_digest,
    ) in (
        (
            "buildkit-provenance",
            "https://slsa.dev/provenance/v1",
            buildkit_manifest,
            f"sha256:{_digest('buildkit-provenance')}",
            platform_digest,
        ),
        (
            "buildkit-sbom",
            "https://spdx.dev/Document",
            buildkit_manifest,
            f"sha256:{_digest('buildkit-sbom')}",
            platform_digest,
        ),
        (
            "github-build-provenance",
            "https://slsa.dev/provenance/v1",
            f"sha256:{_digest('github-provenance')}",
            f"sha256:{_digest('github-provenance-predicate')}",
            image_digest,
        ),
    ):
        argv.extend(
            [
                "--attestation",
                (
                    f"{role},{predicate},{manifest_digest},"
                    f"{attestation_blob_digest},{subject_digest}"
                ),
            ]
        )
    argv.extend(["--output", str(output)])
    return argv, output


def test_release_allows_buildkit_predicates_in_one_manifest() -> None:
    probe = _valid_probe()
    release = ProductionCudaImageRelease.create(
        source_commit="0123456789abcdef0123456789abcdef01234567",
        image_name="ghcr.io/taomic2035/nantai-3d-production-cuda",
        image_digest=f"sha256:{_digest('published-image')}",
        platform_manifest_digest=f"sha256:{_digest('amd64-manifest')}",
        dockerfile_sha256=_digest("dockerfile"),
        requirements_lock_sha256=_digest("requirements-lock"),
        image_probe=probe,
        workflow_repository="taomic2035/nantai-3d",
        workflow_run_id=304_131_516_67,
        workflow_run_attempt=1,
        attestations=_attestations(),
    )

    assert (
        release.attestations[0].manifest_digest
        == release.attestations[1].manifest_digest
    )


def test_release_requires_distinct_attestation_blobs() -> None:
    probe = _valid_probe()
    attestations = list(_attestations())
    attestations[0] = attestations[0].model_copy(
        update={
            "attestation_blob_digest": (
                attestations[1].attestation_blob_digest
            )
        }
    )

    with pytest.raises(
        ValueError,
        match="attestation blob digests must be distinct",
    ):
        ProductionCudaImageRelease.create(
            source_commit=(
                "0123456789abcdef0123456789abcdef01234567"
            ),
            image_name=(
                "ghcr.io/taomic2035/nantai-3d-production-cuda"
            ),
            image_digest=f"sha256:{_digest('published-image')}",
            platform_manifest_digest=(
                f"sha256:{_digest('amd64-manifest')}"
            ),
            dockerfile_sha256=_digest("dockerfile"),
            requirements_lock_sha256=_digest("requirements-lock"),
            image_probe=probe,
            workflow_repository="taomic2035/nantai-3d",
            workflow_run_id=304_131_516_67,
            workflow_run_attempt=1,
            attestations=tuple(attestations),
        )


def test_release_rejects_attestation_subject_mismatch() -> None:
    document = _canonical_dict(
        canonical_production_cuda_image_release_bytes(_valid_release())
    )
    document["attestations"][0]["subject_digest"] = (
        document["image_digest"]
    )

    with pytest.raises(
        ProductionCudaImageReleaseError,
        match="subject",
    ):
        load_production_cuda_image_release_bytes(
            _canonical_payload(document)
        )


def test_release_producer_hashes_inputs_and_emits_canonical_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv, output = _producer_fixture(tmp_path)

    assert release_script.main(argv) == 0

    release = load_production_cuda_image_release_bytes(
        output.read_bytes()
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "image_identity": release.image_identity,
        "receipt_sha256": release.content_sha256,
    }
    assert release.dockerfile_sha256 == hashlib.sha256(
        (tmp_path / "Dockerfile").read_bytes()
    ).hexdigest()
    assert release.requirements_lock_sha256 == hashlib.sha256(
        (tmp_path / "requirements.lock").read_bytes()
    ).hexdigest()
    assert release.runtime_lock_sha256 == hashlib.sha256(
        (tmp_path / "runtime-lock.json").read_bytes()
    ).hexdigest()
    assert release.runtime_policy_image_facts().expected_container_identity == (
        release.image_identity
    )


def test_release_producer_reopens_every_local_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv, _ = _producer_fixture(tmp_path)
    observed: list[str] = []
    original = release_script._read_stable_regular_file

    def recording_read(path: Path, *, label: str, byte_cap: int):
        observed.append(label)
        return original(path, label=label, byte_cap=byte_cap)

    monkeypatch.setattr(
        release_script,
        "_read_stable_regular_file",
        recording_read,
    )

    assert release_script.main(argv) == 0
    assert observed == [
        "runtime lock",
        "image probe",
        "Dockerfile",
        "requirements lock",
        "published receipt",
    ]


def test_release_producer_refuses_existing_output_before_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv, output = _producer_fixture(tmp_path)
    output.write_bytes(b"existing")

    def unexpected_read(*args, **kwargs):
        raise AssertionError("local inputs must not be read")

    monkeypatch.setattr(
        release_script,
        "_read_stable_regular_file",
        unexpected_read,
    )

    assert release_script.main(argv) == 2
    assert output.read_bytes() == b"existing"


def test_release_producer_rejects_symlink_output(
    tmp_path: Path,
) -> None:
    argv, output = _producer_fixture(tmp_path)
    target = tmp_path / "target.json"
    target.write_bytes(b"target")
    try:
        output.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    assert release_script.main(argv) == 2
    assert target.read_bytes() == b"target"


@pytest.mark.parametrize(
    "attestation",
    (
        (
            "unknown-role,https://slsa.dev/provenance/v1,"
            "sha256:"
            + "1" * 64
        ),
        (
            "buildkit-sbom,https://slsa.dev/provenance/v1,"
            "sha256:"
            + "1" * 64
        ),
        (
            "buildkit-sbom,https://spdx.dev/Document,"
            "sha256:ABC"
        ),
    ),
)
def test_release_producer_rejects_invalid_attestation(
    tmp_path: Path,
    attestation: str,
) -> None:
    argv, output = _producer_fixture(tmp_path)
    index = argv.index("--attestation")
    argv[index + 1] = attestation

    assert release_script.main(argv) == 2
    assert not output.exists()


def test_release_producer_rejects_lock_probe_mismatch(
    tmp_path: Path,
) -> None:
    argv, output = _producer_fixture(tmp_path)
    probe_path = Path(argv[argv.index("--probe") + 1])
    probe_path.write_bytes(
        canonical_production_cuda_image_probe_bytes(_valid_probe())
    )

    assert release_script.main(argv) == 2
    assert not output.exists()
