"""Fixed, executable recovery drills for the remote training boundary.

The public runner owns the case registry and derives every case outcome from
an actual bounded pytest process.  Callers provide only production context
identities; they cannot supply cases or pass/fail outcomes.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pipeline.durable_io import DurableIOError, flush_file, publish_file_noreplace


class RemoteTrainingDrillError(ValueError):
    """A drill run or its evidence cannot satisfy the fixed contract."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


DrillSuite = Literal[
    "submit-poll-fetch",
    "checksum-content-drift",
    "crash-resume-retry",
]
DrillCaseStatus = Literal["passed", "failed"]
DrillFailureCode = Literal[
    "pytest-failed",
    "case-timeout",
    "case-exec-error",
    "output-too-large",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
_CONTAINER_PATTERN = r"^[A-Za-z0-9._/:+-]+@sha256:[0-9a-f]{64}$"
_MAX_REPORT_BYTES = 4 * 1024 * 1024
_MAX_CASE_OUTPUT_BYTES = 1024 * 1024
_CASE_TIMEOUT_SECONDS = 120


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


@dataclass(frozen=True)
class DrillCaseDefinition:
    case_id: str
    suite: DrillSuite
    pytest_node_id: str
    expected_semantics: str

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(
            {
                "case_id": self.case_id,
                "expected_semantics": self.expected_semantics,
                "pytest_node_id": self.pytest_node_id,
                "suite": self.suite,
            }
        )

    @property
    def definition_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


DRILL_CASES: tuple[DrillCaseDefinition, ...] = (
    DrillCaseDefinition(
        "P1-3A-submit-running",
        "submit-poll-fetch",
        (
            "tests/test_remote_shell_executor.py::"
            "test_submit_keeps_receipt_not_started_until_authoritative_poll"
        ),
        "submit remains not-started until authoritative lifecycle/status poll",
    ),
    DrillCaseDefinition(
        "P1-3A-poll-timeout-unknown",
        "submit-poll-fetch",
        "tests/test_remote_shell_executor.py::test_poll_timeout_returns_unknown",
        "a bounded poll timeout remains monotonic unknown",
    ),
    DrillCaseDefinition(
        "P1-3A-remote-failed",
        "submit-poll-fetch",
        "tests/test_remote_shell_executor.py::test_poll_remote_failed_returns_failed_observation",
        "a verified nonzero remote result becomes failed",
    ),
    DrillCaseDefinition(
        "P1-3A-fetch-interrupted",
        "submit-poll-fetch",
        "tests/test_remote_shell_executor.py::test_fetch_interrupted_cleans_staging",
        "an interrupted fetch cannot expose a final result bundle",
    ),
    DrillCaseDefinition(
        "P1-3B-result-content-drift",
        "checksum-content-drift",
        "tests/test_remote_shell_executor.py::test_result_bundle_rejects_member_content_drift",
        "result member content drift fails closed",
    ),
    DrillCaseDefinition(
        "P1-3B-manifest-sha-drift",
        "checksum-content-drift",
        "tests/test_remote_shell_executor.py::test_result_bundle_rejects_manifest_member_sha_drift",
        "manifest member sha drift fails closed",
    ),
    DrillCaseDefinition(
        "P1-3B-container-identity-drift",
        "checksum-content-drift",
        "tests/test_remote_shell_executor.py::test_result_bundle_rejects_container_identity_drift",
        "container identity drift fails closed",
    ),
    DrillCaseDefinition(
        "P1-3C-executor-restore-no-transport",
        "crash-resume-retry",
        "tests/test_remote_shell_executor.py::test_restore_attaches_existing_job_without_transport",
        "a fresh executor restores a bound job without transport",
    ),
    DrillCaseDefinition(
        "P1-3C-operation-reconnect-no-resubmit",
        "crash-resume-retry",
        "tests/test_real_scene_operations.py::test_existing_remote_job_is_restored_without_resubmit",
        "production reconnect restores the original job without resubmit",
    ),
    DrillCaseDefinition(
        "P1-3C-resume-original-attempt",
        "crash-resume-retry",
        "tests/test_real_scene_runner.py::test_unknown_remote_state_resumes_the_same_attempt",
        "resume reconnects the original unknown attempt",
    ),
    DrillCaseDefinition(
        "P1-3C-retry-new-attempt",
        "crash-resume-retry",
        "tests/test_real_scene_runner.py::test_unknown_remote_state_explicit_retry_uses_new_attempt",
        "explicit retry creates a new attempt identity",
    ),
)


def drill_case_set_sha256() -> str:
    return hashlib.sha256(
        b"".join(case.canonical_bytes() for case in DRILL_CASES)
    ).hexdigest()


class RemoteTrainingDrillCaseResult(FrozenModel):
    case_id: str = Field(pattern=r"^P1-3[A-C]-[a-z0-9-]+$")
    suite: DrillSuite
    definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    command_sha256: str = Field(pattern=_SHA256_PATTERN)
    observation_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: DrillCaseStatus
    exit_code: int | None = None
    failure_code: DrillFailureCode | None = None

    @model_validator(mode="after")
    def _status_is_consistent(self) -> RemoteTrainingDrillCaseResult:
        if self.status == "passed":
            if self.exit_code != 0 or self.failure_code is not None:
                raise ValueError("passed drill case requires exit code zero")
        elif self.failure_code is None:
            raise ValueError("failed drill case requires a failure code")
        return self


class RemoteTrainingDrillReport(FrozenModel):
    schema_id: Literal["nantai.remote-training-drill.v1"] = Field(
        default="nantai.remote-training-drill.v1",
        alias="schema",
        serialization_alias="schema",
    )
    evidence_scope: Literal["transport-fixture"] = "transport-fixture"
    generated_at_utc: datetime
    report_id: str = Field(
        pattern=r"^remote-training-drill-[0-9a-f]{64}$",
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: Literal["accepted", "failed"]
    exact_commit: str = Field(pattern=_GIT_SHA_PATTERN)
    tree_clean: Literal[True] = True
    python_version: str = Field(min_length=1, max_length=64)
    pytest_version: str = Field(min_length=1, max_length=64)
    ruff_version: str = Field(min_length=1, max_length=64)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    training_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    trainer_name: str = Field(min_length=1, max_length=128)
    trainer_version: str = Field(min_length=1, max_length=128)
    container_identity: str = Field(pattern=_CONTAINER_PATTERN)
    case_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    case_results: tuple[RemoteTrainingDrillCaseResult, ...] = Field(
        min_length=1,
    )

    @field_validator("generated_at_utc")
    @classmethod
    def _utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at_utc must include timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _fixed_registry_and_content(self) -> RemoteTrainingDrillReport:
        expected_ids = tuple(case.case_id for case in DRILL_CASES)
        actual_ids = tuple(item.case_id for item in self.case_results)
        if actual_ids != expected_ids:
            raise ValueError("drill cases must match the fixed registry")
        for definition, result in zip(
            DRILL_CASES,
            self.case_results,
            strict=True,
        ):
            if (
                result.suite != definition.suite
                or result.definition_sha256
                != definition.definition_sha256
            ):
                raise ValueError(
                    "drill case disagrees with the fixed registry"
                )
        if self.case_set_sha256 != drill_case_set_sha256():
            raise ValueError("drill case_set_sha256 disagrees")
        expected_status = (
            "accepted"
            if all(item.status == "passed" for item in self.case_results)
            else "failed"
        )
        if self.status != expected_status:
            raise ValueError("drill report status disagrees")
        expected_sha = remote_training_drill_content_sha256(self)
        if self.content_sha256 != expected_sha:
            raise ValueError("drill report content_sha256 disagrees")
        if self.report_id != f"remote-training-drill-{expected_sha}":
            raise ValueError("drill report_id disagrees")
        return self


def remote_training_drill_content_sha256(
    report: RemoteTrainingDrillReport,
) -> str:
    payload = report.model_dump(
        mode="json",
        by_alias=True,
        exclude={"report_id", "content_sha256"},
    )
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def canonical_remote_training_drill_bytes(
    report: RemoteTrainingDrillReport,
) -> bytes:
    return _canonical_json_bytes(
        report.model_dump(mode="json", by_alias=True)
    )


def _build_remote_training_drill_report(
    **fields: Any,
) -> RemoteTrainingDrillReport:
    case_results = tuple(fields["case_results"])
    status = (
        "accepted"
        if all(item.status == "passed" for item in case_results)
        else "failed"
    )
    zero = "0" * 64
    provisional = RemoteTrainingDrillReport.model_construct(
        report_id=f"remote-training-drill-{zero}",
        content_sha256=zero,
        status=status,
        **{**fields, "case_results": case_results},
    )
    digest = remote_training_drill_content_sha256(provisional)
    return RemoteTrainingDrillReport(
        report_id=f"remote-training-drill-{digest}",
        content_sha256=digest,
        status=status,
        **{**fields, "case_results": case_results},
    )


def _bounded_process(
    argv: list[str],
    *,
    root: Path,
    timeout: int,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=root,
        shell=False,
        capture_output=True,
        timeout=timeout,
    )


def _measure_git_state(root: Path) -> tuple[str, bool]:
    try:
        head = _bounded_process(
            ["git", "rev-parse", "HEAD"],
            root=root,
            timeout=30,
        )
        status_result = _bounded_process(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            root=root,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RemoteTrainingDrillError("git state cannot be measured") from exc
    if head.returncode != 0 or status_result.returncode != 0:
        raise RemoteTrainingDrillError("git state cannot be measured")
    try:
        exact_commit = (head.stdout or b"").decode("ascii").strip()
    except UnicodeError as exc:
        raise RemoteTrainingDrillError("git commit is invalid") from exc
    if len(exact_commit) != 40 or any(
        character not in "0123456789abcdef" for character in exact_commit
    ):
        raise RemoteTrainingDrillError("git commit is invalid")
    return exact_commit, not bool(status_result.stdout or b"")


def _tool_version(root: Path) -> str:
    try:
        completed = _bounded_process(
            [sys.executable, "-m", "ruff", "--version"],
            root=root,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RemoteTrainingDrillError(
            "ruff version cannot be measured"
        ) from exc
    if completed.returncode != 0:
        raise RemoteTrainingDrillError("ruff version cannot be measured")
    try:
        version = (completed.stdout or b"").decode("ascii").strip()
    except UnicodeError as exc:
        raise RemoteTrainingDrillError(
            "ruff version cannot be measured"
        ) from exc
    if not version or len(version) > 64:
        raise RemoteTrainingDrillError("ruff version cannot be measured")
    return version


def _normalized_case_command(case: DrillCaseDefinition) -> list[str]:
    return [
        "<python>",
        "-m",
        "pytest",
        case.pytest_node_id,
        "-q",
        "--tb=short",
        "--disable-warnings",
        "--maxfail=1",
    ]


def _execute_case(
    root: Path,
    case: DrillCaseDefinition,
) -> RemoteTrainingDrillCaseResult:
    normalized = _normalized_case_command(case)
    argv = [sys.executable, *normalized[1:]]
    command_sha = hashlib.sha256(
        _canonical_json_bytes({"argv": normalized})
    ).hexdigest()
    exit_code: int | None
    failure_code: DrillFailureCode | None
    stdout = b""
    stderr = b""
    try:
        completed = _bounded_process(
            argv,
            root=root,
            timeout=_CASE_TIMEOUT_SECONDS,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
        if (
            len(stdout) > _MAX_CASE_OUTPUT_BYTES
            or len(stderr) > _MAX_CASE_OUTPUT_BYTES
        ):
            failure_code = "output-too-large"
        elif exit_code == 0:
            failure_code = None
        else:
            failure_code = "pytest-failed"
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        failure_code = "case-timeout"
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
    except OSError:
        exit_code = None
        failure_code = "case-exec-error"
    status: DrillCaseStatus = (
        "passed" if failure_code is None else "failed"
    )
    observation = {
        "exit_code": exit_code,
        "failure_code": failure_code,
        "status": status,
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
    }
    return RemoteTrainingDrillCaseResult(
        case_id=case.case_id,
        suite=case.suite,
        definition_sha256=case.definition_sha256,
        command_sha256=command_sha,
        observation_sha256=hashlib.sha256(
            _canonical_json_bytes(observation)
        ).hexdigest(),
        status=status,
        exit_code=exit_code,
        failure_code=failure_code,
    )


def run_remote_training_drills(
    repo_root: str | Path,
    *,
    request_sha256: str,
    training_config_sha256: str,
    dataset_receipt_sha256: str,
    trainer_name: str,
    trainer_version: str,
    container_identity: str,
    generated_at_utc: datetime | None = None,
) -> RemoteTrainingDrillReport:
    """Execute the fixed registry and derive a canonical machine report."""

    root = Path(repo_root).resolve()
    exact_commit, clean = _measure_git_state(root)
    if not clean:
        raise RemoteTrainingDrillError(
            "remote training drills require a clean git tree"
        )
    ruff_version = _tool_version(root)
    results = tuple(_execute_case(root, case) for case in DRILL_CASES)
    final_commit, final_clean = _measure_git_state(root)
    if final_commit != exact_commit or not final_clean:
        raise RemoteTrainingDrillError(
            "git state changed during remote training drills"
        )
    return _build_remote_training_drill_report(
        generated_at_utc=generated_at_utc or datetime.now(UTC),
        exact_commit=exact_commit,
        tree_clean=True,
        python_version=platform.python_version(),
        pytest_version=pytest.__version__,
        ruff_version=ruff_version,
        request_sha256=request_sha256,
        training_config_sha256=training_config_sha256,
        dataset_receipt_sha256=dataset_receipt_sha256,
        trainer_name=trainer_name,
        trainer_version=trainer_version,
        container_identity=container_identity,
        case_set_sha256=drill_case_set_sha256(),
        case_results=results,
    )


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RemoteTrainingDrillError(
                "remote training drill report has duplicate keys"
            )
        result[key] = value
    return result


def _stable_report_bytes(path: Path) -> bytes:
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > _MAX_REPORT_BYTES
        ):
            raise RemoteTrainingDrillError(
                "remote training drill report file is invalid"
            )
        payload = path.read_bytes()
        after = path.lstat()
    except RemoteTrainingDrillError:
        raise
    except OSError as exc:
        raise RemoteTrainingDrillError(
            "remote training drill report cannot be read"
        ) from exc
    signature = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if signature != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or len(payload) != before.st_size:
        raise RemoteTrainingDrillError(
            "remote training drill report changed while read"
        )
    return payload


def load_remote_training_drill_report(
    path: str | Path,
) -> RemoteTrainingDrillReport:
    payload = _stable_report_bytes(Path(path))
    try:
        json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        report = RemoteTrainingDrillReport.model_validate_json(payload)
    except RemoteTrainingDrillError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise RemoteTrainingDrillError(
            "remote training drill report is invalid"
        ) from exc
    if payload != canonical_remote_training_drill_bytes(report):
        raise RemoteTrainingDrillError(
            "remote training drill report is not canonical"
        )
    return report


def publish_remote_training_drill_report(
    destination: str | Path,
    report: RemoteTrainingDrillReport,
) -> None:
    final = Path(destination).absolute()
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.with_name(
        f".{final.name}.{uuid.uuid4().hex}.partial"
    )
    try:
        with staging.open("xb") as stream:
            stream.write(canonical_remote_training_drill_bytes(report))
            stream.flush()
            os.fsync(stream.fileno())
        flush_file(staging)
        publish_file_noreplace(staging, final)
    except FileExistsError:
        raise
    except DurableIOError as exc:
        state = (
            "published but durability is unconfirmed"
            if exc.published
            else "not published"
        )
        raise RemoteTrainingDrillError(
            f"remote training drill report cannot be published ({state})"
        ) from exc
    except OSError as exc:
        raise RemoteTrainingDrillError(
            "remote training drill report cannot be published"
        ) from exc
    finally:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
