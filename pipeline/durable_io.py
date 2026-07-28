"""Small fail-closed durable publication primitives.

Writers must finish and sync a sibling candidate before calling these
functions.  Windows publication uses ``MoveFileEx(..., WRITE_THROUGH)``;
POSIX publication syncs the containing directory after the namespace change.
An error raised after a POSIX namespace change records ``published=True`` so
callers never confuse "not published" with "published, durability unknown".
"""

from __future__ import annotations

import ctypes
import os
import stat
import sys
from pathlib import Path


class DurableIOError(OSError):
    """A durable-write primitive could not prove its contract."""

    def __init__(self, message: str, *, published: bool = False):
        super().__init__(message)
        self.published = published


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path, "is_junction", lambda: False)()
    )


def _paths_are_siblings(source: Path, destination: Path) -> None:
    if source.parent != destination.parent:
        raise DurableIOError(
            "durable publication requires source and destination siblings"
        )


def _destination_must_be_absent(destination: Path) -> None:
    if destination.exists() or _is_linklike(destination):
        raise FileExistsError(
            f"durable publication destination already exists: {destination.name}"
        )


def _source_kind(path: Path, *, directory: bool) -> None:
    try:
        current = path.lstat()
    except OSError as exc:
        raise DurableIOError("durable publication source is unavailable") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if _is_linklike(path) or not expected(current.st_mode):
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
        import pywintypes
        import win32file
    except ImportError as exc:
        raise DurableIOError(
            "directory sync on Windows requires pywin32"
        ) from exc
    try:
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
    except pywintypes.error as exc:
        raise DurableIOError("Windows directory sync failed") from exc


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
        _rename_directory_noreplace_posix(source_path, destination_path)
    except OSError:
        raise
    try:
        flush_directory(destination_path.parent)
    except OSError as exc:
        raise DurableIOError(
            "directory was published but parent sync failed",
            published=True,
        ) from exc


def _rename_directory_noreplace_posix(
    source: Path,
    destination: Path,
) -> None:
    """Use an atomic kernel no-replace directory rename where supported."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise DurableIOError(
                "atomic no-replace directory publication requires renameat2"
            )
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            1,
        )
    elif sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise DurableIOError(
                "atomic no-replace directory publication requires renamex_np"
            )
        renamex_np.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    else:
        raise DurableIOError(
            "atomic no-replace directory publication is unsupported"
        )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(
            error,
            os.strerror(error),
            str(destination),
        )


def _move_windows(
    source: Path,
    destination: Path,
    *,
    replace: bool,
) -> None:
    try:
        import pywintypes
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
    except (OSError, pywintypes.error) as exc:
        raise DurableIOError(
            "durable Windows publication failed",
            published=False,
        ) from exc
