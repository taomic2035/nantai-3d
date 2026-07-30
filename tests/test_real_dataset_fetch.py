from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from pipeline.real_dataset import HfDatasetSource, canonical_model_bytes
from pipeline.real_dataset_fetch import (
    DatasetDownloadError,
    HfHttpTransport,
    fetch_hf_dataset,
    resolve_hf_lock,
    verify_hf_dataset,
)

_REVISION = "4" * 40
_BODY = b"image"
_LFS_OID = hashlib.sha256(_BODY).hexdigest()
_GIT_OID = hashlib.sha1(b"blob 5\0image", usedforsecurity=False).hexdigest()


def _source(
    *,
    count: int = 1,
    total_bytes: int = len(_BODY),
) -> HfDatasetSource:
    return HfDatasetSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="poster",
        role="internal-canary",
        source_kind="hf-dataset",
        repository="owner/repo",
        repository_revision=_REVISION,
        subtree="poster",
        capture_subtree="poster/images",
        declared_file_count=count,
        declared_total_bytes=total_bytes,
        license_status="not-declared",
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )


class _FixtureState:
    def __init__(self) -> None:
        self.mode = "ok"
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.server_address = ""

    def tree_entry(self, path: str = "poster/images/frame.png") -> dict[str, object]:
        if self.mode == "git-file":
            return {
                "type": "file",
                "oid": _GIT_OID,
                "size": len(_BODY),
                "path": path,
            }
        return {
            "type": "file",
            "oid": "1" * 40,
            "size": len(_BODY),
            "lfs": {
                "oid": _LFS_OID,
                "size": len(_BODY),
                "pointerSize": 128,
            },
            "path": path,
        }


@contextmanager
def _http_fixture() -> Iterator[tuple[_FixtureState, HfHttpTransport]]:
    state = _FixtureState()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_GET(self) -> None:
            headers = {key.lower(): value for key, value in self.headers.items()}
            state.requests.append((self.path, headers))
            parsed = urlsplit(self.path)

            if "/api/datasets/owner/repo/tree/" in parsed.path:
                if state.mode == "network-forbidden":
                    self.send_error(599)
                    return
                if state.mode == "pagination":
                    if "cursor=second" in parsed.query:
                        payload = [state.tree_entry("poster/images/b.png")]
                        link = None
                    else:
                        payload = [state.tree_entry("poster/images/a.png")]
                        link = (
                            f"<http://{state.server_address}{parsed.path}"
                            "?recursive=true&expand=false&limit=1000&cursor=second>; "
                            'rel="next"'
                        )
                elif state.mode == "duplicate-tree":
                    payload = [
                        state.tree_entry(),
                        state.tree_entry(),
                    ]
                    link = None
                elif state.mode == "outside-subtree":
                    payload = [state.tree_entry("other/frame.png")]
                    link = None
                else:
                    payload = [state.tree_entry()]
                    link = None
                body = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if link is not None:
                    self.send_header("Link", link)
                self.end_headers()
                self.wfile.write(body)
                return

            if "/datasets/owner/repo/resolve/" in parsed.path:
                commit = "f" * 40 if state.mode == "wrong-origin-commit" else _REVISION
                identity = (
                    '"0"'
                    if state.mode == "wrong-server-identity"
                    else f'"{_GIT_OID if state.mode == "git-file" else _LFS_OID}"'
                )
                if state.mode == "unapproved-host":
                    location = f"http://localhost:{self.server.server_port}/cdn/data"
                else:
                    location = f"http://{state.server_address}/cdn/data"
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("X-Repo-Commit", commit)
                if state.mode != "git-file":
                    self.send_header("X-Linked-Size", str(len(_BODY)))
                self.send_header("X-Linked-ETag", identity)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if parsed.path == "/cdn/data":
                if state.mode == "truncated-body":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(_BODY) + 10))
                    self.end_headers()
                    self.wfile.write(_BODY[:2])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                body = b"tiny" if state.mode == "wrong-size" else _BODY
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_error(404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.server_address = f"127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = HfHttpTransport(
        origin_base_url=f"http://{state.server_address}",
        approved_download_hosts=("127.0.0.1",),
        allow_insecure_origin=True,
        allow_insecure_redirects=True,
        origin_headers=(
            ("Authorization", "Bearer secret"),
            ("Cookie", "session=secret"),
        ),
    )
    try:
        yield state, transport
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_fetch_accepts_pinned_origin_and_strips_cdn_credentials(
    tmp_path: Path,
) -> None:
    with _http_fixture() as (state, transport):
        receipt = fetch_hf_dataset(_source(), tmp_path, transport)

    assert receipt.entries[0].actual_sha256 == _LFS_OID
    assert (tmp_path / "dataset/poster/images/frame.png").read_bytes() == _BODY
    cdn_headers = next(headers for path, headers in state.requests if path == "/cdn/data")
    assert "authorization" not in cdn_headers
    assert "cookie" not in cdn_headers


