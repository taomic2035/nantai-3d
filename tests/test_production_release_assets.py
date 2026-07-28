from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

import scripts.stage_production_release_assets as assets_cli
import scripts.verify_production_release_assets as verify_assets_cli
from pipeline.production_release_assets import (
    ProductionReleaseAssets,
    ProductionReleaseAssetsError,
    ProductionReleaseAssetsVerification,
    stage_production_release_assets,
    verify_production_release_assets,
)
from pipeline.production_release_contract import (
    CHECKSUMS_NAME,
    PRODUCTION_RELEASE_NAME,
    build_production_receipt,
)
from pipeline.release_archive import canonical_json_bytes
from tests.production_release_fixtures import (
    modeled_artifact_records,
    modeled_entrypoints,
    modeled_payloads,
    modeled_public_evidence,
    write_modeled_production_archive,
    write_modeled_production_tree,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RELEASE_GUIDE = _REPO_ROOT / "release" / "production-verify-and-run.md"


def _write_real_contract_tree(root: Path) -> dict[str, object]:
    root.mkdir()
    payloads = modeled_payloads()
    public_evidence = modeled_public_evidence()
    public_evidence["fixture_kind"] = None
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
        version="v1.0.0",
        source_commit="a" * 40,
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


def test_stage_exports_only_four_verified_public_assets(tmp_path: Path) -> None:
    tree = tmp_path / "runtime"
    receipt = _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"

    result = stage_production_release_assets(
        archive_path=source,
        privacy_policy_path=policy,
        output_dir=output,
    )

    archive_name = "nantai-3d-v1.0.0-runtime.zip"
    assert sorted(path.name for path in output.iterdir()) == [
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
    verification = verify_production_release_assets(output)
    assert verification.valid is True
    assert verification.package_content_id == receipt["package"]["content_id"]
    assert verification.archive_sha256 == archive_sha


def test_stage_rejects_modeled_contract_fixture(tmp_path: Path) -> None:
    tree = tmp_path / "runtime"
    write_modeled_production_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="modeled contract",
    ):
        stage_production_release_assets(
            archive_path=source,
            privacy_policy_path=policy,
            output_dir=tmp_path / "release-assets",
        )

    assert not (tmp_path / "release-assets").exists()


def test_stage_rejects_privacy_findings_without_publication(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(
        tmp_path / "privacy-policy.json",
        needle=b"<h1>Studio</h1>",
    )

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="privacy audit failed",
    ):
        stage_production_release_assets(
            archive_path=source,
            privacy_policy_path=policy,
            output_dir=tmp_path / "release-assets",
        )

    assert not (tmp_path / "release-assets").exists()


def test_stage_never_replaces_existing_output_directory(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="ascii")

    with pytest.raises(
        ProductionReleaseAssetsError,
        match="must be absent",
    ):
        stage_production_release_assets(
            archive_path=source,
            privacy_policy_path=policy,
            output_dir=output,
        )

    assert sentinel.read_text(encoding="ascii") == "keep"


def test_stage_rejects_junction_output_parent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output_parent = tmp_path / "junction-parent"
    output_parent.mkdir()
    output = output_parent / "release-assets"
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
            archive_path=source,
            privacy_policy_path=policy,
            output_dir=output,
        )

    assert not output.exists()


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
    stage_production_release_assets(
        archive_path=source,
        privacy_policy_path=policy,
        output_dir=output,
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
    mutation: str,
) -> None:
    tree = tmp_path / "runtime"
    _write_real_contract_tree(tree)
    source = tmp_path / "candidate.zip"
    write_modeled_production_archive(tree, source)
    policy = _privacy_policy(tmp_path / "privacy-policy.json")
    output = tmp_path / "release-assets"
    stage_production_release_assets(
        archive_path=source,
        privacy_policy_path=policy,
        output_dir=output,
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
        ]
    )

    assert exit_code == 0
    assert observed == {
        "archive_path": archive,
        "privacy_policy_path": policy,
        "output_dir": output,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["archive_sha256"] == "a" * 64
    assert payload["privacy_valid"] is True
    capsys.readouterr().out.encode("ascii")


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
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "blocked" in captured.err


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
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["version"] == "v1.0.0"
    capsys.readouterr().out.encode("ascii")


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
