"""Pinned Hugging Face dataset fetch with fail-closed local receipts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, ValidationError

from pipeline.durable_io import _is_linklike, first_linklike_path
from pipeline.real_dataset import (
    DatasetEvidenceError,
    DatasetLock,
    DatasetLockEntry,
    DatasetReceipt,
    DatasetReceiptEntry,
    HfDatasetSource,
    canonical_model_bytes,
    validate_dataset_receipt,
)


class DatasetDownloadError(ValueError):
    """The remote or local dataset could not be proven safe and complete."""


class HfHttpTransport(BaseModel):
    """HTTP policy with explicit injection points for local contract tests."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    origin_base_url: str = "https://huggingface.co"
    approved_download_hosts: tuple[str, ...] = ()
    allow_insecure_origin: bool = False
    allow_insecure_redirects: bool = False
    origin_headers: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 60.0


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_TREE_BYTES = 16 * 1024 * 1024
_MAX_TREE_PAGES = 10_000
_MAX_REDIRECTS = 5
_USER_AGENT = "nantai-3d-real-dataset/1"


def _approved_download_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    return normalized == "huggingface.co" or normalized.endswith(".cdn.hf.co")


def _origin_parts(transport: HfHttpTransport) -> tuple[str, str]:
    parsed = urlsplit(transport.origin_base_url)
    if not parsed.hostname or parsed.path.rstrip("/"):
        raise DatasetDownloadError("origin_base_url must contain only scheme and host")
    if parsed.scheme != "https" and not transport.allow_insecure_origin:
        raise DatasetDownloadError("origin_base_url must use HTTPS")
    if parsed.scheme not in {"http", "https"}:
        raise DatasetDownloadError("origin_base_url has an unsupported scheme")
    return parsed.scheme, parsed.netloc


def _origin_headers(transport: HfHttpTransport) -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    for key, value in transport.origin_headers:
        if any(ord(char) < 32 for char in key + value):
            raise DatasetDownloadError("origin header contains control characters")
        headers[key] = value
    return headers


def _open_no_redirect(
    url: str,
    headers: dict[str, str],
    transport: HfHttpTransport,
) -> Any:
    opener = build_opener(_NoRedirect())
    try:
        return opener.open(
            Request(url, headers=headers, method="GET"),
            timeout=transport.timeout_seconds,
        )
    except HTTPError as exc:
        if exc.code in _REDIRECT_CODES:
            return exc
        raise DatasetDownloadError(f"HTTP request failed with status {exc.code}") from exc
    except (OSError, URLError, HTTPException) as exc:
        raise DatasetDownloadError(f"HTTP request failed: {exc}") from exc


def _read_bounded_json(response: Any) -> Any:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_TREE_BYTES:
                raise DatasetDownloadError("tree response exceeds size limit")
        except ValueError as exc:
            raise DatasetDownloadError("tree response has invalid Content-Length") from exc
    try:
        raw = response.read(_MAX_TREE_BYTES + 1)
    except (OSError, HTTPException) as exc:
        raise DatasetDownloadError(f"tree response download failed: {exc}") from exc
    finally:
        response.close()
    if len(raw) > _MAX_TREE_BYTES:
        raise DatasetDownloadError("tree response exceeds size limit")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetDownloadError(f"tree response is invalid JSON: {exc}") from exc


def _next_link(value: str | None) -> str | None:
    if value is None:
        return None
    for item in value.split(","):
        segments = [segment.strip() for segment in item.split(";")]
        if any(segment == 'rel="next"' for segment in segments[1:]):
            target = segments[0]
            if not (target.startswith("<") and target.endswith(">")):
                raise DatasetDownloadError("pagination Link target is malformed")
            return target[1:-1]
    return None


def _tree_url(source: HfDatasetSource, transport: HfHttpTransport) -> str:
    base = transport.origin_base_url.rstrip("/")
    repository = "/".join(quote(part, safe="") for part in source.repository.split("/"))
    revision = quote(source.repository_revision, safe="")
    subtree = quote(source.subtree, safe="/")
    return (
        f"{base}/api/datasets/{repository}/tree/{revision}/{subtree}"
        "?recursive=true&expand=false&limit=1000"
    )


