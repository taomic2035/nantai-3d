"""Milestone A HTTP contract for fail-closed Studio capabilities."""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

from pipeline.studio_server import build_project_snapshot, make_server

READ_ONLY_REASON = "Job execution is not enabled in this Studio milestone."
COMMAND_IDS = ("ingest", "reconstruct", "world", "validate-assets")


@contextmanager
def _running_server(root: Path):
    server = make_server(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, body: bytes | None = None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    connection.request(method, path, body=body)
    response = connection.getresponse()
    payload = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, headers, payload


def _expected_capabilities() -> dict:
    return {
        "schema_version": 1,
        "mode": "read-only",
        "reason": READ_ONLY_REASON,
        "request_token": None,
        "single_writer": True,
        "commands": {
            command: {
                "enabled": False,
                "cancel": False,
                "retry": False,
                "reason": READ_ONLY_REASON,
            }
            for command in COMMAND_IDS
        },
    }


def test_get_capabilities_advertises_only_read_only_operations(tmp_path):
    with _running_server(tmp_path) as server:
        status, headers, payload = _request(server, "GET", "/api/capabilities")

    assert status == HTTPStatus.OK
    assert headers["cache-control"] == "no-store"
    assert json.loads(payload) == _expected_capabilities()


def test_head_capabilities_has_get_headers_without_a_body(tmp_path):
    with _running_server(tmp_path) as server:
        status, headers, payload = _request(server, "HEAD", "/api/capabilities")

    assert status == HTTPStatus.OK
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert int(headers["content-length"]) > 0
    assert payload == b""


def test_capability_discovery_does_not_enable_post_jobs(tmp_path):
    with _running_server(tmp_path) as server:
        _request(server, "GET", "/api/capabilities")
        status, headers, payload = _request(
            server,
            "POST",
            "/api/jobs",
            body=b'{"command":"reconstruct"}',
        )

    assert status == HTTPStatus.METHOD_NOT_ALLOWED
    assert headers["allow"] == "GET, HEAD"
    assert json.loads(payload)["error"]["code"] == "method_not_allowed"


def test_active_run_preserves_only_a_known_command(tmp_path):
    ledger_dir = tmp_path / ".nantai-studio"
    ledger_dir.mkdir()
    ledger_dir.joinpath("runs.json").write_text(
        json.dumps({
            "schema_version": 1,
            "items": [
                {
                    "id": "run-world",
                    "command": "world",
                    "status": "running",
                    "adapter_kind": "local",
                },
                {
                    "id": "run-unknown",
                    "command": "shell",
                    "status": "failed",
                    "adapter_kind": "local",
                },
            ],
        }),
        encoding="utf-8",
    )

    snapshot = build_project_snapshot(tmp_path)

    assert snapshot["active_run"] == {
        "id": "run-world", "command": "world", "status": "running",
    }

    ledger_dir.joinpath("runs.json").write_text(
        json.dumps({
            "schema_version": 1,
            "items": [
                {
                    "id": "run-unknown",
                    "command": "shell",
                    "status": "failed",
                    "adapter_kind": "local",
                },
            ],
        }),
        encoding="utf-8",
    )
    assert build_project_snapshot(tmp_path)["active_run"] is None


def test_compose_pipeline_requires_world_chunk_evidence_not_reconstruction(tmp_path):
    web_data = tmp_path / "web/data"
    web_data.mkdir(parents=True)
    properties = (
        "x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
        "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
    )
    header = ["ply", "format ascii 1.0", "element vertex 1"]
    header.extend(f"property float {name}" for name in properties)
    header.extend(["end_header", " ".join([*("0" for _ in range(10)), "1", "0", "0", "0"])])
    (web_data / "chunk.ply").write_text("\n".join(header) + "\n", encoding="ascii")
    (web_data / "manifest.json").write_text(
        json.dumps({
            "chunks": [{"id": "0_0", "ply_file": "chunk.ply", "point_count": 1}],
        }),
        encoding="utf-8",
    )

    assert build_project_snapshot(tmp_path)["pipeline"]["stitch"]["availability"] == "ready"

    (web_data / "manifest.json").unlink()
    assert build_project_snapshot(tmp_path)["pipeline"]["stitch"]["availability"] == "missing"
