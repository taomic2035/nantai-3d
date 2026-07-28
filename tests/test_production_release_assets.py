from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import shutil
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.production_release_assets as assets_module
import scripts.stage_production_release_assets as assets_cli
import scripts.verify_production_release_assets as verify_assets_cli
from pipeline.production_release_assets import (
    ProductionReleaseAssets,
    ProductionReleaseAssetsError,
    ProductionReleaseAssetsVerification,
    stage_production_release_assets,
    verify_production_release_assets,
)
from pipeline.production_release_builder import (
    ProductionReleaseBuild,
    ProductionReleaseBuilderError,
    ProductionReleaseSourceIdentity,
)
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
    build_production_receipt,
)
from pipeline.production_release_verifier import (
    verify_production_release_archive,
)
from pipeline.release_archive import (
    canonical_json_bytes,
    stable_regular_file_digest,
)
from tests.production_release_fixtures import (
    modeled_artifact_records,
    modeled_entrypoints,
    modeled_payloads,
    modeled_public_evidence,
    write_modeled_production_archive,
    write_modeled_production_tree,
)
from tests.test_production_release_builder import _committed_runtime_repo

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RELEASE_GUIDE = _REPO_ROOT / "release" / "production-verify-and-run.md"


def _write_real_contract_tree(
    root: Path,
    *,
    version: str = "v1.0.0",
    source_commit: str = "a" * 40,
    acceptance_report_sha256: str = "a" * 64,
) -> dict[str, object]:
    root.mkdir()
    payloads = modeled_payloads()
    public_evidence = modeled_public_evidence()
    public_evidence["fixture_kind"] = None
    public_evidence["acceptance"]["report_sha256"] = (
        acceptance_report_sha256
    )
    payloads["evidence/public-evidence.json"] = (
        "public-evidence",
        canonical_json_bytes(public_evidence),
    )
    artifacts = modeled_artifact_records()
    for artifact in artifacts:
        if artifact["path"] == "evidence/public-evidence.json":
            payload = payloads["evidence/public-evidence.json"][1]
            artifact["bytes"] = len(payload)
            artifact["sha256"] = hashlib.sha256(payload).hexdigest()
    for relative, (_role, payload) in payloads.items():
        destination = root.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    receipt = build_production_receipt(
        version=version,
        source_commit=source_commit,
        artifacts=artifacts,
        protected_roots=("web", "scripts", "pipeline", "evidence"),
        entrypoints=modeled_entrypoints(),
        public_evidence=public_evidence,
    )
    receipt_bytes = canonical_json_bytes(receipt)
    (root / PRODUCTION_RELEASE_NAME).write_bytes(receipt_bytes)
    checksum_rows = [
        f"{row['sha256']}  {row['path']}\n"
        for row in receipt["artifacts"]
    ]
    checksum_rows.append(
        f"{hashlib.sha256(receipt_bytes).hexdigest()}  "
        f"{PRODUCTION_RELEASE_NAME}\n"
    )
    (root / CHECKSUMS_NAME).write_bytes(
        "".join(sorted(checksum_rows)).encode("ascii")
    )
    return receipt


def _write_real_contract_archive(
    root: Path,
    *,
    version: str = "v1.0.0",
    source_commit: str = "a" * 40,
    acceptance_report_sha256: str = "a" * 64,
) -> tuple[Path, dict[str, object]]:
    root.mkdir()
    runtime = root / "runtime"
    receipt = _write_real_contract_tree(
        runtime,
        version=version,
        source_commit=source_commit,
        acceptance_report_sha256=acceptance_report_sha256,
    )
    archive = root / "runtime.zip"
    write_modeled_production_archive(
        runtime,
        archive,
        wrapper=f"nantai-3d-{version}",
    )
    return archive, receipt


