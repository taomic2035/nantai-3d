#!/usr/bin/env python3
"""Prepare a verified real-scene bundle for pinned Nerfstudio 1.1.5.

This path never reruns SfM. It copies the already verified COLMAP sparse
model byte-for-byte, converts only its camera representation, and writes
explicit train/val/test filename lists. Held-out pixels live in the
``evaluation/payload`` namespace and never enter the training list.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import stat
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.real_dataset import canonical_model_bytes
from pipeline.real_scene_training import (
    RealSceneTrainingError,
    TrainingBundleMember,
    VerifiedTrainingJobBundle,
    verify_production_training_job_bundle,
)


class PreparedDatasetError(ValueError):
    """A prepared real-scene dataset is incomplete or content-drifted."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PINNED_NERFSTUDIO_VERSION = "1.1.5"
_MAX_TRANSFORMS_BYTES = 64 * 1024 * 1024
_ONE_MIB = 1024 * 1024
_SPARSE_PREFIX = "sfm/sparse/0/"
_REQUIRED_SPARSE_FILES = frozenset(
    {
        "cameras.bin",
        "cameras.txt",
        "images.bin",
        "images.txt",
        "points3D.bin",
        "points3D.txt",
    }
)


def _cross_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        stat.S_IFMT(result.st_mode),
        result.st_size,
        result.st_mtime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _same_surface_signature(
    result: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    return (
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
        int(getattr(result, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )


def _is_linklike(path: Path, observed: os.stat_result) -> bool:
    reparse_flag = getattr(
        stat,
        "FILE_ATTRIBUTE_REPARSE_POINT",
        0x400,
    )
    if (
        stat.S_ISLNK(observed.st_mode)
        or int(getattr(observed, "st_file_attributes", 0))
        & reparse_flag
    ):
        return True
    try:
        return bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


class PreparedDatasetMember(FrozenModel):
    path: str = Field(min_length=1)
    byte_length: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class PreparedDatasetManifest(FrozenModel):
    """Content closure for the remote trainer's materialized dataset."""

    schema_id: Literal["nantai.prepared-real-scene-dataset.v1"] = Field(
        default="nantai.prepared-real-scene-dataset.v1",
        alias="schema",
        serialization_alias="schema",
    )
    training_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    nerfstudio_version: Literal["1.1.5"]
    orientation_method: Literal["none"] = "none"
    center_method: Literal["none"] = "none"
    auto_scale_poses: Literal[False] = False
    scale_factor: Literal[1.0] = 1.0
    registered_frame_count: int = Field(ge=1)
    train_frame_count: int = Field(ge=1)
    held_out_frame_count: int = Field(ge=1)
    unregistered_train_filenames: tuple[str, ...]
    members: tuple[PreparedDatasetMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _members_are_sorted_and_unique(
        self,
    ) -> PreparedDatasetManifest:
        paths = tuple(member.path for member in self.members)
        if paths != tuple(sorted(paths)):
            raise ValueError("prepared dataset members must be path sorted")
        if len(set(paths)) != len(paths):
            raise ValueError("prepared dataset member paths must be unique")
        if (
            self.registered_frame_count
            != self.train_frame_count + self.held_out_frame_count
        ):
            raise ValueError(
                "registered frame count must equal train plus held-out"
            )
        return self


@dataclass(frozen=True)
class PreparedRealSceneDataset:
    root: Path
    transforms_path: Path
    manifest_path: Path
    manifest: PreparedDatasetManifest


def _portable_relative_path(value: str, *, label: str) -> PurePosixPath:
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PreparedDatasetError(f"{label} is not a portable relative path")
    return parsed


def _write_new_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise PreparedDatasetError(
            f"cannot materialize prepared member: {path.name}"
        ) from exc


def _atomic_replace(path: Path, payload: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise PreparedDatasetError(
            f"cannot publish prepared metadata: {path.name}"
        ) from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _verified_member_bytes(
    archive: zipfile.ZipFile,
    members: dict[str, TrainingBundleMember],
    path: str,
) -> bytes:
    member = members.get(path)
    if member is None:
        raise PreparedDatasetError(f"training bundle is missing {path}")
    try:
        payload = archive.read(path)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise PreparedDatasetError(
            f"training bundle member cannot be read: {path}"
        ) from exc
    if len(payload) != member.byte_length:
        raise PreparedDatasetError(
            f"training bundle member length mismatch: {path}"
        )
    if hashlib.sha256(payload).hexdigest() != member.sha256:
        raise PreparedDatasetError(
            f"training bundle member sha256 mismatch: {path}"
        )
    return payload


def _verified_image_bytes(
    archive: zipfile.ZipFile,
    members: dict[str, TrainingBundleMember],
    path: str,
    *,
    identity_sha256: str,
) -> bytes:
    payload = _verified_member_bytes(archive, members, path)
    if hashlib.sha256(payload).hexdigest() != identity_sha256:
        raise PreparedDatasetError(
            f"training image bytes differ from split identity: {path}"
        )
    return payload


def _read_transforms(path: Path) -> dict[str, Any]:
    try:
        before = path.lstat()
        if _is_linklike(path, before) or not stat.S_ISREG(before.st_mode):
            raise PreparedDatasetError(
                "Nerfstudio transforms.json is missing or link-like"
            )
        if before.st_size <= 0 or before.st_size > _MAX_TRANSFORMS_BYTES:
            raise PreparedDatasetError(
                "Nerfstudio transforms.json size is outside the allowed range"
            )
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            stream = os.fdopen(descriptor, "rb", buffering=0)
        except OSError:
            os.close(descriptor)
            raise
        with stream:
            descriptor_before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(descriptor_before.st_mode)
                or _cross_surface_signature(descriptor_before)
                != _cross_surface_signature(before)
            ):
                raise PreparedDatasetError(
                    "Nerfstudio transforms.json changed before read"
                )
            raw = stream.read(_MAX_TRANSFORMS_BYTES + 1)
            descriptor_after = os.fstat(stream.fileno())
        after = path.lstat()
    except PreparedDatasetError:
        raise
    except OSError as exc:
        raise PreparedDatasetError(
            "Nerfstudio transforms.json cannot be read"
        ) from exc
    if (
        _cross_surface_signature(before)
        != _cross_surface_signature(descriptor_before)
        or _same_surface_signature(descriptor_before)
        != _same_surface_signature(descriptor_after)
        or _cross_surface_signature(descriptor_after)
        != _cross_surface_signature(after)
        or _same_surface_signature(before)
        != _same_surface_signature(after)
        or len(raw) > _MAX_TRANSFORMS_BYTES
        or len(raw) != before.st_size
    ):
        raise PreparedDatasetError(
            "Nerfstudio transforms.json changed while being read"
        )

    def reject_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, ValueError) as exc:
        raise PreparedDatasetError(
            "Nerfstudio transforms.json is invalid"
        ) from exc
    if not isinstance(parsed, dict):
        raise PreparedDatasetError(
            "Nerfstudio transforms.json root must be an object"
        )
    return parsed


def _collect_manifest_members(root: Path) -> tuple[PreparedDatasetMember, ...]:
    result: list[PreparedDatasetMember] = []
    for path in sorted(root.rglob("*")):
        if path.name == "prepared-dataset-manifest.json":
            continue
        try:
            before = path.lstat()
        except OSError as exc:
            raise PreparedDatasetError(
                "prepared dataset member disappeared"
            ) from exc
        if _is_linklike(path, before):
            raise PreparedDatasetError(
                "prepared dataset contains a symlink"
            )
        if not stat.S_ISREG(before.st_mode):
            continue
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                stream = os.fdopen(descriptor, "rb", buffering=0)
            except OSError:
                os.close(descriptor)
                raise
            with stream:
                descriptor_before = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(descriptor_before.st_mode)
                    or _cross_surface_signature(descriptor_before)
                    != _cross_surface_signature(before)
                ):
                    raise PreparedDatasetError(
                        f"prepared dataset member changed before read: {path.name}"
                    )
                digest = hashlib.sha256()
                measured = 0
                for chunk in iter(
                    lambda stream=stream: stream.read(_ONE_MIB),
                    b"",
                ):
                    digest.update(chunk)
                    measured += len(chunk)
                descriptor_after = os.fstat(stream.fileno())
            after = path.lstat()
        except PreparedDatasetError:
            raise
        except OSError as exc:
            raise PreparedDatasetError(
                f"prepared dataset member cannot be read: {path.name}"
            ) from exc
        if (
            _cross_surface_signature(before)
            != _cross_surface_signature(descriptor_before)
            or _same_surface_signature(descriptor_before)
            != _same_surface_signature(descriptor_after)
            or _cross_surface_signature(descriptor_after)
            != _cross_surface_signature(after)
            or _same_surface_signature(before)
            != _same_surface_signature(after)
            or measured != before.st_size
        ):
            raise PreparedDatasetError(
                f"prepared dataset member changed while being read: {path.name}"
            )
        if measured <= 0:
            raise PreparedDatasetError(
                f"prepared dataset member is empty: {path.name}"
            )
        result.append(
            PreparedDatasetMember(
                path=path.relative_to(root).as_posix(),
                byte_length=measured,
                sha256=digest.hexdigest(),
            )
        )
    return tuple(result)


def _same_bundle(
    expected: VerifiedTrainingJobBundle,
    actual: VerifiedTrainingJobBundle,
) -> bool:
    return (
        expected.bundle_sha256 == actual.bundle_sha256
        and expected.manifest == actual.manifest
        and expected.request == actual.request
        and expected.split == actual.split
        and expected.member_names == actual.member_names
    )


def prepare_real_scene_dataset(
    bundle_path: Path,
    output_dir: Path,
    *,
    converter: Callable[..., int],
    nerfstudio_version: str,
) -> PreparedRealSceneDataset:
    """Materialize one production training/evaluation dataset.

    ``converter`` must be pinned Nerfstudio 1.1.5
    ``colmap_utils.colmap_to_json``. Dependency injection keeps unit tests
    credential- and GPU-free; the CLI imports the real pinned implementation.
    """

    if nerfstudio_version != _PINNED_NERFSTUDIO_VERSION:
        raise PreparedDatasetError(
            "production preparation requires Nerfstudio exactly 1.1.5"
        )
    try:
        bundle = verify_production_training_job_bundle(bundle_path)
    except RealSceneTrainingError as exc:
        raise PreparedDatasetError(
            f"production training bundle verification failed: {exc}"
        ) from exc

    output = Path(output_dir).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise PreparedDatasetError("prepared output boundary must be absent")
    try:
        parent = output.parent.lstat()
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            raise PreparedDatasetError(
                "prepared output parent must be a real directory"
            )
        output.mkdir()
    except PreparedDatasetError:
        raise
    except OSError as exc:
        raise PreparedDatasetError(
            "prepared output boundary cannot be created"
        ) from exc

    members = {
        member.path: member for member in bundle.manifest.members
    }
    try:
        with zipfile.ZipFile(bundle.path, "r") as archive:
            if tuple(archive.namelist()) != bundle.member_names:
                raise PreparedDatasetError(
                    "training archive members changed before preparation"
                )
            for identity in bundle.split.train:
                source = f"capture/payload/{identity.logical_path}"
                payload = _verified_image_bytes(
                    archive,
                    members,
                    source,
                    identity_sha256=identity.sha256,
                )
                relative = _portable_relative_path(
                    identity.logical_path,
                    label="training image path",
                )
                _write_new_file(
                    output.joinpath("images", *relative.parts),
                    payload,
                )
            for identity in bundle.split.held_out:
                source = f"evaluation/payload/{identity.logical_path}"
                payload = _verified_image_bytes(
                    archive,
                    members,
                    source,
                    identity_sha256=identity.sha256,
                )
                relative = _portable_relative_path(
                    identity.logical_path,
                    label="held-out image path",
                )
                _write_new_file(
                    output.joinpath("images", *relative.parts),
                    payload,
                )

            evidence_members = {
                "training/held-out-split.json":
                    "evidence/held-out-split.json",
                "training/operator-intent-config.yml":
                    "evidence/operator-intent-config.yml",
                "training/training-request.json":
                    "evidence/training-request.json",
            }
            for source, destination in evidence_members.items():
                payload = _verified_member_bytes(
                    archive,
                    members,
                    source,
                )
                _write_new_file(output / destination, payload)

            sparse_members = {
                path.removeprefix(_SPARSE_PREFIX): path
                for path in members
                if path.startswith(_SPARSE_PREFIX)
            }
            if set(sparse_members) != _REQUIRED_SPARSE_FILES:
                raise PreparedDatasetError(
                    "verified sparse model file set is incomplete"
                )
            for filename in sorted(sparse_members):
                payload = _verified_member_bytes(
                    archive,
                    members,
                    sparse_members[filename],
                )
                _write_new_file(
                    output / "colmap" / "sparse" / "0" / filename,
                    payload,
                )
    except PreparedDatasetError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise PreparedDatasetError(
            "production training bundle cannot be materialized"
        ) from exc

    try:
        after_materialization = verify_production_training_job_bundle(
            bundle.path
        )
    except RealSceneTrainingError as exc:
        raise PreparedDatasetError(
            f"training bundle changed during preparation: {exc}"
        ) from exc
    if not _same_bundle(bundle, after_materialization):
        raise PreparedDatasetError(
            "training bundle identity changed during preparation"
        )

    sparse_root = output / "colmap" / "sparse" / "0"
    try:
        converted_count = converter(
            recon_dir=sparse_root,
            output_dir=output,
            camera_mask_path=None,
            image_id_to_depth_path=None,
            image_rename_map=None,
            ply_filename="sparse_pc.ply",
            keep_original_world_coordinate=True,
            use_single_camera_mode=True,
        )
    except Exception as exc:
        raise PreparedDatasetError(
            f"Nerfstudio COLMAP conversion failed: {exc}"
        ) from exc
    if isinstance(converted_count, bool) or not isinstance(
        converted_count,
        int,
    ):
        raise PreparedDatasetError(
            "Nerfstudio converter returned an invalid frame count"
        )

    transforms_path = output / "transforms.json"
    metadata = _read_transforms(transforms_path)
    if "applied_transform" in metadata or "applied_scale" in metadata:
        raise PreparedDatasetError(
            "Nerfstudio converter changed the source coordinate frame"
        )
    frames = metadata.get("frames")
    if not isinstance(frames, list) or not frames:
        raise PreparedDatasetError(
            "Nerfstudio transforms.json contains no frames"
        )
    frame_paths: list[str] = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise PreparedDatasetError(
                "Nerfstudio frame entry must be an object"
            )
        value = frame.get("file_path")
        if not isinstance(value, str):
            raise PreparedDatasetError(
                "Nerfstudio frame path must be a string"
            )
        parsed = _portable_relative_path(value, label="Nerfstudio frame path")
        if not parsed.parts or parsed.parts[0] != "images":
            raise PreparedDatasetError(
                "Nerfstudio frame path must stay under images/"
            )
        frame_paths.append(parsed.as_posix())
    if len(frame_paths) != len(set(frame_paths)):
        raise PreparedDatasetError(
            "Nerfstudio transforms.json contains duplicate frame paths"
        )
    if converted_count != len(frame_paths):
        raise PreparedDatasetError(
            "Nerfstudio converter frame count differs from transforms.json"
        )

    train_candidates = tuple(
        f"images/{identity.logical_path}"
        for identity in bundle.split.train
    )
    held_out = tuple(
        f"images/{identity.logical_path}"
        for identity in bundle.split.held_out
    )
    frame_set = set(frame_paths)
    all_split_paths = set((*train_candidates, *held_out))
    if not frame_set <= all_split_paths:
        raise PreparedDatasetError(
            "Nerfstudio frames contain images outside the verified split"
        )
    missing_held_out = set(held_out) - frame_set
    if missing_held_out:
        raise PreparedDatasetError(
            "registered held-out cameras are missing from transforms.json"
        )
    train = tuple(path for path in train_candidates if path in frame_set)
    unregistered_train = tuple(
        path for path in train_candidates if path not in frame_set
    )
    if set(train) & set(held_out):
        raise PreparedDatasetError(
            "held-out filename entered the training split"
        )
    if set((*train, *held_out)) != frame_set:
        raise PreparedDatasetError(
            "explicit train/test filenames do not cover converted frames"
        )

    metadata["frames"] = sorted(
        frames,
        key=lambda frame: frame["file_path"],
    )
    metadata["orientation_override"] = "none"
    metadata["train_filenames"] = list(train)
    metadata["val_filenames"] = list(held_out)
    metadata["test_filenames"] = list(held_out)
    transforms_bytes = (
        json.dumps(
            metadata,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    _atomic_replace(transforms_path, transforms_bytes)

    prepared_manifest = PreparedDatasetManifest(
        training_bundle_sha256=bundle.bundle_sha256,
        nerfstudio_version=_PINNED_NERFSTUDIO_VERSION,
        registered_frame_count=len(frame_paths),
        train_frame_count=len(train),
        held_out_frame_count=len(held_out),
        unregistered_train_filenames=unregistered_train,
        members=_collect_manifest_members(output),
    )
    manifest_path = output / "prepared-dataset-manifest.json"
    _write_new_file(
        manifest_path,
        canonical_model_bytes(prepared_manifest),
    )
    return PreparedRealSceneDataset(
        root=output,
        transforms_path=transforms_path,
        manifest_path=manifest_path,
        manifest=prepared_manifest,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a split-bound real-scene bundle for Nerfstudio 1.1.5"
        ),
    )
    parser.add_argument(
        "--prepared-bundle",
        required=True,
        type=Path,
        help="verified training-job.zip with evaluation payloads",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="absent prepared dataset directory",
    )
    args = parser.parse_args(argv)
    try:
        installed_version = importlib.metadata.version("nerfstudio")
        from nerfstudio.process_data.colmap_utils import colmap_to_json

        prepared = prepare_real_scene_dataset(
            args.prepared_bundle,
            args.output,
            converter=colmap_to_json,
            nerfstudio_version=installed_version,
        )
    except (
        ImportError,
        importlib.metadata.PackageNotFoundError,
        PreparedDatasetError,
    ) as exc:
        parser.error(str(exc))
    print(prepared.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
