"""Ingest-only execution contracts for the local Studio job kernel.

This module owns fixed command construction, command-specific concurrency
snapshots, and cross-process file locks.  Process execution and publication are
added in later B1 tasks; no function here accepts arbitrary commands or paths.
"""

from __future__ import annotations

import codecs
import hashlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

import psutil

from pipeline.ingest_manifest import (
    PHOTO_SOURCE_SUFFIXES,
    VIDEO_SOURCE_SUFFIXES,
    IngestParams,
    sha256_file,
    verify_ingest_artifact,
)
from pipeline.studio_ledger import (
    CreateRunResult,
    StudioLedger,
    canonical_json,
    sqlite_write_transaction_active,
)

_LOCK_STATE_GUARD = threading.RLock()
_HELD_LOCKS: dict[Path, list[ProjectFileLock]] = {}
_RUN_ID_RE = re.compile(r"^run-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_ENVIRONMENT_ALLOWLIST = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
)


class JobContractError(ValueError):
    """A command, path, or byte snapshot is not safe to execute."""


class LockOrderError(RuntimeError):
    """A caller attempted to violate the global lock discipline."""


class ProcessExecutionError(RuntimeError):
    """The controlled child could not be observed or drained safely."""


class ConcurrentChangeError(JobContractError):
    """A command input or formal target changed before publication."""


class WriterBusyError(RuntimeError):
    """Another process or worker currently owns the project writer lock."""


class ProjectFileLock:
    """Exclusive cross-process project lock with explicit role ordering."""

    def __init__(self, path: str | Path, *, role: Literal["writer", "publish"]):
        self.path = Path(path)
        self.role = role
        self._stream = None
        self._overlapped = None
        self._domain = self.path.parent.absolute()

    @property
    def acquired(self) -> bool:
        return self._stream is not None

    def _check_order(self) -> None:
        if sqlite_write_transaction_active():
            raise LockOrderError("file locks cannot be acquired inside a SQLite write transaction")
        with _LOCK_STATE_GUARD:
            roles = [lock.role for lock in _HELD_LOCKS.get(self._domain, [])]
            if self.role == "writer":
                if roles:
                    raise LockOrderError("writer lock must be the first acquired lock")
            elif roles != ["writer"]:
                raise LockOrderError("publish lock requires the writer lock")

    @staticmethod
    def _prepare_stream(path: Path):
        path = path.absolute()
        parent = path.parent
        if not _path_exists(parent):
            grandparent = parent.parent
            if (
                _is_linklike(grandparent)
                or grandparent.resolve(strict=True) != grandparent
                or not grandparent.is_dir()
            ):
                raise JobContractError("project lock parent is not safely creatable")
            parent.mkdir(parents=False, exist_ok=False)
        if (
            _is_linklike(parent)
            or parent.resolve(strict=True) != parent
            or not parent.is_dir()
            or (_path_exists(path) and _is_linklike(path))
        ):
            raise JobContractError("project lock path is not a real managed path")
        stream = path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        return stream

    @staticmethod
    def _lock_stream(stream, *, blocking: bool):
        if os.name != "nt":
            import fcntl

            operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(stream.fileno(), operation)
            except BlockingIOError:
                return False
            return None

        try:
            import msvcrt

            import pywintypes
            import win32con
            import win32file
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise JobContractError("pywin32 is required for Windows project locking") from exc

        handle = msvcrt.get_osfhandle(stream.fileno())
        overlapped = pywintypes.OVERLAPPED()
        flags = win32con.LOCKFILE_EXCLUSIVE_LOCK
        if not blocking:
            flags |= win32con.LOCKFILE_FAIL_IMMEDIATELY
        try:
            win32file.LockFileEx(handle, flags, 0, 1, overlapped)
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) in {32, 33, 36, 158}:
                return False
            raise
        return overlapped

    def acquire(self, *, blocking: bool = True) -> bool:
        if self.acquired:
            raise LockOrderError("lock instance is already acquired")
        with _LOCK_STATE_GUARD:
            self._check_order()
            stream = self._prepare_stream(self.path)
            try:
                marker = self._lock_stream(stream, blocking=blocking)
                if marker is False:
                    stream.close()
                    return False
            except BaseException:
                stream.close()
                raise
            self._stream = stream
            self._overlapped = marker
            _HELD_LOCKS.setdefault(self._domain, []).append(self)
            return True

    def release(self) -> None:
        if not self.acquired:
            return
        with _LOCK_STATE_GUARD:
            locks = _HELD_LOCKS.get(self._domain, [])
            if not locks or locks[-1] is not self:
                raise LockOrderError("locks must be released in reverse acquisition order")
            stream = self._stream
            try:
                if os.name == "nt":
                    import msvcrt

                    import win32file

                    handle = msvcrt.get_osfhandle(stream.fileno())
                    win32file.UnlockFileEx(handle, 0, 1, self._overlapped)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
                self._stream = None
                self._overlapped = None
                locks.pop()
                if not locks:
                    _HELD_LOCKS.pop(self._domain, None)

    def __enter__(self) -> ProjectFileLock:
        if not self.acquire(blocking=True):  # pragma: no cover - blocking contract
            raise RuntimeError("blocking project lock unexpectedly contended")
        return self

    def __exit__(self, *_args) -> None:
        self.release()


@dataclass(frozen=True)
class FileEvidence:
    path: str
    kind: Literal["photo", "video", "file"]
    size: int
    sha256: str


@dataclass(frozen=True)
class TargetEvidence:
    state: Literal["absent", "tree"]
    digest: str | None
    files: tuple[FileEvidence, ...]


@dataclass(frozen=True)
class ConcurrencySnapshot:
    inputs: tuple[FileEvidence, ...]
    input_digest: str
    target: TargetEvidence

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ConcurrencySnapshot:
        inputs = tuple(FileEvidence(**item) for item in value["inputs"])
        target_value = value["target"]
        target = TargetEvidence(
            state=target_value["state"],
            digest=target_value["digest"],
            files=tuple(FileEvidence(**item) for item in target_value["files"]),
        )
        return cls(
            inputs=inputs,
            input_digest=value["input_digest"],
            target=target,
        )


@dataclass(frozen=True)
class JobInvocation:
    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    input_dir: Path
    stage_dir: Path
    target_dir: Path
    shell: Literal[False] = False


@dataclass(frozen=True)
class ProcessEvent:
    stream: Literal["stdout", "stderr"]
    message: str
    truncated: bool


@dataclass(frozen=True)
class ProcessResult:
    pid: int
    start_identity: str
    exit_code: int
    log_path: Path


@dataclass(frozen=True)
class DurabilityReadiness:
    ready: bool
    reason: str
    filesystem: str | None


@dataclass(frozen=True)
class PublicationResult:
    publication_id: str
    artifact_id: str


@dataclass(frozen=True)
class StartupRecovery:
    ready: bool
    reason: str
    observer_only: bool = False


def _windows_start_identity(handle) -> str:
    import win32process

    created = win32process.GetProcessTimes(handle)["CreationTime"]
    return f"windows-created:{created.timestamp():.6f}"


