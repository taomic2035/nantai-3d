"""Deterministically produce a bound private production runtime policy.

The producer has no network or training side effects.  It derives only
repository-owned identities from one exact clean commit and requires the
operator to provide every external GPU/container fact explicitly.  Its output
is an allow-policy for later fresh measurement; it is not runtime evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from pipeline.durable_io import (
    DurableIOError,
    capture_real_directory_identity,
    first_linklike_path,
    flush_file,
    matches_real_directory_identity,
    publish_file_noreplace,
)
from pipeline.production_runtime_evidence import (
    ProductionRuntimeEvidenceError,
    ProductionRuntimePolicy,
    canonical_production_runtime_policy_bytes,
    load_production_runtime_policy_bytes,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CONTAINER_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$"
)
_GPU_UUID_PATTERN = (
    r"^GPU-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_VERSION_PATTERN = r"^[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9._+-]*)?$"
_COMMIT_RE = re.compile(rb"^[0-9a-f]{40}$")
_MAX_INPUT_BYTES = 1024 * 1024
_MAX_REPOSITORY_ARTIFACT_BYTES = 16 * 1024 * 1024
_REPOSITORY_ARTIFACTS = {
    "expected_checker_sha256": (
        "cloud/production_runtime_entrypoint.py"
    ),
    "expected_worker_sha256": "cloud/remote_training_worker.py",
}


class ProductionRuntimePolicyProducerError(ValueError):
    """A runtime policy cannot be constructed or published safely."""


class ProductionRuntimePolicyInput(BaseModel):
    """External approved facts needed to construct a runtime allow-policy."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    schema_id: Literal[
        "nantai.production-runtime-policy-input.v1"
    ] = Field(
        default="nantai.production-runtime-policy-input.v1",
        alias="schema",
        serialization_alias="schema",
    )
    expected_remote_target_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_container_identity: str = Field(
        pattern=_CONTAINER_PATTERN
    )
    expected_gpu_uuid: str = Field(pattern=_GPU_UUID_PATTERN)
    min_gpu_memory_mib: int = Field(ge=1)
    expected_cuda_runtime_version: str = Field(
        pattern=_VERSION_PATTERN
    )
    expected_python_version: str = Field(pattern=_VERSION_PATTERN)
    expected_nerfstudio_version: str = Field(
        pattern=_VERSION_PATTERN
    )
    expected_training_cli_schema_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    required_training_cli_options: tuple[str, ...]
    expected_container_runtime_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_nvidia_smi_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )
    expected_python_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_training_cli_sha256: str = Field(
        pattern=_SHA256_PATTERN
    )

    @field_validator(
        "expected_remote_target_sha256",
        "expected_training_cli_schema_sha256",
        "expected_container_runtime_sha256",
        "expected_nvidia_smi_sha256",
        "expected_python_sha256",
        "expected_training_cli_sha256",
    )
    @classmethod
    def _reject_placeholder_sha(cls, value: str) -> str:
        if len(set(value)) == 1:
            raise ValueError(
                "production runtime policy input rejects placeholder SHA"
            )
        return value

    @field_validator("expected_container_identity")
    @classmethod
    def _reject_placeholder_container_digest(cls, value: str) -> str:
        digest = value.rpartition("@sha256:")[2]
        if len(set(digest)) == 1:
            raise ValueError(
                "production runtime policy input rejects placeholder "
                "container digest"
            )
        return value

    @field_validator("required_training_cli_options")
    @classmethod
    def _options_are_canonical(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        if (
            not value
            or value != tuple(sorted(value))
            or len(value) != len(set(value))
            or any(
                not option.startswith("--")
                or len(option) < 3
                or any(
                    not (
                        character.islower()
                        or character.isdigit()
                        or character in {".", "-"}
                    )
                    for character in option[2:]
                )
                for option in value
            )
        ):
            raise ValueError(
                "training CLI options must be sorted unique long options"
            )
        return value


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


def canonical_production_runtime_policy_input_bytes(
    operator_input: ProductionRuntimePolicyInput,
) -> bytes:
    """Return the only accepted byte representation of operator facts."""

    return _canonical_json_bytes(
        operator_input.model_dump(mode="json", by_alias=True)
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProductionRuntimePolicyProducerError(
                "production runtime policy input has duplicate keys"
            )
        result[key] = value
    return result


def load_production_runtime_policy_input_bytes(
    payload: bytes,
) -> ProductionRuntimePolicyInput:
    """Load duplicate-key-free exact canonical operator facts."""

    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        operator_input = ProductionRuntimePolicyInput.model_validate_json(
            payload
        )
    except ProductionRuntimePolicyProducerError:
        raise
    except (UnicodeError, ValidationError, ValueError) as exc:
        raise ProductionRuntimePolicyProducerError(
            "production runtime policy input is invalid"
        ) from exc
    if (
        payload
        != canonical_production_runtime_policy_input_bytes(operator_input)
    ):
        raise ProductionRuntimePolicyProducerError(
            "production runtime policy input is not canonical"
        )
    return operator_input


def _identity_signature(
    value: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _change_signature(
    value: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        *_identity_signature(value),
        value.st_ctime_ns,
    )


def _absolute_path(path: Path, *, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ProductionRuntimePolicyProducerError(
            f"{label} must be absolute"
        )
    return candidate.absolute()


def _real_directory(path: Path, *, label: str) -> Path:
    candidate = _absolute_path(path, label=label)
    try:
        redirected = first_linklike_path(
            Path(candidate.anchor),
            candidate,
        )
        observed = candidate.lstat()
    except (OSError, ValueError) as exc:
        raise ProductionRuntimePolicyProducerError(
            f"{label} must be a real directory"
        ) from exc
    if redirected is not None or not stat.S_ISDIR(observed.st_mode):
        raise ProductionRuntimePolicyProducerError(
            f"{label} must be a real directory"
        )
    return candidate


def _read_stable_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    candidate = _absolute_path(path, label=label)
    try:
        redirected = first_linklike_path(
            Path(candidate.anchor),
            candidate,
        )
        before = candidate.lstat()
        if (
            redirected is not None
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > maximum_bytes
        ):
            raise ProductionRuntimePolicyProducerError(
                f"{label} must be a bounded real regular file"
            )
        with candidate.open("rb") as stream:
            opened_before = os.fstat(stream.fileno())
            payload = stream.read(maximum_bytes + 1)
            opened_after = os.fstat(stream.fileno())
        after = candidate.lstat()
    except ProductionRuntimePolicyProducerError:
        raise
    except OSError as exc:
        raise ProductionRuntimePolicyProducerError(
            f"{label} cannot be read"
        ) from exc
    if (
        len(payload) > maximum_bytes
        or _identity_signature(before)
        != _identity_signature(opened_before)
        or _identity_signature(opened_after)
        != _identity_signature(after)
        or _change_signature(before) != _change_signature(after)
        or _change_signature(opened_before)
        != _change_signature(opened_after)
    ):
        raise ProductionRuntimePolicyProducerError(
            f"{label} changed while read"
        )
    return payload


def _git(
    repo_root: Path,
    *arguments: str,
    maximum_bytes: int = _MAX_REPOSITORY_ARTIFACT_BYTES,
) -> bytes:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repo_root),
                *arguments,
            ],
            check=False,
            capture_output=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionRuntimePolicyProducerError(
            "exact repository identity cannot be read"
        ) from exc
    if (
        completed.returncode != 0
        or len(completed.stdout) > maximum_bytes
        or len(completed.stderr) > _MAX_INPUT_BYTES
    ):
        raise ProductionRuntimePolicyProducerError(
            "exact repository identity cannot be read"
        )
    return completed.stdout


def _exact_commit(repo_root: Path) -> str:
    payload = _git(
        repo_root,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        maximum_bytes=128,
    ).strip()
    if not _COMMIT_RE.fullmatch(payload):
        raise ProductionRuntimePolicyProducerError(
            "repository HEAD is not one exact commit"
        )
    return payload.decode("ascii")


def _committed_artifact(
    *,
    repo_root: Path,
    exact_commit: str,
    relative_path: str,
) -> bytes:
    tree = _git(
        repo_root,
        "ls-tree",
        "-z",
        exact_commit,
        "--",
        relative_path,
        maximum_bytes=1024,
    )
    records = [record for record in tree.split(b"\0") if record]
    if len(records) != 1:
        raise ProductionRuntimePolicyProducerError(
            f"{relative_path} is not one committed regular file"
        )
    metadata, separator, path_bytes = records[0].partition(b"\t")
    parts = metadata.split()
    if (
        not separator
        or len(parts) != 3
        or parts[0] not in {b"100644", b"100755"}
        or parts[1] != b"blob"
        or path_bytes != relative_path.encode("ascii")
    ):
        raise ProductionRuntimePolicyProducerError(
            f"{relative_path} is not one committed regular file"
        )
    return _git(
        repo_root,
        "cat-file",
        "blob",
        f"{exact_commit}:{relative_path}",
    )


def _repository_bindings(
    repo_root: Path,
) -> tuple[str, dict[str, str]]:
    root = _real_directory(repo_root, label="repository root")
    exact_commit = _exact_commit(root)
    bindings: dict[str, str] = {}
    for field, relative_path in _REPOSITORY_ARTIFACTS.items():
        worktree_bytes = _read_stable_regular_file(
            root / Path(relative_path),
            label=relative_path,
            maximum_bytes=_MAX_REPOSITORY_ARTIFACT_BYTES,
        )
        committed_bytes = _committed_artifact(
            repo_root=root,
            exact_commit=exact_commit,
            relative_path=relative_path,
        )
        if committed_bytes != worktree_bytes:
            raise ProductionRuntimePolicyProducerError(
                f"{relative_path} differs from exact commit"
            )
        bindings[field] = hashlib.sha256(worktree_bytes).hexdigest()
    if _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        maximum_bytes=_MAX_INPUT_BYTES,
    ):
        raise ProductionRuntimePolicyProducerError(
            "repository must be clean at the exact commit"
        )
    if _exact_commit(root) != exact_commit:
        raise ProductionRuntimePolicyProducerError(
            "repository HEAD changed while policy was built"
        )
    return exact_commit, bindings


def create_production_runtime_policy(
    *,
    repo_root: Path,
    operator_input: ProductionRuntimePolicyInput,
) -> ProductionRuntimePolicy:
    """Build the existing policy schema from exact code and external facts."""

    exact_commit, bindings = _repository_bindings(Path(repo_root))
    from cloud.production_runtime_entrypoint import (  # noqa: PLC0415
        fixed_production_probe_set_sha256,
    )

    fields = operator_input.model_dump(exclude={"schema_id"})
    try:
        return ProductionRuntimePolicy.create(
            expected_exact_commit=exact_commit,
            expected_probe_set_sha256=(
                fixed_production_probe_set_sha256()
            ),
            **bindings,
            **fields,
        )
    except (ProductionRuntimeEvidenceError, ValidationError, ValueError) as exc:
        raise ProductionRuntimePolicyProducerError(
            "production runtime policy fields are invalid"
        ) from exc


def _output_path(path: Path) -> tuple[Path, tuple[int, int, int]]:
    output = _absolute_path(path, label="runtime policy output")
    try:
        parent_identity = capture_real_directory_identity(output.parent)
        if output.exists() or output.is_symlink():
            raise ProductionRuntimePolicyProducerError(
                "runtime policy output must be absent"
            )
    except ProductionRuntimePolicyProducerError:
        raise
    except (OSError, DurableIOError) as exc:
        raise ProductionRuntimePolicyProducerError(
            "runtime policy output parent must be a real directory"
        ) from exc
    return output, parent_identity


def materialize_production_runtime_policy(
    *,
    repo_root: Path,
    operator_input_path: Path,
    output_path: Path,
) -> ProductionRuntimePolicy:
    """Publish one private canonical policy with durable no-replace semantics."""

    output, parent_identity = _output_path(Path(output_path))
    input_payload = _read_stable_regular_file(
        Path(operator_input_path),
        label="production runtime policy input",
        maximum_bytes=_MAX_INPUT_BYTES,
    )
    operator_input = load_production_runtime_policy_input_bytes(input_payload)
    policy = create_production_runtime_policy(
        repo_root=Path(repo_root),
        operator_input=operator_input,
    )
    payload = canonical_production_runtime_policy_bytes(policy)
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.staging"
    published = False
    try:
        descriptor = os.open(
            staging,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        flush_file(staging)
        if not matches_real_directory_identity(
            output.parent,
            parent_identity,
        ):
            raise ProductionRuntimePolicyProducerError(
                "runtime policy output parent changed before publication"
            )
        publish_file_noreplace(staging, output)
        published = True
        reopened = _read_stable_regular_file(
            output,
            label="published production runtime policy",
            maximum_bytes=_MAX_INPUT_BYTES,
        )
        if (
            reopened != payload
            or load_production_runtime_policy_bytes(reopened) != policy
        ):
            raise ProductionRuntimePolicyProducerError(
                "published production runtime policy cannot be verified"
            )
    except ProductionRuntimePolicyProducerError:
        raise
    except (OSError, DurableIOError, ProductionRuntimeEvidenceError) as exc:
        raise ProductionRuntimePolicyProducerError(
            "production runtime policy publication is ambiguous"
        ) from exc
    finally:
        if not published:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    return policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Produce a canonical private runtime allow-policy; this does not "
            "produce accepted runtime evidence."
        )
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--operator-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = materialize_production_runtime_policy(
            repo_root=args.repo_root,
            operator_input_path=args.operator_input,
            output_path=args.output,
        )
    except ProductionRuntimePolicyProducerError as exc:
        print(f"runtime policy production failed: {exc}", file=sys.stderr)
        return 2
    print(f"content_sha256={policy.content_sha256}")
    print(f"output={args.output.absolute()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
