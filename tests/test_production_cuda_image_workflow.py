from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import cloud.inspect_production_cuda_oci as oci_inspector
from cloud.inspect_production_cuda_oci import (
    ProductionCudaOciInspectionError,
    inspect_production_cuda_oci,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "production-cuda-image.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
IMAGE_NAME = "ghcr.io/taomic2035/nantai-3d-production-cuda"
SLSA = "https://slsa.dev/provenance/v1"
SPDX = "https://spdx.dev/Document"


def _stat_with_reparse(observed):
    return SimpleNamespace(
        st_dev=observed.st_dev,
        st_ino=observed.st_ino,
        st_mode=observed.st_mode,
        st_size=observed.st_size,
        st_mtime_ns=observed.st_mtime_ns,
        st_ctime_ns=observed.st_ctime_ns,
        st_file_attributes=getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        ),
    )


def _json_bytes(document: dict) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _statement(
    predicate_type: str,
    subject_digest: str,
    *,
    subject_name: str = "_",
) -> bytes:
    return _json_bytes(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "predicate": {"kind": predicate_type},
            "predicateType": predicate_type,
            "subject": [
                {
                    "digest": {
                        "sha256": subject_digest.removeprefix("sha256:")
                    },
                    "name": subject_name,
                }
            ],
        }
    )


class _FakeRegistry:
    def __init__(
        self,
        *,
        manifests: dict[str, bytes],
        blobs: dict[str, bytes],
        referrers: bytes,
    ) -> None:
        self._manifests = manifests
        self._blobs = blobs
        self._referrers = referrers

    def get_manifest(self, reference: str) -> bytes:
        return self._manifests[reference]

    def get_blob(self, digest: str) -> bytes:
        return self._blobs[digest]

    def get_referrers(self, _digest: str) -> bytes:
        return self._referrers