def _process_start_identity(pid: int, *, handle=None) -> str:
    if os.name == "nt":
        if handle is not None:
            return _windows_start_identity(handle)
        try:
            import win32api
            import win32con
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProcessExecutionError("pywin32 is required for process identity") from exc
        process_handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        try:
            return _windows_start_identity(process_handle)
        finally:
            process_handle.Close()

    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="ascii").split()
        return f"proc-start:{fields[21]}"
    except (OSError, IndexError):
        try:
            return f"psutil-start:{psutil.Process(pid).create_time():.9f}"
        except psutil.NoSuchProcess:
            return f"pid:{pid}:identity-unavailable"
        except (OSError, psutil.AccessDenied) as exc:
            raise ProcessExecutionError("process identity cannot be inspected") from exc


def is_same_process_alive(pid: int, start_identity: str) -> bool:
    """Return true only for the same still-running OS process identity."""

    if pid <= 0 or not start_identity:
        return False
    if os.name == "nt":
        try:
            import pywintypes
            import win32api
            import win32con
            import win32process
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProcessExecutionError("pywin32 is required for process inspection") from exc
        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION | win32con.SYNCHRONIZE,
                False,
                pid,
            )
        except pywintypes.error as exc:
            if getattr(exc, "winerror", None) == 87:
                return False
            raise ProcessExecutionError("process identity cannot be inspected") from exc
        try:
            if win32process.GetExitCodeProcess(handle) != win32con.STILL_ACTIVE:
                return False
            return _windows_start_identity(handle) == start_identity
        finally:
            handle.Close()

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise ProcessExecutionError("process identity cannot be inspected") from exc
    current = _process_start_identity(pid)
    if current.endswith("identity-unavailable"):
        raise ProcessExecutionError("stable process identity is unavailable")
    return current == start_identity


class _RotatingLogWriter:
    def __init__(self, path: Path, *, max_bytes: int, backups: int):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._guard = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("wb")
        self._size = 0

    def _rotate(self) -> None:
        self._stream.close()
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                os.replace(source, self.path.with_name(f"{self.path.name}.{index + 1}"))
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        self._stream = self.path.open("wb")
        self._size = 0

    def write(self, stream_name: str, text: str) -> None:
        payload = f"[{stream_name}] {text}".encode()
        with self._guard:
            while payload:
                if self._size >= self.max_bytes:
                    self._rotate()
                available = self.max_bytes - self._size
                part, payload = payload[:available], payload[available:]
                self._stream.write(part)
                self._stream.flush()
                self._size += len(part)

    def close(self) -> None:
        with self._guard:
            self._stream.close()


