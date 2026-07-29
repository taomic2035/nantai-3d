"""Canonical dependency lock for the Production CUDA OCI runtime."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IMAGE_PATTERN = (
    r"^[a-z0-9][a-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9._+-]*)?$"
_SNAPSHOT_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
_MODULE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_CUDA_ARCHITECTURES = ("7.5", "8.0", "8.6", "8.9", "9.0+PTX")
_BASE_ROLES = ("builder", "runtime")
_SOURCE_ROLES = (
    "cpython-source",
    "gsplat-sdist",
    "nerfstudio-wheel",
    "pyliblzfse-sdist",
    "torch-wheel",
    "torchvision-wheel",
)
_AUXILIARY_PATHS = {
    "apt-build-lock": "containers/production-cuda/apt-build.lock",
    "apt-runtime-lock": "containers/production-cuda/apt-runtime.lock",
    "python-requirements-lock": (
        "containers/production-cuda/requirements.lock"
    ),
}
_COMMIT_BOUND_SOURCE_ROLES = {
    "gsplat-sdist",
    "nerfstudio-wheel",
}


class ProductionCudaRuntimeLockError(ValueError):
    """The Production CUDA runtime lock is ambiguous or untrusted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


def _reject_uniform_digest(value: str) -> str:
    digest = value.rpartition(":")[2]
    if len(digest) == 64 and len(set(digest)) == 1:
        raise ValueError("uniform dummy digest is not accepted")
    return value


def _portable_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("lock path must be portable and relative")
    return value