def test_fetch_verifies_non_lfs_git_blob_identity(tmp_path: Path) -> None:
    with _http_fixture() as (state, transport):
        state.mode = "git-file"
        receipt = fetch_hf_dataset(_source(), tmp_path, transport)

    assert receipt.entries[0].server_identity == f"git-oid:{_GIT_OID}"
    assert receipt.entries[0].actual_sha256 == hashlib.sha256(_BODY).hexdigest()


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("wrong-origin-commit", "commit"),
        ("unapproved-host", "host"),
        ("wrong-size", "length"),
        ("wrong-server-identity", "identity"),
        ("truncated-body", "download"),
    ],
)
def test_fetch_fails_closed(
    mode: str,
    message: str,
    tmp_path: Path,
) -> None:
    with _http_fixture() as (state, transport):
        state.mode = mode
        with pytest.raises(DatasetDownloadError, match=message):
            fetch_hf_dataset(_source(), tmp_path, transport)


def test_production_policy_rejects_http_redirect(tmp_path: Path) -> None:
    with _http_fixture() as (state, transport):
        strict_redirects = transport.model_copy(
            update={"allow_insecure_redirects": False}
        )
        with pytest.raises(DatasetDownloadError, match="HTTPS"):
            fetch_hf_dataset(_source(), tmp_path, strict_redirects)
        assert not any(path == "/cdn/data" for path, _headers in state.requests)


def test_tree_pagination_is_complete_and_sorted() -> None:
    with _http_fixture() as (state, transport):
        state.mode = "pagination"
        lock = resolve_hf_lock(
            _source(count=2, total_bytes=len(_BODY) * 2),
            transport,
        )
    assert [entry.relative_path for entry in lock.entries] == [
        "poster/images/a.png",
        "poster/images/b.png",
    ]


def test_duplicate_tree_member_is_rejected() -> None:
    with _http_fixture() as (state, transport):
        state.mode = "duplicate-tree"
        with pytest.raises(DatasetDownloadError, match="duplicate"):
            resolve_hf_lock(
                _source(count=2, total_bytes=len(_BODY) * 2),
                transport,
            )


def test_tree_member_outside_declared_subtree_is_rejected() -> None:
    with _http_fixture() as (state, transport):
        state.mode = "outside-subtree"
        with pytest.raises(DatasetDownloadError, match="subtree"):
            resolve_hf_lock(_source(), transport)


