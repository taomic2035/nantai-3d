"""Content-addressed probe and release receipt for a CUDA OCI image."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.production_runtime_evidence import (
    training_cli_schema_sha256,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_PROBE_ID_PATTERN = r"^production-cuda-image-probe-[0-9a-f]{64}$"
_RELEASE_ID_PATTERN = r"^production-cuda-image-release-[0-9a-f]{64}$"
_IMAGE_NAME_PATTERN = (
    r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/"
    r"[a-z0-9][a-z0-9._/-]*$"
)
_WORKFLOW_REPOSITORY_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_MODULE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_OPTION_PATTERN = re.compile(r"^--[a-z0-9][a-z0-9.-]*$")
_EXECUTABLE_ROLES = ("ns-export", "ns-train", "python")
_ATTESTATION_ROLES = (
    "buildkit-provenance",
    "buildkit-sbom",
    "github-build-provenance",
)
_PREDICATE_BY_ROLE = {
    "buildkit-provenance": "https://slsa.dev/provenance/v1",
    "buildkit-sbom": "https://spdx.dev/Document",
    "github-build-provenance": "https://slsa.dev/provenance/v1",
}
_REQUIRED_TRAINING_OPTIONS = (
    "--data",
    "--machine.seed",
    "--max-num-iterations",
    "--output-dir",
    "--viewer.quit-on-train-completion",
)


class ProductionCudaImageReleaseError(ValueError):
    """A CUDA image probe or detached receipt cannot be trusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")


def _reject_uniform_identity(value: str) -> str:
    suffix = value.rpartition(":")[2]
    if len(suffix) in {40, 64} and len(set(suffix)) == 1:
        raise ValueError("uniform dummy identity is not accepted")
    return value


def _absolute_posix_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or not path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise ValueError("image executable path must be absolute POSIX")
    return value


def _canonical_options(value: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not value
        or value != tuple(sorted(value))
        or len(value) != len(set(value))
        or any(_OPTION_PATTERN.fullmatch(item) is None for item in value)
    ):
        raise ValueError("training CLI options must be sorted and unique")
    return value


class ImageExecutableObservation(FrozenModel):
    role: Literal["ns-export", "ns-train", "python"]
    resolved_path: str
    byte_length: int = Field(ge=1, le=512 * 1024 * 1024)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    mode: int = Field(ge=0, le=0o177777)

    _path_is_absolute = field_validator("resolved_path")(
        _absolute_posix_path
    )
    _sha_is_not_dummy = field_validator("sha256")(
        _reject_uniform_identity
    )

    @model_validator(mode="after")
    def _executable_is_regular(self) -> ImageExecutableObservation:
        if not stat.S_ISREG(self.mode) or self.mode & 0o111 == 0:
            raise ValueError("image executable must be a regular executable")
        return self


