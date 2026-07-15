"""P3: ingest 溯源清单 (ingest_manifest.json) 测试

覆盖:
- 清单写出且可被 IngestManifest 校验
- session_id 内容寻址、可复现、随参数变化
- 源→输出映射完整 (每个输出图恰好出现一次)
- 输出 sha256 与真实字节一致
- 视频帧诚实记录 EXIF 丢失 (has_embedded_exif=False, gps=None)
- 照片 EXIF 仅当真实携带时转发, 否则 exif_source="none"
- 误导性 docstring 声明已移除
"""
import hashlib
import os
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from pipeline import ingest
from pipeline import ingest_manifest as ingest_contract
from pipeline.ingest import MANIFEST_FILENAME, ingest_all
from pipeline.ingest_manifest import (
    FrameMapping,
    IngestManifest,
    IngestParams,
    SourceRecord,
    build_manifest,
)


def _plain_photo(path: Path, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    Image.fromarray(
        rng.integers(60, 200, (24, 32, 3), dtype=np.uint8)
    ).save(path)


def _exif_photo(path: Path) -> bool:
    """写一张带真实 DateTimeOriginal + GPS 的照片; 返回 EXIF 是否成功嵌入。"""
    from PIL.ExifTags import GPS, Base

    img = Image.new("RGB", (32, 24), (120, 130, 140))
    exif = Image.Exif()
    exif[Base.DateTime.value] = "2026:07:15 10:20:30"
    exif_ifd = exif.get_ifd(Base.ExifOffset.value)
    exif_ifd[Base.DateTimeOriginal.value] = "2026:07:15 10:20:30"
    exif[Base.ExifOffset.value] = exif_ifd
    exif[Base.GPSInfo.value] = {
        GPS.GPSLatitudeRef.value: "N",
        GPS.GPSLatitude.value: (31.0, 14.0, 0.0),
        GPS.GPSLongitudeRef.value: "E",
        GPS.GPSLongitude.value: (121.0, 28.0, 0.0),
    }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        img.save(path, exif=exif)
    dt, gps = ingest._read_photo_exif(path)
    return dt is not None and gps is not None


def _make_video(path: Path, n_frames: int = 12, size=(64, 48)) -> bool:
    """用 cv2.VideoWriter 合成一段短视频; 返回是否成功。"""
    cv2 = pytest.importorskip("cv2")
    w, h = size
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 10.0, (w, h))
    if not writer.isOpened():
        return False
    rng = np.random.default_rng(3)
    for _ in range(n_frames):
        frame = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path.exists() and path.stat().st_size > 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _output_images(output_dir: Path) -> list[Path]:
    """output_dir 下所有输出图 (排除清单文件)。"""
    return sorted(
        p for p in output_dir.rglob("*")
        if p.is_file() and p.name != MANIFEST_FILENAME
    )


# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0.0, -1.0, 30.01, float("nan"), float("inf")])
def test_ingest_params_reject_invalid_fps(bad):
    with pytest.raises(ValidationError):
        IngestParams(
            fps=bad,
            max_frames=300,
            blur_threshold=0,
            max_long_edge=2560,
        )


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("max_frames", 0),
        ("max_frames", 10_001),
        ("blur_threshold", -0.1),
        ("blur_threshold", float("nan")),
        ("max_long_edge", 255),
        ("max_long_edge", 16_385),
    ],
)
def test_ingest_params_reject_other_invalid_bounds(field, bad):
    values = {
        "fps": 2,
        "max_frames": 300,
        "blur_threshold": 0,
        "max_long_edge": 2560,
    }
    values[field] = bad
    with pytest.raises(ValidationError):
        IngestParams(**values)


@pytest.mark.parametrize(
    "path", [
        "", "/abs.jpg", "C:drive.jpg", "CON.jpg", "dir/AUX",
        "../escape.jpg", "a\\b.jpg", "a/./b.jpg", 'bad?.jpg',
        'bad*.jpg', 'bad<name>.jpg', 'bad|name.jpg', 'bad"name.jpg',
    ],
)
def test_source_paths_are_portable_relative_posix(path):
    with pytest.raises(ValidationError):
        _strict_photo_record(source_path=path)