def _validate_tree_page_url(
    url: str,
    source: HfDatasetSource,
    transport: HfHttpTransport,
) -> None:
    scheme, netloc = _origin_parts(transport)
    parsed = urlsplit(url)
    expected_prefix = (
        f"/api/datasets/{source.repository}/tree/"
        f"{source.repository_revision}/{source.subtree}"
    )
    if parsed.scheme != scheme or parsed.netloc != netloc:
        raise DatasetDownloadError("tree pagination changed origin")
    if parsed.path != expected_prefix:
        raise DatasetDownloadError("tree pagination changed repository or revision")


def _server_identity(entry: dict[str, Any]) -> tuple[int, str]:
    size = entry.get("size")
    oid = entry.get("oid")
    if not isinstance(size, int) or size < 0:
        raise DatasetDownloadError("tree file has invalid size")
    if not isinstance(oid, str) or len(oid) != 40:
        raise DatasetDownloadError("tree file has invalid git oid")
    lfs = entry.get("lfs")
    if lfs is None:
        return size, f"git-oid:{oid}"
    if not isinstance(lfs, dict):
        raise DatasetDownloadError("tree file has invalid LFS metadata")
    lfs_oid = lfs.get("oid")
    lfs_size = lfs.get("size")
    if (
        not isinstance(lfs_oid, str)
        or len(lfs_oid) != 64
        or any(char not in "0123456789abcdef" for char in lfs_oid)
        or lfs_size != size
    ):
        raise DatasetDownloadError("tree file has invalid LFS identity")
    return size, f"lfs-sha256:{lfs_oid}"


def resolve_hf_lock(
    source: HfDatasetSource,
    transport: HfHttpTransport | None = None,
) -> DatasetLock:
    """Resolve every file below the immutable subtree into an ordered lock."""

    transport = transport or HfHttpTransport()
    url = _tree_url(source, transport)
    seen_pages: set[str] = set()
    raw_entries: list[DatasetLockEntry] = []
    seen_paths: set[str] = set()

    for _page_number in range(_MAX_TREE_PAGES):
        _validate_tree_page_url(url, source, transport)
        if url in seen_pages:
            raise DatasetDownloadError("tree pagination contains a cycle")
        seen_pages.add(url)
        response = _open_no_redirect(url, _origin_headers(transport), transport)
        if response.code != 200:
            response.close()
            raise DatasetDownloadError("tree request redirected or returned non-200")
        link = response.headers.get("Link")
        payload = _read_bounded_json(response)
        if not isinstance(payload, list):
            raise DatasetDownloadError("tree response must be a JSON list")
        for item in payload:
            if not isinstance(item, dict):
                raise DatasetDownloadError("tree entry must be an object")
            if item.get("type") == "directory":
                continue
            if item.get("type") != "file":
                raise DatasetDownloadError("tree entry has unsupported type")
            path = item.get("path")
            if not isinstance(path, str):
                raise DatasetDownloadError("tree file path is invalid")
            if path != source.subtree and not path.startswith(
                f"{source.subtree}/"
            ):
                raise DatasetDownloadError(
                    "tree member is outside the declared subtree"
                )
            if path in seen_paths:
                raise DatasetDownloadError(f"duplicate tree member: {path}")
            seen_paths.add(path)
            size, identity = _server_identity(item)
            try:
                raw_entries.append(
                    DatasetLockEntry(
                        relative_path=path,
                        expected_bytes=size,
                        server_identity=identity,
                    )
                )
            except ValidationError as exc:
                raise DatasetDownloadError(f"unsafe tree member: {exc}") from exc
        next_url = _next_link(link)
        if next_url is None:
            break
        url = urljoin(url, next_url)
    else:
        raise DatasetDownloadError("tree pagination exceeds page limit")

    entries = tuple(sorted(raw_entries, key=lambda entry: entry.relative_path))
    if len(entries) != source.declared_file_count:
        raise DatasetDownloadError("tree count does not match declared_file_count")
    if sum(entry.expected_bytes for entry in entries) != source.declared_total_bytes:
        raise DatasetDownloadError("tree bytes do not match declared_total_bytes")
    try:
        return DatasetLock(
            schema="nantai.dataset-lock.v1",
            source_sha256=_sha256(canonical_model_bytes(source)),
            repository=source.repository,
            repository_revision=source.repository_revision,
            entries=entries,
        )
    except ValidationError as exc:
        raise DatasetDownloadError(f"resolved dataset lock is invalid: {exc}") from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _download_url(
    source: HfDatasetSource,
    relative_path: str,
    transport: HfHttpTransport,
) -> str:
    repository = "/".join(quote(part, safe="") for part in source.repository.split("/"))
    revision = quote(source.repository_revision, safe="")
    path = quote(relative_path, safe="/")
    return (
        f"{transport.origin_base_url.rstrip('/')}/datasets/{repository}/resolve/"
        f"{revision}/{path}"
    )