class ProductionCudaImageProbe(FrozenModel):
    schema_id: Literal["nantai.production-cuda-image-probe.v1"] = Field(
        default="nantai.production-cuda-image-probe.v1",
        alias="schema",
        serialization_alias="schema",
    )
    probe_id: str = Field(pattern=_PROBE_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    platform: Literal["linux/amd64"]
    runtime_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    python_version: Literal["3.11.9"]
    torch_version: Literal["2.1.2+cu118"]
    torch_cuda_version: Literal["11.8"]
    torchvision_version: Literal["0.16.2+cu118"]
    nerfstudio_version: Literal["1.1.5"]
    gsplat_version: Literal["1.4.0"]
    executables: tuple[ImageExecutableObservation, ...]
    training_cli_options: tuple[str, ...]
    training_cli_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    imported_modules: tuple[str, ...]

    _content_sha_is_not_dummy = field_validator("content_sha256")(
        _reject_uniform_identity
    )
    _lock_sha_is_not_dummy = field_validator("runtime_lock_sha256")(
        _reject_uniform_identity
    )
    _schema_sha_is_not_dummy = field_validator(
        "training_cli_schema_sha256"
    )(_reject_uniform_identity)
    _options_are_canonical = field_validator("training_cli_options")(
        _canonical_options
    )

    @field_validator("imported_modules")
    @classmethod
    def _imports_are_canonical(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            not value
            or value != tuple(sorted(value))
            or len(value) != len(set(value))
            or any(_MODULE_PATTERN.fullmatch(item) is None for item in value)
        ):
            raise ValueError("imported modules must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _probe_is_closed(self) -> ProductionCudaImageProbe:
        if tuple(item.role for item in self.executables) != _EXECUTABLE_ROLES:
            raise ValueError("image executable role set or order is invalid")
        if not set(_REQUIRED_TRAINING_OPTIONS) <= set(
            self.training_cli_options
        ):
            raise ValueError("required training CLI options are missing")
        expected_schema = training_cli_schema_sha256(
            trainer_name="nerfstudio-splatfacto",
            observed_options=self.training_cli_options,
        )
        if self.training_cli_schema_sha256 != expected_schema:
            raise ValueError("training CLI schema SHA disagrees")
        expected_content = hashlib.sha256(
            canonical_production_cuda_image_probe_signing_bytes(self)
        ).hexdigest()
        if self.content_sha256 != expected_content:
            raise ValueError("image probe content SHA disagrees")
        if self.probe_id != (
            f"production-cuda-image-probe-{expected_content}"
        ):
            raise ValueError("image probe ID disagrees")
        return self

    @classmethod
    def create(cls, **fields: Any) -> ProductionCudaImageProbe:
        zero = "0" * 64
        provisional = cls.model_construct(
            probe_id=f"production-cuda-image-probe-{zero}",
            content_sha256=zero,
            **fields,
        )
        digest = hashlib.sha256(
            canonical_production_cuda_image_probe_signing_bytes(
                provisional
            )
        ).hexdigest()
        return cls(
            probe_id=f"production-cuda-image-probe-{digest}",
            content_sha256=digest,
            **fields,
        )


class OciAttestationBinding(FrozenModel):
    role: Literal[
        "buildkit-provenance",
        "buildkit-sbom",
        "github-build-provenance",
    ]
    predicate_type: Literal[
        "https://spdx.dev/Document",
        "https://slsa.dev/provenance/v1",
    ]
    manifest_digest: str = Field(pattern=_DIGEST_PATTERN)
    predicate_blob_digest: str = Field(pattern=_DIGEST_PATTERN)
    subject_digest: str = Field(pattern=_DIGEST_PATTERN)

    _digests_are_not_dummy = field_validator(
        "manifest_digest",
        "predicate_blob_digest",
        "subject_digest",
    )(_reject_uniform_identity)

    @model_validator(mode="after")
    def _predicate_matches_role(self) -> OciAttestationBinding:
        if self.predicate_type != _PREDICATE_BY_ROLE[self.role]:
            raise ValueError("attestation predicate differs from role")
        return self


class RuntimePolicyImageFacts(FrozenModel):
    expected_container_identity: str
    expected_cuda_runtime_version: Literal["11.8"]
    expected_python_version: Literal["3.11.9"]
    expected_nerfstudio_version: Literal["1.1.5"]
    expected_training_cli_schema_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    required_training_cli_options: tuple[str, ...]
    expected_python_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_training_cli_sha256: str = Field(pattern=_SHA256_PATTERN)


class ProductionCudaImageRelease(FrozenModel):
    schema_id: Literal["nantai.production-cuda-image-release.v1"] = Field(
        default="nantai.production-cuda-image-release.v1",
        alias="schema",
        serialization_alias="schema",
    )
    release_id: str = Field(pattern=_RELEASE_ID_PATTERN)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_commit: str = Field(pattern=_COMMIT_PATTERN)
    image_name: str = Field(pattern=_IMAGE_NAME_PATTERN)
    image_digest: str = Field(pattern=_DIGEST_PATTERN)
    platform: Literal["linux/amd64"]
    platform_manifest_digest: str = Field(pattern=_DIGEST_PATTERN)
    dockerfile_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    requirements_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_probe_sha256: str = Field(pattern=_SHA256_PATTERN)
    image_probe: ProductionCudaImageProbe
    workflow_repository: str = Field(
        pattern=_WORKFLOW_REPOSITORY_PATTERN
    )
    workflow_run_id: int = Field(ge=1)
    workflow_run_attempt: int = Field(ge=1)
    attestations: tuple[OciAttestationBinding, ...]

    _source_commit_is_not_dummy = field_validator("source_commit")(
        _reject_uniform_identity
    )
    _image_digest_is_not_dummy = field_validator("image_digest")(
        _reject_uniform_identity
    )
    _platform_digest_is_not_dummy = field_validator(
        "platform_manifest_digest"
    )(_reject_uniform_identity)
    _hashes_are_not_dummy = field_validator(
        "content_sha256",
        "dockerfile_sha256",
        "runtime_lock_sha256",
        "requirements_lock_sha256",
        "image_probe_sha256",
    )(_reject_uniform_identity)

    @field_validator("image_name")
    @classmethod
    def _image_name_has_safe_segments(cls, value: str) -> str:
        path = value.removeprefix("ghcr.io/")
        if (
            "//" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("GHCR image name is not canonical")
        return value

    @model_validator(mode="after")
    def _release_is_closed(self) -> ProductionCudaImageRelease:
        if self.runtime_lock_sha256 != (
            self.image_probe.runtime_lock_sha256
        ):
            raise ValueError("release runtime lock SHA differs from probe")
        if self.image_probe_sha256 != self.image_probe.content_sha256:
            raise ValueError("release image probe SHA differs from probe")
        if tuple(item.role for item in self.attestations) != (
            _ATTESTATION_ROLES
        ):
            raise ValueError("attestation role set or order is invalid")
        if len(
            {item.predicate_blob_digest for item in self.attestations}
        ) != len(self.attestations):
            raise ValueError(
                "attestation predicate blob digests must be distinct"
            )
        for item in self.attestations:
            expected_subject = (
                self.image_digest
                if item.role == "github-build-provenance"
                else self.platform_manifest_digest
            )
            if item.subject_digest != expected_subject:
                raise ValueError(
                    "attestation subject digest differs from role"
                )
        expected_content = hashlib.sha256(
            canonical_production_cuda_image_release_signing_bytes(self)
        ).hexdigest()
        if self.content_sha256 != expected_content:
            raise ValueError("image release content SHA disagrees")
        if self.release_id != (
            f"production-cuda-image-release-{expected_content}"
        ):
            raise ValueError("image release ID disagrees")
        return self

    @classmethod
    def create(
        cls,
        *,
        image_probe: ProductionCudaImageProbe,
        **fields: Any,
    ) -> ProductionCudaImageRelease:
        zero = "0" * 64
        bound_fields = {
            **fields,
            "platform": image_probe.platform,
            "runtime_lock_sha256": image_probe.runtime_lock_sha256,
            "image_probe_sha256": image_probe.content_sha256,
            "image_probe": image_probe,
        }
        provisional = cls.model_construct(
            release_id=f"production-cuda-image-release-{zero}",
            content_sha256=zero,
            **bound_fields,
        )
        digest = hashlib.sha256(
            canonical_production_cuda_image_release_signing_bytes(
                provisional
            )
        ).hexdigest()
        return cls(
            release_id=f"production-cuda-image-release-{digest}",
            content_sha256=digest,
            **bound_fields,
        )

    @property
    def image_identity(self) -> str:
        return f"{self.image_name}@{self.image_digest}"

    def runtime_policy_image_facts(self) -> RuntimePolicyImageFacts:
        executable_by_role = {
            item.role: item for item in self.image_probe.executables
        }
        return RuntimePolicyImageFacts(
            expected_container_identity=self.image_identity,
            expected_cuda_runtime_version=(
                self.image_probe.torch_cuda_version
            ),
            expected_python_version=self.image_probe.python_version,
            expected_nerfstudio_version=(
                self.image_probe.nerfstudio_version
            ),
            expected_training_cli_schema_sha256=(
                self.image_probe.training_cli_schema_sha256
            ),
            required_training_cli_options=_REQUIRED_TRAINING_OPTIONS,
            expected_python_sha256=executable_by_role["python"].sha256,
            expected_training_cli_sha256=(
                executable_by_role["ns-train"].sha256
            ),
        )


def canonical_production_cuda_image_probe_signing_bytes(
    probe: ProductionCudaImageProbe,
) -> bytes:
    return _canonical_json_bytes(
        probe.model_dump(
            mode="json",
            by_alias=True,
            exclude={"probe_id", "content_sha256"},
        )
    )


def canonical_production_cuda_image_probe_bytes(
    probe: ProductionCudaImageProbe,
) -> bytes:
    return _canonical_json_bytes(
        probe.model_dump(mode="json", by_alias=True)
    )


def canonical_production_cuda_image_release_signing_bytes(
    release: ProductionCudaImageRelease,
) -> bytes:
    return _canonical_json_bytes(
        release.model_dump(
            mode="json",
            by_alias=True,
            exclude={"release_id", "content_sha256"},
        )
    )


def canonical_production_cuda_image_release_bytes(
    release: ProductionCudaImageRelease,
) -> bytes:
    return _canonical_json_bytes(
        release.model_dump(mode="json", by_alias=True)
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProductionCudaImageReleaseError(
                "production CUDA image document has duplicate keys"
            )
        result[key] = value
    return result


def _load_canonical(
    payload: bytes,
    *,
    model,
    canonical,
    label: str,
):
    try:
        decoded = payload.decode("ascii")
        json.loads(decoded, object_pairs_hook=_reject_duplicate_pairs)
        parsed = model.model_validate_json(payload)
    except ProductionCudaImageReleaseError:
        raise
    except (UnicodeError, ValidationError, ValueError) as exc:
        raise ProductionCudaImageReleaseError(
            f"{label} is invalid: {exc}"
        ) from exc
    if payload != canonical(parsed):
        raise ProductionCudaImageReleaseError(
            f"{label} is not canonical"
        )
    return parsed


def load_production_cuda_image_probe_bytes(
    payload: bytes,
) -> ProductionCudaImageProbe:
    return _load_canonical(
        payload,
        model=ProductionCudaImageProbe,
        canonical=canonical_production_cuda_image_probe_bytes,
        label="production CUDA image probe",
    )


def load_production_cuda_image_release_bytes(
    payload: bytes,
) -> ProductionCudaImageRelease:
    return _load_canonical(
        payload,
        model=ProductionCudaImageRelease,
        canonical=canonical_production_cuda_image_release_bytes,
        label="production CUDA image release",
    )
