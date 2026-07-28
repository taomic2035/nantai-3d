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
import shutil
import stat
import sys
import tempfile
import uuid
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import BinaryIO


class DurableIOError(OSError):
    """A durable-write primitive could not prove its contract."""

    def __init__(self, message: str, *, published: bool = False):
        super().__init__(message)
        self.published = published


DirectoryIdentity = tuple[int, int, int]


class BoundPathCleanup(Enum):
    """Outcome of a cleanup attempted through a bound parent capability."""

    REMOVED = "removed"
    ABSENT = "absent"
    RETAINED = "retained"


def _component_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
    ):
        raise DurableIOError("bound operation requires a single path component")
    return name


def _windows_modules():
    try:
        import msvcrt

        import pywintypes
        import win32file
    except ImportError as exc:
        raise DurableIOError(
            "bound Windows operations require pywin32"
        ) from exc
    return msvcrt, pywintypes, win32file


def _windows_path(path: Path) -> Path:
    absolute = path.expanduser().absolute()
    raw = str(absolute)
    if raw.startswith("\\\\") or not absolute.drive:
        raise DurableIOError(
            "bound Windows operations require a local drive path"
        )
    return absolute


def _windows_final_path(path: Path) -> str:
    raw = os.path.normcase(os.path.abspath(str(path)))
    return raw.rstrip("\\") or raw


def _windows_handle_path(handle, win32file) -> str:
    raw = str(win32file.GetFinalPathNameByHandle(handle, 0))
    if raw.startswith("\\\\?\\UNC\\"):
        raise DurableIOError("bound Windows operations reject remote paths")
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return os.path.normcase(os.path.abspath(raw)).rstrip("\\")


def _windows_error(exc: BaseException, path: Path) -> OSError:
    raw_code = getattr(exc, "winerror", None)
    if raw_code is None:
        raw_code = getattr(exc, "errno", None)
    code = int(raw_code if raw_code is not None else 1)
    message = str(getattr(exc, "strerror", exc))
    return OSError(code, message, str(path), code)


def _is_not_found_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        code = getattr(current, "winerror", None)
        if code is None:
            code = getattr(current, "errno", None)
        if code is not None and int(code) in {2, 3}:
            return True
        current = current.__cause__
    return False


def _open_windows_handle(
    path: Path,
    *,
    directory: bool,
    creation: int,
    access: int,
):
    _msvcrt, pywintypes, win32file = _windows_modules()
    flags = win32file.FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= win32file.FILE_FLAG_BACKUP_SEMANTICS
    try:
        handle = win32file.CreateFile(
            str(path),
            access,
            win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
            None,
            creation,
            flags,
            None,
        )
        attributes = win32file.GetFileInformationByHandleEx(
            handle,
            win32file.FileAttributeTagInfo,
        )
        if int(attributes["FileAttributes"]) & 0x400:
            handle.Close()
            raise DurableIOError("bound path must not be a reparse point")
        actual = _windows_handle_path(handle, win32file)
        expected = _windows_final_path(path)
        if actual != expected:
            handle.Close()
            raise DurableIOError("bound path identity is not the requested path")
        return handle
    except DurableIOError:
        raise
    except (OSError, pywintypes.error) as exc:
        raise DurableIOError("bound Windows path cannot be opened") from exc