def _privacy_policy(path: Path, needle: bytes = b"private-needle") -> Path:
    path.write_bytes(
        canonical_json_bytes(
            {
                "schema": "nantai.production-privacy-policy.v1",
                "needles": [
                    {
                        "encoding": "base64",
                        "value": base64.b64encode(needle).decode("ascii"),
                    }
                ],
            }
        )
    )
    return path


def _stage_kwargs(
    tmp_path: Path,
    *,
    archive: Path,
    policy: Path,
    output: Path,
) -> dict[str, object]:
    return {
        "repo_root": tmp_path / "repo",
        "acceptance_root": tmp_path / "accepted",
        "version": "v1.0.0",
        "archive_path": archive,
        "privacy_policy_path": policy,
        "output_dir": output,
    }


def _assert_not_published(output: Path) -> None:
    assert not output.exists()
    assert not tuple(output.parent.glob(f".{output.name}.*.staging"))


def _bind_acceptance_rebuild(
    monkeypatch,
    rebuilt_source: Path,
    *,
    identities: list[ProductionReleaseSourceIdentity] | None = None,
) -> list[dict[str, object]]:
    identity_values = list(
        identities
        if identities is not None
        else [
            ProductionReleaseSourceIdentity(
                source_commit="a" * 40,
                tracked_files=("LICENSE", "pipeline/runtime.py"),
            ),
            ProductionReleaseSourceIdentity(
                source_commit="a" * 40,
                tracked_files=("LICENSE", "pipeline/runtime.py"),
            ),
        ]
    )
    builder_calls: list[dict[str, object]] = []

    def _resolve(_repo_root: Path) -> ProductionReleaseSourceIdentity:
        assert identity_values, "unexpected source identity resolution"
        return identity_values.pop(0)

    def _build(**kwargs) -> ProductionReleaseBuild:
        builder_calls.append(kwargs)
        output_path = kwargs["output_path"]
        shutil.copyfile(rebuilt_source, output_path)
        digest = stable_regular_file_digest(output_path)
        output_path.with_suffix(f"{output_path.suffix}.sha256").write_text(
            f"{digest.sha256}  {output_path.name}\n",
            encoding="ascii",
        )
        verification = verify_production_release_archive(output_path)
        return ProductionReleaseBuild(
            archive_path=output_path,
            archive_sha256=digest.sha256,
            package_content_id=verification.package_content_id,
            artifact_count=verification.artifact_count,
            total_bytes=verification.total_bytes,
            scene_identity="scene-" + "c" * 64,
            acceptance_report_sha256="d" * 64,
        )

    monkeypatch.setattr(
        assets_module,
        "resolve_production_release_source_identity",
        _resolve,
    )
    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        _build,
    )
    return builder_calls


def _patch_rebuild_match(monkeypatch, rebuilt_source: Path) -> None:
    _bind_acceptance_rebuild(monkeypatch, rebuilt_source)


def _patch_rebuild_raise(monkeypatch, exc: Exception) -> None:
    identity = ProductionReleaseSourceIdentity(
        source_commit="a" * 40,
        tracked_files=("LICENSE", "pipeline/runtime.py"),
    )
    monkeypatch.setattr(
        assets_module,
        "resolve_production_release_source_identity",
        lambda _repo_root: identity,
    )

    def _raise(**kwargs) -> ProductionReleaseBuild:
        raise exc

    monkeypatch.setattr(
        assets_module, "build_production_release_archive", _raise
    )


def _forbid_acceptance_rebuild(monkeypatch) -> list[str]:
    calls: list[str] = []

    def _resolve(_repo_root: Path) -> ProductionReleaseSourceIdentity:
        calls.append("resolve")
        raise AssertionError("source resolver must not be called")

    def _build(**kwargs) -> ProductionReleaseBuild:
        calls.append("build")
        raise AssertionError("release builder must not be called")

    monkeypatch.setattr(
        assets_module,
        "resolve_production_release_source_identity",
        _resolve,
    )
    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        _build,
    )
    return calls


