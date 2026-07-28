from __future__ import annotations

import re
from pathlib import Path

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
    assert "gh release create" in gh_token_steps[0]["run"]


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
        "gh release create"
    )


def test_release_is_draft_verified_then_published_with_failure_cleanup() -> None:
    workflow = _workflow()
    public_runs = "\n".join(_run_blocks(workflow["jobs"]["public_publish"]))
    all_runs = "\n".join(
        run for job in workflow["jobs"].values() for run in _run_blocks(job)
    )

    create = public_runs.index("gh release create")
    download = public_runs.index("gh release download", create)
    downloaded_verify = public_runs.index(
        "scripts/verify_production_release_assets.py",
        download,
    )
    publish = public_runs.index("gh release edit", downloaded_verify)
    assert create < download < downloaded_verify < publish

    assert "--draft" in public_runs
    assert "--latest=false" in public_runs
    assert '--target "$GITHUB_SHA"' in public_runs
    assert 'gh release upload "$VERSION"' in public_runs
    assert '"$BUNDLE_DIR/$archive_name"' in public_runs
    assert '"$BUNDLE_DIR/$archive_name.sha256"' in public_runs
    assert '"$BUNDLE_DIR/PRODUCTION-RELEASE.json"' in public_runs
    assert '"$BUNDLE_DIR/SHA256SUMS.txt"' in public_runs
    assert "--draft=false" in public_runs
    assert "--latest" in public_runs[publish:]
    assert "trap " in public_runs
    assert 'repos/$GITHUB_REPOSITORY/releases/$release_id' in public_runs
    assert 'repos/$GITHUB_REPOSITORY/git/refs/tags/$VERSION' in public_runs
    assert "gh release delete" not in public_runs
    assert "--cleanup-tag" not in public_runs
    assert "git push" not in all_runs
    assert "releases/tags/$VERSION" in public_runs
    assert "git/ref/tags" in public_runs
    assert 'cmp -- "$source" "$downloaded"' in public_runs
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    assert workflow["concurrency"]["group"] == (
        "production-release-${{ inputs.version }}"
    )

    tag_create = public_runs.index(
        'gh api --method POST "repos/$GITHUB_REPOSITORY/git/refs"'
    )
    release_create = public_runs.index("gh release create")
    release_identity = public_runs.index('release_id="$(', release_create)
    upload = public_runs.index("gh release upload", release_identity)
    assert tag_create < release_create < release_identity < upload
    assert '--verify-tag' in public_runs[release_create:upload]
    assert "release_is_owned_draft" in public_runs
    assert 'release.get("draft") is not True' in public_runs
    assert 'str(release.get("id")) != expected_id' in public_runs
    assert 'tag_sha_after_release_delete" == "$GITHUB_SHA"' in public_runs
    release_delete = public_runs.index(
        '"repos/$GITHUB_REPOSITORY/releases/$release_id"'
    )
    tag_refetch = public_runs.index(
        '"repos/$GITHUB_REPOSITORY/git/ref/tags/$VERSION"',
        release_delete,
    )
    tag_delete = public_runs.index(
        '"repos/$GITHUB_REPOSITORY/git/refs/tags/$VERSION"',
        tag_refetch,
    )
    assert release_delete < tag_refetch < tag_delete
