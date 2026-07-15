"""Secure loopback HTTP contract for B1 ingest jobs."""

from __future__ import annotations

import http.client
import json
import os
import socket
import threading
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

import pytest

from pipeline.studio_jobs import (
    DurabilityReadiness,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)
from pipeline.studio_server import make_server


@contextmanager
def _running_server(root: Path, **kwargs):
    server = make_server(
        root, host="127.0.0.1", port=0, enable_jobs=True, **kwargs,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        if server.job_service is not None:
            server.job_service.shutdown()
        thread.join(timeout=5)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "input/photo.jpg").write_bytes(b"http-photo")
    return root


def _request(server, method: str, path: str, *, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    connection.request(method, path, body=encoded, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    actual_headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, actual_headers, json.loads(payload) if payload else None


def _authorization(server, *, token=None, origin=None, host=None):
    address, port = server.server_address[:2]
    canonical = f"http://{address}:{port}"
    return {
        "Host": host or f"{address}:{port}",
        "Origin": origin or canonical,
        "Content-Type": "application/json",
        "X-Nantai-Token": token or server.request_token,
        "X-Request-ID": "request-http-001",
    }


def _headers_only_post(server, *, host: str, content_length: int) -> bytes:
    headers = _authorization(server, host=host)
    request = "\r\n".join([
        "POST /api/jobs HTTP/1.1",
        *(f"{name}: {value}" for name, value in headers.items()),
        f"Content-Length: {content_length}",
        "Connection: close",
        "",
        "",
    ]).encode("ascii")
    with socket.create_connection(server.server_address, timeout=2) as connection:
        connection.settimeout(2)
        connection.sendall(request)
        chunks = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_enabled_server_advertises_only_ingest_with_startup_scoped_token(tmp_path):
    with _running_server(_project(tmp_path)) as server:
        status, headers, payload = _request(server, "GET", "/api/capabilities")

    assert status == HTTPStatus.OK
    assert headers["cache-control"] == "no-store"
    assert payload["mode"] == "read-write"
    assert payload["request_token"] == server.request_token
    assert len(server.request_token) >= 43
    assert payload["commands"]["ingest"] == {
        "enabled": True, "cancel": False, "retry": False, "reason": None,
    }
    assert payload["commands"]["reconstruct"]["enabled"] is False


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_post_ingest_and_poll_ledger_backed_run_to_success(tmp_path):
    root = _project(tmp_path)
    with _running_server(root) as server:
        status, _, submitted = _request(
            server,
            "POST",
            "/api/jobs",
            body={
                "command": "ingest",
                "parameters": {
                    "fps": 2,
                    "max_frames": 300,
                    "blur_threshold": 0,
                    "max_long_edge": 2560,
                },
            },
            headers=_authorization(server),
        )
        run = server.job_service.wait(submitted["run"]["id"], timeout=30)
        list_status, _, envelope = _request(server, "GET", "/api/runs?cursor=0")
        detail_status, _, detail = _request(
            server, "GET", f"/api/runs/{run.id}",
        )

    assert status == HTTPStatus.ACCEPTED
    assert submitted["created"] is True
    assert run.status == "succeeded"
    assert list_status == detail_status == HTTPStatus.OK
    assert envelope["items"][0]["id"] == run.id
    assert envelope["events"]
    assert envelope["cursor"] == envelope["events"][-1]["cursor"]
    assert detail["run"]["status"] == "succeeded"
    assert (root / "photos/photo.jpg").read_bytes() == b"http-photo"


@pytest.mark.parametrize(
    ("header", "value", "code"),
    [
        ("Host", "evil.example", "invalid_host"),
        ("Origin", "http://evil.example", "invalid_origin"),
        ("X-Nantai-Token", "wrong-token", "invalid_token"),
        ("Content-Type", "text/plain", "invalid_content_type"),
    ],
)
@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_write_request_rejects_rebinding_cross_origin_and_bad_auth(
    tmp_path, header, value, code,
):
    with _running_server(_project(tmp_path)) as server:
        headers = _authorization(server)
        headers[header] = value
        status, _, payload = _request(
            server,
            "POST",
            "/api/jobs",
            body={"command": "ingest", "parameters": {"padding": "x" * 60_000}},
            headers=headers,
        )

    assert status in {HTTPStatus.BAD_REQUEST, HTTPStatus.FORBIDDEN}
    assert payload["error"]["code"] == code


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_early_rejection_does_not_wait_forever_for_a_declared_body(tmp_path):
    with _running_server(_project(tmp_path)) as server:
        response = _headers_only_post(
            server,
            host="evil.example",
            content_length=64 * 1024,
        )

    assert response.startswith(b"HTTP/1.0 400")
    assert b'"code":"invalid_host"' in response
    assert server.job_service.ledger.list_runs() == []


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_oversized_rejection_does_not_drain_an_unbounded_declaration(tmp_path):
    with _running_server(_project(tmp_path)) as server:
        host = _authorization(server)["Host"]
        response = _headers_only_post(
            server,
            host=host,
            content_length=10**12,
        )

    assert response.startswith(b"HTTP/1.0 413")
    assert b'"code":"body_too_large"' in response
    assert server.job_service.ledger.list_runs() == []


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_write_request_rejects_unknown_fields_and_oversized_body(tmp_path):
    with _running_server(_project(tmp_path)) as server:
        status, _, payload = _request(
            server,
            "POST",
            "/api/jobs",
            body={"command": "ingest", "parameters": {}, "path": "outside"},
            headers=_authorization(server),
        )
        huge = {"command": "ingest", "parameters": {"padding": "x" * 2_000_000}}
        huge_status, _, huge_payload = _request(
            server,
            "POST",
            "/api/jobs",
            body=huge,
            headers={**_authorization(server), "X-Request-ID": "request-http-002"},
        )

    assert status == HTTPStatus.BAD_REQUEST
    assert payload["error"]["code"] == "invalid_request"
    assert huge_status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert huge_payload["error"]["code"] == "body_too_large"


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_write_mode_project_and_runs_ignore_legacy_json_ledger(tmp_path):
    root = _project(tmp_path)
    state = root / ".nantai-studio"
    state.mkdir()
    (state / "runs.json").write_text(json.dumps({
        "schema_version": 1,
        "items": [{
            "id": "legacy-forged", "command": "world", "status": "running",
            "adapter_kind": "local",
        }],
    }), encoding="utf-8")

    with _running_server(root) as server:
        _, _, project = _request(server, "GET", "/api/project")
        _, _, runs = _request(server, "GET", "/api/runs")

    assert project["active_run"] is None
    assert runs["items"] == []


def test_requested_jobs_degrade_to_read_only_when_durability_probe_fails(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        WindowsNtfsDurabilityBackend,
        "self_test",
        lambda _self: DurabilityReadiness(False, "probe failed", "NTFS"),
    )
    with _running_server(_project(tmp_path)) as server:
        _, _, capabilities = _request(server, "GET", "/api/capabilities")

    assert server.write_enabled is False
    assert server.request_token is None
    assert capabilities["mode"] == "read-only"
    assert "probe failed" in capabilities["reason"]


@pytest.mark.skipif(os.name != "nt", reason="B1 write capability is Windows/NTFS only")
def test_requested_jobs_degrade_to_read_only_while_writer_is_live(tmp_path):
    root = _project(tmp_path)
    (root / ".nantai-studio").mkdir()
    writer = ProjectFileLock(root / ".nantai-studio/writer.lock", role="writer")

    with writer, _running_server(root) as server:
        _, _, capabilities = _request(server, "GET", "/api/capabilities")

    assert server.write_enabled is False
    assert capabilities["mode"] == "read-only"
    assert "writer lock" in capabilities["reason"]
