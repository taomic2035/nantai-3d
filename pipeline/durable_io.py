"""Small fail-closed durable publication primitives.

Writers must finish and sync a sibling candidate before calling these
functions.  Windows publication uses ``MoveFileEx(..., WRITE_THROUGH)``;
POSIX publication syncs the containing directory after the namespace change.
An error raised after a POSIX namespace change records ``published=True`` so
callers never confuse "not published" with "published, durability unknown".
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class DurableIOError(OSError):
    """A durable-write primitive could not prove its contract."""

    def __init__(self, message: str, *, published: bool = False):
        super().__init__(message)
        self.published = published


def _paths_are_siblings(source: Path, destination: Path) -> None:
    if source.parent != destination.parent:
        raise DurableIOError(
            "durable publication requires source and destination siblings"
        )


def _destination_must_be_absent(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"durable publication destination already exists: {destination.name}"
        )


def _source_kind(path: Path, *, directory: bool) -> None:
    try:
        current = path.lstat()
    except OSError as exc:
        raise DurableIOError("durable publication source is unavailable") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(current.st_mode) or not expected(current.st_mode):
        kind = "directory" if directory else "regular file"
        raise DurableIOError(
            f"durable publication source must be a real {kind}"
        )


def flush_file(path: str | Path) -> None:
    """Flush an existing candidate through a writable descriptor."""

    with Path(path).open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def flush_directory(path: str | Path) -> None:
    """Flush directory entry buffers to disk.

    POSIX: opens the directory read-only and ``os.fsync``.
    Windows: uses ``win32file.CreateFile`` with
    ``FILE_FLAG_BACKUP_SEMANTICS`` to obtain a directory handle, then
    ``FlushFileBuffers``. If ``pywin32`` is unavailable, raises
    :class:`DurableIOError` so the caller cannot silently lose the
    directory-entry durability guarantee.
    """

    resolved = Path(path)
    if os.name == "nt":
        _flush_directory_windows(resolved)
    else:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(resolved, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _flush_directory_windows(path: Path) -> None:
    try:
        import win32file
    except ImportError as exc:
        raise DurableIOError(
            "directory sync on Windows requires pywin32; install "
            "pywin32 or disable directory sync explicitly"
        ) from exc
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


def atomic_replace(source: str | Path, destination: str | Path) -> None:
    """Replace a sibling destination with one durable namespace operation."""

    source_path = Path(source).absolute()
    destination_path = Path(destination).absolute()
    _paths_are_siblings(source_path, destination_path)
    _source_kind(source_path, directory=False)
    if os.name == "nt":
        _move_windows(source_path, destination_path, replace=True)
        return
    os.replace(source_path, destination_path)
    try:
        flush_directory(destination_path.parent)
    except OSError as exc:
        raise DurableIOError(
            "replacement was published but directory sync failed",
            published=True,
        ) from exc


def publish_file_noreplace(
    source: str | Path,
    destination: str | Path,
) -> None:
    """Publish one complete sibling file without replacing any destination."""

    source_path = Path(source).absolute()
    destination_path = Path(destination).absolute()
    _paths_are_siblings(source_path, destination_path)
    _source_kind(source_path, directory=False)
    _destination_must_be_absent(destination_path)
    if os.name == "nt":
        _move_windows(source_path, destination_path, replace=False)
        return
    try:
        os.link(source_path, destination_path)
    except OSError:
        raise
    try:
        flush_directory(destination_path.parent)
        source_path.unlink()
        flush_directory(destination_path.parent)
    except OSError as exc:
        raise DurableIOError(
            "file was published but directory sync failed",
            published=True,
        ) from exc


def publish_directory_noreplace(
    source: str | Path,
    destination: str | Path,
) -> None:
    """Publish one complete sibling directory without intended replacement."""

    source_path = Path(source).absolute()
    destination_path = Path(destination).absolute()
    _paths_are_siblings(source_path, destination_path)
    _source_kind(source_path, directory=True)
    _destination_must_be_absent(destination_path)
    if os.name == "nt":
        _move_windows(source_path, destination_path, replace=False)
        return
    try:
        os.rename(source_path, destination_path)
    except OSError:
        raise
    try:
        flush_directory(destination_path.parent)
    except OSError as exc:
        raise DurableIOError(
            "directory was published but parent sync failed",
            published=True,
        ) from exc


def _move_windows(
    source: Path,
    destination: Path,
    *,
    replace: bool,
) -> None:
    try:
        import win32file
    except ImportError as exc:
        raise DurableIOError(
            "durable Windows publication requires pywin32"
        ) from exc
    flags = win32file.MOVEFILE_WRITE_THROUGH
    if replace:
        flags |= win32file.MOVEFILE_REPLACE_EXISTING
    try:
        win32file.MoveFileEx(str(source), str(destination), flags)
    except OSError as exc:
        raise DurableIOError(
            "durable Windows publication failed",
            published=False,
        ) from exc
