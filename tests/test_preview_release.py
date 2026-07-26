from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from pipeline.preview_release import (
    PREVIEW_RELEASE_SCHEMA,
    ReleaseVerificationError,
    build_receipt,
    build_release_archive,
    canonical_json_bytes,
    safe_posix_member_path,
    verify_release_archive,
    verify_release_tree,
)

SOURCE_COMMIT = "a" * 40
SCENE_TRUST = {
    "synthetic": True,
    "geometry_usability": "preview-only",
    "units": "arbitrary",
    "alignment_status": "unaligned",
    "real_photo_textures": False,
    "trust_effect": "none",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_exact_release(root: Path) -> dict:
    files = {
        "assets/example.ply": b"ply\nexample\n",
        "web/data/manifest.json": b'{"schema_version":1}\n',
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    receipt = build_receipt(
        version="v1.0.0-preview.2",
        source_commit=SOURCE_COMMIT,
        artifacts=[
            {
                "path": relative,
                "role": "registry-asset" if relative.startswith("assets/") else "world-manifest",
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
            for relative, payload in reversed(tuple(files.items()))
        ],
        protected_roots=["assets", "web/data"],
        entrypoints={"studio": "/web/studio/", "viewer": "/web/viewer/"},
        exclusions=[".nantai-studio/", "web/data/recon/source-consistent-canary-v1/"],
        scene_trust=SCENE_TRUST,
    )
    (root / "RELEASE-MANIFEST.json").write_bytes(canonical_json_bytes(receipt))
    return receipt


def _write_source_fixture(root: Path) -> tuple[Path, list[str]]:
    runtime_files = {
        "LICENSE": b"fixture license\n",
        "README.md": b"# Fixture\n",
        "make.py": b"print('fixture')\n",
        "pyproject.toml": b"[project]\nname='fixture'\nversion='0.0.0'\n",
        "pipeline/__init__.py": b"",
        "pipeline/runtime.py": b"RUNTIME = True\n",
        "scripts/serve.py": b"print('serve')\n",
        "web/studio/index.html": b"<main>studio</main>\n",
        "web/studio/app.js": b"export const ready = true;\n",
        "web/studio/app.test.mjs": b"throw new Error('must not ship');\n",
        "web/viewer/index.html": b"<main>viewer</main>\n",
        "web/viewer/main.js": b"export const ready = true;\n",
        "docs/releases/guide.md": b"# Guide\n",
        "docs/manual/setup.md": b"# Setup\n",
    }
    for relative, payload in runtime_files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    asset_payload = b"ply\nasset\n"
    asset_sha = _sha256(asset_payload)
    (root / "assets").mkdir()
    (root / "assets/example_v1.ply").write_bytes(asset_payload)
    asset_registry = {
        "schema_version": 1,
        "assets": {
            "example": {
                "kind": "prop",
                "ply": "example_v1.ply",
                "version": 1,
                "origin": "fixture",
                "sha256": asset_sha,
            }
        },
    }
    (root / "assets/registry.json").write_bytes(canonical_json_bytes(asset_registry))

    data = root / "web/data"
    data.mkdir(parents=True)
    chunk_payloads = {
        "chunk_0_0.ply": b"ply\nchunk-full\n",
        "chunk_0_0_lod0.ply": b"ply\nchunk-lod0\n",
        "chunk_0_0_lod1.ply": b"ply\nchunk-lod1\n",
    }
    for name, payload in chunk_payloads.items():
        (data / name).write_bytes(payload)
    world_manifest = {
        "total_chunks": 1,
        "total_points": 7,
        "grid": {"on_demand": False, "world_seed": 42},
        "mesh_grid": {
            "on_demand": True,
            "url_template": "/api/private/{x}/{y}",
            "mesh_asset_bundle_id": "b" * 64,
        },
        "chunks": [
            {
                "id": "0_0",
                "x": 0,
                "y": 0,
                "point_count": 7,
                "ply_file": "chunk_0_0.ply",
                "lod": {
                    "0": "chunk_0_0_lod0.ply",
                    "1": "chunk_0_0_lod1.ply",
                    "2": "chunk_0_0.ply",
                },
            }
        ],
    }
    (data / "manifest.json").write_bytes(canonical_json_bytes(world_manifest))

    recon_root = data / "recon"
    recon_root.mkdir()
    recon_payloads = {
        "recon_full.ply": b"ply\nrecon-full\n",
        "recon_lod0.ply": b"ply\nrecon-lod0\n",
        "recon_lod1.ply": b"ply\nrecon-lod1\n",
        "recon_lod2.ply": b"ply\nrecon-lod2\n",
    }
    for name, payload in recon_payloads.items():
        (recon_root / name).write_bytes(payload)
    recon_manifest = {
        "schema_version": 2,
        "gaussian_count": 5,
        "artifacts": {
            "full_3dgs": {
                "path": "recon_full.ply",
                "bytes": len(recon_payloads["recon_full.ply"]),
                "sha256": _sha256(recon_payloads["recon_full.ply"]),
            },
            "lod": {
                level: {
                    "path": f"recon_lod{level}.ply",
                    "bytes": len(recon_payloads[f"recon_lod{level}.ply"]),
                    "sha256": _sha256(recon_payloads[f"recon_lod{level}.ply"]),
                }
                for level in ("0", "1", "2")
            },
        },
        "provenance": {
            "synthetic": True,
            "geometry_usability": "preview-proxy",
        },
    }
    (recon_root / "recon_manifest.json").write_bytes(canonical_json_bytes(recon_manifest))

    model_root = recon_root / "model-preview"
    model_root.mkdir()
    model_payload = b"glTFfixture"
    (model_root / "model.glb").write_bytes(model_payload)
    model_manifest = {
        "schema_version": 1,
        "kind": "synthetic-model-preview",
        "synthetic": True,
        "geometry_usability": "preview-only",
        "fidelity": "simplified-pbr-not-render-parity",
        "model": {
            "path": "model.glb",
            "sha256": _sha256(model_payload),
            "byte_length": len(model_payload),
            "media_type": "model/gltf-binary",
        },
        "counts": {"mesh_objects": 2, "visual_materials": 1},
    }
    (model_root / "manifest.json").write_bytes(canonical_json_bytes(model_manifest))

    duplicate = recon_root / "source-consistent-canary-v1"
    duplicate.mkdir()
    (duplicate / "recon_full.ply").write_bytes(b"must not ship")
    private = root / ".nantai-studio/private"
    private.mkdir(parents=True)
    (private / "cache.glb").write_bytes(b"must not ship")
    handoff = root / "handoff"
    handoff.mkdir()
    (handoff / "batch35.glb").write_bytes(b"must not ship")

    manifest_paths = {
        "assets/registry.json": "asset-registry",
        "web/data/manifest.json": "world-manifest",
        "web/data/recon/model-preview/manifest.json": "model-preview-manifest",
        "web/data/recon/recon_manifest.json": "reconstruction-manifest",
    }
    input_lock = {
        "schema": "nantai.preview-release-input-lock.v1",
        "version": "v1.0.0-preview.2",
        "runtime_allowlist": [
            "LICENSE",
            "README.md",
            "make.py",
            "pyproject.toml",
            "pipeline/**/*.py",
            "scripts/**/*.py",
            "web/studio/**",
            "web/viewer/**",
            "docs/releases/**",
            "docs/manual/**",
        ],
        "runtime_exclusions": [
            "**/*.test.mjs",
            "**/__pycache__/**",
            "web/data/**",
        ],
        "source_manifests": [
            {
                "path": relative,
                "role": role,
                "sha256": _sha256((root / relative).read_bytes()),
            }
            for relative, role in manifest_paths.items()
        ],
        "expected_counts": {
            "baked_world_chunks": 1,
            "gaussians": 5,
            "model_mesh_objects": 2,
            "model_visual_materials": 1,
            "registry_assets": 1,
        },
        "protected_roots": ["assets", "web/data"],
        "entrypoints": {"studio": "/web/studio/", "viewer": "/web/viewer/"},
        "excluded_prefixes": [
            ".nantai-studio/",
            "handoff/",
            "tests/",
            "web/data/recon/source-consistent-canary-v1/",
        ],
        "scene_trust": SCENE_TRUST,
    }
    lock_path = root / "release/preview2-inputs.json"
    lock_path.parent.mkdir()
    lock_path.write_bytes(canonical_json_bytes(input_lock))
    tracked_files = sorted(
        relative for relative in runtime_files if not relative.endswith(".test.mjs")
    )
    tracked_files.append("release/preview2-inputs.json")
    return lock_path, tracked_files


def test_canonical_json_is_utf8_sorted_lf_and_stable() -> None:
    payload = {"z": 1, "label": "南台", "nested": {"b": 2, "a": 1}}

    first = canonical_json_bytes(payload)
    second = canonical_json_bytes(json.loads(first))

    assert first == second
    assert first == (
        b'{"label":"\xe5\x8d\x97\xe5\x8f\xb0","nested":{"a":1,"b":2},"z":1}\n'
    )
    assert b"\r" not in first


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        ".",
        "/absolute",
        "../escape",
        "safe/../escape",
        "safe\\windows",
        "C:/drive",
        "//server/share",
        "safe//empty",
        "safe/./dot",
        "CON",
        "web/data/NUL.json",
        "safe/trailing.",
        "safe/trailing ",
    ],
)
def test_safe_member_path_rejects_ambiguous_or_unsafe_names(candidate: str) -> None:
    with pytest.raises(ValueError):
        safe_posix_member_path(candidate)


def test_safe_member_path_returns_a_posix_path() -> None:
    path = safe_posix_member_path("web/data/recon/model-preview/manifest.json")

    assert path.as_posix() == "web/data/recon/model-preview/manifest.json"


def test_receipt_is_content_addressed_and_artifacts_are_sorted() -> None:
    receipt = build_receipt(
        version="v1.0.0-preview.2",
        source_commit=SOURCE_COMMIT,
        artifacts=[
            {"path": "z.bin", "role": "last", "bytes": 1, "sha256": _sha256(b"z")},
            {"path": "a.bin", "role": "first", "bytes": 1, "sha256": _sha256(b"a")},
        ],
        protected_roots=["z", "a"],
        entrypoints={"viewer": "/web/viewer/", "studio": "/web/studio/"},
        exclusions=["private/"],
        scene_trust=SCENE_TRUST,
    )

    assert receipt["schema"] == PREVIEW_RELEASE_SCHEMA
    assert [item["path"] for item in receipt["artifacts"]] == ["a.bin", "z.bin"]
    assert receipt["protected_roots"] == ["a", "z"]
    assert receipt["scene_trust"] == SCENE_TRUST
    assert receipt["package"]["immutable"] is True
    assert len(receipt["package"]["content_id"]) == 64

    without_id = json.loads(json.dumps(receipt))
    without_id["package"]["content_id"] = None
    assert receipt["package"]["content_id"] == _sha256(canonical_json_bytes(without_id))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_commit", "ABC"),
        ("version", "1.0.0-preview.2"),
    ],
)
def test_receipt_rejects_invalid_release_identity(field: str, value: str) -> None:
    kwargs = {
        "version": "v1.0.0-preview.2",
        "source_commit": SOURCE_COMMIT,
        "artifacts": [],
        "protected_roots": ["assets"],
        "entrypoints": {"studio": "/web/studio/"},
        "exclusions": [".nantai-studio/"],
        "scene_trust": SCENE_TRUST,
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        build_receipt(**kwargs)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("synthetic", False),
        ("geometry_usability", "metric-aligned"),
        ("units", "meters"),
        ("alignment_status", "aligned"),
        ("real_photo_textures", True),
        ("trust_effect", "promote"),
    ],
)
def test_preview_receipt_rejects_scene_trust_promotion(key: str, value: object) -> None:
    promoted = {**SCENE_TRUST, key: value}

    with pytest.raises(ValueError):
        build_receipt(
            version="v1.0.0-preview.2",
            source_commit=SOURCE_COMMIT,
            artifacts=[],
            protected_roots=["assets"],
            entrypoints={"studio": "/web/studio/"},
            exclusions=[".nantai-studio/"],
            scene_trust=promoted,
        )