class BoundFile:
    """One newly-created regular file held by an operating-system handle."""

    def __init__(
        self,
        parent: BoundDirectory,
        name: str,
        stream: BinaryIO,
    ):
        self._parent = parent
        self._name = _component_name(name)
        self.stream = stream
        self._closed = False

    @property
    def path(self) -> Path:
        return self._parent.path / self._name

    def flush(self) -> None:
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def publish_noreplace(
        self,
        destination_parent: BoundDirectory,
        destination_name: str,
    ) -> None:
        """Rename/link this exact open file into a bound destination parent."""

        self._require_open()
        destination_parent._require_open()
        target_name = _component_name(destination_name)
        self.flush()
        if os.name == "nt":
            msvcrt, pywintypes, win32file = _windows_modules()
            raw_handle = msvcrt.get_osfhandle(self.stream.fileno())
            try:
                win32file.SetFileInformationByHandle(
                    raw_handle,
                    win32file.FileRenameInfo,
                    {
                        "ReplaceIfExists": False,
                        "RootDirectory": None,
                        "FileName": str(destination_parent.path / target_name),
                    },
                )
            except (OSError, pywintypes.error) as exc:
                raise _windows_error(
                    exc,
                    destination_parent.path / target_name,
                ) from exc
            try:
                destination_parent.flush()
                if destination_parent is not self._parent:
                    self._parent.flush()
            except OSError as exc:
                raise DurableIOError(
                    "file was published but directory sync failed",
                    published=True,
                ) from exc
        else:
            _link_open_file_noreplace(
                self.stream.fileno(),
                destination_parent._fd,
                target_name,
            )
            source_before = os.fstat(self.stream.fileno())
            source_named = os.stat(
                self._name,
                dir_fd=self._parent._fd,
                follow_symlinks=False,
            )
            if (
                source_before.st_dev,
                source_before.st_ino,
            ) != (
                source_named.st_dev,
                source_named.st_ino,
            ):
                raise DurableIOError(
                    "file was published but source name changed",
                    published=True,
                )
            os.unlink(self._name, dir_fd=self._parent._fd)
            os.fsync(destination_parent._fd)
            if destination_parent._fd != self._parent._fd:
                os.fsync(self._parent._fd)
        self._parent = destination_parent
        self._name = target_name

    def _require_open(self) -> None:
        if self._closed:
            raise DurableIOError("bound file is closed")
        self._parent._require_open()

    def close(self) -> None:
        if not self._closed:
            self.stream.close()
            self._closed = True

    def __enter__(self) -> BoundFile:
        self._require_open()
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class BoundDirectory:
    """A directory namespace capability held by an OS descriptor/lease."""

    def __init__(
        self,
        path: Path,
        handle,
        *,
        source_parent_fd: int | None = None,
        source_name: str | None = None,
    ):
        self.path = path
        self._handle = handle
        self._fd = handle if os.name != "nt" else None
        self._source_parent_fd = source_parent_fd
        self._source_name = source_name
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise DurableIOError("bound directory is closed")

    def create_directory(
        self,
        name: str,
        *,
        mode: int = 0o700,
    ) -> BoundDirectory:
        self._require_open()
        component = _component_name(name)
        if os.name == "nt":
            try:
                os.mkdir(self.path / component, mode)
            except OSError:
                raise
            try:
                return _bind_windows_directory(
                    self.path / component,
                    source_name=component,
                )
            except Exception:
                self.remove_tree(component)
                raise
        os.mkdir(component, mode=mode, dir_fd=self._fd)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        child_fd = os.open(component, flags, dir_fd=self._fd)
        return BoundDirectory(
            self.path / component,
            child_fd,
            source_parent_fd=os.dup(self._fd),
            source_name=component,
        )

    def open_directory(self, name: str) -> BoundDirectory:
        self._require_open()
        component = _component_name(name)
        if os.name == "nt":
            return _bind_windows_directory(
                self.path / component,
                source_name=component,
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        child_fd = os.open(component, flags, dir_fd=self._fd)
        return BoundDirectory(
            self.path / component,
            child_fd,
            source_parent_fd=os.dup(self._fd),
            source_name=component,
        )

    def ensure_directories(
        self,
        parts: tuple[str, ...] | list[str],
        *,
        mode: int = 0o700,
    ) -> BoundDirectory:
        """Open/create a relative directory chain one bound component at a time."""

        current = self
        owned: list[BoundDirectory] = []
        try:
            for raw in parts:
                name = _component_name(raw)
                try:
                    child = current.open_directory(name)
                except (FileNotFoundError, DurableIOError) as exc:
                    if not _is_not_found_error(exc):
                        raise
                    child = current.create_directory(name, mode=mode)
                owned.append(child)
                current = child
            if not owned:
                raise DurableIOError("directory chain must not be empty")
            result = owned.pop()
            for opened in owned:
                if opened is not result:
                    opened.close()
            return result
        except Exception:
            for opened in reversed(owned):
                opened.close()
            raise

    def create_file(self, name: str, *, mode: int = 0o600) -> BoundFile:
        self._require_open()
        component = _component_name(name)
        if os.name == "nt":
            msvcrt, _pywintypes, win32file = _windows_modules()
            handle = _open_windows_handle(
                self.path / component,
                directory=False,
                creation=win32file.CREATE_NEW,
                access=(
                    win32file.GENERIC_READ
                    | win32file.GENERIC_WRITE
                    | 0x00010000
                ),
            )
            raw = handle.Detach()
            descriptor = msvcrt.open_osfhandle(
                raw,
                os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
        else:
            descriptor = os.open(
                component,
                (
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                ),
                mode,
                dir_fd=self._fd,
            )
        return BoundFile(self, component, os.fdopen(descriptor, "w+b"))

    def open_file(self, name: str) -> BoundFile:
        """Open one existing regular non-redirected child with delete fencing."""

        self._require_open()
        component = _component_name(name)
        if os.name == "nt":
            msvcrt, _pywintypes, win32file = _windows_modules()
            handle = _open_windows_handle(
                self.path / component,
                directory=False,
                creation=win32file.OPEN_EXISTING,
                access=(
                    win32file.GENERIC_READ
                    | win32file.GENERIC_WRITE
                    | 0x00010000
                ),
            )
            raw = handle.Detach()
            descriptor = msvcrt.open_osfhandle(
                raw,
                os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
        else:
            descriptor = os.open(
                component,
                os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self._fd,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise DurableIOError("bound child must be a regular file")
        return BoundFile(self, component, os.fdopen(descriptor, "r+b"))

    def flush(self) -> None:
        self._require_open()
        if os.name == "nt":
            _flush_directory_windows(self.path)
        else:
            os.fsync(self._fd)

    def unlink_file(
        self,
        name: str,
        *,
        missing_ok: bool = False,
    ) -> BoundPathCleanup:
        self._require_open()
        component = _component_name(name)
        if os.name == "nt":
            _msvcrt, pywintypes, win32file = _windows_modules()
            try:
                handle = _open_windows_handle(
                    self.path / component,
                    directory=False,
                    creation=win32file.OPEN_EXISTING,
                    access=0x00010000 | 0x80,
                )
            except DurableIOError as exc:
                if missing_ok and _is_not_found_error(exc):
                    return BoundPathCleanup.ABSENT
                raise
            try:
                win32file.SetFileInformationByHandle(
                    handle,
                    win32file.FileDispositionInfo,
                    True,
                )
            except (OSError, pywintypes.error) as exc:
                raise _windows_error(
                    exc,
                    self.path / component,
                ) from exc
            finally:
                handle.Close()
            return BoundPathCleanup.REMOVED
        try:
            os.unlink(component, dir_fd=self._fd)
        except FileNotFoundError:
            if missing_ok:
                return BoundPathCleanup.ABSENT
            raise
        return BoundPathCleanup.REMOVED

    def remove_tree(self, name: str) -> BoundPathCleanup:
        self._require_open()
        component = _component_name(name)
        if os.name == "nt":
            try:
                child = self.open_directory(component)
            except DurableIOError as exc:
                if _is_not_found_error(exc):
                    return BoundPathCleanup.ABSENT
                return BoundPathCleanup.RETAINED
            try:
                return child._remove_windows_tree()
            finally:
                child.close()
        if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
            return BoundPathCleanup.RETAINED
        try:
            shutil.rmtree(component, dir_fd=self._fd)
        except FileNotFoundError:
            return BoundPathCleanup.ABSENT
        except OSError:
            return BoundPathCleanup.RETAINED
        return BoundPathCleanup.REMOVED

    def _remove_windows_tree(self) -> BoundPathCleanup:
        self._require_open()
        try:
            entries = tuple(os.scandir(self.path))
        except OSError:
            return BoundPathCleanup.RETAINED
        for entry in entries:
            try:
                if entry.is_symlink():
                    return BoundPathCleanup.RETAINED
                if entry.is_dir(follow_symlinks=False):
                    child = self.open_directory(entry.name)
                    try:
                        if (
                            child._remove_windows_tree()
                            is not BoundPathCleanup.REMOVED
                        ):
                            return BoundPathCleanup.RETAINED
                    finally:
                        child.close()
                elif entry.is_file(follow_symlinks=False):
                    self.unlink_file(entry.name)
                else:
                    return BoundPathCleanup.RETAINED
            except (OSError, DurableIOError):
                return BoundPathCleanup.RETAINED
        _msvcrt, pywintypes, win32file = _windows_modules()
        try:
            win32file.SetFileInformationByHandle(
                self._handle,
                win32file.FileDispositionInfo,
                True,
            )
        except (OSError, pywintypes.error):
            return BoundPathCleanup.RETAINED
        return BoundPathCleanup.REMOVED

    def publish_noreplace(
        self,
        destination_parent: BoundDirectory,
        destination_name: str,
    ) -> None:
        self._require_open()
        destination_parent._require_open()
        target_name = _component_name(destination_name)
        if os.name == "nt":
            _msvcrt, pywintypes, win32file = _windows_modules()
            try:
                win32file.SetFileInformationByHandle(
                    self._handle,
                    win32file.FileRenameInfo,
                    {
                        "ReplaceIfExists": False,
                        "RootDirectory": None,
                        "FileName": str(destination_parent.path / target_name),
                    },
                )
            except (OSError, pywintypes.error) as exc:
                raise _windows_error(
                    exc,
                    destination_parent.path / target_name,
                ) from exc
            try:
                destination_parent.flush()
            except OSError as exc:
                raise DurableIOError(
                    "directory was published but parent sync failed",
                    published=True,
                ) from exc
        else:
            if self._source_parent_fd is None or self._source_name is None:
                raise DurableIOError(
                    "directory publication requires a bound source parent"
                )
            _rename_directory_bound_noreplace(
                self._source_parent_fd,
                self._source_name,
                destination_parent._fd,
                target_name,
            )
            os.fsync(destination_parent._fd)
            if destination_parent._fd != self._source_parent_fd:
                os.fsync(self._source_parent_fd)
        self.path = destination_parent.path / target_name
        if os.name != "nt":
            if self._source_parent_fd is not None:
                os.close(self._source_parent_fd)
            self._source_parent_fd = os.dup(destination_parent._fd)
        self._source_name = target_name

    def close(self) -> None:
        if self._closed:
            return
        if os.name == "nt":
            self._handle.Close()
        else:
            os.close(self._fd)
            if self._source_parent_fd is not None:
                os.close(self._source_parent_fd)
        self._closed = True

    def __enter__(self) -> BoundDirectory:
        self._require_open()
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def _bind_windows_directory(
    path: Path,
    *,
    source_name: str | None = None,
) -> BoundDirectory:
    local = _windows_path(path)
    _msvcrt, _pywintypes, win32file = _windows_modules()
    handle = _open_windows_handle(
        local,
        directory=True,
        creation=win32file.OPEN_EXISTING,
        access=0x00010000 | 0x80,
    )
    return BoundDirectory(
        local,
        handle,
        source_name=source_name,
    )


def bind_directory(path: str | Path) -> BoundDirectory:
    """Bind a real local directory without following redirect components."""

    directory = Path(path).expanduser().absolute()
    if os.name == "nt":
        return _bind_windows_directory(directory)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    anchor = Path(directory.anchor)
    current_fd = os.open(anchor, flags)
    current_path = anchor
    parent_fd: int | None = None
    final_name: str | None = None
    try:
        relative = directory.relative_to(anchor)
        for raw in relative.parts:
            component = _component_name(raw)
            next_fd = os.open(component, flags, dir_fd=current_fd)
            if parent_fd is not None:
                os.close(parent_fd)
            parent_fd = current_fd
            current_fd = next_fd
            current_path = current_path / component
            final_name = component
        return BoundDirectory(
            current_path,
            current_fd,
            source_parent_fd=parent_fd,
            source_name=final_name,
        )
    except Exception:
        os.close(current_fd)
        if parent_fd is not None:
            os.close(parent_fd)
        raise


@contextmanager
def bound_temporary_directory(
    *,
    prefix: str,
    parent: str | Path | None = None,
):
    """Create and safely clean one private UUID directory under a bound parent."""

    root_path = (
        Path(parent)
        if parent is not None
        else Path(tempfile.gettempdir())
    )
    parent_bound = bind_directory(root_path)
    temporary = None
    name = f"{prefix}{uuid.uuid4().hex}"
    try:
        temporary = parent_bound.create_directory(name, mode=0o700)
        yield temporary
    finally:
        if temporary is not None:
            temporary.close()
            outcome = parent_bound.remove_tree(name)
            if outcome not in {
                BoundPathCleanup.REMOVED,
                BoundPathCleanup.ABSENT,
            }:
                parent_bound.close()
                raise DurableIOError(
                    "bound temporary directory was retained for safety"
                )
        parent_bound.close()


def _link_open_file_noreplace(
    source_fd: int,
    destination_fd: int,
    destination_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise DurableIOError(
            "bound file publication is unsupported on this POSIX platform"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    linkat = getattr(libc, "linkat", None)
    if linkat is None:
        raise DurableIOError("bound file publication requires linkat")
    linkat.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    )
    linkat.restype = ctypes.c_int
    source_reference = os.fsencode(f"/proc/self/fd/{source_fd}")
    if linkat(
        -100,
        source_reference,
        destination_fd,
        os.fsencode(destination_name),
        0x400,
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination_name)


def _rename_directory_bound_noreplace(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise DurableIOError(
            "bound directory publication requires Linux renameat2"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise DurableIOError(
            "bound directory publication requires renameat2"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        source_parent_fd,
        os.fsencode(source_name),
        destination_parent_fd,
        os.fsencode(destination_name),
        1,
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination_name)


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
