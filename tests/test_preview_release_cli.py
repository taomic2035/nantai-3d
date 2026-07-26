from __future__ import annotations

import json
from pathlib import Path

from pipeline.preview_release import (
    ReleaseBuild,
    ReleaseVerification,
    ReleaseVerificationError,
)
from scripts import build_preview_release, verify_preview_release

SOURCE_COMMIT = "a" * 40


def test_git_tracked_files_decodes_sorts_and_drops_empty(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        build_preview_release.subprocess,
        "check_output",
        lambda *_args, **_kwargs: b"web/viewer/main.js\0README.md\0",
    )

    assert build_preview_release._git_tracked_files(tmp_path) == [
        "README.md",
        "web/viewer/main.js",
    ]


def test_build_cli_uses_git_identity_and_prints_machine_summary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    output = tmp_path / "runtime.zip"
    lock = root / "release/preview2-inputs.json"
    lock.parent.mkdir()
    lock.write_text("{}\n", encoding="utf-8")
    observed = {}

    monkeypatch.setattr(build_preview_release, "_git_source_commit", lambda _root: SOURCE_COMMIT)
    monkeypatch.setattr(
        build_preview_release,
        "_git_tracked_files",
        lambda _root: ["README.md", "pipeline/runtime.py"],
    )
    clean_checks = []
    monkeypatch.setattr(
        build_preview_release,
        "_git_assert_release_inputs_clean",
        lambda repo_root: clean_checks.append(repo_root),
    )

    def fake_build(repo_root, output_path, **kwargs):
        observed.update(
            repo_root=repo_root,
            output_path=output_path,
            kwargs=kwargs,
        )
        return ReleaseBuild(
            archive_path=Path(output_path),
            archive_sha256="b" * 64,
            package_content_id="c" * 64,
            artifact_count=12,
            total_bytes=345,
            asset_count=1,
            world_chunk_count=1,
            gaussian_count=5,
        )

    monkeypatch.setattr(build_preview_release, "build_release_archive", fake_build)

    code = build_preview_release.main(
        [
            "--repo-root",
            str(root),
            "--input-lock",
            str(lock),
            "--output",
            str(output),
            "--json",
        ]
    )

    assert code == 0
    assert observed == {
        "repo_root": root,
        "output_path": output,
        "kwargs": {
            "input_lock_path": lock,
            "source_commit": SOURCE_COMMIT,
            "tracked_files": ["README.md", "pipeline/runtime.py"],
        },
    }
    assert clean_checks == [root]
    assert json.loads(capsys.readouterr().out) == {
        "archive": str(output),
        "archive_sha256": "b" * 64,
        "package_content_id": "c" * 64,
        "artifact_count": 12,
        "total_bytes": 345,
        "asset_count": 1,
        "world_chunk_count": 1,
        "gaussian_count": 5,
    }


def test_build_cli_returns_two_for_fail_closed_input(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(build_preview_release, "_git_source_commit", lambda _root: SOURCE_COMMIT)
    monkeypatch.setattr(build_preview_release, "_git_tracked_files", lambda _root: [])
    monkeypatch.setattr(
        build_preview_release,
        "_git_assert_release_inputs_clean",
        lambda _root: None,
    )
    monkeypatch.setattr(
        build_preview_release,
        "build_release_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ReleaseVerificationError("source manifest SHA-256 mismatch")
        ),
    )

    code = build_preview_release.main(
        [
            "--repo-root",
            str(root),
            "--input-lock",
            "release/preview2-inputs.json",
            "--output",
            str(tmp_path / "runtime.zip"),
        ]
    )

    assert code == 2
    assert "source manifest SHA-256 mismatch" in capsys.readouterr().err


def test_build_cli_returns_two_for_dirty_release_owned_source(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(build_preview_release, "_git_source_commit", lambda _root: SOURCE_COMMIT)
    monkeypatch.setattr(build_preview_release, "_git_tracked_files", lambda _root: [])
    monkeypatch.setattr(
        build_preview_release,
        "_git_assert_release_inputs_clean",
        lambda _root: (_ for _ in ()).throw(
            ReleaseVerificationError("dirty release-owned source: web/viewer/main.js")
        ),
    )

    assert build_preview_release.main(["--repo-root", str(root)]) == 2
    assert "dirty release-owned source" in capsys.readouterr().err


def test_verify_cli_selects_archive_and_tree_modes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    archive = tmp_path / "runtime.zip"
    archive.write_bytes(b"fixture")
    tree = tmp_path / "tree"
    tree.mkdir()
    observed = []
    report = ReleaseVerification(
        valid=True,
        version="v1.0.0-preview.2",
        source_commit=SOURCE_COMMIT,
        package_content_id="c" * 64,
        artifact_count=12,
        total_bytes=345,
        scene_trust_effect="none",
    )
    monkeypatch.setattr(
        verify_preview_release,
        "verify_release_archive",
        lambda path: observed.append(("archive", Path(path))) or report,
    )
    monkeypatch.setattr(
        verify_preview_release,
        "verify_release_tree",
        lambda path: observed.append(("tree", Path(path))) or report,
    )

    assert verify_preview_release.main([str(archive), "--json"]) == 0
    archive_output = json.loads(capsys.readouterr().out)
    assert verify_preview_release.main([str(tree), "--json"]) == 0
    tree_output = json.loads(capsys.readouterr().out)

    assert observed == [("archive", archive), ("tree", tree)]
    assert archive_output == tree_output == {
        "valid": True,
        "version": "v1.0.0-preview.2",
        "source_commit": SOURCE_COMMIT,
        "package_content_id": "c" * 64,
        "artifact_count": 12,
        "total_bytes": 345,
        "scene_trust_effect": "none",
        "errors": [],
    }


def test_verify_cli_returns_two_for_corrupt_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    archive = tmp_path / "runtime.zip"
    archive.write_bytes(b"corrupt")
    monkeypatch.setattr(
        verify_preview_release,
        "verify_release_archive",
        lambda _path: (_ for _ in ()).throw(
            ReleaseVerificationError("changed protected artifact")
        ),
    )

    assert verify_preview_release.main([str(archive)]) == 2
    assert "changed protected artifact" in capsys.readouterr().err