class ProcessController:
    """Run one registry-built child while safely draining bounded output."""

    _READ_CHUNK_BYTES = 64 * 1024

    def __init__(
        self,
        *,
        event_line_limit: int = 4_096,
        log_rotate_bytes: int = 8 * 1024 * 1024,
        log_backups: int = 3,
    ):
        if event_line_limit < 64 or log_rotate_bytes < 1_024 or log_backups < 1:
            raise ValueError("process output limits are too small")
        self.event_line_limit = event_line_limit
        self.log_rotate_bytes = log_rotate_bytes
        self.log_backups = log_backups

    @staticmethod
    def _redact(text: str, redactions: tuple[str, ...]) -> str:
        for secret in redactions:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def _drain(
        self,
        pipe,
        *,
        stream_name: Literal["stdout", "stderr"],
        log: _RotatingLogWriter,
        redactions: tuple[str, ...],
        on_event,
        errors: list[BaseException],
    ) -> None:
        pending = ""
        truncated = False
        callback_failed = False
        redaction_carry = ""
        max_secret_length = max((len(item) for item in redactions if item), default=1)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        def emit(text: str, *, complete_line: bool) -> None:
            nonlocal callback_failed, pending, truncated
            try:
                log.write(stream_name, text)
            except BaseException as exc:
                errors.append(exc)
            event_text = text.rstrip("\r\n") if complete_line else text
            if not truncated:
                remaining = self.event_line_limit - len(pending)
                pending += event_text[:remaining]
                truncated = len(event_text) > remaining
            if complete_line:
                message = pending + (" … [truncated]" if truncated else "")
                if on_event is not None and not callback_failed:
                    try:
                        on_event(ProcessEvent(stream_name, message, truncated))
                    except BaseException as exc:
                        errors.append(exc)
                        callback_failed = True
                pending = ""
                truncated = False

        try:
            while True:
                raw = pipe.readline(self._READ_CHUNK_BYTES)
                if not raw:
                    break
                text = decoder.decode(raw, final=False)
                complete_line = raw.endswith(b"\n")
                combined = self._redact(redaction_carry + text, redactions)
                if complete_line:
                    redaction_carry = ""
                    emit(combined, complete_line=True)
                else:
                    carry_size = max_secret_length - 1
                    if carry_size and len(combined) > carry_size:
                        emit(combined[:-carry_size], complete_line=False)
                        redaction_carry = combined[-carry_size:]
                    elif carry_size:
                        redaction_carry = combined
                    else:
                        emit(combined, complete_line=False)
                        redaction_carry = ""
            final_text = self._redact(
                redaction_carry + decoder.decode(b"", final=True), redactions,
            )
            if final_text:
                emit(final_text, complete_line=False)
            if pending or truncated:
                message = pending + (" … [truncated]" if truncated else "")
                if on_event is not None and not callback_failed:
                    try:
                        on_event(ProcessEvent(stream_name, message, truncated))
                    except BaseException as exc:
                        errors.append(exc)
                        callback_failed = True
        except BaseException as exc:
            errors.append(exc)
        finally:
            pipe.close()

    def run(
        self,
        invocation: JobInvocation,
        *,
        log_dir: str | Path,
        redactions: tuple[str, ...] = (),
        on_event=None,
        on_spawn=None,
    ) -> ProcessResult:
        if invocation.shell is not False:
            raise JobContractError("Studio subprocesses require shell=False")
        log_path = Path(log_dir) / "process.log"
        log = _RotatingLogWriter(
            log_path,
            max_bytes=self.log_rotate_bytes,
            backups=self.log_backups,
        )
        process = None
        readers: list[threading.Thread] = []
        errors: list[BaseException] = []
        try:
            process = subprocess.Popen(
                invocation.argv,
                cwd=invocation.cwd,
                env=invocation.environment,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            readers = [
                threading.Thread(
                    target=self._drain,
                    kwargs={
                        "pipe": pipe,
                        "stream_name": stream_name,
                        "log": log,
                        "redactions": redactions,
                        "on_event": on_event,
                        "errors": errors,
                    },
                    name=f"studio-{stream_name}-reader",
                    daemon=False,
                )
                for stream_name, pipe in (
                    ("stdout", process.stdout),
                    ("stderr", process.stderr),
                )
            ]
            for reader in readers:
                reader.start()
            start_identity = _process_start_identity(
                process.pid,
                handle=getattr(process, "_handle", None),
            )
            if on_spawn is not None:
                on_spawn(process.pid, start_identity)
            exit_code = process.wait()
            for reader in readers:
                reader.join()
            if errors:
                raise ProcessExecutionError(
                    "child output could not be drained safely",
                ) from errors[0]
            return ProcessResult(
                pid=process.pid,
                start_identity=start_identity,
                exit_code=exit_code,
                log_path=log_path,
            )
        finally:
            if process is not None and process.poll() is None:
                process.wait()
            for reader in readers:
                if reader.is_alive():
                    reader.join()
            log.close()


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _require_real_directory(raw_path: str | Path, *, label: str) -> Path:
    path = Path(raw_path).expanduser().absolute()
    if _is_linklike(path):
        raise JobContractError(f"{label} must not be a symlink or junction")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise JobContractError(f"{label} directory is missing") from exc
    if resolved != path or not path.is_dir():
        raise JobContractError(f"{label} must be a real directory")
    return path


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _stable_evidence(path: Path, *, relative: str, kind: str) -> FileEvidence:
    try:
        if _is_linklike(path) or not path.is_file():
            raise JobContractError(f"managed tree contains a link or non-regular file: {relative}")
        before = path.stat()
        digest = sha256_file(path)
        middle = path.stat()
        confirmation = sha256_file(path)
        after = path.stat()
    except JobContractError:
        raise
    except OSError as exc:
        raise JobContractError(f"managed file cannot be read: {relative}") from exc
    if (
        _stat_signature(before) != _stat_signature(middle)
        or _stat_signature(middle) != _stat_signature(after)
        or digest != confirmation
        or _is_linklike(path)
    ):
        raise JobContractError(f"managed file changed while hashing: {relative}")
    if after.st_size <= 0:
        raise JobContractError(f"managed file is empty: {relative}")
    return FileEvidence(
        path=relative,
        kind=kind,
        size=after.st_size,
        sha256=digest,
    )


def _scan_tree(root: Path, *, label: str) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []

    def scan_error(error: OSError) -> None:
        raise JobContractError(f"{label} recursive scan failed") from error

    for directory, directory_names, file_names in os.walk(
        root, followlinks=False, onerror=scan_error,
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = parent / name
            if _is_linklike(candidate):
                raise JobContractError(f"{label} contains a symlink or junction")
        for name in file_names:
            candidate = parent / name
            if not candidate.is_file():
                raise JobContractError(f"{label} contains a non-regular file")
            files.append((candidate.relative_to(root).as_posix(), candidate))
    return sorted(files)


def _evidence_digest(items: tuple[FileEvidence, ...]) -> str:
    payload = canonical_json([asdict(item) for item in items]).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _tree_target_evidence(target_root: Path, *, label: str) -> TargetEvidence:
    target_files = tuple(
        _stable_evidence(path, relative=relative, kind="file")
        for relative, path in _scan_tree(target_root, label=label)
    )
    return TargetEvidence(
        state="tree",
        digest=_evidence_digest(target_files),
        files=target_files,
    )


def build_concurrency_snapshot(project_root: str | Path) -> ConcurrencySnapshot:
    """Hash exactly the ingest command inputs and its formal target."""

    root = _require_real_directory(project_root, label="project root")
    input_root = _require_real_directory(root / "input", label="input")
    inputs: list[FileEvidence] = []
    for relative, path in _scan_tree(input_root, label="input"):
        suffix = PurePosixPath(relative).suffix.lower()
        if suffix in PHOTO_SOURCE_SUFFIXES:
            kind = "photo"
        elif suffix in VIDEO_SOURCE_SUFFIXES:
            kind = "video"
        else:
            continue
        inputs.append(_stable_evidence(path, relative=relative, kind=kind))
    frozen_inputs = tuple(inputs)

    target_root = root / "photos"
    if _is_linklike(target_root):
        raise JobContractError("formal target must not be a symlink or junction")
    if not target_root.exists():
        target = TargetEvidence(state="absent", digest=None, files=())
    else:
        if not target_root.is_dir() or target_root.resolve(strict=True) != target_root.absolute():
            raise JobContractError("formal target must be a real directory")
        target = _tree_target_evidence(target_root, label="formal target")
    return ConcurrencySnapshot(
        inputs=frozen_inputs,
        input_digest=_evidence_digest(frozen_inputs),
        target=target,
    )


def _minimal_environment(source: Mapping[str, str]) -> dict[str, str]:
    casefolded = {key.casefold(): value for key, value in source.items()}
    environment = {
        key: casefolded[key.casefold()]
        for key in _ENVIRONMENT_ALLOWLIST
        if key.casefold() in casefolded
    }
    environment.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return environment


class CommandRegistry:
    """Authoritative B1 registry containing only the fixed ingest command."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        base_environment: Mapping[str, str] | None = None,
    ):
        self.project_root = _require_real_directory(project_root, label="project root")
        self.base_environment = dict(os.environ if base_environment is None else base_environment)

    def parse(self, command: str, parameters: Mapping[str, object]) -> IngestParams:
        if command != "ingest":
            raise JobContractError(f"unknown command: {command}")
        return IngestParams.model_validate(parameters)

    def build_invocation(self, run_id: str, parameters: IngestParams) -> JobInvocation:
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise JobContractError("run ID is not safe for a workspace path")
        input_dir = self.project_root / "input"
        stage_dir = self.project_root / ".nantai-studio/work" / run_id / "photos"
        target_dir = self.project_root / "photos"
        argv = (
            sys.executable,
            "-m",
            "pipeline.ingest",
            "--input",
            str(input_dir),
            "--output",
            str(stage_dir),
            "--fps",
            str(parameters.fps),
            "--max-frames",
            str(parameters.max_frames),
            "--blur-threshold",
            str(parameters.blur_threshold),
            "--max-long-edge",
            str(parameters.max_long_edge),
        )
        return JobInvocation(
            argv=argv,
            cwd=self.project_root,
            environment={
                **_minimal_environment(self.base_environment),
                # Controlled package root; never inherited from the request.
                "PYTHONPATH": str(Path(__file__).parents[1]),
            },
            input_dir=input_dir,
            stage_dir=stage_dir,
            target_dir=target_dir,
        )

    @staticmethod
    def verify(invocation: JobInvocation):
        return verify_ingest_artifact(
            invocation.stage_dir,
            input_dir=invocation.input_dir,
        )


class WindowsNtfsDurabilityBackend:
    """Write-through directory publication supported by B1 on local NTFS."""

    def __init__(self, project_root: str | Path):
        self.project_root = _require_real_directory(project_root, label="project root")

    def _prerequisites(self) -> DurabilityReadiness:
        if os.name != "nt":
            return DurabilityReadiness(
                False,
                "B1 write mode is limited to tested Windows/NTFS durability.",
                None,
            )
        try:
            version = importlib.metadata.version("pywin32")
            major = int(version.split(".", 1)[0])
            import win32api
            import win32file
        except (ImportError, importlib.metadata.PackageNotFoundError, ValueError) as exc:
            return DurabilityReadiness(False, f"pywin32 311+ is unavailable: {exc}", None)
        if major < 311:
            return DurabilityReadiness(False, "pywin32 311+ is required.", None)
        required = (
            "CreateFile", "FlushFileBuffers", "MoveFileEx", "MOVEFILE_WRITE_THROUGH",
        )
        if any(not hasattr(win32file, name) for name in required):
            return DurabilityReadiness(
                False,
                "required Win32 durability symbols are missing.",
                None,
            )
        try:
            filesystem = win32api.GetVolumeInformation(str(self.project_root.anchor))[4]
        except OSError as exc:
            return DurabilityReadiness(False, f"filesystem cannot be inspected: {exc}", None)
        if filesystem.upper() != "NTFS":
            return DurabilityReadiness(
                False,
                "write mode requires a local NTFS volume.",
                filesystem,
            )
        return DurabilityReadiness(True, "Windows/NTFS durability is ready.", filesystem)

    @staticmethod
    def flush_file(path: Path) -> None:
        import win32file

        handle = win32file.CreateFile(
            str(path),
            win32file.GENERIC_WRITE,
            win32file.FILE_SHARE_READ,
            None,
            win32file.OPEN_EXISTING,
            win32file.FILE_ATTRIBUTE_NORMAL,
            None,
        )
        try:
            win32file.FlushFileBuffers(handle)
        finally:
            handle.Close()

    @staticmethod
    def flush_directory(path: Path) -> None:
        import win32file

        handle = win32file.CreateFile(
            str(path),
            win32file.GENERIC_WRITE,
            (
                win32file.FILE_SHARE_READ
                | win32file.FILE_SHARE_WRITE
                | win32file.FILE_SHARE_DELETE
            ),
            None,
            win32file.OPEN_EXISTING,
            win32file.FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        try:
            win32file.FlushFileBuffers(handle)
        finally:
            handle.Close()

    @staticmethod
    def move(source: Path, destination: Path) -> None:
        import win32file

        if destination.exists() or _is_linklike(destination):
            raise JobContractError(f"durable move destination already exists: {destination.name}")
        win32file.MoveFileEx(
            str(source),
            str(destination),
            win32file.MOVEFILE_WRITE_THROUGH,
        )

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path)
        self.flush_directory(path.parent)

    def self_test(self) -> DurabilityReadiness:
        readiness = self._prerequisites()
        if not readiness.ready:
            return readiness
        test_root = (
            self.project_root / ".nantai-studio"
            / f"durability-selftest-{uuid.uuid4().hex}"
        )
        source = test_root / "source"
        target = test_root / "target"
        backup = test_root / "backup"
        try:
            source.mkdir(parents=True)
            payload = source / "probe.bin"
            payload.write_bytes(b"nantai-durability-probe")
            self.flush_file(payload)
            self.flush_directory(source)
            self.move(source, target)
            self.flush_directory(test_root)
            self.move(target, backup)
            self.flush_directory(test_root)
            if (backup / "probe.bin").read_bytes() != b"nantai-durability-probe":
                raise OSError("write-through probe bytes changed")
            self.remove_tree(backup)
            test_root.rmdir()
            self.flush_directory(test_root.parent)
        except (OSError, JobContractError) as exc:
            shutil.rmtree(test_root, ignore_errors=True)
            return DurabilityReadiness(
                False,
                f"Windows/NTFS durability self-test failed: {exc}",
                readiness.filesystem,
            )
        return readiness


def _path_exists(path: Path) -> bool:
    return path.exists() or _is_linklike(path)


def _require_fixed_managed_path(
    root: Path,
    path: Path,
    expected: Path,
    *,
    state: Literal["absent", "directory", "either"],
    label: str,
) -> None:
    root = root.absolute()
    path = path.absolute()
    expected = expected.absolute()
    if path != expected or not path.is_relative_to(root) or path == root:
        raise JobContractError(f"{label} path is not the fixed managed path")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if _is_linklike(current):
            raise JobContractError(f"{label} path contains a symlink or junction")
    exists = _path_exists(path)
    if state == "absent" and exists:
        raise JobContractError(f"{label} path must be absent")
    if state == "directory" and (not exists or not path.is_dir()):
        raise JobContractError(f"{label} path must be a directory")
    if exists and not path.is_dir():
        raise JobContractError(f"{label} path has an invalid type")
    if exists:
        _scan_tree(path, label=label)


def _writer_lock_is_held(project_root: Path) -> bool:
    domain = (project_root / ".nantai-studio").absolute()
    with _LOCK_STATE_GUARD:
        locks = _HELD_LOCKS.get(domain, [])
        return bool(locks and locks[0].role == "writer")


class ArtifactPromoter:
    """Journaled single-target publisher and crash recovery coordinator."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        ledger,
        durability: WindowsNtfsDurabilityBackend,
        fault_injector=None,
    ):
        self.project_root = _require_real_directory(project_root, label="project root")
        self.ledger = ledger
        self.durability = durability
        self.fault_injector = fault_injector or (lambda _point: None)

    def _fault(self, point: str) -> None:
        self.fault_injector(point)

    def _paths(self, publication_id: str, run_id: str):
        if _RUN_ID_RE.fullmatch(run_id) is None or not re.fullmatch(
            r"publication-[A-Za-z0-9][A-Za-z0-9_-]{0,127}", publication_id,
        ):
            raise JobContractError("publication or run ID is not path safe")
        return (
            self.project_root / ".nantai-studio/work" / run_id / "photos",
            self.project_root / "photos",
            self.project_root / ".nantai-studio/backups" / publication_id / "photos",
        )

    def _gate(
        self,
        stage: Path,
        target: Path,
        backup: Path,
        *,
        stage_state: Literal["absent", "directory", "either"],
        target_state: Literal["absent", "directory", "either"],
        backup_state: Literal["absent", "directory", "either"],
    ) -> None:
        _require_fixed_managed_path(
            self.project_root, stage, stage,
            state=stage_state, label="publication stage",
        )
        _require_fixed_managed_path(
            self.project_root, target, self.project_root / "photos",
            state=target_state, label="formal target",
        )
        _require_fixed_managed_path(
            self.project_root, backup, backup,
            state=backup_state, label="publication backup",
        )

    def _step(
        self,
        *,
        publication_id: str,
        run_id: str,
        owner: str,
        lease_generation: int,
        step: str,
    ) -> None:
        self.ledger.record_publication_step(
            publication_id=publication_id,
            ordinal=0,
            step=step,
            run_id=run_id,
            owner=owner,
            lease_generation=lease_generation,
        )

    def _flush_move_parents(self, source: Path, destination: Path) -> None:
        self.durability.flush_directory(source.parent)
        if destination.parent != source.parent:
            self.durability.flush_directory(destination.parent)

    @staticmethod
    def _verify_committed_tree(publication, target: Path) -> TargetEvidence:
        manifest = publication.manifest
        if (
            set(manifest) != {"schema_version", "artifact_id", "tree_digest", "files"}
            or manifest.get("schema_version") != 1
            or not isinstance(manifest.get("artifact_id"), str)
            or not isinstance(manifest.get("tree_digest"), str)
            or not isinstance(manifest.get("files"), list)
        ):
            raise JobContractError("publication manifest is invalid")
        actual = _tree_target_evidence(target, label="committed formal target")
        if (
            actual.digest != manifest["tree_digest"]
            or [asdict(item) for item in actual.files] != manifest["files"]
        ):
            raise JobContractError(
                "committed formal target does not match its publication journal",
            )
        return actual

    def _flush_staged_tree(
        self,
        stage: Path,
        expected: TargetEvidence,
    ) -> None:
        """Make every verified staged byte durable before any journal commit."""

        for item in expected.files:
            path = stage / PurePosixPath(item.path)
            current = _stable_evidence(
                path, relative=item.path, kind="file",
            )
            if current != item:
                raise ConcurrentChangeError("staged bytes changed before durability flush")
            self.durability.flush_file(path)
        directories = {stage}
        for item in expected.files:
            parent = (stage / PurePosixPath(item.path)).parent
            while parent != stage:
                directories.add(parent)
                parent = parent.parent
        for directory in sorted(
            directories,
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            self.durability.flush_directory(directory)
        self.durability.flush_directory(stage.parent)
        if _tree_target_evidence(stage, label="flushed publication stage") != expected:
            raise ConcurrentChangeError("staged bytes changed during durability flush")

    def publish(
        self,
        *,
        publication_id: str,
        run_id: str,
        owner: str,
        lease_generation: int,
        expected_snapshot: ConcurrencySnapshot,
        invocation: JobInvocation,
        occurred_utc: datetime,
    ) -> PublicationResult:
        if not _writer_lock_is_held(self.project_root):
            raise LockOrderError("publication requires the held writer lock")
        if invocation.stage_dir != self._paths(publication_id, run_id)[0]:
            raise JobContractError("invocation stage does not match the fixed run workspace")
        try:
            manifest = verify_ingest_artifact(
                invocation.stage_dir,
                input_dir=invocation.input_dir,
            )
        except Exception as exc:
            raise JobContractError("staged ingest artifact is invalid") from exc
        publish_lock = ProjectFileLock(
            self.project_root / ".nantai-studio/publish.lock",
            role="publish",
        )
        with publish_lock:
            if build_concurrency_snapshot(self.project_root) != expected_snapshot:
                raise ConcurrentChangeError("input or formal target changed before publication")
            stage, target, backup = self._paths(publication_id, run_id)
            had_old = expected_snapshot.target.state == "tree"
            backup_root = self.project_root / ".nantai-studio/backups"
            _require_fixed_managed_path(
                self.project_root,
                backup_root,
                self.project_root / ".nantai-studio/backups",
                state="directory",
                label="publication backup root",
            )
            _require_fixed_managed_path(
                self.project_root,
                backup.parent,
                self.project_root / ".nantai-studio/backups" / publication_id,
                state="absent",
                label="publication backup transaction",
            )
            backup.parent.mkdir(parents=False, exist_ok=False)
            self._gate(
                stage, target, backup,
                stage_state="directory",
                target_state="directory" if had_old else "absent",
                backup_state="absent",
            )
            stage_evidence = _tree_target_evidence(stage, label="publication stage")
            self._flush_staged_tree(stage, stage_evidence)
            publication_manifest = {
                "schema_version": 1,
                "artifact_id": manifest.session_id,
                "tree_digest": stage_evidence.digest,
                "files": [asdict(item) for item in stage_evidence.files],
            }
            self.ledger.prepare_publication(
                publication_id=publication_id,
                run_id=run_id,
                manifest=publication_manifest,
                targets=[{
                    "target": "photos",
                    "stage": f".nantai-studio/work/{run_id}/photos",
                    "backup": f".nantai-studio/backups/{publication_id}/photos",
                    "had_old": had_old,
                }],
                owner=owner,
                lease_generation=lease_generation,
                created_utc=occurred_utc,
            )

            self._fault("before_target_backup_intent")
            self._gate(
                stage, target, backup,
                stage_state="directory",
                target_state="directory" if had_old else "absent",
                backup_state="absent",
            )
            self._step(
                publication_id=publication_id, run_id=run_id, owner=owner,
                lease_generation=lease_generation, step="target_backup_intent",
            )
            self._fault("after_target_backup_intent")

            self._fault("before_target_backup_move")
            self._gate(
                stage, target, backup,
                stage_state="directory",
                target_state="directory" if had_old else "absent",
                backup_state="absent",
            )
            if had_old:
                self.durability.move(target, backup)
            self._fault("after_target_backup_move")
            self._fault("before_target_backup_flush")
            if had_old:
                self._flush_move_parents(target, backup)
                if _tree_target_evidence(backup, label="publication backup") != (
                    expected_snapshot.target
                ):
                    raise ConcurrentChangeError("formal target changed during backup")
            else:
                self.durability.flush_directory(backup.parent)
            self._fault("after_target_backup_flush")
            self._step(
                publication_id=publication_id, run_id=run_id, owner=owner,
                lease_generation=lease_generation, step="target_backup_done",
            )
            self._fault("after_target_backup_done")

            self._step(
                publication_id=publication_id, run_id=run_id, owner=owner,
                lease_generation=lease_generation, step="stage_target_intent",
            )
            self._fault("after_stage_target_intent")
            self._fault("before_stage_target_move")
            self._gate(
                stage, target, backup,
                stage_state="directory", target_state="absent",
                backup_state="directory" if had_old else "absent",
            )
            self.durability.move(stage, target)
            self._fault("after_stage_target_move")
            self._fault("before_stage_target_flush")
            self._flush_move_parents(stage, target)
            self._fault("after_stage_target_flush")
            self._step(
                publication_id=publication_id, run_id=run_id, owner=owner,
                lease_generation=lease_generation, step="stage_target_done",
            )
            self._fault("after_stage_target_done")

            self._gate(
                stage, target, backup,
                stage_state="absent", target_state="directory",
                backup_state="directory" if had_old else "absent",
            )
            verified = verify_ingest_artifact(target, input_dir=invocation.input_dir)
            if verified.session_id != manifest.session_id:
                raise JobContractError("formal artifact identity changed after publication")
            if build_concurrency_snapshot(self.project_root).input_digest != (
                expected_snapshot.input_digest
            ):
                raise ConcurrentChangeError("input changed during publication")
            self._fault("before_commit")
            self.ledger.commit_publication_success(
                publication_id=publication_id,
                run_id=run_id,
                artifact_ids=[manifest.session_id],
                owner=owner,
                lease_generation=lease_generation,
                message="Verified ingest artifact published.",
                occurred_utc=occurred_utc,
            )
            self._fault("after_commit")
            if had_old and backup.exists():
                self.durability.remove_tree(backup)
            if backup.parent.exists():
                backup.parent.rmdir()
                self.durability.flush_directory(backup.parent.parent)
            return PublicationResult(publication_id, manifest.session_id)

    def recover_all(
        self,
        *,
        owner: str,
        lease_generation: int,
        occurred_utc: datetime,
    ) -> None:
        if not _writer_lock_is_held(self.project_root):
            raise LockOrderError("publication recovery requires the held writer lock")
        with ProjectFileLock(
            self.project_root / ".nantai-studio/publish.lock",
            role="publish",
        ):
            publications = self.ledger.list_publications()
            latest_committed: dict[str, object] = {}
            for publication in publications:
                if publication.status != "committed":
                    continue
                for target_record in publication.targets:
                    current = latest_committed.get(target_record.target_path)
                    if (
                        current is None
                        or publication.journal_order > current.journal_order
                    ):
                        latest_committed[target_record.target_path] = publication
            ordered = sorted(
                publications,
                key=lambda publication: publication.status == "committed",
            )
            for publication in ordered:
                if publication.status == "rolled_back":
                    continue
                if len(publication.targets) != 1:
                    raise JobContractError("B1 recovery requires exactly one publication target")
                journal = publication.targets[0]
                stage, target, backup = self._paths(publication.id, publication.run_id)
                expected_paths = (
                    f".nantai-studio/work/{publication.run_id}/photos",
                    "photos",
                    f".nantai-studio/backups/{publication.id}/photos",
                )
                if (
                    journal.stage_path,
                    journal.target_path,
                    journal.backup_path,
                ) != expected_paths:
                    raise JobContractError("publication journal contains an unexpected path")
                if publication.status == "committed":
                    self._gate(
                        stage, target, backup,
                        stage_state="absent", target_state="directory",
                        backup_state="either",
                    )
                    if latest_committed.get(journal.target_path) is publication:
                        self._verify_committed_tree(publication, target)
                    if backup.exists():
                        self.durability.remove_tree(backup)
                    if backup.parent.exists():
                        backup.parent.rmdir()
                        self.durability.flush_directory(backup.parent.parent)
                    continue

                self._gate(
                    stage, target, backup,
                    stage_state="either", target_state="either", backup_state="either",
                )
                if journal.stage_target_intent and not stage.exists():
                    if not target.is_dir():
                        raise JobContractError("recovery cannot locate the moved staged target")
                    self.durability.move(target, stage)
                    self._flush_move_parents(target, stage)
                if journal.had_old:
                    if backup.exists():
                        if target.exists():
                            raise JobContractError("rollback target is unexpectedly occupied")
                        self.durability.move(backup, target)
                        self._flush_move_parents(backup, target)
                    elif not target.is_dir():
                        raise JobContractError("recovery cannot locate the previous formal target")
                elif target.exists():
                    raise JobContractError("rollback found an unexpected formal target")

                run = self.ledger.get_run(publication.run_id)
                expected = ConcurrencySnapshot.from_dict(run.snapshot)
                if expected.target.state == "tree":
                    restored = _tree_target_evidence(target, label="restored formal target")
                    if restored != expected.target:
                        raise JobContractError("restored formal target does not match its snapshot")
                elif target.exists():
                    raise JobContractError("formal target should be absent after rollback")
                self.ledger.mark_publication_rolled_back(
                    publication_id=publication.id,
                    run_id=publication.run_id,
                    owner=owner,
                    lease_generation=lease_generation,
                    message="Uncommitted publication was rolled back.",
                    occurred_utc=occurred_utc,
                )
                if backup.parent.exists():
                    backup.parent.rmdir()
                    self.durability.flush_directory(backup.parent.parent)


class JobService:
    """Compose one fenced, ingest-only background worker per project."""

    COMMAND_SCHEMA_VERSION = 1
    LEASE_DURATION = timedelta(minutes=5)

    def __init__(
        self,
        project_root: str | Path,
        *,
        ledger: StudioLedger | None = None,
        registry: CommandRegistry | None = None,
        process_controller: ProcessController | None = None,
        durability: WindowsNtfsDurabilityBackend | None = None,
        heartbeat_interval: float = 30.0,
    ):
        self.project_root = _require_real_directory(
            project_root, label="project root",
        )
        state_root = self.project_root / ".nantai-studio"
        self.ledger = ledger or StudioLedger(state_root / "studio.db")
        self.registry = registry or CommandRegistry(self.project_root)
        self.process_controller = process_controller or ProcessController()
        self.durability = durability or WindowsNtfsDurabilityBackend(
            self.project_root,
        )
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat interval must be positive")
        self.heartbeat_interval = heartbeat_interval
        self._threads: dict[str, threading.Thread] = {}
        self._threads_guard = threading.Lock()

    def initialize(self) -> None:
        """Initialize durable state without enabling HTTP write capability."""

        state_root = self.project_root / ".nantai-studio"
        if _path_exists(state_root):
            _require_real_directory(state_root, label="Studio state root")
        else:
            state_root.mkdir(exist_ok=False)
            _require_real_directory(state_root, label="Studio state root")
        expected_database = state_root / "studio.db"
        if self.ledger.database_path.absolute() != expected_database.absolute():
            raise JobContractError("Studio ledger must use the fixed managed path")
        if _path_exists(expected_database) and _is_linklike(expected_database):
            raise JobContractError("Studio ledger must not be a link")
        self.ledger.initialize()
        _require_real_directory(state_root, label="Studio state root")
        for relative in ("work", "backups", "logs", "quarantine"):
            path = state_root / relative
            if _path_exists(path) and (not path.is_dir() or _is_linklike(path)):
                raise JobContractError(
                    f"Studio managed path is unsafe: {relative}",
                )
            path.mkdir(exist_ok=True)
            _require_real_directory(path, label=f"Studio managed {relative}")

    def recover_startup(self) -> StartupRecovery:
        """Resolve durable orphan state before HTTP write capability is issued."""

        writer = ProjectFileLock(
            self.project_root / ".nantai-studio/writer.lock",
            role="writer",
        )
        try:
            if _writer_lock_is_held(self.project_root) or not writer.acquire(
                blocking=False,
            ):
                return StartupRecovery(
                    False,
                    "Another process still owns the Studio writer lock.",
                )
        except LockOrderError:
            return StartupRecovery(
                False,
                "Another process still owns the Studio writer lock.",
            )

        transfer = False
        try:
            active = next((
                run for run in self.ledger.list_runs()
                if run.status in {"queued", "running"}
            ), None)
            if (
                active is not None
                and active.phase == "executing"
                and active.child_pid is not None
                and active.child_start_identity
                and is_same_process_alive(
                    active.child_pid, active.child_start_identity,
                )
            ):
                return StartupRecovery(
                    False,
                    "An orphaned ingest child is still alive; Studio is observer-only.",
                    observer_only=True,
                )

            promoter = ArtifactPromoter(
                self.project_root,
                ledger=self.ledger,
                durability=self.durability,
            )
            if active is None:
                if any(
                    publication.status == "prepared"
                    for publication in self.ledger.list_publications()
                ):
                    raise JobContractError(
                        "an uncommitted publication has no active run",
                    )
                promoter.recover_all(
                    owner="startup-recovery",
                    lease_generation=1,
                    occurred_utc=self._now(),
                )
                return StartupRecovery(True, "Startup recovery is complete.")

            recovered = self.ledger.take_over_for_recovery(
                active.id,
                owner=f"recovery-{uuid.uuid4().hex}",
                lease_generation=active.lease_generation + 1,
                lease_expires_utc=self._now() + self.LEASE_DURATION,
                occurred_utc=self._now(),
            )
            if recovered.status == "queued":
                try:
                    parameters = self.registry.parse(
                        recovered.command, recovered.parameters,
                    )
                    expected = ConcurrencySnapshot.from_dict(recovered.snapshot)
                    if build_concurrency_snapshot(self.project_root) != expected:
                        raise ConcurrentChangeError("queued snapshot is stale")
                except Exception:
                    self.ledger.transition_run(
                        recovered.id,
                        status="failed",
                        phase=None,
                        owner=recovered.owner,
                        lease_generation=recovered.lease_generation,
                        error_code="stale_job",
                        message="Queued job no longer matches its fixed command snapshot.",
                        occurred_utc=self._now(),
                        recovery=True,
                    )
                    return StartupRecovery(True, "A stale queued job was retired.")
                worker = threading.Thread(
                    target=self._run_worker,
                    args=(recovered, parameters, expected, writer),
                    name=f"studio-recover-{recovered.id}",
                    daemon=False,
                )
                with self._threads_guard:
                    self._threads[recovered.id] = worker
                worker.start()
                transfer = True
                return StartupRecovery(True, "A queued job resumed under recovery fencing.")

            if recovered.phase == "executing":
                self._fail_run(
                    recovered.id,
                    code="interrupted",
                    message="The original ingest process exited before startup recovery.",
                )
                self._quarantine_workspace(recovered.id)
                return StartupRecovery(True, "An interrupted ingest was quarantined.")

            if recovered.phase == "validating":
                worker = threading.Thread(
                    target=self._resume_validating,
                    args=(recovered, writer),
                    name=f"studio-recover-{recovered.id}",
                    daemon=False,
                )
                with self._threads_guard:
                    self._threads[recovered.id] = worker
                worker.start()
                transfer = True
                return StartupRecovery(
                    True,
                    "A validated staging recovery is running.",
                )

            if recovered.phase == "publishing":
                promoter.recover_all(
                    owner=recovered.owner,
                    lease_generation=recovered.lease_generation,
                    occurred_utc=self._now(),
                )
                return StartupRecovery(True, "Publication recovery is complete.")
            raise JobContractError("active run has an unsupported recovery phase")
        except Exception as exc:
            return StartupRecovery(
                False,
                f"Startup recovery failed safely: {self._safe_error_message(str(exc))}",
            )
        finally:
            if not transfer and writer.acquired:
                writer.release()

    def _quarantine_workspace(self, run_id: str) -> None:
        source = self.project_root / ".nantai-studio/work" / run_id
        if not source.exists():
            return
        destination = (
            self.project_root / ".nantai-studio/quarantine"
            / f"{run_id}-{uuid.uuid4().hex}"
        )
        source.rename(destination)

    def _resume_validating(self, run, writer: ProjectFileLock) -> None:
        heartbeat_stop, heartbeat_thread, heartbeat_errors = self._start_heartbeat(run)
        promoter = ArtifactPromoter(
            self.project_root,
            ledger=self.ledger,
            durability=self.durability,
        )
        try:
            parameters = self.registry.parse(run.command, run.parameters)
            invocation = self.registry.build_invocation(run.id, parameters)
            expected = ConcurrencySnapshot.from_dict(run.snapshot)
            self.registry.verify(invocation)
            if heartbeat_errors:
                raise ProcessExecutionError("worker lease heartbeat failed")
            if build_concurrency_snapshot(self.project_root) != expected:
                raise ConcurrentChangeError(
                    "input or formal target changed before validation recovery",
                )
            self.ledger.transition_run(
                run.id,
                status="running",
                phase="publishing",
                owner=run.owner,
                lease_generation=run.lease_generation,
                message="Recovered staging output is being published.",
                progress=0.9,
                occurred_utc=self._now(),
            )
            promoter.publish(
                publication_id=f"publication-{uuid.uuid4().hex}",
                run_id=run.id,
                owner=run.owner,
                lease_generation=run.lease_generation,
                expected_snapshot=expected,
                invocation=invocation,
                occurred_utc=self._now(),
            )
        except ConcurrentChangeError as exc:
            self._fail_run(run.id, code="concurrent_change", message=str(exc))
        except Exception as exc:
            self._resolve_worker_failure(
                run,
                promoter,
                exc,
                default_code="validation_failed",
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
            if writer.acquired:
                writer.release()
            with self._threads_guard:
                self._threads.pop(run.id, None)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _safe_error_message(message: str) -> str:
        compact = " ".join(str(message).split())
        return compact[:512] or "The local job failed safely."

    def _start_heartbeat(self, run):
        stop = threading.Event()
        errors: list[BaseException] = []

        def heartbeat() -> None:
            while not stop.wait(self.heartbeat_interval):
                try:
                    now = self._now()
                    self.ledger.renew_lease(
                        run.id,
                        owner=run.owner,
                        lease_generation=run.lease_generation,
                        lease_expires_utc=now + self.LEASE_DURATION,
                        occurred_utc=now,
                    )
                except BaseException as exc:
                    errors.append(exc)
                    stop.set()

        thread = threading.Thread(
            target=heartbeat,
            name=f"studio-heartbeat-{run.id}",
            daemon=False,
        )
        thread.start()
        return stop, thread, errors

    def submit(
        self,
        *,
        command: str,
        parameters: Mapping[str, object],
        request_id: str,
    ) -> CreateRunResult:
        """Idempotently queue one fixed ingest command and transfer its lock."""

        if not isinstance(request_id, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", request_id,
        ) is None:
            raise JobContractError("request ID is invalid")
        parsed = self.registry.parse(command, parameters)
        canonical_parameters = parsed.model_dump(mode="json")
        duplicate = self.ledger.find_request(
            request_id,
            command=command,
            command_schema_version=self.COMMAND_SCHEMA_VERSION,
            parameters=canonical_parameters,
        )
        if duplicate is not None:
            return CreateRunResult(duplicate, created=False)

        writer = ProjectFileLock(
            self.project_root / ".nantai-studio/writer.lock",
            role="writer",
        )
        if _writer_lock_is_held(self.project_root):
            raise WriterBusyError("another Studio writer is active")
        if not writer.acquire(blocking=False):
            raise WriterBusyError("another Studio writer is active")

        work_root: Path | None = None
        try:
            duplicate = self.ledger.find_request(
                request_id,
                command=command,
                command_schema_version=self.COMMAND_SCHEMA_VERSION,
                parameters=canonical_parameters,
            )
            if duplicate is not None:
                writer.release()
                return CreateRunResult(duplicate, created=False)
            snapshot = build_concurrency_snapshot(self.project_root)
            run_id = f"run-{uuid.uuid4().hex}"
            owner = f"worker-{uuid.uuid4().hex}"
            work_root = self.project_root / ".nantai-studio/work" / run_id
            work_root.mkdir(parents=False, exist_ok=False)
            now = self._now()
            created = self.ledger.create_run(
                run_id=run_id,
                request_id=request_id,
                command=command,
                command_schema_version=self.COMMAND_SCHEMA_VERSION,
                parameters=canonical_parameters,
                snapshot=snapshot.as_dict(),
                owner=owner,
                lease_generation=1,
                lease_expires_utc=now + self.LEASE_DURATION,
                staging_path=f".nantai-studio/work/{run_id}/photos",
                created_utc=now,
            )
            if not created.created:
                shutil.rmtree(work_root)
                writer.release()
                return created
            worker = threading.Thread(
                target=self._run_worker,
                args=(created.run, parsed, snapshot, writer),
                name=f"studio-ingest-{run_id}",
                daemon=False,
            )
            with self._threads_guard:
                self._threads[run_id] = worker
            worker.start()
            return created
        except BaseException:
            if work_root is not None and work_root.exists():
                shutil.rmtree(work_root, ignore_errors=True)
            if writer.acquired:
                writer.release()
            raise

    def _fail_run(self, run_id: str, *, code: str, message: str) -> None:
        run = self.ledger.get_run(run_id)
        if run.status != "running":
            return
        self.ledger.transition_run(
            run_id,
            status="failed",
            phase=None,
            owner=run.owner,
            lease_generation=run.lease_generation,
            error_code=code,
            message=self._safe_error_message(message),
            occurred_utc=self._now(),
        )

    def _resolve_worker_failure(
        self,
        run,
        promoter: ArtifactPromoter,
        error: BaseException,
        *,
        default_code: str,
    ) -> None:
        current = self.ledger.get_run(run.id)
        if current.status != "running":
            return
        prepared = (
            current.phase == "publishing"
            and any(
                publication.run_id == run.id
                and publication.status == "prepared"
                for publication in self.ledger.list_publications()
            )
        )
        if prepared:
            try:
                promoter.recover_all(
                    owner=run.owner,
                    lease_generation=run.lease_generation,
                    occurred_utc=self._now(),
                )
            except Exception:
                # Preserve active fenced state for the next startup recovery.
                return
            return
        self._fail_run(
            run.id,
            code="publish_failed" if current.phase == "publishing" else default_code,
            message=str(error),
        )

    def _run_worker(
        self,
        run,
        parameters: IngestParams,
        snapshot: ConcurrencySnapshot,
        writer: ProjectFileLock,
    ) -> None:
        invocation = self.registry.build_invocation(run.id, parameters)
        promoter = ArtifactPromoter(
            self.project_root,
            ledger=self.ledger,
            durability=self.durability,
        )
        heartbeat_stop = None
        heartbeat_thread = None
        heartbeat_errors: list[BaseException] = []
        try:
            self.ledger.transition_run(
                run.id,
                status="running",
                phase="executing",
                owner=run.owner,
                lease_generation=run.lease_generation,
                message="Ingest process started.",
                progress=0.05,
                occurred_utc=self._now(),
            )
            heartbeat_stop, heartbeat_thread, heartbeat_errors = self._start_heartbeat(run)

            def on_spawn(pid: int, start_identity: str) -> None:
                self.ledger.record_child_process(
                    run.id,
                    pid=pid,
                    start_identity=start_identity,
                    owner=run.owner,
                    lease_generation=run.lease_generation,
                    occurred_utc=self._now(),
                )

            def on_event(event: ProcessEvent) -> None:
                message = f"[{event.stream}] {event.message}"[:4_096]
                self.ledger.append_worker_event(
                    run.id,
                    owner=run.owner,
                    lease_generation=run.lease_generation,
                    message=message,
                    occurred_utc=self._now(),
                    level="warning" if event.stream == "stderr" else "info",
                )

            result = self.process_controller.run(
                invocation,
                log_dir=invocation.stage_dir.parent / "logs",
                on_event=on_event,
                on_spawn=on_spawn,
            )
            if heartbeat_errors:
                raise ProcessExecutionError("worker lease heartbeat failed")
            if result.exit_code != 0:
                self._fail_run(
                    run.id,
                    code="process_failed",
                    message=f"Ingest exited with exit code {result.exit_code}.",
                )
                return
            self.ledger.transition_run(
                run.id,
                status="running",
                phase="validating",
                owner=run.owner,
                lease_generation=run.lease_generation,
                message="Ingest output is being verified.",
                progress=0.75,
                occurred_utc=self._now(),
            )
            self.registry.verify(invocation)
            if build_concurrency_snapshot(self.project_root) != snapshot:
                raise ConcurrentChangeError(
                    "input or formal target changed while ingest was running",
                )
            self.ledger.transition_run(
                run.id,
                status="running",
                phase="publishing",
                owner=run.owner,
                lease_generation=run.lease_generation,
                message="Verified ingest output is being published.",
                progress=0.9,
                occurred_utc=self._now(),
            )
            promoter.publish(
                publication_id=f"publication-{uuid.uuid4().hex}",
                run_id=run.id,
                owner=run.owner,
                lease_generation=run.lease_generation,
                expected_snapshot=snapshot,
                invocation=invocation,
                occurred_utc=self._now(),
            )
        except ConcurrentChangeError as exc:
            self._fail_run(
                run.id, code="concurrent_change", message=str(exc),
            )
        except ProcessExecutionError as exc:
            self._fail_run(
                run.id, code="process_observation_failed", message=str(exc),
            )
        except Exception as exc:
            self._resolve_worker_failure(
                run,
                promoter,
                exc,
                default_code="validation_failed",
            )
        finally:
            if heartbeat_stop is not None:
                heartbeat_stop.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join()
            if writer.acquired:
                writer.release()
            with self._threads_guard:
                self._threads.pop(run.id, None)

    def wait(self, run_id: str, *, timeout: float = 30) -> object:
        """Wait for a locally owned worker or poll durable terminal state."""

        deadline = time.monotonic() + timeout
        with self._threads_guard:
            worker = self._threads.get(run_id)
        if worker is not None:
            worker.join(max(0, deadline - time.monotonic()))
        while True:
            run = self.ledger.get_run(run_id)
            if run.status in {"succeeded", "failed", "canceled"}:
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Studio run did not finish: {run_id}")
            time.sleep(0.02)

    def shutdown(self, *, timeout: float = 30) -> None:
        """Wait for all non-daemon workers; B1 intentionally has no cancel."""

        deadline = time.monotonic() + timeout
        while True:
            with self._threads_guard:
                workers = list(self._threads.values())
            if not workers:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Studio workers did not finish before shutdown")
            for worker in workers:
                worker.join(remaining)