def test_symlinked_dataset_root_is_rejected_before_payload_write(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "run"
    workspace.mkdir()
    try:
        (workspace / "dataset").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with _http_fixture() as (_state, transport):
        with pytest.raises(DatasetDownloadError, match="symlink"):
            fetch_hf_dataset(_source(), workspace, transport)
    assert list(outside.rglob("*")) == []


def test_interruption_leaves_only_untrusted_part_and_retry_replaces_it(
    tmp_path: Path,
) -> None:
    with _http_fixture() as (state, transport):
        state.mode = "truncated-body"
        with pytest.raises(DatasetDownloadError):
            fetch_hf_dataset(_source(), tmp_path, transport)
        target = tmp_path / "dataset/poster/images/frame.png"
        part = target.with_name("frame.png.part")
        assert not target.exists()
        assert part.exists()

        state.mode = "ok"
        receipt = fetch_hf_dataset(_source(), tmp_path, transport)

    assert receipt.entries[0].actual_sha256 == _LFS_OID
    assert target.read_bytes() == _BODY
    assert not part.exists()


def test_verify_is_offline_and_rehashes_live_bytes(tmp_path: Path) -> None:
    with _http_fixture() as (state, transport):
        expected = fetch_hf_dataset(_source(), tmp_path, transport)
        requests_before_verify = len(state.requests)
        state.mode = "network-forbidden"
        assert verify_hf_dataset(_source(), tmp_path) == expected
        assert len(state.requests) == requests_before_verify

        (tmp_path / "dataset/poster/images/frame.png").write_bytes(b"other")
        with pytest.raises(DatasetDownloadError, match="dataset verification failed"):
            verify_hf_dataset(_source(), tmp_path)


def test_verify_only_cli_uses_existing_receipts_without_network(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.json"
    source_path.write_bytes(canonical_model_bytes(_source()))
    with _http_fixture() as (_state, transport):
        fetch_hf_dataset(_source(), tmp_path / "run", transport)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.fetch_real_dataset",
            str(source_path),
            str(tmp_path / "run"),
            "--verify-only",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "receipt_sha256=" in result.stdout


class TestDatasetFetchReaderIntegrity:
    """Security boundary tests for real_dataset_fetch trust readers."""

    def test_hash_file_rejects_ancestor_reparse(self, tmp_path, monkeypatch):
        import pipeline.real_dataset_fetch as rdf
        from pipeline.real_dataset_fetch import DatasetDownloadError, _hash_file

        target = tmp_path / "a.bin"
        target.write_bytes(b"data\n")
        sentinel = tmp_path / "ancestor-reparse"
        original = rdf.first_linklike_path

        def fake_first_linklike_path(root, leaf):
            if Path(leaf) == target:
                return sentinel
            return original(root, leaf)

        monkeypatch.setattr(rdf, "first_linklike_path", fake_first_linklike_path)
        with pytest.raises(
            DatasetDownloadError,
            match="regular non-link file|redirected|unsafe",
        ):
            _hash_file(target, expected_bytes=5)

    def test_hash_file_rejects_path_swap_before_open(
        self, tmp_path, monkeypatch
    ):
        from pipeline.real_dataset_fetch import DatasetDownloadError, _hash_file

        original_path = tmp_path / "a.bin"
        original_path.write_bytes(b"original\n")
        swap_count = 0
        original_open = os.open

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swap_count
            swap_count += 1
            if swap_count == 1:
                original_path.write_bytes(b"swapped content\n")
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swapping_open)
        with pytest.raises(
            DatasetDownloadError,
            match="changed before hash|changed while",
        ):
            _hash_file(original_path, expected_bytes=9)

    def test_verify_does_not_use_path_read_bytes_for_policy(
        self, tmp_path, monkeypatch
    ):
        """RED->GREEN: verify_hf_dataset must not use Path.read_bytes for policy.

        dataset-policy.json is a trust-critical input.  Reading it via
        Path.read_bytes() is vulnerable to symlink/reparse redirection and
        lacks identity verification.  It must be loaded via the same
        descriptor-based _load_canonical_model reader used for lock/receipt.
        """
        with _http_fixture() as (_state, transport):
            fetch_hf_dataset(_source(), tmp_path, transport)

        def reject_read_bytes(self, *args, **kwargs):
            raise AssertionError(
                "verify_hf_dataset must not use Path.read_bytes"
            )

        monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)

        verify_hf_dataset(_source(), tmp_path)

    def test_verify_hf_dataset_hides_oserror_details(
        self, tmp_path, monkeypatch
    ):
        """RED->GREEN: OSError text must not leak into DatasetDownloadError."""
        with _http_fixture() as (_state, transport):
            fetch_hf_dataset(_source(), tmp_path, transport)

        private_detail = r"D:\private-capture\secret-token"

        def fail_open(*_args, **_kwargs):
            raise OSError(private_detail)

        monkeypatch.setattr(os, "open", fail_open)

        with pytest.raises(DatasetDownloadError) as exc_info:
            verify_hf_dataset(_source(), tmp_path)

        assert private_detail not in str(exc_info.value)
