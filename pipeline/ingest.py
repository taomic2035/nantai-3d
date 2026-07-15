"""L0 mixed image/video ingest with a publication-safe staged artifact contract.

Successful runs write only to a fresh output directory and end with a strict
``ingest_manifest.json``. Photo bytes are copied exactly. Video frames are
decoded by OpenCV and explicitly record that they do not preserve source bytes
or container metadata. A source add/remove/change during the run aborts without
leaving a success manifest.
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from loguru import logger

from pipeline.ingest_manifest import (
    MANIFEST_FILENAME,
    PHOTO_SOURCE_SUFFIXES,
    SUPPORTED_SOURCE_SUFFIXES,
    VIDEO_SOURCE_SUFFIXES,
    FrameMapping,
    GpsObservation,
    IngestArtifactError,
    IngestParams,
    SourceRecord,
    build_manifest,
    sha256_file,
    verify_ingest_artifact,
)

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    cv2 = None
    HAS_CV2 = False


PHOTO_EXTS = PHOTO_SOURCE_SUFFIXES
VIDEO_EXTS = VIDEO_SOURCE_SUFFIXES

DEFAULT_FPS = 2.0
DEFAULT_MAX_FRAMES = 300
DEFAULT_BLUR_THRESHOLD = 80.0
DEFAULT_MAX_LONG_EDGE = 2560


class IngestError(RuntimeError):
    """Ingest cannot produce a trustworthy successful artifact."""


@dataclass(frozen=True)
class SourceFingerprint:
    size: int
    sha256: str
    mtime_ns: int


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _require_real_input_directory(raw_path: str | Path) -> Path:
    path = Path(raw_path).expanduser().absolute()
    if _is_linklike(path):
        raise IngestError("input directory must not be a symlink or junction")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileNotFoundError(f"输入目录不存在: {path}") from exc
    if resolved != path or not path.is_dir():
        raise IngestError("input directory must be a real directory")
    return path


def _stable_file_fingerprint(path: Path) -> SourceFingerprint:
    try:
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
    except OSError as exc:
        raise IngestError(f"cannot fingerprint source: {path.name}") from exc
    before_key = (before.st_size, before.st_mtime_ns, before.st_ino)
    after_key = (after.st_size, after.st_mtime_ns, after.st_ino)
    if before_key != after_key or before.st_size <= 0:
        raise IngestError(f"input changed while being fingerprinted: {path.name}")
    return SourceFingerprint(after.st_size, digest, after.st_mtime_ns)


def _fingerprint_inputs(input_dir: Path) -> dict[str, SourceFingerprint]:
    fingerprints: dict[str, SourceFingerprint] = {}

    def scan_error(error: OSError) -> None:
        raise IngestError("input recursive scan failed") from error

    for directory, directory_names, file_names in os.walk(
        input_dir, followlinks=False, onerror=scan_error
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            candidate = parent / name
            if _is_linklike(candidate):
                raise IngestError("input contains a symlink or junction")
        for name in file_names:
            candidate = parent / name
            if (
                not candidate.is_file()
                or candidate.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES
            ):
                continue
            relative = candidate.relative_to(input_dir).as_posix()
            fingerprints[relative] = _stable_file_fingerprint(candidate)
    return dict(sorted(fingerprints.items()))


def _require_fresh_output(raw_path: str | Path, *, input_dir: Path) -> Path:
    path = Path(raw_path).expanduser().absolute()
    if path == input_dir or input_dir in path.parents or path in input_dir.parents:
        raise IngestError("fresh output must be separate from the input directory")
    existing_ancestor = path
    while not existing_ancestor.exists() and not _is_linklike(existing_ancestor):
        parent = existing_ancestor.parent
        if parent == existing_ancestor:
            break
        existing_ancestor = parent
    if (
        _is_linklike(existing_ancestor)
        or existing_ancestor.resolve(strict=True) != existing_ancestor
    ):
        raise IngestError("fresh output path has a symlinked or redirected ancestor")

    if path.exists() or _is_linklike(path):
        if _is_linklike(path) or not path.is_dir():
            raise IngestError("fresh output must be a real directory")
        try:
            if next(path.iterdir(), None) is not None:
                raise IngestError("fresh output directory must be empty")
        except OSError as exc:
            raise IngestError("fresh output directory cannot be inspected") from exc
    else:
        path.mkdir(parents=True)
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise IngestError("fresh output path does not resolve to itself")
    return resolved


def extract_video_frames(
    video_path: Path,
    output_dir: Path,
    fps: float = DEFAULT_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
) -> dict:
    """Extract deterministic JPEG frames or fail the entire source."""

    if not HAS_CV2:
        raise IngestError("cv2 is required for video ingest")
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise IngestError(f"cannot open video: {video_path.name}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if source_fps <= 0:
        capture.release()
        raise IngestError(f"cannot decode video fps: {video_path.name}")
    duration_s = total_frames / source_fps if total_frames > 0 else 0.0
    step = max(1, int(round(source_fps / fps)))
    output_dir.mkdir(parents=True, exist_ok=False)

    saved = 0
    skipped_blur = 0
    source_index = 0
    decoded_frames = 0
    stopped_at_limit = False
    frame_map: list[tuple[str, int]] = []
    try:
        while True:
            readable, frame = capture.read()
            if not readable:
                break
            decoded_frames += 1
            if source_index % step == 0:
                if blur_threshold > 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    if variance < blur_threshold:
                        skipped_blur += 1
                        source_index += 1
                        continue
                height, width = frame.shape[:2]
                long_edge = max(height, width)
                if long_edge > max_long_edge:
                    ratio = max_long_edge / long_edge
                    frame = cv2.resize(
                        frame,
                        (max(1, int(width * ratio)), max(1, int(height * ratio))),
                        interpolation=cv2.INTER_AREA,
                    )
                output_name = f"frame_{saved:06d}.jpg"
                output_path = output_dir / output_name
                if not bool(cv2.imwrite(
                    str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]
                )):
                    raise IngestError(f"failed to write video frame: {output_name}")
                frame_map.append((output_name, source_index))
                saved += 1
                if saved >= max_frames:
                    stopped_at_limit = True
                    break
            source_index += 1
    finally:
        capture.release()

    if not stopped_at_limit and total_frames > 0 and decoded_frames < total_frames:
        raise IngestError(
            f"premature video decode failure: {video_path.name} "
            f"({decoded_frames}/{total_frames} frames)"
        )
    if saved == 0:
        raise IngestError(f"video decode produced no publishable frames: {video_path.name}")
    return {
        "frames": saved,
        "skipped_blur": skipped_blur,
        "source_fps": source_fps,
        "duration_s": duration_s,
        "frame_map": frame_map,
    }


def copy_photo(photo_path: Path, output_dir: Path, relative_path: str) -> Path:
    """Copy a photo to its deterministic source-relative staged path."""

    destination = output_dir.joinpath(*PurePosixPath(relative_path).parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise IngestError(f"deterministic photo output already exists: {relative_path}")
    shutil.copy2(photo_path, destination)
    return destination


def _read_photo_exif(path: Path) -> tuple[str | None, GpsObservation | None]:
    """Read only real EXIF values; missing altitude remains ``None``."""

    try:
        import exifread

        with path.open("rb") as stream:
            tags = exifread.process_file(stream, details=False)
        captured_at = str(tags.get("EXIF DateTimeOriginal", "")).strip() or None

        def dms_to_deg(tag, ref) -> float:
            values = tag.values
            degree = float(values[0].num) / values[0].den
            minute = float(values[1].num) / values[1].den
            second = float(values[2].num) / values[2].den
            result = degree + minute / 60 + second / 3600
            return -result if str(ref) in {"S", "W"} else result

        latitude = tags.get("GPS GPSLatitude")
        longitude = tags.get("GPS GPSLongitude")
        latitude_ref = tags.get("GPS GPSLatitudeRef")
        longitude_ref = tags.get("GPS GPSLongitudeRef")
        gps = None
        if (
            latitude and longitude and latitude_ref and longitude_ref
            and str(latitude_ref) in {"N", "S"}
            and str(longitude_ref) in {"E", "W"}
        ):
            altitude_tag = tags.get("GPS GPSAltitude")
            altitude_ref = tags.get("GPS GPSAltitudeRef")
            altitude = None
            if altitude_tag and altitude_ref and str(altitude_ref) in {
                "0", "1", "Above sea level", "Above Sea Level",
                "Below sea level", "Below Sea Level",
            }:
                value = altitude_tag.values[0]
                altitude = float(value.num) / value.den
                if str(altitude_ref) in {
                    "1", "Below sea level", "Below Sea Level",
                }:
                    altitude = -altitude
            gps = GpsObservation(
                lat=dms_to_deg(latitude, latitude_ref),
                lon=dms_to_deg(longitude, longitude_ref),
                altitude_m=altitude,
            )
        return captured_at, gps
    except Exception:
        return None, None


def _measured_output(path: Path) -> tuple[int, str]:
    try:
        size = path.stat().st_size
        digest = sha256_file(path)
    except OSError as exc:
        raise IngestError(f"cannot measure staged output: {path.name}") from exc
    if size <= 0:
        raise IngestError(f"staged output is empty: {path.name}")
    return size, digest


def _write_manifest_atomically(output_dir: Path, manifest) -> Path:
    destination = output_dir / MANIFEST_FILENAME
    temporary = output_dir / f".{MANIFEST_FILENAME}.tmp"
    payload = manifest.model_dump_json(indent=2).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise IngestError("failed to write ingest manifest") from exc
    return destination


def ingest_all(
    input_dir: str | Path,
    output_dir: str | Path,
    fps: float = DEFAULT_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
    blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
    max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
) -> dict:
    """Ingest supported sources into a fresh, verified staged artifact."""

    params = IngestParams(
        fps=fps,
        max_frames=max_frames,
        blur_threshold=blur_threshold,
        max_long_edge=max_long_edge,
    )
    source_root = _require_real_input_directory(input_dir)
    before = _fingerprint_inputs(source_root)
    if not before:
        raise IngestError("no supported photo or video input")
    stage = _require_fresh_output(output_dir, input_dir=source_root)

    records: list[SourceRecord] = []
    photo_results: list[str] = []
    video_results: list[dict] = []
    for relative_path, fingerprint in before.items():
        source_path = source_root.joinpath(*PurePosixPath(relative_path).parts)
        if is_photo(source_path):
            output_path = copy_photo(source_path, stage, relative_path)
            output_size, output_sha = _measured_output(output_path)
            if output_size != fingerprint.size or output_sha != fingerprint.sha256:
                raise IngestError(f"photo copy does not match source: {relative_path}")
            captured_at, gps = _read_photo_exif(source_path)
            records.append(SourceRecord(
                source_path=relative_path,
                source_sha256=fingerprint.sha256,
                kind="photo",
                bytes=fingerprint.size,
                exif_datetime=captured_at,
                gps=gps,
                exif_source="photo-exif" if captured_at or gps else "none",
                outputs=(FrameMapping(
                    output_path=relative_path,
                    output_sha256=output_sha,
                    output_bytes=output_size,
                    source_frame_index=None,
                    preserves_source_bytes=True,
                ),),
            ))
            photo_results.append(relative_path)
            continue

        output_root_relative = f"{relative_path}.frames"
        video_output = stage.joinpath(*PurePosixPath(output_root_relative).parts)
        stats = extract_video_frames(
            source_path,
            video_output,
            fps=params.fps,
            max_frames=params.max_frames,
            blur_threshold=params.blur_threshold,
            max_long_edge=params.max_long_edge,
        )
        mappings: list[FrameMapping] = []
        for output_name, source_index in stats["frame_map"]:
            path = video_output / output_name
            output_size, output_sha = _measured_output(path)
            mappings.append(FrameMapping(
                output_path=f"{output_root_relative}/{output_name}",
                output_sha256=output_sha,
                output_bytes=output_size,
                source_frame_index=source_index,
                preserves_source_bytes=False,
            ))
        records.append(SourceRecord(
            source_path=relative_path,
            source_sha256=fingerprint.sha256,
            kind="video",
            bytes=fingerprint.size,
            exif_datetime=None,
            gps=None,
            exif_source="none",
            source_fps=stats["source_fps"],
            duration_s=stats["duration_s"],
            outputs=tuple(mappings),
        ))
        video_results.append({
            "video": relative_path,
            "output_dir": str(video_output),
            **{key: value for key, value in stats.items() if key != "frame_map"},
        })

    after_processing = _fingerprint_inputs(source_root)
    if after_processing != before:
        raise IngestError("input changed while ingest was running")

    manifest = build_manifest(
        created_utc=datetime.now(UTC),
        params=params,
        sources=tuple(records),
    )
    manifest_path = _write_manifest_atomically(stage, manifest)
    try:
        verified = verify_ingest_artifact(stage, input_dir=source_root)
        if _fingerprint_inputs(source_root) != before:
            raise IngestError("input changed while ingest was being verified")
    except (IngestArtifactError, IngestError):
        manifest_path.unlink(missing_ok=True)
        raise

    logger.info(
        "输入处理完成: {} 个源, {} 个输出 → {}",
        len(verified.sources),
        verified.total_output_frames,
        stage,
    )
    return {
        "photos": photo_results,
        "videos": video_results,
        "total_output": verified.total_output_frames,
        "manifest": str(manifest_path),
        "session_id": verified.session_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="L0 输入处理器 (照片 + 视频 → 可验证图片集)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", "-i", default="input")
    parser.add_argument("--output", "-o", default="photos")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    parser.add_argument("--blur-threshold", type=float, default=DEFAULT_BLUR_THRESHOLD)
    parser.add_argument("--max-long-edge", type=int, default=DEFAULT_MAX_LONG_EDGE)
    args = parser.parse_args(argv)

    started = time.time()
    result = ingest_all(
        input_dir=args.input,
        output_dir=args.output,
        fps=args.fps,
        max_frames=args.max_frames,
        blur_threshold=args.blur_threshold,
        max_long_edge=args.max_long_edge,
    )
    print(f"ingest complete in {time.time() - started:.2f}s")
    print(f"manifest: {result['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