def _safe_filename(value: str) -> str:
    if (
        not value
        or len(value) > 255
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("artifact filename is invalid")
    return value


class LockedBaseImage(FrozenModel):
    role: Literal["builder", "runtime"]
    identity: str = Field(pattern=_IMAGE_PATTERN)
    platform: Literal["linux/amd64"]
    platform_manifest_digest: str = Field(pattern=_DIGEST_PATTERN)

    _identity_is_not_dummy = field_validator("identity")(
        _reject_uniform_digest
    )
    _manifest_is_not_dummy = field_validator("platform_manifest_digest")(
        _reject_uniform_digest
    )


class LockedSourceArtifact(FrozenModel):
    role: Literal[
        "cpython-source",
        "gsplat-sdist",
        "nerfstudio-wheel",
        "pyliblzfse-sdist",
        "torch-wheel",
        "torchvision-wheel",
    ]
    version: str = Field(pattern=_VERSION_PATTERN)
    filename: str
    url: str
    byte_length: int = Field(ge=1, le=4 * 1024 * 1024 * 1024)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    upstream_commit: str | None = Field(
        default=None,
        pattern=_COMMIT_PATTERN,
    )

    _filename_is_safe = field_validator("filename")(_safe_filename)
    _sha_is_not_dummy = field_validator("sha256")(
        _reject_uniform_digest
    )

    @field_validator("url")
    @classmethod
    def _url_is_immutable_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("artifact URL must be credential-free HTTPS")
        return value

    @model_validator(mode="after")
    def _source_binding_is_closed(self) -> LockedSourceArtifact:
        parsed = urlsplit(self.url)
        if unquote(PurePosixPath(parsed.path).name) != self.filename:
            raise ValueError("artifact URL filename differs from lock")
        if (
            self.role in _COMMIT_BOUND_SOURCE_ROLES
        ) != (self.upstream_commit is not None):
            raise ValueError("upstream commit binding differs from role")
        return self


class LockedAuxiliaryFile(FrozenModel):
    role: Literal[
        "apt-build-lock",
        "apt-runtime-lock",
        "python-requirements-lock",
    ]
    path: str
    byte_length: int = Field(ge=1, le=16 * 1024 * 1024)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _path_is_portable = field_validator("path")(_portable_relative_path)
    _sha_is_not_dummy = field_validator("sha256")(
        _reject_uniform_digest
    )

    @model_validator(mode="after")
    def _path_matches_role(self) -> LockedAuxiliaryFile:
        if self.path != _AUXILIARY_PATHS[self.role]:
            raise ValueError("auxiliary lock path differs from role")
        return self


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


class ProductionCudaRuntimeLock(FrozenModel):
    schema_id: Literal["nantai.production-cuda-runtime-lock.v1"] = Field(
        default="nantai.production-cuda-runtime-lock.v1",
        alias="schema",
        serialization_alias="schema",
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    platform: Literal["linux/amd64"]
    ubuntu_snapshot: str
    cuda_architectures: tuple[str, ...]
    base_images: tuple[LockedBaseImage, ...]
    source_artifacts: tuple[LockedSourceArtifact, ...]
    auxiliary_files: tuple[LockedAuxiliaryFile, ...]
    required_imports: tuple[str, ...]

    _content_sha_is_not_dummy = field_validator("content_sha256")(
        _reject_uniform_digest
    )

    @field_validator("ubuntu_snapshot")
    @classmethod
    def _snapshot_is_fixed(cls, value: str) -> str:
        if _SNAPSHOT_PATTERN.fullmatch(value) is None:
            raise ValueError("Ubuntu snapshot must be a fixed UTC timestamp")
        return value

    @field_validator("required_imports")
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
            raise ValueError("required imports must be sorted unique modules")
        return value

    @model_validator(mode="after")
    def _lock_is_closed(self) -> ProductionCudaRuntimeLock:
        if self.cuda_architectures != _CUDA_ARCHITECTURES:
            raise ValueError("CUDA architecture set differs from contract")
        if tuple(item.role for item in self.base_images) != _BASE_ROLES:
            raise ValueError("base image role set or order is invalid")
        if any(item.platform != self.platform for item in self.base_images):
            raise ValueError("base image platform differs from runtime")
        if tuple(item.role for item in self.source_artifacts) != _SOURCE_ROLES:
            raise ValueError("source artifact role set or order is invalid")
        if tuple(item.role for item in self.auxiliary_files) != tuple(
            sorted(_AUXILIARY_PATHS)
        ):
            raise ValueError("auxiliary file role set or order is invalid")
        expected = hashlib.sha256(
            canonical_production_cuda_runtime_lock_signing_bytes(self)
        ).hexdigest()
        if self.content_sha256 != expected:
            raise ValueError("runtime lock content SHA disagrees")
        return self

    @classmethod
    def create(cls, **fields: Any) -> ProductionCudaRuntimeLock:
        provisional = cls.model_construct(
            content_sha256="0" * 64,
            **fields,
        )
        digest = hashlib.sha256(
            canonical_production_cuda_runtime_lock_signing_bytes(provisional)
        ).hexdigest()
        return cls(content_sha256=digest, **fields)


def canonical_production_cuda_runtime_lock_signing_bytes(
    lock: ProductionCudaRuntimeLock,
) -> bytes:
    """Return canonical bytes that determine ``content_sha256``."""

    return _canonical_json_bytes(
        lock.model_dump(
            mode="json",
            by_alias=True,
            exclude={"content_sha256"},
        )
    )


def canonical_production_cuda_runtime_lock_bytes(
    lock: ProductionCudaRuntimeLock,
) -> bytes:
    """Return the only accepted complete lock representation."""

    return _canonical_json_bytes(
        lock.model_dump(mode="json", by_alias=True)
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProductionCudaRuntimeLockError(
                "production CUDA runtime lock has duplicate keys"
            )
        result[key] = value
    return result


def load_production_cuda_runtime_lock_bytes(
    payload: bytes,
) -> ProductionCudaRuntimeLock:
    """Load duplicate-key-free exact canonical runtime-lock bytes."""

    try:
        decoded = payload.decode("ascii")
        json.loads(decoded, object_pairs_hook=_reject_duplicate_pairs)
        lock = ProductionCudaRuntimeLock.model_validate_json(payload)
    except ProductionCudaRuntimeLockError:
        raise
    except (UnicodeError, ValidationError, ValueError) as exc:
        raise ProductionCudaRuntimeLockError(
            f"production CUDA runtime lock is invalid: {exc}"
        ) from exc
    if payload != canonical_production_cuda_runtime_lock_bytes(lock):
        raise ProductionCudaRuntimeLockError(
            "production CUDA runtime lock is not canonical"
        )
    return lock
