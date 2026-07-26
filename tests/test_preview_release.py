from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pipeline.preview_release import (
    PREVIEW_RELEASE_SCHEMA,
    ReleaseVerificationError,
    build_receipt,
    canonical_json_bytes,
    safe_posix_member_path,
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