def _redirect_allowed(url: str, transport: HfHttpTransport) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not transport.allow_insecure_redirects:
        raise DatasetDownloadError("download redirect must use HTTPS")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DatasetDownloadError("download redirect has invalid scheme or host")
    host = parsed.hostname.rstrip(".").lower()
    if transport.approved_download_hosts:
        allowed = host in {
            item.rstrip(".").lower() for item in transport.approved_download_hosts
        }
    else:
        allowed = _approved_download_host(host)
    if not allowed:
        raise DatasetDownloadError(f"download redirect host is not approved: {host}")


def _normalized_etag(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:]
    return normalized.strip('"')


def _verify_origin_headers(
    response: Any,
    source: HfDatasetSource,
    entry: DatasetLockEntry,
) -> None:
    if response.headers.get("X-Repo-Commit") != source.repository_revision:
        raise DatasetDownloadError("origin commit does not match pinned revision")
    linked_size = response.headers.get("X-Linked-Size")
    if linked_size is not None:
        try:
            measured_linked_size = int(linked_size)
        except ValueError as exc:
            raise DatasetDownloadError("origin linked length is invalid") from exc
        if measured_linked_size != entry.expected_bytes:
            raise DatasetDownloadError("origin linked length does not match lock")
    identity = _normalized_etag(
        response.headers.get("X-Linked-ETag") or response.headers.get("ETag")
    )
    expected_identity = entry.server_identity.split(":", 1)[1]
    if identity != expected_identity:
        raise DatasetDownloadError("origin server identity does not match lock")


def _safe_target(dataset_root: Path, relative_path: str) -> Path:
    target = dataset_root.joinpath(*relative_path.split("/"))
    current = dataset_root
    if current.is_symlink():
        raise DatasetDownloadError("dataset root must not be a symlink")
    if current.exists():
        if not current.is_dir():
            raise DatasetDownloadError("dataset root must be a directory")
    else:
        current.mkdir(parents=True)
    for part in relative_path.split("/")[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise DatasetDownloadError("dataset parent is not a safe directory")
        else:
            current.mkdir()
    if target.is_symlink():
        raise DatasetDownloadError("dataset target must not be a symlink")
    return target


def _cross_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int]:
    """Identity stable across lstat and fstat on Windows/POSIX."""

    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
    )