def test_stable_regular_files_equal_compares_all_bytes(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left.bin"
    right = tmp_path / "right.bin"
    left.write_bytes(b"same bytes")
    right.write_bytes(b"same bytes")
    assert assets_module._stable_regular_files_equal(left, right) is True

    right.write_bytes(b"diff bytes")
    assert assets_module._stable_regular_files_equal(left, right) is False


def test_stage_exports_only_four_verified_public_assets(
    tmp_path: Path, monkeypatch
) -> None:
    source, receipt = _write_real_contract_archive(tmp_path / "candidate")
    tree = source.parent / "runtime"
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    acceptance = tmp_path / "accepted"
    repo = tmp_path / "repo"
    output = tmp_path / "release-assets"
    builder_calls = _bind_acceptance_rebuild(monkeypatch, source)

    result = stage_production_release_assets(
        **_stage_kwargs(
            tmp_path,
            archive=source,
            policy=policy,
            output=output,
        )
    )

    archive_name = "nantai-3d-v1.0.0-runtime.zip"
    output_names = [path.name for path in output.iterdir()]
    assert len(output_names) == 4
    assert not any("acceptance-rebuild" in name for name in output_names)
    assert sorted(output_names) == [
        PRODUCTION_RELEASE_NAME,
        CHECKSUMS_NAME,
        archive_name,
        f"{archive_name}.sha256",
    ]
    assert (output / archive_name).read_bytes() == source.read_bytes()
    assert (
        output / PRODUCTION_RELEASE_NAME
    ).read_bytes() == (tree / PRODUCTION_RELEASE_NAME).read_bytes()
    assert (
        output / CHECKSUMS_NAME
    ).read_bytes() == (tree / CHECKSUMS_NAME).read_bytes()
    archive_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert (output / f"{archive_name}.sha256").read_text(
        encoding="ascii"
    ) == f"{archive_sha}  {archive_name}\n"
    assert result.archive_sha256 == archive_sha
    assert result.package_content_id == receipt["package"]["content_id"]
    assert result.privacy_valid is True
    assert len(builder_calls) == 1
    builder_call = builder_calls[0]
    assert builder_call["repo_root"] == repo.absolute()
    assert builder_call["acceptance_root"] == acceptance.absolute()
    assert builder_call["version"] == "v1.0.0"
    assert builder_call["source_commit"] == "a" * 40
    assert builder_call["tracked_files"] == (
        "LICENSE",
        "pipeline/runtime.py",
    )
    rebuilt_output = builder_call["output_path"]
    assert isinstance(rebuilt_output, Path)
    assert rebuilt_output.is_absolute()
    assert rebuilt_output.name == ".acceptance-rebuild.zip"
    verification = verify_production_release_assets(output)
    assert verification.valid is True
    assert verification.package_content_id == receipt["package"]["content_id"]
    assert verification.archive_sha256 == archive_sha


def test_stage_rejects_private_builder_residue_before_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(monkeypatch, source)
    original_build = assets_module.build_production_release_archive

    def _build_with_residue(**kwargs) -> ProductionReleaseBuild:
        result = original_build(**kwargs)
        private = (
            kwargs["output_path"].parent
            / ".acceptance-rebuild.zip.staging"
        )
        private.mkdir()
        (private / "private.txt").write_text("private", encoding="ascii")
        return result

    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        _build_with_residue,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="staged release validation",
    ) as raised:
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    assert str(raised.value) == "Production staged release validation failed"
    _assert_not_published(output)


@pytest.mark.parametrize(
    ("mutation", "replacement"),
    [
        ("archive_path", Path("unexpected.zip")),
        ("archive_sha256", "0" * 64),
    ],
)
def test_stage_rejects_builder_result_identity_mismatch(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
    replacement: object,
) -> None:
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(monkeypatch, source)
    original_build = assets_module.build_production_release_archive

    def _build_with_wrong_result(**kwargs) -> ProductionReleaseBuild:
        result = original_build(**kwargs)
        return replace(result, **{mutation: replacement})

    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        _build_with_wrong_result,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    _assert_not_published(output)


