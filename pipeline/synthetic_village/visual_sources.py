"""Content-addressed private storage for replaceable image2 visual sources."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
import warnings
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.studio_jobs import (
    JobContractError,
    ProjectFileLock,
    WindowsNtfsDurabilityBackend,
)

from .contracts import SlotCategory
from .defaults import load_default_visual_slots

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})
IMAGE_FORMATS_BY_SUFFIX = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}
MAX_SOURCE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_VISUAL_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_IMAGE_PIXELS = 64 * 1024 * 1024
VISUAL_MANIFEST_NAME = "visual-sources.json"


class VisualSourceError(ValueError):
    """A visual source cannot be trusted or published into the private pack."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class VisualSourceRecord(FrozenModel):
    slot_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category: SlotCategory
    object_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    prompt: str = Field(min_length=40)
    source_pack_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_manifest_sha256: Sha256
    generator_interface: str = Field(min_length=1)
    actual_model_id: str = Field(min_length=1)
    reference_sha256: tuple[Sha256, ...] = ()
    synthetic: Literal[True] = True

    @field_validator("object_path")
    @classmethod
    def _portable_object_path(cls, value: str) -> str:
        parsed = PurePosixPath(value)
        if (
            "\\" in value
            or value.startswith("/")
            or parsed.is_absolute()
            or parsed.as_posix() != value
            or any(part in {"", ".", ".."} for part in parsed.parts)
        ):
            raise ValueError("object_path must be a portable relative POSIX path")
        return value

    @model_validator(mode="after")
    def _path_matches_digest(self) -> VisualSourceRecord:
        suffix = PurePosixPath(self.object_path).suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise ValueError("object_path requires an allowed image suffix")
        if self.object_path != f"objects/{self.sha256}{suffix}":
            raise ValueError("object_path must be content-addressed by SHA-256")
        if not self.slot_id.startswith(f"{self.category}-"):
            raise ValueError("slot prefix must match category")
        if len(self.reference_sha256) != len(set(self.reference_sha256)):
            raise ValueError("reference hashes must be unique")
        return self


