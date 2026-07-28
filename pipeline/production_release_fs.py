"""Linux-only append-only filesystem capabilities for Production releases.

This module deliberately exposes no remove, rename, replace, rollback or
recursive-cleanup operation.  A successful namespace mutation is retained even
when a later operation fails, so the caller can audit the partial publication.
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
STREAM_CHUNK_BYTES = 1024 * 1024


class ProductionReleaseFSError(OSError):
    """Base error for fail-closed Production release filesystem operations."""


class ProductionReleaseMutationUnsupportedError(ProductionReleaseFSError):
    """Raised before mutation on a platform without the Linux contract."""


class ProductionReleaseMutationError(ProductionReleaseFSError):
    """A mutation failed after one or more names may have been retained."""

    def __init__(
        self,
        message: str,
        *,
        published: Iterable[str] = (),
        retained: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.published = tuple(published)
        self.retained = tuple(retained)


def require_linux_mutation_support() -> None:
    """Reject unsupported platforms before any filesystem mutation."""

    if sys.platform != "linux":
        raise ProductionReleaseMutationUnsupportedError(
            "Production release mutation requires a private Linux builder"
        )
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise ProductionReleaseMutationUnsupportedError(
            "Production release mutation requires Linux dirfd support"
        )


def _component(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError("expected one safe single path component")
    return name


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


class BoundFile:
    """One newly created regular file held by descriptor until completion."""

    def __init__(
        self,
        descriptor: int,
        *,
        name: str,
        display_path: Path,
    ) -> None:
        self.name = name
        self.path = display_path
        self._descriptor = descriptor
        self._stream: BinaryIO | None = None
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise OSError("created object is not a regular file")
            self._stream = os.fdopen(descriptor, "w+b", buffering=0)
        except Exception as exc:
            if self._stream is None:
                _close_descriptor(descriptor)
            raise ProductionReleaseMutationError(
                f"created Production release file is retained: {name}",
                published=(name,),
                retained=(name,),
            ) from exc

    @property
    def stream(self) -> BinaryIO:
        if self._stream is None or self._stream.closed:
            raise ProductionReleaseFSError("bound file capability is closed")
        return self._stream

    def write_all(self, payload: bytes) -> None:
        view = memoryview(payload)
        while view:
            written = self.stream.write(view)
            if written is None or written <= 0:
                raise ProductionReleaseMutationError(
                    f"Production release file write failed; retained: {self.name}",
                    published=(self.name,),
                    retained=(self.name,),
                )
            view = view[written:]

    def copy_from(
        self,
        source: BinaryIO,
        *,
        expected_bytes: int | None = None,
    ) -> tuple[str, int]:
        digest = hashlib.sha256()
        observed_bytes = 0
        while True:
            chunk = source.read(STREAM_CHUNK_BYTES)
            if not chunk:
                break
            observed_bytes += len(chunk)
            if expected_bytes is not None and observed_bytes > expected_bytes:
                raise ProductionReleaseMutationError(
                    f"Production release source expanded; retained: {self.name}",
                    published=(self.name,),
                    retained=(self.name,),
                )
            digest.update(chunk)
            self.write_all(chunk)
        if expected_bytes is not None and observed_bytes != expected_bytes:
            raise ProductionReleaseMutationError(
                f"Production release source length changed; retained: {self.name}",
                published=(self.name,),
                retained=(self.name,),
            )
        return digest.hexdigest(), observed_bytes

    def finish(self) -> None:
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def digest(self) -> tuple[str, int]:
        """Hash the same held inode; never reopen it by name."""

        self.finish()
        stream = self.stream
        previous = stream.tell()
        stream.seek(0)
        digest = hashlib.sha256()
        observed_bytes = 0
        while True:
            chunk = stream.read(STREAM_CHUNK_BYTES)
            if not chunk:
                break
            observed_bytes += len(chunk)
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if not stat.S_ISREG(after.st_mode) or after.st_size != observed_bytes:
            raise ProductionReleaseMutationError(
                f"Production release file changed; retained: {self.name}",
                published=(self.name,),
                retained=(self.name,),
            )
        stream.seek(previous)
        return digest.hexdigest(), observed_bytes

    def read_bytes(self, *, maximum_bytes: int) -> bytes:
        self.finish()
        stream = self.stream
        previous = stream.tell()
        stream.seek(0)
        payload = stream.read(maximum_bytes + 1)
        stream.seek(previous)
        if len(payload) > maximum_bytes:
            raise ProductionReleaseMutationError(
                f"Production release file exceeds limit; retained: {self.name}",
                published=(self.name,),
                retained=(self.name,),
            )
        return payload

    def close(self) -> None:
        if self._stream is not None and not self._stream.closed:
            self._stream.close()

    def __enter__(self) -> BoundFile:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class BoundDirectory:
    """An existing or newly created directory held by an exact descriptor."""

    def __init__(
        self,
        descriptor: int,
        *,
        display_path: Path,
        relative_name: str = "",
    ) -> None:
        self._descriptor = descriptor
        self.path = display_path
        self.relative_name = relative_name
        try:
            observed = os.fstat(descriptor)
        except OSError:
            _close_descriptor(descriptor)
            raise
        if not stat.S_ISDIR(observed.st_mode):
            _close_descriptor(descriptor)
            raise ProductionReleaseFSError(
                "bound Production release path is not a directory"
            )

    @property
    def descriptor(self) -> int:
        if self._descriptor < 0:
            raise ProductionReleaseFSError(
                "bound directory capability is closed"
            )
        return self._descriptor

    def _relative(self, name: str) -> str:
        return f"{self.relative_name}/{name}".lstrip("/")

    def duplicate(self) -> BoundDirectory:
        """Duplicate this exact directory capability without reopening a name."""

        return BoundDirectory(
            os.dup(self.descriptor),
            display_path=self.path,
            relative_name=self.relative_name,
        )

    def entry_exists(self, name: str) -> bool:
        component = _component(name)
        try:
            os.stat(
                component,
                dir_fd=self.descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return True

    def create_directory(
        self,
        name: str,
        *,
        mode: int = 0o700,
    ) -> BoundDirectory:
        component = _component(name)
        relative = self._relative(component)
        try:
            os.mkdir(component, mode=mode, dir_fd=self.descriptor)
        except FileExistsError:
            raise
        except OSError as exc:
            raise ProductionReleaseFSError(
                f"cannot create Production release directory: {relative}"
            ) from exc
        try:
            descriptor = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=self.descriptor,
            )
        except OSError as exc:
            raise ProductionReleaseMutationError(
                f"created Production release directory is retained: {relative}",
                published=(relative,),
                retained=(relative,),
            ) from exc
        return BoundDirectory(
            descriptor,
            display_path=self.path / component,
            relative_name=relative,
        )

    def create_file(
        self,
        name: str,
        *,
        mode: int = 0o600,
    ) -> BoundFile:
        component = _component(name)
        relative = self._relative(component)
        try:
            descriptor = os.open(
                component,
                _FILE_FLAGS,
                mode,
                dir_fd=self.descriptor,
            )
        except FileExistsError:
            raise
        except OSError as exc:
            raise ProductionReleaseFSError(
                f"cannot create Production release file: {relative}"
            ) from exc
        return BoundFile(
            descriptor,
            name=relative,
            display_path=self.path / component,
        )

    def fsync(self) -> None:
        os.fsync(self.descriptor)

    def close(self) -> None:
        if self._descriptor >= 0:
            descriptor = self._descriptor
            self._descriptor = -1
            _close_descriptor(descriptor)

    def __enter__(self) -> BoundDirectory:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_bound_directory(path: str | Path) -> BoundDirectory:
    """Open an existing absolute directory from its anchor component-by-component."""

    require_linux_mutation_support()
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("bound Production release directory must be absolute")
    parts = candidate.parts
    if not parts:
        raise ValueError("bound Production release directory is empty")
    descriptor = os.open(parts[0], _DIRECTORY_FLAGS)
    current = Path(parts[0])
    try:
        for raw in parts[1:]:
            component = _component(raw)
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            _close_descriptor(descriptor)
            descriptor = next_descriptor
            current /= component
        return BoundDirectory(descriptor, display_path=candidate)
    except Exception:
        _close_descriptor(descriptor)
        raise
