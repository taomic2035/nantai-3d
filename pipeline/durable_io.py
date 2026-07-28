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


DirectoryIdentity = tuple[int, int, int]


def _is_linklike(
    path: Path,
    *,
    observed: os.stat_result | None = None,
) -> bool:
    """Return whether *path* is a symlink or Windows reparse redirect.

    ``Path.is_junction`` is unavailable before Python 3.12, so the
    ``st_file_attributes`` check is the portable Windows trust boundary.
    ``lstat`` errors other than absence intentionally propagate for callers to
    map fail closed.  A junction-probe race is itself treated as link-like.
    """

    if observed is None:
        try:
            current = path.lstat()
        except FileNotFoundError:
            return False
    else:
        current = observed
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(current.st_mode)
        or int(getattr(current, "st_file_attributes", 0)) & reparse_flag
    ):
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction())
    except OSError:
        return True


def first_linklike_path(
    protected_root: str | Path,
    leaf: str | Path,
) -> Path | None:
    """Return the first link-like existing path from root through leaf.

    Paths are normalized lexically with ``absolute``; this deliberately never
    calls ``resolve`` because resolution would traverse the redirect before it
    can be rejected.  Missing components are allowed for fresh destinations.
    Other ``lstat`` errors propagate so trust-boundary callers fail closed.
    """

    root = Path(protected_root).absolute()
    target = Path(leaf).absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("leaf must remain below the protected root") from exc
    current = root
    candidates = [root]
    for part in relative.parts:
        current = current / part
        candidates.append(current)
    for candidate in candidates:
        try:
            observed = candidate.lstat()
        except FileNotFoundError:
            continue
        if _is_linklike(candidate, observed=observed):
            return candidate
    return None


def capture_real_directory_identity(path: str | Path) -> DirectoryIdentity:
    """Capture one non-redirected directory namespace identity."""

    directory = Path(path).absolute()
    try:
        redirected = first_linklike_path(Path(directory.anchor), directory)
        observed = directory.lstat()
    except (OSError, ValueError) as exc:
        raise DurableIOError("directory identity cannot be captured") from exc
    if (
        redirected is not None
        or _is_linklike(directory, observed=observed)
        or not stat.S_ISDIR(observed.st_mode)
    ):
        raise DurableIOError("directory identity cannot be captured")
    return observed.st_dev, observed.st_ino, observed.st_mode


def matches_real_directory_identity(
    path: str | Path,
    expected: DirectoryIdentity,
) -> bool:
    """Return whether *path* is still the same non-redirected directory."""

    directory = Path(path).absolute()
    try:
        return capture_real_directory_identity(directory) == expected
    except DurableIOError:
        return False


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
    if _is_linklike(path, observed=current) or not expected(current.st_mode):
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