def test_verify_release_tree_accepts_exact_protected_content(tmp_path: Path) -> None:
    receipt = _write_exact_release(tmp_path)

    report = verify_release_tree(tmp_path)

    assert report.valid is True
    assert report.version == "v1.0.0-preview.2"
    assert report.source_commit == SOURCE_COMMIT
    assert report.package_content_id == receipt["package"]["content_id"]
    assert report.artifact_count == 2
    assert report.total_bytes == len(b"ply\nexample\n") + len(b'{"schema_version":1}\n')
    assert report.scene_trust_effect == "none"
    assert report.errors == ()


@pytest.mark.parametrize("mutation", ["missing", "changed", "unexpected"])
def test_verify_release_tree_rejects_protected_content_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    _write_exact_release(tmp_path)
    asset = tmp_path / "assets/example.ply"
    if mutation == "missing":
        asset.unlink()
    elif mutation == "changed":
        asset.write_bytes(b"changed")
    else:
        (tmp_path / "assets/unexpected.ply").write_bytes(b"unexpected")

    with pytest.raises(ReleaseVerificationError, match=mutation):
        verify_release_tree(tmp_path)


def test_verify_release_tree_rejects_noncanonical_receipt(tmp_path: Path) -> None:
    receipt = _write_exact_release(tmp_path)
    pretty = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    (tmp_path / "RELEASE-MANIFEST.json").write_bytes(pretty)

    with pytest.raises(ReleaseVerificationError, match="canonical"):
        verify_release_tree(tmp_path)


