from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "production-release-publish.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict:
    document = yaml.load(_text(), Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return document


def _job(job_id: str) -> dict:
    job = _workflow()["jobs"][job_id]
    assert isinstance(job, dict)
    return job


def _run_blocks(job: dict) -> list[str]:
    return [
        step["run"]
        for step in job["steps"]
        if isinstance(step, dict) and "run" in step
    ]


def _action_steps(job: dict, action: str) -> list[dict]:
    return [
        step
        for step in job["steps"]
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith(f"{action}@")
    ]


def _transaction_code() -> str:
    steps = [
        step
        for step in _job("public_publish")["steps"]
        if isinstance(step, dict)
        and step.get("name") == "Create draft, verify download, and publish"
    ]
    assert len(steps) == 1
    run = steps[0]["run"]
    match = re.search(
        r"# BEGIN NUMERIC_RELEASE_TRANSACTION\n"
        r"(?P<code>.*?)"
        r"# END NUMERIC_RELEASE_TRANSACTION",
        run,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group("code")


def test_dispatch_accepts_only_a_validated_version_via_environment() -> None:
    workflow = _workflow()
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]

    assert set(inputs) == {"version"}
    assert inputs["version"]["required"] == "true"

    for job_id in ("private_stage", "public_publish"):
        job = workflow["jobs"][job_id]
        assert job["env"]["VERSION"] == "${{ inputs.version }}"
        runs = "\n".join(_run_blocks(job))
        assert re.search(
            r'"\$VERSION" =~ \^v\[0-9\]\+\\\.\[0-9\]\+'
            r'\\\.\[0-9\]\+\(-\[0-9A-Za-z\.\-\]\+\)\?\$',
            runs,
        )
        assert '"$VERSION"' in runs


def test_untrusted_expressions_are_never_interpolated_in_shell() -> None:
    for job in _workflow()["jobs"].values():
        for run in _run_blocks(job):
            assert not re.search(
                r"\$\{\{\s*(?:inputs|secrets|github\.event\.(?:inputs|client_payload))\.",
                run,
            )


def test_no_remote_candidate_or_caller_supplied_identity_exists() -> None:
    workflow_text = _text()
    assert "archive_url" not in workflow_text
    assert "expected_sha256" not in workflow_text
    dispatch_inputs = _workflow()["on"]["workflow_dispatch"]["inputs"]
    assert "package_content_id" not in dispatch_inputs

    all_runs = "\n".join(
        run
        for job in _workflow()["jobs"].values()
        for run in _run_blocks(job)
    )
    assert not re.search(r"\bcurl\b|\bwget\b", all_runs)


def test_private_and_public_jobs_have_separate_runners_and_permissions() -> None:
    workflow = _workflow()
    assert set(workflow["jobs"]) == {"private_stage", "public_publish"}
    private = workflow["jobs"]["private_stage"]
    public = workflow["jobs"]["public_publish"]

    assert workflow["permissions"]["contents"] == "read"
    assert private["permissions"]["contents"] == "read"
    assert private["runs-on"] == [
        "self-hosted",
        "linux",
        "x64",
        "nantai-production-release",
    ]
    assert private["environment"] == "production-private"

    assert public["needs"] == "private_stage"
    assert public["permissions"]["contents"] == "write"
    assert public["runs-on"] == "ubuntu-latest"
    assert public["environment"] == "production"


def test_all_actions_are_sha_pinned_and_checkout_is_exact_without_credentials() -> None:
    allowed_actions = {
        "actions/checkout",
        "actions/setup-python",
        "actions/upload-artifact",
        "actions/download-artifact",
    }
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            uses = step.get("uses") if isinstance(step, dict) else None
            if not isinstance(uses, str) or uses.startswith("./"):
                continue
            match = re.fullmatch(r"([^@]+)@[0-9a-f]{40}", uses)
            assert match is not None
            assert match.group(1) in allowed_actions

        checkout_steps = _action_steps(job, "actions/checkout")
        assert len(checkout_steps) == 1
        checkout = checkout_steps[0]["with"]
        assert checkout["ref"] == "${{ github.sha }}"
        assert checkout["fetch-depth"] == "0"
        assert checkout["persist-credentials"] == "false"


def test_private_job_builds_then_stages_and_directly_verifies_pure_json() -> None:
    private = _job("private_stage")
    runs = "\n".join(_run_blocks(private))

    build = runs.index("scripts/build_production_release.py")
    stage = runs.index("scripts/stage_production_release_assets.py")
    verify = runs.index("scripts/verify_production_release_assets.py")
    assert build < stage < verify
    assert "make.py verify-production-assets" not in runs
    assert '> "$VERIFY_JSON"' in runs
    assert re.search(r"\^\[0-9a-f\]\{64\}\$", runs)
    assert private["outputs"]["package_content_id"].startswith(
        "${{ steps.verify_bundle.outputs."
    )


def test_private_artifact_upload_is_a_fixed_short_lived_four_file_allowlist() -> None:
    uploads = _action_steps(_job("private_stage"), "actions/upload-artifact")
    assert len(uploads) == 1
    upload = uploads[0]["with"]

    assert upload["name"] == "nantai-production-release"
    assert 1 <= int(upload["retention-days"]) <= 3
    assert upload["if-no-files-found"] == "error"
    assert set(upload["path"].splitlines()) == {
        "${{ runner.temp }}/production-release-assets/"
        "nantai-3d-${{ env.VERSION }}-runtime.zip",
        "${{ runner.temp }}/production-release-assets/"
        "nantai-3d-${{ env.VERSION }}-runtime.zip.sha256",
        "${{ runner.temp }}/production-release-assets/PRODUCTION-RELEASE.json",
        "${{ runner.temp }}/production-release-assets/SHA256SUMS.txt",
    }
    assert "*" not in upload["path"]


def test_public_job_has_no_private_acceptance_or_policy_capability() -> None:
    public_text = yaml.safe_dump(_job("public_publish"))
    for forbidden in (
        "ACCEPTANCE_ROOT",
        "ACCEPTANCE_ROOT_PATH",
        "PRIVACY_POLICY",
        "PRIVACY_POLICY_PATH",
        "production-private",
    ):
        assert forbidden not in public_text

    gh_token_steps = [
        step
        for step in _job("public_publish")["steps"]
        if isinstance(step, dict)
        and isinstance(step.get("env"), dict)
        and "GH_TOKEN" in step["env"]
    ]
    assert len(gh_token_steps) == 1
    assert "# BEGIN NUMERIC_RELEASE_TRANSACTION" in gh_token_steps[0]["run"]
    assert '["gh", "api", *arguments]' in gh_token_steps[0]["run"]


def test_public_job_reverifies_the_exact_artifact_before_release_transaction() -> None:
    public = _job("public_publish")
    downloads = _action_steps(public, "actions/download-artifact")
    assert len(downloads) == 1
    assert downloads[0]["with"]["name"] == "nantai-production-release"

    runs = "\n".join(_run_blocks(public))
    assert "python -m pip install" in runs
    assert "scripts/verify_production_release_assets.py" in runs
    assert "make.py verify-production-assets" not in runs
    assert re.search(r"\^\[0-9a-f\]\{64\}\$", runs)
    assert runs.index("scripts/verify_production_release_assets.py") < runs.index(
        "# BEGIN NUMERIC_RELEASE_TRANSACTION"
    )


def test_release_is_draft_verified_then_published_without_failure_mutation() -> None:
    workflow = _workflow()
    public_runs = "\n".join(_run_blocks(workflow["jobs"]["public_publish"]))
    transaction = _transaction_code()
    all_runs = "\n".join(
        run for job in workflow["jobs"].values() for run in _run_blocks(job)
    )

    create = transaction.index("release = _api_json(")
    upload = transaction.index("encoded_name = urllib.parse.quote", create)
    download = transaction.index("destination = downloaded_dir / name", upload)
    downloaded_verify = transaction.index(
        "scripts/verify_production_release_assets.py",
        download,
    )
    publish = transaction.index(
        'updated = _api_json(',
        downloaded_verify,
    )
    assert create < upload < download < downloaded_verify < publish

    assert "gh release " not in public_runs
    assert "target_commitish" in transaction
    assert "make_latest=false" in transaction
    assert "make_latest=true" in transaction
    assert "draft=true" in transaction
    assert "draft=false" in transaction[publish:]
    assert "PRODUCTION-RELEASE.json" in transaction
    assert "SHA256SUMS.txt" in transaction
    assert 'f"{release_endpoint}/{release_id}"' in transaction
    assert "--hostname" not in transaction
    assert (
        'f"https://uploads.github.com/repos/{repository}/releases/"'
        in transaction
    )
    assert 'f"{release_id}/assets?name={encoded_name}"' in transaction
    assert (
        'f"repos/{repository}/releases/assets/{asset_id}"'
        in transaction
    )
    assert "gh release delete" not in public_runs
    assert "--cleanup-tag" not in public_runs
    assert "git push" not in all_runs
    assert "release_by_tag_endpoint" in transaction
    assert "tag_get_endpoint" in transaction
    assert "_compare_files(bundle_dir / name, destination)" in transaction
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert workflow["concurrency"]["group"] == (
        "production-release-${{ inputs.version }}"
    )

    assert "release_id = release[\"id\"]" in transaction
    assert "release[\"id\"] != release_id" in transaction
    assert 'release.get("tag_name") != version' in transaction
    assert 'release.get("target_commitish") != source_sha' in transaction
    assert 'release.get("draft") is not draft' in transaction
    assert 'release.get("name") != expected_name' in transaction
    assert 'release.get("prerelease") is not False' in transaction
    assert 'not isinstance(release.get("body"), str)' in transaction
    assert 'release.get("body") != expected_body' in transaction
    assert (
        'f"Workflow transaction: {run_id}-{run_attempt}@{source_sha}"'
        in transaction
    )
    assert transaction.count("_assert_transaction(draft=True)") >= 4
    assert "_assert_transaction(draft=False)" in transaction
    assert "assets_by_name != uploaded_assets" in transaction
    assert "not isinstance(result, dict)" in transaction
    assert '"DELETE"' not in transaction
    assert "retained for manual cleanup" in transaction


def test_real_gh_parses_full_upload_url_as_uploads_host(
    tmp_path: Path,
) -> None:
    gh = shutil.which("gh")
    if gh is None:
        pytest.skip("GitHub CLI is unavailable")
    captured: list[bytes] = []

    class CaptureProxy(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            request = self.request.recv(4096)
            captured.append(request.split(b"\r\n", 1)[0])
            self.request.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )

    with socketserver.TCPServer(
        ("127.0.0.1", 0),
        CaptureProxy,
    ) as proxy:
        proxy.timeout = 10
        host, port = proxy.server_address
        proxy_url = f"http://{host}:{port}"
        thread = threading.Thread(
            target=proxy.handle_request,
            daemon=True,
        )
        thread.start()
        environment = os.environ.copy()
        environment.update(
            {
                "ALL_PROXY": "",
                "GH_DEBUG": "api",
                "GH_TOKEN": "invalid-test-token",
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "NO_PROXY": "",
                "all_proxy": "",
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "no_proxy": "",
            }
        )
        endpoint = (
            "https://uploads.github.com/repos/test/repo/releases/"
            "101/assets?name=asset.zip"
        )

        completed = subprocess.run(
            [
                gh,
                "api",
                "--method",
                "POST",
                "--input",
                os.devnull,
                endpoint,
            ],
            check=False,
            capture_output=True,
            env=environment,
            timeout=15,
        )
        thread.join(timeout=12)

    assert completed.returncode != 0
    assert captured == [b"CONNECT uploads.github.com:443 HTTP/1.1"]


@pytest.mark.parametrize(
    ("replacement_phase", "numeric_get_trigger", "expected_uploads"),
    (
        ("upload", 1, 0),
        ("download", 6, 4),
        ("publish", 10, 4),
        ("happy", None, 4),
    ),
)
def test_numeric_transaction_executable_state_machine(
    tmp_path: Path,
    capsys,
    replacement_phase: str,
    numeric_get_trigger: int | None,
    expected_uploads: int,
) -> None:
    version = "v1.2.3"
    source_sha = "a" * 40
    content_id = "b" * 64
    marker = f"Workflow transaction: 123-2@{source_sha}"
    expected_name = f"Nantai 3D {version}"
    expected_body = (
        "This release was staged by the official Nantai "
        "production workflow from the protected private "
        f"staging environment at source commit {source_sha}.\n\n"
        "The downloaded verifier confirms internal consistency "
        "of the four-file bundle and package content ID "
        f"{content_id}. It does not independently prove "
        "source authenticity, private acceptance, rights, real "
        "reconstruction, measured alignment, or Viewer QA.\n\n"
        f"{marker}\n"
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    archive_name = f"nantai-3d-{version}-runtime.zip"
    asset_names = (
        archive_name,
        f"{archive_name}.sha256",
        "PRODUCTION-RELEASE.json",
        "SHA256SUMS.txt",
    )
    for index, name in enumerate(asset_names):
        (bundle / name).write_bytes(f"asset-{index}".encode("ascii"))
    downloaded = tmp_path / "downloaded"
    script = tmp_path / "numeric_release_transaction.py"
    script.write_text(_transaction_code(), encoding="utf-8")
    mutations: list[tuple[str, str]] = []
    api_calls: list[tuple[str, str]] = []
    state = {
        "tag_created": False,
        "replacement_created": False,
        "numeric_gets": 0,
        "verifier_called": False,
    }
    uploaded_assets: dict[str, tuple[int, bytes]] = {}
    original_release = {
        "id": 101,
        "tag_name": version,
        "target_commitish": source_sha,
        "draft": True,
        "name": expected_name,
        "prerelease": False,
        "body": expected_body,
    }
    replacement = {
        "id": 202,
        "tag_name": version,
        "target_commitish": source_sha,
        "draft": True,
        "body": "replacement release outside this transaction",
    }

    def completed(command, payload=None, *, returncode=0, stderr=b""):
        stdout = (
            b""
            if payload is None
            else json.dumps(payload).encode("utf-8")
        )
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout,
            stderr,
        )

    def fake_run(command, **_kwargs):
        if command[0] == sys.executable:
            state["verifier_called"] = True
            return completed(
                command,
                {
                    "package_content_id": content_id,
                    "archive_sha256": "c" * 64,
                },
            )
        assert command[:2] == ["gh", "api"]
        arguments = command[2:]
        method = "GET"
        if "--method" in arguments:
            method = arguments[arguments.index("--method") + 1]
        endpoints = [
            value
            for value in arguments
            if isinstance(value, str)
            and (
                value.startswith("repos/")
                or value.startswith("https://uploads.github.com/repos/")
            )
        ]
        assert endpoints
        endpoint = endpoints[-1]
        api_calls.append((method, endpoint))
        if method != "GET":
            mutations.append((method, endpoint))

        if method == "POST" and endpoint == "repos/test/repo/git/refs":
            state["tag_created"] = True
            return completed(
                command,
                {"object": {"sha": source_sha}},
            )
        if method == "POST" and endpoint == "repos/test/repo/releases":
            return completed(command, original_release)
        if (
            method == "POST"
            and endpoint.startswith(
                "https://uploads.github.com/repos/test/repo/"
                "releases/101/assets?name="
            )
        ):
            name = unquote(endpoint.partition("?name=")[2])
            source = Path(arguments[arguments.index("--input") + 1])
            asset_id = 1000 + len(uploaded_assets)
            uploaded_assets[name] = (asset_id, source.read_bytes())
            return completed(
                command,
                {"id": asset_id, "name": name},
            )
        if endpoint == f"repos/test/repo/git/ref/tags/{version}":
            if state["tag_created"]:
                return completed(
                    command,
                    {"object": {"sha": source_sha}},
                )
            return completed(
                command,
                returncode=1,
                stderr=b"gh: HTTP 404\n",
            )
        if endpoint == f"repos/test/repo/releases/tags/{version}":
            if state["replacement_created"]:
                return completed(command, replacement)
            return completed(
                command,
                returncode=1,
                stderr=b"gh: HTTP 404\n",
            )
        if method == "PATCH" and endpoint == "repos/test/repo/releases/101":
            original_release["draft"] = False
            return completed(command, original_release)
        if method == "GET" and endpoint == "repos/test/repo/releases/101":
            if state["replacement_created"]:
                return completed(
                    command,
                    returncode=1,
                    stderr=b"gh: HTTP 404\n",
                )
            state["numeric_gets"] += 1
            if (
                numeric_get_trigger is not None
                and state["numeric_gets"] == numeric_get_trigger
            ):
                state["replacement_created"] = True
                return completed(
                    command,
                    returncode=1,
                    stderr=b"gh: HTTP 404\n",
                )
            return completed(command, original_release)
        if endpoint == "repos/test/repo/releases/101/assets?per_page=100":
            return completed(
                command,
                [
                    {"id": asset_id, "name": name}
                    for name, (asset_id, _payload) in uploaded_assets.items()
                ],
            )
        if endpoint.startswith("repos/test/repo/releases/assets/"):
            asset_id = int(endpoint.rsplit("/", 1)[1])
            payload = next(
                payload
                for _name, (observed_id, payload) in uploaded_assets.items()
                if observed_id == asset_id
            )
            _kwargs["stdout"].write(payload)
            return completed(command)
        if method == "DELETE":
            return completed(
                command,
                returncode=1,
                stderr=b"unexpected delete",
            )
        raise AssertionError((method, endpoint, arguments))

    environment = {
        "VERSION": version,
        "EXPECTED_CONTENT_ID": content_id,
        "GITHUB_REPOSITORY": "test/repo",
        "GITHUB_SHA": source_sha,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
    }
    with (
        patch.dict(os.environ, environment, clear=False),
        patch.object(
            sys,
            "argv",
            [str(script), str(bundle), str(downloaded)],
        ),
        patch("subprocess.run", side_effect=fake_run),
    ):
        if replacement_phase == "happy":
            runpy.run_path(str(script), run_name="__main__")
            captured = None
        else:
            with pytest.raises(SystemExit) as raised:
                runpy.run_path(str(script), run_name="__main__")
            captured = raised.value

    if captured is not None:
        assert captured.code == 1
    assert mutations[:2] == [
        ("POST", "repos/test/repo/git/refs"),
        ("POST", "repos/test/repo/releases"),
    ]
    upload_mutations = [
        endpoint
        for method, endpoint in mutations
        if method == "POST" and "/assets?name=" in endpoint
    ]
    assert len(upload_mutations) == expected_uploads
    assert not any(method == "DELETE" for method, _ in mutations)
    patches = [
        endpoint
        for method, endpoint in mutations
        if method == "PATCH"
    ]
    assert patches == (
        ["repos/test/repo/releases/101"]
        if replacement_phase == "happy"
        else []
    )
    assert not any("/202" in endpoint for _method, endpoint in api_calls)
    assert state["replacement_created"] is (
        replacement_phase != "happy"
    )
    assert state["verifier_called"] is (
        replacement_phase in {"publish", "happy"}
    )
    stderr = capsys.readouterr().err
    if replacement_phase == "happy":
        assert stderr == ""
        assert {
            path.name for path in downloaded.iterdir()
        } == set(asset_names)
        numeric_downloads = [
            endpoint
            for method, endpoint in api_calls
            if method == "GET"
            and endpoint.startswith(
                "repos/test/repo/releases/assets/"
            )
        ]
        assert len(numeric_downloads) == 4
    else:
        assert "Production release transaction failed" in stderr