class VisualSourceManifest(FrozenModel):
    schema_version: Literal[1] = 1
    pack_id: Literal["synthetic-mountain-village-hybrid-v3"]
    synthetic: Literal[True] = True
    records: tuple[VisualSourceRecord, ...]

    @model_validator(mode="after")
    def _stable_unique_records(self) -> VisualSourceManifest:
        slot_ids = [record.slot_id for record in self.records]
        if slot_ids != sorted(slot_ids):
            raise ValueError("visual-source records must be sorted by slot ID")
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("visual-source slot IDs must be unique")
        return self


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_manifest_bytes(manifest: VisualSourceManifest) -> bytes:
    text = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VisualSourceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_bounded_json(path: Path, *, maximum_bytes: int) -> tuple[bytes, object]:
    try:
        expected_size = path.stat().st_size
    except OSError as exc:
        raise VisualSourceError(f"cannot inspect JSON input: {path.name}") from exc
    if expected_size <= 0 or expected_size > maximum_bytes:
        raise VisualSourceError(f"JSON input size is invalid: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(maximum_bytes + 1)
    if len(raw) != expected_size or len(raw) > maximum_bytes:
        raise VisualSourceError(f"JSON input changed during bounded read: {path.name}")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisualSourceError(f"JSON input is invalid UTF-8 JSON: {path.name}") from exc
    return raw, payload


def load_visual_source_manifest(path: Path) -> VisualSourceManifest:
    path = Path(path)
    try:
        raw, _ = _read_bounded_json(path, maximum_bytes=MAX_VISUAL_MANIFEST_BYTES)
        manifest = VisualSourceManifest.model_validate_json(raw)
        if raw != canonical_manifest_bytes(manifest):
            raise VisualSourceError("visual-source manifest is not canonical JSON")
        _require_real_directory(path.parent, label="visual pack directory")
        _require_real_directory(
            path.parent / "objects",
            label="visual object directory",
        )
        for record in manifest.records:
            object_path = path.parent / Path(record.object_path)
            if _is_linklike(object_path) or not object_path.is_file():
                raise VisualSourceError(
                    f"visual-source object is not a regular file: {record.object_path}",
                )
            if sha256_file(object_path) != record.sha256:
                raise VisualSourceError(
                    f"existing object hash mismatch: {record.object_path}",
                )
        return manifest
    except VisualSourceError:
        raise
    except ValidationError as exc:
        raise VisualSourceError(f"visual-source manifest validation failed: {exc}") from exc
    except OSError as exc:
        raise VisualSourceError(f"visual-source manifest filesystem failure: {exc}") from exc


def _load_source_evidence(source: Path, manifest_path: Path) -> dict[str, object]:
    raw, payload = _read_bounded_json(
        manifest_path,
        maximum_bytes=MAX_SOURCE_MANIFEST_BYTES,
    )
    if not isinstance(payload, dict):
        raise VisualSourceError("source manifest must be a JSON object")
    if payload.get("synthetic") is not True or payload.get("requested_generator") != "image2":
        raise VisualSourceError("source manifest must declare synthetic image2 provenance")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise VisualSourceError("source manifest assets must be a list")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("file") == source.name
    ]
    if len(matches) != 1:
        raise VisualSourceError("source manifest must contain exactly one matching asset")
    asset = matches[0]
    if asset.get("synthetic") is not True:
        raise VisualSourceError("source asset must be declared synthetic")
    prompt = asset.get("prompt")
    if not isinstance(prompt, str) or len(prompt) < 40:
        raise VisualSourceError("source asset requires its complete prompt")
    references = asset.get("reference_image_sha256", [])
    if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
        raise VisualSourceError("source reference hashes must be a list of strings")
    generator_interface = payload.get("generator_interface")
    if not isinstance(generator_interface, str) or not generator_interface:
        raise VisualSourceError("source manifest requires generator_interface")
    source_pack_id = payload.get("pack_id")
    if (
        not isinstance(source_pack_id, str)
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source_pack_id) is None
    ):
        raise VisualSourceError("source manifest pack_id must be a portable slug")
    actual_model_id = payload.get("actual_model_id")
    if not isinstance(actual_model_id, str) or not actual_model_id.strip():
        actual_model_id = "unknown"
    return {
        "asset": asset,
        "prompt": prompt,
        "reference_sha256": tuple(references),
        "generator_interface": generator_interface,
        "source_pack_id": source_pack_id,
        "actual_model_id": actual_model_id,
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _stat_signature(result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _verify_image(path: Path, *, suffix: str) -> tuple[int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image_format = image.format
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise VisualSourceError(f"source is not a valid image: {path.name}") from exc
    if width <= 0 or height <= 0:
        raise VisualSourceError("source image dimensions must be positive")
    if width * height > MAX_SOURCE_IMAGE_PIXELS:
        raise VisualSourceError("source image exceeds the pixel limit")
    if image_format != IMAGE_FORMATS_BY_SUFFIX[suffix]:
        raise VisualSourceError(
            f"decoded image format {image_format!r} does not match suffix {suffix!r}",
        )
    return width, height


def _is_linklike(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _require_real_directory(path: Path, *, label: str) -> None:
    if _is_linklike(path):
        raise VisualSourceError(f"{label} must not be a symlink or junction")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VisualSourceError(f"{label} is not a real path") from exc
    if not path.is_dir() or not _same_path(resolved, path.absolute()):
        raise VisualSourceError(f"{label} must be a real path without redirected ancestors")


def _prepare_pack_root(raw_path: Path) -> Path:
    pack_root = raw_path.expanduser().absolute()
    cursor = pack_root
    missing: list[Path] = []
    while not cursor.exists():
        if _is_linklike(cursor):
            raise VisualSourceError("pack root ancestor must not be a symlink or junction")
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise VisualSourceError("pack root has no existing real ancestor")
        cursor = parent
    _require_real_directory(cursor, label="pack root ancestor")
    for directory in reversed(missing):
        try:
            directory.mkdir(exist_ok=False)
            _flush_directory(directory.parent)
        except FileExistsError:
            pass
        _require_real_directory(directory, label="pack root")
    _require_real_directory(pack_root, label="pack root")
    return pack_root


def _flush_file(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_file(path)
        return
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _flush_directory(path: Path) -> None:
    if os.name == "nt":
        WindowsNtfsDurabilityBackend.flush_directory(path)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_object(source: Path, destination: Path, expected_sha256: str) -> None:
    _require_real_directory(destination.parent, label="visual object directory")
    if _is_linklike(destination):
        raise VisualSourceError("existing object cannot be a symlink or junction")
    if destination.exists():
        if not destination.is_file():
            raise VisualSourceError("existing object is not a regular file")
        if sha256_file(destination) != expected_sha256:
            raise VisualSourceError("existing object does not match its content address")
        _flush_file(destination)
        _flush_directory(destination.parent)
        if sha256_file(destination) != expected_sha256:
            raise VisualSourceError("existing object failed durable hash verification")
        return

    temporary = destination.parent / f".{expected_sha256}.{uuid.uuid4().hex}.tmp"
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1 << 20)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if sha256_file(temporary) != expected_sha256:
            raise VisualSourceError("staged object SHA-256 changed during copy")
        published = False
        try:
            os.link(temporary, destination)
            published = True
        except FileExistsError:
            if (
                _is_linklike(destination)
                or not destination.is_file()
                or sha256_file(destination) != expected_sha256
            ):
                raise VisualSourceError("raced existing object has different bytes") from None
        if published:
            _flush_directory(destination.parent)
        else:
            _flush_file(destination)
            _flush_directory(destination.parent)
        if _is_linklike(destination) or not destination.is_file():
            raise VisualSourceError("published object is not a regular file")
        if sha256_file(destination) != expected_sha256:
            raise VisualSourceError("published object failed durable hash verification")
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(path: Path, manifest: VisualSourceManifest) -> None:
    _require_real_directory(path.parent, label="visual manifest directory")
    if _is_linklike(path):
        raise VisualSourceError("visual manifest cannot be a symlink or junction")
    payload = canonical_manifest_bytes(manifest)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _flush_file(path)
        _flush_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _import_visual_source(
    *,
    slot_id: str,
    source: Path,
    source_manifest: Path,
    pack_root: Path,
) -> VisualSourceRecord:
    try:
        catalog = load_default_visual_slots()
    except (OSError, ValueError) as exc:
        raise VisualSourceError(
            f"default visual-slot catalog is invalid: {exc}",
        ) from exc
    slots = {slot.slot_id: slot for slot in catalog.slots}
    slot = slots.get(slot_id)
    if slot is None:
        raise VisualSourceError(f"visual slot is not declared: {slot_id}")

    source = Path(source)
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise VisualSourceError(f"source requires an allowed image suffix: {source.name}")
    if source.is_symlink() or not source.is_file():
        raise VisualSourceError("source must be a regular image file")

    source_before = source.stat()
    if source_before.st_size <= 0:
        raise VisualSourceError("source image must not be empty")
    if source_before.st_size > MAX_SOURCE_IMAGE_BYTES:
        raise VisualSourceError("source image exceeds the byte limit")
    digest = sha256_file(source)
    source_after_hash = source.stat()
    if _stat_signature(source_before) != _stat_signature(source_after_hash):
        raise VisualSourceError("source image changed while hashing")
    evidence = _load_source_evidence(source, Path(source_manifest))
    asset = evidence["asset"]
    if not isinstance(asset, dict) or asset.get("sha256") != digest:
        raise VisualSourceError("source SHA-256 does not match its source manifest")
    width, height = _verify_image(source, suffix=suffix)
    source_after_verify = source.stat()
    if _stat_signature(source_before) != _stat_signature(source_after_verify):
        raise VisualSourceError("source image changed while validating")
    object_path = f"objects/{digest}{suffix}"
    record = VisualSourceRecord(
        slot_id=slot.slot_id,
        category=slot.category,
        object_path=object_path,
        sha256=digest,
        bytes=source_before.st_size,
        width=width,
        height=height,
        prompt=evidence["prompt"],
        source_pack_id=evidence["source_pack_id"],
        source_manifest_sha256=evidence["source_manifest_sha256"],
        generator_interface=evidence["generator_interface"],
        actual_model_id=evidence["actual_model_id"],
        reference_sha256=evidence["reference_sha256"],
        synthetic=True,
    )

    pack_root = _prepare_pack_root(Path(pack_root))
    try:
        with ProjectFileLock(pack_root / ".pack.lock", role="writer"):
            _require_real_directory(pack_root, label="pack root")
            objects_root = pack_root / "objects"
            if objects_root.exists() or _is_linklike(objects_root):
                _require_real_directory(objects_root, label="visual object directory")
            else:
                objects_root.mkdir(exist_ok=False)
                _require_real_directory(objects_root, label="visual object directory")
                _flush_directory(pack_root)

            manifest_path = pack_root / VISUAL_MANIFEST_NAME
            if _is_linklike(manifest_path):
                raise VisualSourceError("visual manifest cannot be a symlink or junction")
            if manifest_path.exists():
                current = load_visual_source_manifest(manifest_path)
            else:
                current = VisualSourceManifest(
                    pack_id="synthetic-mountain-village-hybrid-v3",
                    synthetic=True,
                    records=(),
                )
            existing = {item.slot_id: item for item in current.records}.get(slot_id)
            if existing is not None:
                if existing != record:
                    raise VisualSourceError(
                        "visual-source slots are immutable; create a new pack "
                        "revision to replace one",
                    )
                _publish_object(source, pack_root / record.object_path, digest)
                return existing

            _publish_object(source, pack_root / record.object_path, digest)
            updated = VisualSourceManifest(
                pack_id=current.pack_id,
                synthetic=True,
                records=tuple(
                    sorted((*current.records, record), key=lambda item: item.slot_id),
                ),
            )
            _write_manifest(manifest_path, updated)
            return record
    except JobContractError as exc:
        raise VisualSourceError(f"visual pack lock is unavailable: {exc}") from exc


def import_visual_source(
    *,
    slot_id: str,
    source: Path,
    source_manifest: Path,
    pack_root: Path,
) -> VisualSourceRecord:
    """Import one verified source while exposing only stable domain failures."""

    try:
        return _import_visual_source(
            slot_id=slot_id,
            source=source,
            source_manifest=source_manifest,
            pack_root=pack_root,
        )
    except VisualSourceError:
        raise
    except ValidationError as exc:
        raise VisualSourceError(f"visual source validation failed: {exc}") from exc
    except OSError as exc:
        raise VisualSourceError(f"visual source filesystem failure: {exc}") from exc