def test_stage_rejects_modeled_contract_fixture(
    tmp_path: Path, monkeypatch
) -> None:
    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    _patch_rebuild_match(monkeypatch, source)
    output = tmp_path / "release-assets"

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="modeled contract",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    _assert_not_published(output)


def test_stage_rejects_privacy_findings_without_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(
        tmp_path / "privacy-policy.json",
        needle=b"<h1>Studio</h1>",
    )
    _patch_rebuild_match(monkeypatch, source)
    output = tmp_path / "release-assets"

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="privacy audit failed",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    _assert_not_published(output)


def test_stage_never_replaces_existing_output_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel_bytes = b"keep\x00byte-identical\r\n"
    sentinel.write_bytes(sentinel_bytes)
    trust_calls = _forbid_acceptance_rebuild(monkeypatch)

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="must be absent",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    assert trust_calls == []
    assert sentinel.read_bytes() == sentinel_bytes
    assert sorted(path.name for path in output.iterdir()) == ["keep.txt"]
    assert not tuple(output.parent.glob(f".{output.name}.*.staging"))


def test_stage_rejects_junction_output_parent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output_parent = tmp_path / "junction-parent"
    output_parent.mkdir()
    sentinel = output_parent / "keep.bin"
    sentinel_bytes = b"junction-parent-sentinel\x00"
    sentinel.write_bytes(sentinel_bytes)
    output = output_parent / "release-assets"
    trust_calls = _forbid_acceptance_rebuild(monkeypatch)
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == output_parent or original(self),
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="real directory",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    assert trust_calls == []
    assert sentinel.read_bytes() == sentinel_bytes
    _assert_not_published(output)


def test_stage_rejects_reparse_output_parent_without_is_junction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_parent = tmp_path / "reparse-parent"
    output_parent.mkdir()
    output = output_parent / "release-assets"
    observed = output_parent.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == output_parent:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=getattr(
                    stat,
                    "FILE_ATTRIBUTE_REPARSE_POINT",
                    0x400,
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="real directory",
    ):
        assets_module._real_absent_output(output)


def test_candidate_copy_rejects_reparse_source_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ancestor = tmp_path / "alias"
    ancestor.mkdir()
    source = ancestor / "candidate.zip"
    source.write_bytes(b"candidate")
    destination = tmp_path / "copy.zip"
    observed = ancestor.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="unavailable|unsafe|regular non-link",
    ):
        assets_module._copy_stable_regular_file(source, destination)

    assert not destination.exists()


def test_verify_bundle_rejects_reparse_root_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ancestor = tmp_path / "alias"
    root = ancestor / "release-assets"
    root.mkdir(parents=True)
    observed = ancestor.lstat()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path):
        if path == ancestor:
            return SimpleNamespace(
                st_mode=observed.st_mode,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="missing or unsafe",
    ):
        verify_production_release_assets(root)


def test_verify_rejects_junction_bundle_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _patch_rebuild_match(monkeypatch, source)
    stage_production_release_assets(
        **_stage_kwargs(
            tmp_path,
            archive=source,
            policy=policy,
            output=output,
        )
    )
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda self: self == output or original(self),
        raising=False,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="missing or unsafe",
    ):
        verify_production_release_assets(output)