@pytest.mark.parametrize(
    "path", [
        "", "/abs.jpg", "C:drive.jpg", "CON.jpg", "dir/AUX",
        "../escape.jpg", "a\\b.jpg", "a/./b.jpg", 'bad?.jpg',
        'bad*.jpg', 'bad<name>.jpg', 'bad|name.jpg', 'bad"name.jpg',
    ],
)
def test_output_paths_are_portable_relative_posix(path):
    with pytest.raises(ValidationError):
        _strict_photo_record(output_path=path)


@pytest.mark.parametrize("digest", ["", "abc", "A" * 64, "g" * 64, None])
def test_source_sha256_is_required_lowercase_hex(digest):
    valid_output = FrameMapping(
        output_path="photo.jpg",
        output_sha256="a" * 64,
        output_bytes=3,
        source_frame_index=None,
        preserves_source_bytes=True,
    )
    with pytest.raises(ValidationError) as error:
        SourceRecord(
            source_path="photo.jpg",
            source_sha256=digest,
            kind="photo",
            bytes=3,
            outputs=(valid_output,),
        )
    assert any(item["loc"] == ("source_sha256",) for item in error.value.errors())


def test_manifest_rejects_wrong_schema_negative_total_and_naive_timestamp():
    common = {
        "session_id": "ingest-" + "a" * 64,
        "created_utc": "2026-07-15T00:00:00+00:00",
        "params": {
            "fps": 2,
            "max_frames": 300,
            "blur_threshold": 0,
            "max_long_edge": 2560,
        },
        "sources": [],
        "total_output_frames": 1,
    }
    with pytest.raises(ValidationError):
        IngestManifest.model_validate({**common, "schema_version": 2})
    with pytest.raises(ValidationError):
        IngestManifest.model_validate({**common, "total_output_frames": -1})
    with pytest.raises(ValidationError):
        IngestManifest.model_validate({**common, "created_utc": "2026-07-15T00:00:00"})


def test_source_kind_fields_cannot_contradict_each_other():
    with pytest.raises(ValidationError):
        SourceRecord(
            source_path="photo.jpg",
            source_sha256="a" * 64,
            kind="photo",
            bytes=3,
            source_fps=30,
            duration_s=1,
            outputs=[],
        )


def _strict_photo_record(
    *, source_path="photo.jpg", output_path="photo.jpg", payload=b"photo-bytes"
):
    digest = hashlib.sha256(payload).hexdigest()
    return SourceRecord(
        source_path=source_path,
        source_sha256=digest,
        kind="photo",
        bytes=len(payload),
        outputs=(FrameMapping(
            output_path=output_path,
            output_sha256=digest,
            output_bytes=len(payload),
            source_frame_index=None,
            preserves_source_bytes=True,
        ),),
    )


def _strict_video_record(
    *,
    source_path="clip.mp4",
    source_fps=10.0,
    frame_indexes=(0,),
    output_paths=None,
):
    if output_paths is None:
        output_paths = tuple(
            f"{source_path}.frames/frame_{ordinal:06d}.jpg"
            for ordinal, _ in enumerate(frame_indexes)
        )
    return SourceRecord(
        source_path=source_path,
        source_sha256="a" * 64,
        kind="video",
        bytes=20,
        source_fps=source_fps,
        duration_s=1,
        outputs=tuple(
            FrameMapping(
                output_path=output_path,
                output_sha256="b" * 64,
                output_bytes=4,
                source_frame_index=frame_index,
                preserves_source_bytes=False,
            )
            for output_path, frame_index in zip(
                output_paths, frame_indexes, strict=True,
            )
        ),
    )


def test_photo_mapping_must_preserve_the_source_relative_path():
    with pytest.raises(ValidationError, match="deterministic|source path"):
        _strict_photo_record(output_path="renamed.jpg")


def test_source_kind_must_match_its_media_suffix():
    with pytest.raises(ValidationError, match="photo suffix"):
        _strict_photo_record(source_path="clip.mp4", output_path="clip.mp4")
    with pytest.raises(ValidationError, match="video suffix"):
        _strict_video_record(source_path="photo.jpg")


@pytest.mark.parametrize(
    "bad", ["", "   ", "2026-99-99", "not-a-date", "2026:7:1 1:2:3"],
)
def test_photo_exif_datetime_requires_real_exif_format(bad):
    base = _strict_photo_record()
    with pytest.raises(ValidationError, match="EXIF datetime"):
        SourceRecord(**{
            **base.model_dump(),
            "exif_datetime": bad,
            "exif_source": "photo-exif",
        })