def _hash_file(path: Path, expected_bytes: int) -> tuple[int, str, str]:
    descriptor = -1
    try:
        redirected = first_linklike_path(
            Path(path.absolute().anchor), path
        )
        before = path.lstat()
    except OSError as exc:
        raise DatasetDownloadError("dataset file cannot be inspected") from exc
    except ValueError as exc:
        raise DatasetDownloadError("dataset file cannot be inspected") from exc
    if (
        redirected is not None
        or _is_linklike(path, observed=before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise DatasetDownloadError("dataset file is not a regular non-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DatasetDownloadError("dataset file cannot be opened") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise DatasetDownloadError("dataset file cannot be opened") from exc
    sha256_digest = hashlib.sha256()
    git_digest = hashlib.sha1(usedforsecurity=False)
    git_digest.update(f"blob {expected_bytes}\0".encode("ascii"))
    measured = 0
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if (
                _cross_surface_signature(descriptor_before)
                != _cross_surface_signature(before)
            ):
                raise DatasetDownloadError("dataset file changed before hash")
            while True:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                measured += len(chunk)
                sha256_digest.update(chunk)
                git_digest.update(chunk)
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except DatasetDownloadError:
        raise
    except OSError as exc:
        raise DatasetDownloadError("dataset file cannot be hashed") from exc
    if (
        _cross_surface_signature(before) != _cross_surface_signature(after)
        or _cross_surface_signature(descriptor_before)
        != _cross_surface_signature(descriptor_after)
    ):
        raise DatasetDownloadError("dataset file changed while being hashed")
    return measured, sha256_digest.hexdigest(), git_digest.hexdigest()


def _content_identity_matches(
    entry: DatasetLockEntry,
    sha256_digest: str,
    git_oid: str,
) -> bool:
    kind, expected = entry.server_identity.split(":", 1)
    if kind == "lfs-sha256":
        return sha256_digest == expected
    if kind == "git-oid":
        return git_oid == expected
    return False


def _download_entry(
    source: HfDatasetSource,
    entry: DatasetLockEntry,
    dataset_root: Path,
    transport: HfHttpTransport,
) -> DatasetReceiptEntry:
    target = _safe_target(dataset_root, entry.relative_path)
    if target.exists():
        size, sha256_digest, git_oid = _hash_file(
            target,
            entry.expected_bytes,
        )
        if size == entry.expected_bytes and _content_identity_matches(
            entry,
            sha256_digest,
            git_oid,
        ):
            return DatasetReceiptEntry(
                relative_path=entry.relative_path,
                expected_bytes=entry.expected_bytes,
                server_identity=entry.server_identity,
                actual_bytes=size,
                actual_sha256=sha256_digest,
            )

    response = _open_no_redirect(
        _download_url(source, entry.relative_path, transport),
        _origin_headers(transport),
        transport,
    )
    _verify_origin_headers(response, source, entry)
    redirects = 0
    while response.code in _REDIRECT_CODES:
        location = response.headers.get("Location")
        response.close()
        if location is None:
            raise DatasetDownloadError("download redirect is missing Location")
        redirected_url = urljoin(
            _download_url(source, entry.relative_path, transport),
            location,
        )
        _redirect_allowed(redirected_url, transport)
        redirects += 1
        if redirects > _MAX_REDIRECTS:
            raise DatasetDownloadError("download redirect limit exceeded")
        response = _open_no_redirect(
            redirected_url,
            {"User-Agent": _USER_AGENT},
            transport,
        )
    if response.code != 200:
        response.close()
        raise DatasetDownloadError(f"download returned status {response.code}")

    part = target.with_name(f"{target.name}.part")
    if part.is_symlink():
        response.close()
        raise DatasetDownloadError("download part path must not be a symlink")
    sha256_digest = hashlib.sha256()
    git_digest = hashlib.sha1(usedforsecurity=False)
    git_digest.update(f"blob {entry.expected_bytes}\0".encode("ascii"))
    measured = 0
    try:
        with part.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                sha256_digest.update(chunk)
                git_digest.update(chunk)
                measured += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, HTTPException) as exc:
        raise DatasetDownloadError(f"download failed for {entry.relative_path}: {exc}") from exc
    finally:
        response.close()
    measured_sha = sha256_digest.hexdigest()
    measured_git_oid = git_digest.hexdigest()
    if measured != entry.expected_bytes:
        raise DatasetDownloadError(
            f"download length mismatch for {entry.relative_path}"
        )
    if not _content_identity_matches(
        entry,
        measured_sha,
        measured_git_oid,
    ):
        raise DatasetDownloadError(
            f"download server identity mismatch for {entry.relative_path}"
        )
    os.replace(part, target)
    return DatasetReceiptEntry(
        relative_path=entry.relative_path,
        expected_bytes=entry.expected_bytes,
        server_identity=entry.server_identity,
        actual_bytes=measured,
        actual_sha256=measured_sha,
    )


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetDownloadError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_canonical_model(path: Path, model_type: type[BaseModel]) -> BaseModel:
    descriptor = -1
    try:
        redirected = first_linklike_path(
            Path(path.absolute().anchor), path
        )
        before = path.lstat()
    except OSError as exc:
        raise DatasetDownloadError(f"invalid receipt file {path.name}: {exc}") from exc
    except ValueError as exc:
        raise DatasetDownloadError(f"invalid receipt file {path.name}: {exc}") from exc
    if (
        redirected is not None
        or _is_linklike(path, observed=before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise DatasetDownloadError(f"receipt file {path.name} is not a regular non-link file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DatasetDownloadError(f"invalid receipt file {path.name}: {exc}") from exc
    try:
        stream = os.fdopen(descriptor, "rb", buffering=0)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise DatasetDownloadError(f"invalid receipt file {path.name}: {exc}") from exc
    try:
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if (
                _cross_surface_signature(descriptor_before)
                != _cross_surface_signature(before)
            ):
                raise DatasetDownloadError(f"receipt file {path.name} changed before read")
            raw = stream.read()
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except DatasetDownloadError:
        raise
    except OSError as exc:
        raise DatasetDownloadError(f"invalid receipt file {path.name}: {exc}") from exc
    if (
        _cross_surface_signature(before) != _cross_surface_signature(after)
        or _cross_surface_signature(descriptor_before)
        != _cross_surface_signature(descriptor_after)
    ):
        raise DatasetDownloadError(f"receipt file {path.name} changed while being read")
    try:
        json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
        model = model_type.model_validate_json(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise DatasetDownloadError(f"invalid receipt file {path.name}: {exc}") from exc
    if raw != canonical_model_bytes(model):
        raise DatasetDownloadError(f"receipt file {path.name} is not canonical")
    return model


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.part")
    if path.is_symlink() or part.is_symlink():
        raise DatasetDownloadError("receipt path must not be a symlink")
    try:
        with part.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, path)
    except OSError as exc:
        raise DatasetDownloadError(f"cannot publish receipt {path.name}: {exc}") from exc


def verify_hf_dataset(
    source: HfDatasetSource,
    workspace: Path,
) -> DatasetReceipt:
    """Revalidate existing receipts and live bytes without network access."""

    try:
        lock = _load_canonical_model(workspace / "dataset-lock.json", DatasetLock)
        receipt = _load_canonical_model(
            workspace / "dataset-receipt.json",
            DatasetReceipt,
        )
        assert isinstance(lock, DatasetLock)
        assert isinstance(receipt, DatasetReceipt)
        policy_bytes = (workspace / "dataset-policy.json").read_bytes()
        if policy_bytes != canonical_model_bytes(source):
            raise DatasetDownloadError("dataset-policy does not match source")
        validate_dataset_receipt(
            source,
            lock,
            receipt,
            workspace / "dataset",
        )
    except DatasetDownloadError:
        raise
    except (OSError, DatasetEvidenceError) as exc:
        raise DatasetDownloadError(str(exc)) from exc
    return receipt


def fetch_hf_dataset(
    source: HfDatasetSource,
    workspace: Path,
    transport: HfHttpTransport | None = None,
) -> DatasetReceipt:
    """Fetch or resume a pinned subtree and publish a verified receipt."""

    transport = transport or HfHttpTransport()
    receipt_path = workspace / "dataset-receipt.json"
    if receipt_path.exists():
        return verify_hf_dataset(source, workspace)

    lock = resolve_hf_lock(source, transport)
    _atomic_write(workspace / "dataset-lock.json", canonical_model_bytes(lock))
    _atomic_write(workspace / "dataset-policy.json", canonical_model_bytes(source))
    entries = tuple(
        _download_entry(source, entry, workspace / "dataset", transport)
        for entry in lock.entries
    )
    receipt = DatasetReceipt(
        schema="nantai.dataset-receipt.v1",
        source_sha256=lock.source_sha256,
        lock_sha256=_sha256(canonical_model_bytes(lock)),
        entries=entries,
    )
    try:
        validate_dataset_receipt(
            source,
            lock,
            receipt,
            workspace / "dataset",
        )
    except DatasetEvidenceError as exc:
        raise DatasetDownloadError(str(exc)) from exc
    _atomic_write(receipt_path, canonical_model_bytes(receipt))
    return receipt
