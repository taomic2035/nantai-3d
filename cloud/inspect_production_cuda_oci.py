#!/usr/bin/env python3
"""Inspect and bind Production CUDA OCI attestations without a GPU."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE = re.compile(
    r"^ghcr\.io/[a-z0-9][a-z0-9._-]*/"
    r"[a-z0-9][a-z0-9._/-]*$"
)
_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_IN_TOTO = "application/vnd.in-toto+json"
_SIGSTORE_BUNDLE = "application/vnd.dev.sigstore.bundle.v0.3+json"
_SLSA = "https://slsa.dev/provenance/v1"
_SPDX = "https://spdx.dev/Document"
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_ATTESTATION_BYTES = 64 * 1024 * 1024
_MAX_REFERRERS = 64


class ProductionCudaOciInspectionError(RuntimeError):
    """OCI evidence is absent, ambiguous or inconsistent."""


class RegistryReader(Protocol):
    def get_manifest(self, reference: str) -> bytes: ...

    def get_blob(self, digest: str) -> bytes: ...

    def get_referrers(self, digest: str) -> bytes: ...


def _canonical_json_bytes(document: dict[str, Any]) -> bytes:
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


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProductionCudaOciInspectionError(
                "OCI JSON contains duplicate keys"
            )
        result[key] = value
    return result


def _json_document(
    payload: bytes,
    *,
    label: str,
    byte_cap: int,
) -> dict[str, Any]:
    if not payload or len(payload) > byte_cap:
        raise ProductionCudaOciInspectionError(
            f"{label} is empty or exceeds its byte cap"
        )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except ProductionCudaOciInspectionError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ProductionCudaOciInspectionError(
            f"{label} is not valid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ProductionCudaOciInspectionError(
            f"{label} must be a JSON object"
        )
    return document


def _require_digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ProductionCudaOciInspectionError(
            f"{label} is not a canonical SHA-256 digest"
        )
    return value


def _payload_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _verify_payload(
    payload: bytes,
    *,
    digest: str,
    size: int | None,
    label: str,
) -> None:
    if _payload_digest(payload) != digest:
        raise ProductionCudaOciInspectionError(
            f"{label} digest differs from its descriptor"
        )
    if size is not None and (
        not isinstance(size, int) or isinstance(size, bool) or size != len(payload)
    ):
        raise ProductionCudaOciInspectionError(
            f"{label} size differs from its descriptor"
        )


def _descriptor_digest(
    descriptor: Any,
    *,
    label: str,
) -> tuple[str, int | None]:
    if not isinstance(descriptor, dict):
        raise ProductionCudaOciInspectionError(
            f"{label} descriptor is not an object"
        )
    digest = _require_digest(descriptor.get("digest"), label=label)
    size = descriptor.get("size")
    if size is not None and (
        not isinstance(size, int) or isinstance(size, bool) or size < 1
    ):
        raise ProductionCudaOciInspectionError(
            f"{label} descriptor size is invalid"
        )
    return digest, size


def _manifest_descriptors(
    document: dict[str, Any],
    *,
    label: str,
) -> list[dict[str, Any]]:
    if (
        document.get("schemaVersion") != 2
        or document.get("mediaType") != _OCI_INDEX
    ):
        raise ProductionCudaOciInspectionError(
            f"{label} is not an OCI image index"
        )
    manifests = document.get("manifests")
    if (
        not isinstance(manifests, list)
        or not manifests
        or len(manifests) > _MAX_REFERRERS
        or any(not isinstance(item, dict) for item in manifests)
    ):
        raise ProductionCudaOciInspectionError(
            f"{label} has an invalid descriptor set"
        )
    return manifests


def _one(items: list[Any], *, label: str) -> Any:
    if len(items) != 1:
        raise ProductionCudaOciInspectionError(
            f"{label} must resolve to exactly one object"
        )
    return items[0]


def _validate_statement(
    payload: bytes,
    *,
    predicate_type: str,
    subject_digest: str,
    expected_subject_name: str | None,
    label: str,
) -> None:
    statement = _json_document(
        payload,
        label=label,
        byte_cap=_MAX_ATTESTATION_BYTES,
    )
    accepted_statement_types = {
        "https://in-toto.io/Statement/v1",
        "https://in-toto.io/Statement/v0.1",
    }
    if predicate_type == _SLSA:
        accepted_statement_types = {"https://in-toto.io/Statement/v1"}
    if statement.get("_type") not in accepted_statement_types:
        raise ProductionCudaOciInspectionError(
            f"{label} has an unsupported statement type"
        )
    if statement.get("predicateType") != predicate_type:
        raise ProductionCudaOciInspectionError(
            f"{label} predicate type differs"
        )
    subjects = statement.get("subject")
    subject = _one(
        subjects if isinstance(subjects, list) else [],
        label=f"{label} subject",
    )
    if not isinstance(subject, dict):
        raise ProductionCudaOciInspectionError(
            f"{label} subject is not an object"
        )
    digest = subject.get("digest")
    expected_hex = subject_digest.removeprefix("sha256:")
    if (
        not isinstance(subject.get("name"), str)
        or not subject["name"]
        or (
            expected_subject_name is not None
            and subject["name"] != expected_subject_name
        )
        or not isinstance(digest, dict)
        or set(digest) != {"sha256"}
        or digest["sha256"] != expected_hex
    ):
        raise ProductionCudaOciInspectionError(
            f"{label} subject is not bound to the expected digest"
        )


def _read_github_bundle(path: Path) -> tuple[bytes, dict[str, Any], bytes]:
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 1
            or before.st_size > _MAX_ATTESTATION_BYTES
        ):
            raise ProductionCudaOciInspectionError(
                "GitHub attestation bundle must be a bounded regular file"
            )
        first = path.read_bytes()
        after = path.lstat()
        second = path.read_bytes()
    except OSError as exc:
        raise ProductionCudaOciInspectionError(
            "GitHub attestation bundle cannot be read"
        ) from exc
    first_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    second_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if first_identity != second_identity or first != second:
        raise ProductionCudaOciInspectionError(
            "GitHub attestation bundle changed while reading"
        )
    if first.endswith(b"\r\n"):
        registry_payload = first[:-2]
    elif first.endswith(b"\n"):
        registry_payload = first[:-1]
    else:
        raise ProductionCudaOciInspectionError(
            "GitHub attestation bundle lacks its action newline"
        )
    bundle = _json_document(
        registry_payload,
        label="GitHub attestation bundle",
        byte_cap=_MAX_ATTESTATION_BYTES,
    )
    return registry_payload, bundle, first


def _github_statement(
    bundle: dict[str, Any],
    *,
    image_name: str,
    image_digest: str,
) -> None:
    if bundle.get("mediaType") != _SIGSTORE_BUNDLE:
        raise ProductionCudaOciInspectionError(
            "GitHub attestation bundle media type differs"
        )
    envelope = bundle.get("dsseEnvelope")
    if (
        not isinstance(envelope, dict)
        or envelope.get("payloadType") != _IN_TOTO
        or not isinstance(envelope.get("payload"), str)
    ):
        raise ProductionCudaOciInspectionError(
            "GitHub attestation bundle lacks a DSSE envelope"
        )
    try:
        statement = base64.b64decode(
            envelope["payload"],
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ProductionCudaOciInspectionError(
            "GitHub attestation payload is not canonical base64"
        ) from exc
    _validate_statement(
        statement,
        predicate_type=_SLSA,
        subject_digest=image_digest,
        expected_subject_name=image_name,
        label="GitHub attestation statement",
    )


def _buildkit_bindings(
    registry: RegistryReader,
    *,
    root: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    manifests = _manifest_descriptors(root, label="image root")
    platform = _one(
        [
            item
            for item in manifests
            if item.get("platform")
            == {"architecture": "amd64", "os": "linux"}
        ],
        label="linux/amd64 platform manifest",
    )
    platform_digest, platform_size = _descriptor_digest(
        platform,
        label="linux/amd64 platform manifest",
    )
    platform_payload = registry.get_manifest(platform_digest)
    _verify_payload(
        platform_payload,
        digest=platform_digest,
        size=platform_size,
        label="linux/amd64 platform manifest",
    )
    buildkit = _one(
        [
            item
            for item in manifests
            if item.get("platform")
            == {"architecture": "unknown", "os": "unknown"}
            and isinstance(item.get("annotations"), dict)
            and item["annotations"].get(
                "vnd.docker.reference.digest"
            )
            == platform_digest
            and item["annotations"].get("vnd.docker.reference.type")
            == "attestation-manifest"
        ],
        label="BuildKit attestation manifest",
    )
    buildkit_digest, buildkit_size = _descriptor_digest(
        buildkit,
        label="BuildKit attestation manifest",
    )
    buildkit_payload = registry.get_manifest(buildkit_digest)
    _verify_payload(
        buildkit_payload,
        digest=buildkit_digest,
        size=buildkit_size,
        label="BuildKit attestation manifest",
    )
    document = _json_document(
        buildkit_payload,
        label="BuildKit attestation manifest",
        byte_cap=_MAX_MANIFEST_BYTES,
    )
    layers = document.get("layers")
    if (
        document.get("schemaVersion") != 2
        or document.get("mediaType") != _OCI_MANIFEST
        or not isinstance(layers, list)
        or not layers
        or len(layers) > 16
    ):
        raise ProductionCudaOciInspectionError(
            "BuildKit attestation manifest structure differs"
        )
    bindings = []
    for role, predicate_type in (
        ("buildkit-provenance", _SLSA),
        ("buildkit-sbom", _SPDX),
    ):
        layer = _one(
            [
                item
                for item in layers
                if isinstance(item, dict)
                and item.get("mediaType") == _IN_TOTO
                and isinstance(item.get("annotations"), dict)
                and item["annotations"].get("in-toto.io/predicate-type")
                == predicate_type
            ],
            label=f"{role} layer",
        )
        blob_digest, blob_size = _descriptor_digest(
            layer,
            label=f"{role} layer",
        )
        blob = registry.get_blob(blob_digest)
        _verify_payload(
            blob,
            digest=blob_digest,
            size=blob_size,
            label=f"{role} blob",
        )
        _validate_statement(
            blob,
            predicate_type=predicate_type,
            subject_digest=platform_digest,
            expected_subject_name=None,
            label=f"{role} statement",
        )
        bindings.append(
            {
                "role": role,
                "predicate_type": predicate_type,
                "manifest_digest": buildkit_digest,
                "attestation_blob_digest": blob_digest,
                "subject_digest": platform_digest,
            }
        )
    return platform_digest, bindings


def _github_binding(
    registry: RegistryReader,
    *,
    image_name: str,
    image_digest: str,
    bundle_path: Path,
) -> dict[str, str]:
    bundle_payload, bundle, _file_payload = _read_github_bundle(bundle_path)
    _github_statement(
        bundle,
        image_name=image_name,
        image_digest=image_digest,
    )
    bundle_digest = _payload_digest(bundle_payload)
    referrers_payload = registry.get_referrers(image_digest)
    referrers = _json_document(
        referrers_payload,
        label="GitHub attestation referrers",
        byte_cap=_MAX_MANIFEST_BYTES,
    )
    descriptors = _manifest_descriptors(
        referrers,
        label="GitHub attestation referrers",
    )
    matches: list[tuple[str, str]] = []
    for descriptor in descriptors:
        if descriptor.get("artifactType") != _SIGSTORE_BUNDLE:
            continue
        manifest_digest, manifest_size = _descriptor_digest(
            descriptor,
            label="GitHub attestation manifest",
        )
        manifest_payload = registry.get_manifest(manifest_digest)
        _verify_payload(
            manifest_payload,
            digest=manifest_digest,
            size=manifest_size,
            label="GitHub attestation manifest",
        )
        manifest = _json_document(
            manifest_payload,
            label="GitHub attestation manifest",
            byte_cap=_MAX_MANIFEST_BYTES,
        )
        subject = manifest.get("subject")
        layers = manifest.get("layers")
        if (
            manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") != _OCI_MANIFEST
            or manifest.get("artifactType") != _SIGSTORE_BUNDLE
            or not isinstance(subject, dict)
            or subject.get("digest") != image_digest
            or manifest.get("annotations", {}).get(
                "dev.sigstore.bundle.predicateType"
            )
            != _SLSA
            or not isinstance(layers, list)
        ):
            continue
        matching_layers = [
            item
            for item in layers
            if isinstance(item, dict)
            and item.get("mediaType") == _SIGSTORE_BUNDLE
            and item.get("digest") == bundle_digest
            and item.get("size") == len(bundle_payload)
        ]
        if len(matching_layers) == 1:
            matches.append((manifest_digest, bundle_digest))
    manifest_digest, attestation_blob_digest = _one(
        matches,
        label="current GitHub attestation",
    )
    return {
        "role": "github-build-provenance",
        "predicate_type": _SLSA,
        "manifest_digest": manifest_digest,
        "attestation_blob_digest": attestation_blob_digest,
        "subject_digest": image_digest,
    }


def inspect_production_cuda_oci(
    *,
    registry: RegistryReader,
    image_name: str,
    image_digest: str,
    github_attestation_bundle: Path,
) -> dict[str, Any]:
    if _IMAGE.fullmatch(image_name) is None or ":" in image_name.removeprefix(
        "ghcr.io/"
    ):
        raise ProductionCudaOciInspectionError(
            "image name is not a canonical untagged GHCR name"
        )
    _require_digest(image_digest, label="image digest")
    root_payload = registry.get_manifest(image_digest)
    _verify_payload(
        root_payload,
        digest=image_digest,
        size=None,
        label="image root",
    )
    root = _json_document(
        root_payload,
        label="image root",
        byte_cap=_MAX_MANIFEST_BYTES,
    )
    platform_digest, bindings = _buildkit_bindings(
        registry,
        root=root,
    )
    bindings.append(
        _github_binding(
            registry,
            image_name=image_name,
            image_digest=image_digest,
            bundle_path=github_attestation_bundle,
        )
    )
    return {
        "schema": "nantai.production-cuda-oci-inspection.v1",
        "image_name": image_name,
        "image_digest": image_digest,
        "platform": "linux/amd64",
        "platform_manifest_digest": platform_digest,
        "attestations": bindings,
    }


class GhcrRegistry:
    """Small bounded, read-only GHCR Distribution API client."""

    def __init__(
        self,
        *,
        image_name: str,
        actor: str,
        github_token: str,
    ) -> None:
        if _IMAGE.fullmatch(image_name) is None:
            raise ProductionCudaOciInspectionError(
                "GHCR image name is invalid"
            )
        if not actor or not github_token:
            raise ProductionCudaOciInspectionError(
                "GHCR actor or token is unavailable"
            )
        self._repository = image_name.removeprefix("ghcr.io/")
        self._actor = actor
        self._github_token = github_token
        self._registry_token: str | None = None

    def _exchange_token(self) -> str:
        if self._registry_token is not None:
            return self._registry_token
        query = urllib.parse.urlencode(
            {
                "scope": f"repository:{self._repository}:pull",
                "service": "ghcr.io",
            }
        )
        credentials = base64.b64encode(
            f"{self._actor}:{self._github_token}".encode()
        ).decode("ascii")
        request = urllib.request.Request(
            f"https://ghcr.io/token?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {credentials}",
                "User-Agent": "nantai-production-cuda-inspector/1",
            },
        )
        payload = self._open(request, byte_cap=128 * 1024)
        document = _json_document(
            payload,
            label="GHCR token response",
            byte_cap=128 * 1024,
        )
        token = document.get("token")
        if not isinstance(token, str) or len(token) < 20:
            raise ProductionCudaOciInspectionError(
                "GHCR token response is invalid"
            )
        self._registry_token = token
        return token

    @staticmethod
    def _open(
        request: urllib.request.Request,
        *,
        byte_cap: int,
        allow_not_found: bool = False,
    ) -> bytes | None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read(byte_cap + 1)
                if len(payload) > byte_cap:
                    raise ProductionCudaOciInspectionError(
                        "GHCR response exceeds its byte cap"
                    )
                return payload
            except urllib.error.HTTPError as exc:
                if allow_not_found and exc.code == 404:
                    return None
                last_error = exc
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (TimeoutError, urllib.error.URLError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1 << attempt)
        raise ProductionCudaOciInspectionError(
            "GHCR request failed"
        ) from last_error

    def _get(
        self,
        path: str,
        *,
        accept: str,
        byte_cap: int,
        allow_not_found: bool = False,
    ) -> bytes | None:
        token = self._exchange_token()
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{self._repository}/{path}",
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {token}",
                "User-Agent": "nantai-production-cuda-inspector/1",
            },
        )
        return self._open(
            request,
            byte_cap=byte_cap,
            allow_not_found=allow_not_found,
        )

    def get_manifest(self, reference: str) -> bytes:
        payload = self._get(
            f"manifests/{reference}",
            accept=(
                f"{_OCI_INDEX}, {_OCI_MANIFEST}, "
                "application/vnd.docker.distribution.manifest.list.v2+json"
            ),
            byte_cap=_MAX_MANIFEST_BYTES,
        )
        assert payload is not None
        return payload

    def get_blob(self, digest: str) -> bytes:
        _require_digest(digest, label="blob digest")
        payload = self._get(
            f"blobs/{digest}",
            accept="application/octet-stream",
            byte_cap=_MAX_ATTESTATION_BYTES,
        )
        assert payload is not None
        return payload

    def get_referrers(self, digest: str) -> bytes:
        _require_digest(digest, label="referrers digest")
        payload = self._get(
            f"referrers/{digest}",
            accept=_OCI_INDEX,
            byte_cap=_MAX_MANIFEST_BYTES,
            allow_not_found=True,
        )
        if payload is not None:
            document = _json_document(
                payload,
                label="GHCR referrers response",
                byte_cap=_MAX_MANIFEST_BYTES,
            )
            manifests = document.get("manifests")
            if isinstance(manifests, list) and manifests:
                return payload
        return self.get_manifest(digest.replace(":", "-", 1))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bind one Production CUDA image digest to BuildKit and GitHub "
            "OCI attestation bytes."
        )
    )
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument(
        "--github-attestation-bundle",
        required=True,
        type=Path,
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        output = args.output.absolute()
        if output.exists() or output.is_symlink():
            raise ProductionCudaOciInspectionError(
                "inspection output already exists"
            )
        registry = GhcrRegistry(
            image_name=args.image_name,
            actor=os.environ.get("GITHUB_ACTOR", ""),
            github_token=os.environ.get("GHCR_TOKEN", ""),
        )
        result = inspect_production_cuda_oci(
            registry=registry,
            image_name=args.image_name,
            image_digest=args.image_digest,
            github_attestation_bundle=args.github_attestation_bundle,
        )
        payload = _canonical_json_bytes(result)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if output.read_bytes() != payload:
            raise ProductionCudaOciInspectionError(
                "inspection output differs after publication"
            )
    except (OSError, ValueError, ProductionCudaOciInspectionError) as exc:
        print(f"production CUDA OCI inspection failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "image_identity": (
                    f"{args.image_name}@{args.image_digest}"
                ),
                "inspection_output": str(output),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