@pytest.mark.parametrize(
    ("frame_indexes", "output_paths"),
    [
        ((0,), ("arbitrary/frame.jpg",)),
        ((5, 0), None),
    ],
)
def test_video_mapping_requires_deterministic_paths_and_order(
    frame_indexes, output_paths,
):
    with pytest.raises(ValidationError, match="deterministic|increasing"):
        _strict_video_record(
            frame_indexes=frame_indexes,
            output_paths=output_paths,
        )


def test_manifest_enforces_max_frames_and_sampling_step():
    too_many_params = IngestParams(
        fps=2, max_frames=1, blur_threshold=0, max_long_edge=2560,
    )
    with pytest.raises(ValidationError, match="max_frames"):
        build_manifest(
            created_utc=datetime.now(UTC),
            params=too_many_params,
            sources=(_strict_video_record(frame_indexes=(0, 5)),),
        )

    sampled_params = IngestParams(
        fps=2, max_frames=10, blur_threshold=0, max_long_edge=2560,
    )
    with pytest.raises(ValidationError, match="sampling step"):
        build_manifest(
            created_utc=datetime.now(UTC),
            params=sampled_params,
            sources=(_strict_video_record(frame_indexes=(1,)),),
        )


def test_manifest_rejects_casefold_path_collisions():
    params = IngestParams(
        fps=10, max_frames=10, blur_threshold=0, max_long_edge=2560,
    )
    with pytest.raises(ValidationError, match="source paths must be unique"):
        build_manifest(
            created_utc=datetime.now(UTC),
            params=params,
            sources=(
                _strict_photo_record(source_path="A.jpg", output_path="A.jpg"),
                _strict_photo_record(source_path="a.jpg", output_path="a.jpg"),
            ),
        )

    with pytest.raises(ValidationError, match="output paths must be unique"):
        build_manifest(
            created_utc=datetime.now(UTC),
            params=params,
            sources=(
                _strict_photo_record(
                    source_path="clip.mp4.frames/frame_000000.jpg",
                    output_path="clip.mp4.frames/frame_000000.jpg",
                ),
                _strict_video_record(source_path="CLIP.mp4"),
            ),
        )


def test_manifest_rejects_wrong_total_and_wrong_session():
    params = IngestParams(
        fps=2, max_frames=300, blur_threshold=0, max_long_edge=2560,
    )
    one = _strict_photo_record()
    common = {
        "session_id": ingest_contract.derive_session_id(params, (one,)),
        "created_utc": datetime.now(UTC),
        "params": params,
        "sources": (one,),
    }
    with pytest.raises(ValidationError, match="total_output_frames"):
        IngestManifest(**{**common, "total_output_frames": 2})
    with pytest.raises(ValidationError, match="session_id"):
        IngestManifest(**{
            **common,
            "total_output_frames": 1,
            "session_id": "ingest-" + "0" * 64,
        })


def _write_strict_stage(tmp_path):
    input_dir = tmp_path / "input"
    stage_dir = tmp_path / "stage"
    input_dir.mkdir()
    stage_dir.mkdir()
    payload = b"publication-safe-photo"
    (input_dir / "photo.jpg").write_bytes(payload)
    (stage_dir / "photo.jpg").write_bytes(payload)
    params = IngestParams(
        fps=2, max_frames=300, blur_threshold=0, max_long_edge=2560,
    )
    manifest = build_manifest(
        created_utc=datetime.now(UTC),
        params=params,
        sources=(_strict_photo_record(payload=payload),),
    )
    (stage_dir / MANIFEST_FILENAME).write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8",
    )
    return input_dir, stage_dir, manifest


def test_verify_accepts_an_exact_staged_artifact(tmp_path):
    input_dir, stage_dir, expected = _write_strict_stage(tmp_path)
    actual = ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)
    assert actual == expected