def _oci_fixture(tmp_path: Path) -> tuple[_FakeRegistry, str, Path]:
    platform_payload = _json_bytes(
        {
            "config": {
                "digest": "sha256:" + "1" * 64,
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": 2,
            },
            "layers": [],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    platform_digest = _digest(platform_payload)
    provenance = _statement(SLSA, platform_digest)
    sbom = _statement(SPDX, platform_digest)
    provenance_digest = _digest(provenance)
    sbom_digest = _digest(sbom)
    buildkit_payload = _json_bytes(
        {
            "config": {
                "digest": "sha256:" + "2" * 64,
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": 2,
            },
            "layers": [
                {
                    "annotations": {
                        "in-toto.io/predicate-type": SLSA
                    },
                    "digest": provenance_digest,
                    "mediaType": "application/vnd.in-toto+json",
                    "size": len(provenance),
                },
                {
                    "annotations": {
                        "in-toto.io/predicate-type": SPDX
                    },
                    "digest": sbom_digest,
                    "mediaType": "application/vnd.in-toto+json",
                    "size": len(sbom),
                },
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    buildkit_digest = _digest(buildkit_payload)
    root_payload = _json_bytes(
        {
            "manifests": [
                {
                    "digest": platform_digest,
                    "mediaType": (
                        "application/vnd.oci.image.manifest.v1+json"
                    ),
                    "platform": {
                        "architecture": "amd64",
                        "os": "linux",
                    },
                    "size": len(platform_payload),
                },
                {
                    "annotations": {
                        "vnd.docker.reference.digest": platform_digest,
                        "vnd.docker.reference.type": (
                            "attestation-manifest"
                        ),
                    },
                    "digest": buildkit_digest,
                    "mediaType": (
                        "application/vnd.oci.image.manifest.v1+json"
                    ),
                    "platform": {
                        "architecture": "unknown",
                        "os": "unknown",
                    },
                    "size": len(buildkit_payload),
                },
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    root_digest = _digest(root_payload)
    github_statement = _statement(
        SLSA,
        root_digest,
        subject_name=IMAGE_NAME,
    )
    bundle = {
        "dsseEnvelope": {
            "payload": base64.b64encode(github_statement).decode("ascii"),
            "payloadType": "application/vnd.in-toto+json",
            "signatures": [{"sig": "test-signature"}],
        },
        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "verificationMaterial": {"test": True},
    }
    bundle_payload = _json_bytes(bundle)
    bundle_digest = _digest(bundle_payload)
    bundle_path = tmp_path / "github-attestation.json"
    bundle_path.write_bytes(bundle_payload + b"\n")
    github_manifest_payload = _json_bytes(
        {
            "annotations": {
                "dev.sigstore.bundle.content": "dsse-envelope",
                "dev.sigstore.bundle.predicateType": SLSA,
            },
            "artifactType": (
                "application/vnd.dev.sigstore.bundle.v0.3+json"
            ),
            "config": {
                "digest": "sha256:" + "3" * 64,
                "mediaType": "application/vnd.oci.empty.v1+json",
                "size": 2,
            },
            "layers": [
                {
                    "digest": bundle_digest,
                    "mediaType": (
                        "application/vnd.dev.sigstore.bundle.v0.3+json"
                    ),
                    "size": len(bundle_payload),
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
            "subject": {
                "digest": root_digest,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "size": len(root_payload),
            },
        }
    )
    github_manifest_digest = _digest(github_manifest_payload)
    referrers = _json_bytes(
        {
            "manifests": [
                {
                    "annotations": {
                        "dev.sigstore.bundle.predicateType": SLSA
                    },
                    "artifactType": (
                        "application/vnd.dev.sigstore.bundle.v0.3+json"
                    ),
                    "digest": github_manifest_digest,
                    "mediaType": (
                        "application/vnd.oci.image.manifest.v1+json"
                    ),
                    "size": len(github_manifest_payload),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    registry = _FakeRegistry(
        manifests={
            root_digest: root_payload,
            platform_digest: platform_payload,
            buildkit_digest: buildkit_payload,
            github_manifest_digest: github_manifest_payload,
        },
        blobs={
            provenance_digest: provenance,
            sbom_digest: sbom,
        },
        referrers=referrers,
    )
    return registry, root_digest, bundle_path


def test_oci_inspection_binds_shared_buildkit_manifest_and_github_bundle(
    tmp_path: Path,
) -> None:
    registry, root_digest, bundle_path = _oci_fixture(tmp_path)

    result = inspect_production_cuda_oci(
        registry=registry,
        image_name=IMAGE_NAME,
        image_digest=root_digest,
        github_attestation_bundle=bundle_path,
    )

    assert result["schema"] == "nantai.production-cuda-oci-inspection.v1"
    assert result["image_digest"] == root_digest
    assert result["platform"] == "linux/amd64"
    assert [item["role"] for item in result["attestations"]] == [
        "buildkit-provenance",
        "buildkit-sbom",
        "github-build-provenance",
    ]
    assert (
        result["attestations"][0]["manifest_digest"]
        == result["attestations"][1]["manifest_digest"]
    )
    assert (
        result["attestations"][0]["attestation_blob_digest"]
        != result["attestations"][1]["attestation_blob_digest"]
    )
    assert result["attestations"][2]["subject_digest"] == root_digest


def test_github_attestation_bundle_rejects_path_reparse_point(
    tmp_path,
    monkeypatch,
):
    _registry, _root_digest, bundle_path = _oci_fixture(tmp_path)
    bundle_path = bundle_path.absolute()
    original_lstat = Path.lstat

    def reparse_lstat(path):
        observed = original_lstat(path)
        return (
            _stat_with_reparse(observed)
            if path.absolute() == bundle_path
            else observed
        )

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(
        ProductionCudaOciInspectionError,
        match="bounded regular file",
    ):
        oci_inspector._read_github_bundle(bundle_path)


def test_github_attestation_bundle_rejects_descriptor_reparse_drift(
    tmp_path,
    monkeypatch,
):
    _registry, _root_digest, bundle_path = _oci_fixture(tmp_path)
    original_fstat = os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        observed = original_fstat(descriptor)
        return _stat_with_reparse(observed) if calls == 2 else observed

    monkeypatch.setattr(oci_inspector.os, "fstat", drifting_fstat)

    with pytest.raises(
        ProductionCudaOciInspectionError,
        match="changed while reading",
    ):
        oci_inspector._read_github_bundle(bundle_path)

    assert calls == 2


def test_oci_inspection_rejects_buildkit_subject_mismatch(
    tmp_path: Path,
) -> None:
    registry, root_digest, bundle_path = _oci_fixture(tmp_path)
    provenance_digest = next(iter(registry._blobs))
    registry._blobs[provenance_digest] = _statement(
        SLSA,
        "sha256:" + "f" * 64,
    )

    with pytest.raises(
        ProductionCudaOciInspectionError,
        match="digest|subject",
    ):
        inspect_production_cuda_oci(
            registry=registry,
            image_name=IMAGE_NAME,
            image_digest=root_digest,
            github_attestation_bundle=bundle_path,
        )


def test_oci_inspection_rejects_bundle_bytes_not_pushed_to_registry(
    tmp_path: Path,
) -> None:
    registry, root_digest, bundle_path = _oci_fixture(tmp_path)
    bundle = json.loads(bundle_path.read_bytes())
    bundle["verificationMaterial"]["extra"] = True
    bundle_path.write_bytes(_json_bytes(bundle) + b"\n")

    with pytest.raises(
        ProductionCudaOciInspectionError,
        match="GitHub attestation",
    ):
        inspect_production_cuda_oci(
            registry=registry,
            image_name=IMAGE_NAME,
            image_digest=root_digest,
            github_attestation_bundle=bundle_path,
        )


def _workflow() -> dict:
    document = yaml.load(
        WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(document, dict)
    return document


def test_manual_workflow_contract() -> None:
    workflow = _workflow()
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert set(workflow["jobs"]) == {"publish"}
    job = workflow["jobs"]["publish"]
    assert job["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == "90"
    assert job["env"]["PLATFORM"] == "linux/amd64"
    all_runs = "\n".join(
        step["run"] for step in job["steps"] if "run" in step
    )
    assert "refs/heads/main" in all_runs
    assert "--network none" in all_runs
    assert "--deny-self-hosted-runners" in all_runs
    assert "--signer-workflow" in all_runs
    assert "--signer-digest" in all_runs
    assert "--source-digest" in all_runs
    assert '--source-ref "refs/heads/main"' in all_runs
    assert "for attempt in 1 2 3 4 5" in all_runs
    assert "steps.build.outputs.digest" in WORKFLOW.read_text(
        encoding="utf-8"
    )
    assert ":latest" not in WORKFLOW.read_text(encoding="utf-8")


def test_manual_workflow_pins_actions_and_attests_exact_digest() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["publish"]
    expected = {
        "actions/attest": (
            "f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"
        ),
        "actions/checkout": (
            "11d5960a326750d5838078e36cf38b85af677262"
        ),
        "actions/upload-artifact": (
            "ea165f8d65b6e75b540449e92b4886f43607fa02"
        ),
        "docker/build-push-action": (
            "10e90e3645eae34f1e60eeb005ba3a3d33f178e8"
        ),
        "docker/login-action": (
            "c94ce9fb468520275223c153574b00df6fe4bcc9"
        ),
        "docker/setup-buildx-action": (
            "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f"
        ),
    }
    for step in job["steps"]:
        uses = step.get("uses")
        if uses is None:
            continue
        action, separator, revision = uses.partition("@")
        assert separator
        assert expected[action] == revision

    build = next(step for step in job["steps"] if step.get("id") == "build")
    assert build["with"]["platforms"] == "linux/amd64"
    assert build["with"]["push"] == "true"
    assert build["with"]["sbom"] == "true"
    assert build["with"]["provenance"] == "mode=max,version=v1"

    image_attestation = next(
        step
        for step in job["steps"]
        if step.get("id") == "attest_image"
    )
    assert image_attestation["with"]["subject-digest"] == (
        "${{ steps.build.outputs.digest }}"
    )
    assert image_attestation["with"]["push-to-registry"] == "true"


def test_manual_workflow_uploads_only_final_json_allowlist() -> None:
    job = _workflow()["jobs"]["publish"]
    uploads = [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    upload = uploads[0]["with"]
    assert upload["if-no-files-found"] == "error"
    assert set(upload["path"].splitlines()) == {
        "${{ runner.temp }}/production-cuda-image-release.json",
        "${{ runner.temp }}/production-cuda-image-verification.json",
    }
    assert "*" not in upload["path"]


def test_ordinary_ci_runs_cuda_contracts_without_building_image() -> None:
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    job = ci.split("  production-release-contract:", 1)[1].split(
        "\n  production-release-content-id-compare:",
        1,
    )[0]
    for path in (
        "tests/test_production_cuda_runtime_lock.py",
        "tests/test_production_cuda_image_release.py",
        "tests/test_production_cuda_image_probe.py",
        "tests/test_production_cuda_image_contract.py",
        "tests/test_production_cuda_image_workflow.py",
    ):
        assert path in job
    assert "docker build" not in job
    assert "build-push-action" not in job
