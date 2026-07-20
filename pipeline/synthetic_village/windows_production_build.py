"""Fail-closed adapter for a verified Windows schema-v2 Blender build."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import canary
from .surface_realism import SurfaceRealismProfileId

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WINDOWS_BLENDER = ROOT / "third/blender/blender.exe"
WINDOWS_BUILD_ADAPTER = "windows-textured-v2"
WINDOWS_BUILD_ENTRIES = (
    "build-report.json",
    *(row.name for row in canary.ARTIFACT_REQUESTS),
)


class WindowsProductionBuildError(RuntimeError):
    """A Windows schema-v2 scene build cannot be verified from current bytes."""


@dataclass(frozen=True)
class VerifiedProductionBuild:
    """One immutable operational view of a byte-verified production scene."""

    adapter: str
    directory: Path
    executable: Path
    blend_path: Path
    build_id: str
    build_report_sha256: str
    blend_sha256: str
    blend_size_bytes: int
    blender_executable_sha256: str
    verification_level: str
    synthetic: bool
    geometry_usability: str
    tool_identity: object
    source_hashes: object
    object_registry: tuple[object, ...]
    auxiliary_registry: tuple[object, ...]
    semantic_registry: tuple[object, ...]
    entry_names: tuple[str, ...]
    report: object
    request: object


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _require_private_build_directory(
    directory: Path,
    *,
    repo_root: Path,
) -> Path:
    try:
        repo_root = canary._require_real_directory(
            Path(repo_root).absolute(),
            label="repository root",
        )
        private_root = canary._require_real_directory(
            repo_root / ".nantai-studio",
            label="private project root",
        )
        directory = canary._require_real_directory(
            Path(directory).absolute(),
            label="Windows production build directory",
        )
        directory.relative_to(private_root)
    except (ValueError, canary.CanaryBuildError) as exc:
        raise WindowsProductionBuildError(
            "Windows production build must be a real private project directory",
        ) from exc
    return directory


def _verify_exact_build_layout(directory: Path) -> None:
    entries = tuple(directory.iterdir())
    if (
        {entry.name for entry in entries} != set(WINDOWS_BUILD_ENTRIES)
        or any(canary._is_linklike(entry) or not entry.is_file() for entry in entries)
    ):
        raise WindowsProductionBuildError(
            "Windows production build is not the exact seven-file set",
        )


def verify_windows_production_build(
    *,
    directory: Path,
    material_bundle_root: Path,
    repo_root: Path = ROOT,
    visual_pack_root: Path | None = None,
    executable: Path = DEFAULT_WINDOWS_BLENDER,
    surface_realism_profile_id: SurfaceRealismProfileId,
) -> VerifiedProductionBuild:
    """Reconstruct the expected request, then verify the report and every byte."""

    repo_root = Path(repo_root).absolute()
    directory = _require_private_build_directory(
        directory,
        repo_root=repo_root,
    )
    _verify_exact_build_layout(directory)

    executable = Path(executable).absolute()
    expected_executable = repo_root / "third/blender/blender.exe"
    if not _same_path(executable, expected_executable):
        raise WindowsProductionBuildError(
            "Windows production build requires the pinned repository executable",
        )

    report_path = directory / "build-report.json"
    try:
        executable_snapshot = canary._snapshot_regular_file(executable)
        report_snapshot = canary._snapshot_regular_file(report_path)
        report = canary.load_textured_build_report(report_path)
        if (
            report.schema_version != canary.TEXTURED_BUILD_REPORT_SCHEMA
            or report.tool_identity.platform != "windows-x64"
        ):
            raise WindowsProductionBuildError(
                "build report is not a Windows schema-v2 report",
            )
        request = canary.build_textured_canary_request(
            repo_root=repo_root,
            material_bundle_root=Path(material_bundle_root).absolute(),
            visual_pack_root=(
                Path(visual_pack_root).absolute()
                if visual_pack_root is not None
                else None
            ),
            surface_realism_profile_id=surface_realism_profile_id,
        )
        canary.verify_textured_build_report(
            report,
            request=request,
            staging=directory,
        )
        if directory.name != report.build_id or report.build_id != request.build_id:
            raise WindowsProductionBuildError(
                "Windows production build directory name disagrees with build ID",
            )
        if (
            executable_snapshot.sha256
            != report.tool_identity.executable_sha256
            or report.tool_identity != request.tool_identity
        ):
            raise WindowsProductionBuildError(
                "Windows Blender executable digest disagrees with build evidence",
            )

        artifacts = {
            row.name: row
            for row in report.artifacts
        }
        if set(artifacts) != {
            row.name for row in canary.ARTIFACT_REQUESTS
        }:
            raise WindowsProductionBuildError(
                "Windows production artifact registry is not exact",
            )
        measured = {}
        for name, artifact in artifacts.items():
            digest, size = canary._sha256_stable_artifact(directory / name)
            if digest != artifact.sha256 or size != artifact.size_bytes:
                raise WindowsProductionBuildError(
                    f"Windows production artifact digest or size mismatch: {name}",
                )
            measured[name] = (digest, size)
        canary._verify_snapshots_unchanged(
            (executable_snapshot, report_snapshot),
        )
    except WindowsProductionBuildError:
        raise
    except canary.CanaryBuildError as exc:
        raise WindowsProductionBuildError(str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise WindowsProductionBuildError(
            f"Windows production build verification failed safely: {exc}",
        ) from exc

    blend_sha256, blend_size_bytes = measured["village-canary.blend"]
    return VerifiedProductionBuild(
        adapter=WINDOWS_BUILD_ADAPTER,
        directory=directory,
        executable=executable_snapshot.path,
        blend_path=directory / "village-canary.blend",
        build_id=report.build_id,
        build_report_sha256=report_snapshot.sha256,
        blend_sha256=blend_sha256,
        blend_size_bytes=blend_size_bytes,
        blender_executable_sha256=executable_snapshot.sha256,
        verification_level=report.verification_level,
        synthetic=report.synthetic,
        geometry_usability=report.geometry_usability,
        tool_identity=report.tool_identity,
        source_hashes=report.source_hashes,
        object_registry=report.object_registry,
        auxiliary_registry=report.auxiliary_registry,
        semantic_registry=report.semantic_registry,
        entry_names=WINDOWS_BUILD_ENTRIES,
        report=report,
        request=request,
    )
