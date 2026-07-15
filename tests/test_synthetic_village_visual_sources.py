"""Private content-addressed import tests for image2 visual sources."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Manager
from pathlib import Path

import pytest
from PIL import Image

import pipeline.synthetic_village.visual_sources as visual_sources
from pipeline.synthetic_village.visual_sources import (
    VisualSourceError,
    canonical_manifest_bytes,
    import_visual_source,
    load_visual_source_manifest,
)
from scripts import synthetic_village as synthetic_village_cli


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(tmp_path: Path, name: str, color: tuple[int, int, int]) -> Path:
    path = tmp_path / name
    Image.new("RGB", (32, 18), color).save(path)
    return path


def _source_manifest(
    tmp_path: Path,
    source: Path,
    *,
    prompt: str | None = None,
    declared_sha256: str | None = None,
    actual_model_id: str | None = None,
    pack_id: str = "image2-test-pack",
) -> Path:
    payload = {
        "schema_version": 1,
        "pack_id": pack_id,
        "synthetic": True,
        "requested_generator": "image2",
        "generator_interface": "OpenAI built-in image generation tool",
        "assets": [{
            "file": source.name,
            "sha256": declared_sha256 or _sha256(source),
            "prompt": prompt or (
                "Create a fictional mountain-village visual source with no real-world "
                "identity, neutral documentary light, generic materials and no text."
            ),
            "reference_image_sha256": [],
            "synthetic": True,
        }],
    }
    if actual_model_id is not None:
        payload["actual_model_id"] = actual_model_id
    path = tmp_path / f"{source.stem}-source-manifest.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _concurrent_import(
    barrier,
    slot_id: str,
    source: str,
    source_manifest: str,
    pack_root: str,
) -> tuple[str, str]:
    barrier.wait(timeout=30)
    try:
        record = import_visual_source(
            slot_id=slot_id,
            source=Path(source),
            source_manifest=Path(source_manifest),
            pack_root=Path(pack_root),
        )
    except Exception as exc:  # returned to the parent for exact assertions
        return "error", str(exc)
    return "ok", record.sha256


def test_import_is_content_addressed_portable_canonical_and_idempotent(tmp_path):
    source = _source(tmp_path, "overview.png", (36, 82, 41))
    source_manifest = _source_manifest(tmp_path, source)
    pack_root = tmp_path / "private-pack"

    first = import_visual_source(
        slot_id="key-view-establishing-expanded-01",
        source=source,
        source_manifest=source_manifest,
        pack_root=pack_root,
    )
    before = (pack_root / "visual-sources.json").read_bytes()
    second = import_visual_source(
        slot_id="key-view-establishing-expanded-01",
        source=source,
        source_manifest=source_manifest,
        pack_root=pack_root,
    )

    assert first == second
    assert first.sha256 == _sha256(source)
    assert first.object_path == f"objects/{first.sha256}.png"
    assert first.width == 32
    assert first.height == 18
    assert first.actual_model_id == "unknown"
    assert first.synthetic is True
    assert (pack_root / first.object_path).read_bytes() == source.read_bytes()
    assert (pack_root / "visual-sources.json").read_bytes() == before
    manifest = load_visual_source_manifest(pack_root / "visual-sources.json")
    assert manifest.records == (first,)
    assert before == canonical_manifest_bytes(manifest)
    assert str(tmp_path).replace("\\", "/") not in before.decode("utf-8")


def test_import_rejects_undeclared_slot_or_non_image_source(tmp_path):
    source = _source(tmp_path, "overview.png", (30, 30, 30))
    source_manifest = _source_manifest(tmp_path, source)

    with pytest.raises(VisualSourceError, match="not declared"):
        import_visual_source(
            slot_id="key-view-not-declared-99",
            source=source,
            source_manifest=source_manifest,
            pack_root=tmp_path / "pack-a",
        )

    text = tmp_path / "not-an-image.txt"
    text.write_text("not an image", encoding="utf-8")
    text_manifest = _source_manifest(tmp_path, text)
    with pytest.raises(VisualSourceError, match="image suffix"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=text,
            source_manifest=text_manifest,
            pack_root=tmp_path / "pack-b",
        )


def test_import_rejects_source_manifest_hash_mismatch(tmp_path):
    source = _source(tmp_path, "overview.png", (1, 2, 3))
    source_manifest = _source_manifest(tmp_path, source, declared_sha256="0" * 64)

    with pytest.raises(VisualSourceError, match="SHA-256"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=source_manifest,
            pack_root=tmp_path / "pack",
        )


def test_import_rejects_corrupt_existing_object_and_slot_replacement(tmp_path):
    first_source = _source(tmp_path, "first.png", (10, 20, 30))
    first_manifest = _source_manifest(tmp_path, first_source)
    pack_root = tmp_path / "pack"
    first = import_visual_source(
        slot_id="key-view-establishing-small-01",
        source=first_source,
        source_manifest=first_manifest,
        pack_root=pack_root,
    )
    object_path = pack_root / first.object_path
    object_path.write_bytes(b"corrupt")

    with pytest.raises(VisualSourceError, match="existing object"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=first_source,
            source_manifest=first_manifest,
            pack_root=pack_root,
        )

    object_path.write_bytes(first_source.read_bytes())
    replacement = _source(tmp_path, "replacement.png", (90, 80, 70))
    replacement_manifest = _source_manifest(tmp_path, replacement)
    with pytest.raises(VisualSourceError, match="immutable"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=replacement,
            source_manifest=replacement_manifest,
            pack_root=pack_root,
        )


def test_manifest_records_are_sorted_by_slot_id(tmp_path):
    pack_root = tmp_path / "pack"
    expanded = _source(tmp_path, "expanded.png", (2, 4, 6))
    small = _source(tmp_path, "small.png", (1, 3, 5))
    import_visual_source(
        slot_id="key-view-establishing-expanded-01",
        source=expanded,
        source_manifest=_source_manifest(tmp_path, expanded),
        pack_root=pack_root,
    )
    import_visual_source(
        slot_id="key-view-establishing-small-01",
        source=small,
        source_manifest=_source_manifest(tmp_path, small),
        pack_root=pack_root,
    )

    manifest = load_visual_source_manifest(pack_root / "visual-sources.json")
    assert [record.slot_id for record in manifest.records] == sorted(
        record.slot_id for record in manifest.records
    )


def test_same_bytes_across_slots_reuse_one_content_object(tmp_path):
    source = _source(tmp_path, "shared.png", (7, 14, 21))
    source_manifest = _source_manifest(tmp_path, source)
    pack_root = tmp_path / "pack"

    first = import_visual_source(
        slot_id="key-view-establishing-small-01",
        source=source,
        source_manifest=source_manifest,
        pack_root=pack_root,
    )
    second = import_visual_source(
        slot_id="key-view-establishing-expanded-01",
        source=source,
        source_manifest=source_manifest,
        pack_root=pack_root,
    )

    assert first.object_path == second.object_path
    assert len(list((pack_root / "objects").iterdir())) == 1


def test_import_rejects_nonportable_pack_id_and_suffix_format_mismatch(tmp_path):
    source = _source(tmp_path, "source.png", (11, 22, 33))
    bad_pack_manifest = _source_manifest(tmp_path, source, pack_id="../../private")
    with pytest.raises(VisualSourceError, match="pack_id"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=bad_pack_manifest,
            pack_root=tmp_path / "pack-a",
        )

    disguised = tmp_path / "disguised.jpg"
    disguised.write_bytes(source.read_bytes())
    disguised_manifest = _source_manifest(tmp_path, disguised)
    with pytest.raises(VisualSourceError, match="format.*suffix"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=disguised,
            source_manifest=disguised_manifest,
            pack_root=tmp_path / "pack-b",
        )


def test_import_rejects_linklike_pack_ancestor(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "linked-parent"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this runner")
    source = _source(tmp_path, "source.png", (4, 5, 6))

    with pytest.raises(VisualSourceError, match="symlink|junction|real path"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=_source_manifest(tmp_path, source),
            pack_root=link / "escaped-pack",
        )
    assert not (external / "escaped-pack").exists()


def test_concurrent_different_slots_do_not_lose_manifest_records(tmp_path):
    first = _source(tmp_path, "concurrent-a.tiff", (20, 40, 60))
    second = _source(tmp_path, "concurrent-b.tiff", (30, 50, 70))
    pack_root = tmp_path / "pack-different"
    with Manager() as manager, ProcessPoolExecutor(max_workers=2) as executor:
        barrier = manager.Barrier(3)
        futures = [
            executor.submit(
                _concurrent_import,
                barrier,
                slot_id,
                str(source),
                str(_source_manifest(tmp_path, source)),
                str(pack_root),
            )
            for slot_id, source in (
                ("key-view-establishing-small-01", first),
                ("key-view-establishing-expanded-01", second),
            )
        ]
        barrier.wait(timeout=30)
        results = [future.result(timeout=30) for future in futures]

    assert [status for status, _ in results] == ["ok", "ok"]
    assert len(load_visual_source_manifest(pack_root / "visual-sources.json").records) == 2


def test_concurrent_same_slot_allows_only_one_immutable_source(tmp_path):
    first = _source(tmp_path, "same-slot-a.tiff", (80, 40, 20))
    second = _source(tmp_path, "same-slot-b.tiff", (70, 50, 30))
    pack_root = tmp_path / "pack-same"
    with Manager() as manager, ProcessPoolExecutor(max_workers=2) as executor:
        barrier = manager.Barrier(3)
        futures = [
            executor.submit(
                _concurrent_import,
                barrier,
                "key-view-establishing-small-01",
                str(source),
                str(_source_manifest(tmp_path, source)),
                str(pack_root),
            )
            for source in (first, second)
        ]
        barrier.wait(timeout=30)
        results = [future.result(timeout=30) for future in futures]

    assert sorted(status for status, _ in results) == ["error", "ok"]
    assert "immutable" in next(message for status, message in results if status == "error")
    assert len(load_visual_source_manifest(pack_root / "visual-sources.json").records) == 1


def test_import_visual_cli_uses_fixed_private_pack_root(tmp_path, monkeypatch, capsys):
    source = _source(tmp_path, "cli.png", (8, 16, 24))
    source_manifest = _source_manifest(tmp_path, source)
    pack_root = tmp_path / "fixed-private-pack"
    monkeypatch.setattr(synthetic_village_cli, "DEFAULT_VISUAL_PACK_ROOT", pack_root)

    result = synthetic_village_cli.main([
        "import-visual",
        "--slot",
        "key-view-establishing-small-01",
        "--source",
        str(source),
        "--source-manifest",
        str(source_manifest),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["slot_id"] == "key-view-establishing-small-01"
    assert (pack_root / "visual-sources.json").is_file()


def test_object_durability_failure_never_publishes_manifest(tmp_path, monkeypatch):
    source = _source(tmp_path, "durability.png", (13, 26, 39))
    pack_root = tmp_path / "pack"
    original_flush_directory = visual_sources._flush_directory

    def fail_object_directory(path):
        if path.name == "objects":
            raise OSError("simulated object-directory flush failure")
        return original_flush_directory(path)

    monkeypatch.setattr(visual_sources, "_flush_directory", fail_object_directory)
    with pytest.raises(VisualSourceError, match="flush failure"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=_source_manifest(tmp_path, source),
            pack_root=pack_root,
        )

    assert not (pack_root / "visual-sources.json").exists()


def test_manifest_publication_failure_leaves_reusable_orphan_object(tmp_path, monkeypatch):
    source = _source(tmp_path, "orphan.png", (14, 28, 42))
    source_manifest = _source_manifest(tmp_path, source)
    pack_root = tmp_path / "pack"
    original_write_manifest = visual_sources._write_manifest

    def fail_manifest(_path, _manifest):
        raise OSError("simulated manifest publication failure")

    monkeypatch.setattr(visual_sources, "_write_manifest", fail_manifest)
    with pytest.raises(VisualSourceError, match="publication failure"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=source_manifest,
            pack_root=pack_root,
        )
    assert not (pack_root / "visual-sources.json").exists()
    objects = list((pack_root / "objects").glob("*"))
    assert len(objects) == 1
    assert _sha256(objects[0]) == _sha256(source)

    monkeypatch.setattr(visual_sources, "_write_manifest", original_write_manifest)
    record = import_visual_source(
        slot_id="key-view-establishing-small-01",
        source=source,
        source_manifest=source_manifest,
        pack_root=pack_root,
    )
    assert (pack_root / record.object_path) == objects[0]


def test_public_boundaries_normalize_validation_and_filesystem_errors(tmp_path):
    invalid_manifest = tmp_path / "invalid-visual-sources.json"
    invalid_manifest.write_text(
        json.dumps({
            "schema_version": 1,
            "pack_id": "synthetic-mountain-village-hybrid-v3",
            "synthetic": True,
            "records": [{"not": "a valid record"}],
        }, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(VisualSourceError, match="validation"):
        load_visual_source_manifest(invalid_manifest)

    source = _source(tmp_path, "directory-object.png", (15, 30, 45))
    pack_root = tmp_path / "pack"
    digest = _sha256(source)
    (pack_root / "objects" / f"{digest}.png").mkdir(parents=True)
    with pytest.raises(VisualSourceError, match="filesystem|regular file"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=_source_manifest(tmp_path, source),
            pack_root=pack_root,
        )


def test_import_normalizes_default_catalog_failures(tmp_path, monkeypatch):
    source = _source(tmp_path, "catalog-failure.png", (18, 36, 54))

    def fail_catalog_load():
        raise ValueError("simulated noncanonical tracked catalog")

    monkeypatch.setattr(visual_sources, "load_default_visual_slots", fail_catalog_load)
    with pytest.raises(VisualSourceError, match="default visual-slot catalog"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=_source_manifest(tmp_path, source),
            pack_root=tmp_path / "pack",
        )


def test_source_byte_and_pixel_limits_fail_closed(tmp_path, monkeypatch):
    source = _source(tmp_path, "limited.png", (16, 32, 48))
    source_manifest = _source_manifest(tmp_path, source)
    monkeypatch.setattr(visual_sources, "MAX_SOURCE_IMAGE_BYTES", source.stat().st_size - 1)
    with pytest.raises(VisualSourceError, match="byte limit"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=source_manifest,
            pack_root=tmp_path / "pack-bytes",
        )

    monkeypatch.setattr(visual_sources, "MAX_SOURCE_IMAGE_BYTES", 128 * 1024 * 1024)
    monkeypatch.setattr(visual_sources, "MAX_SOURCE_IMAGE_PIXELS", 32 * 17)
    with pytest.raises(VisualSourceError, match="pixel limit"):
        import_visual_source(
            slot_id="key-view-establishing-small-01",
            source=source,
            source_manifest=source_manifest,
            pack_root=tmp_path / "pack-pixels",
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_import_rejects_real_windows_junction_ancestor(tmp_path):
    external = tmp_path / "junction-target"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(external)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    source = _source(tmp_path, "junction-source.png", (17, 34, 51))
    try:
        with pytest.raises(VisualSourceError, match="symlink|junction|real path"):
            import_visual_source(
                slot_id="key-view-establishing-small-01",
                source=source,
                source_manifest=_source_manifest(tmp_path, source),
                pack_root=junction / "escaped-pack",
            )
        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert not (external / "escaped-pack").exists()
    finally:
        os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_manifest_loader_rejects_real_windows_objects_junction(tmp_path):
    source = _source(tmp_path, "junction-object.png", (19, 38, 57))
    safe_pack = tmp_path / "safe-pack"
    record = import_visual_source(
        slot_id="key-view-establishing-small-01",
        source=source,
        source_manifest=_source_manifest(tmp_path, source),
        pack_root=safe_pack,
    )

    external_objects = tmp_path / "external-objects"
    external_objects.mkdir()
    external_object = external_objects / Path(record.object_path).name
    external_object.write_bytes(source.read_bytes())
    pack_root = tmp_path / "junction-pack"
    pack_root.mkdir()
    manifest_path = pack_root / "visual-sources.json"
    manifest_path.write_bytes((safe_pack / "visual-sources.json").read_bytes())
    objects_junction = pack_root / "objects"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(objects_junction), str(external_objects)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    try:
        with pytest.raises(VisualSourceError, match="symlink|junction|real path"):
            load_visual_source_manifest(manifest_path)
        assert external_object.read_bytes() == source.read_bytes()
    finally:
        os.rmdir(objects_junction)