def test_verify_rejects_extra_undeclared_file(tmp_path):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    (stage_dir / "stale.jpg").write_bytes(b"stale")
    with pytest.raises(ingest_contract.IngestArtifactError, match="undeclared"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


@pytest.mark.parametrize("target", ["source", "output"])
def test_verify_rejects_changed_bytes(tmp_path, target):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    root = input_dir if target == "source" else stage_dir
    (root / "photo.jpg").write_bytes(b"changed")
    with pytest.raises(ingest_contract.IngestArtifactError, match="size|sha256"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_verify_rejects_same_size_mutation_during_hash(tmp_path, monkeypatch):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    target = stage_dir / "photo.jpg"
    original_hash = ingest_contract.sha256_file

    def hash_then_mutate(path):
        digest = original_hash(path)
        if Path(path) == target:
            target.write_bytes(b"x" * target.stat().st_size)
        return digest

    monkeypatch.setattr(ingest_contract, "sha256_file", hash_then_mutate)
    with pytest.raises(
        ingest_contract.IngestArtifactError,
        match="changed while being verified",
    ):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_verify_rejects_an_added_supported_source(tmp_path):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    _plain_photo(input_dir / "added.jpg", seed=8)
    with pytest.raises(ingest_contract.IngestArtifactError, match="source set"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_verify_rejects_missing_declared_output(tmp_path):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    (stage_dir / "photo.jpg").unlink()
    with pytest.raises(ingest_contract.IngestArtifactError, match="missing"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_verify_rejects_manifest_larger_than_limit(tmp_path):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    (stage_dir / MANIFEST_FILENAME).write_bytes(
        b"{" + b" " * ingest_contract.MAX_MANIFEST_BYTES + b"}",
    )
    with pytest.raises(ingest_contract.IngestArtifactError, match="too large"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_verify_rejects_symlinked_stage_entry(tmp_path):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    link = stage_dir / "link.jpg"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"file symlink unavailable: {exc}")
    with pytest.raises(ingest_contract.IngestArtifactError, match="symlink"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_verifier_fails_closed_when_recursive_scan_errors(tmp_path, monkeypatch):
    input_dir, stage_dir, _ = _write_strict_stage(tmp_path)

    def denied_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("denied subtree"))
        return iter(())

    monkeypatch.setattr(ingest_contract.os, "walk", denied_walk)
    with pytest.raises(ingest_contract.IngestArtifactError, match="scan|read"):
        ingest_contract.verify_ingest_artifact(stage_dir, input_dir=input_dir)


def test_input_fingerprint_fails_closed_when_recursive_scan_errors(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    def denied_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("denied subtree"))
        return iter(())

    monkeypatch.setattr(ingest.os, "walk", denied_walk)
    with pytest.raises(ingest.IngestError, match="scan|read"):
        ingest._fingerprint_inputs(input_dir)


def test_ingest_requires_a_fresh_output_directory(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    _plain_photo(input_dir / "photo.jpg")
    output_dir = tmp_path / "stage"
    output_dir.mkdir()
    (output_dir / "old.jpg").write_bytes(b"old")

    with pytest.raises(ingest.IngestError, match="fresh output"):
        ingest_all(input_dir, output_dir, blur_threshold=0)


def test_ingest_rejects_output_below_a_symlinked_parent(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    _plain_photo(input_dir / "photo.jpg")
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    alias = tmp_path / "alias"
    try:
        os.symlink(real_parent, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    with pytest.raises(ingest.IngestError, match="fresh output|symlink"):
        ingest_all(input_dir, alias / "stage", blur_threshold=0)
    assert not (real_parent / "stage").exists()


def test_output_ancestor_link_guard_runs_before_creation(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    _plain_photo(input_dir / "photo.jpg")
    ancestor = tmp_path / "declared-link"
    ancestor.mkdir()
    original = ingest._is_linklike
    monkeypatch.setattr(
        ingest,
        "_is_linklike",
        lambda path: path == ancestor or original(path),
    )

    with pytest.raises(ingest.IngestError, match="ancestor"):
        ingest_all(input_dir, ancestor / "stage", blur_threshold=0)
    assert not (ancestor / "stage").exists()


def test_nested_duplicate_basenames_keep_deterministic_relative_paths(tmp_path):
    input_dir = tmp_path / "input"
    (input_dir / "left").mkdir(parents=True)
    (input_dir / "right").mkdir()
    _plain_photo(input_dir / "left/photo.jpg", seed=1)
    _plain_photo(input_dir / "right/photo.jpg", seed=2)
    output_dir = tmp_path / "stage"

    ingest_all(input_dir, output_dir, blur_threshold=0)

    assert (output_dir / "left/photo.jpg").is_file()
    assert (output_dir / "right/photo.jpg").is_file()
    manifest = ingest_contract.verify_ingest_artifact(output_dir, input_dir=input_dir)
    assert {item.outputs[0].output_path for item in manifest.sources} == {
        "left/photo.jpg", "right/photo.jpg",
    }


def test_video_names_with_same_stem_have_distinct_output_directories(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    first = _make_video(input_dir / "clip.mp4")
    second = _make_video(input_dir / "clip.mov")
    if not first or not second:
        pytest.skip("cv2 cannot encode both test containers")
    output_dir = tmp_path / "stage"

    ingest_all(input_dir, output_dir, fps=10, blur_threshold=0)

    assert (output_dir / "clip.mp4.frames").is_dir()
    assert (output_dir / "clip.mov.frames").is_dir()


def test_imwrite_false_aborts_without_success_manifest(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    if not _make_video(input_dir / "clip.mp4"):
        pytest.skip("cv2 cannot encode the test video")
    output_dir = tmp_path / "stage"
    monkeypatch.setattr(ingest.cv2, "imwrite", lambda *args, **kwargs: False)

    with pytest.raises(ingest.IngestError, match="write"):
        ingest_all(input_dir, output_dir, fps=10, blur_threshold=0)
    assert not (output_dir / MANIFEST_FILENAME).exists()


def test_open_failed_video_aborts_without_success_manifest(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "broken.mp4").write_bytes(b"not-a-video")
    output_dir = tmp_path / "stage"

    with pytest.raises(ingest.IngestError, match="open|decode"):
        ingest_all(input_dir, output_dir, fps=10, blur_threshold=0)
    assert not (output_dir / MANIFEST_FILENAME).exists()


def test_premature_video_decode_failure_is_not_treated_as_eof(tmp_path, monkeypatch):
    frame = np.full((8, 8, 3), 120, dtype=np.uint8)

    class PartialCapture:
        def __init__(self):
            self.frames = [frame.copy(), frame.copy()]

        def isOpened(self):  # noqa: N802 - mirrors cv2.VideoCapture
            return True

        def get(self, prop):
            if prop == ingest.cv2.CAP_PROP_FPS:
                return 10.0
            if prop == ingest.cv2.CAP_PROP_FRAME_COUNT:
                return 5
            return 0

        def read(self):
            return (True, self.frames.pop(0)) if self.frames else (False, None)

        def release(self):
            return None

    def write_frame(path, _frame, _options):
        Path(path).write_bytes(b"jpeg")
        return True

    monkeypatch.setattr(ingest.cv2, "VideoCapture", lambda _path: PartialCapture())
    monkeypatch.setattr(ingest.cv2, "imwrite", write_frame)

    with pytest.raises(ingest.IngestError, match="premature|decode"):
        ingest.extract_video_frames(
            tmp_path / "partial.mp4",
            tmp_path / "frames",
            fps=10,
            max_frames=10,
            blur_threshold=0,
        )


def test_source_changed_during_ingest_aborts_without_success_manifest(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    source = input_dir / "photo.jpg"
    _plain_photo(source)
    output_dir = tmp_path / "stage"
    real_copy = ingest.copy_photo

    def copy_then_mutate(photo_path, destination, relative_path):
        copied = real_copy(photo_path, destination, relative_path)
        photo_path.write_bytes(b"changed-during-ingest")
        return copied

    monkeypatch.setattr(ingest, "copy_photo", copy_then_mutate)
    with pytest.raises(ingest.IngestError, match="input changed"):
        ingest_all(input_dir, output_dir, blur_threshold=0)
    assert not (output_dir / MANIFEST_FILENAME).exists()


def test_source_added_during_ingest_aborts_without_success_manifest(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    _plain_photo(input_dir / "photo.jpg")
    output_dir = tmp_path / "stage"
    real_copy = ingest.copy_photo

    def copy_then_add(photo_path, destination, relative_path):
        copied = real_copy(photo_path, destination, relative_path)
        _plain_photo(input_dir / "added.jpg", seed=4)
        return copied

    monkeypatch.setattr(ingest, "copy_photo", copy_then_add)
    with pytest.raises(ingest.IngestError, match="input changed"):
        ingest_all(input_dir, output_dir, blur_threshold=0)
    assert not (output_dir / MANIFEST_FILENAME).exists()


class _ExifRatio:
    def __init__(self, value):
        self.num = value
        self.den = 1


class _ExifTag:
    def __init__(self, values=None, text=""):
        self.values = values or []
        self.text = text

    def __str__(self):
        return self.text


def _fake_gps_tags(
    *, lat_ref=None, lon_ref=None, altitude=None, altitude_ref=None,
):
    tags = {
        "GPS GPSLatitude": _ExifTag([_ExifRatio(31), _ExifRatio(14), _ExifRatio(0)]),
        "GPS GPSLongitude": _ExifTag([_ExifRatio(121), _ExifRatio(28), _ExifRatio(0)]),
    }
    if lat_ref is not None:
        tags["GPS GPSLatitudeRef"] = _ExifTag(text=lat_ref)
    if lon_ref is not None:
        tags["GPS GPSLongitudeRef"] = _ExifTag(text=lon_ref)
    if altitude is not None:
        tags["GPS GPSAltitude"] = _ExifTag([_ExifRatio(altitude)])
    if altitude_ref is not None:
        tags["GPS GPSAltitudeRef"] = _ExifTag(text=altitude_ref)
    return tags


def test_gps_without_direction_refs_is_not_declared(tmp_path, monkeypatch):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    fake = SimpleNamespace(process_file=lambda *_args, **_kwargs: _fake_gps_tags())
    monkeypatch.setitem(sys.modules, "exifread", fake)

    captured_at, gps = ingest._read_photo_exif(source)

    assert captured_at is None
    assert gps is None


def test_gps_south_west_refs_preserve_negative_sign(tmp_path, monkeypatch):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    fake = SimpleNamespace(
        process_file=lambda *_args, **_kwargs: _fake_gps_tags(lat_ref="S", lon_ref="W"),
    )
    monkeypatch.setitem(sys.modules, "exifread", fake)

    _, gps = ingest._read_photo_exif(source)

    assert gps is not None
    assert gps.lat < 0
    assert gps.lon < 0


@pytest.mark.parametrize(
    ("altitude_ref", "expected"),
    [(None, None), ("0", 25.0), ("1", -25.0)],
)
def test_gps_altitude_requires_an_explicit_direction_ref(
    tmp_path, monkeypatch, altitude_ref, expected,
):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    fake = SimpleNamespace(process_file=lambda *_args, **_kwargs: _fake_gps_tags(
        lat_ref="N",
        lon_ref="E",
        altitude=25,
        altitude_ref=altitude_ref,
    ))
    monkeypatch.setitem(sys.modules, "exifread", fake)

    _, gps = ingest._read_photo_exif(source)

    assert gps is not None
    assert gps.altitude_m == expected


def test_session_id_covers_output_and_provenance_evidence():
    params = IngestParams(
        fps=2, max_frames=300, blur_threshold=0, max_long_edge=2560,
    )
    base = _strict_photo_record()
    first = SourceRecord(**{
        **base.model_dump(),
        "exif_datetime": "2026:07:15 10:20:30",
        "exif_source": "photo-exif",
    })
    second = SourceRecord(**{
        **base.model_dump(),
        "exif_datetime": "2026:07:15 10:20:31",
        "exif_source": "photo-exif",
    })
    assert ingest_contract.derive_session_id(params, (first,)) != (
        ingest_contract.derive_session_id(params, (second,))
    )

    source_digest = "a" * 64
    video_one = SourceRecord(
        source_path="clip.mp4",
        source_sha256=source_digest,
        kind="video",
        bytes=20,
        source_fps=10,
        duration_s=1,
        outputs=(FrameMapping(
            output_path="clip.mp4.frames/frame_000000.jpg",
            output_sha256="b" * 64,
            output_bytes=4,
            source_frame_index=0,
            preserves_source_bytes=False,
        ),),
    )
    video_two = SourceRecord(**{
        **video_one.model_dump(),
        "outputs": ({
            **video_one.outputs[0].model_dump(),
            "output_sha256": "c" * 64,
        },),
    })
    assert ingest_contract.derive_session_id(params, (video_one,)) != (
        ingest_contract.derive_session_id(params, (video_two,))
    )

def test_manifest_written_and_valid(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    _plain_photo(inp / "a.jpg", seed=1)
    _plain_photo(inp / "b.jpg", seed=2)
    out = tmp_path / "out"

    result = ingest_all(inp, out, blur_threshold=0)

    manifest_path = out / MANIFEST_FILENAME
    assert manifest_path.exists()
    assert result["manifest"] == str(manifest_path)
    m = IngestManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert m.session_id == result["session_id"]
    assert m.session_id.startswith("ingest-")
    assert len(m.sources) == 2
    assert m.total_output_frames == 2


def test_session_id_deterministic(tmp_path):
    def build(root_name, fps):
        inp = tmp_path / f"in_{root_name}"
        inp.mkdir()
        _plain_photo(inp / "a.jpg", seed=5)
        _plain_photo(inp / "b.jpg", seed=6)
        out = tmp_path / f"out_{root_name}"
        return ingest_all(inp, out, fps=fps, blur_threshold=0)["session_id"]

    id1 = build("run1", fps=2.0)
    id2 = build("run2", fps=2.0)
    assert id1 == id2  # 相同输入+参数 → 相同 session_id
    id3 = build("run3", fps=5.0)
    assert id3 != id1  # 改变 fps → 改变 session_id


def test_source_to_output_mapping_complete(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    _plain_photo(inp / "a.jpg", seed=1)
    _plain_photo(inp / "b.jpg", seed=2)
    ok_video = _make_video(inp / "clip.mp4")
    out = tmp_path / "out"

    ingest_all(inp, out, fps=10.0, blur_threshold=0)
    m = IngestManifest.model_validate_json(
        (out / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    mapped = [fm.output_path for s in m.sources for fm in s.outputs]
    # 无重复
    assert len(mapped) == len(set(mapped))
    # 与磁盘上真实输出图一一对应
    on_disk = {p.relative_to(out).as_posix() for p in _output_images(out)}
    assert set(mapped) == on_disk
    # 计数一致
    assert sum(len(s.outputs) for s in m.sources) == m.total_output_frames == len(on_disk)
    if ok_video:
        assert any(s.kind == "video" for s in m.sources)


def test_output_sha_matches_bytes(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    _plain_photo(inp / "a.jpg", seed=1)
    _make_video(inp / "clip.mp4")
    out = tmp_path / "out"

    ingest_all(inp, out, fps=10.0, blur_threshold=0)
    m = IngestManifest.model_validate_json(
        (out / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    for s in m.sources:
        for fm in s.outputs:
            f = out / fm.output_path
            assert f.exists()
            assert fm.output_sha256 == _sha256(f)


def test_video_frames_record_provenance_loss(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    if not _make_video(inp / "clip.mp4"):
        pytest.skip("cv2.VideoWriter 不可用")
    out = tmp_path / "out"

    ingest_all(inp, out, fps=10.0, blur_threshold=0)
    m = IngestManifest.model_validate_json(
        (out / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    vids = [s for s in m.sources if s.kind == "video"]
    assert vids, "应至少有一个视频源记录"
    for v in vids:
        assert v.exif_source == "none"
        assert v.exif_datetime is None
        assert v.gps is None
        assert v.outputs, "视频应有抽帧输出"
        for fm in v.outputs:
            assert fm.preserves_source_bytes is False
            assert fm.source_frame_index is not None  # 保留源帧号


def test_photo_exif_forwarded_when_present(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    has_exif = _exif_photo(inp / "geo.jpg")
    _plain_photo(inp / "bare.jpg", seed=9)
    out = tmp_path / "out"

    ingest_all(inp, out, blur_threshold=0)
    m = IngestManifest.model_validate_json(
        (out / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    by_name = {s.source_path: s for s in m.sources}

    bare = by_name["bare.jpg"]
    assert bare.exif_source == "none"
    assert bare.exif_datetime is None
    assert bare.gps is None

    geo = by_name["geo.jpg"]
    if not has_exif:
        pytest.skip("此环境无法嵌入可解析的 EXIF")
    assert geo.exif_source == "photo-exif"
    assert geo.exif_datetime == "2026:07:15 10:20:30"
    assert geo.gps is not None
    assert geo.gps.lat == pytest.approx(31.2333, abs=1e-3)
    assert geo.gps.lon == pytest.approx(121.4667, abs=1e-3)
    assert geo.gps.altitude_m is None
    # 照片输出保留 EXIF 字节
    assert all(fm.preserves_source_bytes is True for fm in geo.outputs)


def test_false_claim_removed():
    src = Path(ingest.__file__).read_text(encoding="utf-8")
    assert "EXIF 写入源视频时间戳" not in src
    assert "do not preserve source bytes" in src