@pytest.mark.parametrize(
    "mutation",
    ("extra", "sidecar", "receipt", "checksums"),
)
def test_verify_four_asset_bundle_rejects_mixed_or_extra_bytes(
    tmp_path: Path,
    monkeypatch,
    mutation: str,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _patch_rebuild_match(monkeypatch, source)
    stage_production_release_assets(
        **_stage_kwargs(
            tmp_path,
            archive=source,
            policy=policy,
            output=output,
        )
    )
    archive_name = "nantai-3d-v1.0.0-runtime.zip"
    targets = {
        "extra": output / "extra.txt",
        "sidecar": output / f"{archive_name}.sha256",
        "receipt": output / PRODUCTION_RELEASE_NAME,
        "checksums": output / CHECKSUMS_NAME,
    }
    targets[mutation].write_bytes(b"changed\n")

    with pytest.raises(ProductionReleaseAssetsError):
        verify_production_release_assets(output)


def test_cli_stages_exact_inputs_and_emits_ascii_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    archive = tmp_path / "candidate.zip"
    policy = tmp_path / "policy.json"
    acceptance = tmp_path / "acceptance"
    output = tmp_path / "release-assets"
    observed: dict[str, object] = {}

    def stage(**kwargs):
        observed.update(kwargs)
        return ProductionReleaseAssets(
            output_dir=output,
            archive_path=output / "nantai-3d-v1.0.0-runtime.zip",
            archive_sha256="a" * 64,
            receipt_path=output / PRODUCTION_RELEASE_NAME,
            checksums_path=output / CHECKSUMS_NAME,
            package_content_id="b" * 64,
            privacy_valid=True,
            scene_trust_effect="none",
        )

    monkeypatch.setattr(assets_cli, "stage_production_release_assets", stage)

    exit_code = assets_cli.main(
        [
            "--archive",
            str(archive),
            "--privacy-policy",
            str(policy),
            "--output-dir",
            str(output),
            "--acceptance-root",
            str(acceptance),
            "--version",
            "v1.0.0",
        ]
    )

    assert exit_code == 0
    assert observed == {
        "repo_root": assets_cli._REPO_ROOT,
        "acceptance_root": acceptance,
        "version": "v1.0.0",
        "archive_path": archive,
        "privacy_policy_path": policy,
        "output_dir": output,
    }
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["archive_sha256"] == "a" * 64
    assert payload["privacy_valid"] is True
    output.encode("ascii")


def test_cli_fails_without_partial_success_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        assets_cli,
        "stage_production_release_assets",
        lambda **_kwargs: (_ for _ in ()).throw(
            ProductionReleaseAssetsError("blocked")
        ),
    )

    exit_code = assets_cli.main(
        [
            "--archive",
            str(tmp_path / "candidate.zip"),
            "--privacy-policy",
            str(tmp_path / "policy.json"),
            "--output-dir",
            str(tmp_path / "release-assets"),
            "--acceptance-root",
            str(tmp_path / "acceptance"),
            "--version",
            "v1.0.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "blocked" in captured.err


@pytest.mark.parametrize(
    "missing_flag",
    ("--acceptance-root", "--version"),
)
def test_cli_requires_acceptance_root_and_version(
    tmp_path: Path,
    missing_flag: str,
) -> None:
    arguments = [
        "--archive",
        str(tmp_path / "candidate.zip"),
        "--privacy-policy",
        str(tmp_path / "policy.json"),
        "--output-dir",
        str(tmp_path / "release-assets"),
        "--acceptance-root",
        str(tmp_path / "acceptance"),
        "--version",
        "v1.0.0",
    ]
    index = arguments.index(missing_flag)
    del arguments[index : index + 2]

    with pytest.raises(SystemExit) as exc_info:
        assets_cli.main(arguments)

    assert exc_info.value.code == 2


def test_verify_cli_emits_ascii_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bundle = tmp_path / "release-assets"
    observed: list[Path] = []

    def verify(path):
        observed.append(path)
        return ProductionReleaseAssetsVerification(
            valid=True,
            bundle_dir=bundle,
            archive_path=bundle / "nantai-3d-v1.0.0-runtime.zip",
            archive_sha256="a" * 64,
            version="v1.0.0",
            package_content_id="b" * 64,
            scene_trust_effect="none",
        )

    monkeypatch.setattr(
        verify_assets_cli,
        "verify_production_release_assets",
        verify,
    )

    exit_code = verify_assets_cli.main([str(bundle)])

    assert exit_code == 0
    assert observed == [bundle]
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["valid"] is True
    assert payload["version"] == "v1.0.0"
    output.encode("ascii")


def test_verify_cli_fails_closed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        verify_assets_cli,
        "verify_production_release_assets",
        lambda _path: (_ for _ in ()).throw(
            ProductionReleaseAssetsError("mixed")
        ),
    )

    exit_code = verify_assets_cli.main(
        [str(tmp_path / "release-assets")]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "mixed" in captured.err


# --- A scheme: rebuild-from-acceptance-root attack matrix (GLM-027) ---


def test_stage_rejects_verifier_valid_forged_source_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A1: a verifier-valid forged receipt cannot bypass acceptance."""
    source, _receipt = _write_real_contract_archive(
        tmp_path / "forged",
        source_commit="a" * 40,
    )
    rebuilt, _rebuilt_receipt = _write_real_contract_archive(
        tmp_path / "accepted",
        source_commit="b" * 40,
    )
    assert verify_production_release_archive(source).valid is True
    assert verify_production_release_archive(rebuilt).valid is True
    assert source.read_bytes() != rebuilt.read_bytes()
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(monkeypatch, rebuilt)

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild|match",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    _assert_not_published(output)


@pytest.mark.parametrize(
    ("candidate_kwargs", "rebuilt_kwargs"),
    [
        (
            {"source_commit": "b" * 40},
            {"source_commit": "a" * 40},
        ),
        (
            {"acceptance_report_sha256": "b" * 64},
            {"acceptance_report_sha256": "a" * 64},
        ),
    ],
    ids=("source-commit", "acceptance-derived-bytes"),
)
def test_stage_rejects_verifier_valid_acceptance_drift(
    tmp_path: Path,
    monkeypatch,
    candidate_kwargs: dict[str, str],
    rebuilt_kwargs: dict[str, str],
) -> None:
    """A2/A3: independently valid bytes still must match acceptance."""
    source, _receipt = _write_real_contract_archive(
        tmp_path / "candidate",
        **candidate_kwargs,
    )
    rebuilt, _rebuilt_receipt = _write_real_contract_archive(
        tmp_path / "accepted",
        **rebuilt_kwargs,
    )
    assert verify_production_release_archive(source).valid is True
    assert verify_production_release_archive(rebuilt).valid is True
    assert source.read_bytes() != rebuilt.read_bytes()
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(monkeypatch, rebuilt)

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild|match",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    _assert_not_published(output)


def test_stage_rejects_version_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A4: matching bytes do not override the requested version."""
    source, _receipt = _write_real_contract_archive(
        tmp_path / "candidate",
        version="v1.0.0",
    )
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(monkeypatch, source)
    stage_kwargs = _stage_kwargs(
        tmp_path,
        archive=source,
        policy=policy,
        output=output,
    )
    stage_kwargs["version"] = "v1.0.1"

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="version",
    ):
        stage_production_release_assets(
            **stage_kwargs
        )

    _assert_not_published(output)


def test_stage_rejects_source_identity_change_during_rebuild(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    assert verify_production_release_archive(source).valid is True
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(
        monkeypatch,
        source,
        identities=[
            ProductionReleaseSourceIdentity(
                source_commit="a" * 40,
                tracked_files=("LICENSE",),
            ),
            ProductionReleaseSourceIdentity(
                source_commit="b" * 40,
                tracked_files=("LICENSE",),
            ),
        ],
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="source identity changed",
    ) as raised:
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    assert (
        str(raised.value)
        == "Production source identity changed during acceptance rebuild"
    )
    _assert_not_published(output)


@pytest.mark.parametrize("dirty_kind", ("tracked", "untracked"))
def test_stage_rejects_real_dirty_release_owned_source(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    """A5: dirty HEAD causes rebuild to fail."""
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    repo = tmp_path / "repo"
    _committed_runtime_repo(repo)
    if dirty_kind == "tracked":
        (repo / "web/viewer/index.html").write_bytes(b"dirty tracked bytes\n")
    else:
        (repo / "pipeline/untracked_release_source.py").write_bytes(b"dirty\n")

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="Production acceptance rebuild failed",
    ):
        stage_production_release_assets(
            **{
                **_stage_kwargs(
                    tmp_path,
                    archive=source,
                    policy=policy,
                    output=output,
                ),
                "repo_root": repo,
            }
        )

    _assert_not_published(output)


def test_stage_rejects_acceptance_toctou(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A6: acceptance TOCTOU causes rebuild to fail."""
    source, _receipt = _write_real_contract_archive(tmp_path / "candidate")
    assert verify_production_release_archive(source).valid is True
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _patch_rebuild_raise(
        monkeypatch,
        ProductionReleaseBuilderError(
            "acceptance evidence changed during validation"
        ),
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="Production acceptance rebuild failed",
    ) as raised:
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    assert str(raised.value) == "Production acceptance rebuild failed"
    _assert_not_published(output)


def test_stage_rejects_candidate_archive_toctou(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A7: candidate archive changed during rebuild."""
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _bind_acceptance_rebuild(monkeypatch, source)
    original_build = assets_module.build_production_release_archive

    def _corrupt_during_rebuild(**kwargs) -> ProductionReleaseBuild:
        candidate_path = kwargs["output_path"].parent / ".candidate.zip"
        candidate_path.write_bytes(b"corrupted")
        return original_build(**kwargs)

    monkeypatch.setattr(
        assets_module,
        "build_production_release_archive",
        _corrupt_during_rebuild,
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="acceptance rebuild|match|changed",
    ):
        stage_production_release_assets(
            **_stage_kwargs(
                tmp_path,
                archive=source,
                policy=policy,
                output=output,
            )
        )

    _assert_not_published(output)


def test_download_verify_proves_byte_integrity_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A10: verify-production-assets only proves byte integrity, not real gates."""
    tree = tmp_path / "runtime"
    receipt = _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    _patch_rebuild_match(monkeypatch, source)
    stage_production_release_assets(
        **_stage_kwargs(
            tmp_path,
            archive=source,
            policy=policy,
            output=output,
        )
    )

    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    for path in output.iterdir():
        (downloaded / path.name).write_bytes(path.read_bytes())

    verification = verify_production_release_assets(downloaded)
    assert verification.valid is True
    assert verification.archive_sha256 == stable_regular_file_digest(
        downloaded / "nantai-3d-v1.0.0-runtime.zip"
    ).sha256
    assert verification.package_content_id == receipt["package"]["content_id"]
    assert verification.version == "v1.0.0"
    assert not hasattr(verification, "acceptance_root")
    assert not hasattr(verification, "production_release_allowed")


def _load_runtime_targets() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "production_runtime_runner",
        _REPO_ROOT / "release" / "production-runtime-runner.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {"help", *module.TARGETS}


def test_release_guide_make_py_targets_exist() -> None:
    """Every make.py target in the bundled release guide must be a real target."""

    guide = _RELEASE_GUIDE.read_text(encoding="utf-8")
    targets = _load_runtime_targets()
    referenced = set(re.findall(r"make\.py\s+(\S+)", guide))
    assert referenced, "release guide must reference at least one make.py target"
    unknown = referenced - targets
    assert not unknown, f"release guide references unknown make.py targets: {unknown}"


def test_release_guide_references_bundled_offline_verifier() -> None:
    """The release guide must direct users to the bundled offline verifier."""

    guide = _RELEASE_GUIDE.read_text(encoding="utf-8")
    assert "python make.py verify" in guide
    assert "scripts/verify_production_release.py" in guide