def test_verify_release_tree_rejects_symlinked_artifact(tmp_path: Path) -> None:
    _write_exact_release(tmp_path)
    asset = tmp_path / "assets/example.ply"
    target = tmp_path / "outside.ply"
    target.write_bytes(asset.read_bytes())
    asset.unlink()
    asset.symlink_to(target)

    with pytest.raises(ReleaseVerificationError, match="symlink"):
        verify_release_tree(tmp_path)


def test_release_archive_is_deterministic_posix_and_allowlisted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    lock_path, tracked_files = _write_source_fixture(source)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_report = build_release_archive(
        source,
        first,
        input_lock_path=lock_path,
        source_commit=SOURCE_COMMIT,
        tracked_files=tracked_files,
    )
    second_report = build_release_archive(
        source,
        second,
        input_lock_path=lock_path,
        source_commit=SOURCE_COMMIT,
        tracked_files=reversed(tracked_files),
    )

    assert first.read_bytes() == second.read_bytes()
    assert first_report.archive_sha256 == second_report.archive_sha256
    assert first_report.package_content_id == second_report.package_content_id
    assert first_report.asset_count == 1
    assert first_report.world_chunk_count == 1
    assert first_report.gaussian_count == 5

    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert len({name.split("/", 1)[0] for name in names}) == 1
        assert all("\\" not in name for name in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert all((info.external_attr >> 16) & 0o777 == 0o644 for info in archive.infolist())
        assert not any(name.endswith(".test.mjs") for name in names)
        assert not any(".nantai-studio" in name for name in names)
        assert not any("source-consistent-canary-v1" in name for name in names)
        assert not any("batch35" in name for name in names)
        world_member = next(name for name in names if name.endswith("/web/data/manifest.json"))
        packaged_world = json.loads(archive.read(world_member))
        assert packaged_world["grid"]["on_demand"] is False
        assert packaged_world["mesh_grid"] == {
            "on_demand": False,
            "reason": "private-mesh-bundles-not-in-preview2",
        }


def test_verify_release_archive_checks_extracted_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    lock_path, tracked_files = _write_source_fixture(source)
    archive = tmp_path / "runtime.zip"
    built = build_release_archive(
        source,
        archive,
        input_lock_path=lock_path,
        source_commit=SOURCE_COMMIT,
        tracked_files=tracked_files,
    )

    verified = verify_release_archive(archive)

    assert verified.valid is True
    assert verified.package_content_id == built.package_content_id
    assert (tmp_path / "runtime.zip.sha256").read_text(encoding="utf-8") == (
        f"{built.archive_sha256}  runtime.zip\n"
    )


def test_verify_release_tree_rejects_unexpected_runtime_file(tmp_path: Path) -> None:
    _write_exact_release(tmp_path)
    (tmp_path / "injected.py").write_text("malicious = True\n", encoding="utf-8")

    with pytest.raises(ReleaseVerificationError, match="unexpected release file"):
        verify_release_tree(tmp_path)


def test_verify_release_tree_allows_only_declared_runtime_mutable_outputs(
    tmp_path: Path,
) -> None:
    _write_exact_release(tmp_path)
    generated = {
        ".venv/lib/python3.13/site-packages/example.py": b"installed = True\n",
        "pipeline/__pycache__/preview_release.cpython-313.pyc": b"bytecode",
        "nantai_infinite_village.egg-info/PKG-INFO": b"Metadata-Version: 2.4\n",
    }
    for relative, payload in generated.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    assert verify_release_tree(tmp_path).valid is True

    (tmp_path / "pipeline/injected.py").write_text("malicious = True\n", encoding="utf-8")
    with pytest.raises(ReleaseVerificationError, match="unexpected release file"):
        verify_release_tree(tmp_path)


def test_builder_rejects_locked_manifest_drift(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    lock_path, tracked_files = _write_source_fixture(source)
    (source / "assets/registry.json").write_bytes(b"{}\n")

    with pytest.raises(ReleaseVerificationError, match="source manifest SHA-256"):
        build_release_archive(
            source,
            tmp_path / "runtime.zip",
            input_lock_path=lock_path,
            source_commit=SOURCE_COMMIT,
            tracked_files=tracked_files,
        )


def test_builder_rejects_symlinked_runtime_input(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    lock_path, tracked_files = _write_source_fixture(source)
    runtime = source / "pipeline/runtime.py"
    outside = tmp_path / "outside.py"
    outside.write_text("unsafe = True\n", encoding="utf-8")
    runtime.unlink()
    runtime.symlink_to(outside)

    with pytest.raises(ReleaseVerificationError, match="symlink"):
        build_release_archive(
            source,
            tmp_path / "runtime.zip",
            input_lock_path=lock_path,
            source_commit=SOURCE_COMMIT,
            tracked_files=tracked_files,
        )
